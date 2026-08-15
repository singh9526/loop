"""Cross-process exclusive locking for the write path.

The lock lives in a file of its own, never the event log: taking a lock on
a file that is simultaneously opened in append mode and closed on every
write is a race waiting to be found.

Both platforms poll a non-blocking acquire rather than using a blocking
one, so `timeout_s` means the same thing on each. `flock` would otherwise
wait forever and `msvcrt.locking`'s LK_LOCK would impose its own ten
one-second retries.
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from pathlib import Path

POLL_S = 0.02


class LockTimeout(Exception):
    """Another process held the write lock past the timeout."""


if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
    import msvcrt

    def _try_acquire(handle) -> bool:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _release(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_acquire(handle) -> bool:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def _release(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive(path: Path, timeout_s: float = 10.0):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    with path.open("a+b") as handle:
        handle.seek(0)
        while not _try_acquire(handle):
            if time.monotonic() >= deadline:
                raise LockTimeout(
                    f"another loop process held the write lock for more than "
                    f"{timeout_s:g}s ({path})"
                )
            time.sleep(POLL_S)
        try:
            yield
        finally:
            _release(handle)
