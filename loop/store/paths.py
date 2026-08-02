"""Resolve the data directory for each platform.

`LOOP_HOME` overrides everything. Tests rely on that override, so it is
checked before any platform logic.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def loop_home() -> Path:
    """Return the data directory, creating it if it does not exist."""
    override = os.environ.get("LOOP_HOME")
    if override:
        home = Path(override)
    elif sys.platform == "darwin":
        home = Path.home() / ".loop"
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA")
        home = (Path(base) if base else Path.home() / "AppData" / "Roaming") / "loop"
    else:
        base = os.environ.get("XDG_DATA_HOME")
        home = (Path(base) if base else Path.home() / ".local" / "share") / "loop"

    home.mkdir(parents=True, exist_ok=True)
    return home


def events_path() -> Path:
    return loop_home() / "events.jsonl"


def pid_path() -> Path:
    return loop_home() / "daemon.pid"
