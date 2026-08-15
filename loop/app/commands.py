"""Every mutation, headless.

No argparse, no stdout, no Qt. A front end collects the values, calls one
of these, and renders the result. Both front ends get the same refusals
for the same reasons, because there is only one place they are decided.

Every function builds its events inside `writer.mutate`, so every
precondition is checked against the state those events are about to join.
"""

from __future__ import annotations

from dataclasses import dataclass

from loop.app.errors import LoopError, StaleError
from loop.app.writer import Writer
from loop.core import events
from loop.core.models import MAX_STACK_DEPTH, PAUSED, Loop, State


@dataclass(frozen=True, slots=True)
class LoopSummary:
    loop_id: int
    elapsed_s: float
    budget_s: float
    interval_s: float


@dataclass(frozen=True, slots=True)
class Opened:
    event: dict
    loop_id: int
    depth: int
    hypotheses: int
    paused_id: int | None
    paused_elapsed_s: float


@dataclass(frozen=True, slots=True)
class Paused:
    event: dict
    loop_id: int
    elapsed_s: float


@dataclass(frozen=True, slots=True)
class Resumed:
    event: dict
    summary: LoopSummary


@dataclass(frozen=True, slots=True)
class Closed:
    event: dict
    loop_id: int
    elapsed_s: float
    eliminated: int
    resumed: LoopSummary | None


@dataclass(frozen=True, slots=True)
class Abandoned:
    event: dict
    loop_id: int
    elapsed_s: float
    resumed: LoopSummary | None


def _require_active(state: State) -> Loop:
    loop = state.active_loop()
    if loop is None:
        raise LoopError("no active loop. run `loop open \"<question>\"` first.")
    return loop


def _summary(loop: Loop, at: float) -> LoopSummary:
    return LoopSummary(
        loop_id=loop.id, elapsed_s=loop.elapsed(at),
        budget_s=loop.budget_s, interval_s=loop.interval_s,
    )


def open_loop(writer: Writer, *, question: str, stop_condition: str,
              budget_s: float, interval_s: float, hypotheses: list[str],
              stack_on_active: bool) -> Opened:
    def build(state: State, now: float):
        active = state.active_loop()
        written: list[dict] = []
        paused_elapsed = 0.0

        if active is not None:
            depth = events.stack_depth(state)
            if depth >= MAX_STACK_DEPTH:
                raise LoopError(
                    f"stack depth {depth}. close or abandon something before opening another."
                )
            if not stack_on_active:
                raise LoopError("aborted.")
            paused_elapsed = active.elapsed(now)
            # The new question is the reason the old one stopped. That is
            # the honest reason, and it costs the user no extra typing.
            written.append(
                events.make("loop_paused", ts=now, loop_id=active.id, reason=question)
            )

        opened = events.make(
            "loop_opened", ts=now, loop_id=events.next_loop_id(state),
            question=question, stop_condition=stop_condition,
            budget_s=budget_s, interval_s=interval_s,
            hypotheses=list(hypotheses),
            parent_id=active.id if active is not None else None,
        )
        written.append(opened)

        return written, Opened(
            event=opened,
            loop_id=opened["loop_id"],
            depth=events.stack_depth(state) + 1,
            hypotheses=len(hypotheses),
            paused_id=active.id if active is not None else None,
            paused_elapsed_s=paused_elapsed,
        )

    return writer.mutate(build)


def pause(writer: Writer, *, reason: str) -> Paused:
    def build(state: State, now: float):
        loop = _require_active(state)
        event = events.make("loop_paused", ts=now, loop_id=loop.id, reason=reason)
        return [event], Paused(event=event, loop_id=loop.id, elapsed_s=loop.elapsed(now))

    return writer.mutate(build)


def resume(writer: Writer, *, loop_id: int | None, pause_reason: str | None) -> Resumed:
    def build(state: State, now: float):
        target = _resume_target(state, loop_id)
        written: list[dict] = []

        active = state.active_loop()
        if active is not None:
            if active.id == target.id:
                raise LoopError(f"#{target.id} is already active.")
            if not pause_reason:
                # The caller decided what to resume while nothing was
                # active, and something became active in between.
                raise StaleError(
                    f"#{active.id} became active while you were deciding. Try again."
                )
            written.append(
                events.make("loop_paused", ts=now, loop_id=active.id, reason=pause_reason)
            )

        event = events.make("loop_resumed", ts=now, loop_id=target.id)
        written.append(event)
        return written, Resumed(event=event, summary=_summary(target, now))

    return writer.mutate(build)


def _resume_target(state: State, loop_id: int | None) -> Loop:
    if loop_id is None:
        paused = state.paused_loops()
        if not paused:
            raise LoopError("nothing to resume.")
        return paused[0]

    target = state.loops.get(loop_id)
    if target is None:
        raise LoopError(f"no loop #{loop_id}.")
    if target.status != PAUSED:
        raise LoopError(f"#{loop_id} is {target.status}, not paused.")
    return target


def close(writer: Writer, *, what_was_it: str, giveaway: str,
          five_min_path: str) -> Closed:
    def build(state: State, now: float):
        loop = _require_active(state)
        event = events.make(
            "loop_closed", ts=now, loop_id=loop.id,
            what_was_it=what_was_it, giveaway=giveaway, five_min_path=five_min_path,
        )
        written, resumed = _with_parent_pop(state, loop, now, [event])
        return written, Closed(
            event=event, loop_id=loop.id, elapsed_s=loop.elapsed(now),
            eliminated=loop.eliminated, resumed=resumed,
        )

    return writer.mutate(build)


def abandon(writer: Writer) -> Abandoned:
    def build(state: State, now: float):
        loop = _require_active(state)
        event = events.make("loop_abandoned", ts=now, loop_id=loop.id)
        written, resumed = _with_parent_pop(state, loop, now, [event])
        return written, Abandoned(
            event=event, loop_id=loop.id, elapsed_s=loop.elapsed(now), resumed=resumed,
        )

    return writer.mutate(build)


def _with_parent_pop(state: State, loop: Loop, now: float, written: list[dict]):
    """Resume the parent in the same write that ends the child.

    `cli.py` does this as a second, separate append today, which leaves a
    window where nothing is active and the scheduler has nothing to watch.
    """
    if loop.parent_id is None:
        return written, None
    parent = state.loops.get(loop.parent_id)
    if parent is None or parent.status != PAUSED:
        return written, None
    written.append(events.make("loop_resumed", ts=now, loop_id=parent.id))
    return written, _summary(parent, now)


@dataclass(frozen=True, slots=True)
class Logged:
    event: dict
    actions: int


@dataclass(frozen=True, slots=True)
class HypothesisAdded:
    event: dict
    hyp_id: int
    live: int


@dataclass(frozen=True, slots=True)
class HypothesisKilled:
    event: dict
    hyp_id: int
    live: int


@dataclass(frozen=True, slots=True)
class ScopeCut:
    event: dict
    old_stop_condition: str
    new_stop_condition: str


@dataclass(frozen=True, slots=True)
class BudgetExtended:
    event: dict
    old_budget_s: float
    new_budget_s: float


def log_action(writer: Writer, *, action: str, because: str) -> Logged:
    def build(state: State, now: float):
        loop = _require_active(state)
        event = events.make(
            "action_logged", ts=now, loop_id=loop.id, action=action, because=because
        )
        return [event], Logged(event=event, actions=loop.actions + 1)

    return writer.mutate(build)


def add_hypothesis(writer: Writer, *, text: str) -> HypothesisAdded:
    def build(state: State, now: float):
        loop = _require_active(state)
        event = events.make(
            "hypothesis_added", ts=now, loop_id=loop.id,
            hyp_id=events.next_hypothesis_id(loop), text=text,
        )
        return [event], HypothesisAdded(
            event=event, hyp_id=event["hyp_id"], live=len(loop.live_hypotheses()) + 1,
        )

    return writer.mutate(build)


def kill_hypothesis(writer: Writer, *, hyp_id: int) -> HypothesisKilled:
    def build(state: State, now: float):
        loop = _require_active(state)
        target = next((h for h in loop.hypotheses if h.id == hyp_id), None)
        if target is None:
            # Verbatim from cli.py:198 — tests/test_cli_open.py asserts it.
            raise LoopError(f"no live hypothesis {hyp_id}.")
        if not target.alive:
            # Almost always a check-in got here first. Nothing to fix.
            raise StaleError(f"§{hyp_id} {target.text} was already ruled out.")
        event = events.make(
            "hypothesis_eliminated", ts=now, loop_id=loop.id, hyp_id=hyp_id
        )
        return [event], HypothesisKilled(
            event=event, hyp_id=hyp_id, live=len(loop.live_hypotheses()) - 1,
        )

    return writer.mutate(build)


def cut_scope(writer: Writer, *, new_stop_condition: str) -> ScopeCut:
    def build(state: State, now: float):
        loop = _require_active(state)
        event = events.make(
            "scope_cut", ts=now, loop_id=loop.id,
            old_stop_condition=loop.stop_condition,
            new_stop_condition=new_stop_condition,
        )
        return [event], ScopeCut(
            event=event, old_stop_condition=loop.stop_condition,
            new_stop_condition=new_stop_condition,
        )

    return writer.mutate(build)


def extend_budget(writer: Writer, *, new_budget_s: float, learned: str) -> BudgetExtended:
    def build(state: State, now: float):
        loop = _require_active(state)
        event = events.make(
            "budget_extended", ts=now, loop_id=loop.id,
            old_budget_s=loop.budget_s, new_budget_s=new_budget_s, learned=learned,
        )
        return [event], BudgetExtended(
            event=event, old_budget_s=loop.budget_s, new_budget_s=new_budget_s,
        )

    return writer.mutate(build)
