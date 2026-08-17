import pytest

from loop.blockers import base


def test_timeouts_are_the_specified_values():
    assert base.TIMEOUT_S == 300.0


@pytest.mark.parametrize(
    "remaining,expected",
    [(300.0, "5:00"), (192.0, "3:12"), (59.4, "0:59"), (0.0, "0:00"), (-3.0, "0:00")],
)
def test_format_countdown(remaining, expected):
    assert base.format_countdown(remaining) == expected


@pytest.mark.parametrize(
    "remaining,total,expected",
    [(300.0, 300.0, 1.0), (150.0, 300.0, 0.5), (0.0, 300.0, 0.0), (-5.0, 300.0, 0.0)],
)
def test_remaining_fraction(remaining, total, expected):
    assert base.remaining_fraction(remaining, total) == expected


def test_prompt_defaults_are_usable_without_optional_parts():
    prompt = base.Prompt(
        kind="ping",
        title="loop #14 · 42m / 45m",
        question="hypothesis space smaller than 20m ago?",
        choices=[base.Choice("y", "yes"), base.Choice("n", "no")],
    )
    assert prompt.pick_list is None
    assert prompt.pick_after is None
    assert prompt.fields_after == {}
    assert prompt.warning is None
    assert prompt.timeout_s == 300.0
