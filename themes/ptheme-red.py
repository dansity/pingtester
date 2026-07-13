# pingtester theme — "red" (ember)
#
# A theme is any ptheme-*.py here; set only the knobs you want and the rest
# fall back to pingtester.py's built-in look. Cycle with [c] in-app.
# See themes/ptheme-greyscale.py for the full list of knobs.

THEME_NAME = "red"

COLOR_BAR_OK   = "#e0503a"   # warm red-orange
COLOR_BAR_WARN = "#ffd23a"   # over threshold: gold
COLOR_BAR_OVER = "#fff6d6"   # clipped: pale, stands out atop the ember bars

UI_COLOR_BORDER     = "#a83232"
UI_COLOR_TITLE      = "#ff6b5e"
UI_COLOR_STAT_VALUE = "#ffb26b"
UI_COLOR_STAT_LABEL = "#c77a6a"
UI_COLOR_OK         = "#ff8f6b"
UI_COLOR_ALERT      = "#ffe14a"
UI_COLOR_DIM        = "#7a3b32"

# Dark ember → gold ramp across the hops.
TRACE_GRADIENT_START      = "#5a0f0f"
TRACE_GRADIENT_END        = "#ffcf3a"
TRACE_HOP_SEPARATOR_COLOR = "#2a0606"
