# Visual Regression Test Harness

End-to-end tests for Kitchen Search. Each test script drives the app via keyboard/mouse automation, captures screenshots at each step, and compares them against a stored baseline. Differences are shown as pixel-highlighted images plus a structured widget-geometry diff.

---

## Quick start

```bash
# First time — capture the baseline
./run.sh --update-baseline

# After any code change — compare against baseline
./run.sh

# Headless / CI — exits non-zero if anything changed, no UI
./run.sh --no-viewer
```

---

## Recording a new test

```bash
./.venv/bin/python3 tests/record.py <test_name>
```

Example:

```bash
./.venv/bin/python3 tests/record.py keyword_search
```

1. The app opens on your screen.
2. Interact with it normally — every keystroke, mouse click, and pause is recorded.
3. Close the app window when done (or press **Ctrl+F12** to stop recording without closing).
4. A test script is written to `tests/scripts/test_NN_<name>.py`.
5. The test is automatically replayed headlessly and the GIF path is printed:

```
GIF → tests/runs/20260522_110000/test_02_keyword_search/recording.gif
View with:  firefox tests/runs/.../recording.gif
```

---

## Writing a test manually

Create `tests/scripts/test_NN_<name>.py` (NN = two-digit number, higher runs later):

```python
def run(h):
    h.wait(0.5)
    h.screenshot("01_initial")

    h.type("fire")
    h.wait(0.3)
    h.screenshot("02_typed_fire")

    h.key("Down")
    h.screenshot("03_selected")

    h.key("Return")
    h.wait(0.5)
    h.screenshot("04_result")

    h.click(120, 340)          # click at (x, y) relative to window top-left
    h.screenshot("05_clicked")
```

Available actions:

| Call | Description |
|------|-------------|
| `h.screenshot("name")` | Capture window + widget dump |
| `h.type("text")` | Type a string |
| `h.key("Return")` | Send a key (`Up`, `Down`, `Escape`, `ctrl+a`, `ctrl+BackSpace`, …) |
| `h.click(x, y)` | Left-click at window-relative coordinates |
| `h.click(x, y, button=3)` | Right-click |
| `h.wait(seconds)` | Sleep |

---

## Reviewing diffs

After a run with changes the diff viewer opens automatically:

| Key | Action |
|-----|--------|
| `a` | **Approve** — accepts the change, updates baseline |
| `b` | **Mark broken** — flags it, exits non-zero |
| `←` / `→` | Previous / next changed screenshot |

Each changed screenshot shows three panels side by side: **Baseline · Current · Diff** (changed pixels highlighted red).

To open the viewer manually for a specific run:

```bash
./.venv/bin/python3 tests/viewer.py tests/runs/<timestamp>/test_01_main_menu
```

---

## Updating the baseline

To accept all current screenshots as the new baseline:

```bash
./run.sh --update-baseline
```

To accept only one test:

```bash
./.venv/bin/python3 tests/run_tests.py --update-baseline --test test_02_keyword_search
```

---

## File layout

```
tests/
  record.py          — interactive recorder → generates test scripts
  run_tests.py       — main runner
  run.sh             — convenience wrapper (also opens GIF in firefox)
  harness.py         — TestHarness class (launch, screenshot, key, click)
  compare.py         — pixel diff + widget geometry diff
  viewer.py          — tkinter diff viewer
  widget_dump.py     — widget tree serialiser (server in-app, client in harness)
  baseline/
    test_01_*/       — baseline PNGs + JSON widget dumps (commit these)
  runs/
    <timestamp>/     — output of each run (gitignored)
  scripts/
    test_01_*.py     — test scripts (commit these)
```

Commit `tests/baseline/` and `tests/scripts/`. The `tests/runs/` directory is transient — add it to `.gitignore`.
