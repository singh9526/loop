"""The dashboard window.

Binds to `DashboardView` and nothing else — every string and every
enabled/disabled decision was made in `loop.app.view`; this module only
places widgets and copies fields onto them.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QToolBar, QVBoxLayout, QWidget,
)

from loop.app.view import ALL_ACTIONS, DashboardView
from loop.gui import theme
from loop.gui.widgets import (
    ActionLog, BurnMeter, ErrorBanner, HypothesisList, StackBar, StatusStrip,
    ThrashBanner,
)

MIN_SIZE = (720, 560)

# Order is presentation only — ALL_ACTIONS itself is unordered. The
# assertion below keeps this table honest if view.py ever adds or
# renames an action without a matching update here.
ACTION_ORDER = (
    "open", "try", "hyp_add", "hyp_kill", "cut", "extend",
    "pause", "resume", "close", "abandon",
)
ACTION_LABELS = {
    "open": "Open Loop…",
    "try": "Log Attempt…",
    "hyp_add": "Add Hypothesis…",
    "hyp_kill": "Rule Out Hypothesis…",
    "cut": "Cut Scope…",
    "extend": "Extend Budget…",
    "pause": "Pause",
    "resume": "Resume…",
    "close": "Close Loop…",
    "abandon": "Abandon Loop…",
}
assert set(ACTION_ORDER) == ALL_ACTIONS == set(ACTION_LABELS)


def _noop(checked: bool = False) -> None:
    """Placeholder for every action's `triggered` signal. Task 12 wires
    these to dialogs; nothing here decides what an action does."""


class MainWindow(QMainWindow):
    def __init__(self, controller, mode: str = "dark") -> None:
        super().__init__()
        self._controller = controller
        self._mode = mode
        self._actions: dict[str, QAction] = {}
        self.setWindowTitle("loop")
        self.setMinimumSize(*MIN_SIZE)
        self._build()

    def has_active_loop(self) -> bool:
        return self._controller is not None and self._controller.active_id is not None

    def surface(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        """Hide, do not quit. The app is the scheduler — closing the window
        must not silently stop the check-ins the user is relying on."""
        event.ignore()
        self.hide()

    def _build(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)
        self._toolbar = self._build_toolbar()
        self._clock = QLabel("—")
        self._clock.setObjectName("clock")
        self._clock.setTextFormat(Qt.TextFormat.RichText)
        self._meter = BurnMeter(self._mode)
        self._stack = StackBar()
        self._hypotheses = HypothesisList()
        self._log = ActionLog()
        self._thrash = ThrashBanner()
        self._error = ErrorBanner()
        self._strip = StatusStrip()
        for widget in (self._toolbar, self._error, self._stack, self._clock,
                       self._meter, self._thrash):
            layout.addWidget(widget)
        columns = QHBoxLayout()
        columns.addWidget(self._hypotheses, 1)
        columns.addWidget(self._log, 1)
        layout.addLayout(columns)
        layout.addWidget(self._strip)
        self.setCentralWidget(central)

    def _build_toolbar(self) -> QToolBar:
        """A plain widget row placed by the central layout, not a
        dockable QMainWindow toolbar — this app has exactly one and it
        does not move. Every action is added to the menu bar too, so
        nothing here is reachable only by mouse."""
        toolbar = QToolBar(self)
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        menu = self.menuBar().addMenu("&Actions")
        for name in ACTION_ORDER:
            action = QAction(ACTION_LABELS[name], self)
            action.setObjectName(name)
            action.triggered.connect(_noop)
            toolbar.addAction(action)
            menu.addAction(action)
            self._actions[name] = action
        return toolbar

    def bind(self, dashboard: DashboardView) -> None:
        """One place decides what is live. Ten scattered setEnabled
        calls would each be a place to forget."""
        for name, action in self._actions.items():
            action.setEnabled(name in dashboard.enabled_actions)
        self._clock.setText(_clock_text(dashboard, self._mode))
        self._meter.set_meter(dashboard.meter)
        for panel in (self._stack, self._hypotheses, self._log,
                      self._thrash, self._error, self._strip):
            panel.bind(dashboard)


def _mmss(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"


def _clock_text(dashboard: DashboardView, mode: str) -> str:
    meter = dashboard.meter
    if meter is None:
        return "—"
    elapsed = _mmss(meter.elapsed_s)
    budget = _mmss(meter.budget_s)
    if meter.over_s > 0.0:
        crit = theme.TOKENS[mode]["crit"]
        return (
            f'<span style="color:{crit};">{elapsed}</span> / {budget}'
            f" &middot; over by {_mmss(meter.over_s)}"
        )
    return f"{elapsed} / {budget}"
