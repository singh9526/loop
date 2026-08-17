"""Finding and starting the desktop app.

`server_name` is here rather than in `gui/` because the name is derived
from the data directory, not from Qt, and because a module that imports
PySide6 cannot be tested on a machine without it.
"""

from __future__ import annotations

import hashlib

from loop.store import paths


def server_name() -> str:
    """The single-instance socket name for this LOOP_HOME.

    Hashed rather than derived from the path so that two homes — a real
    one and a throwaway under a test's tmp_path — can never collide, and
    so the name survives spaces and separators that Qt would otherwise
    turn into a filesystem path on POSIX.
    """
    digest = hashlib.sha256(str(paths.loop_home()).encode("utf-8")).hexdigest()
    return f"loop-{digest[:16]}"


def available() -> bool:
    """Can this machine run the app?

    Checked before any event is written, preserving the invariant that
    `loop open` never leaves a timer running that nothing can surface a
    prompt for. `find_spec` only locates the modules, it does not import
    them — this stays true to the module docstring's promise that nothing
    here touches PySide6, even transitively.
    """
    from importlib.util import find_spec

    return find_spec("PySide6") is not None and find_spec("loop.gui") is not None


UNAVAILABLE_MESSAGE = (
    "the loop app is not installed, so nothing can show check-ins.\n"
    "  install it with: pip install 'loop-tool[gui]'"
)


def ensure_running() -> None:
    """Start the app if it is not up.

    Unconditional and idempotent: a second copy discovers the running one
    through its socket (`gui.instance.claim`), asks it to surface, and
    exits. Nothing here has to guess, which is why there is no pidfile to
    go stale, and why — unlike `sched.daemon.ensure_running` — this never
    checks liveness itself before spawning: the socket handshake in the
    spawned process is the single source of truth for "is one already
    running," decided after the process starts, not before.
    """
    import subprocess
    import sys

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

    subprocess.Popen([sys.executable, "-m", "loop.gui"], **kwargs)
