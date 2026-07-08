#!/usr/bin/env python3
"""pingtester-report — generate an HTML report from pingtester CSV logs"""

import csv
import glob
import json
import math
import os
import sys
import argparse
from datetime import datetime
from collections import defaultdict
from pathlib import Path
from typing import Optional, List, Dict, Any


# ═══════════════════════════════════════════════════════════════════════════
#  USER-EDITABLE CONFIGURATION
#  External assets the generated HTML loads from a CDN when opened in a browser.
#  Swap these for self-hosted copies to make the report fully offline/private.
# ═══════════════════════════════════════════════════════════════════════════

# Google Fonts hosts to <link rel="preconnect"> (gstatic serves the font files).
FONT_PRECONNECT = ["https://fonts.googleapis.com", "https://fonts.gstatic.com"]

# The JetBrains Mono stylesheet.
FONT_CSS_URL = ("https://fonts.googleapis.com/css2?"
                "family=JetBrains+Mono:wght@300;400;500;600;700&display=swap")

# Chart.js and the plugins the report needs, loaded in order from jsdelivr.
CDN_SCRIPTS = [
    "https://cdn.jsdelivr.net/npm/moment@2.29.4/moment.min.js",
    "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js",
    "https://cdn.jsdelivr.net/npm/chartjs-adapter-moment@1.0.1/dist/chartjs-adapter-moment.min.js",
    "https://cdn.jsdelivr.net/npm/hammerjs@2.0.8/hammer.min.js",
    "https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.min.js",
]

# ═══════════════════════════════════════════════════════════════════════════


def _head_assets() -> str:
    """Build the <link>/<script> tags for the external assets listed above."""
    tags = []
    for u in FONT_PRECONNECT:
        cross = " crossorigin" if "gstatic" in u else ""
        tags.append(f'<link rel="preconnect" href="{u}"{cross}>')
    tags.append(f'<link href="{FONT_CSS_URL}" rel="stylesheet">')
    tags += [f'<script src="{u}"></script>' for u in CDN_SCRIPTS]
    return "\n".join(tags)


# ── data loading ──────────────────────────────────────────────────────────────

def load_csvs(paths: List[str]) -> List[Dict]:
    rows: List[Dict] = []
    seen: set = set()
    for path in sorted(paths):
        try:
            with open(path, newline='', encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    # 'mode' column added later; older logs were all ICMP.
                    mode = (row.get('mode') or 'icmp').strip() or 'icmp'
                    key = (row.get('host', ''), mode, row.get('timestamp', ''))
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        ts = datetime.fromisoformat(row['timestamp'])
                        ms_str = row['ping_ms'].strip()
                        ms = float(ms_str) if ms_str else None
                        rows.append({'ts': ts, 'ms': ms, 'host': row.get('host', '?'),
                                     'mode': mode})
                    except (ValueError, KeyError):
                        pass
        except OSError as e:
            print(f"Warning: {e}", file=sys.stderr)
    rows.sort(key=lambda r: r['ts'])
    return rows


# ── statistics ────────────────────────────────────────────────────────────────

def compute_stats(rows: List[Dict]) -> Dict:
    vals = [r['ms'] for r in rows if r['ms'] is not None]
    n = len(rows)
    lost = n - len(vals)
    if not vals:
        return dict(min=None, max=None, avg=None, p95=None,
                    loss=round(100.0 if n else 0.0, 2), total=n)
    s = sorted(vals)
    return dict(
        min=round(min(vals), 3),
        max=round(max(vals), 3),
        avg=round(sum(vals) / len(vals), 3),
        p95=round(s[min(int(len(s) * 0.95), len(s) - 1)], 3),
        loss=round(lost / n * 100, 2),
        total=n,
    )


def compute_hourly(rows: List[Dict]) -> List[Dict]:
    buckets: Dict[str, List] = defaultdict(list)
    for row in rows:
        key = row['ts'].strftime('%Y-%m-%dT%H:00')
        buckets[key].append(row)
    result = []
    for key in sorted(buckets):
        st = compute_stats(buckets[key])
        st['hour'] = key
        result.append(st)
    return result


def compute_histogram(rows: List[Dict], n_buckets: int = 40) -> List[Dict]:
    vals = [r['ms'] for r in rows if r['ms'] is not None]
    if not vals:
        return []
    mn, mx = min(vals), max(vals)
    if mx == mn:
        return [{'x': round(mn, 1), 'y': len(vals)}]
    width = (mx - mn) / n_buckets
    counts = [0] * n_buckets
    for v in vals:
        b = min(int((v - mn) / width), n_buckets - 1)
        counts[b] += 1
    return [{'x': round(mn + i * width, 1), 'y': counts[i]}
            for i in range(n_buckets) if counts[i] > 0]


def detect_outages(rows: List[Dict], min_count: int = 2) -> List[Dict]:
    outages = []
    start = None
    count = 0
    last_ts = None
    for row in rows:
        if row['ms'] is None:
            if start is None:
                start = row['ts']
                count = 1
            else:
                count += 1
            last_ts = row['ts']
        else:
            if start is not None and count >= min_count:
                dur = (last_ts - start).total_seconds() + 1
                outages.append({
                    'start': start.strftime('%Y-%m-%d %H:%M:%S'),
                    'end': last_ts.strftime('%Y-%m-%d %H:%M:%S'),
                    'duration_s': round(dur, 1),
                    'count': count,
                })
            start = None
            count = 0
            last_ts = None
    if start is not None and count >= min_count:
        dur = (last_ts - start).total_seconds() + 1
        outages.append({
            'start': start.strftime('%Y-%m-%d %H:%M:%S'),
            'end': last_ts.strftime('%Y-%m-%d %H:%M:%S'),
            'duration_s': round(dur, 1),
            'count': count,
        })
    return outages


def detect_high_ping_periods(rows: List[Dict], threshold_ms: float,
                              min_duration_s: float = 10.0) -> List[Dict]:
    periods = []
    start = None
    chunk: List[float] = []
    last_ts = None
    for row in rows:
        if row['ms'] is not None and row['ms'] > threshold_ms:
            if start is None:
                start = row['ts']
                chunk = []
            chunk.append(row['ms'])
            last_ts = row['ts']
        else:
            if start is not None and chunk:
                dur = (last_ts - start).total_seconds()
                if dur >= min_duration_s:
                    periods.append({
                        'start': start.strftime('%Y-%m-%d %H:%M:%S'),
                        'end': last_ts.strftime('%Y-%m-%d %H:%M:%S'),
                        'duration_s': round(dur, 1),
                        'avg_ms': round(sum(chunk) / len(chunk), 1),
                        'max_ms': round(max(chunk), 1),
                        'count': len(chunk),
                    })
            start = None
            chunk = []
            last_ts = None
    if start is not None and chunk:
        dur = (last_ts - start).total_seconds()
        if dur >= min_duration_s:
            periods.append({
                'start': start.strftime('%Y-%m-%d %H:%M:%S'),
                'end': last_ts.strftime('%Y-%m-%d %H:%M:%S'),
                'duration_s': round(dur, 1),
                'avg_ms': round(sum(chunk) / len(chunk), 1),
                'max_ms': round(max(chunk), 1),
                'count': len(chunk),
            })
    return periods


def downsample(rows: List[Dict], max_points: int) -> List[Dict]:
    if len(rows) <= max_points:
        return [{'x': r['ts'].strftime('%Y-%m-%dT%H:%M:%S'),
                 'y': round(r['ms'], 3) if r['ms'] is not None else None}
                for r in rows]
    factor = len(rows) / max_points
    result = []
    for i in range(max_points):
        si = int(i * factor)
        ei = int((i + 1) * factor)
        chunk = rows[si:ei]
        if not chunk:
            continue
        vals = [r['ms'] for r in chunk if r['ms'] is not None]
        ts = chunk[len(chunk) // 2]['ts']
        ms = round(sum(vals) / len(vals), 3) if vals else None
        result.append({'x': ts.strftime('%Y-%m-%dT%H:%M:%S'), 'y': ms})
    return result


def fmt_dur(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    elif s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    else:
        h, rem = divmod(s, 3600)
        return f"{h}h {rem // 60:02d}m {rem % 60:02d}s"


# ── HTML generation ───────────────────────────────────────────────────────────

def generate_report(rows: List[Dict], threshold: float, output_path: str) -> str:
    if not rows:
        sys.exit("No data found in CSV files.")

    hosts = sorted(set(r['host'] for r in rows))
    modes = sorted(set(r['mode'] for r in rows))
    start_dt = rows[0]['ts']
    end_dt = rows[-1]['ts']
    duration_s = (end_dt - start_dt).total_seconds()
    overall = compute_stats(rows)
    hourly = compute_hourly(rows)
    histogram = compute_histogram(rows)
    outages = detect_outages(rows)
    high_ping = detect_high_ping_periods(rows, threshold)
    high_ping_sorted = sorted(high_ping, key=lambda x: x['avg_ms'], reverse=True)

    # Timeline data: full resolution up to 20000 pts
    timeline_data = downsample(rows, 20000)
    # Overview mini chart: 800 pts
    overview_data = downsample(rows, 800)

    total_outage_s = sum(o['duration_s'] for o in outages)
    total_high_s = sum(p['duration_s'] for p in high_ping)

    # Per-host stats
    per_host = {}
    if len(hosts) > 1:
        for h in hosts:
            host_rows = [r for r in rows if r['host'] == h]
            per_host[h] = compute_stats(host_rows)

    # Per-mode stats (only meaningful when a session mixed measure methods)
    per_mode = {}
    if len(modes) > 1:
        for m in modes:
            per_mode[m] = compute_stats([r for r in rows if r['mode'] == m])

    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    data_json = json.dumps({
        'threshold': threshold,
        'hosts': hosts,
        'modes': modes,
        'per_mode': per_mode,
        'start': start_dt.strftime('%Y-%m-%d %H:%M:%S'),
        'end': end_dt.strftime('%Y-%m-%d %H:%M:%S'),
        'duration_s': round(duration_s, 1),
        'overall': overall,
        'hourly': hourly,
        'histogram': histogram,
        'outages': outages,
        'high_ping': high_ping_sorted,
        'timeline': timeline_data,
        'overview_mini': overview_data,
        'total_outage_s': round(total_outage_s, 1),
        'total_high_s': round(total_high_s, 1),
        'per_host': per_host,
        'generated_at': generated_at,
    }, separators=(',', ':'))

    def stat_val(v, unit='ms', na='─'):
        if v is None:
            return na
        return f"{v:.1f}{unit}"

    loss_class = 'red' if overall['loss'] > 5 else ('yellow' if overall['loss'] > 0 else 'green')

    host_str = ', '.join(hosts)
    mode_str = ', '.join(m.upper() for m in modes)
    duration_str = fmt_dur(duration_s)

    # Per-method comparison table (only when a session mixed measure methods)
    per_mode_section = ""
    if per_mode:
        per_mode_rows = "".join(
            f"""<tr>
        <td>{m.upper()}</td>
        <td class="num">{per_mode[m]['total']:,}</td>
        <td class="num">{stat_val(per_mode[m]['avg'])}</td>
        <td class="num green">{stat_val(per_mode[m]['min'])}</td>
        <td class="num red">{stat_val(per_mode[m]['max'])}</td>
        <td class="num">{stat_val(per_mode[m]['p95'])}</td>
        <td class="num {'red' if per_mode[m]['loss'] > 5 else ('' if per_mode[m]['loss'] == 0 else 'yellow')}">{per_mode[m]['loss']:.2f}%</td>
      </tr>"""
            for m in modes
        )
        per_mode_section = f"""
  <div class="section-header">By Measure Method</div>
  <div class="table-wrap"><table class="data-table">
    <thead><tr><th>Method</th><th>Pings</th><th>Avg</th><th>Min</th><th>Max</th><th>P95</th><th>Loss</th></tr></thead>
    <tbody>{per_mode_rows}</tbody>
  </table></div>
"""
    outage_str = f"{len(outages)} outage{'s' if len(outages) != 1 else ''}"
    if outages:
        outage_str += f" ({fmt_dur(total_outage_s)} total)"

    head_assets = _head_assets()

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PingTester Report — {host_str}</title>
{head_assets}
<style>
:root {{
  --bg:       #0a0a0f;
  --bg2:      #10101a;
  --bg3:      #14141f;
  --cyan:     #00cccc;
  --cyan-dim: rgba(0,204,204,0.15);
  --cyan-faint: rgba(0,204,204,0.06);
  --blue:     #4488ff;
  --blue-dim: rgba(68,136,255,0.18);
  --red:      #ff4444;
  --red-dim:  rgba(255,68,68,0.18);
  --yellow:   #ffcc00;
  --green:    #44cc44;
  --white:    #aabbcc;
  --dim:      #445566;
  --dimmer:   #2a3344;
  --font:     'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
}}

*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

html, body {{
  background: var(--bg);
  color: var(--white);
  font-family: var(--font);
  font-size: 13px;
  line-height: 1.5;
  min-height: 100vh;
}}

/* scanline overlay */
body::after {{
  content: '';
  position: fixed;
  inset: 0;
  background: repeating-linear-gradient(
    0deg,
    rgba(0,0,0,0.04) 0px, rgba(0,0,0,0.04) 1px,
    transparent 1px, transparent 2px
  );
  pointer-events: none;
  z-index: 9999;
}}

/* ── header ── */
.report-header {{
  border-bottom: 1px solid var(--cyan-dim);
  padding: 16px 24px 12px;
  background: var(--bg2);
  position: sticky;
  top: 0;
  z-index: 100;
}}
.report-title {{
  color: var(--cyan);
  font-size: 17px;
  font-weight: 700;
  letter-spacing: 0.05em;
  display: flex;
  align-items: center;
  gap: 10px;
}}
.report-title .diamond {{ color: var(--cyan); }}
.report-meta {{
  display: flex;
  gap: 24px;
  margin-top: 4px;
  color: var(--dim);
  font-size: 11px;
  flex-wrap: wrap;
}}
.report-meta span {{ white-space: nowrap; }}
.report-meta .accent {{ color: var(--cyan); }}

/* ── tabs ── */
.tabs {{
  display: flex;
  gap: 0;
  border-bottom: 1px solid var(--cyan-dim);
  background: var(--bg2);
  padding: 0 24px;
}}
.tab-btn {{
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--dim);
  cursor: pointer;
  font-family: var(--font);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.12em;
  padding: 10px 18px 8px;
  text-transform: uppercase;
  transition: color 0.15s, border-color 0.15s;
  margin-bottom: -1px;
}}
.tab-btn:hover {{ color: var(--cyan); }}
.tab-btn.active {{
  color: var(--cyan);
  border-bottom-color: var(--cyan);
}}

/* ── layout ── */
.tab-content {{ display: none; padding: 24px; max-width: 1600px; margin: 0 auto; }}
.tab-content.active {{ display: block; }}

/* ── stat cards ── */
.stat-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 1px;
  border: 1px solid var(--cyan-dim);
  background: var(--cyan-dim);
  margin-bottom: 24px;
}}
.stat-card {{
  background: var(--bg2);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}}
.stat-card.wide {{ grid-column: span 2; }}
.stat-label {{
  color: var(--dim);
  font-size: 10px;
  letter-spacing: 0.15em;
  text-transform: uppercase;
}}
.stat-value {{
  font-size: 20px;
  font-weight: 700;
  color: var(--yellow);
  letter-spacing: -0.02em;
}}
.stat-value.cyan {{ color: var(--cyan); }}
.stat-value.green {{ color: var(--green); }}
.stat-value.red {{ color: var(--red); }}
.stat-value.white {{ color: var(--white); font-size: 14px; font-weight: 500; }}
.stat-unit {{
  font-size: 11px;
  color: var(--dim);
  font-weight: 400;
  margin-left: 2px;
}}

/* ── section headers ── */
.section-header {{
  color: var(--cyan);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}}
.section-header::before {{ content: '◈'; font-size: 10px; }}
.section-header::after {{
  content: '';
  flex: 1;
  height: 1px;
  background: var(--cyan-dim);
}}

/* ── chart boxes ── */
.chart-box {{
  background: var(--bg2);
  border: 1px solid var(--cyan-dim);
  padding: 16px;
  margin-bottom: 24px;
  position: relative;
}}
.chart-box .chart-title {{
  color: var(--dim);
  font-size: 10px;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  margin-bottom: 12px;
}}
.chart-box canvas {{ display: block; width: 100% !important; }}

.chart-row {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 24px;
}}
@media (max-width: 900px) {{
  .chart-row {{ grid-template-columns: 1fr; }}
}}

/* ── tables ── */
.data-table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  margin-bottom: 24px;
}}
.data-table th {{
  color: var(--dim);
  font-weight: 500;
  font-size: 10px;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  text-align: left;
  padding: 8px 12px;
  border-bottom: 1px solid var(--cyan-dim);
  background: var(--bg3);
}}
.data-table td {{
  padding: 7px 12px;
  border-bottom: 1px solid var(--dimmer);
  color: var(--white);
  vertical-align: middle;
}}
.data-table tr:hover td {{ background: var(--cyan-faint); }}
.data-table .num {{ color: var(--yellow); font-variant-numeric: tabular-nums; }}
.data-table .red {{ color: var(--red); }}
.data-table .green {{ color: var(--green); }}
.data-table .dim {{ color: var(--dim); }}
.data-table .ts {{ color: var(--cyan); font-size: 11px; }}

.table-wrap {{
  border: 1px solid var(--cyan-dim);
  background: var(--bg2);
  margin-bottom: 24px;
  max-height: 340px;
  overflow-y: auto;
}}
.table-wrap::-webkit-scrollbar {{ width: 6px; }}
.table-wrap::-webkit-scrollbar-track {{ background: var(--bg); }}
.table-wrap::-webkit-scrollbar-thumb {{ background: var(--cyan-dim); border-radius: 0; }}

/* ── summary row with badge ── */
.event-summary {{
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 16px;
  background: var(--bg2);
  border: 1px solid var(--cyan-dim);
  margin-bottom: 12px;
  font-size: 12px;
  flex-wrap: wrap;
}}
.badge {{
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  border: 1px solid;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.05em;
}}
.badge.red {{ border-color: var(--red); color: var(--red); background: var(--red-dim); }}
.badge.yellow {{ border-color: var(--yellow); color: var(--yellow); background: rgba(255,204,0,0.1); }}
.badge.green {{ border-color: var(--green); color: var(--green); background: rgba(68,204,68,0.1); }}
.badge.cyan {{ border-color: var(--cyan); color: var(--cyan); background: var(--cyan-dim); }}
.no-events {{
  color: var(--dim);
  font-size: 12px;
  padding: 16px;
  text-align: center;
  background: var(--bg2);
  border: 1px solid var(--dimmer);
  margin-bottom: 24px;
}}

/* ── timeline tab ── */
.timeline-controls {{
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}}
.ctrl-btn {{
  background: var(--bg3);
  border: 1px solid var(--cyan-dim);
  color: var(--cyan);
  cursor: pointer;
  font-family: var(--font);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.1em;
  padding: 5px 12px;
  text-transform: uppercase;
  transition: background 0.1s, border-color 0.1s;
}}
.ctrl-btn:hover {{ background: var(--cyan-dim); border-color: var(--cyan); }}
.ctrl-hint {{
  color: var(--dim);
  font-size: 11px;
}}

.range-stats {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 1px;
  background: var(--cyan-dim);
  border: 1px solid var(--cyan-dim);
  margin-top: 20px;
  margin-bottom: 24px;
}}
.range-stat {{
  background: var(--bg2);
  padding: 12px 14px;
}}
.range-stat-label {{
  color: var(--dim);
  font-size: 10px;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  margin-bottom: 3px;
}}
.range-stat-value {{
  color: var(--yellow);
  font-size: 16px;
  font-weight: 700;
}}
.range-stat-value.cyan {{ color: var(--cyan); }}
.range-stat-value.green {{ color: var(--green); }}
.range-stat-value.red {{ color: var(--red); }}

.range-header {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  flex-wrap: wrap;
  gap: 8px;
}}
.range-label {{
  color: var(--dim);
  font-size: 11px;
}}
#range-time {{
  color: var(--cyan);
  font-size: 11px;
}}

/* ── outage mini chart inside timeline ── */
#timeline-outage-list {{
  margin-top: 16px;
}}

/* scrollbar global */
::-webkit-scrollbar {{ width: 6px; height: 6px; }}
::-webkit-scrollbar-track {{ background: var(--bg); }}
::-webkit-scrollbar-thumb {{ background: var(--dimmer); }}
::-webkit-scrollbar-thumb:hover {{ background: var(--dim); }}
</style>
</head>
<body>

<header class="report-header">
  <div class="report-title">
    <span class="diamond">◈</span>
    PINGTESTER REPORT
  </div>
  <div class="report-meta">
    <span>host: <span class="accent">{host_str}</span></span>
    <span>method: <span class="accent">{mode_str}</span></span>
    <span>period: <span class="accent">{start_dt.strftime('%Y-%m-%d %H:%M')}</span> → <span class="accent">{end_dt.strftime('%Y-%m-%d %H:%M')}</span></span>
    <span>duration: <span class="accent">{duration_str}</span></span>
    <span>threshold: <span class="accent">{threshold:.0f}ms</span></span>
    <span style="margin-left:auto">generated: {generated_at}</span>
  </div>
</header>

<nav class="tabs">
  <button class="tab-btn active" onclick="switchTab('overview', this)">[ Overview ]</button>
  <button class="tab-btn" onclick="switchTab('timeline', this)">[ Timeline ]</button>
</nav>

<!-- ══════════════════════════════════════════════════════ OVERVIEW TAB -->
<div id="tab-overview" class="tab-content active">

  <div class="stat-grid">
    <div class="stat-card wide">
      <div class="stat-label">Host(s)</div>
      <div class="stat-value white">{host_str}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Method</div>
      <div class="stat-value white">{mode_str}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Duration</div>
      <div class="stat-value cyan">{duration_str}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Total Pings</div>
      <div class="stat-value">{overall['total']:,}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Avg Latency</div>
      <div class="stat-value">{stat_val(overall['avg'])}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Min Latency</div>
      <div class="stat-value green">{stat_val(overall['min'])}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Max Latency</div>
      <div class="stat-value red">{stat_val(overall['max'])}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">P95 Latency</div>
      <div class="stat-value">{stat_val(overall['p95'])}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Packet Loss</div>
      <div class="stat-value {loss_class}">{overall['loss']:.2f}<span class="stat-unit">%</span></div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Outages</div>
      <div class="stat-value {'red' if outages else 'green'}">{len(outages)}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Outage Time</div>
      <div class="stat-value {'red' if outages else 'dim'}">{fmt_dur(total_outage_s) if total_outage_s else '─'}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">High Ping Events</div>
      <div class="stat-value {'yellow' if high_ping else 'green'}">{len(high_ping)}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">High Ping Time</div>
      <div class="stat-value {'yellow' if high_ping else 'dim'}">{fmt_dur(total_high_s) if total_high_s else '─'}</div>
    </div>
  </div>
{per_mode_section}
  <!-- session overview mini chart -->
  <div class="section-header">Session Overview</div>
  <div class="chart-box">
    <div class="chart-title">Latency over entire session — drag on Timeline tab to explore</div>
    <div style="height:140px"><canvas id="overviewChart"></canvas></div>
  </div>

  <div class="chart-row">
    <div>
      <div class="section-header">Hourly Summary</div>
      <div class="chart-box">
        <div class="chart-title">Avg latency &amp; packet loss per hour</div>
        <div style="height:220px"><canvas id="hourlyChart"></canvas></div>
      </div>
    </div>
    <div>
      <div class="section-header">Latency Distribution</div>
      <div class="chart-box">
        <div class="chart-title">Frequency of latency values (ms)</div>
        <div style="height:220px"><canvas id="histChart"></canvas></div>
      </div>
    </div>
  </div>

  <!-- Outages -->
  <div class="section-header">Outages</div>
  {"<div class='no-events'>◉ No outages detected during this session.</div>" if not outages else f"""
  <div class="event-summary">
    <span class="badge red">✕ {len(outages)} outage{'s' if len(outages)!=1 else ''}</span>
    <span>Total downtime: <strong style="color:var(--red)">{fmt_dur(total_outage_s)}</strong></span>
    <span style="color:var(--dim)">({total_outage_s/max(duration_s,1)*100:.2f}% of session)</span>
  </div>
  <div class="table-wrap"><table class="data-table">
    <thead><tr>
      <th>#</th><th>Start</th><th>End</th><th>Duration</th><th>Timeouts</th>
    </tr></thead>
    <tbody id="outage-tbody"></tbody>
  </table></div>"""}

  <!-- High Ping Periods -->
  <div class="section-header">High Ping Periods (&gt;{threshold:.0f}ms for 10s+)</div>
  {"<div class='no-events'>◉ No sustained high-ping periods detected.</div>" if not high_ping else f"""
  <div class="event-summary">
    <span class="badge yellow">▲ {len(high_ping)} period{'s' if len(high_ping)!=1 else ''}</span>
    <span>Total time above threshold: <strong style="color:var(--yellow)">{fmt_dur(total_high_s)}</strong></span>
  </div>
  <div class="table-wrap"><table class="data-table">
    <thead><tr>
      <th>#</th><th>Start</th><th>End</th><th>Duration</th><th>Avg</th><th>Max</th><th>Pings</th>
    </tr></thead>
    <tbody id="highping-tbody"></tbody>
  </table></div>"""}

</div><!-- /tab-overview -->

<!-- ══════════════════════════════════════════════════════ TIMELINE TAB -->
<div id="tab-timeline" class="tab-content">

  <div class="timeline-controls">
    <button class="ctrl-btn" onclick="resetZoom()">[ Reset Zoom ]</button>
    <span class="ctrl-hint">Drag to zoom · Double-click to reset · Scroll to pan</span>
  </div>

  <div class="chart-box" style="margin-bottom:0">
    <div class="chart-title">Full session timeline — zoom &amp; pan to explore</div>
    <div style="height:320px"><canvas id="timelineChart"></canvas></div>
  </div>

  <div class="range-header" style="margin-top:16px">
    <div class="section-header" style="margin-bottom:0">Selection Stats</div>
    <div class="range-label">Range: <span id="range-time">full session</span></div>
  </div>

  <div class="range-stats" id="range-stats">
    <div class="range-stat"><div class="range-stat-label">Pings</div><div class="range-stat-value cyan" id="rs-total">─</div></div>
    <div class="range-stat"><div class="range-stat-label">Avg</div><div class="range-stat-value" id="rs-avg">─</div></div>
    <div class="range-stat"><div class="range-stat-label">Min</div><div class="range-stat-value green" id="rs-min">─</div></div>
    <div class="range-stat"><div class="range-stat-label">Max</div><div class="range-stat-value red" id="rs-max">─</div></div>
    <div class="range-stat"><div class="range-stat-label">P95</div><div class="range-stat-value" id="rs-p95">─</div></div>
    <div class="range-stat"><div class="range-stat-label">Loss</div><div class="range-stat-value" id="rs-loss">─</div></div>
    <div class="range-stat"><div class="range-stat-label">Timeouts</div><div class="range-stat-value red" id="rs-timeouts">─</div></div>
    <div class="range-stat"><div class="range-stat-label">Outages</div><div class="range-stat-value red" id="rs-outages">─</div></div>
  </div>

  <div id="timeline-outage-list"></div>

</div><!-- /tab-timeline -->

<script>
const D = {data_json};
const THRESHOLD = D.threshold;
const C = {{
  bg:      '#0a0a0f',
  bg2:     '#10101a',
  cyan:    '#00cccc',
  cyanDim: 'rgba(0,204,204,0.12)',
  blue:    '#4488ff',
  blueFill:'rgba(68,136,255,0.08)',
  red:     '#ff4444',
  redFill: 'rgba(255,68,68,0.08)',
  yellow:  '#ffcc00',
  green:   '#44cc44',
  white:   '#aabbcc',
  dim:     '#445566',
  dimmer:  '#2a3344',
  grid:    'rgba(0,204,204,0.07)',
}};
const FONT = "'JetBrains Mono', monospace";

Chart.defaults.color = C.dim;
Chart.defaults.font.family = FONT;
Chart.defaults.font.size = 11;

const AXIS_X_TIME = {{
  type: 'time',
  time: {{ tooltipFormat: 'YYYY-MM-DD HH:mm:ss', displayFormats: {{
    millisecond: 'HH:mm:ss',
    second: 'HH:mm:ss',
    minute: 'HH:mm',
    hour: 'MM-DD HH:mm',
    day: 'MM-DD',
  }} }},
  grid: {{ color: C.grid }},
  ticks: {{ color: C.dim, maxTicksLimit: 10 }},
  border: {{ color: C.dimmer }},
}};

function makeYAxis(unit) {{
  return {{
    min: 0,
    grid: {{ color: C.grid }},
    ticks: {{ color: C.dim, callback: v => unit ? v + unit : v }},
    border: {{ color: C.dimmer }},
  }};
}}

function tooltipDefaults() {{
  return {{
    backgroundColor: '#0d0d14',
    borderColor: C.cyan,
    borderWidth: 1,
    titleColor: C.cyan,
    bodyColor: C.white,
    padding: 10,
    cornerRadius: 0,
    titleFont: {{ family: FONT, size: 11 }},
    bodyFont: {{ family: FONT, size: 11 }},
  }};
}}

// ── overview mini chart ────────────────────────────────────────────────────
(function() {{
  const data = D.overview_mini;
  const ctx = document.getElementById('overviewChart').getContext('2d');
  new Chart(ctx, {{
    type: 'line',
    data: {{
      datasets: [
        {{
          data: data.filter(p => p.y !== null),
          borderColor: C.blue,
          borderWidth: 1,
          pointRadius: 0,
          fill: {{ target: 'origin', above: C.blueFill }},
          tension: 0,
          segment: {{
            borderColor: ctx2 => (ctx2.p1.parsed.y > THRESHOLD) ? C.red : C.blue,
          }},
          label: 'Latency',
        }},
        {{
          data: data.filter(p => p.y === null).map(p => ({{x: p.x, y: 0}})),
          type: 'scatter',
          pointRadius: 2,
          pointStyle: 'cross',
          pointBackgroundColor: C.red,
          borderColor: C.red,
          label: 'Timeout',
        }},
        {{
          data: [
            {{x: data[0]?.x, y: THRESHOLD}},
            {{x: data[data.length-1]?.x, y: THRESHOLD}},
          ],
          borderColor: 'rgba(255,204,0,0.4)',
          borderWidth: 1,
          borderDash: [4,4],
          pointRadius: 0,
          fill: false,
          label: 'Threshold',
        }},
      ],
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: {{ mode: 'index', intersect: false }},
      scales: {{
        x: {{ ...AXIS_X_TIME }},
        y: {{ ...makeYAxis('ms'), suggestedMax: THRESHOLD * 1.5 }},
      }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          ...tooltipDefaults(),
          callbacks: {{
            label: ctx => {{
              if (ctx.datasetIndex === 1) return 'TIMEOUT';
              if (ctx.datasetIndex === 2) return null;
              return ctx.raw.y !== undefined && ctx.raw.y !== null ? ctx.raw.y.toFixed(1) + ' ms' : null;
            }},
          }},
          filter: item => item.datasetIndex < 2,
        }},
      }},
    }},
  }});
}})();

// ── hourly chart ───────────────────────────────────────────────────────────
(function() {{
  const h = D.hourly;
  const ctx = document.getElementById('hourlyChart').getContext('2d');
  new Chart(ctx, {{
    type: 'bar',
    data: {{
      labels: h.map(x => x.hour),
      datasets: [
        {{
          label: 'Avg (ms)',
          data: h.map(x => x.avg),
          backgroundColor: h.map(x => (x.avg > THRESHOLD) ? 'rgba(255,68,68,0.6)' : 'rgba(68,136,255,0.6)'),
          borderColor:     h.map(x => (x.avg > THRESHOLD) ? C.red : C.blue),
          borderWidth: 1,
          borderRadius: 0,
          yAxisID: 'y',
          order: 2,
        }},
        {{
          label: 'Loss %',
          data: h.map(x => x.loss),
          type: 'line',
          borderColor: C.red,
          backgroundColor: 'transparent',
          borderWidth: 1.5,
          pointRadius: 2,
          pointBackgroundColor: C.red,
          yAxisID: 'y2',
          tension: 0.2,
          order: 1,
        }},
      ],
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {{
        x: {{
          type: 'time',
          time: {{ tooltipFormat: 'YYYY-MM-DD HH:mm', displayFormats: {{ hour: 'MM-DD HH:mm' }} }},
          grid: {{ color: C.grid }},
          ticks: {{ color: C.dim, maxTicksLimit: 12 }},
          border: {{ color: C.dimmer }},
        }},
        y: {{ ...makeYAxis('ms'), position: 'left' }},
        y2: {{
          position: 'right',
          min: 0, max: 100,
          grid: {{ drawOnChartArea: false }},
          ticks: {{ color: C.red, callback: v => v + '%' }},
          border: {{ color: C.dimmer }},
        }},
      }},
      plugins: {{
        legend: {{
          labels: {{ color: C.dim, boxWidth: 12, font: {{ family: FONT, size: 10 }} }},
        }},
        tooltip: {{
          ...tooltipDefaults(),
          callbacks: {{
            label: ctx => {{
              const ds = ctx.dataset.label;
              const v = ctx.raw;
              if (v === null || v === undefined) return null;
              return ds === 'Loss %' ? `loss: ${{v.toFixed(2)}}%` : `avg: ${{v.toFixed(1)}}ms`;
            }},
          }},
        }},
      }},
    }},
  }});
}})();

// ── histogram chart ────────────────────────────────────────────────────────
(function() {{
  const hist = D.histogram;
  const ctx = document.getElementById('histChart').getContext('2d');
  new Chart(ctx, {{
    type: 'bar',
    data: {{
      labels: hist.map(b => b.x.toFixed(1)),
      datasets: [{{
        label: 'Count',
        data: hist.map(b => b.y),
        backgroundColor: hist.map(b => (b.x > THRESHOLD) ? 'rgba(255,68,68,0.6)' : 'rgba(68,136,255,0.6)'),
        borderColor:     hist.map(b => (b.x > THRESHOLD) ? C.red : C.blue),
        borderWidth: 1,
        borderRadius: 0,
        categoryPercentage: 1.0,
        barPercentage: 0.95,
      }}],
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {{
        x: {{
          grid: {{ color: C.grid }},
          ticks: {{ color: C.dim, maxTicksLimit: 10, callback: (v, i) => hist[i] ? hist[i].x.toFixed(0)+'ms' : '' }},
          border: {{ color: C.dimmer }},
        }},
        y: {{ ...makeYAxis(''), title: {{ display: true, text: 'count', color: C.dim }} }},
      }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          ...tooltipDefaults(),
          callbacks: {{
            title: items => items[0].label + ' ms',
            label: ctx => `count: ${{ctx.raw}}`,
          }},
        }},
      }},
    }},
  }});
}})();

// ── populate outage table ─────────────────────────────────────────────────
(function() {{
  const tbody = document.getElementById('outage-tbody');
  if (!tbody) return;
  D.outages.slice(0, 100).forEach((o, i) => {{
    const dur = fmtDur(o.duration_s);
    tbody.insertAdjacentHTML('beforeend', `
      <tr>
        <td class="dim">${{i+1}}</td>
        <td class="ts">${{o.start}}</td>
        <td class="ts">${{o.end}}</td>
        <td class="num red">${{dur}}</td>
        <td class="num">${{o.count}}</td>
      </tr>`);
  }});
  if (D.outages.length > 100) {{
    tbody.insertAdjacentHTML('beforeend',
      `<tr><td colspan="5" class="dim" style="text-align:center;padding:12px">
        ... ${{D.outages.length - 100}} more outages not shown
      </td></tr>`);
  }}
}})();

// ── populate high ping table ───────────────────────────────────────────────
(function() {{
  const tbody = document.getElementById('highping-tbody');
  if (!tbody) return;
  D.high_ping.slice(0, 100).forEach((p, i) => {{
    tbody.insertAdjacentHTML('beforeend', `
      <tr>
        <td class="dim">${{i+1}}</td>
        <td class="ts">${{p.start}}</td>
        <td class="ts">${{p.end}}</td>
        <td class="num">${{fmtDur(p.duration_s)}}</td>
        <td class="num yellow">${{p.avg_ms.toFixed(1)}}ms</td>
        <td class="num red">${{p.max_ms.toFixed(1)}}ms</td>
        <td class="num dim">${{p.count}}</td>
      </tr>`);
  }});
  if (D.high_ping.length > 100) {{
    tbody.insertAdjacentHTML('beforeend',
      `<tr><td colspan="7" class="dim" style="text-align:center;padding:12px">
        ... ${{D.high_ping.length - 100}} more periods not shown
      </td></tr>`);
  }}
}})();

// ── timeline chart ─────────────────────────────────────────────────────────
let timelineChart;
(function() {{
  const data = D.timeline;
  const ctx = document.getElementById('timelineChart').getContext('2d');
  const minT = data[0]?.x;
  const maxT = data[data.length-1]?.x;

  timelineChart = new Chart(ctx, {{
    type: 'line',
    data: {{
      datasets: [
        {{
          label: 'Latency',
          data: data,
          borderColor: C.blue,
          borderWidth: 1.5,
          pointRadius: 0,
          pointHoverRadius: 3,
          fill: {{ target: 'origin', above: C.blueFill }},
          tension: 0,
          spanGaps: false,
          segment: {{
            borderColor: ctx2 => {{
              if (ctx2.p1.parsed.y === null) return C.dimmer;
              return ctx2.p1.parsed.y > THRESHOLD ? C.red : C.blue;
            }},
          }},
        }},
        {{
          label: 'Timeouts',
          data: data.filter(p => p.y === null).map(p => ({{x: p.x, y: 0}})),
          type: 'scatter',
          pointRadius: 2,
          pointStyle: 'crossRot',
          pointBackgroundColor: C.red,
          borderColor: C.red,
          showLine: false,
        }},
        {{
          label: 'Threshold',
          data: minT && maxT ? [{{x: minT, y: THRESHOLD}}, {{x: maxT, y: THRESHOLD}}] : [],
          borderColor: 'rgba(255,204,0,0.5)',
          borderWidth: 1,
          borderDash: [6,3],
          pointRadius: 0,
          fill: false,
          tension: 0,
        }},
      ],
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: {{ mode: 'index', intersect: false }},
      scales: {{
        x: {{ ...AXIS_X_TIME }},
        y: {{
          ...makeYAxis('ms'),
          suggestedMax: Math.max(THRESHOLD * 1.5, (D.overall.p95 || THRESHOLD) * 1.2),
        }},
      }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          ...tooltipDefaults(),
          callbacks: {{
            label: ctx => {{
              if (ctx.datasetIndex === 1) return '● TIMEOUT';
              if (ctx.datasetIndex === 2) return null;
              const v = ctx.raw.y;
              if (v === null || v === undefined) return null;
              const flag = v > THRESHOLD ? ' ▲ HIGH' : '';
              return `${{v.toFixed(1)}} ms${{flag}}`;
            }},
            labelColor: ctx => {{
              if (ctx.datasetIndex === 1) return {{borderColor: C.red, backgroundColor: C.red}};
              const v = ctx.raw?.y;
              const col = v > THRESHOLD ? C.red : C.blue;
              return {{borderColor: col, backgroundColor: col}};
            }},
          }},
          filter: item => item.datasetIndex !== 2,
        }},
        zoom: {{
          zoom: {{
            drag: {{
              enabled: true,
              backgroundColor: 'rgba(0,204,204,0.08)',
              borderColor: C.cyan,
              borderWidth: 1,
            }},
            mode: 'x',
            onZoomComplete: updateRangeStats,
          }},
          pan: {{
            enabled: true,
            mode: 'x',
            onPanComplete: updateRangeStats,
          }},
          limits: {{ x: {{ min: 'original', max: 'original' }} }},
        }},
      }},
    }},
  }});

  updateRangeStats();
}})();

function resetZoom() {{
  timelineChart.resetZoom();
  updateRangeStats();
}}

function updateRangeStats() {{
  if (!timelineChart) return;
  const scale = timelineChart.scales.x;
  const minMs = scale.min;
  const maxMs = scale.max;

  const visible = D.timeline.filter(p => {{
    const t = new Date(p.x).getTime();
    return t >= minMs && t <= maxMs;
  }});

  const vals = visible.filter(p => p.y !== null).map(p => p.y);
  const timeouts = visible.filter(p => p.y === null).length;
  const total = visible.length;
  const lost = timeouts;
  const lossP = total > 0 ? (lost / total * 100) : 0;

  const sorted = [...vals].sort((a,b) => a-b);
  const avg = vals.length ? vals.reduce((a,b)=>a+b,0)/vals.length : null;
  const p95 = sorted.length ? sorted[Math.min(Math.floor(sorted.length*0.95), sorted.length-1)] : null;

  set('rs-total', total.toLocaleString(), 'cyan');
  set('rs-avg',  avg !== null ? avg.toFixed(1)+' ms' : '─', avg > THRESHOLD ? 'red' : '');
  set('rs-min',  sorted.length ? sorted[0].toFixed(1)+' ms' : '─', 'green');
  set('rs-max',  sorted.length ? sorted[sorted.length-1].toFixed(1)+' ms' : '─', 'red');
  set('rs-p95',  p95 !== null ? p95.toFixed(1)+' ms' : '─', p95 > THRESHOLD ? 'red' : '');
  set('rs-loss', lossP.toFixed(2)+'%', lossP > 5 ? 'red' : lossP > 0 ? '' : 'green');
  set('rs-timeouts', timeouts.toLocaleString(), timeouts > 0 ? 'red' : 'green');

  // Outages within range
  const startStr = moment(minMs).format('YYYY-MM-DD HH:mm:ss');
  const endStr   = moment(maxMs).format('YYYY-MM-DD HH:mm:ss');
  const rangeOutages = D.outages.filter(o => o.start >= startStr && o.end <= endStr);
  set('rs-outages', rangeOutages.length.toString(), rangeOutages.length > 0 ? 'red' : 'green');

  // Update range label
  const full = (Math.abs(scale.min - new Date(D.timeline[0]?.x).getTime()) < 1000 &&
                Math.abs(scale.max - new Date(D.timeline[D.timeline.length-1]?.x).getTime()) < 1000);
  document.getElementById('range-time').textContent = full
    ? 'full session'
    : `${{moment(minMs).format('MM-DD HH:mm:ss')}} → ${{moment(maxMs).format('MM-DD HH:mm:ss')}}`;

  // Outage list inside range
  const ol = document.getElementById('timeline-outage-list');
  if (rangeOutages.length > 0) {{
    let rows = rangeOutages.slice(0, 20).map((o, i) =>
      `<tr>
        <td class="dim">${{i+1}}</td>
        <td class="ts">${{o.start}}</td>
        <td class="ts">${{o.end}}</td>
        <td class="num red">${{fmtDur(o.duration_s)}}</td>
        <td class="num">${{o.count}} packets lost</td>
      </tr>`).join('');
    ol.innerHTML = `
      <div class="section-header" style="margin-top:8px">Outages in selected range</div>
      <div class="table-wrap"><table class="data-table">
        <thead><tr><th>#</th><th>Start</th><th>End</th><th>Duration</th><th>Lost</th></tr></thead>
        <tbody>${{rows}}</tbody>
      </table></div>`;
  }} else {{
    ol.innerHTML = '';
  }}
}}

function set(id, val, cls) {{
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = val;
  el.className = 'range-stat-value' + (cls ? ' '+cls : '');
}}

// ── tab switching ──────────────────────────────────────────────────────────
function switchTab(name, btn) {{
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  btn.classList.add('active');
  if (name === 'timeline') {{
    setTimeout(() => {{ timelineChart && timelineChart.resize(); }}, 50);
  }}
}}

// ── utility ────────────────────────────────────────────────────────────────
function fmtDur(s) {{
  s = Math.floor(s);
  if (s < 60) return s+'s';
  if (s < 3600) return Math.floor(s/60)+'m '+(s%60).toString().padStart(2,'0')+'s';
  const h = Math.floor(s/3600), rem = s%3600;
  return h+'h '+Math.floor(rem/60).toString().padStart(2,'0')+'m '+(rem%60).toString().padStart(2,'0')+'s';
}}
</script>
</body>
</html>'''

    return html


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="pingtester-report — HTML report from CSV logs")
    ap.add_argument('inputs', nargs='*',
                    help='CSV files or glob patterns (default: pingtester_*.csv in current dir)')
    ap.add_argument('-o', '--output', default='pingtester_report.html',
                    help='Output HTML file (default: pingtester_report.html)')
    ap.add_argument('--threshold', type=float, default=100.0,
                    help='High-ping threshold in ms (default: 100)')
    args = ap.parse_args()

    if args.inputs:
        paths: List[str] = []
        for pattern in args.inputs:
            matched = glob.glob(pattern)
            if matched:
                paths.extend(matched)
            elif os.path.exists(pattern):
                paths.append(pattern)
            else:
                print(f"Warning: no files matched '{pattern}'", file=sys.stderr)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
        paths = sorted(glob.glob(os.path.join(base, 'pingtester_*.csv')))

    if not paths:
        sys.exit("No CSV files found. Run pingtester with --log to generate them.")

    print(f"Loading {len(paths)} file(s)...", file=sys.stderr)
    rows = load_csvs(paths)
    if not rows:
        sys.exit("No valid data rows found in the CSV files.")

    print(f"Loaded {len(rows):,} ping records.", file=sys.stderr)
    html = generate_report(rows, args.threshold, args.output)

    out_path = os.path.abspath(args.output)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"Report written to: {out_path}", file=sys.stderr)
    print(f"Open with: xdg-open '{out_path}'", file=sys.stderr)


if __name__ == '__main__':
    main()
