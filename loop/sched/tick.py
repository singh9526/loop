"""One scheduler tick: ask core what is due, render it, write the answer down.

This is one of only two places (with `cli.py`) that is allowed to touch
core, blockers, and the store in the same breath.
"""

from __future__ import annotations

import time

from loop.app.checkin import (  # noqa: F401  (re-exported for the daemon)
    CUT_FIELDS,
    EXTEND_FIELDS,
    answers_to_events,
    build_prompt,
)
from loop.blockers.base import Blocker
from loop.blockers.factory import get_blocker
from loop.core import events, schedule
from loop.store import jsonl


def tick(now: float | None = None, blocker: Blocker | None = None) -> schedule.Due | None:
    at = time.time() if now is None else now
    state = events.fold(jsonl.read_all())

    due = schedule.next_due(state, at)
    if due is None:
        return None

    loop = state.loops[due.loop_id]
    prompt = build_prompt(state, loop, due)

    if due.kind != "ping":
        jsonl.append(events.make("checkpoint_shown", ts=at, loop_id=loop.id, kind=due.kind))

    answers = (blocker or get_blocker()).ask(prompt)

    # A blocking prompt can sit for minutes. If the loop it was built for
    # was closed, abandoned, or paused from elsewhere while it was up, the
    # answers are stale — discard them and let this tick be a quiet one,
    # exactly like a tick that found nothing due.
    if events.fold(jsonl.read_all()).active_id != due.loop_id:
        return None

    for event in answers_to_events(loop, due, answers, time.time() if now is None else now):
        jsonl.append(event)
    return due
