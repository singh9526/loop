"""The poll loop that replaces the daemon.

Same sequence `sched/tick.py` ran, minus the detached process: ask core
what is due, draw it, write the answer down. The staleness re-check now
happens inside `commands.record_checkin`, under the lock, instead of in a
separate read.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer

from loop.app import checkin as checkin_module
from loop.app import commands
from loop.core import events, schedule
from loop.store import jsonl
from loop.store.lock import LockTimeout

POLL_MS = 10_000


class Scheduler(QObject):
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
                # showing the window now would let a user answer a
                # checkpoint the log never recorded as shown, breaking the
                # shown-before-answered invariant `record_checkin` and the
                # forensics tests both depend on. The next poll retries
                # from a clean read.
                return

        self._showing = True
        try:
            answers = self._blocker.ask(prompt)
        finally:
            self._showing = False

        try:
            commands.record_checkin(self._writer, due=due, answers=answers)
        except LockTimeout:
            # The answer is lost — the same outcome `record_checkin`'s own
            # staleness check produces for a loop closed mid-prompt.
            # Losing one answer to contention beats an unhandled exception
            # escaping a QTimer callback, which would silently kill every
            # tick after it for the rest of the process's life.
            pass

        self._controller.refresh()
