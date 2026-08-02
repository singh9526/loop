from loop.core import events, schedule

BUDGET = 2700.0     # 45m
INTERVAL = 1200.0   # 20m


def opened(ts=0.0, loop_id=1, parent_id=None, budget_s=BUDGET):
    return events.make(
        "loop_opened",
        ts=ts,
        loop_id=loop_id,
        question="staging deploy fails at startup",
        stop_condition="comes up clean twice in a row",
        budget_s=budget_s,
        interval_s=INTERVAL,
        hypotheses=["cert expiry", "env var missing", "pg conn pool"],
        parent_id=parent_id,
    )


def due_for(log, now):
    return schedule.next_due(events.fold(log), now)


def test_nothing_is_due_before_the_first_interval():
    assert due_for([opened()], now=1199.0) is None


def test_ping_is_due_at_the_interval():
    due = due_for([opened()], now=1200.0)
    assert due.kind == "ping"
    assert due.loop_id == 1
    assert due.elapsed == 1200.0


def test_no_active_loop_means_nothing_is_due():
    log = [opened(), events.make("loop_paused", ts=100.0, loop_id=1, reason="lunch")]
    assert due_for(log, now=99_999.0) is None


def test_paused_time_never_makes_a_ping_due():
    log = [opened(), events.make("loop_paused", ts=60.0, loop_id=1, reason="lunch")]
    log.append(events.make("loop_resumed", ts=100_000.0, loop_id=1))
    # 60s of active time before the pause, 100s after it
    assert due_for(log, now=100_100.0) is None


def test_resume_rebases_the_next_ping():
    log = [
        opened(),
        events.make("ping_answered", ts=1200.0, loop_id=1, smaller=True,
                    eliminated=1, shown_at=1200.0),
        events.make("hypothesis_eliminated", ts=1200.0, loop_id=1, hyp_id=1),
        events.make("loop_paused", ts=2300.0, loop_id=1, reason="prod incident"),
        events.make("loop_resumed", ts=90_000.0, loop_id=1),
    ]
    # elapsed at resume is 2300s; a ping must not fire until 3500s of active time
    assert due_for(log, now=90_000.0 + 1199.0) is None
    assert due_for(log, now=90_000.0 + 1200.0).kind == "ping"


def test_sleep_and_wake_fires_exactly_one_ping_then_rebases():
    # 8h budget ensures p75 lands at 21600s, well after 4h sleep jump at 14400s.
    # Tests that one ping fires after long sleep, answering it rebases interval.
    log = [opened(budget_s=28800.0)]
    now = 4.0 * 3600.0  # machine slept for four hours while the loop was active

    first = due_for(log, now)
    assert first.kind == "ping"

    log.append(
        events.make("ping_answered", ts=now, loop_id=1, smaller=False,
                    eliminated=None, shown_at=now)
    )
    assert due_for(log, now) is None
    assert due_for(log, now + 1199.0) is None
    assert due_for(log, now + 1200.0).kind == "ping"


def test_machine_sleep_while_active_still_accrues_elapsed():
    # No pause event, so the four-hour gap counts. p100 is long overdue.
    assert due_for([opened()], now=4.0 * 3600.0).kind in ("ping", "p100")


def test_p75_fires_at_three_quarters_of_the_budget():
    log = [
        opened(),
        events.make("ping_answered", ts=2020.0, loop_id=1, smaller=True,
                    eliminated=None, shown_at=2020.0),
    ]
    assert due_for(log, now=2024.0) is None
    assert due_for(log, now=2025.0).kind == "p75"


def test_p100_wins_when_both_checkpoints_are_due():
    assert due_for([opened()], now=2700.0).kind == "p100"


def test_an_answered_checkpoint_does_not_fire_again():
    log = [
        opened(),
        events.make("checkpoint_answered", ts=2025.0, loop_id=1, kind="p75",
                    on_track=True, decision="continue"),
        events.make("ping_answered", ts=2025.0, loop_id=1, smaller=True,
                    eliminated=None, shown_at=2025.0),
    ]
    assert due_for(log, now=2100.0) is None


def test_a_timed_out_checkpoint_refires_after_ten_minutes():
    log = [
        opened(),
        events.make("ping_answered", ts=2020.0, loop_id=1, smaller=True,
                    eliminated=None, shown_at=2020.0),
        events.make("checkpoint_unanswered", ts=2025.0, loop_id=1, kind="p75",
                    shown_at=2025.0),
    ]
    assert due_for(log, now=2025.0 + 599.0) is None
    assert due_for(log, now=2025.0 + 600.0).kind == "p75"


def test_p75_is_moot_once_p100_has_been_passed_and_answered():
    log = [
        opened(),
        events.make("checkpoint_answered", ts=2700.0, loop_id=1, kind="p100",
                    on_track=False, decision="close"),
        events.make("ping_answered", ts=2700.0, loop_id=1, smaller=True,
                    eliminated=None, shown_at=2700.0),
    ]
    # p75's boundary is also behind us and was never answered, but the budget
    # is gone — asking the 75% question now would be noise.
    assert due_for(log, now=2800.0) is None


def test_p75_is_suppressed_while_p100_is_inside_its_retry_backoff():
    log = [
        opened(),
        events.make("ping_answered", ts=2690.0, loop_id=1, smaller=True,
                    eliminated=None, shown_at=2690.0),
        events.make("checkpoint_unanswered", ts=2700.0, loop_id=1, kind="p100",
                    shown_at=2700.0),
    ]
    assert due_for(log, now=2700.0 + 599.0) is None
    assert due_for(log, now=2700.0 + 600.0).kind == "p100"


def test_a_missed_checkpoint_fires_as_soon_as_the_loop_is_active_again():
    log = [
        opened(),
        events.make("loop_paused", ts=1000.0, loop_id=1, reason="prod incident"),
        events.make("loop_resumed", ts=50_000.0, loop_id=1),
    ]
    # elapsed crosses 2025s only after 1025s of the resumed span
    assert due_for(log, now=50_000.0 + 1024.0) is None
    assert due_for(log, now=50_000.0 + 1025.0).kind == "p75"


def test_a_ping_within_three_minutes_of_a_checkpoint_is_skipped():
    # ping answered at 720s, so the next ping is due at 1920s; p75 lands at 2025s
    log = [
        opened(),
        events.make("ping_answered", ts=720.0, loop_id=1, smaller=True,
                    eliminated=None, shown_at=720.0),
    ]
    assert due_for(log, now=1920.0) is None      # 105s before p75 — suppressed
    assert due_for(log, now=2024.0) is None
    assert due_for(log, now=2025.0).kind == "p75"


def test_a_ping_more_than_three_minutes_before_a_checkpoint_still_fires():
    # ping answered at 600s, next ping at 1800s, which is 225s before p75
    log = [
        opened(),
        events.make("ping_answered", ts=600.0, loop_id=1, smaller=True,
                    eliminated=None, shown_at=600.0),
    ]
    assert due_for(log, now=1800.0).kind == "ping"


def test_extension_rearms_both_checkpoints():
    log = [
        opened(),
        events.make("checkpoint_answered", ts=2025.0, loop_id=1, kind="p75",
                    on_track=False, decision="extend"),
        events.make("budget_extended", ts=2025.0, loop_id=1, old_budget_s=BUDGET,
                    new_budget_s=5400.0, learned="pool is not the issue"),
        events.make("ping_answered", ts=2025.0, loop_id=1, smaller=False,
                    eliminated=None, shown_at=2025.0),
    ]
    assert due_for(log, now=4049.0) is None            # before the new p75
    assert due_for(log, now=4050.0).kind == "p75"      # 0.75 * 5400
    log.append(
        events.make("checkpoint_answered", ts=4050.0, loop_id=1, kind="p75",
                    on_track=True, decision="continue")
    )
    assert due_for(log, now=5400.0).kind == "p100"


def test_several_pause_resume_cycles_sum_towards_the_budget():
    log = [
        opened(),
        events.make("loop_paused", ts=1000.0, loop_id=1, reason="a"),
        events.make("loop_resumed", ts=10_000.0, loop_id=1),
        events.make("loop_paused", ts=11_000.0, loop_id=1, reason="b"),
        events.make("loop_resumed", ts=20_000.0, loop_id=1),
    ]
    state = events.fold(log)
    assert schedule.active_elapsed(state.loops[1], now=20_700.0) == 2700.0
    assert schedule.next_due(state, now=20_700.0).kind == "p100"
