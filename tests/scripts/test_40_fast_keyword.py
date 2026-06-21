"""Prototype of the fast, screen-state test style.

Contrast with the recorded tests (e.g. test_02_keyword_search), which replay
every keystroke with a guessed ``h.wait(...)`` after each one and snapshot the
screen dozens of times. That is slow and timing-fragile: a slower runner loads
results late and the screenshots — and copies — diverge per OS.

This style instead:
  * uses the main menu and types the **whole** word at once,
  * captures **one image per stable screen** (not per keystroke),
  * brackets each Enter/Escape with a before/after image, and
  * synchronises on the app's own readiness beacons (KSEVENT) rather than
    sleeping:
        - ``loaded ...``     image results have finished rendering
        - ``ready mode=...`` a new screen has been painted (after Enter/Escape)

No per-key callbacks, no fixed sleeps, far fewer frames — so it runs fast and
lands on the same screen on every OS. Which *image* a copy lands on is checked
separately by the clipboard gate (check_clipboard); this test verifies the
screens themselves are identical across platforms.
"""


def run(h):
    # 1. Main menu. launch() already waited for the window to map + settle,
    #    so the menu is up; capture it directly.
    h.screenshot("01_menu")

    # 2. Type the full query in one go and capture the fully-typed screen
    #    (state *before* the search Enter).
    h.type("dancing cow")
    h.screenshot("02_typed")

    # 3. Run the search and wait until the results have *finished* loading and
    #    the list screen is painted — event-driven, no sleep — then capture the
    #    one results image (state *after* the search Enter).
    h.key("Return")
    h.wait_for_event("loaded")
    h.wait_for_event("ready mode=imagelist")
    h.screenshot("03_results")

    # 4. Escape back to the menu; wait for the menu screen to be repainted, then
    #    capture it (state *after* Escape).
    h.key("Escape")
    h.wait_for_event("ready")
    h.screenshot("04_after_escape")
