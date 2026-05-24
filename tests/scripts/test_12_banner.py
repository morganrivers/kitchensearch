"""
Recorded test: banner — right-click menu appears on banner
"""


def run(h):
    h.screenshot("01_initial")
    h.wait(0.5)
    # Right-click the banner body to open context menu
    h.mousedown(130, 475, button=3)
    h.screenshot("02_menu_open", stable=False)
    h.mouseup(130, 475, button=3)
    h.wait(0.3)
    h.screenshot("03_menu_stays")
    # Dismiss by clicking "no thanks" (win_x=393..480, win_y=454..472, center≈437,463)
    h.click(437, 463, button=1)
    h.screenshot("04_dismissed")
