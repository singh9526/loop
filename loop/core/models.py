"""Domain objects. Pure data plus the arithmetic that belongs to it.

No I/O, no OS imports. `elapsed_from_intervals` lives here rather than in
`schedule.py` because `events.fold` also needs it, and importing schedule
from events would be circular.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_STACK_DEPTH = 5
WARN_STACK_DEPTH = 3

ACTIVE = "active"
PAUSED = "paused"
CLOSED = "closed"
ABANDONED = "abandoned"


def elapsed_from_intervals(intervals: list[list[float | None]], at: float) -> float:
    """Sum the active spans up to `at`. Paused gaps contribute nothing."""
    total = 0.0
    for start, end in intervals:
        stop = at if end is None else end
        if stop > start:
            total += stop - start
    return total


@dataclass(slots=True)
class Hypothesis:
    id: int
    text: str
    alive: bool = True


@dataclass(slots=True)
class Loop:
    id: int
    question: str
    stop_condition: str
    budget_s: float
    interval_s: float
    parent_id: int | None
    opened_at: float
    original_budget_s: float
    hypotheses: list[Hypothesis] = field(default_factory=list)
    status: str = ACTIVE
    intervals: list[list[float | None]] = field(default_factory=list)

    actions: int = 0
    eliminated: int = 0

    last_ping_elapsed: float = 0.0
    recent_ping_answers: list[bool] = field(default_factory=list)
    pings_answered: int = 0
    pings_timed_out: int = 0

    checkpoints_answered: set[str] = field(default_factory=set)
    checkpoints_pending: dict[str, float] = field(default_factory=dict)
    checkpoints_timed_out: int = 0
    extensions: int = 0

    closed_at: float | None = None
    postmortem: dict[str, str] | None = None

    def live_hypotheses(self) -> list[Hypothesis]:
        return [h for h in self.hypotheses if h.alive]

    def elapsed(self, at: float) -> float:
        return elapsed_from_intervals(self.intervals, at)


@dataclass(slots=True)
class State:
    loops: dict[int, Loop]
    active_id: int | None

    def active_loop(self) -> Loop | None:
        return self.loops.get(self.active_id) if self.active_id is not None else None

    def paused_loops(self) -> list[Loop]:
        """Paused loops, most recently paused first."""
        paused = [lp for lp in self.loops.values() if lp.status == PAUSED]
        return sorted(paused, key=_last_active_end, reverse=True)


def _last_active_end(loop: Loop) -> float:
    if not loop.intervals:
        return loop.opened_at
    end = loop.intervals[-1][1]
    return loop.opened_at if end is None else end


def paused_for(loop: Loop, at: float) -> float:
    """Wall-clock seconds since this loop's last active span ended."""
    if loop.intervals:
        end = loop.intervals[-1][1]
        if end is not None:
            return max(0.0, at - end)
    return max(0.0, at - loop.opened_at)


def pause_gaps(loop: Loop) -> list[float]:
    """Wall-clock seconds spent paused between each pair of active spans.

    One gap per adjacent interval pair. A pair whose earlier interval is
    still open (the loop never paused between it and the next) contributes
    nothing — that only happens for a currently-open final interval, which
    has no "next" to pair with anyway.
    """
    gaps = []
    for index in range(len(loop.intervals) - 1):
        end = loop.intervals[index][1]
        if end is None:
            continue
        start = loop.intervals[index + 1][0]
        gaps.append(max(0.0, start - end))
    return gaps
