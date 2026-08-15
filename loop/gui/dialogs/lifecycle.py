"""Open, close, abandon."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from loop.core.timefmt import format_duration, parse_duration
from loop.gui.dialogs.base import Field, FormDialog

DEFAULT_BUDGET = "45m"
DEFAULT_INTERVAL = "20m"


class OpenDialog(FormDialog):
    def __init__(self, parent, active_question: str | None) -> None:
        super().__init__(parent, "Open a loop", [
            Field("question", "question"),
            Field("stop_condition", "stop when"),
            Field("budget", "time budget", prefill=DEFAULT_BUDGET),
            Field("interval", "check-in interval", prefill=DEFAULT_INTERVAL),
            Field("hypotheses", "hypotheses", multiline=True,
                  helper="one per line, at least one"),
        ])
        self._active_question = active_question
        self.set_warning(self.warning_text())

    def warning_text(self) -> str:
        if self._active_question is None:
            return ""
        return (f"“{self._active_question}” is active. Opening this pauses it "
                f"and stacks on top.")

    def hypotheses(self) -> list[str]:
        return [
            line.strip()
            for line in self.collected()["hypotheses"].splitlines()
            if line.strip()
        ]

    def missing(self) -> list[str]:
        names = [name for name in super().missing() if name != "hypotheses"]
        if not self.hypotheses():
            names.append("hypotheses")
        for name in ("budget", "interval"):
            value = self.collected()[name]
            if value and not _parses(value):
                names.append(name)
        return names

    def budget_s(self) -> float:
        return parse_duration(self.collected()["budget"])

    def interval_s(self) -> float:
        return parse_duration(self.collected()["interval"])


class CloseDialog(FormDialog):
    def __init__(self, parent) -> None:
        super().__init__(parent, "Close the loop", [
            Field("what_was_it", "what was it?"),
            Field("giveaway", "what was the giveaway?"),
            Field("five_min_path", "how could I have found it in 5 minutes?"),
        ])


def confirm_abandon(parent, loop_id: int, elapsed_s: float) -> bool:
    """Named rather than vague: the number is what makes it a decision."""
    answer = QMessageBox.warning(
        parent, "Abandon the loop?",
        f"#{loop_id} has {format_duration(elapsed_s)} of active time on it. "
        f"Abandoning records no postmortem — the logbook counts it as unresolved.",
        QMessageBox.Cancel | QMessageBox.Discard, QMessageBox.Cancel,
    )
    return answer == QMessageBox.Discard


def _parses(value: str) -> bool:
    try:
        parse_duration(value)
        return True
    except ValueError:
        return False
