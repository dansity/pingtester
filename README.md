# pingtester

A terminal-based network latency monitor. It pings a host continuously and draws a live bar chart of response times, color-coded by a configurable warning threshold. Useful for watching connection quality over time without setting up anything heavy.

<img width="1901" height="528" alt="image" src="https://github.com/user-attachments/assets/0b2c76dd-6e28-4a31-b113-44ce321e8667" />

Easily generate self contained html report:
<img width="1570" height="893" alt="image" src="https://github.com/user-attachments/assets/d2f97a89-414b-42f4-a5ea-7df2ed0ac590" />

## Features

- Three probe methods, switchable at runtime — **ICMP** echo, **TCP** connect, and **HTTP(S)** request
- Live scrolling bar chart with sub-row precision using Unicode block characters
- Blue bars below threshold, red above, timeout markers where packets are lost
- Configurable probe interval, warning threshold, and Y-axis scale
- Adjustable view window from 1 minute up to 3 hours
- Stats bar showing min, max, avg, p95 latency and packet loss
- Optional CSV logging, one file per hour, stored next to the script
- Self-contained HTML report generator
- No external dependencies on Linux and macOS (Python standard library only)

# Vibecoded

This application is vibecoded and human edited for good looks and my own purpose. It is "harmless" in a sense that it has no telemetry or analytics and sends no data anywhere other than to the target host you are probing. While it is vibecoded I take privacy seriously. Here is exactly what talks to the network:
- The **pingtester** utility only contacts the target host you choose. In ICMP mode (the default) it runs the system's `ping` utility in a loop. In TCP mode it opens a connection to the target's port. In HTTP mode it fetches the first byte of a GET request (TLS certificate validation is disabled, since it measures latency rather than trust). It has no telemetry and downloads nothing else.
- The **report** utility itself makes no network calls — it only reads your CSV logs and writes a local HTML file. That file, when opened in a browser, loads the JetBrains Mono font from Google Fonts (`fonts.googleapis.com` / `fonts.gstatic.com`) and Chart.js plus its plugins (moment, adapter, hammer.js, zoom) from `cdn.jsdelivr.net`. No measurement data is sent to them. The URLs live at the top of `report.py` — swap them for self-hosted copies to make the report fully self-contained.

Software comes with absolutely no warranty.

## Probe methods

`pingtester` can measure latency three different ways. Cycle between them at runtime with `m`, or set one at startup with `--mode`.

| Mode | Measures | Notes |
|---|---|---|
| `icmp` | ICMP echo round-trip via the OS `ping` binary | The classic. Often deprioritized or blocked by firewalls, so a timeout may mean "filtered", not "down". |
| `tcp` | Time to complete a TCP handshake to `host:port` | Measures the path real traffic takes and works through firewalls that allow the port. Confirms a *service* is up, not just an IP. Set the port with `p` / `--port`, or use `host:port` syntax. |
| `http` | Time to the first response byte of an HTTP(S) GET | Confirms the actual web service is healthy. Redirects are **not** followed, so you measure the host you typed rather than wherever it redirects. TLS certificate validation is disabled — latency is measured, not trust. |

In HTTP mode, if the target isn't actually serving web (e.g. a plain DNS IP), a red reminder appears in the top-right until you point it at a real webhost.

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
| `--mode MODE` | icmp | Probe method: `icmp`, `tcp`, or `http` |
| `--port PORT` | 443 | Port used in `tcp` mode |
| `--interval MS` | 1000 | Probe interval in milliseconds (minimum 100) |
| `--threshold MS` | 100 | Latency warning threshold in milliseconds |
| `--scale MS` | 200 | Y-axis full-scale value in milliseconds |
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
```

### Keybindings

| Key | Action |
|---|---|
| `q` | Quit |
| `m` | Cycle probe mode (icmp → tcp → http) |
| `h` | Cycle through preset hosts (8.8.8.8, 1.1.1.1, 9.9.9.9, a RIPE Atlas anchor) |
| `H` | Enter a custom host |
| `p` | Change the TCP port (only shown in `tcp` mode) |
| `i` | Change probe interval |
| `t` | Change warning threshold |
| `+` / `-` | Double or halve the Y-axis scale |
| `Left` / `Right` (or `,` / `.`) | Zoom the time window in or out |
| `l` | Toggle CSV logging on or off |
| `g` | Generate an HTML report from the logged CSVs |

The time window steps are: 1m, 2m, 3m, 5m, 10m, 15m, 30m, 1h, 1h30m, 2h, 3h.

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

## HTML Report

After a logging session you can generate a self-contained HTML report from the CSV files.

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
# Report from all CSVs in the current directory
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
