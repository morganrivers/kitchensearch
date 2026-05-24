"""
Recorded test: banner
"""


def run(h):
    h.screenshot("01_initial")
    h.wait(2.27)
    h.mousedown(173, 472, button=3)
    h.screenshot("02_step", stable=False)
    h.wait(0.41)
    h.mouseup(173, 472, button=3)
    h.wait(0.52)
    h.click(242, 513, button=1)
    h.screenshot("03_click_242_513")
    h.wait(0.48)
    h.click(256, 535, button=1)
    h.screenshot("04_click_256_535")
    h.wait(0.63)
    h.key("Escape")
