"""
Recorded test: banner
"""


def run(h):
    h.screenshot("01_initial")
    h.wait(2.05)
    h.mousedown(812, 803, button=3)
    h.screenshot("02_step", stable=False)
    h.wait(0.41)
    h.mouseup(812, 803, button=3)
    h.wait(0.84)
    h.click(883, 840, button=1)
    h.screenshot("03_click_883_840")
    h.wait(1.09)
    h.key("Escape")
