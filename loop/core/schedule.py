"""What check-in, if any, is due right now.

Every decision here reads active elapsed. Nothing in this module may look at
a wall clock other than through the `now` argument, and `now` is only ever
used to close the open interval.
"""

from __future__ import annotations

from dataclasses import dataclass

from loop.core.events import checkpoint_key
from loop.core.models import Loop, State

CHECKPOINT_FRACTIONS: tuple[tuple[str, float], ...] = (("p100", 1.0), ("p75", 0.75))
CHECKPOINT_KINDS: tuple[str, ...] = tuple(kind for kind, _ in CHECKPOINT_FRACTIONS)
CHECKPOINT_RETRY_S = 600.0
COLLISION_WINDOW_S = 180.0


@dataclass(frozen=True, slots=True)
class Due:
    kind: str        # "ping" | "p75" | "p100"
    loop_id: int
    elapsed: float


def active_elapsed(loop: Loop, now: float) -> float:
    return loop.elapsed(now)


def checkpoint_boundary(loop: Loop, kind: str) -> float:
    fraction = dict(CHECKPOINT_FRACTIONS)[kind]
    return fraction * loop.budget_s


def next_due(state: State, now: float) -> Due | None:
    loop = state.active_loop()
    if loop is None:
        return None

    elapsed = active_elapsed(loop, now)

    checkpoint = _checkpoint_due(loop, elapsed)
    if checkpoint is not None:
        return Due(kind=checkpoint, loop_id=loop.id, elapsed=elapsed)

    if _ping_due(loop, elapsed) and not _near_checkpoint(loop, elapsed):
        return Due(kind="ping", loop_id=loop.id, elapsed=elapsed)

    return None


def _checkpoint_due(loop: Loop, elapsed: float) -> str | None:
    """Only the highest boundary already passed is live.

    Once elapsed reaches p100, p75 is moot — it asks a weaker version of the
    same question about a budget that is already gone. So the first passed
    boundary decides the answer, whether that answer is "show it", "already
    answered", or "still inside its retry backoff".
    """
    for kind in CHECKPOINT_KINDS:
        if elapsed < checkpoint_boundary(loop, kind):
            continue
        key = checkpoint_key(kind, loop.budget_s)
        if key in loop.checkpoints_answered:
            return None
        pending_at = loop.checkpoints_pending.get(key)
        if pending_at is not None and elapsed - pending_at < CHECKPOINT_RETRY_S:
            return None
        return kind
    return None


def _ping_due(loop: Loop, elapsed: float) -> bool:
    return elapsed - loop.last_ping_elapsed >= loop.interval_s


def _near_checkpoint(loop: Loop, elapsed: float) -> bool:
    """True when an unanswered checkpoint lands within the next three minutes.

    Only boundaries ahead of us matter. A boundary already behind us either
    returned from `_checkpoint_due`, or is inside its retry backoff — and
    during a backoff a ping is the right thing to show.
    """
    for kind in CHECKPOINT_KINDS:
        boundary = checkpoint_boundary(loop, kind)
        if checkpoint_key(kind, loop.budget_s) in loop.checkpoints_answered:
            continue
        if 0.0 <= boundary - elapsed <= COLLISION_WINDOW_S:
            return True
    return False
