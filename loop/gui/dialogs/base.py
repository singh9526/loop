"""One form, many dialogs.

Validation is a method, not a side effect of clicking OK, so the rules are
testable without driving a modal event loop.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout,
)


@dataclass(frozen=True, slots=True)
class Field:
    name: str
    label: str
    multiline: bool = False
    required: bool = True
    prefill: str = ""
    helper: str = ""


class FormDialog(QDialog):
    def __init__(self, parent, title: str, fields: list[Field]) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self._fields = fields
        self._inputs: dict[str, QLineEdit | QPlainTextEdit] = {}

        layout = QVBoxLayout(self)
        self._warning = QLabel()
        self._warning.setWordWrap(True)
        self._warning.setVisible(False)
        layout.addWidget(self._warning)

        for field in fields:
            label = QLabel(field.label)
            label.setObjectName("label")
            layout.addWidget(label)
            widget = QPlainTextEdit(self) if field.multiline else QLineEdit(self)
            if field.prefill:
                self._write(widget, field.prefill)
            layout.addWidget(widget)
            self._inputs[field.name] = widget
            if field.helper:
                helper = QLabel(field.helper)
                helper.setObjectName("muted")
                layout.addWidget(helper)

        self._error = QLabel()
        self._error.setObjectName("over")
        self._error.setVisible(False)
        layout.addWidget(self._error)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def set_warning(self, text: str) -> None:
        self._warning.setText(text)
        self._warning.setVisible(bool(text))

    def set_values(self, values: dict[str, str]) -> None:
        for name, value in values.items():
            self._write(self._inputs[name], value)

    def collected(self) -> dict[str, str]:
        return {
            name: self._read(widget).strip() for name, widget in self._inputs.items()
        }

    def missing(self) -> list[str]:
        """Names of required fields that came back blank. Overridden by
        subclasses with rules a blank check cannot express."""
        collected = self.collected()
        return [
            field.name for field in self._fields
            if field.required and not collected[field.name]
        ]

    def values(self) -> dict[str, str] | None:
        """Run the dialog. `None` means the user cancelled."""
        return self.collected() if self.exec() == QDialog.Accepted else None

    def _try_accept(self) -> None:
        missing = self.missing()
        if missing:
            self._error.setText(f"required: {', '.join(missing)}")
            self._error.setVisible(True)
            return
        self.accept()

    @staticmethod
    def _read(widget) -> str:
        return (widget.toPlainText() if isinstance(widget, QPlainTextEdit)
                else widget.text())

    @staticmethod
    def _write(widget, value: str) -> None:
        if isinstance(widget, QPlainTextEdit):
            widget.setPlainText(value)
        else:
            widget.setText(value)
