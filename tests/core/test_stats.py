from loop.core import events, stats

BUDGET = 2700.0
INTERVAL = 1200.0


def opened(loop_id, ts, budget_s=BUDGET, parent_id=None):
    return events.make(
        "loop_opened", ts=ts, loop_id=loop_id, question=f"q{loop_id}",
        stop_condition="s", budget_s=budget_s, interval_s=INTERVAL,
        hypotheses=["a", "b", "c"], parent_id=parent_id,
    )


def ping(loop_id, ts, smaller=True):
    return events.make("ping_answered", ts=ts, loop_id=loop_id, smaller=smaller,
                       eliminated=None, shown_at=ts)


def closed(loop_id, ts):
    return events.make("loop_closed", ts=ts, loop_id=loop_id, what_was_it="w",
                       giveaway="g", five_min_path="p")


def test_empty_log_reports_zeroes():
    report = stats.compute([], now=0.0)
    assert report["followed"]["count"] == 0
    assert report["abandoned"]["count"] == 0
    assert report["ping_response"] == {"answered": 0, "timed_out": 0}


def test_a_clean_closed_loop_is_followed_and_resolved():
    log = [opened(1, 0.0), ping(1, 1200.0), closed(1, 1800.0)]
    report = stats.compute(log, now=1800.0)
    assert report["followed"]["count"] == 1
    assert report["followed"]["median_s"] == 1800.0
    assert report["followed"]["resolved_pct"] == 100.0
    assert report["abandoned"]["count"] == 0


def test_a_ping_timeout_moves_the_loop_to_abandoned_protocol():
    log = [
        opened(1, 0.0),
        events.make("ping_unanswered", ts=1200.0, loop_id=1, shown_at=1200.0),
        closed(1, 1800.0),
    ]
    report = stats.compute(log, now=1800.0)
    assert report["followed"]["count"] == 0
    assert report["abandoned"]["count"] == 1
    assert report["abandoned"]["resolved_pct"] == 100.0


def test_a_checkpoint_timeout_also_counts_as_abandoned_protocol():
    log = [
        opened(1, 0.0),
        events.make("checkpoint_unanswered", ts=2025.0, loop_id=1, kind="p75",
                    shown_at=2025.0),
        events.make("loop_abandoned", ts=2100.0, loop_id=1),
    ]
    report = stats.compute(log, now=2100.0)
    assert report["abandoned"]["count"] == 1
    assert report["abandoned"]["resolved_pct"] == 0.0


def test_a_caught_checkpoint_retry_still_counts_as_abandoned_protocol():
    # The checkpoint times out, the 10-minute retry fires, and the user
    # answers it — checkpoints_pending ends up empty, but the timeout
    # happened, and the log is not allowed to un-happen it.
    log = [
        opened(1, 0.0),
        events.make("checkpoint_unanswered", ts=2025.0, loop_id=1, kind="p75",
                    shown_at=2025.0),
        events.make("checkpoint_answered", ts=2700.0, loop_id=1, kind="p75",
                    on_track=True, decision="continue"),
        closed(1, 2800.0),
    ]
    report = stats.compute(log, now=2800.0)
    assert report["abandoned"]["count"] == 1
    assert report["followed"]["count"] == 0


def test_a_loop_with_zero_pings_is_still_classified():
    report = stats.compute([opened(1, 0.0), closed(1, 300.0)], now=300.0)
    assert report["followed"]["count"] == 1


def test_open_loops_are_excluded():
    assert stats.compute([opened(1, 0.0)], now=600.0)["followed"]["count"] == 0


def test_median_uses_active_elapsed_not_wall_clock():
    log = [
        opened(1, 0.0),
        events.make("loop_paused", ts=600.0, loop_id=1, reason="lunch"),
        events.make("loop_resumed", ts=200_000.0, loop_id=1),
        closed(1, 200_600.0),
    ]
    assert stats.compute(log, now=200_600.0)["followed"]["median_s"] == 1200.0


def test_estimate_drift_measures_the_original_budget():
    log = [
        opened(1, 0.0),
        events.make("checkpoint_answered", ts=2025.0, loop_id=1, kind="p75",
                    on_track=False, decision="extend"),
        events.make("budget_extended", ts=2025.0, loop_id=1, old_budget_s=BUDGET,
                    new_budget_s=5400.0, learned="x"),
        closed(1, 5400.0),
    ]
    drift = stats.compute(log, now=5400.0)["estimate_drift"]
    assert drift["median_pct"] == 100.0          # 5400s actual against a 2700s promise
    assert drift["extensions"] == 1
    assert drift["loops_with_extensions"] == 1


def test_ping_response_totals_across_loops():
    log = [
        opened(1, 0.0), ping(1, 1200.0),
        events.make("ping_unanswered", ts=2400.0, loop_id=1, shown_at=2400.0),
        closed(1, 2500.0),
    ]
    assert stats.compute(log, now=2500.0)["ping_response"] == {
        "answered": 1, "timed_out": 1,
    }


def test_interruption_metrics():
    log = [
        opened(1, 0.0),
        events.make("loop_paused", ts=600.0, loop_id=1, reason="prod"),
        opened(2, 600.0, parent_id=1),
        events.make("loop_closed", ts=900.0, loop_id=2, what_was_it="w",
                    giveaway="g", five_min_path="p"),
        events.make("loop_resumed", ts=900.0, loop_id=1),
        closed(1, 1200.0),
    ]
    interruptions = stats.compute(log, now=1200.0)["interruptions"]
    assert interruptions["median_pauses"] == 0.5   # loop 1 paused once, loop 2 never
    assert interruptions["median_pause_s"] == 300.0
    assert interruptions["max_depth"] == 2


def test_thrash_episodes_count_contiguous_stretches_not_pings():
    actions = [
        events.make("action_logged", ts=float(index), loop_id=1,
                    action=f"a{index}", because="b")
        for index in range(1, 8)
    ]
    log = [opened(1, 0.0, budget_s=99_999.0), *actions]
    log += [ping(1, 1900.0, smaller=False), ping(1, 3100.0, smaller=False)]
    log += [events.make("hypothesis_eliminated", ts=3200.0, loop_id=1, hyp_id=1)]
    log += [ping(1, 4400.0, smaller=True), closed(1, 4500.0)]

    episodes = stats.compute(log, now=4500.0)["thrash_episodes"]
    assert episodes["total"] == 1


def test_render_produces_the_documented_lines():
    lines = stats.render(stats.compute([opened(1, 0.0), closed(1, 600.0)], now=600.0))
    joined = "\n".join(lines)
    for label in (
        "protocol followed", "protocol abandoned", "thrash episodes",
        "estimate drift", "ping response", "interruptions",
    ):
        assert label in joined
