import json
import threading
import time

import pytest

from loop.blockers.base import Answers
from loop.blockers.fake import FakeBlocker
from loop.core import events
from loop.sched import daemon
from loop.store import jsonl, paths

BUDGET = 2700.0
INTERVAL = 1200.0


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
    return tmp_path


def open_event(ts=0.0, loop_id=1):
    return events.make(
        "loop_opened", ts=ts, loop_id=loop_id, question="q", stop_condition="s",
        budget_s=BUDGET, interval_s=INTERVAL, hypotheses=["a", "b"], parent_id=None,
    )


class FakeClock:
    def __init__(self, start=0.0):
        self.now = start
        self.slept = []

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_run_exits_immediately_when_no_loop_is_active():
    clock = FakeClock()
    daemon.run(blocker=FakeBlocker([]), sleep_fn=clock.sleep, now_fn=lambda: clock.now)
    assert clock.slept == []
    assert not paths.pid_path().exists()


def test_run_writes_a_heartbeat_and_clears_it_on_exit():
    jsonl.append(open_event())
    clock = FakeClock()
    seen = []

    def watching_sleep(seconds):
        seen.append(json.loads(paths.pid_path().read_text()))
        clock.sleep(seconds)
        if len(seen) == 2:
            jsonl.append(events.make("loop_abandoned", ts=clock.now, loop_id=1))

    daemon.run(blocker=FakeBlocker([]), sleep_fn=watching_sleep, now_fn=lambda: clock.now)

    assert len(seen) == 2
    assert seen[0]["heartbeat"] < seen[1]["heartbeat"]
    assert not paths.pid_path().exists()


def test_run_fires_a_ping_when_one_is_due():
    jsonl.append(open_event())
    clock = FakeClock()
    blocker = FakeBlocker([Answers(False, "n", None, {}, 0.0, 0.0)])

    def sleep_then_stop(seconds):
        clock.sleep(seconds)
        if clock.now >= INTERVAL + daemon.POLL_S:
            jsonl.append(events.make("loop_abandoned", ts=clock.now, loop_id=1))

    daemon.run(blocker=blocker, sleep_fn=sleep_then_stop, now_fn=lambda: clock.now)

    assert len(blocker.prompts) == 1
    assert blocker.prompts[0].kind == "ping"
    assert any(event["type"] == "ping_answered" for event in jsonl.read_all())


def test_run_exits_when_the_pidfile_is_removed():
    jsonl.append(open_event())
    clock = FakeClock()

    def sleep_then_delete(seconds):
        clock.sleep(seconds)
        paths.pid_path().unlink()

    daemon.run(blocker=FakeBlocker([]), sleep_fn=sleep_then_delete, now_fn=lambda: clock.now)
    assert clock.slept == [daemon.POLL_S]


def test_run_exits_when_another_daemon_claims_the_pidfile():
    jsonl.append(open_event())
    clock = FakeClock()

    def sleep_then_steal(seconds):
        clock.sleep(seconds)
        paths.pid_path().write_text(json.dumps({"pid": 999_999, "heartbeat": clock.now}))

    daemon.run(blocker=FakeBlocker([]), sleep_fn=sleep_then_steal, now_fn=lambda: clock.now)
    assert clock.slept == [daemon.POLL_S]
    # the other daemon's file is left alone
    assert json.loads(paths.pid_path().read_text())["pid"] == 999_999


def test_is_running_is_false_without_a_pidfile():
    assert daemon.is_running() is False


def test_is_running_is_false_for_a_stale_heartbeat(monkeypatch):
    paths.pid_path().write_text(json.dumps({"pid": 1, "heartbeat": 0.0}))
    monkeypatch.setattr(daemon.time, "time", lambda: 3 * daemon.POLL_S + 1.0)
    assert daemon.is_running() is False


def test_is_running_is_true_for_a_fresh_heartbeat(monkeypatch):
    paths.pid_path().write_text(json.dumps({"pid": 1, "heartbeat": 100.0}))
    monkeypatch.setattr(daemon.time, "time", lambda: 105.0)
    assert daemon.is_running() is True


def test_is_running_tolerates_a_corrupt_pidfile():
    paths.pid_path().write_text("not json")
    assert daemon.is_running() is False


def test_ensure_running_spawns_once(monkeypatch):
    spawned = []
    monkeypatch.setattr(daemon, "_spawn", lambda: spawned.append(1))
    monkeypatch.setattr(daemon, "is_running", lambda: False)
    daemon.ensure_running()
    monkeypatch.setattr(daemon, "is_running", lambda: True)
    daemon.ensure_running()
    assert spawned == [1]


def test_stop_removes_the_pidfile():
    paths.pid_path().write_text(json.dumps({"pid": 1, "heartbeat": 1.0}))
    daemon.stop()
    assert not paths.pid_path().exists()


class SlowBlocker(FakeBlocker):
    """A blocker whose `ask` blocks until the test releases it — the seam
    for proving the heartbeat pulse survives a long-pending human prompt."""

    def __init__(self, answers, release):
        super().__init__(answers)
        self._release = release

    def ask(self, prompt):
        self._release.wait()
        return super().ask(prompt)


def _wait_for_heartbeat(deadline, after=None):
    """Poll the pidfile through the daemon's own tolerant reader.

    `_write_heartbeat` is `Path.write_text` — open-truncate-write-close,
    not an atomic replace — so a read racing a write can transiently see
    an empty file. `_read_pidfile` already treats that as "no record yet"
    (same tolerance `is_running` relies on for a corrupt file); polling
    through it, rather than parsing the raw text directly, is what makes
    this test robust against that ordinary, expected race.
    """
    while time.time() < deadline:
        record = daemon._read_pidfile()
        if record is not None and (after is None or record["heartbeat"] > after):
            return record["heartbeat"]
        time.sleep(0.002)
    raise AssertionError("heartbeat did not advance before the deadline")


def test_run_refreshes_the_heartbeat_while_a_prompt_is_pending():
    # elapsed just over INTERVAL, nowhere near a budget checkpoint — a ping
    # is due the instant `run()` starts, so `ask()` blocks immediately.
    jsonl.append(open_event(ts=time.time() - INTERVAL - 1.0))
    release = threading.Event()
    blocker = SlowBlocker([Answers(False, "n", None, {}, 0.0, 0.0)], release)

    def sleep_and_stop(seconds):
        jsonl.append(events.make("loop_abandoned", ts=time.time(), loop_id=1))

    runner = threading.Thread(
        target=daemon.run,
        kwargs=dict(poll_s=0.01, blocker=blocker, sleep_fn=sleep_and_stop, now_fn=time.time),
    )
    runner.start()
    try:
        deadline = time.time() + 2.0
        first = _wait_for_heartbeat(deadline)

        # ask() is still blocked on `release` — the pulse thread, not the
        # main loop, must be the one advancing the heartbeat from here.
        second = _wait_for_heartbeat(deadline, after=first)
        assert second > first
    finally:
        release.set()
        runner.join(timeout=2.0)
    assert not runner.is_alive()


def test_pulse_once_does_not_recreate_a_pidfile_stop_removed():
    paths.pid_path().write_text(json.dumps({"pid": 42, "heartbeat": 1.0}))
    paths.pid_path().unlink()
    assert daemon._pulse_once(42, lambda: 2.0) is False
    assert not paths.pid_path().exists()


def test_pulse_once_leaves_a_stolen_pidfile_alone():
    paths.pid_path().write_text(json.dumps({"pid": 999_999, "heartbeat": 1.0}))
    assert daemon._pulse_once(42, lambda: 2.0) is False
    assert json.loads(paths.pid_path().read_text())["pid"] == 999_999
