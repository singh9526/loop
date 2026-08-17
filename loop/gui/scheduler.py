"""The poll loop that replaces the daemon.

Same sequence `sched/tick.py` ran, minus the detached process: ask core
what is due, draw it, write the answer down. The staleness re-check now
happens inside `commands.record_checkin`, under the lock, instead of in a
separate read.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from loop.app import checkin as checkin_module
from loop.app import commands
from loop.core import events, schedule
from loop.store import jsonl
from loop.store.lock import LockTimeout

POLL_MS = 10_000

# A `LockTimeout` out of `record_checkin` means an already-collected,
# still-valid answer failed to write because something else held the lock
# — not that the answer stopped being true. (That case is `record_checkin`
# *returning* `False`, handled inline in `_write_answer` below; it never
# raises, and needs no retry.) Retrying costs nothing but a moment, so it
# is retried a bounded number of times, rescheduled through
# `QTimer.singleShot` rather than a blocking loop: a contended lock delays
# the write and lets the event loop keep running in between attempts,
# instead of stacking several blocking lock-waits back to back with
# nothing processed in between.
RECORD_RETRY_MS = 2000
RECORD_MAX_ATTEMPTS = 3


class Scheduler(QObject):
    """`report` mirrors `Actions.report` (`kind`, `text`): today it only
    ever fires `"error"`, and only when an answered check-in could not be
    written after every retry — the one outcome a user must be told
    about, since the interaction they just completed would otherwise
    vanish with nothing on screen to say so."""

    report = Signal(str, str)

    def __init__(self, controller, writer, blocker, poll_ms: int = POLL_MS,
                 now=time.time) -> None:
        super().__init__()
        self._controller = controller
        self._writer = writer
        self._blocker = blocker
        self._now = now
        self._showing = False

        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self.tick)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def tick(self) -> None:
        if self._showing:
            # A check-in blocks for up to five minutes in a nested event
            # loop, and this timer keeps firing underneath it. Two windows
            # for one interval is the bug this prevents.
            return

        try:
            state = events.fold(jsonl.read_all())
        except jsonl.CorruptLogError:
            # The window already says so and has disabled every action.
            # Appending to a log we cannot fold would make it worse.
            return

        due = schedule.next_due(state, self._now())
        if due is None:
            return

        loop = state.loops[due.loop_id]
        prompt = checkin_module.build_prompt(state, loop, due)

        if due.kind != "ping":
            try:
                commands.record_checkpoint_shown(
                    self._writer, loop_id=loop.id, kind=due.kind
                )
            except LockTimeout:
                # `LockTimeout` is not a `LoopError` — it comes straight out
                # of `Writer.mutate` when another process (still, until
                # Task 15, possibly the daemon) held the write lock past
                # its timeout. Skip the whole tick, not just this write:
                # nothing has been shown yet, so there is no answer to
                # lose, and showing the window now would let a user answer
                # a checkpoint the log never recorded as shown, breaking
                # the shown-before-answered invariant `record_checkin` and
                # the forensics tests both depend on. The next poll tries
                # again from a clean read.
                return

        self._showing = True
        try:
            answers = self._blocker.ask(prompt)
        finally:
            self._showing = False

        self._write_answer(due, answers)

    def _write_answer(self, due: schedule.Due, answers, attempt: int = 1) -> None:
        """Write an answer already collected from the user, retrying past
        lock contention rather than dropping it.

        `record_checkin` *returning* `False` (the loop closed, was
        abandoned, or was paused out from under the prompt) is the correct,
        deliberate drop — the answer is genuinely moot, there is nothing to
        retry, and this method does nothing extra for it: it falls through
        the `try` below like any other successful call. `record_checkin`
        *raising* `LockTimeout` is the other case, and the two must not be
        handled by the same branch: the answer here is still valid, so it
        is retried, bounded, off the timer, before ever being given up on.
        """
        try:
            commands.record_checkin(self._writer, due=due, answers=answers)
        except LockTimeout:
            if attempt >= RECORD_MAX_ATTEMPTS:
                # Every retry lost the race for the lock. The answer — and
                # any text the user typed into a cut/extend field — is
                # gone. A `print` here would vanish into a detached
                # process's stdout; `report` is the same signal the
                # window already renders refusals with.
                self.report.emit(
                    "error",
                    f"loop #{due.loop_id}: your answer could not be saved "
                    f"after {RECORD_MAX_ATTEMPTS} tries — the write lock "
                    "stayed busy. nothing was recorded; you may need to "
                    "answer again.",
                )
                self._controller.refresh()
                return
            # Rescheduled rather than retried in place: the event loop
            # gets to run in between attempts (paint, tray, the
            # controller's own poll), instead of this call stacking
            # several blocking lock-waits with nothing processed between
            # them.
            QTimer.singleShot(
                RECORD_RETRY_MS,
                lambda: self._write_answer(due, answers, attempt + 1),
            )
            return

        self._controller.refresh()
