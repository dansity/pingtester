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

# ═══════════════════════════════════════════════════════════════════════════
#  USER-EDITABLE CONFIGURATION  —  tweak anything here to taste
# ═══════════════════════════════════════════════════════════════════════════

# Preset hosts cycled with the 'h' key.
PRESET_HOSTS = [
    "8.8.8.8",
    "1.1.1.1",
    "9.9.9.9",
    "hu-bud-as12303.anchors.atlas.ripe.net",
]

# Startup defaults (each is also overridable via a command-line flag).
DEFAULT_HOST         = "8.8.8.8"
DEFAULT_MODE         = "icmp"     # one of: icmp | tcp | http | trace
DEFAULT_PORT         = 443        # target port used in tcp mode
DEFAULT_INTERVAL_MS  = 1000       # time between probes (min 100)
DEFAULT_THRESHOLD_MS = 100.0      # bars above this are flagged as warnings
DEFAULT_SCALE_MS     = 200.0      # chart Y-axis full-scale value

# Per-probe network timeouts, in seconds.
ICMP_TIMEOUT_S = 3    # subprocess timeout for the system `ping`
TCP_TIMEOUT_S  = 3    # TCP handshake timeout
HTTP_TIMEOUT_S = 5    # HTTP(S) request timeout

# Sent as the User-Agent header by the HTTP probe.
HTTP_USER_AGENT = "pingtester"

# View-window steps in seconds (cycled with ◄/►): 1 min → 3 hours.
TIME_STEPS = [60, 120, 180, 300, 600, 900, 1800, 3600, 5400, 7200, 10800]

# History ring-buffer size in samples (~4 h at a 1 s interval).
HISTORY_MAXLEN = 15000

# ── traceroute mode ────────────────────────────────────────────────────────
TRACE_MAX_HOPS     = 20    # give up after this many hops (traceroute -m)
TRACE_HOP_WAIT_S   = 1     # per-hop reply wait (traceroute -w)
TRACE_TIMEOUT_S    = 25    # hard subprocess timeout for one full traceroute
TRACE_QUERIES      = 1     # probes per hop (traceroute -q); 1 keeps the loop fast
TRACE_MIN_INTERVAL_MS = 1000   # a traceroute can't sensibly run faster than this

# ═══════════════════════════════════════════════════════════════════════════
#  VISUAL CONFIGURATION  —  glyphs, colors, chart geometry
# ═══════════════════════════════════════════════════════════════════════════

# Vertical eighth-blocks used for sub-row bar precision. Index 0 = empty,
# index 8 = full cell. Replace with e.g. " ░░▒▒▓▓██" for a chunkier look.
CHART_BLOCKS = " ▁▂▃▄▅▆▇█"

CHART_TIMEOUT_GLYPH   = "╷"    # drawn at the baseline when a sample is lost
CHART_THRESHOLD_GLYPH = "╌"    # the dashed warning line across the chart
CHART_Y_AXIS_WIDTH    = 7      # columns reserved for the y-axis number labels
CHART_LEGEND_SWATCH   = "▆"    # per-hop color chip in the traceroute legend

# Single-probe (icmp/tcp/http) bar colors. Hex, mapped to the nearest color
# the terminal can actually show.
COLOR_BAR_OK   = "#3d7fd8"     # below threshold
COLOR_BAR_WARN = "#ff5555"     # above threshold
COLOR_BAR_OVER = "#8b0000"     # the value ran off the top of the chart (clipped)

# ── interface (chrome) colors ────────────────────────────────────────────────
# These paint the frame, title, config/stat rows and key legend — everything
# that isn't a chart bar. Like the bar colors above they are hex, mapped through
# the same palette: on a terminal that allows it they land on the exact shade,
# on one that doesn't they snap to the nearest color it can show. This pins the
# chrome to one look everywhere, rather than inheriting the terminal's theme.
UI_COLOR_BORDER     = "#4ab0b4"   # box frame + section divider lines
UI_COLOR_TITLE      = "#4ab0b4"   # the "◈ PingTester" title
UI_COLOR_STAT_VALUE = "#e5c07b"   # numeric stat values, threshold label + line
UI_COLOR_STAT_LABEL = "#b8c0cc"   # config row and stat labels
UI_COLOR_OK         = "#6cc79b"   # healthy live-ping readout, "log ON"
UI_COLOR_ALERT      = "#ff5555"   # timeouts, over-threshold, error notices
UI_COLOR_DIM        = "#7a828e"   # de-emphasised text (also rendered with A_DIM)

# Divider lines (the ├───┤ rules between sections) and the outer frame are drawn
# bold by default — heavier and brighter. Set False for the thinnest weight the
# box-drawing font offers; there is no glyph thinner than a light rule, so this
# is the only lever on their weight.
UI_FRAME_BOLD       = False

# Every bar's topmost cell is drawn a shade darker than its body, which caps
# the bar rather than letting it dissolve into the background. Amount is an
# OKLab lightness drop; 0 disables. (An off-chart bar caps with COLOR_BAR_OVER
# instead, so a clipped bar is unmistakable.)
CHART_BAR_TIP_DARKEN = 0.12

# Blend the two colors that meet inside one character cell — the threshold
# crossing on a single-probe bar, or two hops in a traceroute stack — by
# painting the lower one as the glyph and the upper one as the cell background.
# Set False for flat single-color cells if your terminal renders backgrounds
# oddly; boundaries then snap to the nearest row edge.
CHART_SUBCELL_BLEND = True

# Traceroute hop gradient. The first hop that answers gets the start color, the
# last the end color; everything between is interpolated in OKLab, so the steps
# look evenly spaced to the eye instead of bunching up the way naive RGB
# blending does. The ramp is rebuilt whenever the number of answering hops
# changes, so hop N is not pinned to a fixed color across path changes.
TRACE_GRADIENT_START = "#4ab0b4"   # the first hop that answers (your router)
TRACE_GRADIENT_END   = "#5d3e8e"   # the last hop (the destination)

# Give every answering hop at least this many whole rows, so a hop that adds
# almost no latency still reads as its own block instead of collapsing into a
# hairline under the hop above it. 0 = exact heights (near-zero hops vanish).
# Above 0 the bar can overstate total RTT by up to (hops - 1) rows.
TRACE_MIN_SEGMENT_ROWS = 1

# Thin dividing line drawn at the top of every hop block except the last, in
# eighths of a row. 0 disables. It is carved out of the block below it, so it
# costs no height. Requires CHART_SUBCELL_BLEND.
TRACE_HOP_SEPARATOR_PX    = 1
TRACE_HOP_SEPARATOR_COLOR = TRACE_GRADIENT_END

TRACE_SHOW_LEGEND = True       # draw the hop legend beneath the chart

# A stacked bar needs about one chart row per hop before every hop gets its own
# block and separator, so trace mode fits the Y-scale to the path instead of
# inheriting DEFAULT_SCALE_MS (200 ms would squash a 19 ms path into two rows).
# Pressing +/- takes the scale back under manual control for the rest of the
# session, and that manual value follows you out of trace mode. Leaving trace
# with auto-fit still on restores the scale from before you entered.
TRACE_AUTO_SCALE          = True
TRACE_AUTO_SCALE_HEADROOM = 1.3    # fit to peak RTT × this, then round up a step
TRACE_AUTO_SCALE_SAMPLES  = 60     # how many recent samples the peak is taken from

# Y-scale values the chart snaps to, and the steps +/- walks through.
SCALE_STEPS = [5, 10, 25, 50, 100, 200, 500, 1000, 2000, 5000, 10000]

# Redefine terminal palette slots to the exact colors above when the terminal
# allows it. Without this, colors snap to the nearest xterm-256 entry, whose
# 6×6×6 cube is coarse enough that adjacent light hops become indistinguishable.
# Each color redefines the slot that already holds its closest match, so a
# terminal that advertises the capability but ignores the request still renders
# a sane approximation rather than whatever those slots happened to contain.
# Original slot values are restored on exit. Set False if your terminal
# misbehaves.
USE_EXACT_TERMINAL_COLORS = True

# ── CSV logging ────────────────────────────────────────────────────────────
# In traceroute mode each hop is one column, holding both the hop's address
# and its RTT in a single cell joined by this delimiter, e.g. `10.0.0.1|4.21`.
CSV_HOP_DELIM = "|"

# ═══════════════════════════════════════════════════════════════════════════

_BLOCKS = CHART_BLOCKS


# ── OKLab color space ─────────────────────────────────────────────────────────
#
# OKLab is perceptually uniform: equal numeric steps look like equal steps to
# the eye. Interpolating a hop gradient there keeps every hop distinguishable
# from its neighbours, which naive sRGB interpolation does not — sRGB blends
# bunch up in the dark end and drift through muddy intermediate hues.

def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def _cbrt(x: float) -> float:
    return math.copysign(abs(x) ** (1 / 3), x)


def rgb_to_oklab(r: float, g: float, b: float) -> Tuple[float, float, float]:
    """r/g/b are sRGB in 0..1."""
    r, g, b = _srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b)
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = _cbrt(l), _cbrt(m), _cbrt(s)
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def oklab_to_rgb(L: float, a: float, b: float) -> Tuple[float, float, float]:
    """Returns sRGB in 0..1, clamped."""
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bb = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    return tuple(min(1.0, max(0.0, _linear_to_srgb(v))) for v in (r, g, bb))


def hex_to_rgb(h: str) -> Tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def rgb_to_hex(r: float, g: float, b: float) -> str:
    return "#%02x%02x%02x" % tuple(int(round(v * 255)) for v in (r, g, b))


def oklab_darken(hexstr: str, amount: float) -> str:
    """Drop a color's OKLab lightness, keeping its hue and chroma. Darkening in
    OKLab rather than by scaling RGB keeps the hue from drifting."""
    if amount <= 0:
        return hexstr
    L, a, b = rgb_to_oklab(*hex_to_rgb(hexstr))
    return rgb_to_hex(*oklab_to_rgb(max(0.0, L - amount), a, b))


def oklab_gradient(start_hex: str, end_hex: str, n: int) -> List[str]:
    """n colors from start_hex to end_hex, evenly spaced in OKLab."""
    if n <= 0:
        return []
    if n == 1:
        return [start_hex]
    a = rgb_to_oklab(*hex_to_rgb(start_hex))
    b = rgb_to_oklab(*hex_to_rgb(end_hex))
    out = []
    for i in range(n):
        t = i / (n - 1)
        lab = tuple(a[j] + (b[j] - a[j]) * t for j in range(3))
        out.append(rgb_to_hex(*oklab_to_rgb(*lab)))
    return out


# ── terminal color mapping ────────────────────────────────────────────────────

def _xterm256_table() -> List[Tuple[int, Tuple[float, float, float]]]:
    """Indices 16..255 of the xterm palette: a 6×6×6 RGB cube plus 24 greys.
    Indices 0..15 are skipped — terminals remap those to the user's theme."""
    levels = [0, 95, 135, 175, 215, 255]
    out = []
    for i in range(216):
        r, g, b = levels[i // 36], levels[(i // 6) % 6], levels[i % 6]
        out.append((16 + i, (r / 255, g / 255, b / 255)))
    for i in range(24):
        v = (8 + 10 * i) / 255
        out.append((232 + i, (v, v, v)))
    return out


_XTERM256 = [(idx, rgb_to_oklab(*rgb)) for idx, rgb in _xterm256_table()]

# The canonical RGB of each slot, used to put a borrowed slot back when the
# terminal refuses to tell us what it held (PDCurses errors on color_content).
_XTERM256_RGB = dict(_xterm256_table())

# Fallback when the terminal only has the 8 ANSI colors.
_BASIC8 = [
    (curses.COLOR_BLACK,   (0.0, 0.0, 0.0)),
    (curses.COLOR_RED,     (0.8, 0.0, 0.0)),
    (curses.COLOR_GREEN,   (0.0, 0.8, 0.0)),
    (curses.COLOR_YELLOW,  (0.8, 0.8, 0.0)),
    (curses.COLOR_BLUE,    (0.0, 0.0, 0.9)),
    (curses.COLOR_MAGENTA, (0.8, 0.0, 0.8)),
    (curses.COLOR_CYAN,    (0.0, 0.8, 0.8)),
    (curses.COLOR_WHITE,   (0.85, 0.85, 0.85)),
]
_BASIC8_LAB = [(idx, rgb_to_oklab(*rgb)) for idx, rgb in _BASIC8]


class Palette:
    """Maps hex colors onto whatever the terminal can render, and hands out
    curses color pairs on demand (curses only has a fixed pair table, so pairs
    are allocated lazily and cached)."""

    PAIR_BASE = 20   # color-pair IDs below this are the fixed UI pairs

    def __init__(self):
        self.n_colors = curses.COLORS if hasattr(curses, "COLORS") else 8
        self._table = _XTERM256 if self.n_colors >= 256 else _BASIC8_LAB
        self._color_cache = {}
        self._pair_cache = {}
        self._next_pair = self.PAIR_BASE
        self._max_pairs = min(getattr(curses, "COLOR_PAIRS", 64), 256)

        # Exact-color mode: borrow slots off the top of the palette and
        # redefine them, remembering what they held so exit can put them back.
        self.exact = False
        if USE_EXACT_TERMINAL_COLORS and self.n_colors >= 256:
            try:
                self.exact = curses.can_change_color()
            except curses.error:
                self.exact = False

        self._grad_map = {}
        self._saved_slots = {}
        self._reserved = set()   # slots claimed by the fixed bar colors
        self.init_color_errors = 0

        # Attribute the bars are drawn with. On the 8-color fallback, A_BOLD is
        # the only way to reach the bright half of the palette. With 256 colors
        # it buys nothing — the shade is already exact — and PDCurses treats it
        # as an intensity bit that ORs 8 into the color index, landing on a slot
        # we never redefined. So it is only ever set when we need it.
        self.bar_attr = 0 if self.n_colors >= 256 else curses.A_BOLD

    def _init_slot(self, idx: int, hexstr: str):
        """Overwrite one palette slot, remembering what it held.

        Reading the old value must never gate writing the new one: PDCurses
        advertises can_change_color() yet errors out of color_content(), and
        letting that failure abort the write is how Windows ended up with the
        stock palette. When the slot can't be read back, restore from the
        canonical xterm value instead.
        """
        if idx not in self._saved_slots:
            try:
                self._saved_slots[idx] = curses.color_content(idx)
            except curses.error:
                rgb = _XTERM256_RGB.get(idx)
                self._saved_slots[idx] = (
                    tuple(round(c * 1000) for c in rgb) if rgb else None)
        try:
            r, g, b = hex_to_rgb(hexstr)
            curses.init_color(idx, round(r * 1000), round(g * 1000), round(b * 1000))
        except curses.error:
            self.init_color_errors += 1   # slot keeps its default; nearest-match look

    def set_gradient(self, colors: List[str]):
        """Bind the hop gradient to a set of palette slots.

        Each stop claims the slot holding its nearest match that no earlier
        stop (or fixed bar color) has taken. Deduping matters because a plain
        nearest-match gives neighbouring hops the *same* index — a 20-hop
        grey→blue ramp collapses onto about 10 cube entries. When exact colors
        are available the claimed slot is then overwritten with the true color.
        """
        self._grad_map = {}
        taken = set(self._reserved)
        for hx in colors:
            if hx in self._color_cache:
                # Same hex as a fixed bar color (the ramp ends on COLOR_BAR_OK
                # by default): share its slot rather than burn a second one.
                idx = self._color_cache[hx]
            else:
                idx = self._nearest(hx, exclude=taken)
                if self.exact:
                    self._init_slot(idx, hx)
            taken.add(idx)
            self._grad_map[hx] = idx

    def _nearest(self, hexstr: str, exclude: Optional[set] = None) -> int:
        """Nearest terminal color index, compared in OKLab so the match is
        perceptual rather than a euclidean RGB guess. `exclude` skips indices
        already claimed; if that empties the table, collisions are allowed."""
        lab = rgb_to_oklab(*hex_to_rgb(hexstr))
        best, best_d = None, float("inf")
        for idx, t in self._table:
            if exclude and idx in exclude:
                continue
            d = (lab[0] - t[0]) ** 2 + (lab[1] - t[1]) ** 2 + (lab[2] - t[2]) ** 2
            if d < best_d:
                best, best_d = idx, d
        return best if best is not None else self._nearest(hexstr)

    def color(self, hexstr: str) -> int:
        """Slot index for a fixed (non-gradient) color. Its slot is reserved so
        the hop gradient can never claim it out from under the bar colors."""
        if hexstr in self._grad_map:
            return self._grad_map[hexstr]
        if hexstr in self._color_cache:
            return self._color_cache[hexstr]
        idx = self._nearest(hexstr, exclude=self._reserved)
        self._reserved.add(idx)
        if self.exact:
            self._init_slot(idx, hexstr)
        self._color_cache[hexstr] = idx
        return idx

    def clear_gradient(self):
        """Drop the hop mapping. Needed because a gradient stop can share a hex
        with a static color (the ramp ends on COLOR_BAR_OK by default) — left
        in place, it would answer the flat bars' color lookups too."""
        self._grad_map = {}

    def restore(self):
        """Put every borrowed palette slot back the way we found it."""
        for idx, rgb in self._saved_slots.items():
            if rgb is None:
                continue
            try:
                curses.init_color(idx, *rgb)
            except curses.error:
                pass
        self._saved_slots.clear()

    def pair(self, fg_hex: str, bg_hex: Optional[str] = None) -> int:
        """A curses attribute for this fg/bg combination. Falls back to the
        plain fg pair once the pair table is exhausted."""
        fg = self.color(fg_hex)
        bg = self.color(bg_hex) if bg_hex else -1
        key = (fg, bg)
        if key in self._pair_cache:
            return curses.color_pair(self._pair_cache[key])
        if self._next_pair >= self._max_pairs:
            return curses.color_pair(self._pair_cache.get((fg, -1), 0))
        pid = self._next_pair
        self._next_pair += 1
        try:
            curses.init_pair(pid, fg, bg)
        except curses.error:
            return 0
        self._pair_cache[key] = pid
        return curses.color_pair(pid)


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
    paths = report.default_csv_paths(base)
    if not paths:
        print(f"No {' or '.join(report.CSV_PATTERNS)} files found next to the program.",
              file=sys.stderr)
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

# Frame/divider attribute derived from UI_FRAME_BOLD (see _init_curses, _hline).
_FRAME_ATTR = curses.A_BOLD if UI_FRAME_BOLD else curses.A_NORMAL


@dataclass
class Hop:
    """One traceroute hop. `host` is None and `ms` is None when the hop stayed
    silent (a `*` line) — it still occupies its slot so hop numbering holds."""
    host: Optional[str]
    ms: Optional[float]


@dataclass
class PingResult:
    ms: Optional[float]        # trace mode: RTT of the final (destination) hop
    ts: float
    hops: Optional[List[Hop]] = None   # trace mode only


class CsvLogger:
    """Appends samples to an hourly CSV.

    Traceroute samples go to their own `pingtrace_*.csv` series: they need one
    column per hop, which doesn't fit the flat `pingtester_*.csv` schema the
    HTML report reads. Keeping the two file series apart means the report's
    glob never picks up a row shape it can't parse.
    """

    def __init__(self, enabled: bool = False):
        self.enabled = enabled
        self._lock = threading.Lock()
        self._base_dir = _app_base_dir()
        self._files = {}    # kind -> (file, writer, hour_key)

    def _writer_for(self, kind: str, hour_key: str):
        cur = self._files.get(kind)
        if cur and cur[2] == hour_key:
            return cur[1]
        if cur:
            cur[0].close()
        prefix = "pingtrace" if kind == "trace" else "pingtester"
        path = os.path.join(self._base_dir, f"{prefix}_{hour_key}.csv")
        new_file = not os.path.exists(path)
        f = open(path, "a", newline="")
        w = csv.writer(f)
        if new_file:
            if kind == "trace":
                # Each hop cell packs "address<delim>rtt_ms" so one column stays
                # one hop; a silent hop writes an empty cell.
                w.writerow(["host", "mode", "timestamp", "total_ms"]
                           + [f"hop{i:02d}" for i in range(1, TRACE_MAX_HOPS + 1)])
            else:
                w.writerow(["host", "mode", "timestamp", "ping_ms"])
        self._files[kind] = (f, w, hour_key)
        return w

    def log(self, host: str, mode: str, ts: float, ms: Optional[float],
            hops: Optional[List[Hop]] = None):
        if not self.enabled:
            return
        hour_key = time.strftime("%Y-%m-%d_%H", time.localtime(ts))
        ts_str = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))
        ms_str = f"{ms:.3f}" if ms is not None else ""
        kind = "trace" if mode == "trace" else "flat"
        with self._lock:
            w = self._writer_for(kind, hour_key)
            row = [host, mode, ts_str, ms_str]
            if kind == "trace":
                cells = []
                for i in range(TRACE_MAX_HOPS):
                    h = hops[i] if hops and i < len(hops) else None
                    if h is None or (h.host is None and h.ms is None):
                        cells.append("")
                    else:
                        rtt = f"{h.ms:.3f}" if h.ms is not None else ""
                        cells.append(f"{h.host or '*'}{CSV_HOP_DELIM}{rtt}")
                row += cells
            w.writerow(row)
            self._files[kind][0].flush()

    def close(self):
        with self._lock:
            for f, _, _ in self._files.values():
                f.close()
            self._files.clear()


class PingMonitor:
    MODES = ["icmp", "tcp", "http", "trace"]   # probe method; all yield an (ms, ts) sample

    def __init__(self, host: str, interval_ms: int, threshold_ms: float, scale_ms: float,
                 mode: str = "icmp", port: int = 443, logger: Optional[CsvLogger] = None):
        self.host = host
        self.interval_ms = interval_ms
        self.threshold_ms = threshold_ms
        self.scale_ms = scale_ms
        self.mode = mode if mode in self.MODES else "icmp"
        self.port = port
        self._logger  = logger
        self._results: deque = deque(maxlen=HISTORY_MAXLEN)
        self._total   = 0      # total pings ever appended (never decrements)
        self._running = True
        self._lock    = threading.Lock()
        # Reachability of the *current* (mode, host): True/False once probed,
        # invalidated to None whenever the config changes under us.
        self._probe_ok:  Optional[bool] = None
        self._probe_sig: Optional[tuple] = None
        self._dns_cache: dict = {}
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._running = False

    def _probe(self) -> Tuple[Optional[float], Optional[List[Hop]]]:
        """Dispatch to the active probe method. Returns (latency_ms, hops);
        latency is None on failure/timeout, hops is set only in trace mode."""
        if self.mode == "trace":
            return self._probe_trace()
        if self.mode == "tcp":
            return self._probe_tcp(), None
        if self.mode == "http":
            return self._probe_http(), None
        return self._probe_icmp(), None

    def _probe_icmp(self) -> Optional[float]:
        """ICMP echo via the OS `ping` binary. Cross-platform (win uses -n/-w, others -c/-W)."""
        try:
            if sys.platform == "win32":
                cmd = ["ping", "-n", "1", "-w", "1000", self.host]
            else:
                cmd = ["ping", "-c", "1", "-W", "1", self.host]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=ICMP_TIMEOUT_S)
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
            with socket.create_connection((host, port), timeout=TCP_TIMEOUT_S):
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
            url, method="GET", headers={"User-Agent": HTTP_USER_AGENT}
        )
        t0 = time.monotonic()
        try:
            with _HTTP_OPENER.open(req, timeout=HTTP_TIMEOUT_S) as resp:
                resp.read(1)   # first byte
                return (time.monotonic() - t0) * 1000.0
        except urllib.error.HTTPError:
            # Any HTTP status (3xx/4xx/5xx) means the server responded — valid sample.
            return (time.monotonic() - t0) * 1000.0
        except Exception:
            return None

    def _resolve(self, host: str) -> Optional[str]:
        """Cached forward lookup, used to tell whether a traceroute actually
        reached the destination or just ran out of hops."""
        if host in self._dns_cache:
            return self._dns_cache[host]
        try:
            ip = socket.gethostbyname(self._host_port()[0])
        except Exception:
            ip = None
        self._dns_cache[host] = ip
        return ip

    def _trace_cmd(self) -> List[str]:
        host = self._host_port()[0]
        if sys.platform == "win32":
            return ["tracert", "-d", "-h", str(TRACE_MAX_HOPS),
                    "-w", str(int(TRACE_HOP_WAIT_S * 1000)), host]
        cmd = ["traceroute", "-n",
               "-q", str(TRACE_QUERIES),
               "-w", str(TRACE_HOP_WAIT_S),
               "-m", str(TRACE_MAX_HOPS)]
        if sys.platform.startswith("linux"):
            # Probe all hops concurrently — turns a ~20 s serial walk into ~1 s.
            cmd += ["-N", "32"]
        return cmd + [host]

    @staticmethod
    def _parse_trace(out: str) -> List[Hop]:
        """Parse `traceroute -n` / `tracert -d` output into a dense hop list.

        Both formats put the hop number first, then some mix of `*`, the hop
        address, and one or more `<time> ms` readings. A hop that answered no
        probe becomes Hop(None, None) rather than disappearing, so hop N always
        sits at index N-1 and the chart's colors stay pinned to hop numbers.
        """
        def is_float(t: str) -> bool:
            try:
                float(t.lstrip("<"))
                return True
            except ValueError:
                return False

        hops: List[Hop] = []
        for line in out.splitlines():
            m = re.match(r"^\s*(\d+)\s+(.*)$", line)
            if not m:
                continue
            idx, rest = int(m.group(1)), m.group(2)
            if "timed out" in rest.lower() or "unable to resolve" in rest.lower():
                host, ms = None, None
            else:
                toks = rest.split()
                host = next((t for t in toks
                             if t not in ("*", "ms") and not is_float(t)
                             and not t.startswith("!")), None)
                times = [float(toks[i - 1].lstrip("<"))
                         for i, t in enumerate(toks)
                         if t == "ms" and i > 0 and is_float(toks[i - 1])]
                ms = min(times) if times else None
            while len(hops) < idx - 1:      # pad over any skipped hop number
                hops.append(Hop(None, None))
            if len(hops) == idx - 1:
                hops.append(Hop(host, ms))
            else:
                hops[idx - 1] = Hop(host, ms)
        return hops

    def _probe_trace(self) -> Tuple[Optional[float], Optional[List[Hop]]]:
        """Walk the full path to the host, timing every hop.

        The reported latency is the destination's own RTT, and it is None when
        the trace never reached the destination — otherwise a path that dies
        halfway would silently report the last router's RTT as if it were the
        target's, and loss would read 0%.
        """
        try:
            r = subprocess.run(self._trace_cmd(), capture_output=True, text=True,
                               timeout=TRACE_TIMEOUT_S)
        except Exception:
            return None, None
        hops = self._parse_trace(r.stdout)
        if not hops:
            return None, None
        dest_ip = self._resolve(self.host)
        total = None
        last = hops[-1]
        if last.ms is not None and (dest_ip is None or last.host == dest_ip):
            total = last.ms
        return total, hops

    def _loop(self):
        while self._running:
            t0 = time.monotonic()
            sig = (self.mode, self.host, self.port)   # config this sample belongs to
            ms, hops = self._probe()
            result = PingResult(ms=ms, ts=time.time(), hops=hops)
            with self._lock:
                self._results.append(result)
                self._total += 1
                self._probe_ok  = ms is not None
                self._probe_sig = sig
            if self._logger:
                self._logger.log(self.host, self.mode, result.ts, result.ms, result.hops)
            interval = self.interval_ms
            if self.mode == "trace":
                interval = max(interval, TRACE_MIN_INTERVAL_MS)
            sleep = max(0.0, interval / 1000.0 - (time.monotonic() - t0))
            time.sleep(sleep)

    @property
    def effective_interval_ms(self) -> int:
        """The pace samples actually arrive at — trace mode is floored, since a
        full traceroute takes about a second no matter what interval is set."""
        if self.mode == "trace":
            return max(self.interval_ms, TRACE_MIN_INTERVAL_MS)
        return self.interval_ms

    def reachable(self) -> Optional[bool]:
        """Whether the *current* (mode, host, port) is answering.
        None = not yet probed since the last config change."""
        with self._lock:
            if self._probe_sig != (self.mode, self.host, self.port):
                return None
            return self._probe_ok

    def clear(self):
        """Drop all history. Used when switching between trace and single-probe
        modes, whose samples aren't comparable — a stacked per-hop bar and a
        single latency bar can't share a chart."""
        with self._lock:
            self._results.clear()
            self._total = 0
            self._probe_ok = None
            self._probe_sig = None

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


# ── themes ──────────────────────────────────────────────────────────────────
#
# Every knob under "VISUAL CONFIGURATION" can be overridden by a theme. A theme
# is any  themes/ptheme-<name>.py  that assigns some of the names in
# Theme.FIELDS at module level; whatever it leaves out inherits pingtester.py's
# built-in look above. Themes are cycled live in-app with the [c] key.
#
#   • No theme files          → the built-in colors in this file are used.
#   • themes/ptheme-default.py → becomes the startup theme (still cycle-able).
#
# The built-in look is itself entry 0 in the cycle, so [c] can always return to
# it. See the shipped themes/ptheme-*.py files for the format.

class Theme:
    # Visual knobs a theme may set. Each name here is also a module-level
    # constant above, which supplies the built-in default when a theme is
    # silent about it.
    FIELDS = (
        "CHART_BLOCKS", "CHART_TIMEOUT_GLYPH", "CHART_THRESHOLD_GLYPH",
        "CHART_LEGEND_SWATCH",
        "COLOR_BAR_OK", "COLOR_BAR_WARN", "COLOR_BAR_OVER",
        "UI_COLOR_BORDER", "UI_COLOR_TITLE", "UI_COLOR_STAT_VALUE",
        "UI_COLOR_STAT_LABEL", "UI_COLOR_OK", "UI_COLOR_ALERT", "UI_COLOR_DIM",
        "UI_FRAME_BOLD", "CHART_BAR_TIP_DARKEN", "CHART_SUBCELL_BLEND",
        "TRACE_GRADIENT_START", "TRACE_GRADIENT_END", "TRACE_HOP_SEPARATOR_COLOR",
        "TRACE_MIN_SEGMENT_ROWS", "TRACE_HOP_SEPARATOR_PX", "TRACE_SHOW_LEGEND",
    )

    def __init__(self, name: str, values: dict):
        self.name = name
        for f in self.FIELDS:
            setattr(self, f, values[f])


THEME_DIR = os.path.join(_app_base_dir(), "themes")


def _builtin_theme() -> "Theme":
    """The look defined by the constants in pingtester.py itself."""
    g = globals()
    return Theme("builtin", {f: g[f] for f in Theme.FIELDS})


def _load_theme_file(path: str) -> Optional["Theme"]:
    """Execute one ptheme-*.py and fold whatever knobs it set over the built-in
    defaults. A theme that fails to import is skipped rather than crashing the
    app, so one broken file can't take the whole theme picker down."""
    ns: dict = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            code = compile(f.read(), path, "exec")
        exec(code, ns)
    except Exception:
        return None
    g = globals()
    values = {f: ns.get(f, g[f]) for f in Theme.FIELDS}
    stem = os.path.basename(path)[len("ptheme-"):-len(".py")]
    return Theme(str(ns.get("THEME_NAME", stem)), values)


def load_themes() -> Tuple[List["Theme"], int]:
    """Discover selectable themes.

    Returns (themes, default_idx). themes[0] is always the built-in look; any
    themes/ptheme-*.py files follow in filename order. A themes/ptheme-default.py,
    if present, sets the startup theme; otherwise the built-in is the default.
    """
    themes = [_builtin_theme()]
    default_idx = 0
    if os.path.isdir(THEME_DIR):
        for path in sorted(glob.glob(os.path.join(THEME_DIR, "ptheme-*.py"))):
            t = _load_theme_file(path)
            if t is None:
                continue
            themes.append(t)
            if os.path.basename(path) == "ptheme-default.py":
                default_idx = len(themes) - 1
    return themes, default_idx


class App:
    HOSTS = PRESET_HOSTS            # cycled with 'h' — edit PRESET_HOSTS at top of file
    TIME_STEPS = TIME_STEPS         # view-window steps — edit TIME_STEPS at top of file

    def __init__(self, stdscr, mon: PingMonitor, logger: Optional[CsvLogger] = None,
                 auto_scale: bool = False,
                 themes: Optional[List[Theme]] = None, theme_idx: int = 0):
        self.scr = stdscr
        self.mon = mon
        self._logger = logger
        self._auto_scale = auto_scale
        # Remember the scale auto-fit is about to take over, so leaving trace
        # can hand it back — including when we started in trace mode.
        self._scale_before_trace: Optional[float] = mon.scale_ms if auto_scale else None
        self._inp_mode: Optional[str] = None
        self._inp_buf = ""
        self._msg = ""
        self._msg_until = 0.0
        self._msg_color = _OK
        self._time_idx = 4   # default: TIME_STEPS[4] = 300 s = 5 min
        self._pal_n = 0
        self._pal_colors: List[str] = []
        self._hop_color_of: List[Optional[str]] = []   # hop index → color, None if silent
        self._dark_cache: dict = {}
        # Selectable themes cycled with [c]; theme[0] is always the built-in look.
        self._themes = themes if themes else [_builtin_theme()]
        self._theme_idx = theme_idx if 0 <= theme_idx < len(self._themes) else 0
        self.theme = self._themes[self._theme_idx]
        self.pal: Optional[Palette] = None
        self._frame_attr = curses.A_NORMAL
        self._init_curses()

    @staticmethod
    def _nice_scale(v: float) -> float:
        """Round up to the next scale step, so the axis lands on a readable number."""
        for s in SCALE_STEPS:
            if s >= v:
                return float(s)
        return float(SCALE_STEPS[-1])

    def _auto_fit_scale(self):
        """Track the path's peak RTT with the Y-scale.

        Grows only once a bar actually clips, and shrinks only when a whole step
        of headroom has opened up. Because the steps are ~2x apart, a peak that
        triggers a change always lands comfortably inside the new scale, so the
        axis can't oscillate between two values.
        """
        vals = [r.ms for r in self.mon.recent(TRACE_AUTO_SCALE_SAMPLES) if r.ms is not None]
        if not vals:
            return
        peak = max(vals)
        cur = self.mon.scale_ms
        target = self._nice_scale(peak * TRACE_AUTO_SCALE_HEADROOM)
        if peak >= cur or target < cur:
            if target != cur:
                self.mon.scale_ms = target
                self._flash(f"Y-scale → {target:.0f} ms (auto)", color=_BORDER)

    def _hop_colors(self, n: int) -> List[str]:
        """OKLab gradient across n hops, recomputed only when the path length
        changes so hop→color stays put frame to frame."""
        if n != self._pal_n:
            self._pal_colors = oklab_gradient(
                self.theme.TRACE_GRADIENT_START, self.theme.TRACE_GRADIENT_END, n)
            self._pal_n = n
            self.pal.set_gradient(self._pal_colors)
        return self._pal_colors

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
        self._apply_theme(self.theme)

    def _apply_theme(self, theme: Theme):
        """Point every visual knob at `theme` and (re)bind the fixed UI color
        pairs. Safe to call at runtime: the [c] key cycles themes through here.

        Each switch builds a fresh Palette — the previous one first hands back
        any terminal palette slots it borrowed, then the new theme's hex colors
        are remapped onto whatever the terminal can show. The hop gradient and
        the tip-darken cache are reset so they rebuild against the new colors.
        """
        self.theme = theme
        self._frame_attr = curses.A_BOLD if theme.UI_FRAME_BOLD else curses.A_NORMAL
        self._dark_cache = {}
        self._pal_n = 0
        self._pal_colors = []
        self._hop_color_of = []
        if self.pal is not None:
            self.pal.restore()
        bg = -1
        # Build the palette first: the fixed UI pairs (IDs 1..8) map their hex
        # through it, exactly like the bars, so the chrome lands on the exact
        # shade where the terminal allows and the nearest match where it doesn't.
        self.pal = Palette()
        curses.init_pair(_BORDER, self.pal.color(theme.UI_COLOR_BORDER),     bg)
        curses.init_pair(_TITLE,  self.pal.color(theme.UI_COLOR_TITLE),      bg)
        curses.init_pair(_BAR_OK, self.pal.color(theme.COLOR_BAR_OK),        bg)
        curses.init_pair(_BAR_HI, self.pal.color(theme.UI_COLOR_ALERT),      bg)
        curses.init_pair(_STAT_V, self.pal.color(theme.UI_COLOR_STAT_VALUE), bg)
        curses.init_pair(_STAT_L, self.pal.color(theme.UI_COLOR_STAT_LABEL), bg)
        curses.init_pair(_OK,     self.pal.color(theme.UI_COLOR_OK),         bg)
        curses.init_pair(_DIM,    self.pal.color(theme.UI_COLOR_DIM),        bg)

    # ── helpers ──────────────────────────────────────────────────────────

    def _put(self, y, x, s, attr=0):
        try:
            H, W = self.scr.getmaxyx()
            if 0 <= y < H and 0 <= x < W - 1:
                self.scr.addstr(y, x, s[:max(0, W - x - 1)], attr)
        except curses.error:
            pass

    def _hline(self, row, W):
        b = curses.color_pair(_BORDER) | self._frame_attr
        self._put(row, 0, "├" + "─" * (W - 2) + "┤", b)

    def _flash(self, msg: str, secs: float = 2.0, color: int = _OK):
        self._msg = msg
        self._msg_until = time.monotonic() + secs
        self._msg_color = color

    # ── input handling ───────────────────────────────────────────────────

    def run(self):
        try:
            while True:
                if not self._handle_key():
                    break
                self._draw()
        finally:
            self.pal.restore()   # hand the terminal back its original palette
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
            prev = self.mon.mode
            self.mon.mode = self.mon.MODES[(idx + 1) % len(self.mon.MODES)]
            # Crossing into or out of trace changes what a sample *is*, so the
            # old history can't be charted. Single-probe modes all yield one
            # latency per sample, so those switches keep their history.
            if (prev == "trace") != (self.mon.mode == "trace"):
                self.mon.clear()
                if self.mon.mode == "trace":
                    self._scale_before_trace = self.mon.scale_ms
                    self._auto_scale = TRACE_AUTO_SCALE
                else:
                    # Only undo a scale we chose ourselves; a manual one is the
                    # user's and follows them out of trace mode.
                    if self._auto_scale and self._scale_before_trace is not None:
                        self.mon.scale_ms = self._scale_before_trace
                    self._auto_scale = False
                    self._scale_before_trace = None
            self._flash(f"Mode → {self.mon.mode.upper()}")
        elif k in ("c", "C"):
            # Cycle the color theme. Entry 0 is always the built-in look, so
            # this always loops back to pingtester.py's own colors.
            if len(self._themes) > 1:
                self._theme_idx = (self._theme_idx + 1) % len(self._themes)
                self._apply_theme(self._themes[self._theme_idx])
                self._flash(f"Theme → {self.theme.name}", color=_TITLE)
            else:
                self._flash("No themes found — add themes/ptheme-*.py", color=_STAT_V)
        elif k == "p" and self.mon.mode == "tcp":
            self._start_input("port")
        elif k == "i":
            self._start_input("interval")
        elif k == "t" and self.mon.mode != "trace":
            self._start_input("threshold")
        elif k in ("+", "=", "-", "KEY_UP", "KEY_DOWN"):
            # ↑ / '+' zoom in: a smaller full-scale value makes the bars taller.
            # ↓ / '-' zoom out. The two arrows pair with ◄/► so the whole chart
            # is steerable from the four arrow keys; +/- stay as aliases.
            cur = self.mon.scale_ms
            zoom_out = k in ("-", "KEY_DOWN")
            if zoom_out:
                above = [s for s in SCALE_STEPS if s > cur]
                self.mon.scale_ms = float(above[0] if above else SCALE_STEPS[-1])
            else:
                below = [s for s in SCALE_STEPS if s < cur]
                self.mon.scale_ms = float(below[-1] if below else SCALE_STEPS[0])
            self._auto_scale = False        # touching the scale takes it manual
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

        b  = curses.color_pair(_BORDER) | self._frame_attr

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
        # Trace mode has no threshold: bars are colored by hop, not by latency.
        if self.mon.mode == "trace":
            hops = self._latest_hops()
            warn_tag = f"   hops: {len(hops)}" if hops else "   hops: —"
        else:
            warn_tag = f"   warn: >{self.mon.threshold_ms:.0f}ms"
        conf = (
            f"  {mode_tag}"
            f"   host: {self.mon.host}"
            f"   every: {self.mon.interval_ms}ms"
            + warn_tag +
            f"   yscale: {self.mon.scale_ms:.0f}ms{' (auto)' if self._auto_scale else ''}"
            f"   view: {self._fmt_time(self._time_range_s)}"
            + log_tag
        )
        self._put(1, 1, conf, curses.color_pair(_STAT_L))

        # top-right notification area: transient flash takes priority, otherwise
        # a persistent alert-colored reminder while in HTTP mode.
        flash_active = self._msg and time.monotonic() < self._msg_until
        if flash_active:
            msg = f"  ▸ {self._msg}"
            self._put(1, W - len(msg) - 2, msg, curses.color_pair(self._msg_color) | curses.A_BOLD)
        elif self.mon.mode == "http" and self.mon.reachable() is False:
            # HTTP probes are failing → this host isn't actually serving web.
            msg = "Host isn't answering HTTP — switch to a real webhost"
            self._put(1, W - len(msg) - 2, msg, curses.color_pair(_BAR_HI) | curses.A_BOLD)

        self._hline(2, W)

        # chart: rows 3 .. H-6  (exclusive); trace mode gives up one row to the
        # hop legend, but only if the chart can still spare it.
        chart_top = 3
        chart_bot = H - 5
        legend_row = None
        if self.mon.mode == "trace" and self._auto_scale:
            self._auto_fit_scale()
        if self.mon.mode != "trace" and self._pal_n:
            self.pal.clear_gradient()
            self._pal_n = 0
            self._hop_color_of = []
        if (self.mon.mode == "trace" and self.theme.TRACE_SHOW_LEGEND
                and chart_bot > chart_top + 4):
            chart_bot -= 1
            legend_row = chart_bot
        if chart_bot > chart_top + 2:
            self._draw_chart(chart_top, chart_bot, W)
        if legend_row is not None:
            self._draw_legend(legend_row, W)

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
        interval_s  = self.mon.effective_interval_ms / 1000.0
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

    def _latest_hops(self) -> List[Hop]:
        """Hops of the most recent trace sample that produced any."""
        for r in reversed(self.mon.recent(8)):
            if r.hops:
                return r.hops
        return []

    @staticmethod
    def _bucket_hops(bucket: List[PingResult]) -> List[Optional[float]]:
        """Average each hop's RTT across the samples in one display column.
        Index = hop number - 1; None where no sample in the bucket got a reply."""
        n = max((len(r.hops) for r in bucket if r.hops), default=0)
        out: List[Optional[float]] = []
        for i in range(n):
            vals = [r.hops[i].ms for r in bucket
                    if r.hops and i < len(r.hops) and r.hops[i].ms is not None]
            out.append(sum(vals) / len(vals) if vals else None)
        return out

    def _hop_spans(self, hop_ms: List[Optional[float]], color_of: List[Optional[str]],
                   scale: float, bar_rows: int) -> List[Tuple[str, int, int]]:
        """Turn per-hop RTTs into stacked [color, start_px, end_px) spans, in
        eighth-of-a-row pixels from the baseline.

        Each hop's segment is the extra latency it adds over everything before
        it. RTTs aren't guaranteed to increase along a path (a hop can reply
        faster than its predecessor), so the running total is clamped to be
        monotonic — the stack's full height is the largest RTT on the path,
        which for a healthy trace is the destination's.

        Internal hop boundaries snap to whole rows. A character cell can show
        only two colors, so a separator wedged between two hops mid-cell would
        be the one thing dropped; landing each boundary on a row edge leaves the
        separator sitting in that row's top eighth, where it always renders.

        The stack's total height stays exact to the pixel, and the minimum row
        per hop is carved out of the rows *within* it. Growing the stack instead
        would let the hop count, not the latency, decide the bar's height —
        which pins every bar to the same height and freezes the chart.
        """
        max_px = bar_rows * 8
        sep = self.theme.TRACE_HOP_SEPARATOR_PX if self.theme.CHART_SUBCELL_BLEND else 0
        min_rows = max(0, self.theme.TRACE_MIN_SEGMENT_ROWS)

        cums: List[int] = []      # exact cumulative height of each answering hop
        colors: List[str] = []
        cum_ms = 0.0
        for i, ms in enumerate(hop_ms):
            if ms is None or i >= len(color_of) or color_of[i] is None:
                continue
            cum_ms = max(cum_ms, ms)
            cums.append(int(round(min(cum_ms, scale) / scale * max_px)))
            colors.append(color_of[i])
        if not cums or cums[-1] <= 0:
            return []
        total, n = cums[-1], len(cums)

        # Highest row an internal boundary may sit on while still leaving the
        # top hop a pixel of its own.
        top_row = (total - 1) // 8

        blocks: List[Tuple[str, int, int]] = []
        if min_rows and top_row >= (n - 1) * min_rows:
            # Room for a whole row each. Snap every boundary to a row edge, which
            # is the only place a separator can live: it occupies that row's top
            # eighth, where a cell can still show it alongside the hop below.
            prev = 0
            for i in range(n - 1):
                lo = prev + min_rows
                hi = top_row - (n - 2 - i) * min_rows
                row = min(max(int(round(cums[i] / 8)), lo), hi)
                blocks.append((colors[i], prev * 8, row * 8))
                prev = row
            blocks.append((colors[-1], prev * 8, total))
        else:
            # Too short to seat every hop on its own row. Keep each hop's color
            # by giving it at least a pixel — losing the gradient matters more
            # than losing the separators, which have nowhere to go at this size.
            prev = 0
            for i in range(n - 1):
                hi = total - (n - 1 - i)      # reserve a pixel for each hop above
                e = min(max(cums[i], prev + 1), hi) if min_rows else min(cums[i], hi)
                if e > prev:
                    blocks.append((colors[i], prev, e))
                    prev = e
            blocks.append((colors[-1], prev, total))

        spans: List[Tuple[str, int, int]] = []
        for k, (color, s, e) in enumerate(blocks):
            # A separator needs a row edge to sit on and a block thick enough to
            # carve it from; otherwise the block is drawn whole.
            if sep and k < len(blocks) - 1 and e - s > sep and e % 8 == 0:
                spans.append((color, s, e - sep))
                spans.append((self.theme.TRACE_HOP_SEPARATOR_COLOR, e - sep, e))
            else:
                spans.append((color, s, e))
        return spans

    def _raster_stack(self, spans: List[Tuple[str, int, int]],
                      bar_rows: int) -> dict:
        """Rasterize a bar's colored pixel spans into character cells.

        A cell is 8 pixels tall but can only carry one glyph. Where two colors
        meet inside a cell we draw the lower one as a partial block and paint
        the upper one as the cell's background, so the boundary lands on the
        exact pixel instead of snapping to a row edge. Three or more colors in
        one cell can't all be shown; the outermost two win.

        Returns {row_from_bottom: (glyph, fg_hex, bg_hex_or_None)}.
        """
        if not spans:
            return {}
        blocks = self.theme.CHART_BLOCKS
        blend = self.theme.CHART_SUBCELL_BLEND
        top_px = spans[-1][2]
        cells = {}
        for r in range(bar_rows):
            lo, hi = r * 8, r * 8 + 8
            cover = [(c, max(s, lo), min(e, hi)) for c, s, e in spans
                     if min(e, hi) > max(s, lo)]
            if not cover:
                continue
            if hi <= top_px:                       # cell is fully painted
                if len(cover) == 1:
                    cells[r] = (blocks[8], cover[0][0], None)
                else:
                    boundary = cover[0][2] - lo    # where the lowest band ends
                    if blend and 1 <= boundary <= 7:
                        cells[r] = (blocks[boundary], cover[0][0], cover[-1][0])
                    else:
                        cells[r] = (blocks[8], cover[-1][0], None)
            else:                                  # topmost, partly filled cell
                frac = min(8, top_px - lo)
                if frac > 0:
                    cells[r] = (blocks[frac], cover[-1][0], None)
        return cells

    def _draw_chart(self, top: int, bot: int, W: int):
        CH    = bot - top
        scale = self.mon.scale_ms
        thresh = self.mon.threshold_ms
        is_trace = self.mon.mode == "trace"

        YW    = CHART_Y_AXIS_WIDTH
        ax    = YW + 1
        bar_x = ax + 1
        bar_w = W - bar_x - 1

        b = curses.color_pair(_BORDER) | self._frame_attr

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

        # threshold dash line — meaningless in trace mode, where bar color
        # encodes the hop rather than "fast or slow", so it's omitted there.
        if not is_trace and 0 < thresh < scale:
            t_row = top + int(bar_rows * (1.0 - thresh / scale))
            t_row = max(top, min(bot - 2, t_row))
            lbl = f"{thresh:.0f}"
            self._put(t_row, ax - len(lbl), lbl, curses.color_pair(_STAT_V))
            self._put(t_row, ax, "┼", curses.color_pair(_STAT_V) | curses.A_BOLD)
            for cx in range(bar_x, bar_x + bar_w):
                self._put(t_row, cx, self.theme.CHART_THRESHOLD_GLYPH,
                          curses.color_pair(_STAT_V) | curses.A_DIM)

        # x-axis
        self._put(bot - 1, ax, "└" + "─" * bar_w, b)

        # build column buckets and draw bars
        columns = self._build_columns(bar_w)
        if is_trace:
            self._draw_bars_trace(columns, bar_x, top, bot, bar_rows, scale)
        else:
            self._draw_bars_flat(columns, bar_x, top, bot, bar_rows, scale, thresh)

        # x-axis labels: left shows window size + aggregation hint
        interval_s  = self.mon.effective_interval_ms / 1000.0
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

    def _darken(self, hexstr: str) -> str:
        if hexstr not in self._dark_cache:
            self._dark_cache[hexstr] = oklab_darken(hexstr, self.theme.CHART_BAR_TIP_DARKEN)
        return self._dark_cache[hexstr]

    def _flat_spans(self, avg_ms: float, thresh: float, scale: float,
                    bar_rows: int) -> List[Tuple[str, int, int]]:
        """Split one bar into colored bands, in eighth-of-a-row pixels.

        The bar is the theme's OK color up to the threshold line and its WARN
        color above it, so only the part that actually breached shows as a
        warning rather than the whole column flipping color.
        """
        ok, warn = self.theme.COLOR_BAR_OK, self.theme.COLOR_BAR_WARN
        max_px = bar_rows * 8
        val_px = int(round(min(avg_ms, scale) / scale * max_px))
        if val_px <= 0:
            return []
        th_px = int(round(min(thresh, scale) / scale * max_px)) if thresh > 0 else 0
        th_px = max(0, min(th_px, max_px))
        if val_px <= th_px:
            return [(ok, 0, val_px)]
        spans = []
        if th_px > 0:
            spans.append((ok, 0, th_px))
        spans.append((warn, th_px, val_px))
        return spans

    def _draw_bars_flat(self, columns, bar_x, top, bot, bar_rows, scale, thresh):
        """One bar per column, banded at the threshold. The topmost cell is
        capped a shade darker than the band beneath it — or in COLOR_BAR_OVER
        when the value ran past the top of the chart and the bar was clipped."""
        for col, bucket in enumerate(columns):
            cx = bar_x + col
            if not bucket:
                continue
            valid = [r.ms for r in bucket if r.ms is not None]
            if not valid:
                self._put(bot - 2, cx, self.theme.CHART_TIMEOUT_GLYPH,
                          curses.color_pair(_BAR_HI) | curses.A_DIM)
                continue

            avg_ms = sum(valid) / len(valid)
            spans  = self._flat_spans(avg_ms, thresh, scale, bar_rows)
            if not spans:
                continue
            cells = self._raster_stack(spans, bar_rows)
            if not cells:
                continue

            # Cap the bar. In a blended cell the upper color is the background,
            # so that is what the cap has to replace.
            tip_hex = self.theme.COLOR_BAR_OVER if avg_ms > scale else self._darken(spans[-1][0])
            top_r = max(cells)
            glyph, fg, bg = cells[top_r]
            cells[top_r] = (glyph, fg, tip_hex) if bg else (glyph, tip_hex, None)

            for r, (glyph, fg, bg) in cells.items():
                row = bot - 2 - r
                if top <= row < bot - 1:
                    self._put(row, cx, glyph, self.pal.pair(fg, bg) | self.pal.bar_attr)

    def _draw_bars_trace(self, columns, bar_x, top, bot, bar_rows, scale):
        """One stacked bar per column: a colored segment per hop, bottom-up."""
        buckets = [self._bucket_hops(b) if b else [] for b in columns]
        n_hops = max((len(h) for h in buckets), default=0)
        if n_hops == 0:
            return

        # The gradient spans the hops that actually answer, not every hop
        # number: a silent (`*`) hop draws nothing, so including it would spend
        # part of the ramp on a color that never appears and leave the visible
        # hops stopping short of the end color. One mapping for the whole
        # window — a per-column ramp would recolor every bar in the chart
        # whenever a single hop dropped a reply.
        tracked = [i for i in range(n_hops)
                   if any(i < len(b) and b[i] is not None for b in buckets)]
        if not tracked:
            return
        colors = self._hop_colors(len(tracked))
        color_of: List[Optional[str]] = [None] * n_hops
        for rank, i in enumerate(tracked):
            color_of[i] = colors[rank]
        self._hop_color_of = color_of      # the legend reuses this mapping

        for col, hop_ms in enumerate(buckets):
            cx = bar_x + col
            if not hop_ms:
                continue
            if all(m is None for m in hop_ms):
                self._put(bot - 2, cx, self.theme.CHART_TIMEOUT_GLYPH,
                          curses.color_pair(_BAR_HI) | curses.A_DIM)
                continue
            spans = self._hop_spans(hop_ms, color_of, scale, bar_rows)
            for r, (glyph, fg, bg) in self._raster_stack(spans, bar_rows).items():
                row = bot - 2 - r
                if top <= row < bot - 1:
                    self._put(row, cx, glyph, self.pal.pair(fg, bg) | self.pal.bar_attr)

    # ── hop legend ────────────────────────────────────────────────────────

    def _draw_legend(self, row: int, W: int):
        hops = self._latest_hops()
        if not hops:
            self._put(row, 2, "tracing…", curses.color_pair(_DIM) | curses.A_DIM)
            return
        # Same hop→color mapping the chart just used, so swatches can't drift
        # out of step with the bars.
        color_of = self._hop_color_of
        swatch = self.theme.CHART_LEGEND_SWATCH
        dim = curses.color_pair(_DIM) | curses.A_DIM
        x = 2
        for i, h in enumerate(hops):
            label = h.host or "*"
            chunk = f"{swatch} {i + 1} {label}"
            if x + len(chunk) + 2 > W - 2:
                self._put(row, x, "…", dim)
                break
            hex_c = color_of[i] if i < len(color_of) else None
            # A silent hop has no segment in the chart, so it gets no swatch.
            self._put(row, x, swatch if hex_c else " ",
                      self.pal.pair(hex_c) | self.pal.bar_attr if hex_c else dim)
            x += 2
            self._put(row, x, f"{i + 1}", dim)
            x += len(str(i + 1)) + 1
            self._put(row, x, label, curses.color_pair(_STAT_L) if hex_c else dim)
            x += len(label) + 2

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
                hot  = self.mon.mode != "trace" and r.ms > self.mon.threshold_ms
                attr = curses.color_pair(_BAR_HI if hot else _OK) | curses.A_BOLD
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
            ("c",    "theme",       n),
            ("h",    "cycle host",  n),
            ("H",    "custom host", n),
        ]
        if self.mon.mode == "tcp":
            keys.append(("p", "tcp port", n))
        keys.append(("i", "interval", n))
        if self.mon.mode != "trace":     # no threshold when bars are hop-colored
            keys.append(("t", "threshold", n))
        keys += [
            ("▲/▼",  "yscale",      n),
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
    ap.add_argument("--host",      default=DEFAULT_HOST,  help=f"Target host (default: {DEFAULT_HOST})")
    ap.add_argument("--mode",      default=DEFAULT_MODE, choices=PingMonitor.MODES,
                    help=f"Probe method: icmp | tcp | http (default: {DEFAULT_MODE})")
    ap.add_argument("--port",      type=int,   default=DEFAULT_PORT,   help=f"TCP-mode port (default: {DEFAULT_PORT})")
    ap.add_argument("--interval",  type=int,   default=DEFAULT_INTERVAL_MS,  help=f"Ping interval ms (default: {DEFAULT_INTERVAL_MS})")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_MS, help=f"Warn threshold ms (default: {DEFAULT_THRESHOLD_MS:.0f})")
    ap.add_argument("--scale",     type=float, default=None, help=f"Chart Y-scale ms (default: {DEFAULT_SCALE_MS:.0f}; trace mode auto-fits unless given)")
    ap.add_argument("--log",       action="store_true",       help="Enable CSV logging at startup")
    ap.add_argument("--generate-report", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    # Hidden mode: generate the HTML report and exit (used by the in-app 'g' key).
    if args.generate_report:
        run_report_cli(args.threshold)
        return

    logger = CsvLogger(enabled=args.log)
    # An explicit --scale is the user's; without one, trace mode fits its own.
    scale = DEFAULT_SCALE_MS if args.scale is None else args.scale
    auto_scale = (args.scale is None and args.mode == "trace" and TRACE_AUTO_SCALE)
    themes, theme_idx = load_themes()
    mon = PingMonitor(args.host, args.interval, args.threshold, scale,
                      mode=args.mode, port=args.port, logger=logger)
    try:
        curses.wrapper(lambda s: App(s, mon, logger=logger, auto_scale=auto_scale,
                                     themes=themes, theme_idx=theme_idx).run())
    except KeyboardInterrupt:
        pass
    finally:
        mon.stop()
        logger.close()
    print("Goodbye.")


if __name__ == "__main__":
    main()
