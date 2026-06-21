"""Prototype of the fast, screen-state test style.

Contrast with the recorded tests (e.g. test_02_keyword_search), which replay
every keystroke with a guessed ``h.wait(...)`` after each one and snapshot the
screen dozens of times. That is slow and timing-fragile: a slower runner loads
results late and the screenshots diverge per OS.

This style instead:
  * uses the front search screen and types the **whole** word at once
    (with the default semantic-first settings, typing searches live — no
    Enter needed to run it),
  * captures **one image per stable screen** (not per keystroke),
  * brackets the copy and Escape with before/after images, and
  * synchronises on the app's own readiness beacons (KSEVENT) rather than
    sleeping:
        - ``loaded ...``       image results have finished rendering
        - ``ready mode=...``   a screen has been painted (after a transition)
        - ``select idx=...``   the selection actually moved
        - ``copy_selected ...``a copy actually fired

No per-key callbacks, no fixed sleeps, far fewer frames — so it runs fast and
lands on the same screen on every OS. *Which* image a copy lands on is checked
separately by the clipboard gate; this verifies the screens themselves.
"""


def run(h):
    # 1. Front menu/search screen. launch() already waited for the window to
    #    map + settle, so it is up; capture it.
    h.screenshot("01_menu")

    # 2. Type the whole query at once. With semantic-first settings this runs
    #    the search live, so just wait for the results to finish loading and
    #    the list to be painted — event-driven, no sleep.
    h.type("dancing cow")
    h.wait_for_event("loaded")
    h.wait_for_event("ready mode=imagelist")
    h.screenshot("02_results")               # state *before* the copy

    # 3. Arrow into the result list and copy. The first arrow after a fresh
    #    search can be absorbed establishing list focus, so confirm the
    #    selection actually moved via the "select" beacon (still event-driven)
    #    before pressing Enter, then wait for the copy to fire.
    for _ in range(3):
        h.key("Down")
        if h.wait_for_event("select idx=", timeout=3):
            break
    h.key("Return")
    h.wait_for_event("copy_selected", timeout=8)
    h.screenshot("03_after_copy")            # state *after* the copy

    # 4. Escape out of the results; wait for the next screen to be painted.
    h.key("Escape")
    h.wait_for_event("ready", timeout=8)
    h.screenshot("04_after_escape")          # state *after* Escape
