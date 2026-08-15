import pytest

from loop.app import commands
from loop.app.errors import LoopError, StaleError
from loop.app.writer import Writer
from loop.gui.actions import Actions
from loop.gui.controller import Controller


@pytest.fixture
def wired(qapp):
    writer = Writer(now=lambda: 0.0)
    controller = Controller(writer=writer, now=lambda: 0.0)
    actions = Actions(window=None, controller=controller, writer=writer)
    return writer, controller, actions


def test_a_refusal_becomes_a_report_not_a_traceback(wired):
    _writer, _controller, actions = wired
    seen = []
    actions.report.connect(lambda kind, text: seen.append((kind, text)))
    actions.call(lambda: commands.log_action(_writer_of(wired), action="a", because="b"))
    assert seen[0][0] == "error"
    assert "no active loop" in seen[0][1]


def test_a_stale_click_is_reported_quietly_not_as_an_error(wired):
    writer, controller, actions = wired
    commands.open_loop(writer, question="q", stop_condition="s", budget_s=2700.0,
                       interval_s=1200.0, hypotheses=["cert expiry"],
                       stack_on_active=True)
    commands.kill_hypothesis(writer, hyp_id=1)
    seen = []
    actions.report.connect(lambda kind, text: seen.append((kind, text)))
    actions.call(lambda: commands.kill_hypothesis(writer, hyp_id=1))
    assert seen[0][0] == "stale"
    assert "already ruled out" in seen[0][1]


def test_a_successful_call_refreshes_the_view_immediately(wired):
    writer, controller, actions = wired
    controller.refresh()
    commands.open_loop(writer, question="q", stop_condition="s", budget_s=2700.0,
                       interval_s=1200.0, hypotheses=["cert expiry"],
                       stack_on_active=True)
    actions.call(lambda: commands.log_action(writer, action="a", because="b"))
    assert len(controller.view.timeline) == 1  # not waiting for the next poll


def _writer_of(wired):
    return wired[0]
