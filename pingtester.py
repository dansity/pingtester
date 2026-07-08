#!/usr/bin/env python3
"""pingtester — beautiful CLI network latency monitor"""

import csv
import glob
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
import re
import math
import urllib.request
import urllib.error
import webbrowser
from urllib.parse import urlparse
from collections import deque
from dataclasses import dataclass
from typing import Optional, List, Tuple
import argparse
import locale

try:
    import curses
except ImportError:
    sys.exit("curses not found — on Windows run: pip install windows-curses")

locale.setlocale(locale.LC_ALL, "")

_BLOCKS = " ▁▂▃▄▅▆▇█"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Treat 3xx as a final response instead of chasing it to another host —
    otherwise we'd measure the redirect target (e.g. 8.8.8.8 → dns.google),
    not the host the user asked for."""
    def redirect_request(self, *a, **k):
        return None


def _build_http_opener() -> urllib.request.OpenerDirector:
    ctx = ssl.create_default_context()   # cert validation off: we measure latency, not trust
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ctx))


_HTTP_OPENER = _build_http_opener()


def _app_base_dir() -> str:
    """Where CSV logs and the HTML report live: next to the binary when frozen
    by PyInstaller, otherwise next to this script."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def run_report_cli(threshold: float) -> None:
    """Generate the HTML report in-process from the CSVs in the app dir.

    Invoked via the hidden `--generate-report` flag so the single frozen exe
    can produce its own report by re-invoking itself — no separate report.py
    needs to ship alongside the binary. `report` is bundled into the exe.
    """
    import report   # bundled into the exe via this import (see build.sh)

    base = _app_base_dir()
    paths = sorted(glob.glob(os.path.join(base, "pingtester_*.csv")))
    if not paths:
        print("No pingtester_*.csv files found next to the program.", file=sys.stderr)
        return
    rows = report.load_csvs(paths)
    if not rows:
        print("No valid data rows found in the CSV files.", file=sys.stderr)
        return
    out = os.path.join(base, "pingtester_report.html")
    html = report.generate_report(rows, threshold, out)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Report written to: {out}", file=sys.stderr)
    try:
        webbrowser.open("file://" + os.path.abspath(out))
    except Exception:
        pass


# Color pair IDs
_BORDER = 1
_TITLE  = 2
_BAR_OK = 3
_BAR_HI = 4
_STAT_V = 5
_STAT_L = 6
_OK     = 7
_DIM    = 8


@dataclass
class PingResult:
    ms: Optional[float]
    ts: float


class CsvLogger:
    def __init__(self, enabled: bool = False):
        self.enabled = enabled
        self._lock = threading.Lock()
        self._file = None
        self._writer = None
        self._current_hour: Optional[str] = None
        self._base_dir = _app_base_dir()

    def _rotate(self, hour_key: str):
        if self._file:
            self._file.close()
        path = os.path.join(self._base_dir, f"pingtester_{hour_key}.csv")
        new_file = not os.path.exists(path)
        self._file = open(path, "a", newline="")
        self._writer = csv.writer(self._file)
        if new_file:
            self._writer.writerow(["host", "mode", "timestamp", "ping_ms"])
        self._current_hour = hour_key

    def log(self, host: str, mode: str, ts: float, ms: Optional[float]):
        if not self.enabled:
            return
        hour_key = time.strftime("%Y-%m-%d_%H", time.localtime(ts))
        ts_str = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))
        with self._lock:
            if hour_key != self._current_hour:
                self._rotate(hour_key)
            self._writer.writerow([host, mode, ts_str, f"{ms:.3f}" if ms is not None else ""])
            self._file.flush()

    def close(self):
        with self._lock:
            if self._file:
                self._file.close()
                self._file = None


class PingMonitor:
    MODES = ["icmp", "tcp", "http"]   # probe method; all yield an (ms, ts) sample

    def __init__(self, host: str, interval_ms: int, threshold_ms: float, scale_ms: float,
                 mode: str = "icmp", port: int = 443, logger: Optional[CsvLogger] = None):
        self.host = host
        self.interval_ms = interval_ms
        self.threshold_ms = threshold_ms
        self.scale_ms = scale_ms
        self.mode = mode if mode in self.MODES else "icmp"
        self.port = port
        self._logger  = logger
        self._results: deque = deque(maxlen=15000)
        self._total   = 0      # total pings ever appended (never decrements)
        self._running = True
        self._lock    = threading.Lock()
        # Reachability of the *current* (mode, host): True/False once probed,
        # invalidated to None whenever the config changes under us.
        self._probe_ok:  Optional[bool] = None
        self._probe_sig: Optional[tuple] = None
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._running = False

    def _probe(self) -> Optional[float]:
        """Dispatch to the active probe method. Returns latency in ms, or None on failure/timeout."""
        if self.mode == "tcp":
            return self._probe_tcp()
        if self.mode == "http":
            return self._probe_http()
        return self._probe_icmp()

    def _probe_icmp(self) -> Optional[float]:
        """ICMP echo via the OS `ping` binary. Cross-platform (win uses -n/-w, others -c/-W)."""
        try:
            if sys.platform == "win32":
                cmd = ["ping", "-n", "1", "-w", "1000", self.host]
            else:
                cmd = ["ping", "-c", "1", "-W", "1", self.host]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            m = re.search(r"time[=<]([\d.]+)", r.stdout)
            return float(m.group(1)) if m else None
        except Exception:
            return None

    def _host_port(self) -> Tuple[str, int]:
        """Resolve the current host into (hostname, port) for socket-based probes.

        Accepts a bare host, `host:port`, or a full URL; falls back to self.port.
        """
        h = self.host.strip()
        if h.startswith(("http://", "https://")):
            u = urlparse(h)
            return u.hostname or h, (u.port or (443 if u.scheme == "https" else 80))
        # host:port  (single colon → treat as IPv4/hostname + port; IPv6 left untouched)
        if h.count(":") == 1:
            host, _, p = h.partition(":")
            try:
                return host, int(p)
            except ValueError:
                return host, self.port   # unparseable port → drop it, keep the host
        return h, self.port

    def _probe_tcp(self) -> Optional[float]:
        """Time a full TCP handshake to host:port. Measures the path real traffic takes,
        and works through firewalls that allow the port even when ICMP is blocked."""
        host, port = self._host_port()
        try:
            t0 = time.monotonic()
            with socket.create_connection((host, port), timeout=3):
                return (time.monotonic() - t0) * 1000.0
        except Exception:
            return None

    def _probe_http(self) -> Optional[float]:
        """Time an HTTP(S) GET to the host's own first response (≈ time-to-first-byte).
        Redirects are NOT followed — a 3xx counts as the server answering, so we
        measure the host the user typed, not wherever it redirects.
        Confirms the actual service is healthy, not just that the box answers."""
        url = self.host.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        req = urllib.request.Request(
            url, method="GET", headers={"User-Agent": "pingtester"}
        )
        t0 = time.monotonic()
        try:
            with _HTTP_OPENER.open(req, timeout=5) as resp:
                resp.read(1)   # first byte
                return (time.monotonic() - t0) * 1000.0
        except urllib.error.HTTPError:
            # Any HTTP status (3xx/4xx/5xx) means the server responded — valid sample.
            return (time.monotonic() - t0) * 1000.0
        except Exception:
            return None

    def _loop(self):
        while self._running:
            t0 = time.monotonic()
            sig = (self.mode, self.host, self.port)   # config this sample belongs to
            ms = self._probe()
            result = PingResult(ms=ms, ts=time.time())
            with self._lock:
                self._results.append(result)
                self._total += 1
                self._probe_ok  = ms is not None
                self._probe_sig = sig
            if self._logger:
                self._logger.log(self.host, self.mode, result.ts, result.ms)
            sleep = max(0.0, self.interval_ms / 1000.0 - (time.monotonic() - t0))
            time.sleep(sleep)

    def reachable(self) -> Optional[bool]:
        """Whether the *current* (mode, host, port) is answering.
        None = not yet probed since the last config change."""
        with self._lock:
            if self._probe_sig != (self.mode, self.host, self.port):
                return None
            return self._probe_ok

    def recent(self, n: int) -> List[PingResult]:
        with self._lock:
            return list(self._results)[-n:]

    def total(self) -> int:
        with self._lock:
            return self._total

    def stats(self) -> dict:
        with self._lock:
            data = list(self._results)
        vals = [r.ms for r in data if r.ms is not None]
        n = len(data)
        lost = n - len(vals)
        if not vals:
            return dict(min=None, max=None, avg=None, p95=None,
                        loss=100.0 if n else 0.0, total=n)
        s = sorted(vals)
        return dict(
            min=min(vals), max=max(vals),
            avg=sum(vals) / len(vals),
            p95=s[min(int(len(s) * 0.95), len(s) - 1)],
            loss=lost / n * 100,
            total=n,
        )


class App:
    HOSTS = ["8.8.8.8", "1.1.1.1", "9.9.9.9", "hu-bud-as12303.anchors.atlas.ripe.net"]
    # View window steps in seconds: 1 min → 3 hours
    TIME_STEPS = [60, 120, 180, 300, 600, 900, 1800, 3600, 5400, 7200, 10800]

    def __init__(self, stdscr, mon: PingMonitor, logger: Optional[CsvLogger] = None):
        self.scr = stdscr
        self.mon = mon
        self._logger = logger
        self._inp_mode: Optional[str] = None
        self._inp_buf = ""
        self._msg = ""
        self._msg_until = 0.0
        self._msg_color = _OK
        self._time_idx = 4   # default: TIME_STEPS[4] = 300 s = 5 min
        self._init_curses()

    @property
    def _time_range_s(self) -> int:
        return self.TIME_STEPS[self._time_idx]

    @staticmethod
    def _fmt_time(s: int) -> str:
        if s < 3600:
            return f"{s // 60}m"
        rem = s % 3600
        return f"{s // 3600}h" if rem == 0 else f"{s // 3600}h{rem // 60}m"

    def _init_curses(self):
        curses.curs_set(0)
        self.scr.nodelay(True)
        self.scr.timeout(80)
        curses.start_color()
        curses.use_default_colors()
        bg = -1
        curses.init_pair(_BORDER, curses.COLOR_CYAN,   bg)
        curses.init_pair(_TITLE,  curses.COLOR_CYAN,   bg)
        curses.init_pair(_BAR_OK, curses.COLOR_BLUE,   bg)
        curses.init_pair(_BAR_HI, curses.COLOR_RED,    bg)
        curses.init_pair(_STAT_V, curses.COLOR_YELLOW, bg)
        curses.init_pair(_STAT_L, curses.COLOR_WHITE,  bg)
        curses.init_pair(_OK,     curses.COLOR_GREEN,  bg)
        curses.init_pair(_DIM,    curses.COLOR_WHITE,  bg)

    # ── helpers ──────────────────────────────────────────────────────────

    def _put(self, y, x, s, attr=0):
        try:
            H, W = self.scr.getmaxyx()
            if 0 <= y < H and 0 <= x < W - 1:
                self.scr.addstr(y, x, s[:max(0, W - x - 1)], attr)
        except curses.error:
            pass

    def _hline(self, row, W):
        b = curses.color_pair(_BORDER) | curses.A_BOLD
        self._put(row, 0, "├" + "─" * (W - 2) + "┤", b)

    def _flash(self, msg: str, secs: float = 2.0, color: int = _OK):
        self._msg = msg
        self._msg_until = time.monotonic() + secs
        self._msg_color = color

    # ── input handling ───────────────────────────────────────────────────

    def run(self):
        while True:
            if not self._handle_key():
                break
            self._draw()
        self.mon.stop()
        if self._logger:
            self._logger.close()

    def _handle_key(self) -> bool:
        try:
            k = self.scr.getkey()
        except curses.error:
            return True

        if self._inp_mode:
            if k in ("\n", "\r"):
                self._commit()
                self._inp_mode = None
                self._inp_buf = ""
                curses.curs_set(0)
            elif k == "\x1b":
                self._inp_mode = None
                self._inp_buf = ""
                curses.curs_set(0)
            elif k in ("KEY_BACKSPACE", "\x7f", "\b"):
                self._inp_buf = self._inp_buf[:-1]
            elif len(k) == 1 and k.isprintable():
                self._inp_buf += k
            return True

        if k in ("q", "Q"):
            return False
        elif k == "h":
            try:
                idx = self.HOSTS.index(self.mon.host)
            except ValueError:
                idx = -1
            self.mon.host = self.HOSTS[(idx + 1) % len(self.HOSTS)]
            self._flash(f"Host → {self.mon.host}")
        elif k == "H":
            self._start_input("host")
        elif k == "m":
            idx = self.mon.MODES.index(self.mon.mode)
            self.mon.mode = self.mon.MODES[(idx + 1) % len(self.mon.MODES)]
            self._flash(f"Mode → {self.mon.mode.upper()}")
        elif k == "p" and self.mon.mode == "tcp":
            self._start_input("port")
        elif k == "i":
            self._start_input("interval")
        elif k == "t":
            self._start_input("threshold")
        elif k in ("+", "="):
            self.mon.scale_ms = min(self.mon.scale_ms * 2, 10000)
            self._flash(f"Y-scale → {self.mon.scale_ms:.0f} ms")
        elif k == "-":
            self.mon.scale_ms = max(self.mon.scale_ms / 2, 5)
            self._flash(f"Y-scale → {self.mon.scale_ms:.0f} ms")
        elif k in ("KEY_LEFT", ",", "<"):
            self._time_idx = max(0, self._time_idx - 1)
            self._flash(f"View → {self._fmt_time(self._time_range_s)}")
        elif k in ("KEY_RIGHT", ".", ">"):
            self._time_idx = min(len(self.TIME_STEPS) - 1, self._time_idx + 1)
            self._flash(f"View → {self._fmt_time(self._time_range_s)}")
        elif k == "l" and self._logger:
            self._logger.enabled = not self._logger.enabled
            self._flash("Logging ON" if self._logger.enabled else "Logging OFF")
        elif k == "g":
            self._generate_report()
        return True

    def _generate_report(self):
        # Re-invoke ourselves with --generate-report so the report generator can
        # be bundled into the single exe (frozen) rather than shipped separately.
        # Frozen: sys.executable IS the app. Otherwise run this script via Python.
        if getattr(sys, "frozen", False):
            argv = [sys.executable, "--generate-report"]
        else:
            argv = [sys.executable, os.path.abspath(__file__), "--generate-report"]
        argv += ["--threshold", str(self.mon.threshold_ms)]
        try:
            subprocess.Popen(
                argv,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._flash("Report generating…", color=_BORDER)
        except Exception:
            self._flash("Report launch failed", secs=3.0, color=_BAR_HI)

    def _start_input(self, mode: str):
        self._inp_mode = mode
        self._inp_buf = ""
        curses.curs_set(1)

    def _commit(self):
        v = self._inp_buf.strip()
        if not v:
            return
        if self._inp_mode == "host":
            self.mon.host = v
            self._flash(f"Host → {v}")
        elif self._inp_mode == "port":
            try:
                n = int(v)
                if 1 <= n <= 65535:
                    self.mon.port = n
                    self._flash(f"Port → {n}")
            except ValueError:
                pass
        elif self._inp_mode == "interval":
            try:
                n = int(v)
                if n >= 100:
                    self.mon.interval_ms = n
                    self._flash(f"Interval → {n} ms")
            except ValueError:
                pass
        elif self._inp_mode == "threshold":
            try:
                self.mon.threshold_ms = float(v)
                self._flash(f"Threshold → {v} ms")
            except ValueError:
                pass

    # ── drawing ──────────────────────────────────────────────────────────

    def _draw(self):
        self.scr.erase()
        H, W = self.scr.getmaxyx()

        if H < 10 or W < 40:
            self._put(0, 0, "Terminal too small (min 40×10)!", curses.color_pair(_BAR_HI))
            self.scr.refresh()
            return

        b  = curses.color_pair(_BORDER) | curses.A_BOLD

        # outer box
        self._put(0,     0, "╭" + "─" * (W - 2) + "╮", b)
        for r in range(1, H - 1):
            self._put(r, 0, "│", b)
            self._put(r, W - 1, "│", b)
        self._put(H - 1, 0, "╰" + "─" * (W - 2) + "╯", b)

        # title
        title = " ◈ PingTester "
        self._put(0, 2, title, curses.color_pair(_TITLE) | curses.A_BOLD)
        ts = time.strftime(" %H:%M:%S ")
        self._put(0, W - len(ts) - 2, ts, curses.color_pair(_DIM) | curses.A_DIM)

        # config row
        log_tag = ""
        if self._logger:
            log_tag = "   log: ON" if self._logger.enabled else "   log: off"
        mode_tag = f"mode: {self.mon.mode}"
        if self.mon.mode == "tcp":
            mode_tag += f":{self.mon.port}"
        conf = (
            f"  {mode_tag}"
            f"   host: {self.mon.host}"
            f"   every: {self.mon.interval_ms}ms"
            f"   warn: >{self.mon.threshold_ms:.0f}ms"
            f"   yscale: {self.mon.scale_ms:.0f}ms"
            f"   view: {self._fmt_time(self._time_range_s)}"
            + log_tag
        )
        self._put(1, 1, conf, curses.color_pair(_STAT_L))

        # top-right notification area: transient flash takes priority, otherwise
        # a persistent red reminder while in HTTP mode.
        flash_active = self._msg and time.monotonic() < self._msg_until
        if flash_active:
            msg = f"  ▸ {self._msg}"
            self._put(1, W - len(msg) - 2, msg, curses.color_pair(self._msg_color) | curses.A_BOLD)
        elif self.mon.mode == "http" and self.mon.reachable() is False:
            # HTTP probes are failing → this host isn't actually serving web.
            msg = "Host isn't answering HTTP — switch to a real webhost"
            self._put(1, W - len(msg) - 2, msg, curses.color_pair(_BAR_HI) | curses.A_BOLD)

        self._hline(2, W)

        # chart: rows 3 .. H-6  (exclusive)
        chart_top = 3
        chart_bot = H - 5
        if chart_bot > chart_top + 2:
            self._draw_chart(chart_top, chart_bot, W)

        self._hline(H - 5, W)
        self._draw_stats(H - 4, W)
        self._hline(H - 3, W)
        self._draw_keys(H - 2, W)

        if self._inp_mode:
            self._draw_input(H, W)

        self.scr.refresh()

    # ── chart ─────────────────────────────────────────────────────────────

    def _build_columns(self, bar_w: int) -> List[List[PingResult]]:
        """
        Map ping history into bar_w display columns spanning _time_range_s seconds.

        Uses absolute ping indices so bucket boundaries never shift mid-stream:
          bucket k  =  pings with absolute index in [k*spc, (k+1)*spc)
        Only the rightmost (live) bucket grows as new pings arrive; all
        completed buckets are frozen. Bars scroll left only when a bucket
        completes and a new one opens — never on every individual ping.

        spc == 1 : 1 ping per column   (zoomed in / 1 px per bar)
        spc >= 2 : N pings averaged per column (zoomed out)
        """
        interval_s  = self.mon.interval_ms / 1000.0
        total_f     = self._time_range_s / interval_s
        spc         = max(1, int(total_f / bar_w + 0.5))

        T           = self.mon.total()                        # pings ever received
        if T == 0:
            return [[] for _ in range(bar_w)]

        live_k      = (T - 1) // spc                         # bucket index of newest ping
        n_fetch     = bar_w * spc + spc
        results     = self.mon.recent(n_fetch)
        n           = len(results)
        abs_start   = T - n                                   # absolute index of results[0]

        cols: List[List[PingResult]] = []
        for col in range(bar_w):
            cfr     = bar_w - 1 - col                        # 0 = rightmost (newest)
            k       = live_k - cfr                           # absolute bucket index
            if k < 0:
                cols.append([])
                continue
            r_start = k * spc - abs_start
            # Live bucket may be incomplete; completed buckets are exactly spc wide.
            r_end   = n if cfr == 0 else r_start + spc
            if r_end <= 0 or r_start >= n:
                cols.append([])
            else:
                cols.append(results[max(0, r_start) : min(n, r_end)])
        return cols

    def _draw_chart(self, top: int, bot: int, W: int):
        CH    = bot - top
        scale = self.mon.scale_ms
        thresh = self.mon.threshold_ms

        YW    = 7
        ax    = YW + 1
        bar_x = ax + 1
        bar_w = W - bar_x - 1

        b = curses.color_pair(_BORDER) | curses.A_BOLD

        # y-axis spine
        for r in range(top, bot - 1):
            self._put(r, ax, "│", b)

        bar_rows = CH - 1

        def y_label(row, val):
            lbl = f"{val:.0f}"
            self._put(row, ax - len(lbl), lbl, curses.color_pair(_DIM) | curses.A_DIM)
            self._put(row, ax, "┤", b)

        y_label(top, scale)
        y_label(top + bar_rows // 2, scale / 2)

        # threshold dash line
        if 0 < thresh < scale:
            t_row = top + int(bar_rows * (1.0 - thresh / scale))
            t_row = max(top, min(bot - 2, t_row))
            lbl = f"{thresh:.0f}"
            self._put(t_row, ax - len(lbl), lbl, curses.color_pair(_STAT_V))
            self._put(t_row, ax, "┼", curses.color_pair(_STAT_V) | curses.A_BOLD)
            for cx in range(bar_x, bar_x + bar_w):
                self._put(t_row, cx, "╌", curses.color_pair(_STAT_V) | curses.A_DIM)

        # x-axis
        self._put(bot - 1, ax, "└" + "─" * bar_w, b)

        # build column buckets and draw bars
        columns = self._build_columns(bar_w)
        for col, bucket in enumerate(columns):
            cx = bar_x + col
            if not bucket:
                continue

            valid = [r.ms for r in bucket if r.ms is not None]
            if not valid:
                self._put(bot - 2, cx, "╷", curses.color_pair(_BAR_HI) | curses.A_DIM)
                continue

            avg_ms = sum(valid) / len(valid)
            is_hi  = avg_ms > thresh
            color  = _BAR_HI if is_hi else _BAR_OK
            attr   = curses.color_pair(color) | curses.A_BOLD
            dim_a  = curses.color_pair(color)

            clamped = min(avg_ms, scale)
            pix  = clamped / scale * bar_rows * 8
            full = int(pix // 8)
            frac = int(pix % 8)

            for rf in range(full):
                row = bot - 2 - rf
                if top <= row < bot - 1:
                    a = dim_a if rf == full - 1 and frac == 0 else attr
                    self._put(row, cx, "█", a)

            if frac:
                row = bot - 2 - full
                if top <= row < bot - 1:
                    self._put(row, cx, _BLOCKS[frac], dim_a)

        # x-axis labels: left shows window size + aggregation hint
        interval_s  = self.mon.interval_ms / 1000.0
        total_f     = self._time_range_s / interval_s
        spc         = max(1, int(total_f / bar_w + 0.5))   # same formula as _build_columns
        lbl_l       = f"←{self._fmt_time(self._time_range_s)}"
        if spc >= 2:
            secs_col = spc * interval_s
            hint = f"{secs_col:.0f}s/col" if secs_col < 60 else f"{secs_col/60:.1f}m/col"
            lbl_l += f" ({hint})"
        lbl_r = "now→"
        self._put(bot - 1, bar_x + 1, lbl_l, curses.color_pair(_DIM) | curses.A_DIM)
        self._put(bot - 1, bar_x + bar_w - len(lbl_r) - 1, lbl_r,
                  curses.color_pair(_DIM) | curses.A_DIM)

    # ── stats bar ─────────────────────────────────────────────────────────

    def _draw_stats(self, row: int, W: int):
        st = self.mon.stats()

        def ms(v):
            return f"{v:.1f}ms" if v is not None else "─"

        # right: live ping
        recent = self.mon.recent(1)
        if recent:
            r = recent[0]
            if r.ms is not None:
                lbl  = f"● {r.ms:.1f} ms"
                attr = curses.color_pair(_BAR_HI if r.ms > self.mon.threshold_ms else _OK) | curses.A_BOLD
            else:
                lbl  = "● TIMEOUT"
                attr = curses.color_pair(_BAR_HI) | curses.A_BOLD
            self._put(row, W - len(lbl) - 2, lbl, attr)

        parts = [
            ("min", ms(st["min"]),  _STAT_V),
            ("max", ms(st["max"]),  _STAT_V),
            ("avg", ms(st["avg"]),  _STAT_V),
            ("p95", ms(st["p95"]),  _STAT_V),
            ("loss", f"{st['loss']:.1f}%",
             _BAR_HI if st["loss"] > 5 else (_STAT_V if st["loss"] > 0 else _OK)),
            ("pkts", str(st["total"]), _DIM),
        ]

        x = 2
        for lbl, val, c in parts:
            self._put(row, x, lbl + ":", curses.color_pair(_DIM) | curses.A_DIM)
            x += len(lbl) + 1
            self._put(row, x, val, curses.color_pair(c) | curses.A_BOLD)
            x += len(val) + 3
            if x > W - 20:
                break

    # ── key legend ────────────────────────────────────────────────────────

    def _draw_keys(self, row: int, W: int):
        b = curses.color_pair(_BORDER) | curses.A_BOLD
        n = curses.color_pair(_STAT_L)
        t = curses.color_pair(_TITLE) | curses.A_BOLD

        logging_on = bool(self._logger and self._logger.enabled)
        log_desc   = "● log:ON" if logging_on else "log csv"
        log_attr   = curses.color_pair(_OK) | curses.A_BOLD if logging_on else n

        keys = [
            ("q",    "quit",        n),
            ("m",    "mode",        n),
            ("h",    "cycle host",  n),
            ("H",    "custom host", n),
        ]
        if self.mon.mode == "tcp":
            keys.append(("p", "tcp port", n))
        keys += [
            ("i",    "interval",    n),
            ("t",    "threshold",   n),
            ("+/-",  "yscale",      n),
            ("◄/►",  "view",        n),
            ("l",    log_desc,      log_attr),
            ("g",    "report",      n),
        ]
        x = 2
        for k, d, d_attr in keys:
            if x + len(k) + len(d) + 5 > W - 2:
                break
            self._put(row, x, "[", b);           x += 1
            self._put(row, x, k, t);             x += len(k)
            self._put(row, x, "] ", n);          x += 2
            self._put(row, x, d, d_attr);        x += len(d) + 2

    # ── input overlay ─────────────────────────────────────────────────────

    def _draw_input(self, H: int, W: int):
        prompts = {
            "host":      f"Ping host  [{self.mon.host}]: ",
            "port":      f"TCP port  [{self.mon.port}]: ",
            "interval":  f"Interval ms  [{self.mon.interval_ms}]: ",
            "threshold": f"Threshold ms  [{self.mon.threshold_ms:.0f}]: ",
        }
        prompt = prompts.get(self._inp_mode, "Enter: ")
        line   = prompt + self._inp_buf + "▌"
        bw     = min(W - 4, max(len(line) + 4, 46))
        bx     = (W - bw) // 2
        by     = H // 2 - 1
        s      = curses.color_pair(_STAT_V) | curses.A_BOLD
        self._put(by - 1, bx, "╭" + "─" * (bw - 2) + "╮", s)
        self._put(by,     bx, "│" + " " * (bw - 2) + "│", s)
        self._put(by + 1, bx, "╰" + "─" * (bw - 2) + "╯", s)
        self._put(by, bx + 2, line[: bw - 4], s)


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="pingtester — CLI latency monitor")
    ap.add_argument("--host",      default="8.8.8.8",  help="Target host (default: 8.8.8.8)")
    ap.add_argument("--mode",      default="icmp", choices=PingMonitor.MODES,
                    help="Probe method: icmp | tcp | http (default: icmp)")
    ap.add_argument("--port",      type=int,   default=443,   help="TCP-mode port (default: 443)")
    ap.add_argument("--interval",  type=int,   default=1000,  help="Ping interval ms (default: 1000)")
    ap.add_argument("--threshold", type=float, default=100.0, help="Warn threshold ms (default: 100)")
    ap.add_argument("--scale",     type=float, default=200.0, help="Chart Y-scale ms (default: 200)")
    ap.add_argument("--log",       action="store_true",       help="Enable CSV logging at startup")
    ap.add_argument("--generate-report", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    # Hidden mode: generate the HTML report and exit (used by the in-app 'g' key).
    if args.generate_report:
        run_report_cli(args.threshold)
        return

    logger = CsvLogger(enabled=args.log)
    mon = PingMonitor(args.host, args.interval, args.threshold, args.scale,
                      mode=args.mode, port=args.port, logger=logger)
    try:
        curses.wrapper(lambda s: App(s, mon, logger=logger).run())
    except KeyboardInterrupt:
        pass
    finally:
        mon.stop()
        logger.close()
    print("Goodbye.")


if __name__ == "__main__":
    main()
