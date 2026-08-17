"""Direct, isolated tests of `loop.app.checkin.build_prompt` and
`answers_to_events` — the translation layer, called without going through
a scheduler or the jsonl append path at all.

`tests/gui/test_scheduler.py` exercises the same logic end to end through
`Scheduler.tick`, including the staleness re-check `commands.record_checkin`
now does under the lock. These tests exist so the translation layer — every
prompt string, and every event an answer becomes — can be proven without
Qt, and so they still run on a machine with no PySide6 installed.
"""

from __future__ import annotations

import pytest

from loop.app import checkin as checkin_module
from loop.blockers.base import Answers
from loop.core import events, schedule
from loop.store import jsonl

BUDGET = 2700.0
INTERVAL = 1200.0


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))


def answer(choice=None, picked=None, fields=None, timed_out=False, shown_at=0.0):
    return Answers(
        timed_out=timed_out, choice=choice, picked=picked,
        fields=fields or {}, shown_at=shown_at, answered_at=0.0,
    )


def seed(extra=(), budget_s=BUDGET, interval_s=INTERVAL, loop_id=1, hypotheses=None):
    jsonl.append(
        events.make(
            "loop_opened", ts=0.0, loop_id=loop_id,
            question="staging deploy fails at startup",
            stop_condition="comes up clean twice in a row",
            budget_s=budget_s, interval_s=interval_s,
            hypotheses=hypotheses or ["cert expiry", "env var missing", "pg conn pool"],
            parent_id=None,
        )
    )
    for event in extra:
        jsonl.append(event)
    return events.fold(jsonl.read_all())


# --- build_prompt -----------------------------------------------------


def test_ping_prompt_carries_title_question_and_live_hypotheses():
    state = seed([events.make("hypothesis_eliminated", ts=5.0, loop_id=1, hyp_id=2)])
    due = schedule.Due(kind="ping", loop_id=1, elapsed=INTERVAL)

    prompt = checkin_module.build_prompt(state, state.loops[1], due)

    assert prompt.kind == "ping"
    assert prompt.title == "loop #1 · 20m / 45m"
    assert prompt.question == "hypothesis space smaller than 20m ago?"
    assert [c.key for c in prompt.choices] == ["y", "n"]
    assert prompt.pick_list == ["cert expiry", "pg conn pool"]
    assert prompt.pick_after == "y"
    assert prompt.warning is None
    assert prompt.timeout_s == 300.0


def test_ping_question_uses_the_loops_own_interval():
    state = seed(interval_s=300.0)  # 5m, not the default 20m
    due = schedule.Due(kind="ping", loop_id=1, elapsed=300.0)

    prompt = checkin_module.build_prompt(state, state.loops[1], due)
    assert "5m" in prompt.question


def test_ping_title_shows_paused_count():
    state = seed([
        events.make("loop_paused", ts=1.0, loop_id=1, reason="prod incident"),
        events.make(
            "loop_opened", ts=1.0, loop_id=2, question="prod 500s",
            stop_condition="s", budget_s=BUDGET, interval_s=INTERVAL,
            hypotheses=["a"], parent_id=1,
        ),
    ])
    due = schedule.Due(kind="ping", loop_id=2, elapsed=INTERVAL)

    prompt = checkin_module.build_prompt(state, state.loops[2], due)
    assert prompt.title.endswith("· 1 paused")


def test_the_thrash_panel_rides_the_ping_prompt():
    actions = [
        events.make("action_logged", ts=float(index), loop_id=1,
                    action=f"a{index}", because="b")
        for index in range(1, 8)
    ]
    # a long budget keeps the checkpoints out of the way; this test is about the ping
    state = seed(actions, budget_s=99_999.0)
    due = schedule.Due(kind="ping", loop_id=1, elapsed=3120.0)  # 52m elapsed

    prompt = checkin_module.build_prompt(state, state.loops[1], due)
    assert prompt.kind == "ping"
    assert "you are thrashing" in prompt.warning
    assert "7 actions, 0 ruled out, 52m elapsed" in prompt.warning


def test_p75_prompt_offers_continue_cut_and_extend():
    state = seed()
    due = schedule.Due(kind="p75", loop_id=1, elapsed=2025.0)

    prompt = checkin_module.build_prompt(state, state.loops[1], due)
    assert prompt.kind == "p75"
    assert prompt.title == "75% of budget gone · 33m / 45m"
    assert prompt.question == "is 75% of the work done?"
    assert [c.key for c in prompt.choices] == ["y", "c", "e"]
    assert [c.label for c in prompt.choices] == ["yes", "cut scope", "extend estimate"]
    assert [f.name for f in prompt.fields_after["c"]] == ["new_stop_condition"]
    assert [f.name for f in prompt.fields_after["e"]] == ["new_budget", "learned"]


def test_p100_prompt_offers_close_cut_and_extend():
    state = seed()
    due = schedule.Due(kind="p100", loop_id=1, elapsed=BUDGET)

    prompt = checkin_module.build_prompt(state, state.loops[1], due)
    assert prompt.kind == "p100"
    assert prompt.title == "budget gone · 45m / 45m"
    assert prompt.question == "budget is gone. what now?"
    assert [c.key for c in prompt.choices] == ["x", "c", "e"]
    assert [c.label for c in prompt.choices] == [
        "stop now", "cut scope", "extend estimate",
    ]
    assert [f.name for f in prompt.fields_after["c"]] == ["new_stop_condition"]
    assert [f.name for f in prompt.fields_after["e"]] == ["new_budget", "learned"]


# --- answers_to_events: ping -------------------------------------------


def test_ping_no_becomes_a_single_event():
    state = seed()
    due = schedule.Due(kind="ping", loop_id=1, elapsed=INTERVAL)

    result = checkin_module.answers_to_events(state.loops[1], due, answer(choice="n"), now=1200.0)
    assert result == [
        {"type": "ping_answered", "ts": 1200.0, "loop_id": 1,
         "smaller": False, "eliminated": None, "shown_at": 0.0}
    ]


def test_ping_yes_with_a_pick_also_eliminates_the_hypothesis():
    """The critical adjacency: `ping_answered(eliminated=X)` is immediately
    followed by `hypothesis_eliminated(hyp_id=X)`, same ts, in one batch.
    `core/timeline.py` reads this exact adjacency to attribute a kill to
    the check-in that caused it (tests/core/test_timeline.py). Reordering,
    splitting, or re-timestamping this pair would silently break that
    projection, so the order and the shared ts are asserted explicitly."""
    state = seed()
    due = schedule.Due(kind="ping", loop_id=1, elapsed=INTERVAL)

    result = checkin_module.answers_to_events(
        state.loops[1], due, answer(choice="y", picked=3), now=1200.0
    )

    assert [event["type"] for event in result] == ["ping_answered", "hypothesis_eliminated"]
    ping_answered, hypothesis_eliminated = result
    assert ping_answered["eliminated"] == 3
    assert hypothesis_eliminated["hyp_id"] == 3
    assert ping_answered["ts"] == hypothesis_eliminated["ts"] == 1200.0
    assert ping_answered["loop_id"] == hypothesis_eliminated["loop_id"] == 1


def test_pick_indexes_the_live_list_not_the_full_list():
    state = seed([events.make("hypothesis_eliminated", ts=5.0, loop_id=1, hyp_id=1)])
    due = schedule.Due(kind="ping", loop_id=1, elapsed=INTERVAL)
    # live list is now [env var missing (2), pg conn pool (3)]; picking 1 means id 2

    result = checkin_module.answers_to_events(
        state.loops[1], due, answer(choice="y", picked=1), now=1200.0
    )
    assert result[-1]["hyp_id"] == 2


def test_a_ping_that_finds_nothing_smaller_eliminates_nothing():
    state = seed()
    due = schedule.Due(kind="ping", loop_id=1, elapsed=INTERVAL)
    result = checkin_module.answers_to_events(state.loops[1], due, answer(choice="y"), now=1200.0)
    assert len(result) == 1
    assert result[0]["eliminated"] is None


def test_a_timed_out_ping_is_recorded_as_unanswered():
    state = seed()
    due = schedule.Due(kind="ping", loop_id=1, elapsed=INTERVAL)
    result = checkin_module.answers_to_events(
        state.loops[1], due, answer(timed_out=True, shown_at=990.0), now=1200.0
    )
    assert result == [
        {"type": "ping_unanswered", "ts": 1200.0, "loop_id": 1, "shown_at": 990.0}
    ]


# --- answers_to_events: checkpoints --------------------------------------


def test_p75_yes_records_on_track_continue():
    state = seed()
    due = schedule.Due(kind="p75", loop_id=1, elapsed=2025.0)
    result = checkin_module.answers_to_events(state.loops[1], due, answer(choice="y"), now=2025.0)
    assert result == [
        {"type": "checkpoint_answered", "ts": 2025.0, "loop_id": 1,
         "kind": "p75", "on_track": True, "decision": "continue"}
    ]


def test_cutting_scope_records_both_conditions():
    state = seed()
    due = schedule.Due(kind="p75", loop_id=1, elapsed=2025.0)
    result = checkin_module.answers_to_events(
        state.loops[1], due,
        answer(choice="c", fields={"new_stop_condition": "comes up once"}),
        now=2025.0,
    )
    assert [event["type"] for event in result] == ["checkpoint_answered", "scope_cut"]
    assert result[0]["decision"] == "cut"
    assert result[0]["on_track"] is False
    assert result[1] == {
        "type": "scope_cut", "ts": 2025.0, "loop_id": 1,
        "old_stop_condition": "comes up clean twice in a row",
        "new_stop_condition": "comes up once",
    }


def test_extending_records_the_new_budget():
    state = seed()
    due = schedule.Due(kind="p75", loop_id=1, elapsed=2025.0)
    result = checkin_module.answers_to_events(
        state.loops[1], due,
        answer(choice="e", fields={"new_budget": "90m", "learned": "pool is fine"}),
        now=2025.0,
    )
    assert [event["type"] for event in result] == ["checkpoint_answered", "budget_extended"]
    assert result[0]["decision"] == "extend"
    assert result[0]["on_track"] is False
    assert result[1]["old_budget_s"] == BUDGET
    assert result[1]["new_budget_s"] == 5400.0
    assert result[1]["learned"] == "pool is fine"


def test_an_unparseable_budget_defers_the_checkpoint():
    state = seed()
    due = schedule.Due(kind="p75", loop_id=1, elapsed=2025.0)
    result = checkin_module.answers_to_events(
        state.loops[1], due,
        answer(choice="e", fields={"new_budget": "soon", "learned": "x"}, shown_at=2020.0),
        now=2025.0,
    )
    # re-fires in ten minutes; never guess the number
    assert result == [
        {"type": "checkpoint_unanswered", "ts": 2025.0, "loop_id": 1,
         "kind": "p75", "shown_at": 2020.0}
    ]


def test_p100_close_records_decision_close_without_a_postmortem():
    state = seed()
    due = schedule.Due(kind="p100", loop_id=1, elapsed=BUDGET)
    result = checkin_module.answers_to_events(state.loops[1], due, answer(choice="x"), now=BUDGET)
    assert result == [
        {"type": "checkpoint_answered", "ts": BUDGET, "loop_id": 1,
         "kind": "p100", "on_track": False, "decision": "close"}
    ]


def test_a_timed_out_checkpoint_is_recorded_as_unanswered():
    state = seed()
    due = schedule.Due(kind="p100", loop_id=1, elapsed=BUDGET)
    result = checkin_module.answers_to_events(
        state.loops[1], due, answer(timed_out=True, shown_at=2690.0), now=BUDGET
    )
    assert result == [
        {"type": "checkpoint_unanswered", "ts": BUDGET, "loop_id": 1,
         "kind": "p100", "shown_at": 2690.0}
    ]


# --- module surface -------------------------------------------------------


def test_cut_and_extend_fields_are_exported():
    assert [f.name for f in checkin_module.CUT_FIELDS] == ["new_stop_condition"]
    assert [f.name for f in checkin_module.EXTEND_FIELDS] == ["new_budget", "learned"]
