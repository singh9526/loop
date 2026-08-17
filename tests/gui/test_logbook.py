import pytest

from loop.core import stats
from loop.gui.logbook import LogbookView

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
