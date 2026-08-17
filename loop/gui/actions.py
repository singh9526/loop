"""Turning a click into a command, and a refusal into a sentence.

Every path through here refreshes the controller on success, so the window
never shows a state one poll behind the write that caused it.

THE ACTION-NAME TRAP: `loop.app.view.ALL_ACTIONS` uses CLI-subcommand names
(`open`, `try`, `hyp_add`, `hyp_kill`, `cut`, `extend`, `pause`, `resume`,
`close`, `abandon`); `loop.app.commands` names its functions differently
(`open_loop`, `log_action`, `add_hypothesis`, `kill_hypothesis`, `cut_scope`,
`extend_budget`, and the four that coincide: `pause`, `resume`, `close`,
`abandon`). There is no `getattr(commands, action_name)` anywhere in this
file — every `run_<name>` method below spells out the `commands.*` call it
makes, by name, so the mapping is grep-able instead of implicit.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from loop.app import commands
from loop.app.errors import LoopError, StaleError
from loop.app.view import ALL_ACTIONS
from loop.store.lock import LockTimeout


class Actions(QObject):
    report = Signal(str, str)      # kind: "ok" | "stale" | "error"

    def __init__(self, window, controller, writer) -> None:
        super().__init__()
        self._window = window
        self._controller = controller
        self._writer = writer

    def run(self, name: str) -> None:
        """Dispatch by the CLI-style action name — the entry point the
        toolbar and menu bar use, since a `QAction` only carries a name,
        never a target id. Row controls (a specific hypothesis, a specific
        paused loop) call their `run_<name>` method directly instead, with
        the id the click supplied."""
        _DISPATCH[name](self)

    def call(self, run):
        """Run one command. Returns its result, or None if it was refused.

        `StaleError` gets its own kind because it is not a mistake: a
        check-in got there first, and the outcome the user wanted has
        already happened. Reporting it in the same tone as a real error
        would teach the user to distrust a correct app.

        `LockTimeout` is not a `LoopError` — it comes from `store/lock.py`
        and propagates straight out of `Writer.mutate` — so it is caught
        alongside `LoopError` explicitly, not inherited into the catch by
        accident.
        """
        try:
            result = run()
        except StaleError as exc:
            self.report.emit("stale", str(exc))
            self._controller.refresh()
            return None
        except (LoopError, LockTimeout, ValueError) as exc:
            self.report.emit("error", str(exc))
            return None
        self._controller.refresh()
        return result

    # -- open / close / abandon -------------------------------------------

    def run_open(self) -> None:
        from loop.gui.dialogs.lifecycle import OpenDialog

        view = self._controller.view
        active_question = view.question if view is not None else None
        dialog = OpenDialog(self._window, active_question=active_question)
        values = dialog.values()
        if values is None:
            return
        self.call(lambda: commands.open_loop(
            self._writer,
            question=values["question"],
            stop_condition=values["stop_condition"],
            budget_s=dialog.budget_s(),
            interval_s=dialog.interval_s(),
            hypotheses=dialog.hypotheses(),
            stack_on_active=True,
        ))

    def run_close(self) -> None:
        from loop.gui.dialogs.lifecycle import CloseDialog

        dialog = CloseDialog(self._window)
        values = dialog.values()
        if values is None:
            return
        self.call(lambda: commands.close(
            self._writer,
            what_was_it=values["what_was_it"],
            giveaway=values["giveaway"],
            five_min_path=values["five_min_path"],
        ))

    def run_abandon(self) -> None:
        from loop.gui.dialogs.lifecycle import confirm_abandon

        view = self._controller.view
        if view is None or view.active_id is None:
            self.report.emit("error", "no active loop.")
            return
        elapsed_s = view.meter.elapsed_s if view.meter is not None else 0.0
        if not confirm_abandon(self._window, view.active_id, elapsed_s):
            return
        self.call(lambda: commands.abandon(self._writer))

    # -- content: actions, hypotheses, scope, budget -----------------------

    def run_try(self) -> None:
        from loop.gui.dialogs.content import ActionDialog

        dialog = ActionDialog(self._window)
        values = dialog.values()
        if values is None:
            return
        self.call(lambda: commands.log_action(
            self._writer, action=values["action"], because=values["because"]
        ))

    def run_hyp_add(self) -> None:
        from loop.gui.dialogs.content import text_dialog

        dialog = text_dialog(self._window, "Add Hypothesis", "hypothesis")
        values = dialog.values()
        if values is None:
            return
        self.call(lambda: commands.add_hypothesis(self._writer, text=values["text"]))

    def run_hyp_kill(self, hyp_id: int | None = None) -> None:
        """`hyp_id` comes straight from `HypothesisList.kill_requested` for
        the row-button path. The menu-bar path has no row to click, so it
        resolves `None` to an id by asking — the only one of the ten
        actions that needs to, since every other id-bearing command either
        takes none (pause, cut, extend, close, abandon all act on *the*
        active loop) or degrades cleanly to "the first one" (resume)."""
        if hyp_id is None:
            hyp_id = self._pick_live_hypothesis()
            if hyp_id is None:
                return
        self.call(lambda: commands.kill_hypothesis(self._writer, hyp_id=hyp_id))

    def _pick_live_hypothesis(self) -> int | None:
        from loop.gui.dialogs.content import text_dialog

        view = self._controller.view
        live = [h for h in (view.hypotheses if view is not None else ()) if h.alive]
        if not live:
            self.report.emit("error", "no live hypotheses to rule out.")
            return None
        helper = "; ".join(f"§{h.hyp_id} {h.text}" for h in live)
        dialog = text_dialog(self._window, "Rule Out Hypothesis", "hypothesis id",
                              helper=helper)
        values = dialog.values()
        if values is None:
            return None
        text = values["text"]
        if not text.lstrip("-").isdigit():
            self.report.emit("error", f"not a hypothesis id: {text!r}")
            return None
        return int(text)

    def run_pause(self) -> None:
        from loop.gui.dialogs.content import text_dialog

        dialog = text_dialog(self._window, "Pause", "reason")
        values = dialog.values()
        if values is None:
            return
        self.call(lambda: commands.pause(self._writer, reason=values["text"]))

    def run_resume(self, loop_id: int | None = None) -> None:
        """`loop_id` comes from `StackBar.resume_requested` for the
        row-button path. The menu-bar path leaves it `None`; `commands.resume`
        already treats that as "the most recently paused loop", the same
        default `loop resume` (no argument) uses on the CLI, so there is
        nothing here to ask the user — `resume` is only ever enabled while
        nothing is active, so `pause_reason` is always `None` too."""
        self.call(lambda: commands.resume(
            self._writer, loop_id=loop_id, pause_reason=None
        ))

    def run_cut(self) -> None:
        from loop.gui.dialogs.content import text_dialog

        view = self._controller.view
        current = view.stop_condition if view is not None else None
        dialog = text_dialog(self._window, "Cut Scope", "new stop condition")
        if current:
            dialog.set_warning(f"currently: {current}")
        values = dialog.values()
        if values is None:
            return
        self.call(lambda: commands.cut_scope(
            self._writer, new_stop_condition=values["text"]
        ))

    def run_extend(self) -> None:
        from loop.gui.dialogs.content import ExtendDialog

        view = self._controller.view
        current_budget_s = (
            view.meter.budget_s if view is not None and view.meter is not None else 0.0
        )
        dialog = ExtendDialog(self._window, current_budget_s=current_budget_s)
        values = dialog.values()
        if values is None:
            return
        self.call(lambda: commands.extend_budget(
            self._writer, new_budget_s=dialog.new_budget_s(), learned=values["learned"]
        ))


# The explicit action-name -> handler table the module docstring promises.
# Keyed off the same ten strings `loop.gui.window`'s `ACTION_ORDER` and
# `loop.app.view.ALL_ACTIONS` use, and asserted against the latter so a
# renamed or added action fails at import time, not at a runtime click.
_DISPATCH = {
    "open": Actions.run_open,
    "try": Actions.run_try,
    "hyp_add": Actions.run_hyp_add,
    "hyp_kill": Actions.run_hyp_kill,
    "pause": Actions.run_pause,
    "resume": Actions.run_resume,
    "cut": Actions.run_cut,
    "extend": Actions.run_extend,
    "close": Actions.run_close,
    "abandon": Actions.run_abandon,
}

assert set(_DISPATCH) == ALL_ACTIONS
