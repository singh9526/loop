"""Flailing has a precise signature: actions climbing, eliminations flat.

`elapsed` is always active elapsed — a loop that sat paused overnight has
not been thrashing for eight hours.
"""

from __future__ import annotations

from dataclasses import dataclass

from loop.core.models import Loop
from loop.core.timefmt import format_duration

MIN_ELAPSED_S = 1800.0
MIN_ACTIONS = 5
NEGATIVE_PING_STREAK = 3


@dataclass(frozen=True, slots=True)
class Thrash:
    firing: bool
    panel: str | None = None


def detect(loop: Loop, elapsed: float) -> Thrash:
    if _no_progress_despite_activity(loop, elapsed):
        return Thrash(
            firing=True,
            panel=(
                f"⚠  {loop.actions} actions, {loop.eliminated} ruled out, "
                f"{format_duration(elapsed)} elapsed.\n"
                "   you are thrashing.\n"
                "   → step away 5m, or escalate."
            ),
        )

    if _negative_streak(loop):
        return Thrash(
            firing=True,
            panel=(
                f"⚠  last {NEGATIVE_PING_STREAK} pings: no, no, no.\n"
                "   you are thrashing.\n"
                "   → step away 5m, or escalate."
            ),
        )

    return Thrash(firing=False)


def _no_progress_despite_activity(loop: Loop, elapsed: float) -> bool:
    return (
        elapsed >= MIN_ELAPSED_S
        and loop.actions >= MIN_ACTIONS
        and loop.eliminated == 0
    )


def _negative_streak(loop: Loop) -> bool:
    recent = loop.recent_ping_answers[-NEGATIVE_PING_STREAK:]
    return len(recent) == NEGATIVE_PING_STREAK and not any(recent)
