"""The only path that appends to the event log.

`mutate` holds the lock across read, validate, and append. That is what
makes a multi-event mutation atomic — `fold` rejects a `loop_opened` that
arrives while another loop is active, so a torn pause/open pair produces a
log that will not load — and it is what makes a precondition mean
anything: `build` validates against the state its events are about to be
appended to, not against whatever a window last painted.

`build` returns the events *and* the result, computed together under the
one lock hold. A result assembled afterwards, from a second read, could
report a number a concurrent write had already changed.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from loop.core import events
from loop.core.models import State
from loop.store import jsonl, lock, paths

R = TypeVar("R")
Build = Callable[[State, float], "tuple[list[dict], R]"]


class Writer:
    def __init__(self, now: Callable[[], float] = time.time) -> None:
        self._now = now

    def read(self) -> State:
        """The display read. Deliberately unlocked: a reader that blocked
        behind a writer would stall the clock for no gain, and a fold of a
        half-written tail is exactly what `read_all` already tolerates."""
        return events.fold(jsonl.read_all())

    def mutate(self, build: Build) -> R:
        with lock.exclusive(paths.lock_path()):
            state = events.fold(jsonl.read_all())
            written, result = build(state, self._now())
            for event in written:
                jsonl.append(event)
            return result
