"""
Visual diff viewer.  Shows baseline | current | diff side by side.
Approve (updates baseline) or mark broken for each changed screenshot.

Usage:
    python tests/viewer.py tests/runs/<timestamp>
"""

import sys
import json
import shutil
from pathlib import Path

import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk

sys.path.insert(0, str(Path(__file__).parent))
from compare import compare_runs

# ── layout constants ──────────────────────────────────────────────────────────
THUMB_W = 340
THUMB_H = 340
PAD     = 8


class DiffViewer(tk.Tk):
    def __init__(self, run_dir: Path, results: dict[str, dict], baseline_dir: Path):
        super().__init__()
        self.title("Test Diff Viewer")
        self.configure(bg="#1e1e1e")

        self.run_dir      = run_dir
        self.baseline_dir = baseline_dir
        self.results      = results
        self.decisions: dict[str, str] = {}   # name → "ok" | "broken"

        # Only show screenshots that are not "ok"
        self.names = sorted(
            [n for n, r in results.items() if r["status"] != "ok"]
        )
        self.idx = 0

        self._build_ui()
        if self.names:
            self._show(0)
        else:
            self._lbl_title.configure(text="All screenshots match baseline — nothing to review.")
            self._refresh_list()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        top = tk.Frame(self, bg="#1e1e1e")
        top.pack(fill="x", padx=PAD, pady=PAD)

        self._lbl_title = tk.Label(
            top, text="", font=("Helvetica", 13, "bold"),
            bg="#1e1e1e", fg="#e0e0e0", anchor="w"
        )
        self._lbl_title.pack(side="left", fill="x", expand=True)

        self._lbl_counter = tk.Label(
            top, text="", font=("Helvetica", 11),
            bg="#1e1e1e", fg="#888888"
        )
        self._lbl_counter.pack(side="right")

        # Main area: image panels + sidebar
        main = tk.Frame(self, bg="#1e1e1e")
        main.pack(fill="both", expand=True, padx=PAD)

        # Three image panels (left)
        panels = tk.Frame(main, bg="#1e1e1e")
        panels.pack(side="left", fill="both", expand=True)

        self._canvas_b, self._lbl_b = self._make_panel(panels, "Baseline")
        self._canvas_c, self._lbl_c = self._make_panel(panels, "Current")
        self._canvas_d, self._lbl_d = self._make_panel(panels, "Diff  (red = changed)")

        # Sidebar: per-test status list (right)
        sidebar = tk.Frame(main, bg="#1e1e1e", width=200)
        sidebar.pack(side="right", fill="y", padx=(PAD * 2, 0))
        sidebar.pack_propagate(False)

        tk.Label(sidebar, text="Tests", font=("Helvetica", 10, "bold"),
                 bg="#1e1e1e", fg="#777777").pack(anchor="w", pady=(4, 4))

        list_wrap = tk.Frame(sidebar, bg="#252525")
        list_wrap.pack(fill="both", expand=True)

        sb = tk.Scrollbar(list_wrap, orient="vertical")
        self._listbox = tk.Listbox(
            list_wrap, yscrollcommand=sb.set,
            bg="#252525", fg="#aaaaaa",
            selectbackground="#252525", selectforeground="#ffffff",
            font=("Courier", 10), relief="flat", borderwidth=0,
            activestyle="none", highlightthickness=0,
        )
        sb.config(command=self._listbox.yview)
        sb.pack(side="right", fill="y")
        self._listbox.pack(side="left", fill="both", expand=True)
        self._listbox.bind("<<ListboxSelect>>", self._on_list_select)

        # Pixel-diff label
        self._lbl_pct = tk.Label(
            self, text="", font=("Helvetica", 11),
            bg="#1e1e1e", fg="#ccaa44"
        )
        self._lbl_pct.pack(pady=(0, PAD))

        # Navigation + decision buttons
        btn_frame = tk.Frame(self, bg="#1e1e1e")
        btn_frame.pack(pady=PAD)

        btn_cfg = dict(font=("Helvetica", 11), relief="flat", padx=14, pady=6, cursor="hand2")

        tk.Button(btn_frame, text="◀  Prev", command=self._prev,
                  bg="#333", fg="#ccc", **btn_cfg).pack(side="left", padx=4)

        tk.Button(btn_frame, text="✓  Approve (update baseline)", command=self._approve,
                  bg="#2a5e2a", fg="#aaffaa", **btn_cfg).pack(side="left", padx=4)

        tk.Button(btn_frame, text="✗  Mark broken", command=self._broken,
                  bg="#5e2a2a", fg="#ffaaaa", **btn_cfg).pack(side="left", padx=4)

        tk.Button(btn_frame, text="Next  ▶", command=self._next,
                  bg="#333", fg="#ccc", **btn_cfg).pack(side="left", padx=4)

        # Summary bar at bottom
        self._lbl_summary = tk.Label(
            self, text="", font=("Helvetica", 10),
            bg="#111", fg="#999", anchor="w"
        )
        self._lbl_summary.pack(fill="x", padx=PAD, pady=(PAD, 0))

        self.bind("<Left>",  lambda _: self._prev())
        self.bind("<Right>", lambda _: self._next())
        self.bind("<a>",     lambda _: self._approve())
        self.bind("<b>",     lambda _: self._broken())

    def _make_panel(self, parent, title):
        frame = tk.Frame(parent, bg="#1e1e1e")
        frame.pack(side="left", padx=PAD, pady=PAD, fill="both", expand=True)

        lbl = tk.Label(frame, text=title, font=("Helvetica", 10),
                       bg="#1e1e1e", fg="#888888")
        lbl.pack()

        canvas = tk.Canvas(
            frame, width=THUMB_W, height=THUMB_H,
            bg="#2a2a2a", highlightthickness=1, highlightbackground="#444"
        )
        canvas.pack()
        return canvas, lbl

    # ── navigation ────────────────────────────────────────────────────────────

    def _show(self, idx: int):
        if not self.names:
            return
        self.idx = idx % len(self.names)
        name     = self.names[self.idx]
        r        = self.results[name]

        self._lbl_title.configure(text=name)
        self._lbl_counter.configure(
            text=f"{self.idx + 1} / {len(self.names)}"
        )
        clipboard_diff = r.get("clipboard_diff")
        pct_text = f"{r['pct']:.1f}% pixels changed  [{r['status']}]"
        if clipboard_diff:
            pct_text += f"  |  {clipboard_diff}"
        self._lbl_pct.configure(text=pct_text)

        self._load_panel(self._canvas_b, r.get("baseline"))
        self._load_panel(self._canvas_c, r.get("run"))
        if r.get("diff"):
            self._lbl_d.configure(text="Diff  (red = changed)")
            self._load_panel(self._canvas_d, r["diff"])
        elif clipboard_diff:
            self._lbl_d.configure(text="Clipboard diff")
            self._show_text_panel(self._canvas_d, clipboard_diff)
        else:
            self._lbl_d.configure(text="Diff  (red = changed)")
            self._load_panel(self._canvas_d, None)

        n_ok     = sum(1 for d in self.decisions.values() if d == "ok")
        n_broken = sum(1 for d in self.decisions.values() if d == "broken")
        n_left   = len(self.names) - len(self.decisions)
        self._lbl_summary.configure(
            text=f"  {n_ok} approved  {n_broken} broken  {n_left} remaining"
        )
        self._refresh_list()

    def _show_text_panel(self, canvas: tk.Canvas, text: str):
        canvas.delete("all")
        canvas.create_text(
            THUMB_W // 2, THUMB_H // 2, text=text,
            fill="#ffcc44", font=("Helvetica", 11),
            width=THUMB_W - 20, justify="center",
        )

    def _load_panel(self, canvas: tk.Canvas, path: str | None):
        canvas.delete("all")
        if not path or not Path(path).exists():
            canvas.create_text(
                THUMB_W // 2, THUMB_H // 2, text="(none)",
                fill="#555", font=("Helvetica", 12)
            )
            return
        img = Image.open(path).convert("RGB")
        img.thumbnail((THUMB_W, THUMB_H), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        canvas._photo = photo   # keep reference
        x = (THUMB_W - img.width)  // 2
        y = (THUMB_H - img.height) // 2
        canvas.create_image(x, y, anchor="nw", image=photo)

    def _refresh_list(self):
        self._listbox.delete(0, "end")
        for i, name in enumerate(self.names):
            dec = self.decisions.get(name, "")
            if dec == "ok":
                icon, fg, bg = "✓", "#88dd88", "#1e2e1e"
            elif dec == "broken":
                icon, fg, bg = "✗", "#dd8888", "#2e1e1e"
            else:
                icon, fg, bg = "·", "#aaaaaa", "#252525"
            if i == self.idx:
                bg = "#2e2e4a"
            self._listbox.insert("end", f" {icon} {name}")
            self._listbox.itemconfigure(i, fg=fg, bg=bg)
        self._listbox.see(self.idx)

    def _on_list_select(self, event):
        sel = self._listbox.curselection()
        if sel and sel[0] != self.idx:
            self._show(sel[0])

    def _prev(self):
        self._show(self.idx - 1)

    def _next(self):
        self._show(self.idx + 1)

    # ── decisions ─────────────────────────────────────────────────────────────

    def _approve(self):
        name = self.names[self.idx]
        r    = self.results[name]
        # Copy current screenshot over baseline
        if r.get("run") and Path(r["run"]).exists():
            self.baseline_dir.mkdir(parents=True, exist_ok=True)
            dest = self.baseline_dir / Path(r["run"]).name
            shutil.copy2(r["run"], dest)
            print(f"  baseline updated: {dest.name}")
        self.decisions[name] = "ok"
        self._lbl_title.configure(fg="#aaffaa")
        self.after(300, lambda: self._lbl_title.configure(fg="#e0e0e0"))
        self._next()

    def _broken(self):
        name = self.names[self.idx]
        self.decisions[name] = "broken"
        self._lbl_title.configure(fg="#ffaaaa")
        self.after(300, lambda: self._lbl_title.configure(fg="#e0e0e0"))
        self._next()


def _latest_baseline_dir(tests_dir: Path, test_name: str) -> Path | None:
    approved = tests_dir / "baseline_approved" / test_name
    if approved.exists():
        return approved
    runs_dir = tests_dir / "baseline_runs"
    runs = sorted(runs_dir.glob("*/")) if runs_dir.exists() else []
    return (runs[-1] / test_name) if runs else None


def main(run_dir: Path):
    results_file = run_dir / "results.json"
    if not results_file.exists():
        print(f"No results.json found in {run_dir}")
        sys.exit(1)

    tests_dir    = run_dir.parent.parent
    baseline_dir = _latest_baseline_dir(tests_dir, run_dir.name)
    if not baseline_dir:
        print("No baseline found in baseline_approved/ or baseline_runs/.")
        sys.exit(1)

    # Re-compare against the current baseline so previously approved items
    # don't show up again.
    diff_dir = run_dir.parent / "_diffs" / run_dir.name
    results  = compare_runs(baseline_dir, run_dir, diff_dir)
    results_file.write_text(json.dumps(results, indent=2), encoding="utf-8")

    app = DiffViewer(run_dir, results, baseline_dir)
    app.mainloop()

    broken = [n for n, d in app.decisions.items() if d == "broken"]
    if broken:
        print(f"\nMarked broken: {', '.join(broken)}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        arg = Path(__file__).parent / "test_run"
    else:
        arg = Path(sys.argv[1])
    # If given the run root, open each test subdir that has a results.json
    if not (arg / "results.json").exists():
        subdirs = sorted(d for d in arg.iterdir() if (d / "results.json").exists())
        if not subdirs:
            print(f"No results.json found in {arg}")
            sys.exit(1)
        for d in subdirs:
            main(d)
    else:
        main(arg)
