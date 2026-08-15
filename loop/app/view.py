"""What the window shows, as plain data.

Every claim the dashboard makes is decided here and asserted in ordinary
unit tests. The widgets bind; they do not compute. Anything in `gui/` that
needs a test of its own is a sign that logic leaked down.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from loop.core import events, schedule, thrash, timeline
from loop.core.models import State, paused_for
from loop.core.timefmt import format_duration

STALE_PAUSE_S = 86_400.0

ALL_ACTIONS = frozenset({
    "open", "try", "hyp_add", "hyp_kill", "pause",
    "resume", "cut", "extend", "close", "abandon",
})

ACTIVE_ACTIONS = frozenset({
    "try", "hyp_add", "hyp_kill", "pause", "cut", "extend", "close", "abandon",
})

THRASH_TITLE = "You are thrashing."
THRASH_FIX = "Step away for five minutes, or escalate to someone who has seen this before."


@dataclass(frozen=True, slots=True)
class MeterView:
    elapsed_s: float
    budget_s: float
    fraction: float          # clamped to 1.0; the overrun rides in over_s
    over_s: float
    next_checkin_s: float | None


@dataclass(frozen=True, slots=True)
class StackRow:
    loop_id: int
    question: str
    active: bool
    elapsed_s: float
    paused_s: float
    stale: bool


@dataclass(frozen=True, slots=True)
class HypothesisRow:
    hyp_id: int
    text: str
    alive: bool


@dataclass(frozen=True, slots=True)
class ThrashView:
    title: str
    why: str
    fix: str


@dataclass(frozen=True, slots=True)
class StatusView:
    state: str               # armed | paused | idle | unreadable
    detail: str
    depth: int


@dataclass(frozen=True, slots=True)
class DashboardView:
    active_id: int | None
    question: str | None
    stop_condition: str | None
    meter: MeterView | None
    stack: tuple[StackRow, ...]
    hypotheses: tuple[HypothesisRow, ...]
    timeline: tuple[timeline.Entry, ...]
    thrash: ThrashView | None
    status: StatusView
    enabled_actions: frozenset[str]
    live_count: int
    dead_count: int
    error: str | None = None


def build(state: State, log: list[dict], now: float) -> DashboardView:
    loop = state.active_loop()
    depth = events.stack_depth(state)
    stack = _stack(state, now)

    if loop is None:
        enabled = {"open"}
        if state.paused_loops():
            enabled.add("resume")
            status = StatusView("paused", "no check-ins while paused", depth)
        else:
            status = StatusView("idle", "no check-ins scheduled", depth)
        return DashboardView(
            active_id=None, question=None, stop_condition=None, meter=None,
            stack=stack, hypotheses=(), timeline=(), thrash=None,
            status=status, enabled_actions=frozenset(enabled),
            live_count=0, dead_count=0,
        )

    elapsed = loop.elapsed(now)
    return DashboardView(
        active_id=loop.id,
        question=loop.question,
        stop_condition=loop.stop_condition,
        meter=_meter(loop, elapsed),
        stack=stack,
        hypotheses=tuple(
            HypothesisRow(h.id, h.text, h.alive) for h in loop.hypotheses
        ),
        timeline=tuple(timeline.entries(log, loop.id)),
        thrash=_thrash(loop, elapsed),
        status=StatusView("armed", "check-ins armed", depth),
        enabled_actions=frozenset(ACTIVE_ACTIONS | {"open"}),
        live_count=len(loop.live_hypotheses()),
        dead_count=loop.eliminated,
    )


def unreadable(previous: DashboardView | None, *, path: str, line: int,
               frozen_at: float) -> DashboardView:
    """The log stopped parsing at an interior line.

    The last state that folded cleanly stays on screen — a stale clock
    beats a blank window — and every action is disabled. Appending to a
    log we cannot fold would make the damage worse, and this app is the
    last thing that should do that.
    """
    detail = f"frozen at {format_duration(frozen_at)}"
    if previous is None:
        return DashboardView(
            active_id=None, question=None, stop_condition=None, meter=None,
            stack=(), hypotheses=(), timeline=(), thrash=None,
            status=StatusView("unreadable", detail, 0),
            enabled_actions=frozenset(), live_count=0, dead_count=0,
            error=f"{path}:{line}",
        )
    return replace(
        previous,
        status=StatusView("unreadable", detail, previous.status.depth),
        enabled_actions=frozenset(),
        error=f"{path}:{line}",
    )


def _meter(loop, elapsed: float) -> MeterView:
    budget = loop.budget_s
    return MeterView(
        elapsed_s=elapsed,
        budget_s=budget,
        fraction=min(1.0, elapsed / budget) if budget > 0 else 1.0,
        over_s=max(0.0, elapsed - budget),
        next_checkin_s=_next_checkin(loop, elapsed),
    )


def _next_checkin(loop, elapsed: float) -> float | None:
    """Seconds of *active* time until the next thing fires.

    Whichever comes first: the next ping, or an unanswered checkpoint
    boundary still ahead of us. Nothing ahead means the budget is gone and
    p100 is already due, which the meter says in its own way.
    """
    candidates = [loop.last_ping_elapsed + loop.interval_s - elapsed]
    for kind in schedule.CHECKPOINT_KINDS:
        key = events.checkpoint_key(kind, loop.budget_s)
        if key in loop.checkpoints_answered:
            continue
        candidates.append(schedule.checkpoint_boundary(loop, kind) - elapsed)
    ahead = [value for value in candidates if value > 0]
    return min(ahead) if ahead else None


def _stack(state: State, now: float) -> tuple[StackRow, ...]:
    """Paused parents first, most recently paused at the top; active last.

    That ordering is the mockup's: the paused rows are context you scan
    past, and the active loop is where the eye lands.
    """
    rows = [
        StackRow(
            loop_id=loop.id, question=loop.question, active=False,
            elapsed_s=loop.elapsed(now), paused_s=paused_for(loop, now),
            stale=paused_for(loop, now) >= STALE_PAUSE_S,
        )
        for loop in state.paused_loops()
    ]
    active = state.active_loop()
    if active is not None:
        rows.append(StackRow(
            loop_id=active.id, question=active.question, active=True,
            elapsed_s=active.elapsed(now), paused_s=0.0, stale=False,
        ))
    return tuple(rows)


def _thrash(loop, elapsed: float) -> ThrashView | None:
    """Re-derives the reason rather than parsing `thrash.panel`.

    The detector's panel is terminal text with box glyphs and newlines in
    it. Reading `firing` and recomputing the counts keeps the two renderers
    independent, and the counts are the auditable part.
    """
    if not thrash.detect(loop, elapsed).firing:
        return None
    if loop.actions >= thrash.MIN_ACTIONS and loop.eliminated == 0:
        why = (f"{loop.actions} actions · {loop.eliminated} ruled out · "
               f"{format_duration(elapsed)} elapsed")
    else:
        recent = loop.recent_ping_answers[-thrash.NEGATIVE_PING_STREAK:]
        why = "last 3 check-ins: " + " · ".join("yes" if answer else "no" for answer in recent)
    return ThrashView(title=THRASH_TITLE, why=why, fix=THRASH_FIX)
