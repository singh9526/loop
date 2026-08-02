from loop.core.models import Hypothesis, Loop, State, elapsed_from_intervals


def test_elapsed_of_a_closed_span():
    assert elapsed_from_intervals([[100.0, 160.0]], at=500.0) == 60.0


def test_elapsed_of_an_open_span_runs_to_now():
    assert elapsed_from_intervals([[100.0, None]], at=250.0) == 150.0


def test_paused_time_never_accrues():
    # active 100-160, paused 160-1000, active again 1000-1030
    intervals = [[100.0, 160.0], [1000.0, None]]
    assert elapsed_from_intervals(intervals, at=1030.0) == 90.0


def test_many_pause_resume_cycles_sum_correctly():
    intervals = [[0.0, 10.0], [100.0, 130.0], [500.0, 505.0], [900.0, None]]
    assert elapsed_from_intervals(intervals, at=920.0) == 10.0 + 30.0 + 5.0 + 20.0


def test_no_intervals_is_zero():
    assert elapsed_from_intervals([], at=99.0) == 0.0


def test_at_before_open_span_start_does_not_go_negative():
    assert elapsed_from_intervals([[100.0, None]], at=50.0) == 0.0


def test_loop_live_hypotheses_excludes_killed():
    loop = Loop(
        id=1,
        question="q",
        stop_condition="s",
        budget_s=2700.0,
        interval_s=1200.0,
        parent_id=None,
        opened_at=0.0,
        original_budget_s=2700.0,
        hypotheses=[
            Hypothesis(id=1, text="cert expiry", alive=False),
            Hypothesis(id=2, text="env var missing", alive=True),
        ],
    )
    assert [h.id for h in loop.live_hypotheses()] == [2]


def test_state_active_loop_is_none_when_nothing_active():
    assert State(loops={}, active_id=None).active_loop() is None
