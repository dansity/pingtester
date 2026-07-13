# pingtester

A nice looking terminal-based network latency monitor. It performs various tests on a host continuously and draws a live bar chart of response times, color-coded by a configurable warning threshold. It can also run a continuous traceroute, stacking every hop on the path into one bar so you can see *where* the latency comes from. Useful for watching connection quality over time without setting up anything heavy.

<img width="1899" height="533" alt="image" src="https://github.com/user-attachments/assets/6d3342a5-e8ba-4bc7-95f3-1a114bf35891" />
<img width="1901" height="528" alt="image" src="https://github.com/user-attachments/assets/0b2c76dd-6e28-4a31-b113-44ce321e8667" />

Easily generate self contained html report:
<img width="1570" height="893" alt="image" src="https://github.com/user-attachments/assets/d2f97a89-414b-42f4-a5ea-7df2ed0ac590" />

## Features

- Four probe methods, switchable at runtime — **ICMP**, **TCP** connect, **HTTP(S)**, and **traceroute**
- **Traceroute mode** stacks each hop as a colored block in one bar, so a slow hop stands out
- Live scrolling bar chart with sub-row precision using Unicode block characters
- Customizable colors, interpolated in OKLab so any path length gets a distinguishable ramp
- Adjustable interval, threshold, Y-scale (auto-fit in traceroute) and view window (1 min – 3 h)
- Stats bar: min, max, avg, p95, packet loss
- Optional hourly CSV logging and a self-contained HTML report
- Python standard library only (Linux and macOS)

# Vibecoded

This application is vibecoded and human edited for good looks and my own purpose. It is "harmless" in a sense that it has no telemetry or analytics and sends no data anywhere other than to the target host you are probing. While it is vibecoded I take privacy seriously. Here is exactly what talks to the network:
- The **pingtester** utility only contacts the target host you choose. In ICMP mode (the default) it runs the system's `ping` utility in a loop. In TCP mode it opens a connection to the target's port. In HTTP mode it fetches the first byte of a GET request (TLS certificate validation is disabled, since it measures latency rather than trust). It has no telemetry and downloads nothing else.
- The **report** utility itself makes no network calls — it only reads your CSV logs and writes a local HTML file. That file, when opened in a browser, loads the JetBrains Mono font from Google Fonts (`fonts.googleapis.com` / `fonts.gstatic.com`) and Chart.js plus its plugins (moment, adapter, hammer.js, zoom) from `cdn.jsdelivr.net`. No measurement data is sent to them. The URLs live at the top of `report.py` — swap them for self-hosted copies to make the report fully self-contained.

Software comes with absolutely no warranty.

## Requirements

### Linux / macOS

- Python 3.7 or newer
- The `ping` utility (present on all standard Linux/macOS installs)
- A terminal that supports UTF-8 and 256 colors (virtually any modern terminal emulator)

### Windows

- Python 3.7 or newer
- The `windows-curses` package:

```
pip install windows-curses
```

- Windows Terminal is strongly recommended. The classic `cmd.exe` console does not render the Unicode block and box-drawing characters correctly.

## Installation

No installation needed. Just clone or download the single file and run it.

```
git clone https://github.com/dansity/pingtester.git
cd pingtester
python3 pingtester.py
```

On Windows:

```
pip install windows-curses
python pingtester.py
```

## Usage

```
python3 pingtester.py [options]
```

### Options

| Option | Default | Description |
|---|---|---|
| `--host HOST` | 8.8.8.8 | Host to probe (accepts `host`, `host:port`, or a URL) |
| `--mode MODE` | icmp | Probe method: `icmp`, `tcp`, `http`, or `trace` |
| `--port PORT` | 443 | Port used in `tcp` mode |
| `--interval MS` | 1000 | Probe interval in milliseconds (minimum 100; `trace` is floored at 1000) |
| `--threshold MS` | 100 | Latency warning threshold in milliseconds (ignored in `trace` mode) |
| `--scale MS` | 200 | Y-axis full-scale value in milliseconds. Omit it in `trace` mode to let the scale auto-fit the path |
| `--log` | off | Enable CSV logging at startup |

### Examples

```
python3 pingtester.py
python3 pingtester.py --host 1.1.1.1 --interval 500
python3 pingtester.py --host 192.168.1.1 --threshold 10 --scale 50
python3 pingtester.py --host 8.8.8.8 --log

# TCP handshake latency to a web server on port 443
python3 pingtester.py --mode tcp --host example.com --port 443

# HTTP(S) time-to-first-byte
python3 pingtester.py --mode http --host https://example.com

# Continuous traceroute, Y-axis fitted to the path automatically
python3 pingtester.py --mode trace --host 1.1.1.1

# Traceroute with a fixed Y-axis and per-hop CSV logging
python3 pingtester.py --mode trace --host example.com --scale 100 --log
```
## Probe methods

`pingtester` can measure latency four different ways. Cycle between them at runtime with `m`, or set one at startup with `--mode`.

| Mode | Measures | Notes |
|---|---|---|
| `icmp` | ICMP echo round-trip via the OS `ping` binary | The classic. Often deprioritized or blocked by firewalls, so a timeout may mean "filtered", not "down". |
| `tcp` | Time to complete a TCP handshake to `host:port` | Measures the path real traffic takes and works through firewalls that allow the port. Confirms a *service* is up, not just an IP. Set the port with `p` / `--port`, or use `host:port` syntax. |
| `http` | Time to the first response byte of an HTTP(S) GET | Confirms the actual web service is healthy. Redirects are **not** followed, so you measure the host you typed rather than wherever it redirects. TLS certificate validation is disabled — latency is measured, not trust. |
| `trace` | Round-trip to *every hop* on the path, once per interval | Shows which hop adds the latency, not just the total. Needs the `traceroute` binary (`tracert` on Windows). |

In HTTP mode, if the target isn't actually serving web (e.g. a plain DNS IP), a red reminder appears in the top-right until you point it at a real webhost.

## Traceroute mode

Traceroute mode runs a full traceroute on every interval and draws the result as a single stacked bar. Each hop is one colored block, and a block's height is the latency *that hop adds* over everything before it — so the bar's total height is the destination's round-trip time, and a tall block means you found the slow link.

```
     50┤
       │                    ███  ← hop 8 (8.8.8.8), the destination
       │                   ▇███  ← thin separator line between hops
       │                   ████
    25 ┤                   ████
       │                   ████  ← hop 3, the block that owns most of the latency
       │                   ████
       │                   ▇▇▇▇
       │                   ████  ← hop 2
       │                   ▇▇▇▇
       └───────────────────────
 ▆ 1 192.168.1.1  ▆ 2 192.168.150.1  ▆ 3 77.221.45.178  …  6 *  7 *
```

A legend under the chart names each hop and shows its color. Hops that never answer (the `*` lines in ordinary traceroute output) keep their number but draw no block and get no swatch.

Details worth knowing:

- **The threshold disappears.** Bar color encodes the hop, not "fast or slow", so the warning line and the `t` keybinding are hidden in this mode.
- **The Y-scale auto-fits** (`TRACE_AUTO_SCALE`). A stacked bar needs roughly one chart row per hop before every hop gets its own block, and the 200 ms default would squash a 20 ms path into two rows. On entering trace mode the scale is fitted to the path's recent peak and the config row shows `yscale: 50ms (auto)`. Pressing `+` or `-` takes it back under manual control; an explicit `--scale` disables auto-fitting from the start. Leaving trace mode with auto-fit still on restores the scale you had before.
- **Switching in or out of trace clears the chart**, because a stacked per-hop bar and a single latency bar are not the same measurement. Switching between `icmp`, `tcp`, and `http` keeps the history.
- **Hop RTTs are not always increasing** — a router can reply faster than the one before it. The running total is clamped so the stack never shrinks, which means the bar's height is the largest RTT on the path (the destination's, on a healthy trace).
- **Very short bars lose detail.** A character cell can show at most two colors, so when the whole stack is only a row or two tall, some middle hops merge into their neighbours. Auto-fit normally keeps you out of this.
- Probes are paced at a minimum of one second regardless of `--interval`, since a full traceroute takes about that long.
- **The chart updates more slowly on Windows.** Linux `traceroute` probes every hop in parallel; `tracert` walks the path one hop at a time and always sends three probes per hop, so a trace that takes about a second on Linux can take several. Silent (`*`) hops cost the most, one full timeout per probe.

Traceroute samples are logged to their own CSV series — see [CSV Logging](#csv-logging).

### Keybindings

| Key | Action |
|---|---|
| `q` | Quit |
| `m` | Cycle probe mode (icmp → tcp → http → trace) |
| `c` | Cycle the color theme (see [Themes](#themes)) |
| `h` | Cycle through preset hosts (8.8.8.8, 1.1.1.1, 9.9.9.9, a RIPE Atlas anchor) |
| `H` | Enter a custom host |
| `p` | Change the TCP port (only shown in `tcp` mode) |
| `i` | Change probe interval |
| `t` | Change warning threshold (hidden in `trace` mode) |
| `+` / `-` | Zoom the Y-axis in or out (smaller or larger full-scale value) |
| `Left` / `Right` (or `,` / `.`) | Zoom the time window in or out |
| `l` | Toggle CSV logging on or off |
| `g` | Generate an HTML report from the logged CSVs |

The time window steps are: 1m, 2m, 3m, 5m, 10m, 15m, 30m, 1h, 1h30m, 2h, 3h.

## Customizing the colors

Everything visual lives in one block at the top of `pingtester.py`, under `VISUAL CONFIGURATION`. Colors are plain hex strings — edit them and restart. Every knob described below can also be packaged as a **theme** and switched live without editing the source.

### Themes

A theme is any file named `ptheme-<name>.py` in a `themes/` folder next to `pingtester.py`. Press **`c`** in-app to cycle through the available themes; the flash message shows the name you land on.

- **No `themes/` folder (or no `ptheme-*.py` in it)** → the colors defined in `pingtester.py` are used, exactly as before.
- **`themes/ptheme-default.py` exists** → it becomes the startup theme.
- The built-in look from `pingtester.py` is always entry 0 in the cycle, so `c` can always loop you back to it.

A theme file simply assigns the visual knobs you want to change at module level; anything you leave out inherits the built-in default. So a whole theme can be as short as:

```python
# themes/ptheme-green.py
THEME_NAME     = "green"          # optional; defaults to the filename after "ptheme-"
COLOR_BAR_OK   = "#00b34a"
COLOR_BAR_WARN = "#c8ff00"
TRACE_GRADIENT_START = "#0a3d1a"
TRACE_GRADIENT_END   = "#7dff9c"
```

The full set of knobs a theme may override — glyphs (including the block ladder), bar colors, all the interface/chrome colors, the frame weight, sub-cell blending, and the traceroute gradient — is documented in `themes/ptheme-greyscale.py`, which doubles as the reference. A handful of themes ship in the box: **greyscale**, **green**, **red**, **sunset**, and **ocean**. A broken or unreadable theme file is skipped rather than crashing the picker.

The sections below describe each knob in `pingtester.py`; the same names are what a theme file sets.

### Bar colors

```python
COLOR_BAR_OK   = "#3d7fd8"   # the part of a bar below the threshold
COLOR_BAR_WARN = "#ff5555"   # the part above it
COLOR_BAR_OVER = "#8b0000"   # cap on a bar that ran off the top of the chart
```

A bar is drawn in `COLOR_BAR_OK` up to the threshold line and `COLOR_BAR_WARN` above it, so only the part that actually breached shows as a warning. Every bar's topmost cell is drawn a shade darker than its body, which caps it rather than letting it dissolve into the background:

```python
CHART_BAR_TIP_DARKEN = 0.12  # OKLab lightness drop; 0 disables
```

The darkening happens in OKLab rather than by scaling RGB, so the cap keeps the hue of the bar instead of drifting toward grey.

### The traceroute hop gradient

Hop colors are interpolated between two endpoints, so you pick two hex values and any path length gets a ramp:

```python
TRACE_GRADIENT_START = "#bfdafc"   # first hop that answers
TRACE_GRADIENT_END   = "#3d7fd8"   # last hop (the destination)
```

Interpolation runs in the **OKLab** color space, which is perceptually uniform — equal numeric steps look like equal steps to the eye. Interpolating the same two colors in plain sRGB bunches the steps up in the dark end and drags the intermediate colors through muddy hues, which makes neighbouring hops hard to tell apart.

The ramp is spread across the hops that **answer**, not every hop number. A silent (`*`) hop draws nothing, so including it would spend part of the ramp on a color that never appears and leave the visible hops stopping short of `TRACE_GRADIENT_END`. The ramp is rebuilt whenever the number of answering hops changes, so hop 1 always gets the start color and the destination always gets the end color.

A thin line separates adjacent hop blocks:

```python
TRACE_HOP_SEPARATOR_PX    = 1                    # thickness in eighths of a row; 0 disables
TRACE_HOP_SEPARATOR_COLOR = TRACE_GRADIENT_END   # any hex value
TRACE_MIN_SEGMENT_ROWS    = 1                    # rows guaranteed to each answering hop
```

The separator is carved out of the block below it, so it costs no bar height. It needs `CHART_SUBCELL_BLEND` (below) and enough room for each hop to hold a whole row — on a bar only a couple of rows tall there is nowhere to draw it.

### Glyphs

```python
CHART_BLOCKS          = " ▁▂▃▄▅▆▇█"  # the eighth-block ladder; try " ░░▒▒▓▓██" for a chunkier look
CHART_TIMEOUT_GLYPH   = "╷"          # marks a lost sample at the baseline
CHART_THRESHOLD_GLYPH = "╌"          # the dashed warning line
CHART_LEGEND_SWATCH   = "▆"          # the color chip in the traceroute legend
CHART_Y_AXIS_WIDTH    = 7            # columns reserved for the y-axis labels
```

### How colors reach the terminal

A character cell can carry one glyph and two colors — a foreground and a background. Where two colors meet *inside* a cell (a bar crossing the threshold, or two hops meeting in a stack), the lower one is drawn as a partial block and the upper one becomes the cell's background, so the boundary lands on the exact pixel instead of snapping to a row edge:

```python
CHART_SUBCELL_BLEND = True   # False draws flat single-color cells
```

Turn this off if your terminal renders cell backgrounds oddly. Boundaries then snap to the nearest row, and hop separators are disabled along with it.

```python
USE_EXACT_TERMINAL_COLORS = True
```

Terminals only have a fixed palette of 256 colors, and its 6×6×6 RGB cube is coarse enough that adjacent light hops would land on the same entry — a 20-hop grey→blue ramp collapses onto about 10 distinct colors. So when the terminal allows it, `pingtester` redefines palette slots to the exact colors you configured, and restores the original values on exit.

Each color redefines the slot that already holds its **closest match**. That way a terminal which advertises the capability but quietly ignores the request still renders a sane approximation of your ramp, rather than whatever those slots happened to contain. When exact colors aren't available at all, each color claims the nearest palette entry no earlier color has taken, so every hop stays distinguishable even if it drifts slightly off the ideal ramp.

Set this to `False` if your terminal misbehaves; you'll get the nearest-match colors instead.

## CSV Logging

When logging is enabled (either via `--log` or by pressing `l`), a CSV file is written next to `pingtester.py`. A new file is created each hour to keep file sizes manageable.

File naming: `pingtester_YYYY-MM-DD_HH.csv`

Example: `pingtester_2024-11-15_14.csv`

Columns:

| Column | Description |
|---|---|
| `host` | The probe target at the time of the measurement |
| `mode` | Probe method used: `icmp`, `tcp`, or `http` |
| `timestamp` | Local time in ISO 8601 format (YYYY-MM-DDTHH:MM:SS) |
| `ping_ms` | Latency in milliseconds, empty if the probe timed out |

If you enable logging mid-session and a file for the current hour already exists, new rows are appended to it rather than overwriting it. CSV files written before the `mode` column existed are still readable by the report — those rows are treated as `icmp`.

### Traceroute logs

Traceroute samples need one column per hop, which does not fit the flat schema above, so they go to a separate hourly series: `pingtrace_YYYY-MM-DD_HH.csv`. Keeping the two apart means the HTML report's `pingtester_*.csv` glob never picks up a row shape it can't parse.

| Column | Description |
|---|---|
| `host` | The probe target at the time of the measurement |
| `mode` | Always `trace` |
| `timestamp` | Local time in ISO 8601 format |
| `total_ms` | The destination's round-trip time, empty if the trace never reached it |
| `hop01` … `hop20` | One column per hop, up to `TRACE_MAX_HOPS` |

Each hop cell packs the hop's address and its RTT into one value, joined by `CSV_HOP_DELIM` (default `|`). A hop that stayed silent writes an empty cell:

```
host,mode,timestamp,total_ms,hop01,hop02,hop03,hop04,hop05,hop06,hop07,hop08,...
8.8.8.8,trace,2026-07-10T09:23:46,18.256,192.168.1.1|0.338,192.168.150.1|8.995,77.221.45.178|18.324,77.221.45.177|18.152,77.221.45.170|18.280,,,8.8.8.8|18.256,...
```

`total_ms` is empty when the trace ran out of hops before reaching the target — otherwise a path that dies halfway would report the last responding router's RTT as if it were the destination's, and packet loss would read 0%.

The HTML report reads these files alongside the regular ones. It uses `total_ms` as the sample and ignores the hop columns, so a traceroute session charts exactly like any other probe — an empty `total_ms` counts as a lost packet. Trace rows appear as their own row in the per-method comparison table. A dedicated per-hop report may come later.

## HTML Report

After a logging session you can generate a self-contained HTML report from the CSV files. With no file arguments it reads both `pingtester_*.csv` and `pingtrace_*.csv` from the directory the script lives in.

```
python3 report.py [options] [files...]
```

### Options

| Option | Default | Description |
|---|---|---|
| `--output FILE` | `pingtester_report.html` | Output HTML file |
| `--threshold MS` | 100 | Latency threshold used to classify high-ping events |

### Examples

```bash
# Report from all CSVs next to the script (both ping and trace logs)
python3 report.py

# Custom output file and threshold
python3 report.py --output report.html --threshold 50

# Specific files or glob patterns
python3 report.py pingtester_2024-11-15_*.csv
python3 report.py --output night.html pingtester_2024-11-15_2*.csv pingtester_2024-11-15_23.csv
```

The report is a single HTML file that fetches Chart.js from a CDN on first open; everything else is self-contained.

### Overview tab

- Summary stats: host, measure method, session duration, total pings, avg / min / max / p95 latency, packet loss percentage
- A per-method comparison table when a session mixed probe methods (e.g. you switched from ICMP to TCP mid-run)
- Full-session mini chart showing the entire session at a glance
- Hourly bar chart with average latency and packet-loss percentage overlay
- Latency distribution histogram
- Outage table — every consecutive timeout run of 2 or more packets, with start time, end time, duration, and packet count
- High-ping periods table — every run of 10+ seconds above the threshold, sorted by average latency

### Timeline tab

- Full-resolution interactive chart covering the entire session; line segments are colored blue below the threshold and red above; timeouts appear as red cross markers; the threshold is drawn as a dashed yellow line
- Drag horizontally to zoom into a range; scroll to pan; double-click to reset
- A stats panel below the chart updates live as you zoom: pings, avg, min, max, p95, loss %, timeout count, and outages within the visible window
- When the visible range contains outages, a detail table for those outages appears below the stats

## Notes

- The history buffer holds up to 15,000 samples, which covers roughly 4 hours at a 1-second interval.
- When the view window is wider than the available ping history, the left side of the chart is empty until the buffer fills up.
- When zoomed out enough that multiple pings map to a single column, the bar shows the average for that bucket and the X-axis label shows the aggregation rate (e.g. `10s/col`).
- Changing the host mid-session does not clear the history. Old samples from the previous host remain in the buffer and will scroll off the left edge as new pings arrive.
