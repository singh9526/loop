import pytest

from loop.gui.dialogs.base import Field, FormDialog
from loop.gui.dialogs.content import ExtendDialog
from loop.gui.dialogs.lifecycle import OpenDialog


def test_a_form_reports_missing_required_fields_and_does_not_close(qapp):
    dialog = FormDialog(None, "Log an action", [
        Field("action", "action"), Field("because", "because"),
    ])
    dialog.set_values({"action": "added print", "because": "  "})
    assert dialog.missing() == ["because"]


def test_a_complete_form_returns_stripped_values(qapp):
    dialog = FormDialog(None, "Log an action", [
        Field("action", "action"), Field("because", "because"),
    ])
    dialog.set_values({"action": "  added print  ", "because": "shows the frame"})
    assert dialog.missing() == []
    assert dialog.collected() == {"action": "added print", "because": "shows the frame"}


def test_optional_fields_may_be_blank(qapp):
    dialog = FormDialog(None, "t", [Field("note", "note", required=False)])
    dialog.set_values({"note": ""})
    assert dialog.missing() == []


def test_open_defaults_match_the_cli(qapp):
    """45m and 20m, or the two front ends disagree about what a loop is."""
    dialog = OpenDialog(None, active_question=None)
    assert dialog.collected()["budget"] == "45m"
    assert dialog.collected()["interval"] == "20m"


def test_open_needs_at_least_one_hypothesis(qapp):
    dialog = OpenDialog(None, active_question=None)
    dialog.set_values({"question": "q", "stop_condition": "s",
                       "budget": "45m", "interval": "20m", "hypotheses": "  \n  "})
    assert "hypotheses" in dialog.missing()


def test_open_splits_hypotheses_one_per_line_dropping_blanks(qapp):
    dialog = OpenDialog(None, active_question=None)
    dialog.set_values({"question": "q", "stop_condition": "s", "budget": "45m",
                       "interval": "20m", "hypotheses": "cert expiry\n\npg pool\n"})
    assert dialog.hypotheses() == ["cert expiry", "pg pool"]


def test_open_warns_when_a_loop_is_already_active(qapp):
    dialog = OpenDialog(None, active_question="is the logging config filtering it?")
    assert "is the logging config filtering it?" in dialog.warning_text()


def test_open_says_nothing_about_stacking_when_nothing_is_active(qapp):
    assert OpenDialog(None, active_question=None).warning_text() == ""


def test_extend_rejects_an_unparseable_budget_before_writing(qapp):
    dialog = ExtendDialog(None, current_budget_s=2700.0)
    dialog.set_values({"new_budget": "soon", "learned": "the pool is shared"})
    assert dialog.missing() == ["new_budget"]


def test_extend_parses_a_bare_number_as_minutes(qapp):
    """45 means 45 minutes. The CLI made that choice; the window keeps it."""
    dialog = ExtendDialog(None, current_budget_s=2700.0)
    dialog.set_values({"new_budget": "90", "learned": "x"})
    assert dialog.missing() == []
    assert dialog.new_budget_s() == 5400.0
