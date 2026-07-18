# Visual Regression Test Harness

End-to-end tests for Kitchen Search. Each test script drives the app via keyboard/mouse automation, captures screenshots at each step, and compares them against a stored baseline. Differences are shown as pixel-highlighted images plus a structured widget-geometry diff.

---

## Quick start

# Record a new test:
micromamba run -n py311 python tests/record.py my_new_feature                                                        
  → writes scripts/test_03_my_new_feature.py                                                 
  → runs --update-baseline --test test_03_my_new_feature                                     
  → saves to baseline_unapproved/test_03_my_new_feature/                                     
  → prints GIF path + "Approve with: micromamba run -n py311 python tests/approve.py test_03_my_new_feature"         
                                                                                             
# Re-baseline existing tests (by prefix):                                                    
micromamba run -n py311 python tests/run_tests.py --update-baseline --test test_01                                   
  → saves to baseline_unapproved/test_01_main_menu/ (clears old unapproved first)            
  → writes meta.json with {"recorded_at": "2026-05-22 12:34:56"}                             
                                                                                             
# Review then approve:                                                                       
micromamba run -n py311 python tests/approve.py test_03_my_new_feature   # specific                                  
micromamba run -n py311 python tests/approve.py                          # all pending                             
                                                                                             
# Normal test run (compares against baseline_approved):
micromamba run -n py311 python tests/run_tests.py                                                                    

---

## Recording a new test

```bash
micromamba run -n py311 python tests/record.py <test_name>
```

Example:

```bash
micromamba run -n py311 python tests/record.py keyword_search
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
micromamba run -n py311 python tests/viewer.py tests/runs/<timestamp>/test_01_main_menu
```

---

## Updating the baseline

To accept all current screenshots as the new baseline:

```bash
./run.sh --update-baseline
```

To accept only one test:

```bash
micromamba run -n py311 python tests/run_tests.py --update-baseline --test test_02_keyword_search
```

---

## Cross-platform CI (mac / Windows / Linux)

Three GitHub workflows (`.github/workflows/{linux,mac,windows}-smoke.yml`) install
the app on each OS, drive the same tests through the platform harness, and upload
each test's folder (GIF, screenshots, copied clipboard image) as an artifact.

**Pass condition is baseline-free.** The old gate compared the copied clipboard
image against a stored hash and failed on every harmless re-encoding, forcing
constant re-baselining across OSes. The gate now (`tests/health_check.py`) fails
only when an OS is genuinely broken:

- the driver recorded a crash (`status.json` `failed`),
- a screenshot rendered blank / solid,
- a copied clipboard image is blank / black / transparent,
- the whole run copied nothing at all (`copied-images.log` empty on every test).

Cosmetic per-OS pixel differences pass. Run it locally against a downloaded run:

```bash
micromamba run -n py311 python tests/health_check.py --all \
  --skip test_16_story --run-dir tests/test_run
```

### Eyeball what each OS copied (one command)

`tests/os_compare.py` downloads the newest Linux/macOS/Windows artifacts with `gh`
and builds one self-contained HTML page showing the final copied image per test,
one column per OS. Informational only — it never gates CI.

```bash
micromamba run -n py311 python tests/os_compare.py --open
```

Needs the GitHub CLI authenticated for this repo. Pin specific runs instead of the
newest with `--linux/--mac/--windows <run_id>`. The 6-char tag under each thumbnail
is the image content hash: identical tag = byte-identical across OSes.

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
  baseline_approved/
    test_01_*/       — approved baseline PNGs + JSON widget dumps (tracked)
  baseline_unapproved/
    test_01_*/       — pending review, not yet approved (gitignored)
  test_run/
    <timestamp>/     — output of each run (gitignored)
  scripts/
    test_NN_*.py     — test scripts (tracked)
  semantic_tests/
    semantic_quality.py  — search quality benchmark (8 conceptual queries)
    results/         — timestamped JSON benchmark results (gitignored)
  approve.py         — interactive approve/reject for unapproved baselines
  viewer.py          — tkinter diff viewer (baseline · current · diff panels)
```

Tracked: `tests/baseline_approved/`, `tests/scripts/`, `tests/semantic_tests/semantic_quality.py`.
Gitignored: `tests/test_run/`, `tests/baseline_unapproved/`, `tests/semantic_tests/results/`.
