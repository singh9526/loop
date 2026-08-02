import pytest

from loop import cli
from loop.core import events
from loop.store import jsonl


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
    monkeypatch.setenv("LOOP_BLOCKER", "fake")


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
