"""Important 9: `loop tick` is a shipped subcommand and had no test."""

import json
import time

import pytest

from loop import cli
from loop.core import events
from loop.store import jsonl

BUDGET = 2700.0
INTERVAL = 1200.0


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
    monkeypatch.setenv("LOOP_BLOCKER", "fake")
    monkeypatch.delenv("LOOP_FAKE_ANSWERS", raising=False)
    monkeypatch.setattr("loop.sched.daemon.ensure_running", lambda: None)
    return tmp_path


def seed(ts: float) -> None:
    jsonl.append(events.make(
        "loop_opened", ts=ts, loop_id=1, question="q", stop_condition="s",
        budget_s=BUDGET, interval_s=INTERVAL, hypotheses=["a"], parent_id=None,
    ))


def test_tick_fires_a_due_ping_and_records_it_as_unanswered():
    seed(ts=time.time() - INTERVAL - 1.0)

    assert cli.main(["tick"]) == 0

    log = jsonl.read_all()
    assert log[-1]["type"] == "ping_unanswered"


def test_tick_reports_nothing_fired_as_json_when_nothing_is_due(capsys):
    seed(ts=time.time())

    assert cli.main(["tick", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"fired": None}
    assert jsonl.read_all()[-1]["type"] == "loop_opened"  # nothing new written


def test_tick_reports_the_fired_kind_as_json(capsys):
    seed(ts=time.time() - INTERVAL - 1.0)

    assert cli.main(["tick", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"fired": "ping"}
