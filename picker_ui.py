import sys
import os
import re
import math
import threading
import json
import time
import hashlib
from datetime import date, timedelta, datetime as _datetime
import subprocess
import webbrowser
import tkinter as tk
from tkinter import ttk

class NiceScrollbar(tk.Canvas):
    """Rounded, theme-aware scrollbar — drop-in for CTkScrollbar."""

    def __init__(self, master, command=None, width=14,
                 fg_color="#e0e0e0", button_color="#888888",
                 button_hover_color="#555555", corner_radius=6,
                 orientation="vertical", **kw):
        super().__init__(master, width=width, bg=fg_color,
                         highlightthickness=0, bd=0, **kw)
        self._cmd      = command
        self._track    = fg_color
        self._btn      = button_color
        self._hover    = button_hover_color
        self._radius   = corner_radius
        self._bar_w    = width
        self._first    = 0.0
        self._last     = 1.0
        self._hovering = False
        self._dragging = False
        self._drag_y   = 0
        self._drag_first = 0.0

        self.bind("<Configure>",      self._draw)
        self.bind("<ButtonPress-1>",  self._on_press)
        self.bind("<B1-Motion>",      self._on_drag)
        self.bind("<ButtonRelease-1>",self._on_release)
        self.bind("<Enter>",          lambda _: self._set_hover(True))
        self.bind("<Leave>",          lambda _: self._set_hover(False))

    def configure(self, **kw):
        if "fg_color"           in kw: self._track = kw.pop("fg_color");            super().configure(bg=self._track)
        if "button_color"       in kw: self._btn   = kw.pop("button_color")
        if "button_hover_color" in kw: self._hover = kw.pop("button_hover_color")
        if "command"            in kw: self._cmd   = kw.pop("command")
        if kw: super().configure(**kw)
        self._draw()

    def set(self, first, last):
        self._first, self._last = float(first), float(last)
        self._draw()

    def _thumb(self):
        h = max(self.winfo_height(), 1)
        y0, y1 = int(self._first * h), int(self._last * h)
        if y1 - y0 < 20:
            mid = (y0 + y1) // 2
            y0, y1 = max(0, mid - 10), min(h, mid + 10)
        return y0, y1

    def _draw(self, *_):
        self.delete("all")
        y0, y1 = self._thumb()
        w  = self._bar_w
        r  = min(self._radius, (y1 - y0) // 2, w // 2)
        c  = self._hover if self._hovering else self._btn
        x0, x1 = 2, w - 2
        self.create_arc(x0,      y0+2,    x0+2*r, y0+2+2*r, start=90,  extent=90,  fill=c, outline=c)
        self.create_arc(x1-2*r,  y0+2,    x1,     y0+2+2*r, start=0,   extent=90,  fill=c, outline=c)
        self.create_arc(x0,      y1-2-2*r,x0+2*r, y1-2,     start=180, extent=90,  fill=c, outline=c)
        self.create_arc(x1-2*r,  y1-2-2*r,x1,     y1-2,     start=270, extent=90,  fill=c, outline=c)
        self.create_rectangle(x0+r, y0+2, x1-r, y1-2, fill=c, outline=c)
        self.create_rectangle(x0,   y0+2+r, x1, y1-2-r, fill=c, outline=c)

    def _set_hover(self, v):
        self._hovering = v
        self._draw()

    def _on_press(self, e):
        y0, y1 = self._thumb()
        if y0 <= e.y <= y1:
            self._dragging   = True
            self._drag_y     = e.y
            self._drag_first = self._first
        elif self._cmd:
            self._cmd("scroll", -1 if e.y < y0 else 1, "pages")

    def _on_drag(self, e):
        if not self._dragging or not self._cmd:
            return
        h = max(self.winfo_height(), 1)
        delta    = (e.y - self._drag_y) / h
        new_first = max(0.0, min(1.0 - (self._last - self._first),
                                 self._drag_first + delta))
        self._cmd("moveto", new_first)

    def _on_release(self, e):
        self._dragging = False

from PIL import Image, ImageDraw, ImageFont as _ImageFont, ImageTk
from picker_utils import (
    _dbg,
    _get_monitors,
    _REPO,
    THUMB_DIR,
    DAEMON_STATUS,
    HEADER_MARKER,
    LOAD_MORE,
    render_emoji_pil,
    _daemon_alive,
    _daemon_ready,
    _cleanup_incomplete_data,
    get_buymeacoffee_url,
)                                                                                                                                                                      


# ── TkPicker ──────────────────────────────────────────────────────────────────

class TkPicker:
    LIGHT = {
        "BG":           "#ffffff",
        "FG":           "#222222",
        "FG_DIM":       "#999999",
        "ENTRY_BG":     "#f5f5f5",
        "ACCENT":       "#6633cc",
        "ROW_COLORS":   ["#f5f5f5", "#ffffff"],
        "SEL_BG":       "#dde0ff",
        "SB_TRACK":     "#e0e0e0",
        "SB_BTN":       "#888888",
        "SB_HOVER":     "#555555",
        "ENTRY_BORDER": "#dddddd",
        "BANNER_FG":    "#a05000",
    }
    DARK = {
        "BG":           "#1c1b2e",
        "FG":           "#e2e0f0",
        "FG_DIM":       "#888899",
        "ENTRY_BG":     "#2a2940",
        "ACCENT":       "#9966ff",
        "ROW_COLORS":   ["#252440", "#1c1b2e"],
        "SEL_BG":       "#3d3560",
        "SB_TRACK":     "#2e2d45",
        "SB_BTN":       "#555566",
        "SB_HOVER":     "#888899",
        "ENTRY_BORDER": "#444455",
        "BANNER_FG":    "#ffaa44",
    }

    THUMB         = 96
    RAINBOW_VIVID = ["#FF0000", "#FF8C00", "#FFD700", "#32CD32", "#1E90FF", "#8B00FF"]
    TITLE_H       = 52
    _VIRT_ROW_H   = 34
    _DM_SIZE      = 26

    def _apply_theme(self, theme):
        self.BG         = theme["BG"]
        self.FG         = theme["FG"]
        self.FG_DIM     = theme["FG_DIM"]
        self.ENTRY_BG   = theme["ENTRY_BG"]
        self.ACCENT     = theme["ACCENT"]
        self.ROW_COLORS = theme["ROW_COLORS"]
        self.SEL_BG     = theme["SEL_BG"]
        self._theme     = theme

    def __init__(self, floating=False, frameless=True, dark=False, on_dark_toggle=None):
        self._dark            = dark
        self._on_dark_toggle  = on_dark_toggle
        self._apply_theme(self.DARK if dark else self.LIGHT)
        root = tk.Tk()
        root.configure(bg=self.BG)
        root.title("Kitchen Search")
        self._frameless = frameless
        self._floating  = self._setup_floating(root, floating=floating, frameless=frameless)
        mouse_x = root.winfo_pointerx()
        mouse_y = root.winfo_pointery()
        mon = None
        if _get_monitors:
            for m in _get_monitors():
                if m.x <= mouse_x < m.x + m.width and m.y <= mouse_y < m.y + m.height:
                    mon = m
                    break
            if mon is None:
                mon = _get_monitors()[0]
            mw, mh, mx, my = mon.width, mon.height, mon.x, mon.y
        else:
            mx, my = 0, 0
            mw = root.winfo_screenwidth()
            mh = root.winfo_screenheight()
        side = min(mw, mh) // 2
        x = mx + (mw - side) // 2
        y = my + (mh - side) // 2
        self._geometry   = f"{side}x{side}+{x}+{y}"
        root.geometry(self._geometry)
        root.withdraw()  # stay hidden until _run() so first paint is fully populated
        self.root       = root
        self._result       = None
        self.result_typed  = False
        self._mode      = "input"
        self._rows      = []
        self._sel       = -1
        self._img_refs        = []
        self._prompt_img_refs = []
        self._options   = []
        self._trace_id      = None
        self._filter_after_id = None
        self._filter_mode = False
        self._on_select  = None
        self._gen_id      = 0  # incremented on each _reset() to detect stale callbacks
        self._active_popup = None
        self._shown             = False
        self._virt_mode         = False
        self._virt_items        = {}   # idx -> {rect, cids, photos}
        self._virt_photo_cache  = {}   # (char, size) -> PhotoImage, lives for session
        self._all_image_children = []  # ("h", frame) | ("d", frame, rd) in insertion order
        self._ph_active = False
        self._build()

    def _build(self):
        root = self.root

        # ── rainbow border + content frame ────────────────────────────────
        self._make_rainbow_border(root)
        cf = self._content_frame

        # ── rainbow title bar ─────────────────────────────────────────────
        self._title_canvas = tk.Canvas(cf, height=self.TITLE_H,
                                       highlightthickness=0, bd=0, bg=self.BG)
        self._title_canvas.pack(fill="x")
        self._title_canvas.bind("<Configure>",
            lambda e: self._draw_title(e.width, e.height))

        # ── top bar ───────────────────────────────────────────────────────
        top = tk.Frame(cf, bg=self.BG)
        top.pack(fill="x", padx=10, pady=(10, 4))

        self._prompt_frame = tk.Frame(top, bg=self.BG)
        self._prompt_frame.pack(fill="x")

        self._entry_var = tk.StringVar()
        self._entry = tk.Entry(top, textvariable=self._entry_var,
                               bg=self.ENTRY_BG, fg=self.FG,
                               insertbackground=self.FG,
                               font=("Helvetica", 13),
                               relief="flat", bd=6,
                               highlightthickness=2,
                               highlightbackground="#dddddd",
                               highlightcolor=self.ACCENT,
                               insertofftime=0 if os.environ.get("KITCHENSEARCH_NO_BLINK") else 300)
        self._entry.pack(fill="x", pady=(4, 0))

        self._entry.bind("<Escape>",       self._cancel)
        self._entry.bind("<Return>",       self._on_return)
        self._entry.bind("<space>",        self._on_space_key)
        self._entry.bind("<Up>",           lambda e: (self._up(),            "break")[1])
        self._entry.bind("<Down>",         lambda e: (self._down(),          "break")[1])
        self._entry.bind("<Home>",         lambda e: (self._home(),          "break")[1])
        self._entry.bind("<End>",          lambda e: (self._end(),           "break")[1])
        self._entry.bind("<Control-Down>",      lambda e: (_dbg("ENTRY Ctrl+Down"), self._next_page(), "break")[2])
        self._entry.bind("<Control-Up>",        lambda e: (_dbg("ENTRY Ctrl+Up"),   self._prev_page(), "break")[2])
        self._entry.bind("<Control-BackSpace>", self._delete_word_before_cursor)
        self._entry.bind("<Control-Delete>",    self._delete_word_after_cursor)
        self._entry.bind("<Next>",         lambda e: (_dbg("ENTRY PageDown"),  self._next_page(), "break")[2])
        self._entry.bind("<Prior>",        lambda e: (_dbg("ENTRY PageUp"),    self._prev_page(), "break")[2])
        self._entry.bind("<Key>",          self._dbg_keypress)
        self._entry.bind("<Key>",          self._on_entry_key_for_ph, add="+")

        # ── progress bar (hidden until explicitly shown) ──────────────────
        self._prog_frame = tk.Frame(cf, bg=self.BG)
        self._prog_var     = tk.DoubleVar(value=0)
        self._prog_lbl_var = tk.StringVar()
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("lgt.Horizontal.TProgressbar",
                        troughcolor="#e8e8e8", background=self.ACCENT,
                        bordercolor=self.BG, lightcolor=self.ACCENT,
                        darkcolor=self.ACCENT)
        self._progbar = ttk.Progressbar(self._prog_frame,
                                        variable=self._prog_var,
                                        style="lgt.Horizontal.TProgressbar",
                                        mode="determinate")
        self._progbar.pack(fill="x", padx=10, pady=(4, 0))
        tk.Label(self._prog_frame, textvariable=self._prog_lbl_var,
                 bg=self.BG, fg=self.FG_DIM,
                 font=("Helvetica", 9), anchor="e", padx=10
                 ).pack(fill="x")

        # ── scrollable list ───────────────────────────────────────────────
        list_outer = tk.Frame(cf, bg=self.BG)
        list_outer.pack(fill="both", expand=True, padx=2)

        self._canvas = tk.Canvas(list_outer, bg=self.BG, highlightthickness=0, bd=0)
        self._sb = NiceScrollbar(list_outer, orientation="vertical",
                                 command=self._canvas.yview,
                                 fg_color="#e0e0e0",
                                 button_color="#888888",
                                 button_hover_color="#555555",
                                 corner_radius=6,
                                 width=14)
        self._canvas.configure(yscrollcommand=self._on_yscroll)
        self._canvas.pack(side="left", fill="both", expand=True)

        self._inner = tk.Frame(self._canvas, bg=self.BG)
        self._win_id = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        def _on_inner_configure(e):
            if self._virt_mode:
                return
            sr = self._canvas.bbox("all")
            _dbg(f"INNER_CONFIGURE scrollregion={sr} inner_h={self._inner.winfo_reqheight()} gen={self._gen_id} nrows={len(self._rows)}")
            self._canvas.configure(scrollregion=sr)
        self._inner.bind("<Configure>", _on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)

        for w in (self._canvas, self._inner):
            w.bind("<MouseWheel>",    self._on_scroll)
            w.bind("<Button-4>",      self._on_scroll)
            w.bind("<Button-5>",      self._on_scroll)
            w.bind("<Control-Down>",  self._next_page)
            w.bind("<Control-Up>",    self._prev_page)
            w.bind("<Next>",          self._next_page)
            w.bind("<Prior>",         self._prev_page)

        self._canvas.bind("<ButtonPress-1>", lambda e: self._dismiss_popup(), add="+")
        root.bind("<FocusOut>", lambda e: self._dismiss_popup(), add="+")

        root.bind("<Escape>",       self._cancel)
        root.bind("<Up>",           self._up)
        root.bind("<Down>",         self._down)
        root.bind("<Home>",         self._home)
        root.bind("<End>",          self._end)
        root.bind("<Return>",       self._on_return)
        root.bind("<Control-Down>", self._next_page)
        root.bind("<Control-Up>",   self._prev_page)
        root.bind("<Next>",         self._next_page)
        root.bind("<Prior>",        self._prev_page)

        self._make_dm_button()

    # ── dark mode button ──────────────────────────────────────────────────────

    def _make_dm_button(self):
        btn = tk.Canvas(self._content_frame,
                        width=self._DM_SIZE, height=self._DM_SIZE,
                        bg=self.BG, highlightthickness=0, bd=0,
                        cursor="hand2", takefocus=True)
        self._dm_btn = btn
        self._dm_draw()
        # starts hidden; pick_with_images(show_dark_btn=True) makes it visible
        btn.bind("<Button-1>", lambda e: self._toggle_dark_mode())
        btn.bind("<Return>",   lambda e: self._toggle_dark_mode())
        btn.bind("<space>",    lambda e: self._toggle_dark_mode())
        btn.bind("<FocusIn>",  lambda e: btn.configure(
            highlightthickness=2, highlightbackground=self.ACCENT))
        btn.bind("<FocusOut>", lambda e: btn.configure(highlightthickness=0))

    def _dm_draw(self):
        btn = self._dm_btn
        btn.delete("all")
        s = self._DM_SIZE
        c = s // 2
        btn.configure(bg=self.BG)
        if self._dark:
            # Sun — gold disc + 8 short rays
            r = s // 4
            btn.create_oval(c - r, c - r, c + r, c + r,
                            fill="#FFD700", outline="")
            for deg in range(0, 360, 45):
                rad = math.radians(deg)
                x1 = c + (r + 2) * math.cos(rad)
                y1 = c + (r + 2) * math.sin(rad)
                x2 = c + (r + 5) * math.cos(rad)
                y2 = c + (r + 5) * math.sin(rad)
                btn.create_line(x1, y1, x2, y2,
                                fill="#FFD700", width=2, capstyle="round")
        else:
            # Moon — crescent via two overlapping circles
            r = s // 2 - 3
            btn.create_oval(c - r, c - r, c + r, c + r,
                            fill="#7755bb", outline="")
            btn.create_oval(c - r + 5, c - r - 2,
                            c + r + 5, c + r - 2,
                            fill=self.BG, outline="")

    @staticmethod
    def _norm_color(color):
        """Normalise to lowercase 6-digit hex; X11 cget returns 12-digit."""
        if isinstance(color, str) and color.startswith("#") and len(color) == 13:
            return "#" + color[1:3] + color[5:7] + color[9:11]
        return color

    def _walk_retheme(self, widget, color_map):
        for attr in ("background", "foreground"):
            try:
                old = self._norm_color(widget.cget(attr))
                new = color_map.get(old)
                if new:
                    widget.configure(**{attr: new})
            except (tk.TclError, ValueError):
                pass
        for child in widget.winfo_children():
            self._walk_retheme(child, color_map)

    def _toggle_dark_mode(self):
        old_theme = self.DARK if self._dark else self.LIGHT
        new_theme = self.LIGHT if self._dark else self.DARK

        # Unified old→new color map (includes ROW_COLORS list entries)
        color_map = {}
        for key, old_val in old_theme.items():
            new_val = new_theme[key]
            if isinstance(old_val, list):
                for o, n in zip(old_val, new_val):
                    color_map[o] = n
            else:
                color_map[old_val] = new_val

        self._dark = not self._dark
        self._apply_theme(new_theme)

        self.root.configure(bg=self.BG)
        self._walk_retheme(self.root, color_map)

        # Entry-specific attrs not caught by the widget walk
        self._entry.configure(
            insertbackground=self.FG,
            highlightbackground=new_theme["ENTRY_BORDER"],
            highlightcolor=self.ACCENT,
        )
        if self._ph_active:
            self._entry.config(fg=self._PH_COLOR)

        # TTK progressbar style
        style = ttk.Style()
        style.configure("lgt.Horizontal.TProgressbar",
                        troughcolor=self.ENTRY_BG, background=self.ACCENT,
                        bordercolor=self.BG, lightcolor=self.ACCENT,
                        darkcolor=self.ACCENT)

        # CTK scrollbar (uses custom attrs, not standard bg/fg)
        self._sb.configure(
            fg_color=new_theme["SB_TRACK"],
            button_color=new_theme["SB_BTN"],
            button_hover_color=new_theme["SB_HOVER"],
        )

        # Virtual canvas items (rectangles and text, not widget children)
        if self._virt_mode:
            for i, item in self._virt_items.items():
                bg = self.SEL_BG if i == self._sel else self._rows[i]["row_bg"]
                self._canvas.itemconfig(item["rect"], fill=bg)
                for cid in item["cids"]:
                    try:
                        self._canvas.itemconfig(cid, fill=self.FG)
                    except tk.TclError:
                        pass
            for i, rd in enumerate(self._rows):
                rd["row_bg"] = self.ROW_COLORS[i % 2]
        else:
            for i, rd in enumerate(self._rows):
                old_bg = rd.get("row_bg")
                new_bg = color_map.get(old_bg) if old_bg else None
                if new_bg:
                    rd["row_bg"] = new_bg
                    for w in rd.get("bg_widgets", rd.get("all_widgets", [])):
                        try:
                            w.configure(bg=new_bg)
                        except tk.TclError:
                            pass
                # Foreground on every Label in the row
                for w in rd.get("all_widgets", []):
                    if isinstance(w, tk.Label):
                        try:
                            old_fg = self._norm_color(w.cget("foreground"))
                            new_fg = color_map.get(old_fg)
                            if new_fg:
                                w.configure(fg=new_fg)
                        except tk.TclError:
                            pass

        # tk.Text tag foreground (not a widget attr, not caught by walk)
        def _fix_text_tags(w):
            for child in w.winfo_children():
                if isinstance(child, tk.Text):
                    child.tag_configure("alt_bold",  foreground=self.FG)
                    child.tag_configure("kw_normal", foreground=self.FG_DIM)
                    child.tag_configure("kw_bold",   foreground=self.FG)
                _fix_text_tags(child)
        _fix_text_tags(self._inner)

        # Redraw title (uses both canvas fill and PIL)
        self._draw_title(
            self._title_canvas.winfo_width(),
            self._title_canvas.winfo_height())

        self._dm_draw()
        self.root.tk.call('raise', self._dm_btn._w)

        if self._on_dark_toggle:
            self._on_dark_toggle(self._dark)

    def _make_rainbow_border(self, root):
        colors = self.RAINBOW_VIVID

        tb = tk.Frame(root, height=2, bd=0, highlightthickness=0)
        tb.pack(fill="x", side="top")
        tb.pack_propagate(False)
        for c in colors:
            tk.Frame(tb, bg=c, bd=0, highlightthickness=0).pack(
                side="left", fill="both", expand=True)

        bb = tk.Frame(root, height=2, bd=0, highlightthickness=0)
        bb.pack(fill="x", side="bottom")
        bb.pack_propagate(False)
        for c in colors:
            tk.Frame(bb, bg=c, bd=0, highlightthickness=0).pack(
                side="left", fill="both", expand=True)

        mid = tk.Frame(root, bd=0, highlightthickness=0, bg=self.BG)
        mid.pack(fill="both", expand=True)

        lb = tk.Frame(mid, width=2, bd=0, highlightthickness=0)
        lb.pack(side="left", fill="y")
        lb.pack_propagate(False)
        for c in colors:
            tk.Frame(lb, bg=c, bd=0, highlightthickness=0).pack(
                side="top", fill="both", expand=True)

        rb = tk.Frame(mid, width=2, bd=0, highlightthickness=0)
        rb.pack(side="right", fill="y")
        rb.pack_propagate(False)
        for c in colors:
            tk.Frame(rb, bg=c, bd=0, highlightthickness=0).pack(
                side="top", fill="both", expand=True)

        self._content_frame = tk.Frame(mid, bg=self.BG, bd=0, highlightthickness=0)
        self._content_frame.pack(fill="both", expand=True)

    def _draw_title(self, W, H):
        c = self._title_canvas
        c.delete("all")
        if W <= 1 or H <= 1:
            return
        colors = self.RAINBOW_VIVID
        n = len(colors)
        stripe_w = max(20, (W + H) // (n * 3))
        i = 0
        x = -H
        while x < W:
            col = colors[i % n]
            x0, x1 = x, x + stripe_w
            c.create_polygon(x0, 0, x1, 0, x1 + H, H, x0 + H, H,
                             fill=col, outline="")
            x += stripe_w
            i += 1
        font_path = _REPO / "data" / "fonts" / "BubblegumSans-Regular.ttf"
        try:
            pil_font = _ImageFont.truetype(str(font_path), 30)
            img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.text((W // 2, H // 2), "Kitchen Search",
                      font=pil_font, fill=(255, 255, 255, 255),
                      stroke_width=3, stroke_fill=(148, 0, 211, 255),
                      anchor="mm")
            photo = ImageTk.PhotoImage(img)
            c.create_image(0, 0, image=photo, anchor="nw")
            c._title_photo = photo
            return
        except Exception:
            pass
        c.create_text(W // 2 + 1, H // 2 + 1,
                      text="Kitchen Search",
                      font=("Helvetica", 18, "bold"),
                      fill="#000000", anchor="center")
        c.create_text(W // 2, H // 2,
                      text="Kitchen Search",
                      font=("Helvetica", 18, "bold"),
                      fill="#ffffff", anchor="center")

    @staticmethod
    def _setup_floating(root, floating=False, frameless=True):
        if floating:
            # Bypasses WM entirely — no title bar on any WM including i3.
            # focus_force + grab_set_global in _run() handle keyboard focus.
            # root.overrideredirect(True)
            root.attributes("-topmost", True)
            try:
                root.wm_attributes("-type", "splash")
            except tk.TclError:
                pass
        
        if frameless and sys.platform.startswith("linux"):
            # WM hint: ask WM to float the window (i3 obeys this).
            # Title bar visibility depends on the WM.
            try:
                root.overrideredirect(True)
                root.update()
                # wid = root.winfo_id()
                # subprocess.run([
                #     'xprop', '-id', str(wid),
                #     '-f', '_MOTIF_WM_HINTS', '32c',
                #     '-set', '_MOTIF_WM_HINTS', '2, 0, 0, 0, 0'
                # ])
                # root.mainloop()
                root.wm_attributes("-type", "splash")
            except tk.TclError:
                pass

        return frameless or floating

    def _run(self):
        # Defer focus/grab so the window is fully mapped before we grab input.
        # With -type splash the WM handles focus; with overrideredirect we need
        # focus_force + grab_set to capture keyboard events.
        if not self._shown:
            self._shown = True
            self.root.deiconify()
            if self._frameless:
                self.root.geometry(self._geometry)
            if os.environ.get("KITCHENSEARCH_NO_GRAB"):
                # Start the test-mode widget-dump server
                try:
                    import sys as _sys
                    _repo = _sys.path[0] if _sys.path else "."
                    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent / "tests"))
                    from widget_dump import start_test_server
                    start_test_server(self.root)
                except Exception:
                    pass
        def _activate():
            self.root.focus_force()
            if self._entry.winfo_ismapped():
                self._entry.focus_set()
            try:
                if not os.environ.get("KITCHENSEARCH_NO_GRAB"):
                    if self._frameless:
                        self.root.grab_set_global()
                    else:
                        self.root.grab_set()
            except tk.TclError:
                pass
        self.root.after(50, _activate)
        self.root.mainloop()
        try:
            self.root.grab_release()
        except tk.TclError:
            pass
        return self._result

    _PH_TEXT  = "start typing to filter"
    _PH_COLOR = "#aaaaaa"

    def _show_ph(self, text=None):
        if self._ph_active:
            return
        self._ph_active = True
        self._entry_var.set(text if text is not None else self._PH_TEXT)
        self._entry.config(fg=self._PH_COLOR)
        self._entry.icursor(0)

    def _hide_ph(self):
        if not self._ph_active:
            return
        self._entry_var.set("")   # trace fires here; _ph_active still True → no-op
        self._ph_active = False
        self._entry.config(fg=self.FG)

    def _real_text(self):
        return "" if self._ph_active else self._entry_var.get()

    def _on_entry_key_for_ph(self, e):
        if self._ph_active:
            if e.char and e.char.isprintable():
                self._hide_ph()
            elif e.keysym in ("BackSpace", "Delete"):
                return "break"

    def _set_prompt(self, text):
        for w in self._prompt_frame.winfo_children():
            w.destroy()
        self._prompt_img_refs = []
        widgets = self._pack_rich_label(
            self._prompt_frame, text, self.BG,
            font=("Helvetica", 11, "bold"), pady=2,
            img_refs=self._prompt_img_refs)
        for w in widgets:
            w.configure(fg=self.ACCENT)

    def _on_space_key(self, e=None):
        if not self._real_text() and self._mode in ("list", "imagelist"):
            self._on_return()
            return "break"

    def _delete_word_after_cursor(self, e):
        idx = self._entry.index("insert")
        text = self._entry.get()
        i = idx
        while i < len(text) and text[i] == " ":
            i += 1
        while i < len(text) and text[i] != " ":
            i += 1
        self._entry.delete(idx, i)
        return "break"

    def _delete_word_before_cursor(self, e):
        idx = self._entry.index("insert")
        text = self._entry.get()
        i = idx
        while i > 0 and text[i - 1] == " ":
            i -= 1
        while i > 0 and text[i - 1] != " ":
            i -= 1
        self._entry.delete(i, idx)
        return "break"

    def _dbg_keypress(self, e):
        _dbg(f"KEY keysym={e.keysym!r} char={e.char!r} mode={self._mode!r} entry={self._entry_var.get()!r} gen={self._gen_id}")

    # ── scrolling ─────────────────────────────────────────────────────────────

    def _on_yscroll(self, first, last):
        _dbg(f"YSCROLL first={first} last={last} mode={self._mode!r} gen={self._gen_id} nrows={len(self._rows)} inner_children={len(self._inner.winfo_children())}")
        if float(first) <= 0.001 and float(last) >= 0.999:
            self._sb.pack_forget()
        else:
            self._sb.pack(side="right", fill="y")
        self._sb.set(first, last)
        if self._virt_mode:
            self._virt_refresh()

    def _on_scroll(self, e):
        going_down = e.num == 5 or (e.num == 0 and (e.delta or 0) < 0)
        if not self._rows:
            # No list rows — pan the canvas directly (story/image view)
            self._canvas.yview_scroll(1 if going_down else -1, "units")
        elif going_down:
            self._down()
        else:
            self._up()
        return "break"

    # ── keyboard ──────────────────────────────────────────────────────────────

    def _dismiss_popup(self):
        if self._active_popup:
            try:
                self._active_popup.unpost()
            except Exception:
                pass
            self._active_popup = None

    def _cancel(self, e=None):
        self._dismiss_popup()
        self._result = None
        if self._mode == "loading":
            _kill_daemon()
        self.root.quit()

    def _on_return(self, e=None):
        if self._mode == "input":
            val = self._entry_var.get().strip()
            self._result = val or None
            self.root.quit()
        elif self._mode == "list":
            if self._sel >= 0 and self._rows:
                self._result = self._rows[self._sel]["label"]
            else:
                self._result = self._real_text().strip() or None
            self.root.quit()
        elif self._mode == "imagelist":
            val = self._real_text().strip()
            if val:
                self._result = val
                self.result_typed = True
            elif self._sel >= 0 and self._rows:
                self._result = self._rows[self._sel]["label"]
                self.result_typed = False
            else:
                self._result = None
                self.result_typed = False
            self.root.quit()
        elif self._mode == "showimage":
            self._result = "copy"
            self.root.quit()

    def _up(self, e=None):
        if self._rows and self._sel > 0:
            self._select(self._sel - 1)

    def _down(self, e=None):
        if self._rows and self._sel < len(self._rows) - 1:
            self._select(self._sel + 1)

    def _home(self, e=None):
        if self._rows:
            self._select(0)

    def _end(self, e=None):
        if self._rows:
            self._select(len(self._rows) - 1)

    def _next_page(self, e=None):
        first, last = self._canvas.yview()
        if last < 1.0:
            self._canvas.yview_scroll(1, "pages")

    def _prev_page(self, e=None):
        first, last = self._canvas.yview()
        if first > 0.0:
            self._canvas.yview_scroll(-1, "pages")

    # ── selection ─────────────────────────────────────────────────────────────

    def _select(self, idx, scroll=True):
        if 0 <= self._sel < len(self._rows):
            self._color_row(self._sel, selected=False)
        self._sel = idx
        if 0 <= idx < len(self._rows):
            self._color_row(idx, selected=True)
            if scroll:
                self._scroll_into_view(idx)

    def _scroll_into_view(self, idx):
        if self._virt_mode:
            total_h = len(self._rows) * self._VIRT_ROW_H
            if total_h <= 0:
                return
            row_y    = idx * self._VIRT_ROW_H
            canvas_h = self._canvas.winfo_height() or 400
            view_top = self._canvas.yview()[0] * total_h
            view_bot = self._canvas.yview()[1] * total_h
            if row_y < view_top:
                self._canvas.yview_moveto(row_y / total_h)
            elif row_y + self._VIRT_ROW_H > view_bot:
                self._canvas.yview_moveto(
                    (row_y + self._VIRT_ROW_H - canvas_h) / total_h)
            self._virt_refresh()
            return
        self.root.update_idletasks()
        row = self._rows[idx]["frame"]
        row_y = row.winfo_y()
        row_h = row.winfo_reqheight()
        canvas_h = self._canvas.winfo_height()
        total_h  = self._inner.winfo_reqheight()
        if total_h <= 0:
            return
        view_top = self._canvas.yview()[0] * total_h
        view_bot = self._canvas.yview()[1] * total_h
        if row_y < view_top:
            self._canvas.yview_moveto(row_y / total_h)
        elif row_y + row_h > view_bot:
            self._canvas.yview_moveto((row_y + row_h - canvas_h) / total_h)

    def _color_row(self, idx, selected):
        if self._virt_mode:
            bg = self.SEL_BG if selected else self._rows[idx]["row_bg"]
            if idx in self._virt_items:
                self._canvas.itemconfig(self._virt_items[idx]["rect"], fill=bg)
            return
        rd = self._rows[idx]
        bg = self.SEL_BG if selected else rd["row_bg"]
        for w in rd.get("bg_widgets", rd["all_widgets"]):
            try:
                w.configure(bg=bg)
            except tk.TclError:
                pass
        if "on_select" in rd:
            rd["on_select"](selected)

    def _click_row(self, idx):
        self._select(idx, scroll=False)
        label = self._rows[idx]["label"]
        if self._mode == "imagelist" and self._on_select is not None and label != LOAD_MORE:
            self._on_select(label)
        else:
            self._result = label
            self.root.quit()

    # ── state management ──────────────────────────────────────────────────────

    def _reset(self):
        self._gen_id += 1
        _dbg(f"RESET gen={self._gen_id} mode={self._mode!r} nrows={len(self._rows)} inner_children={len(self._inner.winfo_children())}")
        if self._trace_id:
            try: self._entry_var.trace_remove("write", self._trace_id)
            except Exception: pass
            self._trace_id = None
        if self._filter_after_id:
            self.root.after_cancel(self._filter_after_id)
            self._filter_after_id = None
        self._virt_mode = False
        self._canvas.itemconfig(self._win_id, height=0)  # restore natural sizing
        self._canvas.delete("vrow")
        self._virt_items.clear()
        for w in self._inner.winfo_children():
            w.destroy()
        _dbg(f"RESET yview_moveto(0) gen={self._gen_id}")
        self._canvas.yview_moveto(0)
        self._rows               = []
        self._img_refs           = []
        self._sel                = -1
        self._filter_mode        = False
        self._all_image_children = []
        self._ph_active = False
        self._entry.config(fg=self.FG)
        self._entry_var.set("")
        self._prog_frame.pack_forget()
        self._progbar.configure(mode="determinate")
        if not self._entry.winfo_ismapped():
            self._entry.pack(fill="x", pady=(4, 0))
        self._dm_btn.place_forget()

    # ── public API ────────────────────────────────────────────────────────────

    def ask(self, prompt):
        """Plain text input. Returns typed text or None."""
        self._reset()
        self._mode = "input"
        self._set_prompt(prompt)
        return self._run()

    def ask_with_loading_bar(self, prompt):
        """
        Input prompt that also shows daemon loading progress below the entry.
        If the daemon finishes loading before the user submits, the bar is hidden.
        If the user submits before the daemon is ready, the entry is disabled and
        the bar keeps updating until the daemon is ready, then the result is returned.
        Returns typed text or None.
        """
        self._reset()
        self._mode = "input"
        self._set_prompt(prompt)

        if _daemon_ready():
            return self._run()

        self._prog_var.set(0)
        self._progbar.configure(maximum=100, mode="determinate")
        self._prog_lbl_var.set("")
        self._prog_frame.pack(fill="x")

        desc_var = tk.StringVar(value="Starting...")
        desc_lbl = tk.Label(
            self._inner, textvariable=desc_var,
            bg=self.BG, fg=self.FG_DIM,
            font=("Helvetica", 10), anchor="w", padx=14, pady=4,
        )
        desc_lbl.pack(fill="x")

        submitted_query = [None]
        daemon_ready    = [False]

        def _submit(e=None):
            submitted_query[0] = self._entry_var.get().strip()
            if daemon_ready[0]:
                self._result = submitted_query[0] or None
                self.root.quit()
            else:
                self._entry.configure(state="disabled")
                self._set_prompt("Waiting for models to load...")

        self._entry.bind("<Return>", _submit)
        self.root.bind("<Return>",   _submit)

        def _poll():
            if _daemon_ready():
                daemon_ready[0] = True
                self._prog_var.set(100)
                desc_var.set("Ready!")
                if submitted_query[0] is not None:
                    self._result = submitted_query[0] or None
                    self.root.after(300, self.root.quit)
                else:
                    self._prog_frame.pack_forget()
                    try:
                        desc_lbl.destroy()
                    except Exception:
                        pass
                return
            if not _daemon_alive():
                desc_var.set("Daemon failed to start.")
                self.root.after(1500, self.root.quit)
                return
            try:
                data = json.loads(DAEMON_STATUS.read_text())
                self._prog_var.set(float(data.get("pct", 0)))
                desc_var.set(data.get("step", "Starting..."))
            except Exception:
                pass
            self.root.after(150, _poll)

        self.root.after(150, _poll)
        result = self._run()

        self._entry.bind("<Return>", self._on_return)
        self.root.bind("<Return>",   self._on_return)
        return result

    def message(self, text):
        """Display a message. Escape dismisses."""
        self._reset()
        self._mode = "message"
        self._set_prompt(text)
        tk.Label(self._inner, text="Press Esc to dismiss",
                 bg=self.BG, fg=self.FG_DIM,
                 font=("Helvetica", 11, "bold"), anchor="w", padx=10, pady=20
                 ).pack(fill="x")
        self._entry.pack_forget()
        return self._run()

    def pick(self, prompt, options, filter=True, initial_sel=0):
        _dbg(f"PICK start prompt={prompt!r} n_options={len(options)}")
        self._reset()
        self._mode        = "list"
        self._options     = list(options)
        self._filter_mode = filter
        self._set_prompt(prompt)
        _dbg(f"PICK: _build_text_rows start n={len(self._options)}")
        self._build_text_rows(self._options)
        _dbg(f"PICK: _build_text_rows done n_rows={len(self._rows)}")
        if self._rows:
            self._select(min(initial_sel, len(self._rows) - 1))
        if filter:
            self._trace_id = self._entry_var.trace_add("write", self._filter_cb)
            self._show_ph()
        _dbg("PICK: _run start")
        result = self._run()
        _dbg(f"PICK: _run done result={result!r}")
        return result

    def _filter_cb(self, *_):
        if self._ph_active:
            return
        if self._filter_after_id:
            self.root.after_cancel(self._filter_after_id)
        fn = self._do_image_filter if self._mode == "imagelist" else self._do_filter
        self._filter_after_id = self.root.after(300, fn)

    def _do_filter(self):
        self._filter_after_id = None
        if self._ph_active:
            return
        q = self._entry_var.get().lower()
        filtered = [o for o in self._options if q in o.lower()] if q else self._options
        prev = self._sel
        self._build_text_rows(filtered)
        if self._rows:
            self._select(min(prev, len(self._rows) - 1) if prev >= 0 else 0)
        if not q and self._filter_mode:
            self._show_ph()

    def _do_image_filter(self):
        self._filter_after_id = None
        if self._ph_active:
            return
        q = self._entry_var.get().lower().strip()
        # Pack_forget all children first, then re-pack in original order so
        # headers and data rows always appear in the right sequence.
        for item in self._all_image_children:
            item[1].pack_forget()
        visible = []
        for item in self._all_image_children:
            if item[0] == "h":
                item[1].pack(fill="x", padx=6, pady=(6, 2))
            else:
                rd = item[2]
                if not q or q in rd["label"].lower():
                    rd["frame"].pack(fill="x", padx=2, pady=1)
                    visible.append(rd)
        self._rows = visible
        self._sel  = -1
        if visible:
            self._select(0)
        if not q:
            self._show_ph()

    def _click_image_row(self, rd):
        try:
            idx = self._rows.index(rd)
        except ValueError:
            return
        self._click_row(idx)

    @staticmethod
    def _is_emoji_char(ch):
        cp = ord(ch)
        return (0x2300 <= cp <= 0x27BF or   # Misc Technical, Symbols, Dingbats
                0x2B00 <= cp <= 0x2BFF or   # Misc Symbols and Arrows
                0x1F000 <= cp <= 0x1FFFF)   # Main emoji block

    def _pack_rich_label(self, parent, text, bg, font=("Helvetica", 12), pady=5, img_refs=None):
        """
        Pack a series of Label widgets into parent for text that may contain
        emoji characters. Emoji are rendered via PIL; plain text uses font.
        Returns a list of all created widgets (for click-binding).
        """
        if img_refs is None:
            img_refs = self._img_refs
        # Segment the string into alternating text/emoji runs
        segments, buf, in_emoji = [], "", False
        for ch in text:
            is_e = self._is_emoji_char(ch)
            if is_e != in_emoji:
                if buf:
                    segments.append(("e" if in_emoji else "t", buf))
                buf, in_emoji = ch, is_e
            else:
                buf += ch
        if buf:
            segments.append(("e" if in_emoji else "t", buf))

        em_size = font[1] + 4
        widgets = []
        for idx, (kind, content) in enumerate(segments):
            is_last = (idx == len(segments) - 1)
            if kind == "e":
                for ch in content:
                    pil_img = render_emoji_pil(ch, size=em_size)
                    if pil_img:
                        from PIL import ImageTk
                        photo = ImageTk.PhotoImage(pil_img)
                        img_refs.append(photo)
                        w = tk.Label(parent, image=photo, bg=bg,
                                     width=em_size + 4, height=em_size + 4)
                        w.pack(side="left", pady=pady)
                    else:
                        w = tk.Label(parent, text=ch, bg=bg, fg=self.FG, font=font)
                        w.pack(side="left", pady=pady)
                    widgets.append(w)
            else:
                w = tk.Label(parent, text=content, bg=bg, fg=self.FG,
                             font=font, anchor="w")
                kw = dict(side="left", pady=pady)
                if is_last:
                    kw.update(fill="x", expand=True)
                w.pack(**kw)
                widgets.append(w)
        return widgets

    def _on_canvas_configure(self, e):
        self._canvas.itemconfig(self._win_id, width=e.width)
        if self._virt_mode:
            # Invalidate all rendered items so they're redrawn at the new width
            for item in self._virt_items.values():
                self._canvas.delete(item["rect"])
                for cid in item["cids"]:
                    self._canvas.delete(cid)
            self._virt_items.clear()
            total_h = max(len(self._rows) * self._VIRT_ROW_H, 1)
            self._canvas.configure(scrollregion=(0, 0, e.width, total_h))
            self._virt_refresh()

    def _build_text_rows(self, opts):
        _dbg(f"BUILD_TEXT_ROWS start n={len(opts)}")
        # Clear previous state
        self._canvas.delete("vrow")
        self._virt_items.clear()
        for w in self._inner.winfo_children():
            w.destroy()
        self._img_refs = []
        self._rows     = []
        self._sel      = -1
        self._virt_mode = True
        self._canvas.itemconfig(self._win_id, height=1)  # hide _inner behind virt items

        for i, label in enumerate(opts):
            self._rows.append({"label": label,
                                "row_bg": self.ROW_COLORS[i % len(self.ROW_COLORS)]})

        self._canvas.yview_moveto(0)
        total_h = max(len(opts) * self._VIRT_ROW_H, 1)
        cw = max(self._canvas.winfo_width(), 100)
        self._canvas.configure(scrollregion=(0, 0, cw, total_h))
        self._virt_refresh()
        _dbg(f"BUILD_TEXT_ROWS done n_rows={len(self._rows)}")

    def _virt_refresh(self):
        if not self._virt_mode or not self._rows:
            return
        canvas   = self._canvas
        n        = len(self._rows)
        total_h  = n * self._VIRT_ROW_H
        cw       = max(canvas.winfo_width(), 100)
        canvas_h = max(canvas.winfo_height(), 100)

        y0_frac = canvas.yview()[0]
        y0_px   = y0_frac * total_h

        BUFFER    = 3
        first_vis = max(0, int(y0_px / self._VIRT_ROW_H) - BUFFER)
        last_vis  = min(n - 1, int((y0_px + canvas_h) / self._VIRT_ROW_H) + BUFFER)

        # Remove items that scrolled outside the buffered window
        to_remove = [i for i in self._virt_items if i < first_vis or i > last_vis]
        for i in to_remove:
            item = self._virt_items.pop(i)
            canvas.delete(item["rect"])
            for cid in item["cids"]:
                canvas.delete(cid)

        # Create rows newly entering the buffered window
        EM = 20
        for i in range(first_vis, last_vis + 1):
            if i in self._virt_items:
                continue
            rd    = self._rows[i]
            bg    = self.SEL_BG if i == self._sel else rd["row_bg"]
            y     = i * self._VIRT_ROW_H
            yc    = y + self._VIRT_ROW_H // 2
            label = rd["label"]

            rect = canvas.create_rectangle(
                2, y, cw - 2, y + self._VIRT_ROW_H - 1,
                fill=bg, outline="", tags="vrow")

            cids   = []
            photos = []  # kept alive via virt_items, not _img_refs
            x = 10

            # Segment label into emoji / plain-text runs
            segs, buf, in_em = [], "", False
            for ch in label:
                is_e = self._is_emoji_char(ch)
                if is_e != in_em:
                    if buf:
                        segs.append(("e" if in_em else "t", buf))
                    buf, in_em = ch, is_e
                else:
                    buf += ch
            if buf:
                segs.append(("e" if in_em else "t", buf))

            for kind, content in segs:
                if kind == "e":
                    for ch in content:
                        pil_img = render_emoji_pil(ch, size=EM)
                        if pil_img:
                            key = (ch, EM)
                            photo = self._virt_photo_cache.get(key)
                            if photo is None:
                                photo = ImageTk.PhotoImage(pil_img)
                                self._virt_photo_cache[key] = photo
                            photos.append(photo)
                            cid = canvas.create_image(
                                x + EM // 2, yc, image=photo,
                                anchor="center", tags="vrow")
                            cids.append(cid)
                            x += EM + 6
                        else:
                            cid = canvas.create_text(
                                x, yc, text=ch, anchor="w",
                                font=("Helvetica", 12), fill=self.FG, tags="vrow")
                            cids.append(cid)
                            x += 18
                else:
                    cid = canvas.create_text(
                        x, yc, text=content, anchor="w",
                        font=("Helvetica", 12, "bold"), fill=self.FG, tags="vrow")
                    cids.append(cid)

            self._virt_items[i] = {"rect": rect, "cids": cids, "photos": photos}

            for cid in [rect] + cids:
                canvas.tag_bind(cid, "<Button-1>",
                                lambda e, i=i: self._click_row(i))

    def _build_settings_rows(self, opts):
        for w in self._inner.winfo_children():
            w.destroy()
        self._rows = []
        self._sel  = -1
        ti = 0  # counter for alternating row colors across only toggle rows
        for i, label in enumerate(opts):
            is_checked   = label.startswith("[x]")
            is_unchecked = label.startswith("[ ]")
            is_toggle    = is_checked or is_unchecked

            if is_toggle:
                rbg = self.ROW_COLORS[ti % len(self.ROW_COLORS)]
                ti += 1
            else:
                rbg = self.BG

            row   = tk.Frame(self._inner, bg=rbg, cursor="hand2")
            extra = {}
            row.pack(fill="x", padx=2, pady=(0, 1) if is_toggle else (6, 2))

            if is_toggle:
                strip_color = self.ACCENT if is_checked else "#cccccc"
                strip = tk.Frame(row, bg=strip_color, width=4, bd=0,
                                 highlightthickness=0)
                strip.pack(side="left", fill="y")

                BOX = 18
                box_cv = tk.Canvas(row, width=BOX, height=BOX, bg=rbg,
                                   highlightthickness=0, bd=0)
                box_cv.pack(side="left", padx=(10, 8), pady=10)
                if is_checked:
                    box_cv.create_rectangle(0, 0, BOX, BOX,
                                            fill=self.ACCENT, outline="")
                    box_cv.create_line(3, 9,  7, 14, fill="white", width=2,
                                       capstyle="round", joinstyle="round")
                    box_cv.create_line(7, 14, 15, 4, fill="white", width=2,
                                       capstyle="round", joinstyle="round")
                else:
                    box_cv.create_rectangle(1, 1, BOX-1, BOX-1,
                                            fill="", outline="#cccccc", width=2)

                display = label[4:]
                fg = self.FG if is_checked else self.FG_DIM
                lbl = tk.Label(row, text=display, bg=rbg, fg=fg,
                               font=("Helvetica", 12), anchor="w")
                lbl.pack(side="left", fill="x", expand=True, pady=10, padx=(0, 12))

                all_widgets = [row, strip, box_cv, lbl]
                bg_widgets  = [row, box_cv, lbl]
            else:
                inner_ws    = self._pack_rich_label(row, label, rbg,
                                                    font=("Helvetica", 12, "bold"), pady=8)
                all_widgets = [row] + inner_ws
                bg_widgets  = all_widgets
                extra       = {}
                if "buy me a coffee" in label:
                    link_url             = get_buymeacoffee_url()
                    link_color           = "#4fa3e8"
                    link_hover           = "#82c4ff"
                    link_font            = ("Helvetica", 12, "bold underline")
                    text_ws              = [w for w in inner_ws
                                            if isinstance(w, tk.Label) and w.cget("text")]
                    for tw in text_ws:
                        tw.configure(fg=link_color, font=link_font)
                    def _enter(e, ws=text_ws, c=link_hover):
                        for tw in ws: tw.configure(fg=c)
                    def _leave(e, ws=text_ws, c=link_color):
                        for tw in ws: tw.configure(fg=c)
                    def _show_link_menu(e, url=link_url):
                        self._dismiss_popup()
                        menu = tk.Menu(self.root, tearoff=0)
                        menu.add_command(label="Open in browser",
                                         command=lambda: webbrowser.open(url))
                        menu.add_command(label="Copy link address",
                                         command=lambda: (
                                             self.root.clipboard_clear(),
                                             self.root.clipboard_append(url)))
                        # Do NOT assign _active_popup here: tk_popup triggers a
                        # FocusOut on root which would call _dismiss_popup() and
                        # instantly unpost the menu. tk.Menu manages its own
                        # lifetime via Tk's internal bind-all ButtonPress handler.
                        try:
                            menu.tk_popup(e.x_root, e.y_root)
                        finally:
                            menu.grab_release()
                    for w in all_widgets:
                        w.bind("<Enter>",    _enter)
                        w.bind("<Leave>",    _leave)
                        w.bind("<Button-3>", _show_link_menu)
                    def _on_select_coffee(sel, ws=text_ws, nc=link_hover, oc=link_color):
                        for tw in ws: tw.configure(fg=nc if sel else oc)
                    bg_widgets = []  # suppress full-row bg highlight; only text color changes
                    extra      = {"on_select": _on_select_coffee}

            self._rows.append({"frame": row, "label": label,
                                "row_bg": rbg, "all_widgets": all_widgets,
                                "bg_widgets": bg_widgets, **extra})
            for w in all_widgets:
                w.bind("<Button-1>",   lambda e, i=i: self._click_row(i))
                w.bind("<MouseWheel>", self._on_scroll)
                w.bind("<Button-4>",   self._on_scroll)
                w.bind("<Button-5>",   self._on_scroll)

    def pick_settings(self, prompt, options, initial_sel=0):
        self._reset()
        self._mode        = "list"
        self._options     = list(options)
        self._filter_mode = False
        self._set_prompt(prompt)
        self._build_settings_rows(self._options)
        if self._rows:
            self._select(min(initial_sel, len(self._rows) - 1))
        return self._run()

    BMC_BANNER_LABEL  = "__BMC_BANNER__"
    BMC_DISMISS_LABEL = "__BMC_DISMISS__"
    BMC_SNOOZE_LABEL  = "__BMC_SNOOZE__"
    BMC_BORDER_COLOR  = "#ff9999"

    def _make_snooze_btn(self, parent, row_bg):
        BLUE       = "#4a90d9"
        BLUE_HOVER = "#2270c0"
        PURPLE     = "#7744cc"
        W, H, R, OW = 92, 28, 8, 2

        cv = tk.Canvas(parent, width=W, height=H,
                       bg=row_bg, highlightthickness=0, bd=0, cursor="hand2")

        def _draw(fill):
            cv.delete("all")
            x1, y1, x2, y2 = OW, OW, W - OW, H - OW
            # Fill rounded rect
            cv.create_arc(x1,      y1,      x1+2*R, y1+2*R, start=90,  extent=90, fill=fill,   outline=fill,   style="pieslice")
            cv.create_arc(x2-2*R,  y1,      x2,     y1+2*R, start=0,   extent=90, fill=fill,   outline=fill,   style="pieslice")
            cv.create_arc(x1,      y2-2*R,  x1+2*R, y2,     start=180, extent=90, fill=fill,   outline=fill,   style="pieslice")
            cv.create_arc(x2-2*R,  y2-2*R,  x2,     y2,     start=270, extent=90, fill=fill,   outline=fill,   style="pieslice")
            cv.create_rectangle(x1+R, y1, x2-R, y2, fill=fill, outline=fill)
            cv.create_rectangle(x1, y1+R, x2, y2-R, fill=fill, outline=fill)
            # Purple outline
            cv.create_arc(x1,      y1,      x1+2*R, y1+2*R, start=90,  extent=90, outline=PURPLE, width=OW, style="arc")
            cv.create_arc(x2-2*R,  y1,      x2,     y1+2*R, start=0,   extent=90, outline=PURPLE, width=OW, style="arc")
            cv.create_arc(x1,      y2-2*R,  x1+2*R, y2,     start=180, extent=90, outline=PURPLE, width=OW, style="arc")
            cv.create_arc(x2-2*R,  y2-2*R,  x2,     y2,     start=270, extent=90, outline=PURPLE, width=OW, style="arc")
            cv.create_line(x1+R, y1, x2-R, y1, fill=PURPLE, width=OW)
            cv.create_line(x2,   y1+R, x2,   y2-R, fill=PURPLE, width=OW)
            cv.create_line(x1+R, y2, x2-R, y2, fill=PURPLE, width=OW)
            cv.create_line(x1,   y1+R, x1,   y2-R, fill=PURPLE, width=OW)
            # White text (always white regardless of theme)
            cv.create_text(W // 2, H // 2, text="💤 snooze",
                           fill="#ffffff", font=("Helvetica", 10, "bold"))

        _draw(BLUE)
        cv.bind("<Enter>", lambda e: _draw(BLUE_HOVER))
        cv.bind("<Leave>", lambda e: _draw(BLUE))
        return cv

    def _append_bmc_banner(self, banner):
        outer = tk.Frame(self._inner, bg=self.BMC_BORDER_COLOR,
                         bd=0, highlightthickness=0)
        outer.pack(side="top", fill="x", padx=4, pady=(8, 4))

        row = tk.Frame(outer, bg=self.BG, cursor="hand2",
                       bd=0, highlightthickness=0)
        row.pack(fill="x", padx=2, pady=2)

        stripe = tk.Frame(row, width=8, bg=self.BG, bd=0, highlightthickness=0)
        stripe.pack(side="left", fill="y")
        stripe.pack_propagate(False)
        for c in self.RAINBOW_VIVID:
            tk.Frame(stripe, bg=c, bd=0, highlightthickness=0).pack(
                side="top", fill="both", expand=True)

        dismiss = tk.Label(row, text="✕  no thanks",
                           bg=self.BG, fg=self.FG_DIM, cursor="hand2",
                           font=("Helvetica", 9), padx=8)
        dismiss.pack(side="right", padx=(4, 8))

        snooze = self._make_snooze_btn(row, self.BG)
        snooze.pack(side="right", padx=(0, 4), pady=4)

        body = tk.Frame(row, bg=self.BG)
        body.pack(side="left", fill="both", expand=True, padx=(10, 4), pady=6)

        if banner.get("headline"):
            tk.Label(body, text=banner["headline"], bg=self.BG,
                     fg=self._theme["BANNER_FG"],
                     font=("Helvetica", 11, "bold"), anchor="w").pack(fill="x")

        img_path = banner.get("image")
        img_lbl  = None
        if img_path:
            try:
                img = Image.open(img_path).convert("RGBA")
                max_w = 200
                if img.width > max_w:
                    img = img.resize((max_w, int(img.height * max_w / img.width)),
                                     Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self._img_refs.append(photo)
                img_lbl = tk.Label(body, image=photo, bg=self.BG, cursor="hand2")
                img_lbl.pack(anchor="w")
            except Exception:
                pass
        if img_lbl is None:
            img_lbl = tk.Label(body, text="❤  Buy me a coffee",
                               bg="#587180", fg="#ffffff", cursor="hand2",
                               font=("Helvetica", 12, "bold"), padx=14, pady=6)
            img_lbl.pack(anchor="w")

        idx = len(self._rows)
        action_widgets = [row, body, img_lbl]
        self._rows.append({"frame": row, "label": self.BMC_BANNER_LABEL,
                            "row_bg": self.BG, "all_widgets": action_widgets})
        url = banner["url"]

        def _show_banner_menu(e):
            self._dismiss_popup()
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label="Open in browser",
                             command=lambda: self._click_row(idx))
            menu.add_command(label="Copy link address",
                             command=lambda: (
                                 self.root.clipboard_clear(),
                                 self.root.clipboard_append(url)))
            try:
                menu.tk_popup(e.x_root, e.y_root)
            finally:
                menu.grab_release()

            # Delay binding dismiss handlers so that any FocusOut/ButtonPress
            # events queued during tk_popup have already fired before we listen.
            def _setup_dismiss():
                bid_root = [None]
                bid_key  = [None]

                def _close(*_):
                    try:
                        menu.unpost()
                    except Exception:
                        pass
                    try:
                        self.root.unbind("<ButtonPress-1>", bid_root[0])
                    except Exception:
                        pass
                    try:
                        self._entry.unbind("<Key>", bid_key[0])
                    except Exception:
                        pass

                bid_root[0] = self.root.bind("<ButtonPress-1>", lambda e: _close(), add="+")
                bid_key[0]  = self._entry.bind("<Key>", lambda e: _close(), add="+")
                menu.bind("<Unmap>", _close)

            self.root.after(50, _setup_dismiss)

        for w in action_widgets:
            w.bind("<Button-1>",   lambda e, i=idx: self._click_row(i))
            w.bind("<Button-3>",   _show_banner_menu)
            w.bind("<MouseWheel>", self._on_scroll)
            w.bind("<Button-4>",   self._on_scroll)
            w.bind("<Button-5>",   self._on_scroll)

        def _on_dismiss(e=None):
            self._result = self.BMC_DISMISS_LABEL
            self.root.quit()
        dismiss.bind("<Button-1>", _on_dismiss)
        dismiss.bind("<Enter>",    lambda e: dismiss.configure(fg=self.FG))
        dismiss.bind("<Leave>",    lambda e: dismiss.configure(fg=self.FG_DIM))

        def _on_snooze(e=None):
            self._result = self.BMC_SNOOZE_LABEL
            self.root.quit()
        snooze.bind("<Button-1>", _on_snooze)

    def pick_with_images(self, prompt, entries, on_url, on_select=None, thumb_size=None, patterns=None, preload=False, placeholder=None, filter=True, banner=None, show_dark_btn=False):
        thumb = thumb_size if thumb_size is not None else self.THUMB
        self._reset()
        if show_dark_btn:
            self._dm_btn.place(relx=1.0, rely=1.0, anchor="se", x=-6, y=-6)
            self.root.tk.call('raise', self._dm_btn._w)
        gen = self._gen_id  # capture generation ID for stale-callback detection
        self._mode = "imagelist"
        self._on_select = on_select
        self._set_prompt(prompt)

        entries   = list(entries)
        _dbg(f"PICK_WITH_IMAGES gen={gen} n_entries={len(entries)} prompt={prompt!r}")

        next_rank       = [0]
        pending         = {}
        banner_appended = [False]
        total_entries   = len(entries)

        def _maybe_append_banner():
            if banner and not banner_appended[0] and next_rank[0] >= total_entries:
                self._append_bmc_banner(banner)
                banner_appended[0] = True

        def _append_header_row(text, color, image_path=None):
            hr = tk.Frame(self._inner, bg=self.BG, bd=0, highlightthickness=0)
            hr.pack(fill="x", padx=6, pady=(6, 2))
            self._all_image_children.append(("h", hr))
            if image_path:
                try:
                    img_size = 30
                    img = Image.open(image_path).convert("RGBA")
                    img = img.resize((img_size, img_size), Image.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                    self._img_refs.append(photo)
                    tk.Label(hr, image=photo, bg=self.BG).pack(side="left", padx=(0, 8))
                except Exception:
                    pass
            lbl = tk.Label(hr, text=text, bg=self.BG, fg=color or self.FG_DIM,
                           font=("Helvetica", 11, "bold"), anchor="w")
            lbl.pack(side="left", fill="x", expand=True)

        def _append_row(label, photo, score=None):
            i = len(self._rows)
            cur_gen = self._gen_id
            if cur_gen != gen:
                _dbg(f"STALE _append_row label={label!r} row_gen={gen} cur_gen={cur_gen} — STALE CALLBACK from old pick_with_images!", include_tb=True)
            else:
                _dbg(f"APPEND_ROW gen={gen} row_idx={i} label={label[:60]!r} inner_children={len(self._inner.winfo_children())}")
            rbg = self.ROW_COLORS[i % len(self.ROW_COLORS)]
            row = tk.Frame(self._inner, bg=rbg, cursor="hand2")
            row.pack(fill="x", padx=2, pady=1)
            img_lbl = tk.Label(row, image=photo, bg=rbg, width=thumb, height=thumb)
            img_lbl.pack(side="left", padx=(6, 10), pady=4)
            self._img_refs.append(photo)

            # Parse "alt  🔮🫐  (keywords...)" into name part and keywords part
            sep_idx = label.find("  (")
            if sep_idx >= 0:
                name_part = label[:sep_idx]
                kw_part   = label[sep_idx + 2:]
            else:
                name_part = label
                kw_part   = ""

            font_main    = ("Helvetica", 11, "bold")
            font_kw      = ("Helvetica", 10)
            font_kw_bold = ("Helvetica", 10, "bold")
            em_size      = 15

            txt = tk.Text(row, height=2, wrap="word",
                          bg=rbg, fg=self.FG,
                          font=font_main,
                          relief="flat", bd=0,
                          highlightthickness=0,
                          spacing1=1, spacing3=1,
                          cursor="hand2")
            txt.tag_configure("alt_bold",  font=font_main,    foreground=self.FG)
            txt.tag_configure("kw_normal", font=font_kw,      foreground=self.FG_DIM)
            txt.tag_configure("kw_bold",   font=font_kw_bold, foreground=self.FG)
            txt.bind("<Key>",     lambda e: "break")
            txt.bind("<Button-2>", lambda e: "break")
            txt.pack(side="left", fill="x", expand=True, padx=(0, 8), pady=0)

            def _auto_height(e, t=txt):
                dlines = t.count("1.0", "end", "displaylines")
                if dlines:
                    new_h = max(1, dlines[0])
                    if int(t.cget("height")) != new_h:
                        t.configure(height=new_h)
            txt.bind("<Configure>", _auto_height)

            # Insert alt name + inline emojis as images
            segs, buf, in_em = [], "", False
            for ch in name_part:
                is_e = self._is_emoji_char(ch)
                if is_e != in_em:
                    if buf:
                        segs.append(("e" if in_em else "t", buf))
                    buf, in_em = ch, is_e
                else:
                    buf += ch
            if buf:
                segs.append(("e" if in_em else "t", buf))

            for kind, content in segs:
                if kind == "e":
                    for ch in content:
                        pil_img = render_emoji_pil(ch, size=em_size)
                        if pil_img:
                            photo_e = ImageTk.PhotoImage(pil_img)
                            self._img_refs.append(photo_e)
                            txt.image_create("end", image=photo_e, pady=1)
                        else:
                            txt.insert("end", ch, "alt_bold")
                else:
                    txt.insert("end", content, "alt_bold")

            # Keywords: bold the exact pattern matches
            if kw_part:
                txt.insert("end", "  ")
                if patterns:
                    kw_lower = kw_part.lower()
                    spans = []
                    for p in patterns:
                        for m in p.finditer(kw_lower):
                            spans.append((m.start(), m.end()))
                    spans.sort()
                    merged = []
                    for s, e in spans:
                        if merged and s <= merged[-1][1]:
                            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
                        else:
                            merged.append([s, e])
                    pos = 0
                    for s, e in merged:
                        if pos < s:
                            txt.insert("end", kw_part[pos:s], "kw_normal")
                        txt.insert("end", kw_part[s:e], "kw_bold")
                        pos = e
                    if pos < len(kw_part):
                        txt.insert("end", kw_part[pos:], "kw_normal")
                else:
                    txt.insert("end", kw_part, "kw_normal")

            all_widgets = [row, img_lbl, txt]
            rd = {"frame": row, "label": label,
                  "row_bg": rbg, "all_widgets": all_widgets}
            self._rows.append(rd)
            self._all_image_children.append(("d", row, rd))
            for w in all_widgets:
                w.bind("<Button-1>",   lambda e, _rd=rd: self._click_image_row(_rd))
                w.bind("<MouseWheel>", self._on_scroll)
                w.bind("<Button-4>",   self._on_scroll)
                w.bind("<Button-5>",   self._on_scroll)
            if len(self._rows) == 1:
                self._select(0)

        def _flush():
            _dbg(f"FLUSH gen={gen} cur_gen={self._gen_id} next_rank={next_rank[0]} pending_keys={sorted(pending.keys())}")
            while next_rank[0] in pending:
                item = pending.pop(next_rank[0])
                next_rank[0] += 1
                if item is None:
                    pass
                elif isinstance(item, tuple) and item[0] == "__HEADER__":
                    _append_header_row(item[1], item[2], item[3] if len(item) > 3 else None)
                else:
                    _append_row(*item)
            _maybe_append_banner()

        def _on_image_ready(rank, label, path, score):
            cur_gen = self._gen_id
            _dbg(f"ON_IMAGE_READY gen={gen} cur_gen={cur_gen} rank={rank} label={label[:50]!r} path_ok={path is not None}")
            if cur_gen != gen:
                _dbg(f"STALE ON_IMAGE_READY rank={rank} label={label[:50]!r} — dropping stale callback gen={gen} cur={cur_gen}")
                return
            photo = None
            if path:
                try:
                    img   = Image.open(path).convert("RGBA")
                    img   = img.resize((thumb, thumb), Image.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                except Exception:
                    pass
            pending[rank] = None if photo is None else (label, photo, score)
            _flush()

        _dbg(f"PICK_WITH_IMAGES: dispatching workers gen={gen} n_entries={len(entries)} preload={preload}")
        for rank, entry in enumerate(entries):
            label      = entry[0]
            url        = entry[1] if len(entry) > 1 else None
            score      = entry[2] if len(entry) > 2 else None
            image_path = entry[3] if len(entry) > 3 else None
            if url == HEADER_MARKER:
                pending[rank] = ("__HEADER__", label, score, image_path)
                _flush()
                continue
            if url is None:
                # LOAD_MORE or similar text-only entry — invisible 1×1 image
                photo = ImageTk.PhotoImage(
                    Image.new("RGBA", (1, 1), (0, 0, 0, 0)))
                self._img_refs.append(photo)
                pending[rank] = (label, photo, None)
                _flush()
                continue

            if preload:
                # Synchronous load: rows fully built before _run(), no empty-then-populate flash
                path = on_url(url)
                _on_image_ready(rank, label, path, score)
            else:
                def _worker(rank=rank, label=label, url=url, score=score):
                    _dbg(f"WORKER start rank={rank} url={url[:60]!r}")
                    path = on_url(url)
                    _dbg(f"WORKER done  rank={rank} path_ok={path is not None}")
                    try:
                        self.root.after(0, lambda: _on_image_ready(rank, label, path, score))
                    except RuntimeError:
                        pass
                threading.Thread(target=_worker, daemon=True).start()

        if preload:
            _maybe_append_banner()
            self.root.update_idletasks()
            self._sb.pack_forget()

        _dbg(f"PICK_WITH_IMAGES: {'preloaded' if preload else 'all workers dispatched'} gen={gen}, calling _run")
        if filter:
            self._trace_id = self._entry_var.trace_add("write", self._filter_cb)
        else:
            def _no_filter_cb(*_):
                if self._ph_active:
                    return
                q = self._entry_var.get().strip()
                if q:
                    if self._sel >= 0:
                        self._color_row(self._sel, selected=False)
                        self._sel = -1
                else:
                    self._show_ph(placeholder)
                    if self._rows:
                        self._select(0)
            self._trace_id = self._entry_var.trace_add("write", _no_filter_cb)
        self._show_ph(placeholder)
        result = self._run()
        _dbg(f"PICK_WITH_IMAGES: _run done gen={gen} result={result!r}")
        return result

    def show_download_progress(self, title, download_fn):
        """
        Run download_fn(progress_cb) in a thread and show a determinate
        progress bar. progress_cb(downloaded_bytes, total_bytes).
        Returns None on success, or an error string.
        """
        self._reset()
        self._mode = "download"
        self._set_prompt(title)
        self._entry.pack_forget()

        self._prog_var.set(0)
        self._progbar.configure(maximum=100, mode="determinate")
        self._prog_lbl_var.set("Starting...")
        self._prog_frame.pack(fill="x")

        result  = [None]
        skipped   = [False]
        stop_evt  = threading.Event()

        def _progress_cb(downloaded, total):
            def _update():
                if skipped[0]:
                    return
                if total:
                    pct = downloaded / total * 100
                    self._prog_var.set(pct)
                    self._prog_lbl_var.set(
                        f"{downloaded/1e6:.1f} MB / {total/1e6:.1f} MB")
                else:
                    self._prog_lbl_var.set(f"{downloaded/1e6:.1f} MB")
            try:
                self.root.after(0, _update)
            except Exception:
                pass

        def _worker():
            err = download_fn(_progress_cb, stop_evt)
            result[0] = err
            def _finish():
                if skipped[0]:
                    return
                if err:
                    self._set_prompt(f"Download failed: {err[:80]}")
                else:
                    self._prog_var.set(100)
                    self._prog_lbl_var.set("Complete")
                self.root.after(600, self.root.quit)
            self.root.after(0, _finish)

        _W, _H, _R = 160, 42, 21

        def _do_skip():
            skipped[0] = True
            stop_evt.set()
            self.root.quit()

        btn_canvas = tk.Canvas(self._inner, width=_W, height=_H,
                               bg=self.BG, highlightthickness=0, bd=0,
                               cursor="hand2")
        btn_canvas.pack(pady=(10, 20))

        def _draw_skip_btn(color):
            btn_canvas.delete("all")
            for x0, y0, x1, y1, s, e in [
                (0, 0, 2*_R, 2*_R, 90, 90),
                (_W-2*_R, 0, _W, 2*_R, 0, 90),
                (0, _H-2*_R, 2*_R, _H, 180, 90),
                (_W-2*_R, _H-2*_R, _W, _H, 270, 90),
            ]:
                btn_canvas.create_arc(x0, y0, x1, y1, start=s, extent=e,
                                      fill=color, outline="")
            btn_canvas.create_rectangle(_R, 0, _W-_R, _H, fill=color, outline="")
            btn_canvas.create_rectangle(0, _R, _W, _H-_R, fill=color, outline="")
            btn_canvas.create_text(_W//2, _H//2, text="Skip for now",
                                   fill="#ffffff", font=("Helvetica", 13, "bold"))

        _HOVER_COLOR  = "#4a1f99"
        _PRESS_COLOR  = "#3a1577"

        _draw_skip_btn(self.ACCENT)
        btn_canvas.bind("<Enter>",          lambda e: _draw_skip_btn(_HOVER_COLOR))
        btn_canvas.bind("<Leave>",          lambda e: _draw_skip_btn(self.ACCENT))
        btn_canvas.bind("<ButtonPress-1>",  lambda e: _draw_skip_btn(_PRESS_COLOR))
        btn_canvas.bind("<ButtonRelease-1>",lambda e: _do_skip())
        self.root.bind("<Escape>", lambda e: _do_skip())

        worker_thread = threading.Thread(target=_worker, daemon=True)
        worker_thread.start()
        self.root.mainloop()
        self.root.bind("<Escape>", self._cancel)

        if skipped[0]:
            worker_thread.join(timeout=2.0)
            _cleanup_incomplete_data()

        return result[0]

    def show_model_loading_progress(self):
        """Poll the daemon status file and show a progress bar until the daemon is ready."""
        self._reset()
        self._mode = "loading"
        self._set_prompt("Loading search models...")
        self._entry.pack_forget()

        self._prog_var.set(0)
        self._progbar.configure(maximum=100, mode="determinate")
        self._prog_lbl_var.set("")
        self._prog_frame.pack(fill="x")

        desc_var = tk.StringVar(value="Starting...")
        tk.Label(
            self._inner, textvariable=desc_var,
            bg=self.BG, fg=self.FG,
            font=("Helvetica", 11), anchor="w", padx=14, pady=14,
        ).pack(fill="x")

        cancelled = [False]

        def _do_cancel():
            cancelled[0] = True
            self._result = None
            _kill_daemon()
            self.root.quit()

        _W, _H, _R = 120, 42, 21
        btn_canvas = tk.Canvas(self._inner, width=_W, height=_H,
                               bg=self.BG, highlightthickness=0, bd=0)
        btn_canvas.pack(pady=(10, 20))

        def _draw_btn(color):
            btn_canvas.delete("all")
            for x0, y0, x1, y1, s, e in [
                (0, 0, 2*_R, 2*_R, 90, 90),
                (_W-2*_R, 0, _W, 2*_R, 0, 90),
                (0, _H-2*_R, 2*_R, _H, 180, 90),
                (_W-2*_R, _H-2*_R, _W, _H, 270, 90),
            ]:
                btn_canvas.create_arc(x0, y0, x1, y1, start=s, extent=e,
                                      fill=color, outline="")
            btn_canvas.create_rectangle(_R, 0, _W-_R, _H, fill=color, outline="")
            btn_canvas.create_rectangle(0, _R, _W, _H-_R, fill=color, outline="")
            btn_canvas.create_text(_W//2, _H//2, text="Cancel",
                                   fill="#ffffff", font=("Helvetica", 13, "bold"))

        _draw_btn(self.ACCENT)
        btn_canvas.bind("<Enter>",    lambda e: _draw_btn("#4a1f99"))
        btn_canvas.bind("<Leave>",    lambda e: _draw_btn(self.ACCENT))
        btn_canvas.bind("<Button-1>", lambda e: _do_cancel())

        def _poll():
            if cancelled[0]:
                return
            if _daemon_ready():
                self._prog_var.set(100)
                desc_var.set("Ready!")
                self.root.after(350, self.root.quit)
                return
            if not _daemon_alive():
                desc_var.set("Daemon failed to start.")
                self._prog_var.set(0)
                self.root.after(1500, self.root.quit)
                return
            try:
                data = json.loads(DAEMON_STATUS.read_text())
                pct  = float(data.get("pct", 0))
                step = data.get("step", "Starting...")
                self._prog_var.set(pct)
                desc_var.set(step)
            except Exception:
                pass
            self.root.after(150, _poll)

        self.root.after(150, _poll)
        self.root.mainloop()
        return not cancelled[0]

    def show_story_progress(self, cmd):
        """Run a story command and show phrase-by-phrase progress. Returns None or error string."""
        self._reset()
        self._mode = "story_progress"
        self._set_prompt("Generating emoji story...")
        self._entry.pack_forget()

        self._prog_var.set(0)
        self._progbar.configure(maximum=100, mode="indeterminate")
        self._progbar.start(12)
        self._prog_lbl_var.set("")
        self._prog_frame.pack(fill="x")

        desc_var = tk.StringVar(value="Searching phrases...")
        tk.Label(
            self._inner, textvariable=desc_var,
            bg=self.BG, fg=self.FG,
            font=("Helvetica", 11), anchor="w", padx=14, pady=14,
        ).pack(fill="x")

        total  = [0]
        done   = [0]
        error  = [None]

        def _update():
            if total[0] > 0:
                self._progbar.stop()
                self._progbar.configure(mode="determinate", maximum=total[0])
                self._prog_var.set(done[0])
                desc_var.set(f"Fetched {done[0]} / {total[0]} emojis")

        from picker_utils import CACHE_DIR as _CACHE_DIR
        log_path = _CACHE_DIR / "story.log"

        def _worker():
            captured = []
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                logf = open(log_path, "w")
                logf.write("$ " + " ".join(cmd) + "\n")
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in proc.stdout:
                    logf.write(line)
                    logf.flush()
                    captured.append(line)
                    m = re.match(r'(\d+) phrases', line)
                    if m:
                        total[0] = int(m.group(1))
                        self.root.after(0, _update)
                    elif line.startswith("  "):
                        done[0] += 1
                        self.root.after(0, _update)
                proc.wait()
                logf.write(f"\n[exit {proc.returncode}]\n")
                logf.close()
                if proc.returncode != 0:
                    tail = "".join(captured[-15:]).rstrip()
                    error[0] = f"Story generation failed (exit {proc.returncode}). Log: {log_path}\n\n{tail}"
            except Exception as exc:
                error[0] = f"{exc}\nLog: {log_path}"
            self.root.after(0, self.root.quit)

        threading.Thread(target=_worker, daemon=True).start()
        self.root.mainloop()
        self._progbar.stop()
        return error[0]

    def run_blocking(self, title, fn):
        """
        Run fn() in a background thread, show an indeterminate spinner.
        Returns fn's return value, or raises its exception.
        """
        self._reset()
        self._mode = "spinner"
        self._set_prompt(title)
        self._entry.pack_forget()

        self._progbar.configure(mode="indeterminate")
        self._prog_frame.pack(fill="x")
        self._progbar.start(12)

        result = [None]
        error  = [None]

        def _worker():
            try:
                result[0] = fn()
            except Exception as exc:
                error[0] = exc
            self.root.after(0, self.root.quit)

        threading.Thread(target=_worker, daemon=True).start()
        self.root.mainloop()
        self._progbar.stop()

        if error[0]:
            raise error[0]
        return result[0]

    def show_image(self, prompt, image_path):
        """
        Display an image full-size in the content area.
        Enter → returns 'copy'.  Esc → returns None.
        """
        self._reset()
        self._mode = "showimage"
        self._set_prompt(prompt)
        self._entry.pack_forget()

        self.root.update_idletasks()
        avail_w = max(self.root.winfo_width() - 20, 100)
        img = Image.open(image_path)
        if img.width > avail_w:
            img = img.resize(
                (avail_w, int(img.height * avail_w / img.width)),
                Image.LANCZOS,
            )
        photo = ImageTk.PhotoImage(img)
        self._img_refs.append(photo)
        img_label = tk.Label(self._inner, image=photo, bg=self.BG, cursor="hand2")
        img_label.pack(pady=(10, 6))
        img_label.bind("<Button-1>", self._on_return)

        tk.Label(self._inner,
                 text="Enter to copy  |  Esc to cancel",
                 bg=self.BG, fg=self.FG_DIM,
                 font=("Helvetica", 10), anchor="center"
                 ).pack()

        self.root.update_idletasks()
        return self._run()

    def destroy(self):
        try: self.root.destroy()
        except Exception: pass


# ── helpers that need picker ───────────────────────────────────────────────────

def pick_base_emoji(base_index, prompt, picker):
    labels = [f"{emoji} {name}" for _, (emoji, name) in base_index]
    selected = picker.pick(prompt, labels)
    if not selected:
        return None
    for _hex, (emoji, name) in base_index:
        if selected == f"{emoji} {name}":
            return (emoji, name)
    return ("", selected)

