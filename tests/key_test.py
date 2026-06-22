#!/usr/bin/env python3
"""Unit-test the harness key() layer in isolation, per platform.

Launches the tiny `key_probe.py` Tk window through the *same* platform harness
the real tests use, sends each key, and checks the probe received the correct
Tk key event. This pins down input-layer differences between Linux / macOS /
Windows (e.g. "F11" being typed as literal text on macOS) without any of the
real app's loading/timing noise.

Usage:
    python tests/key_test.py            # auto-wraps under xvfb-run on Linux
    python tests/key_test.py --json

Exit 0 if every key produced the expected keysym (and modifier); 1 otherwise.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(_TESTS_DIR))

_PROBE = _TESTS_DIR / "key_probe.py"

# Tk state bitmask (stable across platforms for these two).
SHIFT, CONTROL = 0x0001, 0x0004

# Each item: (send, {accepted keysyms}, required_modifier_bit_or_0)
# Page_Up/Down report as Prior/Next on X11 but Page_Up/Page_Down elsewhere, so
# accept both. shift+Tab can arrive as ISO_Left_Tab on X11.
KEY_CASES = [
    ("Return",      {"Return"},               0),
    ("Escape",      {"Escape"},               0),
    ("space",       {"space"},                0),
    ("Tab",         {"Tab"},                  0),
    ("Down",        {"Down"},                 0),
    ("Up",          {"Up"},                   0),
    ("Left",        {"Left"},                 0),
    ("Right",       {"Right"},                0),
    ("BackSpace",   {"BackSpace"},            0),
    ("Delete",      {"Delete"},               0),
    ("Home",        {"Home"},                 0),
    ("End",         {"End"},                  0),
    ("Page_Up",     {"Prior", "Page_Up"},     0),
    ("Page_Down",   {"Next", "Page_Down"},    0),
    ("F11",         {"F11"},                  0),
    ("F12",         {"F12"},                  0),
    ("a",           {"a"},                    0),
    ("z",           {"z"},                    0),
    ("1",           {"1"},                    0),
    ("ctrl+a",      {"a"},                    CONTROL),
    ("ctrl+Right",  {"Right"},                CONTROL),
    ("shift+Tab",   {"Tab", "ISO_Left_Tab"},  SHIFT),
]


def _make_harness(run_dir: Path, app_cmd: list[str]):
    if sys.platform == "darwin":
        from mac_harness import MacTestHarness
        return MacTestHarness(run_dir=run_dir, app_cmd=app_cmd)
    if sys.platform == "win32":
        from win_harness import WinTestHarness
        return WinTestHarness(run_dir=run_dir, app_cmd=app_cmd)
    from harness import TestHarness
    return TestHarness(run_dir=run_dir, app_cmd=app_cmd)


def _parse_log(path: Path):
    events = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            keysym = parts[0]
            try:
                state = int(parts[2])
            except ValueError:
                state = 0
            events.append((keysym, state))
    return events


def _verify(events):
    """Walk events in order, matching each expected case. Returns list of results."""
    results = []
    cursor = 0
    for send, accept, mod in KEY_CASES:
        found = None
        i = cursor
        while i < len(events):
            keysym, state = events[i]
            if keysym in accept and (mod == 0 or (state & mod) == mod):
                found = (keysym, state)
                cursor = i + 1
                break
            i += 1
        if found:
            results.append({"send": send, "status": "ok",
                            "got": found[0], "state": found[1]})
        else:
            # Did the keysym show up at all (maybe without the modifier)?
            seen = [k for k, _ in events[cursor:] if k in accept]
            detail = (f"got keysym but missing modifier" if seen
                      else "keysym never received")
            results.append({"send": send, "status": "fail", "detail": detail})
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    # Auto-wrap under xvfb-run on Linux when there's no display.
    if (sys.platform.startswith("linux")
            and not os.environ.get("DISPLAY")
            and not os.environ.get("_XVFB_WRAPPED")):
        env = os.environ.copy()
        env["_XVFB_WRAPPED"] = "1"
        sys.exit(subprocess.run(["xvfb-run", "-a", sys.executable] + sys.argv, env=env).returncode)

    run_dir = _TESTS_DIR / "test_run" / "key_test"
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "keys.log"
    log_path.unlink(missing_ok=True)

    app_cmd = [sys.executable, str(_PROBE), "--log", str(log_path)]
    harness = _make_harness(run_dir, app_cmd)
    # The probe is a bare Tk window, not the app — it emits no readiness beacon,
    # so don't block each key() waiting for one (that would hang this test).
    harness.beacon_sync = False

    print(f"\n  Key-command unit test on {sys.platform} ...", flush=True)
    try:
        with harness as h:
            h.wait(0.5)
            for send, _accept, _mod in KEY_CASES:
                h.key(send)
                h.wait(0.12)
            h.wait(0.3)
    except Exception as exc:
        print(f"  ERROR driving probe: {exc}", file=sys.stderr)

    events = _parse_log(log_path)
    results = _verify(events)
    passed = all(r["status"] == "ok" for r in results)

    if args.json:
        print(json.dumps({"platform": sys.platform, "passed": passed,
                          "results": results, "event_count": len(events)}, indent=2))
    else:
        print("  " + "-" * 50)
        for r in results:
            if r["status"] == "ok":
                print(f"  ok    {r['send']:<12} -> {r['got']}")
            else:
                print(f"  FAIL  {r['send']:<12} -> {r['detail']}")
        n_ok = sum(1 for r in results if r["status"] == "ok")
        print("  " + "-" * 50)
        print(f"  {n_ok}/{len(results)} keys correct on {sys.platform} "
              f"({len(events)} key events received)\n")

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
