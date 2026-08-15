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
