# pingtester theme — "ocean" (gradient)
#
# A theme is any ptheme-*.py here; set only the knobs you want and the rest
# fall back to pingtester.py's built-in look. Cycle with [c] in-app.
# See themes/ptheme-greyscale.py for the full list of knobs.

THEME_NAME = "ocean"

COLOR_BAR_OK   = "#2ba6c9"   # cyan
COLOR_BAR_WARN = "#ff6b6b"   # over threshold: coral
COLOR_BAR_OVER = "#08243a"   # clipped: deep navy cap

UI_COLOR_BORDER     = "#2d7d9a"
UI_COLOR_TITLE      = "#4fd0e0"
UI_COLOR_STAT_VALUE = "#8fe0d8"
UI_COLOR_STAT_LABEL = "#6a9bb0"
UI_COLOR_OK         = "#4fd0e0"
UI_COLOR_ALERT      = "#ff6b6b"
UI_COLOR_DIM        = "#3a6070"

# Pale aqua (surface) → deep navy (depths) ramp across the hops.
TRACE_GRADIENT_START      = "#7fe3d4"
TRACE_GRADIENT_END        = "#12325e"
TRACE_HOP_SEPARATOR_COLOR = "#06182e"
