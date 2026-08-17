import time

import pytest

from loop import cli
from loop.core import events
from loop.store import jsonl


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
    monkeypatch.setattr("loop.app.launcher.ensure_running", lambda: None)
    monkeypatch.setattr("loop.app.launcher.available", lambda: True)


def open_loop(feed, question, stack=False):
    answers = (["y"] if stack else []) + ["stop cond", "45m", "a hypothesis", ""]
    feed(answers)
    cli.main(["open", question])


def state():
    return events.fold(jsonl.read_all())


POSTMORTEM = ["it was the cert", "tls handshake reset", "openssl s_client"]


def test_pause_requires_a_reason_and_clears_active(feed):
    open_loop(feed, "staging deploy fails")
    feed(["prod incident"])
    assert cli.main(["pause"]) == 0

    current = state()
    assert current.active_id is None
    assert jsonl.read_all()[-1]["reason"] == "prod incident"


def test_pause_with_no_active_loop_fails(capsys):
    assert cli.main(["pause"]) == 1
    assert "no active loop" in capsys.readouterr().err


def test_resume_without_an_id_picks_the_most_recently_paused(feed):
    open_loop(feed, "first")
    feed(["a"])
    cli.main(["pause"])
    open_loop(feed, "second")
    feed(["b"])
    cli.main(["pause"])

    assert cli.main(["resume"]) == 0
    assert state().active_id == 2


def test_resume_jumps_to_an_arbitrary_id(feed):
    open_loop(feed, "first")
    feed(["a"])
    cli.main(["pause"])
    open_loop(feed, "second")
    feed(["b"])
    cli.main(["pause"])

    assert cli.main(["resume", "1"]) == 0
    assert state().active_id == 1


def test_resume_pauses_whatever_is_active_first(feed):
    open_loop(feed, "first")
    feed(["a"])
    cli.main(["pause"])
    open_loop(feed, "second")

    feed(["switching back"])
    assert cli.main(["resume", "1"]) == 0
    current = state()
    assert current.active_id == 1
    assert current.loops[2].status == "paused"


def test_resume_rejects_a_loop_that_is_not_paused(feed, capsys):
    open_loop(feed, "first")
    assert cli.main(["resume", "1"]) == 1
    assert "not paused" in capsys.readouterr().err


def test_open_stacks_and_records_the_parent(feed, capsys):
    open_loop(feed, "staging deploy fails")
    open_loop(feed, "prod 500s on /checkout", stack=True)

    current = state()
    assert current.active_id == 2
    assert current.loops[2].parent_id == 1
    assert current.loops[1].status == "paused"
    assert "stack depth 2" in capsys.readouterr().out


def test_aborted_stacked_open_leaves_the_parent_active_and_nothing_new_logged(
    feed, monkeypatch
):
    open_loop(feed, "staging deploy fails")
    before = jsonl.read_all()

    # "y" answers the stack confirmation; every prompt after that hits
    # Ctrl-D (EOFError -> Aborted) before a single new event is written.
    queue = ["y"]

    def input_then_eof(_prompt=""):
        if queue:
            return queue.pop(0)
        raise EOFError

    monkeypatch.setattr("builtins.input", input_then_eof)

    assert cli.main(["open", "prod 500s on /checkout"]) == 1

    current = state()
    assert current.active_id == 1
    assert current.loops[1].status == "active"
    assert jsonl.read_all() == before


def test_open_refuses_at_max_depth(feed, capsys):
    open_loop(feed, "one")
    for question in ("two", "three", "four", "five"):
        open_loop(feed, question, stack=True)
    assert events.stack_depth(state()) == 5

    feed(["y"])
    assert cli.main(["open", "six"]) == 1
    assert "stack depth" in capsys.readouterr().err


def test_open_warns_at_depth_three(feed, capsys):
    open_loop(feed, "one")
    open_loop(feed, "two", stack=True)
    open_loop(feed, "three", stack=True)
    assert "context switching, not working" in capsys.readouterr().out


def test_close_requires_all_three_postmortem_answers(feed):
    open_loop(feed, "staging deploy fails")
    feed(["", "it was the cert", "tls handshake reset", "openssl s_client"])
    assert cli.main(["close"]) == 0
    assert jsonl.read_all()[-1]["what_was_it"] == "it was the cert"


def test_close_pops_and_resumes_the_parent(feed, capsys):
    open_loop(feed, "staging deploy fails")
    open_loop(feed, "prod 500s", stack=True)

    feed(POSTMORTEM)
    assert cli.main(["close"]) == 0

    current = state()
    assert current.active_id == 1
    assert current.loops[2].status == "closed"
    assert "resumed #1" in capsys.readouterr().out


def test_closing_the_bottom_loop_leaves_nothing_active(feed):
    open_loop(feed, "only loop")
    feed(POSTMORTEM)
    cli.main(["close"])
    assert state().active_id is None
    assert events.stack_depth(state()) == 0


def test_abandon_needs_no_postmortem_and_pops(feed):
    open_loop(feed, "staging deploy fails")
    open_loop(feed, "prod 500s", stack=True)
    assert cli.main(["abandon"]) == 0
    current = state()
    assert current.loops[2].status == "abandoned"
    assert current.active_id == 1


def test_close_does_not_resume_a_parent_that_is_already_closed(feed):
    open_loop(feed, "parent")
    open_loop(feed, "child", stack=True)
    # abandon the child, resume parent, then abandon the parent too
    cli.main(["abandon"])
    cli.main(["abandon"])
    assert state().active_id is None


def test_ls_shows_the_stack(feed, capsys):
    open_loop(feed, "staging deploy fails")
    open_loop(feed, "prod 500s on /checkout", stack=True)
    cli.main(["ls"])
    out = capsys.readouterr().out
    assert "▶ #2  prod 500s on /checkout" in out
    assert "#1  staging deploy fails" in out
    assert "paused" in out


def test_ls_on_an_empty_stack(capsys):
    assert cli.main(["ls"]) == 0
    assert "stack is empty" in capsys.readouterr().out


# --- a CLI invocation is a single instant ---
#
# `main` samples the clock once, at entry, and threads that reading into
# every command's `Writer`. These two tests advance the clock *while the
# user is answering*, which a test that freezes the clock to one constant
# cannot do — and which is the only way to tell an invocation-time stamp
# apart from a write-time one.


class _Clock:
    """A clock that moves only when the test moves it."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_close_stamps_the_invocation_not_the_moment_the_answers_finished(
    feed, monkeypatch
):
    """Minutes spent answering the postmortem are not minutes of debugging.

    A `Writer` left on its own `time.time` would stamp `loop_closed` when
    the write happens — after the three prompts — pushing `closed_at` past
    the moment the user actually stopped, and inflating every stats figure
    derived from it.
    """
    clock = _Clock(3_000.0)
    monkeypatch.setattr(time, "time", clock)
    open_loop(feed, "staging deploy fails")

    # The user takes 90 seconds over the three postmortem questions.
    answers = iter(POSTMORTEM)

    def slow_answer(_prompt=""):
        clock.now += 30.0
        return next(answers)

    monkeypatch.setattr("builtins.input", slow_answer)

    assert cli.main(["close"]) == 0
    assert clock.now == 3_090.0, "the typing has to actually take time"

    closed = jsonl.read_all()[-1]
    assert closed["type"] == "loop_closed"
    assert closed["ts"] == 3_000.0
    assert state().loops[1].closed_at == 3_000.0


def test_open_stamps_the_invocation_so_answering_counts_against_the_budget(
    monkeypatch,
):
    """The budget starts burning when you run the command, not when you
    finish describing the problem."""
    clock = _Clock(5_000.0)
    monkeypatch.setattr(time, "time", clock)

    answers = iter(["stop cond", "45m", "a hypothesis", ""])

    def slow_answer(_prompt=""):
        clock.now += 15.0
        return next(answers)

    monkeypatch.setattr("builtins.input", slow_answer)

    assert cli.main(["open", "staging deploy fails"]) == 0
    assert clock.now == 5_060.0, "the typing has to actually take time"

    opened = jsonl.read_all()[0]
    assert opened["type"] == "loop_opened"
    assert opened["ts"] == 5_000.0
    # the minute spent answering is already on the loop's clock
    assert state().loops[1].elapsed(clock.now) == 60.0
