import pytest

from loop.blockers.base import Answers
from loop.blockers.fake import FakeBlocker
from loop.core import events, schedule
from loop.sched import tick as tick_module
from loop.store import jsonl

BUDGET = 2700.0
INTERVAL = 1200.0


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))


def answer(choice=None, picked=None, fields=None, timed_out=False):
    return Answers(
        timed_out=timed_out, choice=choice, picked=picked,
        fields=fields or {}, shown_at=0.0, answered_at=0.0,
    )


def seed(extra=(), budget_s=BUDGET, interval_s=INTERVAL):
    jsonl.append(
        events.make(
            "loop_opened", ts=0.0, loop_id=1,
            question="staging deploy fails at startup",
            stop_condition="comes up clean twice in a row",
            budget_s=budget_s, interval_s=interval_s,
            hypotheses=["cert expiry", "env var missing", "pg conn pool"],
            parent_id=None,
        )
    )
    for event in extra:
        jsonl.append(event)


def log():
    return jsonl.read_all()


def state():
    return events.fold(log())


def test_nothing_due_does_nothing():
    seed()
    blocker = FakeBlocker([answer(choice="n")])
    assert tick_module.tick(now=10.0, blocker=blocker) is None
    assert blocker.prompts == []
    assert len(log()) == 1


def test_ping_prompt_carries_title_question_and_live_hypotheses():
    seed([events.make("hypothesis_eliminated", ts=5.0, loop_id=1, hyp_id=2)])
    blocker = FakeBlocker([answer(choice="n")])
    tick_module.tick(now=INTERVAL, blocker=blocker)

    prompt = blocker.prompts[0]
    assert prompt.kind == "ping"
    assert prompt.title == "loop #1 · 20m / 45m"
    assert prompt.question == "hypothesis space smaller than 20m ago?"
    assert [c.key for c in prompt.choices] == ["y", "n"]
    assert prompt.pick_list == ["cert expiry", "pg conn pool"]
    assert prompt.pick_after == "y"
    assert prompt.warning is None
    assert prompt.timeout_s == 300.0


def test_ping_question_uses_the_loops_own_interval():
    seed(interval_s=300.0)  # 5m, not the default 20m
    blocker = FakeBlocker([answer(choice="n")])
    tick_module.tick(now=300.0, blocker=blocker)
    assert "5m" in blocker.prompts[0].question


def test_ping_title_shows_paused_count():
    seed([
        events.make("loop_paused", ts=1.0, loop_id=1, reason="prod incident"),
        events.make(
            "loop_opened", ts=1.0, loop_id=2, question="prod 500s",
            stop_condition="s", budget_s=BUDGET, interval_s=INTERVAL,
            hypotheses=["a"], parent_id=1,
        ),
    ])
    blocker = FakeBlocker([answer(choice="n")])
    tick_module.tick(now=1.0 + INTERVAL, blocker=blocker)
    assert blocker.prompts[0].title.endswith("· 1 paused")


def test_ping_no_logs_a_single_event():
    seed()
    tick_module.tick(now=INTERVAL, blocker=FakeBlocker([answer(choice="n")]))
    last = log()[-1]
    assert last["type"] == "ping_answered"
    assert last["smaller"] is False
    assert last["eliminated"] is None
    assert state().loops[1].pings_answered == 1


def test_ping_yes_with_a_pick_also_eliminates_the_hypothesis():
    seed()
    tick_module.tick(now=INTERVAL, blocker=FakeBlocker([answer(choice="y", picked=3)]))

    kinds = [event["type"] for event in log()[-2:]]
    assert kinds == ["ping_answered", "hypothesis_eliminated"]
    assert log()[-2]["eliminated"] == 3
    assert log()[-1]["hyp_id"] == 3
    assert state().loops[1].eliminated == 1


def test_pick_indexes_the_live_list_not_the_full_list():
    seed([events.make("hypothesis_eliminated", ts=5.0, loop_id=1, hyp_id=1)])
    # live list is now [env var missing (2), pg conn pool (3)]; picking 1 means id 2
    tick_module.tick(now=INTERVAL, blocker=FakeBlocker([answer(choice="y", picked=1)]))
    assert log()[-1]["hyp_id"] == 2


def test_a_timed_out_ping_is_recorded_as_unanswered():
    seed()
    tick_module.tick(now=INTERVAL, blocker=FakeBlocker([answer(timed_out=True)]))
    assert log()[-1]["type"] == "ping_unanswered"
    assert state().loops[1].pings_timed_out == 1


def test_the_thrash_panel_rides_the_ping_prompt():
    actions = [
        events.make("action_logged", ts=float(index), loop_id=1,
                    action=f"a{index}", because="b")
        for index in range(1, 8)
    ]
    # a long budget keeps the checkpoints out of the way; this test is about the ping
    seed(actions, budget_s=99_999.0)
    blocker = FakeBlocker([answer(choice="n")])
    tick_module.tick(now=3120.0, blocker=blocker)  # 52m elapsed

    assert blocker.prompts[0].kind == "ping"
    assert "you are thrashing" in blocker.prompts[0].warning
    assert "7 actions, 0 ruled out, 52m elapsed" in blocker.prompts[0].warning


def test_p75_prompt_offers_continue_cut_and_extend():
    seed([events.make("ping_answered", ts=2020.0, loop_id=1, smaller=True,
                      eliminated=None, shown_at=2020.0)])
    blocker = FakeBlocker([answer(choice="y")])
    tick_module.tick(now=2025.0, blocker=blocker)

    prompt = blocker.prompts[0]
    assert prompt.kind == "p75"
    assert prompt.title == "75% of budget gone · 33m / 45m"
    assert prompt.question == "is 75% of the work done?"
    assert [c.key for c in prompt.choices] == ["y", "c", "e"]
    assert [f.name for f in prompt.fields_after["c"]] == ["new_stop_condition"]
    assert [f.name for f in prompt.fields_after["e"]] == ["new_budget", "learned"]
    assert log()[-2]["type"] == "checkpoint_shown"
    assert log()[-1] == {
        "type": "checkpoint_answered", "ts": log()[-1]["ts"], "loop_id": 1,
        "kind": "p75", "on_track": True, "decision": "continue",
    }


def test_cutting_scope_records_both_conditions():
    seed([events.make("ping_answered", ts=2020.0, loop_id=1, smaller=True,
                      eliminated=None, shown_at=2020.0)])
    blocker = FakeBlocker(
        [answer(choice="c", fields={"new_stop_condition": "comes up once"})]
    )
    tick_module.tick(now=2025.0, blocker=blocker)

    assert log()[-2]["decision"] == "cut"
    assert log()[-2]["on_track"] is False
    assert log()[-1] == {
        "type": "scope_cut", "ts": log()[-1]["ts"], "loop_id": 1,
        "old_stop_condition": "comes up clean twice in a row",
        "new_stop_condition": "comes up once",
    }
    assert state().loops[1].stop_condition == "comes up once"


def test_extending_records_the_new_budget_and_rearms_the_checkpoints():
    seed([events.make("ping_answered", ts=2020.0, loop_id=1, smaller=True,
                      eliminated=None, shown_at=2020.0)])
    blocker = FakeBlocker(
        [answer(choice="e", fields={"new_budget": "90m", "learned": "pool is fine"})]
    )
    tick_module.tick(now=2025.0, blocker=blocker)

    assert [event["type"] for event in log()[-2:]] == [
        "checkpoint_answered", "budget_extended",
    ]
    assert log()[-1]["old_budget_s"] == BUDGET
    assert log()[-1]["new_budget_s"] == 5400.0
    assert log()[-1]["learned"] == "pool is fine"

    current = state()
    assert current.loops[1].budget_s == 5400.0
    assert schedule.next_due(current, now=4050.0).kind == "p75"


def test_an_unparseable_budget_defers_the_checkpoint():
    seed([events.make("ping_answered", ts=2020.0, loop_id=1, smaller=True,
                      eliminated=None, shown_at=2020.0)])
    blocker = FakeBlocker(
        [answer(choice="e", fields={"new_budget": "soon", "learned": "x"})]
    )
    tick_module.tick(now=2025.0, blocker=blocker)

    assert log()[-1]["type"] == "checkpoint_unanswered"
    assert state().loops[1].budget_s == BUDGET
    assert schedule.next_due(state(), now=2025.0 + 600.0).kind == "p75"


def test_p100_offers_close_and_records_it_without_a_postmortem():
    seed([events.make("ping_answered", ts=2690.0, loop_id=1, smaller=True,
                      eliminated=None, shown_at=2690.0)])
    blocker = FakeBlocker([answer(choice="x")])
    tick_module.tick(now=BUDGET, blocker=blocker)

    prompt = blocker.prompts[0]
    assert prompt.kind == "p100"
    assert [c.key for c in prompt.choices] == ["x", "c", "e"]
    assert log()[-1]["decision"] == "close"
    # the loop stays open; the postmortem happens in the terminal, not here
    assert state().loops[1].status == "active"
    assert schedule.next_due(state(), now=BUDGET + 1.0) is None


def test_a_timed_out_checkpoint_is_recorded_and_refires():
    seed([events.make("ping_answered", ts=2690.0, loop_id=1, smaller=True,
                      eliminated=None, shown_at=2690.0)])
    tick_module.tick(now=BUDGET, blocker=FakeBlocker([answer(timed_out=True)]))
    assert log()[-1]["type"] == "checkpoint_unanswered"
    assert schedule.next_due(state(), now=BUDGET + 599.0) is None
    assert schedule.next_due(state(), now=BUDGET + 600.0).kind == "p100"


class ClosesTheLoopWhileAsking(FakeBlocker):
    """A blocker whose `ask` closes the loop the prompt was built for
    before handing back an answer — simulating a human closing/abandoning/
    pausing from another terminal while the overlay was still up."""

    def ask(self, prompt):
        jsonl.append(events.make(
            "loop_closed", ts=999.0, loop_id=1,
            what_was_it="it", giveaway="g", five_min_path="p",
        ))
        return super().ask(prompt)


def test_a_stale_answer_after_the_loop_closes_is_discarded():
    seed()
    blocker = ClosesTheLoopWhileAsking([answer(choice="n")])

    result = tick_module.tick(now=INTERVAL, blocker=blocker)

    assert result is None
    # only the loop_closed the blocker itself appended — no answer events
    assert len(log()) == 2
    assert log()[-1]["type"] == "loop_closed"
