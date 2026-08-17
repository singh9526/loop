import pytest

from loop import cli
from loop.core import events
from loop.store import jsonl


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
    monkeypatch.setattr("loop.app.launcher.ensure_running", lambda: None)
    monkeypatch.setattr("loop.app.launcher.available", lambda: True)


def seed_closed_loop():
    jsonl.append(events.make(
        "loop_opened", ts=0.0, loop_id=1, question="staging deploy fails",
        stop_condition="s", budget_s=2700.0, interval_s=1200.0,
        hypotheses=["cert expiry"], parent_id=None,
    ))
    jsonl.append(events.make("action_logged", ts=10.0, loop_id=1,
                             action="restart the pg container",
                             because="connection refused in the logs"))
    jsonl.append(events.make("loop_closed", ts=1800.0, loop_id=1,
                             what_was_it="expired intermediate cert",
                             giveaway="connection refused only over tls",
                             five_min_path="openssl s_client -connect"))


def test_stats_prints_every_line(capsys):
    seed_closed_loop()
    assert cli.main(["stats"]) == 0
    out = capsys.readouterr().out
    assert "protocol followed   : 1 loops" in out
    assert "estimate drift" in out


def test_stats_json(capsys):
    seed_closed_loop()
    cli.main(["stats", "--json"])
    assert '"followed"' in capsys.readouterr().out


def test_grep_finds_postmortems_and_actions(capsys):
    seed_closed_loop()
    assert cli.main(["grep", "connection refused"]) == 0
    out = capsys.readouterr().out
    assert "#1" in out
    assert "giveaway" in out
    assert "restart the pg container" in out


def test_grep_is_case_insensitive_and_reports_nothing_found(capsys):
    seed_closed_loop()
    cli.main(["grep", "CONNECTION REFUSED"])
    assert "#1" in capsys.readouterr().out

    cli.main(["grep", "kubernetes"])
    assert "no matches" in capsys.readouterr().out


def test_grep_skips_loops_that_are_still_open(capsys):
    jsonl.append(events.make(
        "loop_opened", ts=0.0, loop_id=1, question="connection refused everywhere",
        stop_condition="s", budget_s=2700.0, interval_s=1200.0,
        hypotheses=["a"], parent_id=None,
    ))
    cli.main(["grep", "connection refused"])
    assert "no matches" in capsys.readouterr().out
