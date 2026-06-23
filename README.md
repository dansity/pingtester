# pingtester

A terminal-based network latency monitor. It pings a host continuously and draws a live bar chart of response times, color-coded by a configurable warning threshold. Useful for watching connection quality over time without setting up anything heavy.

<img width="1901" height="528" alt="image" src="https://github.com/user-attachments/assets/0b2c76dd-6e28-4a31-b113-44ce321e8667" />

## Features

- Live scrolling bar chart with sub-row precision using Unicode block characters
- Blue bars below threshold, red above, timeout markers where packets are lost
- Configurable ping interval, warning threshold, and Y-axis scale
- Adjustable view window from 1 minute up to 3 hours
- Stats bar showing min, max, avg, p95 latency and packet loss
- Optional CSV logging, one file per hour, stored next to the script
- No external dependencies on Linux and macOS

# Vibecoded

This application is vibecoded and human edited for good looks for my own use. It is "harmless" in a sense that it does not download nor send data anywhere other than the ping calls. Comes with absolute no warranty.

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
git clone https://github.com/yourname/pingtester.git
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
| `--host HOST` | 8.8.8.8 | Host to ping |
| `--interval MS` | 1000 | Ping interval in milliseconds (minimum 100) |
| `--threshold MS` | 100 | Latency warning threshold in milliseconds |
| `--scale MS` | 200 | Y-axis full-scale value in milliseconds |
| `--log` | off | Enable CSV logging at startup |

### Examples

```
python3 pingtester.py
python3 pingtester.py --host 1.1.1.1 --interval 500
python3 pingtester.py --host 192.168.1.1 --threshold 10 --scale 50
python3 pingtester.py --host 8.8.8.8 --log
```

### Keybindings

| Key | Action |
|---|---|
| `q` | Quit |
| `h` | Cycle through preset hosts (8.8.8.8, 1.1.1.1, 9.9.9.9, 208.67.222.222) |
| `H` | Enter a custom host |
| `i` | Change ping interval |
| `t` | Change warning threshold |
| `+` / `-` | Double or halve the Y-axis scale |
| `Left` / `Right` (or `,` / `.`) | Zoom the time window in or out |
| `l` | Toggle CSV logging on or off |

The time window steps are: 1m, 2m, 3m, 5m, 10m, 15m, 30m, 1h, 1h30m, 2h, 3h.

## CSV Logging

When logging is enabled (either via `--log` or by pressing `l`), a CSV file is written next to `pingtester.py`. A new file is created each hour to keep file sizes manageable.

File naming: `pingtester_YYYY-MM-DD_HH.csv`

Example: `pingtester_2024-11-15_14.csv`

Columns:

| Column | Description |
|---|---|
| `host` | The ping target at the time of the measurement |
| `timestamp` | Local time in ISO 8601 format (YYYY-MM-DDTHH:MM:SS) |
| `ping_ms` | Round-trip time in milliseconds, empty if the packet timed out |

If you enable logging mid-session and a file for the current hour already exists, new rows are appended to it rather than overwriting it.

## Notes

- The history buffer holds up to 15,000 samples, which covers roughly 4 hours at a 1-second interval.
- When the view window is wider than the available ping history, the left side of the chart is empty until the buffer fills up.
- When zoomed out enough that multiple pings map to a single column, the bar shows the average for that bucket and the X-axis label shows the aggregation rate (e.g. `10s/col`).
- Changing the host mid-session does not clear the history. Old samples from the previous host remain in the buffer and will scroll off the left edge as new pings arrive.
