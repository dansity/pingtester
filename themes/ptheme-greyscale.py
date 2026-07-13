# pingtester theme — "greyscale"
#
# Any file named  ptheme-<name>.py  in this folder is a selectable theme.
# Cycle themes live in-app with the [c] key. Set only the knobs you want to
# change; anything you leave out inherits pingtester.py's built-in look.
#
# Full list of knobs (see the "VISUAL CONFIGURATION" block in pingtester.py
# for what each one does):
#
#   Glyphs   CHART_BLOCKS (9 chars, empty→full), CHART_TIMEOUT_GLYPH,
#            CHART_THRESHOLD_GLYPH, CHART_LEGEND_SWATCH
#   Bars     COLOR_BAR_OK, COLOR_BAR_WARN, COLOR_BAR_OVER            (hex)
#   Chrome   UI_COLOR_BORDER, UI_COLOR_TITLE, UI_COLOR_STAT_VALUE,
#            UI_COLOR_STAT_LABEL, UI_COLOR_OK, UI_COLOR_ALERT, UI_COLOR_DIM
#   Toggles  UI_FRAME_BOLD, CHART_SUBCELL_BLEND (bool),
#            CHART_BAR_TIP_DARKEN (0..1 OKLab drop)
#   Trace    TRACE_GRADIENT_START, TRACE_GRADIENT_END,
#            TRACE_HOP_SEPARATOR_COLOR, TRACE_HOP_SEPARATOR_PX,
#            TRACE_MIN_SEGMENT_ROWS, TRACE_SHOW_LEGEND
#
# Optional:  THEME_NAME = "..."   overrides the name shown in-app
#            (defaults to the part of the filename after "ptheme-").

THEME_NAME = "greyscale"

# Chunkier block ramp: shading tiers instead of thin eighth-bars.
CHART_BLOCKS = " ░░▒▒▓▓██"

# No hue to lean on, so severity is encoded purely by brightness.
COLOR_BAR_OK   = "#8a8a8a"
COLOR_BAR_WARN = "#e6e6e6"
COLOR_BAR_OVER = "#ffffff"

UI_COLOR_BORDER     = "#6a6a6a"
UI_COLOR_TITLE      = "#d6d6d6"
UI_COLOR_STAT_VALUE = "#cfcfcf"
UI_COLOR_STAT_LABEL = "#9a9a9a"
UI_COLOR_OK         = "#bcbcbc"
UI_COLOR_ALERT      = "#f5f5f5"
UI_COLOR_DIM        = "#5f5f5f"

# Dark → light grey ramp across the traceroute hops.
TRACE_GRADIENT_START     = "#4a4a4a"
TRACE_GRADIENT_END       = "#e8e8e8"
TRACE_HOP_SEPARATOR_COLOR = "#2a2a2a"
