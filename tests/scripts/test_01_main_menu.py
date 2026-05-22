"""
Test 01 — Main menu
Captures the initial menu state, then exercises basic keyboard navigation.
"""


def run(h):
    # Initial render
    h.wait(0.5)
    h.screenshot("01_initial")

    # Navigate down through menu items
    h.key("Down")
    h.wait(0.15)
    h.screenshot("02_down_1")

    h.key("Down")
    h.wait(0.15)
    h.screenshot("03_down_2")

    h.key("Down")
    h.wait(0.15)
    h.screenshot("04_down_3")

    h.key("Up")
    h.wait(0.15)
    h.screenshot("05_up_back")

    h.key("Home")
    h.wait(0.15)
    h.screenshot("06_home")

    h.key("End")
    h.wait(0.15)
    h.screenshot("07_end")

    # Type into search bar (keyword search)
    h.key("Home")
    h.wait(0.1)
    h.type("fire")
    h.wait(0.3)
    h.screenshot("08_typed_fire")

    # Clear with ctrl+a then Delete
    h.key("ctrl+Right")
    h.key("Delete")
    h.wait(0.2)
    h.screenshot("09_right")
    # Clear with ctrl+a then Delete
    h.key("ctrl+BackSpace")
    h.key("Delete")
    h.wait(0.2)
    h.screenshot("10_cleared")
