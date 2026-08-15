"""The read side: poll the log, build the view, tell the window.

Polling rather than watching the filesystem, because a 1 Hz stat of one
file costs nothing and a file watcher is a per-platform dependency with
its own failure modes. The clock has to redraw every second anyway.

The controller never mutates the log — it only reads. `Writer` is held
onto (and exposed via `.writer`) purely so a caller that needs to make a
change shares the same instance and the same `now`, not because refresh
needs one.
"""

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from loop.app import view
from loop.app.writer import Writer
from loop.core import events
from loop.store import jsonl, paths

POLL_MS = 1000


class Controller(QObject):
    """Owns the poll timer and the last-known-good view.

    `changed` fires on every refresh, successful or frozen, with the
    `DashboardView` to render. `.view` holds the same value between
    signals for anything that reads it synchronously (e.g. a dialog
    checking `active_id` before it opens).
    """

    changed = Signal(object)

    def __init__(self, writer: Writer | None = None, poll_ms: int = POLL_MS,
                 now: Callable[[], float] = time.time) -> None:
        super().__init__()
        self._writer = writer if writer is not None else Writer(now=now)
        self._now = now
        self._log: list[dict] = []
        self._size = -1
        self._good: view.DashboardView | None = None
        self._frozen_at: float | None = None
        self.reads = 0
        self.view: view.DashboardView | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self.refresh)

    @property
    def writer(self) -> Writer:
        """Not part of the read path. Exposed so a dialog that needs to
        call into `loop.app.commands` can share this controller's
        `Writer` instead of constructing a second one with a different
        clock."""
        return self._writer

    def start(self) -> None:
        self.refresh()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    @property
    def active_id(self) -> int | None:
        return self.view.active_id if self.view is not None else None

    def refresh(self) -> None:
        try:
            self._reload_if_changed()
        except jsonl.CorruptLogError as exc:
            self.view = view.unreadable(
                self._good,
                path=str(paths.events_path()),
                line=_line_number(exc),
                frozen_at=self._frozen_at or 0.0,
            )
            self.changed.emit(self.view)
            return

        now = self._now()
        state = events.fold(self._log)
        self._good = view.build(state, self._log, now)
        self._frozen_at = self._good.meter.elapsed_s if self._good.meter else 0.0
        self.view = self._good
        self.changed.emit(self.view)

    def _reload_if_changed(self) -> None:
        """Size is a complete change signal on an append-only file, and
        unlike mtime it has no filesystem granularity to lose a write in.
        A repair that shortens the file changes it too.

        Raising here leaves `self._size` unchanged, so the next poll
        retries the read — that is what lets a repaired log recover
        without a restart.
        """
        path = paths.events_path()
        size = path.stat().st_size if path.exists() else 0
        if size == self._size:
            return
        self._log = jsonl.read_all()
        self._size = size
        self.reads += 1


def _line_number(exc: jsonl.CorruptLogError) -> int:
    """`CorruptLogError` carries the line in its message: '<path>: line N
    is not valid JSON'. Parsing it beats widening the exception for one
    caller, and a miss costs a wrong number in a banner, not a wrong
    state."""
    text = str(exc)
    marker = "line "
    if marker not in text:
        return 0
    tail = text.split(marker, 1)[1]
    digits = tail.split(" ", 1)[0]
    return int(digits) if digits.isdigit() else 0
