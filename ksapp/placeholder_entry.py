"""Single source of truth for placeholder ("greytext") behavior on a tk.Entry.

A PlaceholderManager owns the placeholder lifecycle for one Entry:

  - show(text):  display placeholder in grey if the entry is empty; if already
                 active, swap to the new text (so callers do not have to clear
                 a flag manually). Remembers the text so FocusOut can restore.
  - hide():      remove placeholder, restore the normal foreground colour.
  - reset():     forget the remembered placeholder, empty the entry, restore fg.
                 Use this when re-prompting in a new mode.
  - real_text(): the user-typed text, or "" while the placeholder is showing.
  - is_active(): True iff the placeholder is currently being displayed.

The placeholder is cleared ONLY by explicit user action:

  - left-click on the entry (<Button-1>)
  - typing a printable key
  - the manager's clear_for_user_focus() hook, called by the picker's Tab
    handlers when keyboard navigation lands on the entry

It is NOT cleared by:

  - the picker's initial focus_set() in _run() (we never bind <FocusIn>)
  - window-manager refocus (same reason)

When the entry loses focus while empty, the placeholder is restored.
While the placeholder is active, BackSpace and Delete are suppressed so the
user cannot partially delete the greytext.

Theme toggles: call restore_color() after switching themes so the placeholder
adopts the new foreground.
"""

import tkinter as tk


DEFAULT_FILTER_PROMPT = "start typing to filter"


class PlaceholderManager:
    PH_COLOR = "#aaaaaa"

    def __init__(self, entry, var, fg_color, ph_color=PH_COLOR):
        assert isinstance(entry, tk.Entry), "PlaceholderManager requires a tk.Entry"
        assert isinstance(var, tk.StringVar), "PlaceholderManager requires a tk.StringVar"
        self._entry = entry
        self._var = var
        self._fg = fg_color
        self._ph_color = ph_color
        self._active = False
        self._text = None

        entry.bind("<Button-1>",        lambda _e: self.hide(), add="+")
        entry.bind("<Key>",             self._on_key,           add="+")
        entry.bind("<FocusOut>",        self._on_focus_out,     add="+")
        entry.bind("<<Paste>>",         lambda _e: self.hide(), add="+")
        entry.bind("<<PasteSelection>>",lambda _e: self.hide(), add="+")

    def show(self, text=None):
        """Show the placeholder. If `text` is given, remember it as the
        restore-on-FocusOut text. If the placeholder is already active, swap
        to the new text in-place. If the user has typed real text, no-op."""
        if text is None:
            text = self._text if self._text is not None else DEFAULT_FILTER_PROMPT
        self._text = text
        if self._active:
            self._var.set(text)
            self._entry.config(fg=self._ph_color)
            self._entry.icursor(0)
            return
        if self._var.get():
            return
        self._active = True
        self._var.set(text)
        self._entry.config(fg=self._ph_color)
        self._entry.icursor(0)

    def hide(self):
        if not self._active:
            return
        self._var.set("")
        self._active = False
        self._entry.config(fg=self._fg)

    def clear_for_user_focus(self):
        """Call when keyboard navigation (Tab / Shift-Tab) lands on the entry.
        The Button-1 binding handles mouse focus; this handles keyboard focus
        because we deliberately don't bind <FocusIn> (which would also fire on
        WM refocus and initial focus_set)."""
        self.hide()

    def is_active(self):
        return self._active

    def real_text(self):
        return "" if self._active else self._var.get()

    def update_fg_color(self, fg_color):
        """Theme-toggle helper: change the normal foreground colour."""
        self._fg = fg_color
        if not self._active:
            self._entry.config(fg=fg_color)

    def restore_color(self):
        """Re-apply the placeholder colour after a theme toggle."""
        if self._active:
            self._entry.config(fg=self._ph_color)

    def reset(self):
        """Forget the remembered placeholder text, clear the entry, and
        restore the normal foreground colour. Call this when starting a new
        prompt that does not (yet) have a placeholder."""
        self._active = False
        self._text = None
        self._entry.config(fg=self._fg)
        self._var.set("")

    def _on_key(self, e):
        if not self._active:
            return
        if e.keysym in ("BackSpace", "Delete"):
            return "break"
        if e.keysym in ("Left", "Right"):
            self.hide()
            return
        if e.char and e.char.isprintable():
            self.hide()

    def _on_focus_out(self, _e=None):
        if self._active or self._text is None:
            return
        if self._var.get():
            return
        self._active = True
        self._var.set(self._text)
        self._entry.config(fg=self._ph_color)
        self._entry.icursor(0)
