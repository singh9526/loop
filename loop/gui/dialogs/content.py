"""Actions, hypotheses, scope, budget."""

from __future__ import annotations

from loop.core.timefmt import format_duration, parse_duration
from loop.gui.dialogs.base import Field, FormDialog


class ActionDialog(FormDialog):
    def __init__(self, parent) -> None:
        super().__init__(parent, "Log an action", [
            Field("action", "what did you do?"),
            Field("because", "what do you believe this will show?"),
        ])


class ExtendDialog(FormDialog):
    def __init__(self, parent, current_budget_s: float) -> None:
        super().__init__(parent, "Extend the budget", [
            Field("new_budget", "new budget",
                  helper=f"currently {format_duration(current_budget_s)}; "
                         f"a bare number means minutes"),
            Field("learned", "what did you learn that made it bigger?"),
        ])

    def missing(self) -> list[str]:
        names = super().missing()
        value = self.collected()["new_budget"]
        if value and "new_budget" not in names and self._parsed(value) is None:
            names.append("new_budget")
        return names

    def new_budget_s(self) -> float:
        return parse_duration(self.collected()["new_budget"])

    @staticmethod
    def _parsed(value: str) -> float | None:
        try:
            return parse_duration(value)
        except ValueError:
            return None


def text_dialog(parent, title: str, label: str, helper: str = "") -> FormDialog:
    """One required line. Used by add-hypothesis, pause, cut-scope, and the
    menu-bar path for rule-out-hypothesis (which needs the id typed rather
    than clicked)."""
    return FormDialog(parent, title, [Field("text", label, helper=helper)])
