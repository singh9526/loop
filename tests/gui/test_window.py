"""The clock label: plain text always, over-budget state carried by
`objectName` + the stylesheet — never by markup embedded in the string.

Regression coverage for a review finding on Task 11: the first cut built
the over-budget colour as inline HTML (`<span style="color:...">`),
which (a) meant `_clock.text()` was not plain text for callers that read
it and (b) left `theme.py`'s purpose-built `QLabel#over` rule dead.
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
