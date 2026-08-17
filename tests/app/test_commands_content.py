import pytest

from loop.app import commands
from loop.app.errors import LoopError, StaleError
from loop.app.writer import Writer
from loop.core import events
from loop.store import jsonl


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))


@pytest.fixture
def writer():
    made = Writer(now=lambda: 0.0)
    commands.open_loop(
        made, question="q", stop_condition="s", budget_s=2700.0, interval_s=1200.0,
        hypotheses=["cert expiry", "pg pool"], stack_on_active=True,
    )
    return made


def log():
    return jsonl.read_all()


def test_log_action_counts_from_the_state_it_wrote_into(writer):
    assert commands.log_action(writer, action="a", because="b").actions == 1
    assert commands.log_action(writer, action="c", because="d").actions == 2
    assert log()[-1]["because"] == "d"


def test_log_action_with_nothing_active_refuses():
    with pytest.raises(LoopError, match="no active loop"):
        commands.log_action(Writer(now=lambda: 0.0), action="a", because="b")


def test_add_hypothesis_allocates_the_next_id(writer):
    result = commands.add_hypothesis(writer, text="dns cache")
    assert result.hyp_id == 3
    assert result.live == 3
    assert log()[-1]["text"] == "dns cache"


def test_kill_hypothesis_reports_what_is_left(writer):
    result = commands.kill_hypothesis(writer, hyp_id=1)
    assert result.hyp_id == 1
    assert result.live == 1
    assert log()[-1]["type"] == "hypothesis_eliminated"


def test_killing_an_unknown_hypothesis_refuses(writer):
    """Message preserved verbatim — tests/test_cli_open.py asserts this string."""
    with pytest.raises(LoopError, match="no live hypothesis 9"):
        commands.kill_hypothesis(writer, hyp_id=9)
    assert len(log()) == 1


def test_killing_an_already_dead_hypothesis_is_stale_not_an_error(writer):
    """The outcome the user wanted already happened. Say so, write nothing."""
    commands.kill_hypothesis(writer, hyp_id=1)
    before = log()
    with pytest.raises(StaleError, match="cert expiry"):
        commands.kill_hypothesis(writer, hyp_id=1)
    assert log() == before


def test_cut_scope_reports_both_conditions(writer):
    result = commands.cut_scope(writer, new_stop_condition="just the 500s")
    assert result.old_stop_condition == "s"
    assert result.new_stop_condition == "just the 500s"
    assert events.fold(log()).loops[1].stop_condition == "just the 500s"


def test_extend_budget_reports_both_budgets(writer):
    result = commands.extend_budget(writer, new_budget_s=3600.0, learned="pool is shared")
    assert result.old_budget_s == 2700.0
    assert result.new_budget_s == 3600.0
    assert log()[-1]["learned"] == "pool is shared"
    assert events.fold(log()).loops[1].extensions == 1


def test_extend_budget_rearms_the_checkpoints(writer):
    """fold keys checkpoints on the budget in force, so a bigger budget
    means p75 and p100 are due again. Guard the behaviour, not the field."""
    from loop.core import schedule
    commands.extend_budget(writer, new_budget_s=3600.0, learned="x")
    state = events.fold(log())
    assert schedule.checkpoint_boundary(state.loops[1], "p75") == 2700.0


def test_every_content_command_refuses_when_nothing_is_active():
    empty = Writer(now=lambda: 0.0)
    for call in (
        lambda: commands.add_hypothesis(empty, text="x"),
        lambda: commands.kill_hypothesis(empty, hyp_id=1),
        lambda: commands.cut_scope(empty, new_stop_condition="x"),
        lambda: commands.extend_budget(empty, new_budget_s=60.0, learned="x"),
    ):
        with pytest.raises(LoopError, match="no active loop"):
            call()
