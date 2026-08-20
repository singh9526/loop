import pytest

from loop.core.timefmt import (
    format_clock, format_duration, format_mmss, parse_duration,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("45m", 2700.0),
        ("2h", 7200.0),
        ("1h30m", 5400.0),
        ("90s", 90.0),
        ("45", 2700.0),          # bare number means minutes
        (" 45m ", 2700.0),
        ("1H30M", 5400.0),
    ],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "-5m", "0", "5x", "m30"])
def test_parse_duration_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_duration(text)


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0.0, "0m"),
        (59.0, "0m"),
        (60.0, "1m"),
        (2700.0, "45m"),
        (3600.0, "1h"),
        (5400.0, "1h30m"),
        (90000.0, "1d1h"),
    ],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0.0, "0:00"),
        (9.0, "0:09"),
        (59.9, "0:59"),
        (60.0, "1:00"),
        (1122.0, "18:42"),
        (2700.0, "45:00"),
        (-5.0, "0:00"),         # a negative remainder is zero, not "-1:55"
    ],
)
def test_format_mmss(seconds, expected):
    """The burn clock's own format. `format_duration` rounds to whole
    minutes, which is the wrong resolution for something you watch."""
    assert format_mmss(seconds) == expected


def test_format_clock_is_local_wall_time_to_the_minute():
    """The action log's left column. Asserted against `time.localtime` in
    the running process's own zone, because that is the point — a UTC
    timestamp beside a wall clock is a bug the user reads as a bug."""
    import time

    ts = 1_700_000_000.0
    expected = time.strftime("%H:%M", time.localtime(ts))
    assert format_clock(ts) == expected
    assert len(format_clock(ts)) == 5 and format_clock(ts)[2] == ":"
