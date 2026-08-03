"""The detached poller.

A singleton, not bound to a loop id: it services whatever loop is active
and exits when none is. It holds no state between ticks — every tick
re-reads the log — so pausing, resuming, stacking, and closing from any
terminal all work without signalling it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from loop.core import events
from loop.sched.tick import tick
from loop.store import jsonl, paths

POLL_S = 10.0
STALE_AFTER_S = 3 * POLL_S


def is_running() -> bool:
    record = _read_pidfile()
    if record is None:
        return False
    return time.time() - record.get("heartbeat", 0.0) < STALE_AFTER_S


def ensure_running() -> None:
    if not is_running():
        _spawn()


def stop() -> None:
    paths.pid_path().unlink(missing_ok=True)


def run(
    *,
    poll_s: float = POLL_S,
    blocker=None,
    sleep_fn=time.sleep,
    now_fn=time.time,
) -> None:
    pid = os.getpid()
    try:
        while True:
            state = events.fold(jsonl.read_all())
            if state.active_id is None:
                return

            _write_heartbeat(pid, now_fn())
            tick(now=now_fn(), blocker=blocker)
            sleep_fn(poll_s)

            if not _owns_pidfile(pid):
                return
    finally:
        if _owns_pidfile(pid):
            stop()


def _spawn() -> None:
    """The only platform branch outside `loop/blockers/`."""
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True

    subprocess.Popen([sys.executable, "-m", "loop.sched.daemon"], **kwargs)


def _read_pidfile() -> dict | None:
    path = paths.pid_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_heartbeat(pid: int, at: float) -> None:
    paths.pid_path().write_text(
        json.dumps({"pid": pid, "heartbeat": at}), encoding="utf-8"
    )


def _owns_pidfile(pid: int) -> bool:
    record = _read_pidfile()
    return record is not None and record.get("pid") == pid


if __name__ == "__main__":  # pragma: no cover
    run()
