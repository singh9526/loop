import pytest

from loop.app import commands
from loop.app.writer import Writer
from loop.blockers.base import Answers
from loop.blockers.fake import FakeBlocker
from loop.gui.controller import Controller
from loop.gui.scheduler import Scheduler
from loop.store import jsonl


def answer(choice=None, picked=None, fields=None, timed_out=False):
    return Answers(timed_out=timed_out, choice=choice, picked=picked,
                   fields=fields or {}, shown_at=0.0, answered_at=0.0)


@pytest.fixture
def wired(qapp):
    clock = [0.0]
    writer = Writer(now=lambda: clock[0])
    controller = Controller(writer=writer, now=lambda: clock[0])
    return clock, writer, controller


def open_one(writer, budget_s=2700.0, interval_s=1200.0):
    commands.open_loop(writer, question="q", stop_condition="s",
                       budget_s=budget_s, interval_s=interval_s,
                       hypotheses=["cert expiry", "pg pool"], stack_on_active=True)


def log():
    return jsonl.read_all()


def test_nothing_due_shows_nothing(wired):
    clock, writer, controller = wired
    open_one(writer)
    blocker = FakeBlocker([answer(choice="n")])
    Scheduler(controller, writer, blocker, now=lambda: clock[0]).tick()
    assert blocker.prompts == []


def test_a_due_ping_is_shown_and_its_answer_written(wired):
    clock, writer, controller = wired
    open_one(writer)
    clock[0] = 1200.0
    blocker = FakeBlocker([answer(choice="n")])
    Scheduler(controller, writer, blocker, now=lambda: clock[0]).tick()
    assert blocker.prompts[0].kind == "ping"
    assert log()[-1]["type"] == "ping_answered"
    assert log()[-1]["smaller"] is False


def test_a_checkpoint_records_that_it_was_shown_before_asking(wired):
    """Forensics: a timed-out checkpoint must be distinguishable from one
    that was never displayed."""
    clock, writer, controller = wired
    open_one(writer)
    clock[0] = 2025.0
    blocker = FakeBlocker([answer(choice="y")])
    Scheduler(controller, writer, blocker, now=lambda: clock[0]).tick()
    kinds = [event["type"] for event in log()]
    assert kinds.index("checkpoint_shown") < kinds.index("checkpoint_answered")


def test_a_scheduler_with_no_active_loop_does_nothing(wired):
    clock, writer, controller = wired
    blocker = FakeBlocker([answer(choice="n")])
    Scheduler(controller, writer, blocker, now=lambda: clock[0]).tick()
    assert blocker.prompts == []
    assert log() == []


def test_a_reentrant_tick_is_refused(wired):
    """A check-in blocks for minutes in a nested event loop, during which
    the poll timer keeps firing. A second prompt on top of the first would
    be two windows for one interval."""
    clock, writer, controller = wired
    open_one(writer)
    clock[0] = 1200.0
    scheduler = Scheduler(controller, writer, FakeBlocker([answer(choice="n")]),
                          now=lambda: clock[0])

    seen = []

    class Reentrant:
        def ask(self, prompt):
            seen.append(prompt)
            scheduler.tick()          # the poll timer, firing mid-prompt
            return answer(choice="n")

    scheduler._blocker = Reentrant()
    scheduler.tick()
    assert len(seen) == 1


def test_answers_for_a_loop_closed_mid_prompt_are_dropped(wired):
    clock, writer, controller = wired
    open_one(writer)
    clock[0] = 1200.0

    class ClosesFirst:
        def ask(self, prompt):
            commands.close(writer, what_was_it="a", giveaway="b", five_min_path="c")
            return answer(choice="n")

    Scheduler(controller, writer, ClosesFirst(), now=lambda: clock[0]).tick()
    assert [event["type"] for event in log()][-1] == "loop_closed"


def test_a_lock_timeout_recording_shown_does_not_crash_and_shows_nothing(wired, monkeypatch):
    """`LockTimeout` is not a `LoopError`; it propagates straight out of
    `Writer.mutate` when another process (still, until Task 15, possibly
    the daemon) holds the lock. A checkpoint tick must swallow it rather
    than let it escape a QTimer callback — and must not show the window,
    since that would answer a checkpoint the log never recorded as shown."""
    from loop.store.lock import LockTimeout

    clock, writer, controller = wired
    open_one(writer)
    clock[0] = 2025.0  # p75 is due

    def boom(*args, **kwargs):
        raise LockTimeout("contended")

    monkeypatch.setattr(commands, "record_checkpoint_shown", boom)
    blocker = FakeBlocker([answer(choice="y")])
    Scheduler(controller, writer, blocker, now=lambda: clock[0]).tick()
    assert blocker.prompts == []
    assert log() == [] or all(e["type"] != "checkpoint_shown" for e in log())


def test_a_real_checkin_window_runs_end_to_end_offscreen(wired, monkeypatch):
    """Every test above uses `FakeBlocker`, which never creates a widget.
    This drives the real `QtBlocker` -> `CheckinWindow` pipeline once, to
    prove the scheduler's wiring to Qt itself works, not just its wiring
    to the translation layer — offscreen (`tests/gui/conftest.py` forces
    and hard-asserts that), and on a deadline shortened the same way
    `tests/gui/test_checkin.py::
    test_the_blocker_runs_the_whole_window_and_returns_the_timeout` does:
    a per-instance `dataclasses.replace`, not the production `TIMEOUT_S`
    constant, which is never touched."""
    import dataclasses

    from loop.app import checkin as checkin_module
    from loop.gui.checkin import QtBlocker

    clock, writer, controller = wired
    open_one(writer)
    clock[0] = 1200.0  # a ping is due

    real_build_prompt = checkin_module.build_prompt
    monkeypatch.setattr(
        checkin_module, "build_prompt",
        lambda *a, **k: dataclasses.replace(real_build_prompt(*a, **k), timeout_s=0.05),
    )

    Scheduler(controller, writer, QtBlocker(mode="dark"), now=lambda: clock[0]).tick()

    assert log()[-1]["type"] == "ping_unanswered"


def test_a_lock_timeout_recording_the_answer_drops_it_without_crashing(wired, monkeypatch):
    """Same guard on the write side: the user answered, but the write lost
    the race for the lock. The answer is lost, not a crash."""
    from loop.store.lock import LockTimeout

    clock, writer, controller = wired
    open_one(writer)
    clock[0] = 1200.0  # a ping is due

    def boom(*args, **kwargs):
        raise LockTimeout("contended")

    monkeypatch.setattr(commands, "record_checkin", boom)
    blocker = FakeBlocker([answer(choice="n")])
    scheduler = Scheduler(controller, writer, blocker, now=lambda: clock[0])
    scheduler.tick()  # must not raise
    assert blocker.prompts[0].kind == "ping"
    assert all(e["type"] != "ping_answered" for e in log())
