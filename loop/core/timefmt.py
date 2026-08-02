"""Duration strings to seconds and back.

A bare number means minutes, because the tool's unit of thought is minutes
and `--budget 45` should not silently mean 45 seconds.
"""

from __future__ import annotations

import re

_UNITS = {"h": 3600.0, "m": 60.0, "s": 1.0}
_PATTERN = re.compile(r"(\d+)\s*([hms])", re.IGNORECASE)


def parse_duration(text: str) -> float:
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("empty duration")

    if cleaned.isdigit():
        seconds = float(cleaned) * 60.0
    else:
        matches = _PATTERN.findall(cleaned)
        if not matches or _PATTERN.sub("", cleaned).strip():
            raise ValueError(f"cannot parse duration: {text!r}")
        seconds = sum(float(value) * _UNITS[unit.lower()] for value, unit in matches)

    if seconds <= 0:
        raise ValueError(f"duration must be positive: {text!r}")
    return seconds


def format_duration(seconds: float) -> str:
    total_minutes = int(seconds // 60)
    days, rest = divmod(total_minutes, 1440)
    hours, minutes = divmod(rest, 60)

    if days:
        return f"{days}d{hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h{minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"
