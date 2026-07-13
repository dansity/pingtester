# pingtester theme — "green" (phosphor / matrix)
#
# A theme is any ptheme-*.py here; set only the knobs you want and the rest
# fall back to pingtester.py's built-in look. Cycle with [c] in-app.
# See themes/ptheme-greyscale.py for the full list of knobs.

THEME_NAME = "green"

# Keep the crisp eighth-block ramp for a terminal-glow feel.
COLOR_BAR_OK   = "#00b34a"   # healthy: bright phosphor green
COLOR_BAR_WARN = "#c8ff00"   # over threshold: acid lime
COLOR_BAR_OVER = "#ff3b3b"   # clipped: red, unmistakable against the green

UI_COLOR_BORDER     = "#1f8f3a"
UI_COLOR_TITLE      = "#3dff6e"
UI_COLOR_STAT_VALUE = "#8dff9f"
UI_COLOR_STAT_LABEL = "#4fae66"
UI_COLOR_OK         = "#3dff6e"
UI_COLOR_ALERT      = "#ff5555"
UI_COLOR_DIM        = "#2c6b3a"

# Dark → bright green ramp across the hops.
TRACE_GRADIENT_START      = "#0a3d1a"
TRACE_GRADIENT_END        = "#7dff9c"
TRACE_HOP_SEPARATOR_COLOR = "#04240f"
