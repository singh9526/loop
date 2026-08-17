"""The panels. Each takes a view model and draws it. None of them decide anything."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from loop.app.view import DashboardView, MeterView
from loop.core.timefmt import format_duration
from loop.gui import theme

TRACK_H = 9
CHECKPOINT_FRACTION = 0.75


class BurnMeter(QWidget):
    """The signature component.

    The track is a fixed cool-to-hot gradient and the fill is a mask that
    retracts from the left, so the colour at the leading edge is a direct
    read of how much budget is gone. Over budget, the mask is dropped and
    the whole track burns.
    """

    def __init__(self, mode: str = "dark") -> None:
        super().__init__()
        self._mode = mode
        self._meter: MeterView | None = None
        self.setFixedHeight(TRACK_H)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_meter(self, meter: MeterView | None) -> None:
        self._meter = meter
        self.update()

    def paintEvent(self, _event) -> None:
        if self._meter is None:
            return
        tokens = theme.TOKENS[self._mode]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        gradient = QLinearGradient(0, 0, self.width(), 0)
        gradient.setColorAt(0.00, QColor(tokens["accent"]))
        gradient.setColorAt(0.38, QColor(tokens["accent"]))
        gradient.setColorAt(0.76, QColor(tokens["warn"]))
        gradient.setColorAt(1.00, QColor(tokens["crit"]))
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(self.rect(), TRACK_H / 2, TRACK_H / 2)

        if self._meter.over_s <= 0.0:
            edge = int(self.width() * self._meter.fraction)
            painter.setBrush(QColor(tokens["raised"]))
            painter.drawRect(edge, 0, self.width() - edge, self.height())

        tick_x = int(self.width() * CHECKPOINT_FRACTION)
        painter.setPen(QColor(tokens["panel"]))
        painter.drawLine(tick_x, 0, tick_x, self.height())
        painter.end()


class StackBar(QFrame):
    """Paused parents above, active last. Row buttons appear on hover or
    focus so the list reads as data first — never the only way to reach an
    action, which is why every one of them is also in the menu bar."""

    resume_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("panel")
        self._layout = QVBoxLayout(self)

    def bind(self, dashboard: DashboardView) -> None:
        _clear(self._layout)
        for row in dashboard.stack:
            line = QHBoxLayout()
            line.addWidget(QLabel("▶" if row.active else "⏸"))
            line.addWidget(QLabel(f"#{row.loop_id}"))
            question = QLabel(row.question)
            question.setObjectName("question" if row.active else "muted")
            line.addWidget(question, 1)
            meta = f"{format_duration(row.elapsed_s)} active"
            if not row.active:
                meta += f" · paused {format_duration(row.paused_s)}"
                if row.stale:
                    meta += "  ⚠"
            line.addWidget(QLabel(meta))
            if not row.active and "resume" in dashboard.enabled_actions:
                button = QPushButton("Resume")
                button.clicked.connect(
                    lambda _checked=False, loop_id=row.loop_id:
                    self.resume_requested.emit(loop_id)
                )
                line.addWidget(button)
            self._layout.addLayout(line)


class HypothesisList(QFrame):
    kill_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("panel")
        self._layout = QVBoxLayout(self)

    def bind(self, dashboard: DashboardView) -> None:
        _clear(self._layout)
        head = QLabel(f"{dashboard.live_count} live · {dashboard.dead_count} ruled out")
        head.setObjectName("label")
        self._layout.addWidget(head)
        for row in dashboard.hypotheses:
            line = QHBoxLayout()
            line.addWidget(QLabel(str(row.hyp_id) if row.alive else "✗"))
            text = QLabel(row.text)
            if not row.alive:
                # Kept visible on purpose: ruled-out hypotheses are the
                # evidence the loop is converging.
                text.setObjectName("dead")
            line.addWidget(text, 1)
            if row.alive and "hyp_kill" in dashboard.enabled_actions:
                button = QPushButton("Rule out")
                button.clicked.connect(
                    lambda _checked=False, hyp_id=row.hyp_id:
                    self.kill_requested.emit(hyp_id)
                )
                line.addWidget(button)
            self._layout.addLayout(line)


class ActionLog(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("panel")
        self._layout = QVBoxLayout(self)

    def bind(self, dashboard: DashboardView) -> None:
        _clear(self._layout)
        head = QLabel(f"{len(dashboard.timeline)} entries")
        head.setObjectName("label")
        self._layout.addWidget(head)
        for entry in dashboard.timeline:
            block = QVBoxLayout()
            block.addWidget(QLabel(entry.text))
            if entry.because:
                because = QLabel(entry.because)
                because.setObjectName("muted")
                block.addWidget(because)
            self._layout.addLayout(block)


class ThrashBanner(QFrame):
    """Takes vertical space and pushes the body down. An alarm that costs
    nothing is an alarm you stop seeing."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("banner")
        self._layout = QVBoxLayout(self)

    def bind(self, dashboard: DashboardView) -> None:
        _clear(self._layout)
        self.setVisible(dashboard.thrash is not None)
        if dashboard.thrash is None:
            return
        for text in (dashboard.thrash.title, dashboard.thrash.why, dashboard.thrash.fix):
            self._layout.addWidget(QLabel(text))


class ErrorBanner(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("error")
        self._layout = QVBoxLayout(self)

    def bind(self, dashboard: DashboardView) -> None:
        _clear(self._layout)
        self.setVisible(dashboard.error is not None)
        if dashboard.error is None:
            return
        self._layout.addWidget(QLabel(
            "Event log stopped parsing. Showing the last state that folded "
            "cleanly. Actions are disabled until the file is repaired."
        ))
        path = QLabel(dashboard.error)
        path.setObjectName("path")
        path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._layout.addWidget(path)


class StatusStrip(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self._layout = QHBoxLayout(self)
        self._label = QLabel()
        self._layout.addWidget(self._label)

    def bind(self, dashboard: DashboardView) -> None:
        parts = [dashboard.status.detail]
        if dashboard.meter and dashboard.meter.next_checkin_s is not None:
            parts.append(f"next {format_duration(dashboard.meter.next_checkin_s)}")
        if dashboard.status.depth > 1:
            parts.append(f"depth {dashboard.status.depth}")
        self._label.setText("  ·  ".join(parts))


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())
