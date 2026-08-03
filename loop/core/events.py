"""Event constructors and the fold that turns a log into state.

The event table is data, not fourteen near-identical constructor functions.
`make` validates that the fields supplied are exactly the fields declared,
which catches a typo at write time rather than three weeks later when
`fold` silently ignores it.
"""

from __future__ import annotations

from loop.core.models import (
    ABANDONED,
    ACTIVE,
    CLOSED,
    PAUSED,
    Hypothesis,
    Loop,
    State,
)


class InvariantError(Exception):
    """The log describes a state the domain forbids."""


EVENT_FIELDS: dict[str, tuple[str, ...]] = {
    "loop_opened": (
        "question", "stop_condition", "budget_s", "interval_s", "hypotheses", "parent_id",
    ),
    "loop_paused": ("reason",),
    "loop_resumed": (),
    "action_logged": ("action", "because"),
    "hypothesis_added": ("hyp_id", "text"),
    "hypothesis_eliminated": ("hyp_id",),
    "ping_answered": ("smaller", "eliminated", "shown_at"),
    "ping_unanswered": ("shown_at",),
    "checkpoint_shown": ("kind",),
    "checkpoint_answered": ("kind", "on_track", "decision"),
    "checkpoint_unanswered": ("kind", "shown_at"),
    "scope_cut": ("old_stop_condition", "new_stop_condition"),
    "budget_extended": ("old_budget_s", "new_budget_s", "learned"),
    "loop_closed": ("what_was_it", "giveaway", "five_min_path"),
    "loop_abandoned": (),
}


def make(type: str, ts: float, loop_id: int | None = None, **fields) -> dict:
    if type not in EVENT_FIELDS:
        raise ValueError(f"unknown event type: {type!r}")

    expected = set(EVENT_FIELDS[type])
    supplied = set(fields)
    if missing := sorted(expected - supplied):
        raise ValueError(f"{type} is missing fields: {', '.join(missing)}")
    if extra := sorted(supplied - expected):
        raise ValueError(f"{type} got unexpected fields: {', '.join(extra)}")

    event = {"type": type, "ts": ts, "loop_id": loop_id}
    event.update(fields)
    return event


def checkpoint_key(kind: str, budget_s: float) -> str:
    return f"{kind}:{budget_s}"


def stack_depth(state: State) -> int:
    return sum(1 for lp in state.loops.values() if lp.status in (ACTIVE, PAUSED))


def next_loop_id(state: State) -> int:
    return max(state.loops, default=0) + 1


def next_hypothesis_id(loop: Loop) -> int:
    return max((h.id for h in loop.hypotheses), default=0) + 1


def fold(events: list[dict]) -> State:
    state = State(loops={}, active_id=None)
    for event in events:
        apply(state, event)
    return state


def apply(state: State, event: dict) -> None:
    kind = event["type"]
    ts = event["ts"]
    loop_id = event["loop_id"]

    if kind == "loop_opened":
        if state.active_id is not None:
            raise InvariantError(
                f"loop {loop_id} opened while loop {state.active_id} is still active"
            )
        state.loops[loop_id] = Loop(
            id=loop_id,
            question=event["question"],
            stop_condition=event["stop_condition"],
            budget_s=event["budget_s"],
            interval_s=event["interval_s"],
            parent_id=event["parent_id"],
            opened_at=ts,
            original_budget_s=event["budget_s"],
            hypotheses=[
                Hypothesis(id=index, text=text)
                for index, text in enumerate(event["hypotheses"], start=1)
            ],
            intervals=[[ts, None]],
        )
        state.active_id = loop_id
        return

    loop = state.loops[loop_id]

    if kind == "loop_paused":
        _end_interval(loop, ts)
        loop.status = PAUSED
        state.active_id = None

    elif kind == "loop_resumed":
        if state.active_id is not None:
            raise InvariantError(
                f"loop {loop_id} resumed while loop {state.active_id} is still active"
            )
        loop.status = ACTIVE
        loop.intervals.append([ts, None])
        # Rebase: returning to a loop must never fire a ping immediately.
        loop.last_ping_elapsed = loop.elapsed(ts)
        state.active_id = loop_id

    elif kind == "action_logged":
        loop.actions += 1

    elif kind == "hypothesis_added":
        loop.hypotheses.append(Hypothesis(id=event["hyp_id"], text=event["text"]))

    elif kind == "hypothesis_eliminated":
        for hypothesis in loop.hypotheses:
            if hypothesis.id == event["hyp_id"] and hypothesis.alive:
                hypothesis.alive = False
                loop.eliminated += 1
                break

    elif kind == "ping_answered":
        loop.last_ping_elapsed = loop.elapsed(ts)
        loop.pings_answered += 1
        loop.recent_ping_answers.append(bool(event["smaller"]))

    elif kind == "ping_unanswered":
        loop.last_ping_elapsed = loop.elapsed(ts)
        loop.pings_timed_out += 1

    elif kind == "checkpoint_shown":
        pass  # recorded for forensics only; carries no state

    elif kind == "checkpoint_answered":
        key = checkpoint_key(event["kind"], loop.budget_s)
        loop.checkpoints_answered.add(key)
        loop.checkpoints_pending.pop(key, None)
        # A ping suppressed by the collision window (schedule._near_checkpoint)
        # must not become due again the instant this checkpoint is resolved —
        # rebase it here, exactly like a resume rebases it after a pause.
        loop.last_ping_elapsed = loop.elapsed(ts)

    elif kind == "checkpoint_unanswered":
        key = checkpoint_key(event["kind"], loop.budget_s)
        loop.checkpoints_pending[key] = loop.elapsed(ts)
        loop.checkpoints_timed_out += 1
        loop.last_ping_elapsed = loop.elapsed(ts)

    elif kind == "scope_cut":
        loop.stop_condition = event["new_stop_condition"]

    elif kind == "budget_extended":
        loop.budget_s = event["new_budget_s"]
        loop.extensions += 1

    elif kind in ("loop_closed", "loop_abandoned"):
        _end_interval(loop, ts)
        loop.status = CLOSED if kind == "loop_closed" else ABANDONED
        loop.closed_at = ts
        if kind == "loop_closed":
            loop.postmortem = {
                "what_was_it": event["what_was_it"],
                "giveaway": event["giveaway"],
                "five_min_path": event["five_min_path"],
            }
        if state.active_id == loop_id:
            state.active_id = None

    else:  # pragma: no cover - EVENT_FIELDS and apply are kept in step
        raise ValueError(f"fold has no handler for {kind!r}")


def _end_interval(loop: Loop, ts: float) -> None:
    if loop.intervals and loop.intervals[-1][1] is None:
        loop.intervals[-1][1] = ts
