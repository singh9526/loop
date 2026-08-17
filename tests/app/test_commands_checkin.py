import pytest

from loop.app import commands
from loop.app.writer import Writer
from loop.blockers.base import Answers
from loop.core import events, schedule
from loop.store import jsonl


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))


@pytest.fixture
def writer():
    made = Writer(now=lambda: 100.0)
    commands.open_loop(
        made, question="q", stop_condition="s", budget_s=2700.0, interval_s=1200.0,
        hypotheses=["cert expiry", "pg pool"], stack_on_active=True,
    )
    return made


def answer(choice=None, picked=None, fields=None, timed_out=False):
    return Answers(timed_out=timed_out, choice=choice, picked=picked,
                   fields=fields or {}, shown_at=0.0, answered_at=0.0)


def log():
    return jsonl.read_all()


def test_checkpoint_shown_is_recorded_for_forensics(writer):
    commands.record_checkpoint_shown(writer, loop_id=1, kind="p75")
    assert log()[-1] == {"type": "checkpoint_shown", "ts": 100.0, "loop_id": 1, "kind": "p75"}


def test_a_ping_answer_becomes_events(writer):
    due = schedule.Due(kind="ping", loop_id=1, elapsed=1200.0)
    assert commands.record_checkin(writer, due=due, answers=answer(choice="y", picked=1)) is True
    assert [event["type"] for event in log()[-2:]] == ["ping_answered", "hypothesis_eliminated"]
    assert log()[-1]["hyp_id"] == 1


def test_answers_for_a_loop_that_is_no_longer_active_are_dropped(writer):
    """A check-in can sit for five minutes. If the loop was closed from a
    terminal meanwhile, the answers describe a world that ended."""
    due = schedule.Due(kind="ping", loop_id=1, elapsed=1200.0)
    commands.close(writer, what_was_it="a", giveaway="b", five_min_path="c")
    before = log()
    assert commands.record_checkin(writer, due=due, answers=answer(choice="n")) is False
    assert log() == before


def test_a_timed_out_ping_is_recorded_as_unanswered(writer):
    due = schedule.Due(kind="ping", loop_id=1, elapsed=1200.0)
    commands.record_checkin(writer, due=due, answers=answer(timed_out=True))
    assert log()[-1]["type"] == "ping_unanswered"
