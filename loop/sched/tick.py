"""One scheduler tick: ask core what is due, render it, write the answer down.

This is one of only two places (with `cli.py`) that is allowed to touch
core, blockers, and the store in the same breath.
"""

from __future__ import annotations

import time

from loop.blockers.base import Answers, Choice, Prompt, TextField
from loop.blockers.factory import get_blocker
from loop.core import events, schedule, thrash
from loop.core.models import Loop, State
from loop.core.timefmt import format_duration, parse_duration
from loop.store import jsonl

PING_QUESTION = "hypothesis space smaller than 20m ago?"

CUT_FIELDS = [TextField("new_stop_condition", "new stop condition")]
EXTEND_FIELDS = [
    TextField("new_budget", "new budget"),
    TextField("learned", "what did you learn that made it bigger?"),
]


def build_prompt(state: State, loop: Loop, due: schedule.Due) -> Prompt:
    span = f"{format_duration(due.elapsed)} / {format_duration(loop.budget_s)}"

    if due.kind == "ping":
        paused = events.stack_depth(state) - 1
        title = f"loop #{loop.id} · {span}"
        if paused > 0:
            title += f" · {paused} paused"
        detected = thrash.detect(loop, due.elapsed)
        return Prompt(
            kind="ping",
            title=title,
            question=PING_QUESTION,
            choices=[Choice("y", "yes"), Choice("n", "no")],
            pick_list=[h.text for h in loop.live_hypotheses()],
            pick_after="y",
            warning=detected.panel,
        )

    if due.kind == "p75":
        return Prompt(
            kind="p75",
            title=f"75% of budget gone · {span}",
            question="is 75% of the work done?",
            choices=[
                Choice("y", "yes"),
                Choice("c", "cut scope"),
                Choice("e", "extend estimate"),
            ],
            fields_after={"c": CUT_FIELDS, "e": EXTEND_FIELDS},
        )

    return Prompt(
        kind="p100",
        title=f"budget gone · {span}",
        question="budget is gone. what now?",
        choices=[
            Choice("x", "stop now — then run `loop close`"),
            Choice("c", "cut scope"),
            Choice("e", "extend estimate"),
        ],
        fields_after={"c": CUT_FIELDS, "e": EXTEND_FIELDS},
    )


def answers_to_events(loop: Loop, due: schedule.Due, answers: Answers, now: float) -> list[dict]:
    if due.kind == "ping":
        return _ping_events(loop, answers, now)
    return _checkpoint_events(loop, due, answers, now)


def _ping_events(loop: Loop, answers: Answers, now: float) -> list[dict]:
    if answers.timed_out:
        return [
            events.make("ping_unanswered", ts=now, loop_id=loop.id,
                        shown_at=answers.shown_at)
        ]

    smaller = answers.choice == "y"
    hyp_id = None
    if smaller and answers.picked is not None:
        live = loop.live_hypotheses()
        if 1 <= answers.picked <= len(live):
            hyp_id = live[answers.picked - 1].id

    written = [
        events.make("ping_answered", ts=now, loop_id=loop.id, smaller=smaller,
                    eliminated=hyp_id, shown_at=answers.shown_at)
    ]
    if hyp_id is not None:
        written.append(
            events.make("hypothesis_eliminated", ts=now, loop_id=loop.id, hyp_id=hyp_id)
        )
    return written


def _checkpoint_events(loop: Loop, due: schedule.Due, answers: Answers, now: float) -> list[dict]:
    deferred = [
        events.make("checkpoint_unanswered", ts=now, loop_id=loop.id,
                    kind=due.kind, shown_at=answers.shown_at)
    ]
    if answers.timed_out:
        return deferred

    if answers.choice == "c":
        return _answered(loop, due, now, decision="cut", on_track=False) + [
            events.make("scope_cut", ts=now, loop_id=loop.id,
                        old_stop_condition=loop.stop_condition,
                        new_stop_condition=answers.fields["new_stop_condition"])
        ]

    if answers.choice == "e":
        try:
            new_budget_s = parse_duration(answers.fields["new_budget"])
        except ValueError:
            return deferred  # re-fires in ten minutes; never guess the number
        return _answered(loop, due, now, decision="extend", on_track=False) + [
            events.make("budget_extended", ts=now, loop_id=loop.id,
                        old_budget_s=loop.budget_s, new_budget_s=new_budget_s,
                        learned=answers.fields["learned"])
        ]

    decision = "close" if answers.choice == "x" else "continue"
    return _answered(loop, due, now, decision=decision, on_track=decision == "continue")


def _answered(loop: Loop, due: schedule.Due, now: float, *, decision: str, on_track: bool) -> list[dict]:
    """Always first in the returned list: fold keys the checkpoint on the
    budget in force at this instant, which is what makes an extension re-arm."""
    return [
        events.make("checkpoint_answered", ts=now, loop_id=loop.id,
                    kind=due.kind, on_track=on_track, decision=decision)
    ]


def tick(now: float | None = None, blocker=None) -> schedule.Due | None:
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
    for event in answers_to_events(loop, due, answers, time.time() if now is None else now):
        jsonl.append(event)
    return due
