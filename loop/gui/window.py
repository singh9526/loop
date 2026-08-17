"""The dashboard window.

Binds to `DashboardView` and nothing else — every string and every
enabled/disabled decision was made in `loop.app.view`; this module only
places widgets and copies fields onto them. It also hosts the logbook
(`loop.gui.logbook.LogbookView`) as a second page of the same central
stack, toggled by a menu action and a button next to the status strip —
that page binds itself; this module only switches to it.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QPushButton, QStackedWidget, QToolBar,
    QVBoxLayout, QWidget,
)

from loop.app.view import ALL_ACTIONS, DashboardView
from loop.gui.actions import Actions
from loop.gui.logbook import LogbookView
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


class MainWindow(QMainWindow):
    def __init__(self, controller, mode: str = "dark") -> None:
        super().__init__()
        self._controller = controller
        self._mode = mode
        self._actions: dict[str, QAction] = {}
        self._dashboard: DashboardView | None = None
        self._checkin_active = False
        # `controller` is `None` in the clock-only tests (test_window.py) —
        # they never click anything, so a runner-less window must still
        # construct cleanly. `Actions` needs a `Writer`, which only a real
        # controller has.
        self._runner = (
            Actions(window=self, controller=controller, writer=controller.writer)
            if controller is not None else None
        )
        self.setWindowTitle("loop")
        self.setMinimumSize(*MIN_SIZE)
        self._build()
        if self._runner is not None:
            self._runner.report.connect(self._on_report)
            # Through `_trigger`-style guards, not straight to the runner:
            # a row button is a second door into the same commands, and the
            # check-in lockout has to hold both.
            self._stack.resume_requested.connect(self._resume_requested)
            self._hypotheses.kill_requested.connect(self._kill_requested)

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
        self._meter = BurnMeter(self._mode)
        self._stack = StackBar()
        self._hypotheses = HypothesisList()
        self._log = ActionLog()
        self._thrash = ThrashBanner()
        self._error = ErrorBanner()
        self._strip = StatusStrip()
        self._report = QLabel()
        self._report.setObjectName("muted")
        self._report.setWordWrap(True)
        self._report.setVisible(False)
        for widget in (self._toolbar, self._error, self._stack, self._clock,
                       self._meter, self._thrash):
            layout.addWidget(widget)
        columns = QHBoxLayout()
        columns.addWidget(self._hypotheses, 1)
        columns.addWidget(self._log, 1)
        layout.addLayout(columns)

        strip_row = QHBoxLayout()
        strip_row.addWidget(self._strip, 1)
        self._logbook_button = QPushButton("Logbook")
        self._logbook_button.clicked.connect(lambda checked=False: self._toggle_logbook())
        strip_row.addWidget(self._logbook_button)
        layout.addLayout(strip_row)

        layout.addWidget(self._report)

        # The dashboard and the logbook are two pages of one stack, not two
        # calls to `setCentralWidget` — `QMainWindow` deletes whatever
        # widget it replaces, so swapping the central widget back and
        # forth would destroy the dashboard the first time the user left
        # it.
        self._logbook = LogbookView(self._controller)
        self._pages = QStackedWidget(self)
        self._pages.addWidget(central)
        self._pages.addWidget(self._logbook)
        self.setCentralWidget(self._pages)

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
            # `Actions.run` takes the CLI-style name, not a target id — the
            # right entry point here, since a `QAction` never carries one.
            # `hyp_kill` and `resume` resolve the id themselves (asking, or
            # defaulting to "the most recent") when reached this way; the
            # row controls that *do* have an id bypass `run` and call
            # `run_hyp_kill`/`run_resume` directly (wired in `__init__`).
            action.triggered.connect(lambda checked=False, n=name: self._trigger(n))
            toolbar.addAction(action)
            menu.addAction(action)
            self._actions[name] = action

        # A second menu for the read-only view toggle — not one of the ten
        # `ALL_ACTIONS`, so it does not go through `Actions.run`: there is
        # no command to dispatch, only a page to switch to.
        view_menu = self.menuBar().addMenu("&View")
        self._logbook_action = QAction("Logbook", self)
        self._logbook_action.triggered.connect(lambda checked=False: self._toggle_logbook())
        view_menu.addAction(self._logbook_action)
        return toolbar

    def set_checkin_active(self, active: bool) -> None:
        """Refuse every action while a check-in owns the screen.

        The scheduler's `busy_changed` drives this. It has to live here
        rather than in `view.build`, which knows only what the log says
        and nothing about what is on screen — and it has to survive the
        1 Hz rebind, which is why `bind` masks rather than this method
        calling `setEnabled` once.

        Deferring is not an option worth taking: every one of these ten
        actions opens an application-modal dialog, and a modal dialog
        opened while the overlay is up renders beneath it, blocks keys to
        it, and holds `ask()`'s `finally: hide()` until it is dismissed —
        with nothing left to kill either window. Refusing costs the user
        one click after the check-in they are already answering.
        """
        if active == self._checkin_active:
            return
        self._checkin_active = active
        if self._dashboard is not None:
            self.bind(self._dashboard)

    def run_postmortem(self) -> None:
        """The p100 `stop now` continuation, called by the scheduler once
        the checkpoint event is written and the overlay is down.

        Deliberately not routed through `_trigger`: the lockout above is
        still on (the scheduler holds its guard until this returns), and
        this dialog *is* the check-in's own next step, not a competing
        action reached around it.
        """
        if self._runner is None:
            return
        self.surface()
        self._runner.run_close()

    def _trigger(self, name: str) -> None:
        if self._runner is None or self._checkin_active:
            return
        self._runner.run(name)

    def _resume_requested(self, loop_id: int) -> None:
        if self._runner is None or self._checkin_active:
            return
        self._runner.run_resume(loop_id)

    def _kill_requested(self, hyp_id: int) -> None:
        if self._runner is None or self._checkin_active:
            return
        self._runner.run_hyp_kill(hyp_id)

    def _toggle_logbook(self) -> None:
        """Flip between the dashboard and the logbook. Entering the
        logbook refreshes it, so it never shows a stale search from the
        last time it was open; leaving it costs nothing to refresh
        because the dashboard already redraws on every poll."""
        if self._pages.currentWidget() is self._logbook:
            self._pages.setCurrentIndex(0)
        else:
            self._logbook.refresh()
            self._pages.setCurrentWidget(self._logbook)

    def bind(self, dashboard: DashboardView) -> None:
        """One place decides what is live. Ten scattered setEnabled
        calls would each be a place to forget.

        A check-in in progress is the one input that does not come from
        the view, and it is applied by emptying `enabled_actions` here so
        the row buttons (`StackBar`, `HypothesisList`, which read the same
        field) go with it. The unmasked view is kept so lifting the
        lockout can rebind it without waiting for the next poll.
        """
        self._dashboard = dashboard
        if self._checkin_active:
            dashboard = replace(dashboard, enabled_actions=frozenset())
        for name, action in self._actions.items():
            action.setEnabled(name in dashboard.enabled_actions)
        self._clock.setText(_clock_text(dashboard))
        self._set_clock_over(dashboard.meter is not None and dashboard.meter.over_s > 0.0)
        self._meter.set_meter(dashboard.meter)
        for panel in (self._stack, self._hypotheses, self._log,
                      self._thrash, self._error, self._strip):
            panel.bind(dashboard)

    def _set_clock_over(self, over: bool) -> None:
        """State lives in the object name, not in the text — `theme.py`'s
        `QLabel#over` rule (font matched to `#clock`, colour crit) does the
        colouring, so `_clock.text()` stays plain in both states.

        Qt caches a widget's resolved style at polish time and will not
        re-derive it just because `objectName` changed — the label would
        keep rendering with its old colour forever without an explicit
        unpolish/polish to force it.
        """
        name = "over" if over else "clock"
        if self._clock.objectName() == name:
            return
        self._clock.setObjectName(name)
        style = self._clock.style()
        style.unpolish(self._clock)
        style.polish(self._clock)

    def show_report(self, kind: str, text: str) -> None:
        """The public seam for anything outside this window that needs to
        surface a message the same way `Actions.report` does — the
        scheduler's `report` signal (an answer that survived every retry
        but still could not be written), for one. Kept separate from
        `_on_report` so the rendering logic — styled `#muted` vs `#over` —
        has exactly one owner regardless of who is reporting."""
        self._on_report(kind, text)

    def _on_report(self, kind: str, text: str) -> None:
        """`Actions.report` lands here. `"stale"` reads as `#muted` — the
        outcome the user wanted already happened, so it is not styled as a
        problem — and `"error"` as `#over`, the same crit colour the clock
        uses when the loop runs long."""
        self._report.setText(text)
        self._report.setVisible(bool(text))
        self._set_report_style("over" if kind == "error" else "muted")

    def _set_report_style(self, name: str) -> None:
        """Same gotcha as `_set_clock_over`: an `objectName` change on an
        already-polished widget needs an explicit unpolish/polish or the
        colour never updates."""
        if self._report.objectName() == name:
            return
        self._report.setObjectName(name)
        style = self._report.style()
        style.unpolish(self._report)
        style.polish(self._report)


def _mmss(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"


def _clock_text(dashboard: DashboardView) -> str:
    """Plain text only — colour for the over-budget state comes from
    `objectName` + the stylesheet, never from markup in the string."""
    meter = dashboard.meter
    if meter is None:
        return "—"
    elapsed = _mmss(meter.elapsed_s)
    budget = _mmss(meter.budget_s)
    if meter.over_s > 0.0:
        return f"{elapsed} / {budget} · over by {_mmss(meter.over_s)}"
    return f"{elapsed} / {budget}"
