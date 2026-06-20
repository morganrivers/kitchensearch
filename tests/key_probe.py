#!/usr/bin/env python3
"""Minimal Tk window that logs every key event it receives.

Used to unit-test the test harnesses themselves: it lets `key_test.py` verify
that ``harness.key("F11")`` / ``key("Down")`` / ``key("ctrl+a")`` actually
deliver the *correct* Tk key event on each platform, isolated from the real
app's loading/rendering/timing.

The window title is "Kitchen Search" so the existing harnesses locate it the
same way they locate the real app. Each received <KeyPress> is appended to the
log file (``--log`` or $KITCHENSEARCH_KEY_LOG) as:

    <keysym>\t<char repr>\t<state bitmask>

A trailing keysym of "F1" typed as three chars 'F','1' would therefore show up
as separate 'F'/'1' events instead of a single "F1" — which is exactly the
mac literal-keystroke bug this is meant to catch.
"""
import argparse
import os
import sys
import tkinter as tk


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=os.environ.get("KITCHENSEARCH_KEY_LOG", "keys.log"))
    args = ap.parse_args()

    log = open(args.log, "a", buffering=1, encoding="utf-8")

    root = tk.Tk()
    root.title("Kitchen Search")
    root.geometry("420x240+120+120")

    # A focusable text widget so printable keys land somewhere and the entry
    # has keyboard focus (mirrors the app's search box).
    box = tk.Text(root)
    box.pack(fill="both", expand=True)
    box.focus_set()

    def on_key(e):
        log.write(f"{e.keysym}\t{e.char!r}\t{e.state}\n")
        # Bind at the widget level and swallow the event: this fires *before*
        # the Text class binding, so focus-traversal keys (Tab / Shift-Tab) are
        # logged instead of being consumed, and focus stays on this widget.
        return "break"

    box.bind("<KeyPress>", on_key)
    # Make sure we actually have focus once mapped.
    root.after(100, lambda: (root.focus_force(), box.focus_set()))
    print("KEY_PROBE_READY", flush=True)
    root.mainloop()


if __name__ == "__main__":
    main()
