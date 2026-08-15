import pytest

from loop.app import view
from loop.core import events


def opened(loop_id=1, parent_id=None, budget_s=2700.0, question="q"):
    return events.make(
        "loop_opened", ts=0.0, loop_id=loop_id, question=question,
        stop_condition="comes up clean twice", budget_s=budget_s,
        interval_s=1200.0, hypotheses=["cert expiry", "pg pool"],
        parent_id=parent_id,
    )


def build(log, now):
    return view.build(events.fold(log), log, now)


def test_an_empty_log_offers_only_open():
    dashboard = build([], 0.0)
    assert dashboard.active_id is None
    assert dashboard.meter is None
    assert dashboard.enabled_actions == frozenset({"open"})
    assert dashboard.status.state == "idle"


def test_an_active_loop_offers_everything_but_resume():
    dashboard = build([opened()], 600.0)
    assert dashboard.active_id == 1
    assert dashboard.question == "q"
    assert dashboard.stop_condition == "comes up clean twice"
    assert "resume" not in dashboard.enabled_actions
    assert {"try", "hyp_add", "hyp_kill", "pause", "cut", "extend", "close", "abandon"} \
        <= dashboard.enabled_actions
    assert dashboard.status.state == "armed"


def test_the_meter_reads_elapsed_against_budget():
    dashboard = build([opened()], 1350.0)
    assert dashboard.meter.elapsed_s == 1350.0
    assert dashboard.meter.budget_s == 2700.0
    assert dashboard.meter.fraction == pytest.approx(0.5)
    assert dashboard.meter.over_s == 0.0


def test_the_meter_clamps_and_reports_the_overrun_separately():
    dashboard = build([opened()], 3000.0)
    assert dashboard.meter.fraction == 1.0
    assert dashboard.meter.over_s == 300.0


def test_next_checkin_counts_down_to_the_ping():
    dashboard = build([opened()], 600.0)
    assert dashboard.meter.next_checkin_s == pytest.approx(600.0)


def test_next_checkin_is_none_once_the_budget_is_gone():
    """Nothing is scheduled ahead; p100 is due now and the strip says so."""
    dashboard = build([opened()], 2800.0)
    assert dashboard.meter.next_checkin_s is None


def test_paused_parents_sit_above_the_active_loop_most_recent_first():
    log = [
        opened(1, question="first"),
        events.make("loop_paused", ts=100.0, loop_id=1, reason="x"),
        opened(2, parent_id=1, question="second"),
        events.make("loop_paused", ts=200.0, loop_id=2, reason="y"),
        opened(3, parent_id=2, question="third"),
    ]
    dashboard = build(log, 300.0)
    assert [(row.loop_id, row.active) for row in dashboard.stack] == [
        (2, False), (1, False), (3, True),
    ]
    assert dashboard.status.depth == 3


def test_a_loop_paused_over_a_day_is_marked_stale():
    log = [opened(1), events.make("loop_paused", ts=100.0, loop_id=1, reason="x")]
    assert build(log, 100.0 + 86_400.0).stack[0].stale is True
    assert build(log, 100.0 + 3600.0).stack[0].stale is False


def test_a_paused_stack_with_nothing_active_offers_resume_not_try():
    log = [opened(1), events.make("loop_paused", ts=100.0, loop_id=1, reason="x")]
    dashboard = build(log, 200.0)
    assert dashboard.active_id is None
    assert "resume" in dashboard.enabled_actions
    assert "try" not in dashboard.enabled_actions
    assert dashboard.status.state == "paused"


def test_ruled_out_hypotheses_stay_visible_and_counted():
    log = [opened(), events.make("hypothesis_eliminated", ts=50.0, loop_id=1, hyp_id=1)]
    dashboard = build(log, 100.0)
    assert [(row.hyp_id, row.alive) for row in dashboard.hypotheses] == [(1, False), (2, True)]
    assert dashboard.live_count == 1
    assert dashboard.dead_count == 1


def test_the_thrash_banner_shows_the_counts_that_fired_it():
    log = [opened()] + [
        events.make("action_logged", ts=float(index), loop_id=1,
                    action=f"a{index}", because="b")
        for index in range(5)
    ]
    dashboard = build(log, 2000.0)
    assert dashboard.thrash.title == "You are thrashing."
    assert dashboard.thrash.why == "5 actions · 0 ruled out · 33m elapsed"


def test_no_thrash_banner_when_the_detector_is_quiet():
    assert build([opened()], 600.0).thrash is None


def test_the_timeline_is_the_active_loops_entries():
    log = [opened(), events.make("action_logged", ts=5.0, loop_id=1,
                                 action="added print", because="shows the frame")]
    assert [entry.text for entry in build(log, 100.0).timeline] == ["added print"]


def test_unreadable_keeps_the_last_good_view_and_disables_everything():
    good = build([opened()], 600.0)
    frozen = view.unreadable(good, path="~/.loop/events.jsonl", line=214, frozen_at=600.0)
    assert frozen.enabled_actions == frozenset()
    assert frozen.active_id == 1
    assert frozen.meter.elapsed_s == 600.0     # the clock stops where it stopped
    assert frozen.status.state == "unreadable"
    assert frozen.error == "~/.loop/events.jsonl:214"


def test_unreadable_with_no_previous_view_still_disables_everything():
    frozen = view.unreadable(None, path="p", line=1, frozen_at=0.0)
    assert frozen.enabled_actions == frozenset()
    assert frozen.active_id is None
    assert frozen.meter is None
