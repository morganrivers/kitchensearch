"""
Recorded test: searchandfilter
"""


def run(h):
    h.screenshot("01_initial")
    h.wait(1.32)
    h.wait(0.21)
    h.wait(0.16)
    h.type("one")
    h.key("space")
    h.screenshot("02_step")
    h.screenshot("03_step")
    h.type("thing")
    h.key("space")
    h.type("to")
    h.key("space")
    h.screenshot("04_space")
    h.wait(0.19)
    h.screenshot("05_step")
    h.wait(0.38)
    h.type("flah")
    h.key("BackSpace")
    h.screenshot("06_BackSpace")
    h.wait(0.44)
    h.type("g")
    h.key("Return")
    h.screenshot("07_Return")
    h.screenshot("08_step")
    h.wait(2.12)
    h.key("Return")
