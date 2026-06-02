"""
Recorded test: darkmode
"""


def run(h):
    h.screenshot("01_initial")
    h.wait(0.84)
    h.key("Tab")
    h.wait(0.47)
    h.key("Tab")
    h.screenshot("02_Tab")
    h.wait(0.35)
    h.key("Tab")
    h.screenshot("03_Tab")
    h.screenshot("04_step")
    h.wait(0.97)
    h.key("Tab")
    h.wait(0.4)
    h.key("Escape")
    h.screenshot("05_Tab")
