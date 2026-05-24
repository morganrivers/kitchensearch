"""
Recorded test: banner — click menu item "Copy link address"
"""


def run(h):
    h.screenshot("01_initial")
    h.wait(0.5)
    h.mousedown(130, 475, button=3)
    h.screenshot("02_menu_open", stable=False)
    h.mouseup(130, 475, button=3)
    h.wait(0.3)
    # Click "Copy link address" (second menu item; first item center≈y488, second≈y507)
    h.click(184, 507, button=1)
    h.screenshot("03_link_copied")
    h.wait(0.5)
    h.key("Escape")
