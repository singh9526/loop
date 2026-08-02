import pytest

from loop.core.timefmt import format_duration, parse_duration


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
