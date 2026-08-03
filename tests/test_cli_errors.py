"""Important 3: ordinary failures must not escape `main()` as tracebacks."""

import pytest

from loop import cli
from loop.core import events
from loop.store import jsonl, paths


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
    monkeypatch.setenv("LOOP_BLOCKER", "fake")
    monkeypatch.setattr("loop.sched.daemon.ensure_running", lambda: None)
    return tmp_path


def test_corrupt_log_is_reported_and_names_the_path_and_says_recoverable(home, capsys):
    path = paths.events_path()
    path.write_text(
        '{"type": "a", "ts": 1.0, "loop_id": null}\nNOT JSON\n'
        '{"type": "c", "ts": 2.0, "loop_id": null}\n',
        encoding="utf-8",
    )

    assert cli.main(["status"]) == 1

    err = capsys.readouterr().err
    assert str(path) in err
    assert "line 2" in err
    assert "recoverable" in err


def test_invariant_violation_is_reported_not_raised(capsys):
    jsonl.append(events.make(
        "loop_opened", ts=0.0, loop_id=1, question="q1", stop_condition="s",
        budget_s=2700.0, interval_s=1200.0, hypotheses=["a"], parent_id=None,
    ))
    jsonl.append(events.make(
        "loop_opened", ts=1.0, loop_id=2, question="q2", stop_condition="s",
        budget_s=2700.0, interval_s=1200.0, hypotheses=["a"], parent_id=None,
    ))

    assert cli.main(["status"]) == 1
    assert "still active" in capsys.readouterr().err


def test_unparseable_budget_flag_is_reported_not_raised(feed, capsys):
    feed(["stop cond"])
    assert cli.main(["open", "q", "--budget", "soon"]) == 1
    assert "cannot parse duration" in capsys.readouterr().err
