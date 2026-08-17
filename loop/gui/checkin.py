"""The check-in, drawn by Qt.

One window for macOS and Windows, replacing `blockers/macos.py` and
`blockers/tk.py`. It draws `PromptSession` — the interaction state machine
in `blockers/session.py`, already shared and already tested — and decides
nothing on its own. Which question comes next is that class's business;
everything here is layout, keystrokes, and the clock.

What it cannot do: `macos.py` used CGShieldingWindowLevel and
DisableProcessSwitching, which stopped cmd-tab itself. Qt has no
equivalent on either platform. This window is frameless, always on top,
covers every screen, re-raises itself once a second, and has no close
button and no Escape binding — insistent, not inescapable. That trade was
made deliberately when the toolkit was chosen: one implementation for both
platforms was worth more than an unbypassable block on one.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QEventLoop, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from loop.blockers.base import Answers, Prompt, TextField, format_countdown
from loop.blockers.session import PromptSession
from loop.gui import theme

RAISE_MS = 1000
TICK_MS = 250

PICK_LEAD = "which died?"
SUBMIT_LABEL = "Submit"


class CheckinWindow(QWidget):
    """The overlay. `settled` fires exactly once, from `_settle`."""

    settled = Signal()

    def __init__(self, prompt: Prompt, mode: str = "dark", now=time.time) -> None:
        super().__init__(
            None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self._prompt = prompt
        self._now = now
        self._session = PromptSession(prompt, shown_at=now())
        # `Prompt.timeout_s` defaults to `blockers.base.TIMEOUT_S` (300s) and
        # nothing in `app/checkin.py` overrides it; reading the field rather
        # than the constant keeps a per-prompt deadline possible without a
        # second source of truth.
        self._timeout_s = prompt.timeout_s
        self._answers: Answers | None = None
        self._timers: list[QTimer] = []
        self._field_inputs: dict[str, QLineEdit] = {}
        # Without a focus policy a bare QWidget never receives key events,
        # and the keyboard path — the only one the old overlays had — dies
        # silently.
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet(theme.stylesheet(mode))
        self._build()

    # --- text the tests assert on, and the widgets read ---

    def title_text(self) -> str:
        return self._prompt.title

    def question_text(self) -> str:
        return self._prompt.question

    def warning_text(self) -> str:
        return self._prompt.warning or ""

    def choice_buttons(self) -> list[QPushButton]:
        return self._choice_buttons

    def countdown_text(self, at: float) -> str:
        return format_countdown(self._remaining(at))

    # --- the state machine, delegated ---

    def press(self, key: str) -> None:
        if self._answers is not None:
            return  # already settled; a late keystroke changes nothing
        if self._session.press_key(key):
            self._render_stage()

    def visible_pick_list(self) -> list[str] | None:
        return self._session.visible_pick_list()

    def pending_fields(self) -> list[TextField]:
        return self._session.pending_fields()

    def submit_fields(self, values: dict[str, str]) -> list[str]:
        if self._answers is not None:
            return []
        if not self._session.pending_fields():
            # Nothing is asking for fields. `PromptSession.submit_fields`
            # does not check its own stage, so on a ping window this would
            # otherwise complete the session with a choice of `None` and
            # whatever keys the caller passed. The window's own UI cannot
            # reach that — the Submit button only exists while fields are
            # on screen — but `submit_fields` is public, so the door is
            # shut here rather than in `session.py`, which stays a
            # toolkit-free state machine with its own tests.
            return []
        missing = self._session.submit_fields(values)
        self._show_missing(missing)
        if not missing:
            self._finish()
        return missing

    def is_complete(self) -> bool:
        return self._session.is_complete()

    def answers(self) -> Answers:
        return self._session.answers(self._now())

    def timed_out(self, at: float | None = None) -> Answers:
        return self._session.timed_out(self._now() if at is None else at)

    def result(self) -> Answers | None:
        """The settled outcome, or `None` while the check-in is still open."""
        return self._answers

    # --- running it for real ---

    def ask(self) -> Answers:
        """Block until answered or timed out, then return the outcome."""
        if self._answers is not None:
            return self._answers  # never put a settled check-in on screen
        self._cover_all_screens()
        self.show()
        self._insist()
        self._start_timers()
        try:
            return self._block_until_settled()
        finally:
            self._stop_timers()
            # `hide`, not `close`: `closeEvent` refuses while the check-in
            # is unsettled, and an exception unwinding through here must
            # never leave a display-sized overlay stranded on screen.
            self.hide()

    def _block_until_settled(self) -> Answers:
        """Wait on a nested QEventLoop until `_settle` runs.

        A `while self._answers is None: processEvents()` spin — the shape
        this replaced — busy-waits a core, re-enters the dispatcher while
        the window is tearing down, and leaves the timeout and the answer
        both writing the same slot. A nested QEventLoop is the Qt idiom
        for "stop here, but keep the timers underneath running": the
        scheduler's own QTimer keeps ticking, and this returns on the
        first `settled`.
        """
        if self._answers is not None:
            # Settled before we got here — a keystroke handled while the
            # window was being shown, or a zero-length deadline. Entering
            # the loop now would wait on a `settled` already emitted, and
            # nothing would ever quit it.
            return self._answers

        loop = QEventLoop()
        self.settled.connect(loop.quit)
        try:
            # Nothing between the check above and this call can deliver an
            # event, so `settled` cannot slip past the connection.
            loop.exec()
        finally:
            self.settled.disconnect(loop.quit)

        if self._answers is None:
            # Quit by something other than `settled` — the application
            # shutting down is the only route in. Record it the way the tk
            # overlay did: unanswered, never a `None` the caller's type
            # does not allow.
            self._settle(self._session.timed_out(self._now()))
        return self._answers

    def keyPressEvent(self, event) -> None:
        """No Escape, no Return-to-dismiss. The only way out is an answer
        or the timeout."""
        if event.key() == Qt.Key_Escape:
            event.accept()
            return
        text = event.text().lower()
        if text:
            self.press(text)
        event.accept()

    def closeEvent(self, event) -> None:
        if self._answers is None:
            event.ignore()
            return
        event.accept()

    # --- the one place an outcome is decided ---

    def _settle(self, answers: Answers) -> None:
        """The only writer of `_answers` and the only emitter of `settled`.

        First caller wins; every later call is a no-op. That is what makes
        "the user answered" and "the clock ran out" mutually exclusive
        instead of a race: whichever reaches here first *is* the outcome,
        and the loser is inert even when it fires a moment later while the
        event loop unwinds. Timers stop here rather than after `exec()`
        returns, so nothing queued can run during teardown.
        """
        if self._answers is not None:
            return
        self._answers = answers
        self._stop_timers()
        self.settled.emit()

    def _finish(self) -> None:
        self._settle(self._session.answers(self._now()))

    def _expire(self) -> None:
        self._settle(self._session.timed_out(self._now()))

    # --- internals ---

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(72, 56, 72, 40)
        layout.setSpacing(12)
        layout.addStretch(1)

        self._title = QLabel(self._prompt.title)
        self._title.setObjectName("overlay_title")
        layout.addWidget(self._title)

        # `theme.py` styles `QFrame#banner`, not `QLabel#banner` — the
        # warning has to be the frame or it renders unstyled.
        self._banner: QFrame | None = None
        if self._prompt.warning:
            self._banner = QFrame()
            self._banner.setObjectName("banner")
            banner_layout = QVBoxLayout(self._banner)
            warning = QLabel(self._prompt.warning)
            warning.setObjectName("overlay_warning")
            banner_layout.addWidget(warning)
            layout.addWidget(self._banner)

        self._question = QLabel(self._prompt.question)
        self._question.setObjectName("overlay_question")
        self._question.setWordWrap(True)
        layout.addWidget(self._question)

        self._choice_buttons: list[QPushButton] = []
        choices = QHBoxLayout()
        for choice in self._prompt.choices:
            # The keystroke is drawn beside the button, not folded into its
            # text: `choice.label` is a product string and the button must
            # read back exactly that.
            hint = QLabel(f"[{choice.key}]")
            hint.setObjectName("overlay_caption")
            button = QPushButton(choice.label)
            button.setObjectName("overlay_choice")
            # The window owns the keyboard; a focusable button would eat
            # space and return before `keyPressEvent` ever saw them.
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(
                lambda _checked=False, key=choice.key: self.press(key)
            )
            choices.addWidget(hint)
            choices.addWidget(button)
            self._choice_buttons.append(button)
        choices.addStretch(1)
        layout.addLayout(choices)

        self._pick_area = QWidget()
        self._pick_layout = QVBoxLayout(self._pick_area)
        self._pick_area.setVisible(False)
        layout.addWidget(self._pick_area)

        self._field_area = QWidget()
        self._field_layout = QVBoxLayout(self._field_area)
        self._field_area.setVisible(False)
        layout.addWidget(self._field_area)

        self._missing = QLabel()
        self._missing.setObjectName("overlay_missing")
        self._missing.setVisible(False)
        layout.addWidget(self._missing)

        layout.addStretch(1)
        self._countdown = QLabel()
        self._countdown.setObjectName("overlay_countdown")
        layout.addWidget(self._countdown)

    def _render_stage(self) -> None:
        self._show_missing([])
        picks = self._session.visible_pick_list()
        self._pick_area.setVisible(picks is not None)
        _clear(self._pick_layout)
        if picks is not None:
            lead = QLabel(PICK_LEAD)
            lead.setObjectName("overlay_caption")
            self._pick_layout.addWidget(lead)
            for index, text in enumerate(picks, start=1):
                button = QPushButton(f"{index}.  {text}")
                button.setObjectName("overlay_pick")
                button.setFocusPolicy(Qt.NoFocus)
                button.clicked.connect(
                    lambda _checked=False, key=str(index): self.press(key)
                )
                self._pick_layout.addWidget(button)

        fields = self._session.pending_fields()
        self._field_area.setVisible(bool(fields))
        _clear(self._field_layout)
        self._field_inputs = {}
        if fields:
            for field in fields:
                label = QLabel(field.label)
                label.setObjectName("overlay_caption")
                self._field_layout.addWidget(label)
                edit = QLineEdit()
                edit.setObjectName("overlay_field")
                edit.returnPressed.connect(self._submit_from_inputs)
                self._field_layout.addWidget(edit)
                self._field_inputs[field.name] = edit
            submit = QPushButton(SUBMIT_LABEL)
            submit.setObjectName("overlay_submit")
            submit.clicked.connect(self._submit_from_inputs)
            self._field_layout.addWidget(submit)
            next(iter(self._field_inputs.values())).setFocus()

        if self._session.is_complete():
            self._finish()

    def _submit_from_inputs(self) -> None:
        self.submit_fields({
            name: edit.text() for name, edit in self._field_inputs.items()
        })

    def _show_missing(self, missing: list[str]) -> None:
        """Every attempt re-states the whole set, so a field flagged on the
        first try stops being flagged once it is filled in."""
        self._missing.setText(f"required: {', '.join(missing)}" if missing else "")
        self._missing.setVisible(bool(missing))

    def _remaining(self, at: float) -> float:
        return self._session.shown_at + self._timeout_s - at

    def _start_timers(self) -> None:
        self._stop_timers()
        deadline = QTimer(self)
        deadline.setSingleShot(True)
        deadline.setTimerType(Qt.PreciseTimer)
        deadline.timeout.connect(self._expire)
        # `ceil`, not `int`: truncating rounds the deadline *down*, so the
        # timer can fire up to a millisecond before the check-in has
        # actually run its full window and hand back an `answered_at` that
        # is short of `shown_at + timeout_s`. Rounding up costs at most a
        # millisecond of overrun and can never end the window early.
        deadline.start(max(0, math.ceil(self._remaining(self._now()) * 1000)))

        tick = QTimer(self)
        tick.timeout.connect(self._tick)
        tick.start(TICK_MS)

        insist = QTimer(self)
        insist.timeout.connect(self._insist)
        insist.start(RAISE_MS)

        self._timers = [deadline, tick, insist]
        self._tick()

    def _stop_timers(self) -> None:
        for timer in self._timers:
            timer.stop()

    def _tick(self) -> None:
        """Paints the countdown, and is the authority on the deadline: a
        machine that slept through the single-shot timer still expires
        here, on wall-clock time. Both routes end in `_settle`, so the
        duplicate check cannot produce a second outcome."""
        at = self._now()
        self._countdown.setText(f"{self.countdown_text(at)} left")
        if self._remaining(at) <= 0:
            self._expire()

    def _insist(self) -> None:
        """Undo a foreground steal. The best Qt can do; see the module
        docstring for what it cannot.

        Focus is only taken when nothing inside the window holds it —
        otherwise this would yank the caret out of a text field once a
        second while the user is typing an answer into it.
        """
        self.raise_()
        self.activateWindow()
        if self.focusWidget() is None:
            self.setFocus()

    def _cover_all_screens(self) -> None:
        geometry = None
        for screen in QGuiApplication.screens():
            geometry = (screen.geometry() if geometry is None
                        else geometry.united(screen.geometry()))
        if geometry is not None:
            self.setGeometry(geometry)


class QtBlocker:
    """The `Blocker` protocol, drawn by Qt. Must be called from the Qt
    main thread — the scheduler is a QTimer, so it always is."""

    def __init__(self, mode: str = "dark") -> None:
        self._mode = mode

    def ask(self, prompt: Prompt) -> Answers:
        return CheckinWindow(prompt, self._mode).ask()


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
