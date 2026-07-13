# pingtester theme — "sunset" (gradient, chunky blocks)
#
# A theme is any ptheme-*.py here; set only the knobs you want and the rest
# fall back to pingtester.py's built-in look. Cycle with [c] in-app.
# See themes/ptheme-greyscale.py for the full list of knobs.

THEME_NAME = "sunset"

# Chunky shaded blocks + a bold frame for a heavier, warmer look.
CHART_BLOCKS  = " ░░▒▒▓▓██"
UI_FRAME_BOLD = True

COLOR_BAR_OK   = "#f2a24b"   # warm orange
COLOR_BAR_WARN = "#e5486b"   # over threshold: pink-red
COLOR_BAR_OVER = "#3a1c5e"   # clipped: deep purple cap

UI_COLOR_BORDER     = "#c85a7c"
UI_COLOR_TITLE      = "#ffb26b"
UI_COLOR_STAT_VALUE = "#ffd98a"
UI_COLOR_STAT_LABEL = "#c98aa5"
UI_COLOR_OK         = "#ffb26b"
UI_COLOR_ALERT      = "#ff5e7a"
UI_COLOR_DIM        = "#7a5a86"

# Gold → violet ramp across the hops — the signature sunset gradient.
TRACE_GRADIENT_START      = "#ffd166"
TRACE_GRADIENT_END        = "#6a3d99"
TRACE_HOP_SEPARATOR_COLOR = "#2a1b3d"
