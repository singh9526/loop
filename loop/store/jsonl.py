"""The append-only event log.

One JSON object per line. Appends are a single `write` of a line, which is
atomic on POSIX for payloads under PIPE_BUF, so a crash can damage at most
the final line.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from loop.store import paths


class CorruptLogError(Exception):
    """An interior line of the log could not be parsed."""


def append(event: dict, path: Path | None = None) -> None:
    target = path if path is not None else paths.events_path()
    line = json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def read_all(path: Path | None = None) -> list[dict]:
    target = path if path is not None else paths.events_path()
    if not target.exists():
        return []

    raw = target.read_text(encoding="utf-8").splitlines()
    events: list[dict] = []
    for index, line in enumerate(raw, start=1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            if index == len(raw):
                break  # truncated tail from a crash mid-append
            raise CorruptLogError(f"{target}: line {index} is not valid JSON") from exc
    return events
