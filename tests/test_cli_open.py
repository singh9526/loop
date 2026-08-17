import json

import pytest

from loop import cli
from loop.core import events
from loop.store import jsonl


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
    monkeypatch.setattr("loop.app.launcher.ensure_running", lambda: None)
    monkeypatch.setattr("loop.app.launcher.available", lambda: True)
    return tmp_path


def log():
    return jsonl.read_all()


# --- require_app must actually gate `loop open`, not just exist ---
#
# Every fixture in this file patches `launcher.available` to True so the
# rest of the suite can run without PySide6 installed. That leaves the
# gate itself uncovered: deleting the `require_app()` call in `cmd_open`
# would pass every other test here. This one flips the patch back to
# False for itself and drives the real refusal.


def test_open_refuses_when_the_app_is_not_available(monkeypatch, capsys):
    monkeypatch.setattr("loop.app.launcher.available", lambda: False)
    assert cli.main(["open", "q"]) == 1
    assert "the loop app is not installed" in capsys.readouterr().err
    assert log() == []


def test_open_writes_a_loop_opened_event(feed, capsys):
    feed(["comes up clean twice", "45m", "cert expiry", "pg pool", ""])
    assert cli.main(["open", "staging deploy fails at startup"]) == 0

    entries = log()
    assert len(entries) == 1
    assert entries[0]["type"] == "loop_opened"
    assert entries[0]["loop_id"] == 1
    assert entries[0]["question"] == "staging deploy fails at startup"
    assert entries[0]["stop_condition"] == "comes up clean twice"
    assert entries[0]["budget_s"] == 2700.0
    assert entries[0]["interval_s"] == 1200.0
    assert entries[0]["hypotheses"] == ["cert expiry", "pg pool"]
    assert entries[0]["parent_id"] is None
    assert "#1 open" in capsys.readouterr().out


def test_open_budget_flag_skips_the_budget_prompt(feed):
    feed(["comes up clean twice", "cert expiry", ""])
    assert cli.main(["open", "q", "--budget", "30m", "--interval", "10m"]) == 0
    assert log()[0]["budget_s"] == 1800.0
    assert log()[0]["interval_s"] == 600.0


def test_open_json_prints_the_event(feed, capsys):
    feed(["s", "45m", "h", ""])
    cli.main(["open", "q", "--json"])
    assert json.loads(capsys.readouterr().out)["type"] == "loop_opened"


# --- Important 5: --json stdout must stay parseable through a validation retry ---


def test_open_json_stdout_parses_cleanly_after_a_validation_retry(feed, capsys):
    feed(["", "s", "not-a-time", "45m", "h", ""])
    assert cli.main(["open", "q", "--json"]) == 0
    out, err = capsys.readouterr()
    assert json.loads(out)["type"] == "loop_opened"
    assert "required." in err
    assert "cannot parse duration" in err


def test_try_requires_a_belief(feed, capsys):
    feed(["s", "45m", "h", ""])
    cli.main(["open", "q"])
    with pytest.raises(SystemExit):
        cli.main(["try", "restart the pg container"])


def test_try_records_action_and_belief(feed):
    feed(["s", "45m", "h", ""])
    cli.main(["open", "q"])
    assert cli.main(["try", "restart pg", "--because", "pool exhausted"]) == 0
    assert log()[-1] == {
        "type": "action_logged",
        "ts": log()[-1]["ts"],
        "loop_id": 1,
        "action": "restart pg",
        "because": "pool exhausted",
    }


def test_try_without_an_active_loop_fails(capsys):
    assert cli.main(["try", "a", "--because", "b"]) == 1
    assert "no active loop" in capsys.readouterr().err


def test_hyp_add_gets_the_next_id(feed):
    feed(["s", "45m", "one", "two", ""])
    cli.main(["open", "q"])
    cli.main(["hyp", "add", "three"])
    assert log()[-1]["hyp_id"] == 3


def test_hyp_kill_emits_elimination(feed):
    feed(["s", "45m", "one", "two", ""])
    cli.main(["open", "q"])
    assert cli.main(["hyp", "kill", "2"]) == 0
    assert log()[-1]["type"] == "hypothesis_eliminated"
    assert log()[-1]["hyp_id"] == 2


def test_hyp_kill_rejects_an_unknown_id(feed, capsys):
    feed(["s", "45m", "one", ""])
    cli.main(["open", "q"])
    assert cli.main(["hyp", "kill", "9"]) == 1
    assert "no live hypothesis 9" in capsys.readouterr().err


def test_status_shows_elapsed_and_hypotheses(feed, capsys):
    feed(["comes up clean twice", "45m", "cert expiry", "pg pool", ""])
    cli.main(["open", "staging deploy fails"])
    cli.main(["hyp", "kill", "1"])
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "#1  staging deploy fails" in out
    assert "comes up clean twice" in out
    assert "✗ 1  cert expiry" in out
    assert "  2  pg pool" in out


def test_status_with_no_loop(capsys):
    assert cli.main(["status"]) == 0
    assert "no active loop" in capsys.readouterr().out


# --- ruling 1: DEFAULT_BUDGET_S must be load-bearing ---


def test_open_blank_budget_prompt_uses_default_budget(feed):
    feed(["s", "", "h", ""])
    assert cli.main(["open", "q"]) == 0
    assert log()[0]["budget_s"] == cli.DEFAULT_BUDGET_S
