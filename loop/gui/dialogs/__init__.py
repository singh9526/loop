"""Dialogs: one module per family.

`base` is the one form every dialog is built from. `lifecycle` covers
open/close/abandon — the actions that change which loop is active.
`content` covers what happens inside an active loop: actions, hypotheses,
scope, budget. Nothing here calls `loop.app.commands` directly — that
belongs to `loop.gui.actions`, which owns error handling and the refresh.
"""

from __future__ import annotations
