"""
Recorded test: searchandfilter
"""


def run(h):
    h.wait(0.5)
    h.key("Down")
    h.screenshot("01_initial")
    h.wait(0.17)
    h.key("Down")
    h.wait(0.16)
    h.key("Down")
    h.key("Down")
    h.screenshot("02_Down")
    h.wait(0.16)
    h.key("Down")
    h.wait(0.3)
    h.key("Return")
    h.screenshot("03_Down")
    h.screenshot("04_step")
    h.wait(1.41)
    h.key("Down")
    h.screenshot("05_Down")
    h.wait(2.67)
    h.key("Escape")
    h.screenshot("06_Escape")
    h.wait(0.61)
    h.key("Escape")
