"""The portable overlay. Weaker than the macOS block — Alt-Tab escapes it.

Ships on macOS, Windows, and Linux. Draws whatever stage the session
reports and forwards keystrokes; it makes no decisions of its own.
"""

from __future__ import annotations

import time
import tkinter as tk

from loop.blockers.base import Answers, Prompt, format_countdown, remaining_fraction
from loop.blockers.session import FIELDS, PICK, PromptSession

BG = "#101014"
FG = "#e6e6e6"
DIM = "#8a8a94"
WARN = "#ff5f56"
BAR_BG = "#2a2a33"
BAR_FG = "#7aa2f7"


class TkBlocker:
    def ask(self, prompt: Prompt) -> Answers:
        return _Overlay(prompt).run()


class _Overlay:
    def __init__(self, prompt: Prompt) -> None:
        self.prompt = prompt
        self.session = PromptSession(prompt, shown_at=time.time())
        self.result: Answers | None = None
        self.entries: dict[str, tk.Entry] = {}

        self.root = tk.Tk()
        self.root.configure(bg=BG)
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

        self.body = tk.Frame(self.root, bg=BG)
        self.body.place(relx=0.5, rely=0.45, anchor="center")

        self.bar_canvas = tk.Canvas(
            self.root, height=10, bg=BAR_BG, highlightthickness=0
        )
        self.bar_canvas.place(relx=0.5, rely=0.86, anchor="center", relwidth=0.5)
        self.countdown = tk.Label(self.root, bg=BG, fg=DIM, font=("Menlo", 16))
        self.countdown.place(relx=0.5, rely=0.90, anchor="center")

        self.root.bind("<Key>", self._on_key)
        self.root.after(100, self._grab)
        self.root.after(0, self._tick)

    def run(self) -> Answers:
        self._render()
        self.root.mainloop()
        if self.result is None:  # mainloop ended without a decision
            self.result = self.session.timed_out(at=time.time())
        return self.result

    def _grab(self) -> None:
        self.root.focus_force()
        try:
            self.root.grab_set_global()
        except tk.TclError:
            pass  # not honoured on this platform; the overlay is still on top

    def _tick(self) -> None:
        remaining = self.prompt.timeout_s - (time.time() - self.session.shown_at)
        if remaining <= 0:
            self.result = self.session.timed_out(at=time.time())
            self.root.destroy()
            return

        self.countdown.configure(text=f"{format_countdown(remaining)} left")
        self.bar_canvas.delete("all")
        width = max(1, self.bar_canvas.winfo_width())
        filled = int(width * remaining_fraction(remaining, self.prompt.timeout_s))
        self.bar_canvas.create_rectangle(0, 0, filled, 10, fill=BAR_FG, width=0)
        self.root.after(200, self._tick)

    def _on_key(self, event: "tk.Event") -> None:
        if self.session.stage == FIELDS:
            if event.keysym == "Return":
                self._submit_fields()
            return
        if self.session.press_key(event.char.lower()):
            self._render()
            self._finish_if_done()

    def _submit_fields(self) -> None:
        values = {name: entry.get() for name, entry in self.entries.items()}
        missing = self.session.submit_fields(values)
        if missing:
            for name in missing:
                self.entries[name].configure(highlightbackground=WARN, highlightthickness=2)
            return
        self._finish_if_done()

    def _finish_if_done(self) -> None:
        if self.session.is_complete():
            self.result = self.session.answers(answered_at=time.time())
            self.root.destroy()

    def _render(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        self.entries.clear()

        self._label(self.prompt.title, size=18, colour=DIM)
        self._label("", size=8)
        self._label(self.prompt.question, size=32)
        self._label("", size=8)
        self._label(
            "   ".join(f"[{c.key}] {c.label}" for c in self.prompt.choices),
            size=22, colour=BAR_FG,
        )

        if self.session.stage == PICK:
            self._label("", size=8)
            self._label("which died?", size=18, colour=DIM)
            for index, text in enumerate(self.session.visible_pick_list() or [], start=1):
                self._label(f"{index}  {text}", size=20)

        if self.session.stage == FIELDS:
            for field in self.session.pending_fields():
                self._label("", size=6)
                self._label(field.label, size=18, colour=DIM)
                entry = tk.Entry(
                    self.body, font=("Menlo", 20), width=40,
                    bg=BAR_BG, fg=FG, insertbackground=FG, relief="flat",
                )
                entry.pack(pady=4)
                self.entries[field.name] = entry
            self._label("", size=6)
            self._label("press return to submit", size=14, colour=DIM)
            next(iter(self.entries.values())).focus_set()

        if self.prompt.warning:
            self._label("", size=12)
            self._label(self.prompt.warning, size=18, colour=WARN)

    def _label(self, text: str, *, size: int, colour: str = FG) -> None:
        tk.Label(
            self.body, text=text, bg=BG, fg=colour,
            font=("Menlo", size), justify="left",
        ).pack(anchor="w")
