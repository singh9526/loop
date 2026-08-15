import pytest

from loop.app import commands
from loop.app.errors import LoopError, StaleError
from loop.app.writer import Writer
from loop.core import events
from loop.store import jsonl


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))


class Clock:
    def __init__(self, at=0.0):
        self.at = at

    def __call__(self):
        return self.at


def writer_at(clock):
    return Writer(now=clock)


def open_one(writer, question="q", **kwargs):
    return commands.open_loop(
        writer, question=question, stop_condition="s",
        budget_s=kwargs.pop("budget_s", 2700.0),
        interval_s=kwargs.pop("interval_s", 1200.0),
        hypotheses=kwargs.pop("hypotheses", ["cert expiry", "pg pool"]),
        stack_on_active=kwargs.pop("stack_on_active", True),
    )


def log():
    return jsonl.read_all()


def test_open_writes_one_event_and_reports_the_new_id():
    result = open_one(writer_at(Clock()))
    assert result.loop_id == 1
    assert result.depth == 1
    assert result.hypotheses == 2
    assert result.paused_id is None
    assert [event["type"] for event in log()] == ["loop_opened"]
    assert log()[0]["parent_id"] is None


def test_open_on_top_pauses_the_parent_in_the_same_write():
    clock = Clock()
    writer = writer_at(clock)
    open_one(writer, "parent")
    clock.at = 600.0
    result = open_one(writer, "child")

    assert [event["type"] for event in log()] == ["loop_opened", "loop_paused", "loop_opened"]
    assert result.paused_id == 1
    assert result.paused_elapsed_s == 600.0
    assert result.depth == 2
    assert log()[1]["reason"] == "child"      # the new question is why the parent stopped
    assert log()[2]["parent_id"] == 1
    assert events.fold(log()).active_id == 2  # fold accepts it: the pause landed first


def test_open_without_consent_writes_nothing():
    writer = writer_at(Clock())
    open_one(writer, "parent")
    with pytest.raises(LoopError, match="aborted"):
        open_one(writer, "child", stack_on_active=False)
    assert len(log()) == 1
    assert events.fold(log()).active_id == 1


def test_open_refuses_past_the_stack_ceiling():
    clock = Clock()
    writer = writer_at(clock)
    for index in range(5):
        clock.at = index * 60.0
        open_one(writer, f"q{index}")
    clock.at = 999.0
    with pytest.raises(LoopError, match="stack depth 5"):
        open_one(writer, "one too many")
    assert len(log()) == 9  # 5 opens + 4 pauses


def test_pause_reports_active_elapsed():
    clock = Clock()
    writer = writer_at(clock)
    open_one(writer)
    clock.at = 900.0
    result = commands.pause(writer, reason="standup")
    assert result.elapsed_s == 900.0
    assert log()[-1]["reason"] == "standup"


def test_pause_with_nothing_active_refuses():
    with pytest.raises(LoopError, match="no active loop"):
        commands.pause(writer_at(Clock()), reason="standup")


def test_resume_without_an_id_takes_the_most_recently_paused():
    clock = Clock()
    writer = writer_at(clock)
    open_one(writer, "first")
    clock.at = 100.0
    commands.pause(writer, reason="x")
    clock.at = 200.0
    result = commands.resume(writer, loop_id=None, pause_reason=None)
    assert result.summary.loop_id == 1
    assert result.summary.elapsed_s == 100.0
    assert result.summary.budget_s == 2700.0
    assert result.summary.interval_s == 1200.0


def test_resume_of_a_specific_loop_pauses_the_active_one():
    clock = Clock()
    writer = writer_at(clock)
    open_one(writer, "first")
    clock.at = 100.0
    open_one(writer, "second")
    clock.at = 200.0
    commands.resume(writer, loop_id=1, pause_reason="back to it")
    assert [event["type"] for event in log()[-2:]] == ["loop_paused", "loop_resumed"]
    assert events.fold(log()).active_id == 1


def test_resume_needs_a_reason_when_something_else_is_active():
    clock = Clock()
    writer = writer_at(clock)
    open_one(writer, "first")
    clock.at = 100.0
    open_one(writer, "second")
    with pytest.raises(StaleError, match="#2"):
        commands.resume(writer, loop_id=1, pause_reason=None)
    assert events.fold(log()).active_id == 2


def test_resume_of_the_active_loop_refuses():
    # `_resume_target` rejects any non-paused target before the
    # `active.id == target.id` branch in `resume` can run — a loop is
    # never both ACTIVE and PAUSED, so that branch is unreachable here.
    # Matches the existing CLI behavior: tests/test_cli_stack.py
    # ::test_resume_rejects_a_loop_that_is_not_paused asserts "not paused"
    # for this exact scenario.
    writer = writer_at(Clock())
    open_one(writer)
    with pytest.raises(LoopError, match="is active, not paused"):
        commands.resume(writer, loop_id=1, pause_reason=None)


def test_resume_with_nothing_paused_refuses():
    with pytest.raises(LoopError, match="nothing to resume"):
        commands.resume(writer_at(Clock()), loop_id=None, pause_reason=None)


def test_resume_of_a_closed_loop_names_its_status():
    clock = Clock()
    writer = writer_at(clock)
    open_one(writer)
    clock.at = 60.0
    commands.close(writer, what_was_it="a", giveaway="b", five_min_path="c")
    with pytest.raises(LoopError, match="closed, not paused"):
        commands.resume(writer, loop_id=1, pause_reason=None)


@pytest.mark.xfail(reason="kill_hypothesis arrives in Task 5", strict=True)
def test_close_records_the_postmortem_and_the_counts():
    clock = Clock()
    writer = writer_at(clock)
    open_one(writer)
    commands.kill_hypothesis(writer, hyp_id=1)
    clock.at = 1200.0
    result = commands.close(writer, what_was_it="stale cert",
                            giveaway="only after a restart", five_min_path="check notAfter")
    assert result.elapsed_s == 1200.0
    assert result.eliminated == 1
    assert result.resumed is None
    assert log()[-1]["what_was_it"] == "stale cert"


def test_close_of_a_child_resumes_the_parent_in_the_same_write():
    clock = Clock()
    writer = writer_at(clock)
    open_one(writer, "parent")
    clock.at = 600.0
    open_one(writer, "child")
    clock.at = 900.0
    result = commands.close(writer, what_was_it="a", giveaway="b", five_min_path="c")

    assert [event["type"] for event in log()[-2:]] == ["loop_closed", "loop_resumed"]
    assert result.resumed.loop_id == 1
    assert result.resumed.elapsed_s == 600.0
    assert events.fold(log()).active_id == 1


def test_abandon_of_a_child_also_resumes_the_parent():
    clock = Clock()
    writer = writer_at(clock)
    open_one(writer, "parent")
    clock.at = 600.0
    open_one(writer, "child")
    clock.at = 700.0
    result = commands.abandon(writer)
    assert result.elapsed_s == 100.0
    assert result.resumed.loop_id == 1
    assert events.fold(log()).active_id == 1


def test_close_with_nothing_active_refuses():
    with pytest.raises(LoopError, match="no active loop"):
        commands.close(writer_at(Clock()), what_was_it="a", giveaway="b", five_min_path="c")
