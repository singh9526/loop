"""The append-only event log.

One JSON object per line. Every append in this codebase runs while its
caller holds the exclusive lock from `loop.store.lock` — see
`loop/app/writer.py`, which is the only place that calls `append`. That is
what makes concurrent writers safe on Windows, which offers no equivalent
of POSIX's atomic-append-under-PIPE_BUF.

A crash mid-append can still damage the final line, and `read_all`
tolerates exactly that — permanently, because `append` repairs the tail
before adding to it rather than concatenating onto it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from loop.store import paths

# How much of the tail to read at a time when scanning backwards for the
# last newline. One event is far smaller than this; the loop exists for
# the pathological case, not the normal one.
TAIL_BLOCK = 8192


class CorruptLogError(Exception):
    """An interior line of the log could not be parsed, or the file could
    not be decoded at all. Both carry `<path>: line N ...`, which
    `Controller` and `LogbookView` read the number back out of."""


def append(event: dict, path: Path | None = None) -> None:
    target = path if path is not None else paths.events_path()
    line = json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n"
    _repair_tail(target)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _repair_tail(target: Path) -> None:
    """Make the file end in a newline, so this append starts on its own.

    A crash mid-append can leave the last line without its terminator.
    `read_all` forgives such a tail — but only until the next write:
    without this, the new event would be concatenated onto the partial
    line (the append reports success and the event is silently not
    there), and one more append would push that merged line off the end,
    where it stops being the tail `read_all` forgives and becomes
    permanent interior corruption — no dashboard, no check-ins, every CLI
    command refusing, from a single torn byte.

    Repairing here is safe because `append`'s only caller is
    `loop/app/writer.py`, which holds the exclusive lock across the whole
    mutation: nothing else can be mid-write while this runs.

    A tail that is *whole* JSON and merely unterminated — a crash between
    the event's bytes and its newline — is finished rather than dropped.
    `read_all` returns that event today, so throwing it away would turn a
    repair into data loss. Anything else is the torn tail `read_all`
    already discards, and is truncated away.
    """
    if not target.exists():
        return
    with target.open("r+b") as handle:
        start, tail = _tail_after_last_newline(handle)
        if not tail:
            return
        if _is_whole_line(tail):
            handle.seek(0, os.SEEK_END)
            handle.write(b"\n")
        else:
            handle.truncate(start)
        handle.flush()
        os.fsync(handle.fileno())


def _tail_after_last_newline(handle) -> tuple[int, bytes]:
    """`(offset, bytes)` of whatever follows the file's final newline.
    `(size, b"")` when the file is empty or already ends in one."""
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    if size == 0:
        return 0, b""

    position = size
    while position > 0:
        start = max(0, position - TAIL_BLOCK)
        handle.seek(start)
        chunk = handle.read(position - start)
        index = chunk.rfind(b"\n")
        if index != -1:
            offset = start + index + 1
            handle.seek(offset)
            return offset, handle.read()
        position = start

    handle.seek(0)
    return 0, handle.read()          # no newline anywhere in the file


def _is_whole_line(tail: bytes) -> bool:
    """Exactly what `read_all` would make of this line if it were
    terminated: parseable JSON, or a blank line it skips."""
    try:
        text = tail.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if not text.strip():
        return False                 # blank: nothing to preserve, drop it
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return False
    return True


def read_all(path: Path | None = None) -> list[dict]:
    target = path if path is not None else paths.events_path()
    if not target.exists():
        return []

    raw = _decoded(target).splitlines()
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


def _decoded(target: Path) -> str:
    """The log as text, with undecodable bytes reported the way every
    caller already knows how to handle.

    A `UnicodeDecodeError` is a `ValueError`, not a `CorruptLogError`, so
    it slips past `Controller.refresh` and `Scheduler.tick` — the
    dashboard freezes with no unreadable banner and the check-ins stop
    silently. The line number is recovered from the byte offset so the
    banner points at the damage like it does for unparseable JSON.

    No tolerance for an undecodable *tail*, unlike an unparseable one:
    `json.dumps` escapes every non-ASCII character (`·` is written
    `\\u00b7`), so a torn write of ours can only ever leave valid UTF-8.
    An undecodable byte means damage from outside this program, and the
    banner is the right answer to that.
    """
    data = target.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        line = data[: exc.start].count(b"\n") + 1
        raise CorruptLogError(f"{target}: line {line} is not valid UTF-8") from exc
