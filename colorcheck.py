#!/usr/bin/env python3
"""
colorcheck — diagnose how a terminal renders pingtester's colors.

Run it from the same directory as pingtester.py:

    python colorcheck.py        (Windows)
    python3 colorcheck.py       (Linux / macOS)

It draws the traceroute hop gradient twice, once with A_BOLD (which is what
pingtester currently does) and once without. On ncurses, A_BOLD does nothing to
a 256-color foreground. On PDCurses (windows-curses) it may be treated as an
intensity bit that ORs 8 into the color index, landing on a palette slot the
program never redefined — which would explain colors differing from Linux.

Press any key to exit; a text report is printed afterwards.
"""
import curses
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pingtester as p   # noqa: E402

REPORT = []


def log(line=""):
    REPORT.append(line)


def describe_lib():
    ver = getattr(curses, "version", None)
    if isinstance(ver, bytes):
        ver = ver.decode("ascii", "replace")
    nc = getattr(curses, "ncurses_version", None)
    return f"library={ver or '?'}  ncurses_version={nc or 'n/a (likely PDCurses)'}"


def main(scr):
    curses.start_color()
    try:
        curses.use_default_colors()
        udc = "ok"
    except curses.error as e:
        udc = f"FAILED ({e})"

    try:
        ccc = curses.can_change_color()
    except curses.error as e:
        ccc = f"error ({e})"

    log(describe_lib())
    log(f"COLORS={curses.COLORS}  COLOR_PAIRS={curses.COLOR_PAIRS}  "
        f"can_change_color={ccc}  use_default_colors={udc}")

    pal = p.Palette()
    log(f"Palette.exact={pal.exact}  "
        f"table={'xterm256' if pal.n_colors >= 256 else 'basic8 (only 8 colors!)'}  "
        f"bar_attr={'A_BOLD' if pal.bar_attr else 'none'}")

    colors = p.oklab_gradient(p.TRACE_GRADIENT_START, p.TRACE_GRADIENT_END, 6)
    pal.set_gradient(colors)
    slots = [pal.color(hx) for hx in colors]

    log()
    log("1) Did init_color() raise?   (this is the write; it is what matters)")
    log(f"   init_color() errors: {pal.init_color_errors} of {len(colors)}")
    if pal.exact and pal.init_color_errors == 0:
        log("   => the palette writes were accepted. Bars should be the exact hex.")
    elif pal.exact:
        log("   => init_color() FAILED. Bars fall back to the terminal's stock")
        log("      values for those slots, which is why colors differ per platform.")
    else:
        log("   => exact color disabled; bars use nearest-match slots.")

    log()
    log("2) Can the old slot value be read back?   (only affects restore-on-exit)")
    for i, hx in enumerate(colors):
        try:
            got = str(curses.color_content(slots[i]))
        except curses.error as e:
            got = f"ERR ({e}) -> will restore from the canonical xterm value"
        log(f"   hop{i+1} {hx} -> slot {slots[i]:3d}   {got}")

    log()
    log("3) Slots A_BOLD would land on, if this curses treats it as intensity:")
    for i, s in enumerate(slots):
        shifted = s | 8
        note = "same" if shifted == s else f"slot {shifted} (NOT redefined -> wrong color)"
        log(f"   hop{i+1}: slot {s:3d}  ->  {note}")
    log(f"   pingtester now draws bars with bar_attr="
        f"{'A_BOLD' if pal.bar_attr else 'no attribute'}, so this "
        f"{'still applies' if pal.bar_attr else 'no longer applies'}.")

    # ── on-screen comparison ──────────────────────────────────────────────
    scr.erase()
    H, W = scr.getmaxyx()
    if H < 12 or W < 60:
        scr.addstr(0, 0, "Terminal too small; resize to at least 60x12.")
        scr.getch()
        pal.restore()
        return

    scr.addstr(0, 0, "pingtester colorcheck", curses.A_BOLD)
    scr.addstr(1, 0, "Row 'plain' is what pingtester now draws. It should be a smooth")
    scr.addstr(2, 0, "pale-blue -> blue ramp. If 'bold' differs, A_BOLD shifts the color.")

    for row, (label, attr) in enumerate(
            (("bold ", curses.A_BOLD), ("plain", 0)), start=4):
        scr.addstr(row, 0, label)
        x = 14
        for hx in colors:
            scr.addstr(row, x, "████", pal.pair(hx) | attr)
            x += 5

    scr.addstr(7, 0, "hop slots:   " + "  ".join(f"{s:3d} " for s in slots))

    scr.addstr(9, 0, "separator blend (fg hop3 over bg hop6), bold then plain:")
    scr.addstr(10, 14, "▇▇▇▇", pal.pair(colors[2], colors[5]) | curses.A_BOLD)
    scr.addstr(10, 22, "▇▇▇▇", pal.pair(colors[2], colors[5]))

    scr.addstr(12, 0, "Press any key to quit and print the report...")
    scr.refresh()
    scr.getch()
    pal.restore()


if __name__ == "__main__":
    curses.wrapper(main)
    print("\n".join(REPORT))
