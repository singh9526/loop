import pytest

from loop.app import commands
from loop.app.writer import Writer
from loop.blockers.base import Answers
from loop.blockers.fake import FakeBlocker
from loop.gui import scheduler as scheduler_module
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


def test_a_lock_timeout_recording_the_answer_does_not_crash_and_defers_the_write(wired, monkeypatch):
    """The user answered, but the first write lost the race for the lock.
    Unlike the checkpoint-shown case above (nothing to lose yet), there is
    a real answer in hand here — `tick()` must not raise, and must not
    drop it on this first failure: the retry is merely scheduled (through
    `QTimer.singleShot`, never fired synchronously), so nothing is written
    *yet*, but nothing has been given up on either. The busy guard stays
    held — checked directly here, not just through its effect — because
    the operation (the retry) is still outstanding."""
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
    # Not written yet — the retry is only scheduled, not run synchronously.
    assert all(e["type"] != "ping_answered" for e in log())
    assert scheduler._busy is True, "the guard must stay held while a retry is outstanding"


def test_a_second_tick_while_a_retry_is_outstanding_shows_no_second_prompt(wired, monkeypatch):
    """The reproduction for the critical finding this round exists to fix:
    guarding only the window's display time — the first version of this
    fix — let a poll firing while a retry sat unresolved show a second
    full-screen prompt for the same due ping. Pings have no
    `checkpoints_pending`-style backoff (`_ping_due` in `core/schedule.py`
    is driven purely by `last_ping_elapsed`, which only advances once a
    write actually lands), so nothing else stood in the way.

    `QTimer.singleShot` is patched to record its callback without firing
    it, so the retry stays outstanding — deterministically, with no real
    delay — for the second `tick()` to land in the middle of."""
    from PySide6.QtCore import QTimer
    from loop.store.lock import LockTimeout

    scheduled = []
    monkeypatch.setattr(QTimer, "singleShot", staticmethod(
        lambda ms, fn: scheduled.append(fn)
    ))

    def always_busy(*args, **kwargs):
        raise LockTimeout("contended")

    monkeypatch.setattr(commands, "record_checkin", always_busy)

    clock, writer, controller = wired
    open_one(writer)
    clock[0] = 1200.0  # a ping is due

    blocker = FakeBlocker([answer(choice="n"), answer(choice="n")])
    scheduler = Scheduler(controller, writer, blocker, now=lambda: clock[0])

    scheduler.tick()
    scheduler.tick()  # the poll timer firing again while the retry is outstanding

    assert scheduled, "a retry should have been scheduled from the first tick"
    assert len(blocker.prompts) == 1, "a second prompt for the same due must not appear"


def test_an_unexpected_exception_writing_the_answer_still_releases_the_guard(wired, monkeypatch):
    """Requirement: the busy guard must be released on every exit path,
    including one nobody planned for. A guard stuck `True` here would be
    worse than the duplicate-prompt bug this whole fix exists to close —
    the app would look alive and simply never ask again, with nothing on
    screen to say why. Deliberately not a `LockTimeout` here: this is the
    "real bug, not contention" branch."""
    clock, writer, controller = wired
    open_one(writer)
    clock[0] = 1200.0  # a ping is due

    def broken(*args, **kwargs):
        raise RuntimeError("not a lock problem")

    monkeypatch.setattr(commands, "record_checkin", broken)
    blocker = FakeBlocker([answer(choice="n")])
    scheduler = Scheduler(controller, writer, blocker, now=lambda: clock[0])

    with pytest.raises(RuntimeError):
        scheduler.tick()

    assert scheduler._busy is False, "an unexpected exception must not strand the guard"

    # The symptom of a stranded guard is total, silent, permanent silence:
    # prove the scheduler can still serve a later tick, not just that the
    # flag's value looks right.
    monkeypatch.setattr(commands, "record_checkin", lambda *a, **k: None)
    blocker2 = FakeBlocker([answer(choice="n")])
    scheduler._blocker = blocker2
    scheduler.tick()
    assert len(blocker2.prompts) == 1, "a released guard must allow a later tick to work"


def test_a_transient_lock_timeout_retries_and_the_answer_survives(wired, monkeypatch):
    """The crux of the fix: a `LockTimeout` on `record_checkin` must not
    be a permanent loss of an answer the user already gave. Driven
    deterministically — no real waiting — by making `QTimer.singleShot`
    invoke its callback immediately, which is exactly what advancing the
    clock by `RECORD_RETRY_MS` would eventually do for real."""
    from PySide6.QtCore import QTimer
    from loop.store.lock import LockTimeout

    monkeypatch.setattr(QTimer, "singleShot", staticmethod(lambda ms, fn: fn()))

    clock, writer, controller = wired
    open_one(writer)
    clock[0] = 1200.0  # a ping is due

    real_record_checkin = commands.record_checkin
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise LockTimeout("contended")
        return real_record_checkin(*args, **kwargs)

    monkeypatch.setattr(commands, "record_checkin", flaky)
    blocker = FakeBlocker([answer(choice="n")])
    scheduler = Scheduler(controller, writer, blocker, now=lambda: clock[0])
    reports = []
    scheduler.report.connect(lambda kind, text: reports.append((kind, text)))

    scheduler.tick()

    assert calls["n"] == 3, "should have retried past the first two failures"
    assert log()[-1]["type"] == "ping_answered"
    assert reports == [], "it eventually succeeded; nothing should be reported as lost"


def test_a_persistent_lock_timeout_is_surfaced_not_silently_dropped(wired, monkeypatch):
    """The other half of the crux: retries are bounded, and when every one
    of them fails, the user must be told — silent loss is the defect this
    whole fix exists to close. `Scheduler.report` is the surface (mirrors
    `Actions.report`); nothing here is a `print` that could vanish into a
    detached process's stdout."""
    from PySide6.QtCore import QTimer
    from loop.store.lock import LockTimeout

    monkeypatch.setattr(QTimer, "singleShot", staticmethod(lambda ms, fn: fn()))

    clock, writer, controller = wired
    open_one(writer)
    clock[0] = 1200.0  # a ping is due

    calls = {"n": 0}

    def always_busy(*args, **kwargs):
        calls["n"] += 1
        raise LockTimeout("contended")

    monkeypatch.setattr(commands, "record_checkin", always_busy)
    blocker = FakeBlocker([answer(choice="n")])
    scheduler = Scheduler(controller, writer, blocker, now=lambda: clock[0])
    reports = []
    scheduler.report.connect(lambda kind, text: reports.append((kind, text)))

    scheduler.tick()  # must not raise, must not retry forever

    assert calls["n"] == scheduler_module.RECORD_MAX_ATTEMPTS, "retries must be bounded"
    assert all(e["type"] != "ping_answered" for e in log()), "the answer must never land"
    assert len(reports) == 1, "the loss must be surfaced exactly once, not silently"
    assert reports[0][0] == "error"
    assert "loop #1" in reports[0][1]


def test_a_moot_answer_and_a_contended_lock_are_handled_by_different_paths(wired, monkeypatch):
    """The two cases must stay distinct in the code, not just in a
    comment: a loop closed mid-prompt makes `record_checkin` *return*
    `False` (no exception, no retry, correctly dropped); a busy lock makes
    it *raise* `LockTimeout` (retried, then reported if it never lands).
    This proves the closed-mid-prompt path is untouched by the retry
    machinery — it does not go anywhere near `LockTimeout` handling."""
    from PySide6.QtCore import QTimer

    calls = []
    monkeypatch.setattr(QTimer, "singleShot", staticmethod(
        lambda ms, fn: calls.append(fn) or None
    ))

    clock, writer, controller = wired
    open_one(writer)
    clock[0] = 1200.0

    class ClosesFirst:
        def ask(self, prompt):
            commands.close(writer, what_was_it="a", giveaway="b", five_min_path="c")
            return answer(choice="n")

    Scheduler(controller, writer, ClosesFirst(), now=lambda: clock[0]).tick()

    assert calls == [], "a moot answer must never schedule a retry"
    assert [event["type"] for event in log()][-1] == "loop_closed"
