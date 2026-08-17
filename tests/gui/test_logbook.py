import pytest
from PySide6.QtWidgets import QLabel

from loop.core import stats
from loop.gui.logbook import LogbookView
from loop.store import paths

pytestmark = pytest.mark.usefixtures("qapp")


def _report(median_pct: float = 0.0) -> dict:
    report = stats.compute([], 0.0)
    report["estimate_drift"]["median_pct"] = median_pct
    return report


def test_a_negative_drift_renders_as_under_not_a_negative_over():
    """Regression guard for the Task 9 sign fix: the window must not
    re-derive the over/under word itself and get it backwards."""
    view = LogbookView(controller=None)
    view.bind(_report(median_pct=-20.0), [])
    text = view._value_labels["estimate drift"].text()
    assert "20.0% under" in text
    assert "-20.0% over" not in text
    assert "over" not in text


def test_binding_an_empty_report_raises_nothing_and_shows_no_matches():
    view = LogbookView(controller=None)
    view.bind(stats.compute([], 0.0), [])

    headers = [
        view._matches_layout.itemAt(i).widget()
        for i in range(view._matches_layout.count())
    ]
    questions = [w for w in headers if w is not None and w.objectName() == "question"]
    assert questions == []


def test_a_corrupt_log_shows_the_unreadable_banner_not_an_exception(tmp_path):
    """`_on_search` (and `refresh`, which calls it) reads the log itself —
    unlike the dashboard, nothing upstream shields it from
    `jsonl.CorruptLogError`. Without the fix this raises out of `refresh`
    instead of degrading the way the rest of the app does for the exact
    same failure."""
    paths.events_path().write_text(
        "NOT JSON\n"
        '{"type":"loop_abandoned","ts":1.0,"loop_id":1}\n',
        encoding="utf-8",
    )

    logbook = LogbookView(controller=None)
    logbook.refresh()  # must not raise

    # `isVisible()` would report `False` regardless of `setVisible(True)`
    # here, since neither `logbook` nor `_error` has a shown top-level
    # ancestor in this test — `isVisibleTo` checks the "would show if
    # `logbook` were shown" flag instead, which is what `ErrorBanner.bind`
    # actually set.
    assert logbook._error.isVisibleTo(logbook)
    path_label = logbook._error.findChild(QLabel, "path")
    assert path_label is not None
    assert path_label.text().endswith(":1")
