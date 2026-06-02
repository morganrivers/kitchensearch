"""
Recorded test: searchandfilter
"""


def run(h):
    h.screenshot("01_initial")
    h.wait(2.35)
    h.key("Return")
    h.screenshot("02_Return")
    h.wait(3.0)
    h.key("Escape")
    h.screenshot("03_Escape")
    h.wait(0.59)
    h.key("Escape")
