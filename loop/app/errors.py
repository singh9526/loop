"""Refusals the front ends render.

Both mean the same thing about the log: nothing was written.
"""

from __future__ import annotations


class LoopError(Exception):
    """A user-facing refusal. The request was well-formed and declined."""


class StaleError(LoopError):
    """What the caller saw is no longer true.

    A check-in ruled out the hypothesis you just clicked; the loop you
    aimed at is no longer active. The refusal is not an error the user
    made — usually the outcome they wanted already happened. Subclasses
    `LoopError` so no caller has to learn about it to be correct.
    """
