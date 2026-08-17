"""The window's two pieces of state that are not just copied off a
`DashboardView`: the clock label's over-budget styling, and the lockout
that a check-in in progress imposes on every action.

The clock tests are regression coverage for a review finding on Task 11:
the first cut built the over-budget colour as inline HTML (`<span
style="color:...">`), which (a) meant `_clock.text()` was not plain text
for callers that read it and (b) left `theme.py`'s purpose-built
`QLabel#over` rule dead.
"""

from __future__ import annotations

import pytest

from loop.app import view
from loop.gui import theme
from loop.gui.window import MainWindow, _clock_text

pytestmark = pytest.mark.usefixtures("qapp")


def _dashboard(meter: view.MeterView | None) -> view.DashboardView:
    return view.DashboardView(
        active_id=1 if meter else None, question="q", stop_condition="s",
        meter=meter, stack=(), hypotheses=(), timeline=(), thrash=None,
        status=view.StatusView("armed", "x", 1), enabled_actions=frozenset(),
        live_count=0, dead_count=0,
    )


def _over_meter() -> view.MeterView:
    return view.MeterView(elapsed_s=138.0, budget_s=60.0, fraction=1.0,
                           over_s=78.0, next_checkin_s=None)


def _under_meter() -> view.MeterView:
    return view.MeterView(elapsed_s=10.0, budget_s=60.0, fraction=0.16,
                           over_s=0.0, next_checkin_s=None)


def test_clock_text_has_no_markup_idle_under_or_over_budget():
    for dashboard in (_dashboard(None), _dashboard(_under_meter()), _dashboard(_over_meter())):
        text = _clock_text(dashboard)
        assert "<" not in text and ">" not in text
    assert _clock_text(_dashboard(_over_meter())) == "2:18 / 1:00 · over by 1:18"
    assert _clock_text(_dashboard(_under_meter())) == "0:10 / 1:00"
    assert _clock_text(_dashboard(None)) == "—"


def test_over_budget_is_carried_by_object_name_not_by_the_text():
    """The two states must be distinguishable by something other than the
    string — here, `objectName`, which is what `theme.py`'s `QLabel#over`
    selector keys off."""
    window = MainWindow(controller=None, mode="dark")
    window.bind(_dashboard(_over_meter()))
    assert window._clock.objectName() == "over"
    assert "<" not in window._clock.text()

    window.bind(_dashboard(_under_meter()))
    assert window._clock.objectName() == "clock"


def test_the_stylesheet_actually_recolours_the_label_after_toggling(qapp):
    """A regression guard for the Qt re-polish gotcha: setting objectName
    on an already-polished widget does nothing to its rendered style
    until the widget is explicitly unpolished and repolished. This test
    reads the resolved `QPalette.WindowText` colour — the same channel
    Qt's stylesheet engine writes a QLabel's `color:` property into — so
    a regression that drops the repolish call would fail it even though
    `objectName` alone still looks correct.
    """
    window = MainWindow(controller=None, mode="dark")
    window.setStyleSheet(theme.stylesheet("dark"))
    window.show()
    qapp.processEvents()

    from PySide6.QtGui import QPalette

    window.bind(_dashboard(_over_meter()))
    qapp.processEvents()
    over_colour = window._clock.palette().color(QPalette.ColorRole.WindowText).name()
    assert over_colour.lower() == theme.TOKENS["dark"]["crit"].lower()

    window.bind(_dashboard(_under_meter()))
    qapp.processEvents()
    under_colour = window._clock.palette().color(QPalette.ColorRole.WindowText).name()
    assert under_colour.lower() != over_colour.lower()
    assert under_colour.lower() == theme.TOKENS["dark"]["text"].lower()

    window.hide()


def _live_dashboard() -> view.DashboardView:
    return view.DashboardView(
        active_id=1, question="q", stop_condition="s", meter=_under_meter(),
        stack=(), hypotheses=(), timeline=(), thrash=None,
        status=view.StatusView("armed", "x", 1),
        enabled_actions=frozenset({"close", "try", "pause"}),
        live_count=0, dead_count=0,
    )


def test_a_checkin_in_progress_disables_every_action_and_then_restores_them():
    """`enabled_actions` comes from `view.build`, which knows nothing about
    a check-in being on screen. Without this the user can reach Actions ▸
    Close Loop… from the menu bar an always-on-top window does not cover,
    and the modal dialog it opens renders *beneath* the overlay."""
    window = MainWindow(controller=None, mode="dark")
    window.bind(_live_dashboard())
    assert window._actions["close"].isEnabled()

    window.set_checkin_active(True)
    assert not any(action.isEnabled() for action in window._actions.values())

    # The 1 Hz poll keeps binding fresh views underneath; the lockout must
    # survive them rather than be undone by the next refresh.
    window.bind(_live_dashboard())
    assert not any(action.isEnabled() for action in window._actions.values())

    window.set_checkin_active(False)
    assert window._actions["close"].isEnabled()
    assert not window._actions["open"].isEnabled(), "the view still decides the rest"


def test_the_row_buttons_are_refused_while_a_checkin_is_up():
    """`StackBar.resume_requested` and `HypothesisList.kill_requested` reach
    `Actions` without passing the menu bar's `QAction`s, so disabling those
    is not on its own enough."""
    from loop.app.writer import Writer
    from loop.gui.controller import Controller

    writer = Writer(now=lambda: 0.0)
    window = MainWindow(Controller(writer=writer, now=lambda: 0.0), mode="dark")
    reports = []
    window._runner.report.connect(lambda kind, text: reports.append((kind, text)))

    window.set_checkin_active(True)
    window._hypotheses.kill_requested.emit(3)
    window._stack.resume_requested.emit(2)
    assert reports == [], "a row click must not reach a command during a check-in"

    window.set_checkin_active(False)
    window._hypotheses.kill_requested.emit(3)
    assert reports, "and must reach it again once the check-in is over"
