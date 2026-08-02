"""Pick a blocker for this machine.

`LOOP_BLOCKER` overrides everything, which is how tests and day-to-day
development avoid a full-screen window every twenty minutes.
"""

from __future__ import annotations

import os
import sys


def _tk_blocker():
    from loop.blockers.tk import TkBlocker

    return TkBlocker()


def _macos_blocker():
    from loop.blockers.macos import MacOSBlocker

    return MacOSBlocker()


def _fake_blocker():
    from loop.blockers.fake import from_env

    return from_env()


def get_blocker():
    override = os.environ.get("LOOP_BLOCKER")
    if override == "fake":
        return _fake_blocker()
    if override == "tk":
        return _tk_blocker()
    if override == "macos":
        return _macos_blocker()
    if override:
        raise ValueError(f"unknown LOOP_BLOCKER: {override!r}")

    if sys.platform == "darwin":
        try:
            return _macos_blocker()
        except ImportError:
            print(
                "  pyobjc not installed — using a soft block. "
                "install with: pip install 'loop-tool[macos]'",
                file=sys.stderr,
            )
    return _tk_blocker()
