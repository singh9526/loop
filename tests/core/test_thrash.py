from loop.core import thrash
from loop.core.models import Loop


def make_loop(actions=0, eliminated=0, answers=()):
    return Loop(
        id=14,
        question="staging deploy fails at startup",
        stop_condition="comes up clean twice in a row",
        budget_s=2700.0,
        interval_s=1200.0,
        parent_id=None,
        opened_at=0.0,
        original_budget_s=2700.0,
        actions=actions,
        eliminated=eliminated,
        recent_ping_answers=list(answers),
    )


def test_quiet_when_nothing_is_wrong():
    assert thrash.detect(make_loop(actions=2, eliminated=1), elapsed=600.0).firing is False


def test_fires_on_many_actions_and_no_eliminations():
    result = thrash.detect(make_loop(actions=7, eliminated=0), elapsed=3120.0)
    assert result.firing is True
    assert "7 actions" in result.panel
    assert "0 ruled out" in result.panel
    assert "52m elapsed" in result.panel
    assert "you are thrashing" in result.panel


def test_does_not_fire_one_second_under_thirty_minutes():
    assert thrash.detect(make_loop(actions=7), elapsed=1799.0).firing is False


def test_fires_exactly_at_thirty_minutes():
    assert thrash.detect(make_loop(actions=5), elapsed=1800.0).firing is True


def test_does_not_fire_with_four_actions():
    assert thrash.detect(make_loop(actions=4), elapsed=3600.0).firing is False


def test_an_elimination_clears_the_action_condition():
    assert thrash.detect(make_loop(actions=9, eliminated=1), elapsed=3600.0).firing is False


def test_fires_on_three_consecutive_negative_pings():
    result = thrash.detect(make_loop(answers=[True, False, False, False]), elapsed=600.0)
    assert result.firing is True
    assert "3 pings" in result.panel


def test_two_negative_pings_are_not_enough():
    assert thrash.detect(make_loop(answers=[False, False]), elapsed=600.0).firing is False


def test_a_positive_ping_breaks_the_streak():
    loop = make_loop(answers=[False, False, True])
    assert thrash.detect(loop, elapsed=600.0).firing is False


def test_a_long_pause_does_not_push_a_loop_over_the_threshold():
    # 25 active minutes, regardless of how long the loop sat paused
    assert thrash.detect(make_loop(actions=8), elapsed=1500.0).firing is False
