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
import threading
import time

from loop.blockers.base import Blocker
from loop.blockers.factory import BlockerUnavailable
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
    blocker: Blocker | None = None,
    sleep_fn=time.sleep,
    now_fn=time.time,
) -> None:
    pid = os.getpid()
    try:
        while True:
            state = events.fold(jsonl.read_all())
            if state.active_id is None:
                return

            try:
                _write_heartbeat(pid, now_fn())
            except OSError as exc:
                print(f"loop daemon: heartbeat write failed: {exc}", file=sys.stderr)
                return

            # `tick()` can block in `blocker.ask()` for minutes waiting on
            # a human — far longer than STALE_AFTER_S. Keep the heartbeat
            # fresh for the duration so a slow answer doesn't make
            # `is_running()` think this daemon has died and let another
            # `loop open`/`loop resume` spawn a second one.
            stop_pulse = threading.Event()
            pulse = threading.Thread(
                target=_pulse_loop, args=(pid, stop_pulse, poll_s, now_fn), daemon=True,
            )
            pulse.start()
            try:
                tick(now=now_fn(), blocker=blocker)
            except BlockerUnavailable as exc:
                print(f"loop daemon: {exc}", file=sys.stderr)
                return
            finally:
                stop_pulse.set()
                pulse.join(timeout=poll_s)

            sleep_fn(poll_s)

            if not _owns_pidfile(pid):
                return
    finally:
        if _owns_pidfile(pid):
            stop()


def _pulse_loop(
    pid: int, stop_event: threading.Event, poll_s: float, now_fn
) -> None:
    """Runs in a background thread while a tick blocks on a human answer.

    Refreshes the heartbeat every `poll_s` seconds, real time — this is
    the one place in the module where waiting on the wall clock rather
    than an injected `sleep_fn` is correct, since it exists specifically
    to outlast a call the main loop does not control the duration of.
    Stops the moment `stop_event` is set, or on its own the moment the
    pidfile is gone or no longer names this PID.
    """
    while not stop_event.wait(poll_s):
        if not _pulse_once(pid, now_fn):
            return


def _pulse_once(pid: int, now_fn) -> bool:
    """One heartbeat refresh, through the same `_write_heartbeat` path
    the main loop uses. Returns False without writing if this process no
    longer owns the pidfile — gone or claimed by someone else — so the
    pulse never recreates a file `stop()` deleted."""
    if not _owns_pidfile(pid):
        return False
    _write_heartbeat(pid, now_fn())
    return True


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
