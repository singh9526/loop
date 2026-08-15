import pytest

from loop.app.errors import LoopError, StaleError
from loop.app.writer import Writer
from loop.core import events
from loop.store import jsonl


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
    return tmp_path


def opened(loop_id=1):
    return events.make(
        "loop_opened", ts=0.0, loop_id=loop_id, question="q",
        stop_condition="s", budget_s=2700.0, interval_s=1200.0,
        hypotheses=["h"], parent_id=None,
    )


def test_stale_error_is_a_loop_error():
    """cli.py catches LoopError; a StaleError must not escape it."""
    assert issubclass(StaleError, LoopError)


def test_mutate_appends_and_returns_the_result():
    writer = Writer(now=lambda: 5.0)
    result = writer.mutate(lambda state, now: ([opened()], "done"))
    assert result == "done"
    assert [event["type"] for event in jsonl.read_all()] == ["loop_opened"]


def test_build_sees_the_state_it_will_be_appended_to():
    writer = Writer(now=lambda: 5.0)
    writer.mutate(lambda state, now: ([opened()], None))
    seen = {}

    def build(state, now):
        seen["active_id"] = state.active_id
        seen["now"] = now
        return [], None

    writer.mutate(build)
    assert seen == {"active_id": 1, "now": 5.0}


def test_a_raising_build_leaves_the_log_byte_identical():
    writer = Writer(now=lambda: 5.0)
    writer.mutate(lambda state, now: ([opened()], None))
    before = jsonl.read_all()

    def build(state, now):
        raise LoopError("nope")

    with pytest.raises(LoopError):
        writer.mutate(build)
    assert jsonl.read_all() == before


def test_a_multi_event_mutation_is_written_in_order():
    """loop_paused must land before loop_opened or fold raises InvariantError."""
    writer = Writer(now=lambda: 5.0)
    writer.mutate(lambda state, now: ([opened(1)], None))
    writer.mutate(lambda state, now: ([
        events.make("loop_paused", ts=now, loop_id=1, reason="stacking"),
        events.make("loop_opened", ts=now, loop_id=2, question="q2",
                    stop_condition="s", budget_s=600.0, interval_s=300.0,
                    hypotheses=["h"], parent_id=1),
    ], None))
    state = events.fold(jsonl.read_all())  # raises if the order is wrong
    assert state.active_id == 2


def test_read_folds_without_taking_the_lock(tmp_path):
    """Reads are for display and must never contend with a writer."""
    writer = Writer()
    assert writer.read().active_id is None
    assert not (tmp_path / "lock").exists()
