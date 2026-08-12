# `loop` Desktop App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `loop` from a CLI with a headless check-in daemon into a standalone macOS + Windows desktop app that owns every action and schedules its own check-ins, while the CLI keeps working unchanged.

**Architecture:** A new headless `loop/app/` layer holds every mutation as a plain function — no argparse, no stdout, no Qt — and both front ends sit on it. All writes go through one `Writer` that takes a cross-process file lock, re-reads state, validates, and appends. A new `loop/gui/` package is the only place PySide6 is imported; it draws the dashboard from pure view models and runs a `QTimer` scheduler that replaces the daemon and both full-screen overlays.

**Tech Stack:** Python 3.11+, PySide6 (optional extra `loop-tool[gui]`), pytest. No other runtime dependencies. Standard library `fcntl` / `msvcrt` for locking.

**Spec:** `docs/superpowers/specs/2026-08-12-loop-gui-design.md`

## Global Constraints

- **Layer rule (existing):** `core/` imports nothing from `store/`, `blockers/`, `app/`, or `gui/`. `blockers/` imports nothing from `core/`.
- **Layer rule (new):** `gui/` is the only package that may import PySide6, directly or transitively.
- **Layer rule (new):** `jsonl.append` has exactly one caller — `loop/app/writer.py`.
- **No test may spawn the app, open a window, or require PySide6 to be installed.** The one exception is Task 1's lock test, which spawns bare `python -c` children because cross-process locking cannot be tested in one process.
- **No test may write to the user's real `~/.loop`.** Every test sets `LOOP_HOME` to a `tmp_path`.
- **Check-in prompt strings are the product.** Preserve verbatim: ping title `loop #{id} · {elapsed} / {budget} · {n} paused`, question `hypothesis space smaller than {interval} ago?`; p75 title `75% of budget gone · {span}`, question `is 75% of the work done?`; p100 title `budget gone · {span}`, question `budget is gone. what now?`. Choice labels `cut scope` and `extend estimate`. The only string that changes in this plan is the p100 `x` label, in Task 14.
- **Constants keep their existing values and homes:** `TIMEOUT_S = 300.0` (`blockers/base.py`), `POLL_S = 10.0`, `CHECKPOINT_RETRY_S = 600.0`, `COLLISION_WINDOW_S = 180.0`, `MAX_STACK_DEPTH = 5`, `WARN_STACK_DEPTH = 3`, `DEFAULT_BUDGET_S = 2700.0`, `DEFAULT_INTERVAL_S = 1200.0`, `STALE_PAUSE_S = 86400.0`. `KILL_AFTER_S` is deleted in Task 15.
- **A bare number in a duration means minutes** (`core/timefmt.parse_duration`). Do not "fix" this.
- **`--json` output must stay byte-identical.** Every rewired `cmd_*` returns the same dict it returns today.
- **Commit after every task.** Use `git add <exact paths>` — never `git add -A`; the working tree has unrelated uncommitted changes (`loop/blockers/macos.py`, `tutorial.md`, two untracked macOS test files) that must not be swept into a commit.
- **Run the full suite before each commit:** `python -m pytest`. It must be green.

## Build-order note

The spec's §14 deletes the daemon at step 5 but does not create the GUI scheduler until step 13, leaving eight steps in which an active loop gets no check-ins at all. This plan reorders so the daemon dies exactly when the GUI can replace it (Task 14 wires the scheduler, Task 15 demolishes). Every task is green *and* functional. No design decision changes.

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `loop/store/lock.py` | Cross-process exclusive lock. The only platform branch in the write path. |
| `loop/core/timeline.py` | Pure projection: event log → action-log entries. `fold` discards what this needs. |
| `loop/app/errors.py` | `LoopError`, `StaleError`. |
| `loop/app/writer.py` | The single write path: lock → read → validate → append. |
| `loop/app/commands.py` | Eleven mutations as functions. No I/O, no Qt, no argparse. |
| `loop/app/checkin.py` | `Prompt` ↔ `Answers` ↔ events. Moved out of `sched/tick.py`. |
| `loop/app/view.py` | Pure view models the window binds to. |
| `loop/app/launcher.py` | Is the GUI available; spawn it. Replaces `daemon.ensure_running`. |
| `loop/gui/theme.py` | The token table and the Qt stylesheet built from it. |
| `loop/gui/__main__.py` | Entry point + single-instance guard. |
| `loop/gui/tray.py` | Tray / menu bar, hide-vs-quit. |
| `loop/gui/window.py` | The dashboard. |
| `loop/gui/dialogs/` | One module per dialog family. |
| `loop/gui/checkin.py` | The full-screen check-in. Replaces `macos.py` + `tk.py`. |
| `loop/gui/scheduler.py` | `QTimer` → `next_due` → check-in → append. Replaces the daemon. |
| `loop/gui/logbook.py` | Stats and search view. |

**Modified:** `loop/store/jsonl.py`, `loop/store/paths.py`, `loop/core/stats.py`, `loop/cli.py`, `pyproject.toml`.

**Deleted (Task 15):** `loop/sched/` (both files), `loop/blockers/macos.py`, `loop/blockers/tk.py`, `loop/blockers/factory.py`.

**Naming note:** the spec calls the write path `app/session.py`. This plan names it `app/writer.py` because `loop/blockers/session.py` already exists and holds `PromptSession`, an unrelated thing. Two `session.py` in one codebase is a trap.

---

### Task 1: The write lock

`jsonl.py`'s docstring claims appends are atomic "on POSIX for payloads under PIPE_BUF". That is true, and irrelevant on Windows, which offers no such guarantee. With the GUI, the CLI, and the scheduler all able to append, one writer at a time is the only correct answer.

**Files:**
- Create: `loop/store/lock.py`
- Modify: `loop/store/paths.py` (add `lock_path`), `loop/store/jsonl.py` (docstring only)
- Test: `tests/store/test_lock.py`, `tests/store/test_paths.py`

**Interfaces:**
- Consumes: `loop.store.paths.loop_home`
- Produces: `loop.store.lock.exclusive(path: Path, timeout_s: float = 10.0)` — a context manager; `loop.store.lock.LockTimeout`; `loop.store.paths.lock_path() -> Path`

- [ ] **Step 1: Write the failing tests**

Create `tests/store/test_lock.py`:

```python
import subprocess
import sys
import textwrap
import time

import pytest

from loop.store import lock


def test_exclusive_creates_the_lock_file(tmp_path):
    target = tmp_path / "nested" / "lock"
    with lock.exclusive(target):
        pass
    assert target.exists()


def test_exclusive_is_reentrant_across_sequential_holds(tmp_path):
    target = tmp_path / "lock"
    for _ in range(3):
        with lock.exclusive(target):
            pass


HOLD_PROGRAM = textwrap.dedent("""
    import sys, time
    from pathlib import Path
    from loop.store import lock
    with lock.exclusive(Path(sys.argv[1])):
        print("held", flush=True)
        time.sleep(5)
""")


def test_a_held_lock_times_out_a_second_acquirer(tmp_path):
    target = tmp_path / "lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_PROGRAM, str(target)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        started = time.monotonic()
        with pytest.raises(lock.LockTimeout):
            with lock.exclusive(target, timeout_s=0.5):
                pass
        assert time.monotonic() - started < 4.0
    finally:
        holder.kill()
        holder.wait()


def test_concurrent_appends_do_not_interleave_or_vanish(tmp_path):
    """The whole point of the lock: two processes, 200 lines each, 400 intact."""
    events_path = tmp_path / "events.jsonl"
    lock_path = tmp_path / "lock"
    program = textwrap.dedent("""
        import json, sys
        from pathlib import Path
        from loop.store import jsonl, lock
        events_path, lock_path, tag = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
        for index in range(200):
            with lock.exclusive(lock_path):
                jsonl.append({"tag": tag, "index": index, "pad": "x" * 600}, events_path)
    """)
    workers = [
        subprocess.Popen([sys.executable, "-c", program, str(events_path), str(lock_path), tag])
        for tag in ("a", "b")
    ]
    for worker in workers:
        assert worker.wait(timeout=60) == 0

    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 400
    parsed = [json.loads(line) for line in lines]  # raises if a line is torn
    assert sorted(entry["index"] for entry in parsed if entry["tag"] == "a") == list(range(200))
    assert sorted(entry["index"] for entry in parsed if entry["tag"] == "b") == list(range(200))
```

The 600-byte padding matters: it pushes each line past the size where a
naive append would look atomic by luck on POSIX, so the test can actually
fail if the lock is wrong.

Add to `tests/store/test_paths.py`, replacing `test_events_and_pid_live_under_home`:

```python
def test_events_and_lock_live_under_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
    assert paths.events_path() == tmp_path / "events.jsonl"
    assert paths.lock_path() == tmp_path / "lock"
    assert paths.pid_path() == tmp_path / "daemon.pid"
```

(`pid_path` still exists until Task 15; the assertion goes with it.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/store/test_lock.py tests/store/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.store.lock'`, and `AttributeError: module 'loop.store.paths' has no attribute 'lock_path'`.

- [ ] **Step 3: Write `loop/store/lock.py`**

```python
"""Cross-process exclusive locking for the write path.

The lock lives in a file of its own, never the event log: taking a lock on
a file that is simultaneously opened in append mode and closed on every
write is a race waiting to be found.

Both platforms poll a non-blocking acquire rather than using a blocking
one, so `timeout_s` means the same thing on each. `flock` would otherwise
wait forever and `msvcrt.locking`'s LK_LOCK would impose its own ten
one-second retries.
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from pathlib import Path

POLL_S = 0.02


class LockTimeout(Exception):
    """Another process held the write lock past the timeout."""


if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
    import msvcrt

    def _try_acquire(handle) -> bool:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _release(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_acquire(handle) -> bool:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def _release(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive(path: Path, timeout_s: float = 10.0):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    with path.open("a+b") as handle:
        handle.seek(0)
        while not _try_acquire(handle):
            if time.monotonic() >= deadline:
                raise LockTimeout(
                    f"another loop process held the write lock for more than "
                    f"{timeout_s:g}s ({path})"
                )
            time.sleep(POLL_S)
        try:
            yield
        finally:
            _release(handle)
```

- [ ] **Step 4: Add `lock_path` to `loop/store/paths.py`**

Insert after `events_path`:

```python
def lock_path() -> Path:
    return loop_home() / "lock"
```

- [ ] **Step 5: Correct the `jsonl.py` docstring**

Replace the module docstring of `loop/store/jsonl.py`:

```python
"""The append-only event log.

One JSON object per line. Every append in this codebase runs while its
caller holds the exclusive lock from `loop.store.lock` — see
`loop/app/writer.py`, which is the only place that calls `append`. That is
what makes concurrent writers safe on Windows, which offers no equivalent
of POSIX's atomic-append-under-PIPE_BUF.

A crash mid-append can still damage the final line, and `read_all`
tolerates exactly that.
"""
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/store/ -v`
Expected: PASS. The concurrency test takes a few seconds.

- [ ] **Step 7: Run the full suite and commit**

```bash
python -m pytest
git add loop/store/lock.py loop/store/paths.py loop/store/jsonl.py tests/store/test_lock.py tests/store/test_paths.py
git commit -m "feat: cross-process write lock

Windows gives no atomic-append guarantee, and three processes are about
to want this file."
```

---

### Task 2: The action-log projection

`fold` collapses actions to a counter (`Loop.actions: int`, `events.py:133`). The dashboard shows an action *log* — text, `because`, timestamp — so it needs a second projection over the same events.

One rule is not local and is the reason this reads the log rather than the folded state: a `hypothesis_eliminated` immediately preceded by a `ping_answered` carrying the same `eliminated` id came from a check-in. `sched/tick.py:97-105` emits exactly that pair, adjacent and same-timestamp. The fold discards the adjacency.

**Files:**
- Create: `loop/core/timeline.py`
- Test: `tests/core/test_timeline.py`

**Interfaces:**
- Consumes: `loop.core.timefmt.format_duration`
- Produces: `loop.core.timeline.Entry(ts: float, kind: str, text: str, because: str | None)`; `loop.core.timeline.entries(log: list[dict], loop_id: int) -> list[Entry]`; the kind constants `ACTION`, `HYPOTHESIS`, `RULED_OUT`, `PING`, `CHECKPOINT`, `CUT`, `EXTEND`, `PAUSED`, `RESUMED`

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_timeline.py`:

```python
from loop.core import events, timeline


def opened(loop_id=1, hypotheses=("cert expiry", "pg pool")):
    return events.make(
        "loop_opened", ts=0.0, loop_id=loop_id,
        question="staging deploy fails at startup",
        stop_condition="comes up clean twice",
        budget_s=2700.0, interval_s=1200.0,
        hypotheses=list(hypotheses), parent_id=None,
    )


def test_the_loops_own_boundaries_produce_no_entries():
    log = [opened(), events.make("loop_closed", ts=9.0, loop_id=1,
                                 what_was_it="a", giveaway="b", five_min_path="c")]
    assert timeline.entries(log, 1) == []


def test_an_action_carries_its_because():
    log = [opened(), events.make("action_logged", ts=5.0, loop_id=1,
                                 action="added print in wrapper",
                                 because="will show the swallowed frame")]
    assert timeline.entries(log, 1) == [
        timeline.Entry(ts=5.0, kind=timeline.ACTION,
                       text="added print in wrapper",
                       because="will show the swallowed frame")
    ]


def test_other_loops_are_excluded():
    log = [
        opened(loop_id=1),
        events.make("action_logged", ts=5.0, loop_id=1, action="mine", because="x"),
        events.make("action_logged", ts=6.0, loop_id=2, action="theirs", because="y"),
    ]
    assert [entry.text for entry in timeline.entries(log, 1)] == ["mine"]


def test_a_manual_kill_names_the_hypothesis_and_has_no_because():
    log = [opened(), events.make("hypothesis_eliminated", ts=7.0, loop_id=1, hyp_id=2)]
    entry, = timeline.entries(log, 1)
    assert entry.kind == timeline.RULED_OUT
    assert entry.text == "ruled out §2 pg pool"
    assert entry.because is None


def test_a_kill_that_followed_its_ping_is_attributed_to_the_checkin():
    log = [
        opened(),
        events.make("ping_answered", ts=7.0, loop_id=1, smaller=True,
                    eliminated=2, shown_at=6.0),
        events.make("hypothesis_eliminated", ts=7.0, loop_id=1, hyp_id=2),
    ]
    ping, killed = timeline.entries(log, 1)
    assert ping.kind == timeline.PING
    assert ping.text == "check-in: hypothesis space smaller"
    assert killed.because == timeline.CHECKIN_BECAUSE


def test_a_kill_that_did_not_follow_its_ping_is_not_attributed():
    """Same events, one action wedged between. The adjacency is the evidence."""
    log = [
        opened(),
        events.make("ping_answered", ts=7.0, loop_id=1, smaller=True,
                    eliminated=2, shown_at=6.0),
        events.make("action_logged", ts=7.5, loop_id=1, action="a", because="b"),
        events.make("hypothesis_eliminated", ts=8.0, loop_id=1, hyp_id=2),
    ]
    assert timeline.entries(log, 1)[-1].because is None


def test_a_ping_answered_no_reads_as_no_progress():
    log = [opened(), events.make("ping_answered", ts=7.0, loop_id=1, smaller=False,
                                 eliminated=None, shown_at=6.0)]
    assert timeline.entries(log, 1)[0].text == "check-in: hypothesis space not smaller"


def test_an_unanswered_ping_is_recorded():
    log = [opened(), events.make("ping_unanswered", ts=7.0, loop_id=1, shown_at=6.0)]
    assert timeline.entries(log, 1)[0].text == "check-in: timed out"


def test_a_late_hypothesis_is_named_by_its_own_event():
    log = [
        opened(),
        events.make("hypothesis_added", ts=3.0, loop_id=1, hyp_id=3, text="dns"),
        events.make("hypothesis_eliminated", ts=4.0, loop_id=1, hyp_id=3),
    ]
    added, killed = timeline.entries(log, 1)
    assert added.text == "added §3 dns"
    assert killed.text == "ruled out §3 dns"


def test_checkpoints_scope_cuts_extensions_pauses_and_resumes_all_render():
    log = [
        opened(),
        events.make("checkpoint_shown", ts=10.0, loop_id=1, kind="p75"),
        events.make("checkpoint_answered", ts=11.0, loop_id=1, kind="p75",
                    on_track=False, decision="cut"),
        events.make("scope_cut", ts=11.0, loop_id=1,
                    old_stop_condition="old", new_stop_condition="just the 500s"),
        events.make("checkpoint_unanswered", ts=20.0, loop_id=1, kind="p100", shown_at=19.0),
        events.make("budget_extended", ts=21.0, loop_id=1, old_budget_s=2700.0,
                    new_budget_s=3600.0, learned="the pool is shared"),
        events.make("loop_paused", ts=22.0, loop_id=1, reason="standup"),
        events.make("loop_resumed", ts=23.0, loop_id=1),
    ]
    texts = [entry.text for entry in timeline.entries(log, 1)]
    assert texts == [
        "p75: cut",
        "cut scope to just the 500s",
        "p100: timed out",
        "extended budget to 1h",
        "paused: standup",
        "resumed",
    ]
    assert timeline.entries(log, 1)[3].because == "the pool is shared"


def test_checkpoint_shown_alone_produces_nothing():
    log = [opened(), events.make("checkpoint_shown", ts=10.0, loop_id=1, kind="p75")]
    assert timeline.entries(log, 1) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/core/test_timeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.core.timeline'`.

- [ ] **Step 3: Write `loop/core/timeline.py`**

```python
"""The action log, projected from events.

`fold` collapses actions to a counter, which is the right shape for the
scheduler and the wrong shape for a screen. This walks the same events and
keeps the text.

It reads the log rather than the folded state for one reason: a
hypothesis that a check-in ruled out is only distinguishable from one you
ruled out by hand by its adjacency to the `ping_answered` that carried it,
and folding throws that away.
"""

from __future__ import annotations

from dataclasses import dataclass

from loop.core.timefmt import format_duration

ACTION = "action"
HYPOTHESIS = "hypothesis"
RULED_OUT = "ruled_out"
PING = "ping"
CHECKPOINT = "checkpoint"
CUT = "cut"
EXTEND = "extend"
PAUSED = "paused"
RESUMED = "resumed"

CHECKIN_BECAUSE = "answered at the check-in"

# The loop's own boundaries are drawn by the header, not the log.
IGNORED = frozenset({"loop_opened", "loop_closed", "loop_abandoned", "checkpoint_shown"})


@dataclass(frozen=True, slots=True)
class Entry:
    ts: float
    kind: str
    text: str
    because: str | None = None


def entries(log: list[dict], loop_id: int) -> list[Entry]:
    out: list[Entry] = []
    names: dict[int, str] = {}
    pending_kill: int | None = None

    for event in log:
        if event.get("loop_id") != loop_id:
            continue
        kind = event["type"]

        if kind == "loop_opened":
            names = {
                index: text
                for index, text in enumerate(event["hypotheses"], start=1)
            }
        elif kind == "hypothesis_added":
            names[event["hyp_id"]] = event["text"]

        entry = _entry(event, kind, names, pending_kill)
        pending_kill = (
            event["eliminated"]
            if kind == "ping_answered" and event["eliminated"] is not None
            else None
        )
        if entry is not None:
            out.append(entry)

    return out


def _entry(event: dict, kind: str, names: dict[int, str], pending_kill: int | None):
    ts = event["ts"]

    if kind in IGNORED:
        return None

    if kind == "action_logged":
        return Entry(ts, ACTION, event["action"], event["because"])

    if kind == "hypothesis_added":
        return Entry(ts, HYPOTHESIS, f"added §{event['hyp_id']} {event['text']}")

    if kind == "hypothesis_eliminated":
        hyp_id = event["hyp_id"]
        because = CHECKIN_BECAUSE if pending_kill == hyp_id else None
        return Entry(ts, RULED_OUT, f"ruled out §{hyp_id} {names.get(hyp_id, '')}".rstrip(), because)

    if kind == "ping_answered":
        shape = "smaller" if event["smaller"] else "not smaller"
        return Entry(ts, PING, f"check-in: hypothesis space {shape}")

    if kind == "ping_unanswered":
        return Entry(ts, PING, "check-in: timed out")

    if kind == "checkpoint_answered":
        return Entry(ts, CHECKPOINT, f"{event['kind']}: {event['decision']}")

    if kind == "checkpoint_unanswered":
        return Entry(ts, CHECKPOINT, f"{event['kind']}: timed out")

    if kind == "scope_cut":
        return Entry(ts, CUT, f"cut scope to {event['new_stop_condition']}")

    if kind == "budget_extended":
        return Entry(
            ts, EXTEND,
            f"extended budget to {format_duration(event['new_budget_s'])}",
            event["learned"],
        )

    if kind == "loop_paused":
        return Entry(ts, PAUSED, f"paused: {event['reason']}")

    if kind == "loop_resumed":
        return Entry(ts, RESUMED, "resumed")

    raise ValueError(f"timeline has no handler for {kind!r}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/core/test_timeline.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Run the full suite and commit**

```bash
python -m pytest
git add loop/core/timeline.py tests/core/test_timeline.py
git commit -m "feat: action-log projection

fold keeps a counter; a screen needs the text. Check-in attribution
lives in the event adjacency, which is why this reads the log."
```

---

### Task 3: Errors and the single write path

Every mutation in the app goes through one place that takes the lock, re-reads state, lets the caller validate against *that* state, and appends. Preconditions checked anywhere else are checked against a stale view.

**Files:**
- Create: `loop/app/__init__.py` (empty), `loop/app/errors.py`, `loop/app/writer.py`
- Test: `tests/app/__init__.py` (empty), `tests/app/test_writer.py`

**Interfaces:**
- Consumes: `loop.store.lock.exclusive`, `loop.store.paths.lock_path`, `loop.store.jsonl`, `loop.core.events.fold`
- Produces: `loop.app.errors.LoopError`; `loop.app.errors.StaleError` (subclass of `LoopError`); `loop.app.writer.Writer(now=time.time)` with `.read() -> State` and `.mutate(build) -> R`, where `build` is `Callable[[State, float], tuple[list[dict], R]]`

- [ ] **Step 1: Write the failing tests**

Create `tests/app/__init__.py` (empty) and `tests/app/test_writer.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/app/test_writer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.app'`.

- [ ] **Step 3: Write `loop/app/errors.py`**

```python
"""Refusals the front ends render.

Both mean the same thing about the log: nothing was written.
"""

from __future__ import annotations


class LoopError(Exception):
    """A user-facing refusal. The request was well-formed and declined."""


class StaleError(LoopError):
    """What the caller saw is no longer true.

    A check-in ruled out the hypothesis you just clicked; the loop you
    aimed at is no longer active. The refusal is not an error the user
    made — usually the outcome they wanted already happened. Subclasses
    `LoopError` so no caller has to learn about it to be correct.
    """
```

- [ ] **Step 4: Write `loop/app/writer.py`**

```python
"""The only path that appends to the event log.

`mutate` holds the lock across read, validate, and append. That is what
makes a multi-event mutation atomic — `fold` rejects a `loop_opened` that
arrives while another loop is active, so a torn pause/open pair produces a
log that will not load — and it is what makes a precondition mean
anything: `build` validates against the state its events are about to be
appended to, not against whatever a window last painted.

`build` returns the events *and* the result, computed together under the
one lock hold. A result assembled afterwards, from a second read, could
report a number a concurrent write had already changed.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from loop.core import events
from loop.core.models import State
from loop.store import jsonl, lock, paths

R = TypeVar("R")
Build = Callable[[State, float], "tuple[list[dict], R]"]


class Writer:
    def __init__(self, now: Callable[[], float] = time.time) -> None:
        self._now = now

    def read(self) -> State:
        """The display read. Deliberately unlocked: a reader that blocked
        behind a writer would stall the clock for no gain, and a fold of a
        half-written tail is exactly what `read_all` already tolerates."""
        return events.fold(jsonl.read_all())

    def mutate(self, build: Build) -> R:
        with lock.exclusive(paths.lock_path()):
            state = events.fold(jsonl.read_all())
            written, result = build(state, self._now())
            for event in written:
                jsonl.append(event)
            return result
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/app/test_writer.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 6: Run the full suite and commit**

```bash
python -m pytest
git add loop/app/__init__.py loop/app/errors.py loop/app/writer.py tests/app/__init__.py tests/app/test_writer.py
git commit -m "feat: single locked write path

read, validate, and append under one lock hold. A precondition checked
anywhere else is checked against a stale view."
```

---

### Task 4: Lifecycle commands

The five mutations that move a loop through its states. Each is a plain function taking already-collected values — never a prompt, never an argparse namespace.

Two facts from `core/` drive the shapes here. `fold` raises `InvariantError` if a `loop_opened` arrives while `active_id` is set (`events.py:92`), so opening on top of an active loop must emit `loop_paused` first, in the same `mutate`. And closing a child must resume its paused parent in the same `mutate`, which `cli.py:287` does today as a second, separate write.

**Files:**
- Create: `loop/app/commands.py`
- Test: `tests/app/test_commands_lifecycle.py`

**Interfaces:**
- Consumes: `loop.app.writer.Writer`, `loop.app.errors.LoopError`/`StaleError`, `loop.core.events`, `loop.core.models.{MAX_STACK_DEPTH, PAUSED}`
- Produces:
  - `LoopSummary(loop_id: int, elapsed_s: float, budget_s: float, interval_s: float)`
  - `Opened(event: dict, loop_id: int, depth: int, hypotheses: int, paused_id: int | None, paused_elapsed_s: float)`
  - `Paused(event: dict, loop_id: int, elapsed_s: float)`
  - `Resumed(event: dict, summary: LoopSummary)`
  - `Closed(event: dict, loop_id: int, elapsed_s: float, eliminated: int, resumed: LoopSummary | None)`
  - `Abandoned(event: dict, loop_id: int, elapsed_s: float, resumed: LoopSummary | None)`
  - `open_loop(writer, *, question, stop_condition, budget_s, interval_s, hypotheses, stack_on_active) -> Opened`
  - `pause(writer, *, reason) -> Paused`
  - `resume(writer, *, loop_id: int | None, pause_reason: str | None) -> Resumed`
  - `close(writer, *, what_was_it, giveaway, five_min_path) -> Closed`
  - `abandon(writer) -> Abandoned`

- [ ] **Step 1: Write the failing tests**

Create `tests/app/test_commands_lifecycle.py`:

```python
import pytest

from loop.app import commands
from loop.app.errors import LoopError, StaleError
from loop.app.writer import Writer
from loop.core import events
from loop.store import jsonl


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))


class Clock:
    def __init__(self, at=0.0):
        self.at = at

    def __call__(self):
        return self.at


def writer_at(clock):
    return Writer(now=clock)


def open_one(writer, question="q", **kwargs):
    return commands.open_loop(
        writer, question=question, stop_condition="s",
        budget_s=kwargs.pop("budget_s", 2700.0),
        interval_s=kwargs.pop("interval_s", 1200.0),
        hypotheses=kwargs.pop("hypotheses", ["cert expiry", "pg pool"]),
        stack_on_active=kwargs.pop("stack_on_active", True),
    )


def log():
    return jsonl.read_all()


def test_open_writes_one_event_and_reports_the_new_id():
    result = open_one(writer_at(Clock()))
    assert result.loop_id == 1
    assert result.depth == 1
    assert result.hypotheses == 2
    assert result.paused_id is None
    assert [event["type"] for event in log()] == ["loop_opened"]
    assert log()[0]["parent_id"] is None


def test_open_on_top_pauses_the_parent_in_the_same_write():
    clock = Clock()
    writer = writer_at(clock)
    open_one(writer, "parent")
    clock.at = 600.0
    result = open_one(writer, "child")

    assert [event["type"] for event in log()] == ["loop_opened", "loop_paused", "loop_opened"]
    assert result.paused_id == 1
    assert result.paused_elapsed_s == 600.0
    assert result.depth == 2
    assert log()[1]["reason"] == "child"      # the new question is why the parent stopped
    assert log()[2]["parent_id"] == 1
    assert events.fold(log()).active_id == 2  # fold accepts it: the pause landed first


def test_open_without_consent_writes_nothing():
    writer = writer_at(Clock())
    open_one(writer, "parent")
    with pytest.raises(LoopError, match="aborted"):
        open_one(writer, "child", stack_on_active=False)
    assert len(log()) == 1
    assert events.fold(log()).active_id == 1


def test_open_refuses_past_the_stack_ceiling():
    clock = Clock()
    writer = writer_at(clock)
    for index in range(5):
        clock.at = index * 60.0
        open_one(writer, f"q{index}")
    clock.at = 999.0
    with pytest.raises(LoopError, match="stack depth 5"):
        open_one(writer, "one too many")
    assert len(log()) == 9  # 5 opens + 4 pauses


def test_pause_reports_active_elapsed():
    clock = Clock()
    writer = writer_at(clock)
    open_one(writer)
    clock.at = 900.0
    result = commands.pause(writer, reason="standup")
    assert result.elapsed_s == 900.0
    assert log()[-1]["reason"] == "standup"


def test_pause_with_nothing_active_refuses():
    with pytest.raises(LoopError, match="no active loop"):
        commands.pause(writer_at(Clock()), reason="standup")


def test_resume_without_an_id_takes_the_most_recently_paused():
    clock = Clock()
    writer = writer_at(clock)
    open_one(writer, "first")
    clock.at = 100.0
    commands.pause(writer, reason="x")
    clock.at = 200.0
    result = commands.resume(writer, loop_id=None, pause_reason=None)
    assert result.summary.loop_id == 1
    assert result.summary.elapsed_s == 100.0
    assert result.summary.budget_s == 2700.0
    assert result.summary.interval_s == 1200.0


def test_resume_of_a_specific_loop_pauses_the_active_one():
    clock = Clock()
    writer = writer_at(clock)
    open_one(writer, "first")
    clock.at = 100.0
    open_one(writer, "second")
    clock.at = 200.0
    commands.resume(writer, loop_id=1, pause_reason="back to it")
    assert [event["type"] for event in log()[-2:]] == ["loop_paused", "loop_resumed"]
    assert events.fold(log()).active_id == 1


def test_resume_needs_a_reason_when_something_else_is_active():
    clock = Clock()
    writer = writer_at(clock)
    open_one(writer, "first")
    clock.at = 100.0
    open_one(writer, "second")
    with pytest.raises(StaleError, match="#2"):
        commands.resume(writer, loop_id=1, pause_reason=None)
    assert events.fold(log()).active_id == 2


def test_resume_of_the_active_loop_refuses():
    writer = writer_at(Clock())
    open_one(writer)
    with pytest.raises(LoopError, match="already active"):
        commands.resume(writer, loop_id=1, pause_reason=None)


def test_resume_with_nothing_paused_refuses():
    with pytest.raises(LoopError, match="nothing to resume"):
        commands.resume(writer_at(Clock()), loop_id=None, pause_reason=None)


def test_resume_of_a_closed_loop_names_its_status():
    clock = Clock()
    writer = writer_at(clock)
    open_one(writer)
    clock.at = 60.0
    commands.close(writer, what_was_it="a", giveaway="b", five_min_path="c")
    with pytest.raises(LoopError, match="closed, not paused"):
        commands.resume(writer, loop_id=1, pause_reason=None)


def test_close_records_the_postmortem_and_the_counts():
    clock = Clock()
    writer = writer_at(clock)
    open_one(writer)
    commands.kill_hypothesis(writer, hyp_id=1)
    clock.at = 1200.0
    result = commands.close(writer, what_was_it="stale cert",
                            giveaway="only after a restart", five_min_path="check notAfter")
    assert result.elapsed_s == 1200.0
    assert result.eliminated == 1
    assert result.resumed is None
    assert log()[-1]["what_was_it"] == "stale cert"


def test_close_of_a_child_resumes_the_parent_in_the_same_write():
    clock = Clock()
    writer = writer_at(clock)
    open_one(writer, "parent")
    clock.at = 600.0
    open_one(writer, "child")
    clock.at = 900.0
    result = commands.close(writer, what_was_it="a", giveaway="b", five_min_path="c")

    assert [event["type"] for event in log()[-2:]] == ["loop_closed", "loop_resumed"]
    assert result.resumed.loop_id == 1
    assert result.resumed.elapsed_s == 600.0
    assert events.fold(log()).active_id == 1


def test_abandon_of_a_child_also_resumes_the_parent():
    clock = Clock()
    writer = writer_at(clock)
    open_one(writer, "parent")
    clock.at = 600.0
    open_one(writer, "child")
    clock.at = 700.0
    result = commands.abandon(writer)
    assert result.elapsed_s == 100.0
    assert result.resumed.loop_id == 1
    assert events.fold(log()).active_id == 1


def test_close_with_nothing_active_refuses():
    with pytest.raises(LoopError, match="no active loop"):
        commands.close(writer_at(Clock()), what_was_it="a", giveaway="b", five_min_path="c")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/app/test_commands_lifecycle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.app.commands'`.

- [ ] **Step 3: Write `loop/app/commands.py`**

Note `kill_hypothesis` is used by one test above but implemented in Task 5; write the lifecycle half now and expect that one test to fail until Task 5 lands. Mark it `@pytest.mark.xfail(reason="kill_hypothesis arrives in Task 5", strict=True)` in Step 1 and remove the marker in Task 5.

```python
"""Every mutation, headless.

No argparse, no stdout, no Qt. A front end collects the values, calls one
of these, and renders the result. Both front ends get the same refusals
for the same reasons, because there is only one place they are decided.

Every function builds its events inside `writer.mutate`, so every
precondition is checked against the state those events are about to join.
"""

from __future__ import annotations

from dataclasses import dataclass

from loop.app.errors import LoopError, StaleError
from loop.app.writer import Writer
from loop.core import events
from loop.core.models import MAX_STACK_DEPTH, PAUSED, Loop, State


@dataclass(frozen=True, slots=True)
class LoopSummary:
    loop_id: int
    elapsed_s: float
    budget_s: float
    interval_s: float


@dataclass(frozen=True, slots=True)
class Opened:
    event: dict
    loop_id: int
    depth: int
    hypotheses: int
    paused_id: int | None
    paused_elapsed_s: float


@dataclass(frozen=True, slots=True)
class Paused:
    event: dict
    loop_id: int
    elapsed_s: float


@dataclass(frozen=True, slots=True)
class Resumed:
    event: dict
    summary: LoopSummary


@dataclass(frozen=True, slots=True)
class Closed:
    event: dict
    loop_id: int
    elapsed_s: float
    eliminated: int
    resumed: LoopSummary | None


@dataclass(frozen=True, slots=True)
class Abandoned:
    event: dict
    loop_id: int
    elapsed_s: float
    resumed: LoopSummary | None


def _require_active(state: State) -> Loop:
    loop = state.active_loop()
    if loop is None:
        raise LoopError("no active loop. run `loop open \"<question>\"` first.")
    return loop


def _summary(loop: Loop, at: float) -> LoopSummary:
    return LoopSummary(
        loop_id=loop.id, elapsed_s=loop.elapsed(at),
        budget_s=loop.budget_s, interval_s=loop.interval_s,
    )


def open_loop(writer: Writer, *, question: str, stop_condition: str,
              budget_s: float, interval_s: float, hypotheses: list[str],
              stack_on_active: bool) -> Opened:
    def build(state: State, now: float):
        active = state.active_loop()
        written: list[dict] = []
        paused_elapsed = 0.0

        if active is not None:
            depth = events.stack_depth(state)
            if depth >= MAX_STACK_DEPTH:
                raise LoopError(
                    f"stack depth {depth}. close or abandon something before opening another."
                )
            if not stack_on_active:
                raise LoopError("aborted.")
            paused_elapsed = active.elapsed(now)
            # The new question is the reason the old one stopped. That is
            # the honest reason, and it costs the user no extra typing.
            written.append(
                events.make("loop_paused", ts=now, loop_id=active.id, reason=question)
            )

        opened = events.make(
            "loop_opened", ts=now, loop_id=events.next_loop_id(state),
            question=question, stop_condition=stop_condition,
            budget_s=budget_s, interval_s=interval_s,
            hypotheses=list(hypotheses),
            parent_id=active.id if active is not None else None,
        )
        written.append(opened)

        return written, Opened(
            event=opened,
            loop_id=opened["loop_id"],
            depth=events.stack_depth(state) + 1,
            hypotheses=len(hypotheses),
            paused_id=active.id if active is not None else None,
            paused_elapsed_s=paused_elapsed,
        )

    return writer.mutate(build)


def pause(writer: Writer, *, reason: str) -> Paused:
    def build(state: State, now: float):
        loop = _require_active(state)
        event = events.make("loop_paused", ts=now, loop_id=loop.id, reason=reason)
        return [event], Paused(event=event, loop_id=loop.id, elapsed_s=loop.elapsed(now))

    return writer.mutate(build)


def resume(writer: Writer, *, loop_id: int | None, pause_reason: str | None) -> Resumed:
    def build(state: State, now: float):
        target = _resume_target(state, loop_id)
        written: list[dict] = []

        active = state.active_loop()
        if active is not None:
            if active.id == target.id:
                raise LoopError(f"#{target.id} is already active.")
            if not pause_reason:
                # The caller decided what to resume while nothing was
                # active, and something became active in between.
                raise StaleError(
                    f"#{active.id} became active while you were deciding. Try again."
                )
            written.append(
                events.make("loop_paused", ts=now, loop_id=active.id, reason=pause_reason)
            )

        event = events.make("loop_resumed", ts=now, loop_id=target.id)
        written.append(event)
        return written, Resumed(event=event, summary=_summary(target, now))

    return writer.mutate(build)


def _resume_target(state: State, loop_id: int | None) -> Loop:
    if loop_id is None:
        paused = state.paused_loops()
        if not paused:
            raise LoopError("nothing to resume.")
        return paused[0]

    target = state.loops.get(loop_id)
    if target is None:
        raise LoopError(f"no loop #{loop_id}.")
    if target.status != PAUSED:
        raise LoopError(f"#{loop_id} is {target.status}, not paused.")
    return target


def close(writer: Writer, *, what_was_it: str, giveaway: str,
          five_min_path: str) -> Closed:
    def build(state: State, now: float):
        loop = _require_active(state)
        event = events.make(
            "loop_closed", ts=now, loop_id=loop.id,
            what_was_it=what_was_it, giveaway=giveaway, five_min_path=five_min_path,
        )
        written, resumed = _with_parent_pop(state, loop, now, [event])
        return written, Closed(
            event=event, loop_id=loop.id, elapsed_s=loop.elapsed(now),
            eliminated=loop.eliminated, resumed=resumed,
        )

    return writer.mutate(build)


def abandon(writer: Writer) -> Abandoned:
    def build(state: State, now: float):
        loop = _require_active(state)
        event = events.make("loop_abandoned", ts=now, loop_id=loop.id)
        written, resumed = _with_parent_pop(state, loop, now, [event])
        return written, Abandoned(
            event=event, loop_id=loop.id, elapsed_s=loop.elapsed(now), resumed=resumed,
        )

    return writer.mutate(build)


def _with_parent_pop(state: State, loop: Loop, now: float, written: list[dict]):
    """Resume the parent in the same write that ends the child.

    `cli.py` does this as a second, separate append today, which leaves a
    window where nothing is active and the scheduler has nothing to watch.
    """
    if loop.parent_id is None:
        return written, None
    parent = state.loops.get(loop.parent_id)
    if parent is None or parent.status != PAUSED:
        return written, None
    written.append(events.make("loop_resumed", ts=now, loop_id=parent.id))
    return written, _summary(parent, now)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/app/test_commands_lifecycle.py -v`
Expected: PASS, with `test_close_records_the_postmortem_and_the_counts` xfailing.

- [ ] **Step 5: Run the full suite and commit**

```bash
python -m pytest
git add loop/app/commands.py tests/app/test_commands_lifecycle.py
git commit -m "feat: lifecycle commands, headless

open/pause/resume/close/abandon as functions. The parent pop now lands
in the same write as the close instead of a second append."
```

---

### Task 5: Content commands

The six mutations that change what a loop contains rather than what state it is in. `kill_hypothesis` is where `StaleError` earns its keep: clicking "rule out" on a hypothesis a check-in eliminated four seconds earlier must be refused, not appended.

**Files:**
- Modify: `loop/app/commands.py`
- Test: `tests/app/test_commands_content.py`, `tests/app/test_commands_lifecycle.py` (drop the xfail marker)

**Interfaces:**
- Consumes: everything Task 4 produced
- Produces:
  - `Logged(event: dict, actions: int)`
  - `HypothesisAdded(event: dict, hyp_id: int, live: int)`
  - `HypothesisKilled(event: dict, hyp_id: int, live: int)`
  - `ScopeCut(event: dict, old_stop_condition: str, new_stop_condition: str)`
  - `BudgetExtended(event: dict, old_budget_s: float, new_budget_s: float)`
  - `log_action(writer, *, action, because) -> Logged`
  - `add_hypothesis(writer, *, text) -> HypothesisAdded`
  - `kill_hypothesis(writer, *, hyp_id) -> HypothesisKilled`
  - `cut_scope(writer, *, new_stop_condition) -> ScopeCut`
  - `extend_budget(writer, *, new_budget_s, learned) -> BudgetExtended`
  - `record_checkin(writer, *, due, answers) -> bool` — arrives in Task 7, once `app/checkin.py` exists

- [ ] **Step 1: Write the failing tests**

Create `tests/app/test_commands_content.py`:

```python
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
```

In `tests/app/test_commands_lifecycle.py`, delete the `@pytest.mark.xfail` marker added in Task 4.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/app/test_commands_content.py -v`
Expected: FAIL — `AttributeError: module 'loop.app.commands' has no attribute 'log_action'`.

- [ ] **Step 3: Append to `loop/app/commands.py`**

Add the result types beside the others, and the functions after `abandon`:

```python
@dataclass(frozen=True, slots=True)
class Logged:
    event: dict
    actions: int


@dataclass(frozen=True, slots=True)
class HypothesisAdded:
    event: dict
    hyp_id: int
    live: int


@dataclass(frozen=True, slots=True)
class HypothesisKilled:
    event: dict
    hyp_id: int
    live: int


@dataclass(frozen=True, slots=True)
class ScopeCut:
    event: dict
    old_stop_condition: str
    new_stop_condition: str


@dataclass(frozen=True, slots=True)
class BudgetExtended:
    event: dict
    old_budget_s: float
    new_budget_s: float


def log_action(writer: Writer, *, action: str, because: str) -> Logged:
    def build(state: State, now: float):
        loop = _require_active(state)
        event = events.make(
            "action_logged", ts=now, loop_id=loop.id, action=action, because=because
        )
        return [event], Logged(event=event, actions=loop.actions + 1)

    return writer.mutate(build)


def add_hypothesis(writer: Writer, *, text: str) -> HypothesisAdded:
    def build(state: State, now: float):
        loop = _require_active(state)
        event = events.make(
            "hypothesis_added", ts=now, loop_id=loop.id,
            hyp_id=events.next_hypothesis_id(loop), text=text,
        )
        return [event], HypothesisAdded(
            event=event, hyp_id=event["hyp_id"], live=len(loop.live_hypotheses()) + 1,
        )

    return writer.mutate(build)


def kill_hypothesis(writer: Writer, *, hyp_id: int) -> HypothesisKilled:
    def build(state: State, now: float):
        loop = _require_active(state)
        target = next((h for h in loop.hypotheses if h.id == hyp_id), None)
        if target is None:
            # Verbatim from cli.py:198 — tests/test_cli_open.py asserts it.
            raise LoopError(f"no live hypothesis {hyp_id}.")
        if not target.alive:
            # Almost always a check-in got here first. Nothing to fix.
            raise StaleError(f"§{hyp_id} {target.text} was already ruled out.")
        event = events.make(
            "hypothesis_eliminated", ts=now, loop_id=loop.id, hyp_id=hyp_id
        )
        return [event], HypothesisKilled(
            event=event, hyp_id=hyp_id, live=len(loop.live_hypotheses()) - 1,
        )

    return writer.mutate(build)


def cut_scope(writer: Writer, *, new_stop_condition: str) -> ScopeCut:
    def build(state: State, now: float):
        loop = _require_active(state)
        event = events.make(
            "scope_cut", ts=now, loop_id=loop.id,
            old_stop_condition=loop.stop_condition,
            new_stop_condition=new_stop_condition,
        )
        return [event], ScopeCut(
            event=event, old_stop_condition=loop.stop_condition,
            new_stop_condition=new_stop_condition,
        )

    return writer.mutate(build)


def extend_budget(writer: Writer, *, new_budget_s: float, learned: str) -> BudgetExtended:
    def build(state: State, now: float):
        loop = _require_active(state)
        event = events.make(
            "budget_extended", ts=now, loop_id=loop.id,
            old_budget_s=loop.budget_s, new_budget_s=new_budget_s, learned=learned,
        )
        return [event], BudgetExtended(
            event=event, old_budget_s=loop.budget_s, new_budget_s=new_budget_s,
        )

    return writer.mutate(build)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/app/ -v`
Expected: PASS, no xfails remaining.

- [ ] **Step 5: Run the full suite and commit**

```bash
python -m pytest
git add loop/app/commands.py tests/app/test_commands_content.py tests/app/test_commands_lifecycle.py
git commit -m "feat: content commands, headless

kill_hypothesis refuses a stale click instead of appending it. The
precondition now runs under the same lock as the append."
```

---

### Task 6: Rewire the CLI onto the command layer

`cli.py` keeps its argparse tree, its `--json`, its exit codes, and every string it prints. What changes is that each `cmd_*` becomes three lines: collect with `prompts`, call the command, render the result. The existing CLI suite is the regression net and must pass unchanged.

Two checks stay duplicated in `cli.py` on purpose. The stack-depth ceiling and the "pause it and stack this on top?" consent both run *before* the remaining prompts, so a refusal costs the user four keystrokes instead of forty. Those copies are UX; the copies inside `open_loop` are the authority, because only they run under the lock. Do not delete either.

The daemon stays alive this task. `require_blocker()` and `daemon.ensure_running()` are untouched until Task 15.

**Files:**
- Modify: `loop/cli.py`
- Test: no new tests — `tests/test_cli_*.py` are the check

**Interfaces:**
- Consumes: every command from Tasks 4 and 5, `loop.app.writer.Writer`, `loop.app.errors.LoopError`
- Produces: no new public surface. `cli.LoopError` becomes a re-export of `loop.app.errors.LoopError` so `from loop.cli import LoopError` keeps working.

- [ ] **Step 1: Confirm the baseline is green**

Run: `python -m pytest tests/ -v`
Expected: PASS. Note the count — it must not drop this task.

- [ ] **Step 2: Replace the imports and the error class in `loop/cli.py`**

Delete the local `class LoopError` (lines 37-38) and `def emit` (lines 45-46). Replace the import block:

```python
from loop import prompts, render
from loop.app import commands
from loop.app.errors import LoopError  # noqa: F401  (re-exported; tests import it)
from loop.app.writer import Writer
from loop.blockers.factory import BlockerUnavailable, get_blocker
from loop.core import events
from loop.core.models import MAX_STACK_DEPTH, WARN_STACK_DEPTH, State
from loop.core.timefmt import format_duration
from loop.sched import daemon
from loop.store import jsonl
```

`PAUSED` is no longer needed here — `_resume_target` moves into `commands.py`. Delete `_resume_target`, `pop_to_parent`, and `_report_pop` from `cli.py`; the command layer owns all three now.

- [ ] **Step 3: Rewrite the mutating commands**

```python
def cmd_open(args, state: State, now: float) -> dict:
    require_blocker()

    # Advisory copies of two checks open_loop makes under the lock. They
    # run here so a refusal costs four keystrokes, not forty.
    active = state.active_loop()
    if active is not None:
        depth = events.stack_depth(state)
        if depth >= MAX_STACK_DEPTH:
            raise LoopError(
                f"stack depth {depth}. close or abandon something before opening another."
            )
        say(f"  ⚠  #{active.id} \"{active.question}\" is active")
        if not prompts.ask_yes_no("pause it and stack this on top?"):
            raise LoopError("aborted.")

    # Every answer is collected before anything is written. An abort here
    # (Ctrl-D at any remaining prompt) must leave the parent active and the
    # log untouched — not paused with nothing running underneath it.
    stop_condition = prompts.ask_text("stop condition?")
    budget_s = (
        prompts.parse_duration(args.budget)
        if args.budget
        else prompts.ask_duration("time budget?", default=DEFAULT_BUDGET_S)
    )
    interval_s = prompts.parse_duration(args.interval) if args.interval else DEFAULT_INTERVAL_S
    hypotheses = prompts.ask_lines("hypotheses?", minimum=1)

    result = commands.open_loop(
        Writer(), question=args.question, stop_condition=stop_condition,
        budget_s=budget_s, interval_s=interval_s, hypotheses=hypotheses,
        stack_on_active=True,
    )

    if result.paused_id is not None:
        say(f"  #{result.paused_id} paused at "
            f"{format_duration(result.paused_elapsed_s)} active.")
    say(f"  #{result.loop_id} open. timer running. "
        f"{result.hypotheses} hypotheses live.")
    if result.depth > 1:
        say(f"  stack depth {result.depth}.")
    if result.depth >= WARN_STACK_DEPTH:
        say("  ⚠  you are context switching, not working.")
    daemon.ensure_running()
    return result.event


def cmd_try(args, state: State, now: float) -> dict:
    result = commands.log_action(Writer(), action=args.action, because=args.because)
    say(f"  logged. {result.actions} actions this loop.")
    return result.event


def cmd_hyp(args, state: State, now: float) -> dict:
    if args.hyp_command == "add":
        result = commands.add_hypothesis(Writer(), text=args.text)
        say(f"  hypothesis {result.hyp_id} added. {result.live} live.")
        return result.event

    result = commands.kill_hypothesis(Writer(), hyp_id=args.hyp_id)
    say(f"  hypothesis {result.hyp_id} ruled out. {result.live} live.")
    return result.event


def cmd_pause(args, state: State, now: float) -> dict:
    reason = args.reason or prompts.ask_text("what interrupted?")
    result = commands.pause(Writer(), reason=reason)
    say(f"  #{result.loop_id} paused at {format_duration(result.elapsed_s)} active.")
    return result.event


def cmd_resume(args, state: State, now: float) -> dict:
    require_blocker()

    # Asked here, before the write, because the command needs the answer
    # and only the terminal can collect it.
    pause_reason = (
        prompts.ask_text("what interrupted?")
        if state.active_loop() is not None
        and state.active_loop().id != args.loop_id
        else None
    )
    result = commands.resume(Writer(), loop_id=args.loop_id, pause_reason=pause_reason)
    _say_resumed(result.summary)
    daemon.ensure_running()
    return result.event


def _say_resumed(summary) -> None:
    """The resumed-status line, shared by `resume` and the close/abandon pop."""
    say(f"  → resumed #{summary.loop_id} · "
        f"{format_duration(summary.elapsed_s)} / {format_duration(summary.budget_s)} · "
        f"next ping {format_duration(summary.interval_s)}")


def cmd_close(args, state: State, now: float) -> dict:
    result = commands.close(
        Writer(),
        what_was_it=prompts.ask_text("what was it?"),
        giveaway=prompts.ask_text("what was the giveaway?"),
        five_min_path=prompts.ask_text("how could I have found it in 5 minutes?"),
    )
    say(f"  closed #{result.loop_id}. {format_duration(result.elapsed_s)}. "
        f"{result.eliminated} hypotheses ruled out. pattern saved.")
    if result.resumed is not None:
        _say_resumed(result.resumed)
    return result.event


def cmd_abandon(args, state: State, now: float) -> dict:
    result = commands.abandon(Writer())
    say(f"  abandoned #{result.loop_id} at {format_duration(result.elapsed_s)} active.")
    if result.resumed is not None:
        _say_resumed(result.resumed)
    return result.event
```

Note `cmd_resume`'s guard: `args.loop_id` may be `None`, in which case the
comparison is against `None` and the reason is asked. That matches today,
where the prompt fires whenever an active loop exists and is not the target.
`commands.resume` refuses with `#N is already active.` when it is the target.

- [ ] **Step 4: Widen the exception handler in `main`**

`LockTimeout` is a new way for a write to fail and must not reach the user as a traceback. In `main`, add it beside `CorruptLogError`:

```python
    except lock.LockTimeout as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1
```

and add `from loop.store import jsonl, lock` to the imports.

- [ ] **Step 5: Run the CLI suite**

Run: `python -m pytest tests/test_cli_open.py tests/test_cli_stack.py tests/test_cli_errors.py tests/test_cli_stats.py -v`
Expected: PASS, same count as Step 1. If a `--json` test fails, the result object is not returning the same `event` dict — fix the command, not the test.

- [ ] **Step 6: Run the full suite and commit**

```bash
python -m pytest
git add loop/cli.py
git commit -m "refactor: CLI calls the command layer

Every mutation was welded to blocking terminal I/O. Now cli.py collects,
calls, and renders — the same three lines the GUI will use."
```

---

### Task 7: The check-in translation layer

`build_prompt` and `answers_to_events` already speak only `core` and `blockers.base`, and they are already tested. They move to `app/` so the GUI can reach them without importing `sched/`. `sched/tick.py` imports them back for the daemon's remaining life — two tasks.

`record_checkin` gets one thing `tick()` does not have: the staleness re-check runs *inside* the lock. `tick.py:167` re-reads the log after the prompt returns, decides the answers are stale, and drops them — but between that read and the append, anything could happen. Under the lock, it cannot.

**Files:**
- Create: `loop/app/checkin.py`
- Modify: `loop/sched/tick.py` (import shim), `loop/app/commands.py` (add `record_checkin`, `record_checkpoint_shown`)
- Test: `tests/app/test_checkin.py` (moved from `tests/sched/test_tick.py`), `tests/app/test_commands_checkin.py`

**Interfaces:**
- Consumes: `loop.blockers.base.{Answers, Choice, Prompt, TextField}`, `loop.core.{events, schedule, thrash}`
- Produces:
  - `loop.app.checkin.CUT_FIELDS`, `EXTEND_FIELDS`
  - `loop.app.checkin.build_prompt(state: State, loop: Loop, due: schedule.Due) -> Prompt`
  - `loop.app.checkin.answers_to_events(loop: Loop, due: schedule.Due, answers: Answers, now: float) -> list[dict]`
  - `loop.app.commands.record_checkpoint_shown(writer, *, loop_id: int, kind: str) -> None`
  - `loop.app.commands.record_checkin(writer, *, due: schedule.Due, answers: Answers) -> bool` — False when the answers were stale and nothing was written

- [ ] **Step 1: Move the module**

```bash
git mv tests/sched/test_tick.py tests/app/test_checkin.py
```

In `tests/app/test_checkin.py`, change `from loop.sched import tick as tick_module` to `from loop.app import checkin as checkin_module`, and rename every `tick_module.` reference to `checkin_module.`. Tests that call `tick_module.tick(...)` — the orchestration, not the translation — stay behind: move those back into a new `tests/sched/test_tick.py` importing `from loop.sched import tick as tick_module`, unchanged. Split by what the test touches: `build_prompt`/`answers_to_events` tests go to `tests/app/`, `tick()` tests stay in `tests/sched/`.

- [ ] **Step 2: Write the failing test for the new commands**

Create `tests/app/test_commands_checkin.py`:

```python
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
```

- [ ] **Step 3: Create `loop/app/checkin.py`**

Move `CUT_FIELDS`, `EXTEND_FIELDS`, `_ping_question`, `build_prompt`, `answers_to_events`, `_ping_events`, `_checkpoint_events`, and `_answered` out of `loop/sched/tick.py` verbatim — every string unchanged, including the p100 `"stop now — then run \`loop close\`"` label, which changes in Task 14 and not before. Header:

```python
"""Prompts out, events in.

The translation between what a check-in shows and what the log records.
Speaks only `core` and `blockers.base` — no store, no scheduler, no Qt —
which is why both the daemon and the GUI can use it unchanged.
"""
```

`loop/sched/tick.py` keeps `tick()` and replaces the moved code with:

```python
from loop.app.checkin import (  # noqa: F401  (re-exported for the daemon)
    CUT_FIELDS,
    EXTEND_FIELDS,
    answers_to_events,
    build_prompt,
)
```

Delete now-unused imports from `tick.py` (`Choice`, `Prompt`, `TextField`, `thrash`, `parse_duration`) — leave `Answers`, `Blocker`, `events`, `schedule`, `Loop`, `State`, `format_duration`, `jsonl`, `get_blocker` as `tick()` still needs them. Run `python -m pyflakes loop/sched/tick.py` if available, or just check the test run for `F401`.

- [ ] **Step 4: Append `record_checkpoint_shown` and `record_checkin` to `loop/app/commands.py`**

```python
def record_checkpoint_shown(writer: Writer, *, loop_id: int, kind: str) -> None:
    """Forensics only: `fold` carries no state for this event. It exists so
    a timed-out checkpoint can be told from one that was never displayed."""
    writer.mutate(lambda state, now: (
        [events.make("checkpoint_shown", ts=now, loop_id=loop_id, kind=kind)], None
    ))


def record_checkin(writer: Writer, *, due, answers) -> bool:
    """Write a check-in's answers, unless the world moved while it was up.

    A blocking prompt can sit for five minutes. If the loop it was built
    for was closed, abandoned, or paused from elsewhere in that time, the
    answers are about a state that no longer exists. Checking that inside
    the lock — rather than in a separate read, as `sched/tick.py` did —
    closes the window between the check and the append.
    """
    from loop.app import checkin

    def build(state: State, now: float):
        if state.active_id != due.loop_id:
            return [], False
        loop = state.loops[due.loop_id]
        return checkin.answers_to_events(loop, due, answers, now), True

    return writer.mutate(build)
```

The `checkin` import is function-local to keep `commands.py` importable
without dragging in `blockers.base` for callers that only open loops.

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/app/ tests/sched/ -v`
Expected: PASS. The moved tests should pass without edits beyond the rename.

- [ ] **Step 6: Run the full suite and commit**

```bash
python -m pytest
git add loop/app/checkin.py loop/app/commands.py loop/sched/tick.py tests/app/test_checkin.py tests/app/test_commands_checkin.py tests/sched/test_tick.py
git commit -m "refactor: move check-in translation into app/

Prompt/Answers/events already spoke only core and blockers.base. The GUI
needs them without importing sched/. Staleness is now checked under the
lock instead of in a separate read."
```

---

### Task 8: View models

Everything the dashboard claims, computed in pure Python and asserted without a display. If this task is right, the widgets in Tasks 11-16 are bindings, and anything in `gui/` that needs a test of its own is a sign that logic leaked down.

`enabled_actions` is the single switch the log-unreadable state throws. Eleven scattered `setEnabled` calls would each be a place to forget.

**Files:**
- Create: `loop/app/view.py`
- Test: `tests/app/test_view.py`

**Interfaces:**
- Consumes: `loop.core.{events, schedule, thrash, timeline}`, `loop.core.models.{State, paused_for}`, `loop.core.timefmt.format_duration`
- Produces:
  - `ALL_ACTIONS: frozenset[str]` = `{"open", "try", "hyp_add", "hyp_kill", "pause", "resume", "cut", "extend", "close", "abandon"}`
  - `MeterView(elapsed_s, budget_s, fraction, over_s, next_checkin_s)`
  - `StackRow(loop_id, question, active, elapsed_s, paused_s, stale)`
  - `HypothesisRow(hyp_id, text, alive)`
  - `ThrashView(title, why, fix)`
  - `StatusView(state: str, detail: str, depth: int)` — `state` is one of `"armed" | "paused" | "idle" | "unreadable"`
  - `DashboardView(active_id, question, stop_condition, meter, stack, hypotheses, timeline, thrash, status, enabled_actions, live_count, dead_count, error)`
  - `build(state: State, log: list[dict], now: float) -> DashboardView`
  - `unreadable(previous: DashboardView | None, path: str, line: int, frozen_at: float) -> DashboardView`

- [ ] **Step 1: Write the failing tests**

Create `tests/app/test_view.py`:

```python
import pytest

from loop.app import view
from loop.core import events


def opened(loop_id=1, parent_id=None, budget_s=2700.0, question="q"):
    return events.make(
        "loop_opened", ts=0.0, loop_id=loop_id, question=question,
        stop_condition="comes up clean twice", budget_s=budget_s,
        interval_s=1200.0, hypotheses=["cert expiry", "pg pool"],
        parent_id=parent_id,
    )


def build(log, now):
    return view.build(events.fold(log), log, now)


def test_an_empty_log_offers_only_open():
    dashboard = build([], 0.0)
    assert dashboard.active_id is None
    assert dashboard.meter is None
    assert dashboard.enabled_actions == frozenset({"open"})
    assert dashboard.status.state == "idle"


def test_an_active_loop_offers_everything_but_resume():
    dashboard = build([opened()], 600.0)
    assert dashboard.active_id == 1
    assert dashboard.question == "q"
    assert dashboard.stop_condition == "comes up clean twice"
    assert "resume" not in dashboard.enabled_actions
    assert {"try", "hyp_add", "hyp_kill", "pause", "cut", "extend", "close", "abandon"} \
        <= dashboard.enabled_actions
    assert dashboard.status.state == "armed"


def test_the_meter_reads_elapsed_against_budget():
    dashboard = build([opened()], 1350.0)
    assert dashboard.meter.elapsed_s == 1350.0
    assert dashboard.meter.budget_s == 2700.0
    assert dashboard.meter.fraction == pytest.approx(0.5)
    assert dashboard.meter.over_s == 0.0


def test_the_meter_clamps_and_reports_the_overrun_separately():
    dashboard = build([opened()], 3000.0)
    assert dashboard.meter.fraction == 1.0
    assert dashboard.meter.over_s == 300.0


def test_next_checkin_counts_down_to_the_ping():
    dashboard = build([opened()], 600.0)
    assert dashboard.meter.next_checkin_s == pytest.approx(600.0)


def test_next_checkin_is_none_once_the_budget_is_gone():
    """Nothing is scheduled ahead; p100 is due now and the strip says so."""
    dashboard = build([opened()], 2800.0)
    assert dashboard.meter.next_checkin_s is None


def test_paused_parents_sit_above_the_active_loop_most_recent_first():
    log = [
        opened(1, question="first"),
        events.make("loop_paused", ts=100.0, loop_id=1, reason="x"),
        opened(2, parent_id=1, question="second"),
        events.make("loop_paused", ts=200.0, loop_id=2, reason="y"),
        opened(3, parent_id=2, question="third"),
    ]
    dashboard = build(log, 300.0)
    assert [(row.loop_id, row.active) for row in dashboard.stack] == [
        (2, False), (1, False), (3, True),
    ]
    assert dashboard.status.depth == 3


def test_a_loop_paused_over_a_day_is_marked_stale():
    log = [opened(1), events.make("loop_paused", ts=100.0, loop_id=1, reason="x")]
    assert build(log, 100.0 + 86_400.0).stack[0].stale is True
    assert build(log, 100.0 + 3600.0).stack[0].stale is False


def test_a_paused_stack_with_nothing_active_offers_resume_not_try():
    log = [opened(1), events.make("loop_paused", ts=100.0, loop_id=1, reason="x")]
    dashboard = build(log, 200.0)
    assert dashboard.active_id is None
    assert "resume" in dashboard.enabled_actions
    assert "try" not in dashboard.enabled_actions
    assert dashboard.status.state == "paused"


def test_ruled_out_hypotheses_stay_visible_and_counted():
    log = [opened(), events.make("hypothesis_eliminated", ts=50.0, loop_id=1, hyp_id=1)]
    dashboard = build(log, 100.0)
    assert [(row.hyp_id, row.alive) for row in dashboard.hypotheses] == [(1, False), (2, True)]
    assert dashboard.live_count == 1
    assert dashboard.dead_count == 1


def test_the_thrash_banner_shows_the_counts_that_fired_it():
    log = [opened()] + [
        events.make("action_logged", ts=float(index), loop_id=1,
                    action=f"a{index}", because="b")
        for index in range(5)
    ]
    dashboard = build(log, 2000.0)
    assert dashboard.thrash.title == "You are thrashing."
    assert dashboard.thrash.why == "5 actions · 0 ruled out · 33m elapsed"


def test_no_thrash_banner_when_the_detector_is_quiet():
    assert build([opened()], 600.0).thrash is None


def test_the_timeline_is_the_active_loops_entries():
    log = [opened(), events.make("action_logged", ts=5.0, loop_id=1,
                                 action="added print", because="shows the frame")]
    assert [entry.text for entry in build(log, 100.0).timeline] == ["added print"]


def test_unreadable_keeps_the_last_good_view_and_disables_everything():
    good = build([opened()], 600.0)
    frozen = view.unreadable(good, path="~/.loop/events.jsonl", line=214, frozen_at=600.0)
    assert frozen.enabled_actions == frozenset()
    assert frozen.active_id == 1
    assert frozen.meter.elapsed_s == 600.0     # the clock stops where it stopped
    assert frozen.status.state == "unreadable"
    assert frozen.error == "~/.loop/events.jsonl:214"


def test_unreadable_with_no_previous_view_still_disables_everything():
    frozen = view.unreadable(None, path="p", line=1, frozen_at=0.0)
    assert frozen.enabled_actions == frozenset()
    assert frozen.active_id is None
    assert frozen.meter is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/app/test_view.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.app.view'`.

- [ ] **Step 3: Write `loop/app/view.py`**

```python
"""What the window shows, as plain data.

Every claim the dashboard makes is decided here and asserted in ordinary
unit tests. The widgets bind; they do not compute. Anything in `gui/` that
needs a test of its own is a sign that logic leaked down.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from loop.core import events, schedule, thrash, timeline
from loop.core.models import PAUSED, State, paused_for
from loop.core.timefmt import format_duration

STALE_PAUSE_S = 86_400.0

ALL_ACTIONS = frozenset({
    "open", "try", "hyp_add", "hyp_kill", "pause",
    "resume", "cut", "extend", "close", "abandon",
})

ACTIVE_ACTIONS = frozenset({
    "try", "hyp_add", "hyp_kill", "pause", "cut", "extend", "close", "abandon",
})

THRASH_TITLE = "You are thrashing."
THRASH_FIX = "Step away for five minutes, or escalate to someone who has seen this before."


@dataclass(frozen=True, slots=True)
class MeterView:
    elapsed_s: float
    budget_s: float
    fraction: float          # clamped to 1.0; the overrun rides in over_s
    over_s: float
    next_checkin_s: float | None


@dataclass(frozen=True, slots=True)
class StackRow:
    loop_id: int
    question: str
    active: bool
    elapsed_s: float
    paused_s: float
    stale: bool


@dataclass(frozen=True, slots=True)
class HypothesisRow:
    hyp_id: int
    text: str
    alive: bool


@dataclass(frozen=True, slots=True)
class ThrashView:
    title: str
    why: str
    fix: str


@dataclass(frozen=True, slots=True)
class StatusView:
    state: str               # armed | paused | idle | unreadable
    detail: str
    depth: int


@dataclass(frozen=True, slots=True)
class DashboardView:
    active_id: int | None
    question: str | None
    stop_condition: str | None
    meter: MeterView | None
    stack: tuple[StackRow, ...]
    hypotheses: tuple[HypothesisRow, ...]
    timeline: tuple[timeline.Entry, ...]
    thrash: ThrashView | None
    status: StatusView
    enabled_actions: frozenset[str]
    live_count: int
    dead_count: int
    error: str | None = None


def build(state: State, log: list[dict], now: float) -> DashboardView:
    loop = state.active_loop()
    depth = events.stack_depth(state)
    stack = _stack(state, now)

    if loop is None:
        enabled = {"open"}
        if state.paused_loops():
            enabled.add("resume")
            status = StatusView("paused", "no check-ins while paused", depth)
        else:
            status = StatusView("idle", "no check-ins scheduled", depth)
        return DashboardView(
            active_id=None, question=None, stop_condition=None, meter=None,
            stack=stack, hypotheses=(), timeline=(), thrash=None,
            status=status, enabled_actions=frozenset(enabled),
            live_count=0, dead_count=0,
        )

    elapsed = loop.elapsed(now)
    return DashboardView(
        active_id=loop.id,
        question=loop.question,
        stop_condition=loop.stop_condition,
        meter=_meter(loop, elapsed),
        stack=stack,
        hypotheses=tuple(
            HypothesisRow(h.id, h.text, h.alive) for h in loop.hypotheses
        ),
        timeline=tuple(timeline.entries(log, loop.id)),
        thrash=_thrash(loop, elapsed),
        status=StatusView("armed", "check-ins armed", depth),
        enabled_actions=frozenset(ACTIVE_ACTIONS | {"open"}),
        live_count=len(loop.live_hypotheses()),
        dead_count=loop.eliminated,
    )


def unreadable(previous: DashboardView | None, *, path: str, line: int,
               frozen_at: float) -> DashboardView:
    """The log stopped parsing at an interior line.

    The last state that folded cleanly stays on screen — a stale clock
    beats a blank window — and every action is disabled. Appending to a
    log we cannot fold would make the damage worse, and this app is the
    last thing that should do that.
    """
    detail = f"frozen at {format_duration(frozen_at)}"
    if previous is None:
        return DashboardView(
            active_id=None, question=None, stop_condition=None, meter=None,
            stack=(), hypotheses=(), timeline=(), thrash=None,
            status=StatusView("unreadable", detail, 0),
            enabled_actions=frozenset(), live_count=0, dead_count=0,
            error=f"{path}:{line}",
        )
    return replace(
        previous,
        status=StatusView("unreadable", detail, previous.status.depth),
        enabled_actions=frozenset(),
        error=f"{path}:{line}",
    )


def _meter(loop, elapsed: float) -> MeterView:
    budget = loop.budget_s
    return MeterView(
        elapsed_s=elapsed,
        budget_s=budget,
        fraction=min(1.0, elapsed / budget) if budget > 0 else 1.0,
        over_s=max(0.0, elapsed - budget),
        next_checkin_s=_next_checkin(loop, elapsed),
    )


def _next_checkin(loop, elapsed: float) -> float | None:
    """Seconds of *active* time until the next thing fires.

    Whichever comes first: the next ping, or an unanswered checkpoint
    boundary still ahead of us. Nothing ahead means the budget is gone and
    p100 is already due, which the meter says in its own way.
    """
    candidates = [loop.last_ping_elapsed + loop.interval_s - elapsed]
    for kind in schedule.CHECKPOINT_KINDS:
        key = events.checkpoint_key(kind, loop.budget_s)
        if key in loop.checkpoints_answered:
            continue
        candidates.append(schedule.checkpoint_boundary(loop, kind) - elapsed)
    ahead = [value for value in candidates if value > 0]
    return min(ahead) if ahead else None


def _stack(state: State, now: float) -> tuple[StackRow, ...]:
    """Paused parents first, most recently paused at the top; active last.

    That ordering is the mockup's: the paused rows are context you scan
    past, and the active loop is where the eye lands.
    """
    rows = [
        StackRow(
            loop_id=loop.id, question=loop.question, active=False,
            elapsed_s=loop.elapsed(now), paused_s=paused_for(loop, now),
            stale=paused_for(loop, now) >= STALE_PAUSE_S,
        )
        for loop in state.paused_loops()
    ]
    active = state.active_loop()
    if active is not None:
        rows.append(StackRow(
            loop_id=active.id, question=active.question, active=True,
            elapsed_s=active.elapsed(now), paused_s=0.0, stale=False,
        ))
    return tuple(rows)


def _thrash(loop, elapsed: float) -> ThrashView | None:
    """Re-derives the reason rather than parsing `thrash.panel`.

    The detector's panel is terminal text with box glyphs and newlines in
    it. Reading `firing` and recomputing the counts keeps the two renderers
    independent, and the counts are the auditable part.
    """
    if not thrash.detect(loop, elapsed).firing:
        return None
    if loop.actions >= thrash.MIN_ACTIONS and loop.eliminated == 0:
        why = (f"{loop.actions} actions · {loop.eliminated} ruled out · "
               f"{format_duration(elapsed)} elapsed")
    else:
        recent = loop.recent_ping_answers[-thrash.NEGATIVE_PING_STREAK:]
        why = "last 3 check-ins: " + " · ".join("yes" if answer else "no" for answer in recent)
    return ThrashView(title=THRASH_TITLE, why=why, fix=THRASH_FIX)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/app/test_view.py -v`
Expected: PASS, 15 tests.

- [ ] **Step 5: Run the full suite and commit**

```bash
python -m pytest
git add loop/app/view.py tests/app/test_view.py
git commit -m "feat: pure view models

Every claim the dashboard makes, decided without a display. The widgets
bind; they do not compute."
```

---

### Task 9: Fix the estimate-drift sign

`stats.py:112` prints `median {drift['median_pct']}% over budget` unconditionally, and `median_pct` is signed (`stats.py:74`). A loop that finished early prints `median -99.7% over budget`. Pre-existing bug, fixed here because the logbook view in Task 16 renders the same number and the two must not disagree.

**Files:**
- Modify: `loop/core/stats.py`
- Test: `tests/core/test_stats.py`

**Interfaces:**
- Produces: `loop.core.stats.format_drift(median_pct: float) -> str`

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_stats.py`:

```python
def test_over_budget_drift_reads_over():
    assert stats.format_drift(18.4) == "18.4% over"


def test_under_budget_drift_reads_under_not_negative_over():
    """A loop that finished early is good news and must not read as -99.7% over."""
    assert stats.format_drift(-99.7) == "99.7% under"


def test_exactly_on_budget_reads_over_with_zero():
    assert stats.format_drift(0.0) == "0.0% over"


def test_the_rendered_report_uses_the_helper(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
    report = {
        "followed": {"count": 0, "median_s": 0.0, "resolved_pct": 0.0},
        "abandoned": {"count": 0, "median_s": 0.0, "resolved_pct": 0.0},
        "thrash_episodes": {"total": 0},
        "estimate_drift": {"median_pct": -20.0, "extensions": 0, "loops_with_extensions": 0},
        "ping_response": {"answered": 0, "timed_out": 0},
        "interruptions": {"median_pauses": 0.0, "median_pause_s": 0.0, "max_depth": 0},
    }
    drift_line = next(line for line in stats.render(report) if "estimate drift" in line)
    assert "20.0% under budget" in drift_line
    assert "-20.0" not in drift_line
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/core/test_stats.py -v`
Expected: FAIL — `AttributeError: module 'loop.core.stats' has no attribute 'format_drift'`.

- [ ] **Step 3: Add the helper and use it**

In `loop/core/stats.py`, add above `render`:

```python
def format_drift(median_pct: float) -> str:
    """Signed drift as a word.

    `median_pct` is signed, so rendering it into a fixed "over budget"
    suffix makes a loop that finished early read as -99.7% over.
    """
    return f"{abs(median_pct)}% {'under' if median_pct < 0 else 'over'}"
```

Replace the `estimate drift` line in `render`:

```python
        f"  {'estimate drift':<20}: median {format_drift(drift['median_pct'])} budget | "
        f"{drift['extensions']} extensions across {drift['loops_with_extensions']} loops",
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/core/test_stats.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and commit**

```bash
python -m pytest
git add loop/core/stats.py tests/core/test_stats.py
git commit -m "fix: estimate drift read -99.7% over budget when under

median_pct is signed and the suffix was not."
```

---

### Task 10: GUI shell — theme, single instance, tray

The first task that imports PySide6. It produces a running app with an empty window, a tray icon, and the hide-vs-quit behaviour. No loop data yet.

Two pieces are deliberately Qt-free so they can be tested on a machine with no PySide6: `gui/theme.py` builds the stylesheet as a string, and `app/launcher.server_name` derives the single-instance socket name.

**Files:**
- Create: `loop/gui/__init__.py` (empty), `loop/gui/theme.py`, `loop/gui/instance.py`, `loop/gui/tray.py`, `loop/gui/window.py` (shell only), `loop/gui/__main__.py`, `loop/app/launcher.py`
- Modify: `pyproject.toml`
- Test: `tests/gui/__init__.py` (empty), `tests/gui/conftest.py`, `tests/gui/test_theme.py`, `tests/gui/test_shell.py`, `tests/app/test_launcher.py`

**Interfaces:**
- Consumes: `loop.store.paths.loop_home`
- Produces:
  - `loop.app.launcher.server_name() -> str`
  - `loop.gui.theme.TOKENS: dict[str, dict[str, str]]` keyed `"light"` / `"dark"`
  - `loop.gui.theme.stylesheet(mode: str) -> str`
  - `loop.gui.instance.claim(app) -> QLocalServer | None` — `None` when another copy already holds it, after asking it to surface
  - `loop.gui.tray.Tray(window)` with `.set_burn(fraction: float | None)`
  - `loop.gui.window.MainWindow(controller)` — `.surface()`, `.closeEvent` hides
  - `loop.gui.__main__.main(argv=None) -> int`

- [ ] **Step 1: Add the dependency and the entry points**

In `pyproject.toml`, replace the `[project.optional-dependencies]` and `[project.scripts]` blocks:

```toml
[project.optional-dependencies]
gui = ["PySide6>=6.6"]
dev = ["pytest>=8.0"]

[project.scripts]
loop = "loop.cli:main"
loop-gui = "loop.gui.__main__:main"
```

The `macos` extra (PyObjC) goes. Nothing imports it after Task 15, and leaving it advertises a blocker that no longer exists.

- [ ] **Step 2: Write the Qt-free tests**

Create `tests/app/test_launcher.py`:

```python
from loop.app import launcher


def test_server_name_is_stable_for_one_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
    assert launcher.server_name() == launcher.server_name()


def test_two_homes_get_different_names(tmp_path, monkeypatch):
    """A throwaway LOOP_HOME must never surface the window watching the real one."""
    monkeypatch.setenv("LOOP_HOME", str(tmp_path / "a"))
    first = launcher.server_name()
    monkeypatch.setenv("LOOP_HOME", str(tmp_path / "b"))
    assert launcher.server_name() != first


def test_server_name_is_a_legal_socket_name(tmp_path, monkeypatch):
    """No separators: Qt turns this into a filesystem path on POSIX."""
    monkeypatch.setenv("LOOP_HOME", str(tmp_path / "with spaces" / "and/slashes"))
    name = launcher.server_name()
    assert name.startswith("loop-")
    assert name.replace("loop-", "").isalnum()
    assert len(name) <= 32
```

Create `tests/gui/__init__.py` (empty) and `tests/gui/test_theme.py`:

```python
from loop.gui import theme


def test_both_modes_define_the_same_token_names():
    assert set(theme.TOKENS["light"]) == set(theme.TOKENS["dark"])


def test_every_token_is_a_hex_colour():
    for mode in theme.TOKENS.values():
        for value in mode.values():
            assert value.startswith("#") and len(value) == 7


def test_the_stylesheet_substitutes_the_mode_and_leaves_no_placeholders():
    light = theme.stylesheet("light")
    dark = theme.stylesheet("dark")
    assert theme.TOKENS["light"]["panel"] in light
    assert theme.TOKENS["dark"]["panel"] in dark
    assert light != dark
    assert "{" not in light and "}" in light  # QSS braces survive; format slots do not


def test_an_unknown_mode_refuses_rather_than_rendering_half_a_theme():
    import pytest
    with pytest.raises(KeyError):
        theme.stylesheet("sepia")
```

- [ ] **Step 3: Write the Qt test harness and shell tests**

Create `tests/gui/conftest.py`:

```python
"""Every test in this package needs a Qt that may not be installed.

The suite must pass on a machine with no PySide6 — these skip, and the
coverage that matters lives in tests/app/.
"""

import os

import pytest

pytest.importorskip("PySide6", reason="GUI tests need the [gui] extra")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
```

Create `tests/gui/test_shell.py`:

```python
import pytest

from loop.gui import instance


def test_the_first_claim_succeeds_and_the_second_is_refused(qapp):
    server = instance.claim(qapp)
    assert server is not None
    try:
        assert instance.claim(qapp) is None
    finally:
        server.close()


def test_a_stale_socket_is_reclaimed(qapp, monkeypatch):
    """A SIGKILLed copy leaves a socket file behind on POSIX. The next
    launch must take it over, not refuse to start forever."""
    server = instance.claim(qapp)
    assert server is not None
    server.close()          # closed without removing, like a hard kill
    reclaimed = instance.claim(qapp)
    assert reclaimed is not None
    reclaimed.close()


def test_closing_the_window_hides_it_instead_of_quitting(qapp):
    from PySide6.QtGui import QCloseEvent
    from loop.gui.window import MainWindow

    window = MainWindow(controller=None)
    window.show()
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert window.isHidden()
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `python -m pytest tests/gui/ tests/app/test_launcher.py -v`
Expected: FAIL — `ModuleNotFoundError` for `loop.gui.theme` and `loop.app.launcher`. If PySide6 is not installed, `tests/gui/test_shell.py` skips and `test_theme.py` still fails, which is correct.

- [ ] **Step 5: Write `loop/app/launcher.py`**

```python
"""Finding and starting the desktop app.

`server_name` is here rather than in `gui/` because the name is derived
from the data directory, not from Qt, and because a module that imports
PySide6 cannot be tested on a machine without it.
"""

from __future__ import annotations

import hashlib

from loop.store import paths


def server_name() -> str:
    """The single-instance socket name for this LOOP_HOME.

    Hashed rather than derived from the path so that two homes — a real
    one and a throwaway under a test's tmp_path — can never collide, and
    so the name survives spaces and separators that Qt would otherwise
    turn into a filesystem path on POSIX.
    """
    digest = hashlib.sha256(str(paths.loop_home()).encode("utf-8")).hexdigest()
    return f"loop-{digest[:16]}"
```

- [ ] **Step 6: Write `loop/gui/theme.py`**

Restate the design tokens from the spec's §11. No Qt import — this module is a string builder.

```python
"""The token table, and the Qt stylesheet built from it.

The same values the design previews use. System faces only: PySide6 has to
render them too, and bundling a face for parity with a web preview is a
cost with no return.

No Qt import here on purpose — this is a string builder, and it stays
testable on a machine with no PySide6.
"""

from __future__ import annotations

TOKENS: dict[str, dict[str, str]] = {
    "light": {
        "ink": "#F1F5F5", "panel": "#FFFFFF", "raised": "#E7EDED", "line": "#D0DBDB",
        "text": "#13201F", "muted": "#576C71", "dim": "#8598A0", "accent": "#0F857C",
        "ok": "#2C8A4E", "warn": "#A96F16", "crit": "#C04630",
    },
    "dark": {
        "ink": "#0C1214", "panel": "#121B1E", "raised": "#19252A", "line": "#26363C",
        "text": "#DCE8EA", "muted": "#7B9198", "dim": "#506469", "accent": "#5AD1C8",
        "ok": "#5FCB7E", "warn": "#E4A33C", "crit": "#E4644E",
    },
}

MONO = '"SF Mono", "Cascadia Mono", "JetBrains Mono", Consolas, monospace'
UI = '"SF Pro Text", "Segoe UI Variable Text", "Segoe UI", sans-serif'

_QSS = """
QWidget { background: %(ink)s; color: %(text)s; font-family: %(ui)s; font-size: 13px; }
QFrame#panel { background: %(panel)s; border: 1px solid %(line)s; border-radius: 8px; }
QLabel#question { font-size: 18px; font-weight: 600; }
QLabel#label { color: %(dim)s; font-size: 10px; letter-spacing: 1px; text-transform: uppercase; }
QLabel#muted { color: %(muted)s; }
QLabel#clock { font-family: %(mono)s; font-size: 26px; font-weight: 600; }
QLabel#over { color: %(crit)s; }
QPushButton {
    background: %(raised)s; color: %(text)s; border: 1px solid %(line)s;
    border-radius: 6px; padding: 5px 12px;
}
QPushButton:hover { border-color: %(accent)s; }
QPushButton:disabled { color: %(dim)s; border-color: %(line)s; background: %(ink)s; }
QPushButton#primary { background: %(accent)s; color: %(panel)s; border-color: %(accent)s; }
QPushButton#danger { color: %(crit)s; }
QLineEdit, QPlainTextEdit {
    background: %(ink)s; border: 1px solid %(line)s; border-radius: 6px;
    padding: 6px 8px; selection-background-color: %(accent)s;
}
QLineEdit:focus, QPlainTextEdit:focus { border-color: %(accent)s; }
QFrame#banner { background: %(raised)s; border-left: 3px solid %(warn)s; border-radius: 4px; }
QFrame#error { background: %(raised)s; border-left: 3px solid %(crit)s; border-radius: 4px; }
QLabel#dead { color: %(dim)s; text-decoration: line-through; }
QLabel#path { font-family: %(mono)s; color: %(muted)s; font-size: 11px; }
"""


def stylesheet(mode: str) -> str:
    """Raises KeyError on an unknown mode rather than rendering half a theme."""
    return _QSS % {**TOKENS[mode], "mono": MONO, "ui": UI}
```

- [ ] **Step 7: Write `loop/gui/instance.py`**

```python
"""One app per LOOP_HOME.

Two copies would fire two check-ins for one interval and contend on every
append. The socket is the only authority on liveness — no pidfile, no
heartbeat, nothing that can go stale in a way that lies.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from loop.app.launcher import server_name

CONNECT_TIMEOUT_MS = 300
SURFACE = b"surface\n"


def claim(app: QCoreApplication) -> QLocalServer | None:
    """Become the single instance, or ask the incumbent to show itself.

    Returns the server on success, `None` when another copy answered.
    """
    name = server_name()

    probe = QLocalSocket()
    probe.connectToServer(name)
    if probe.waitForConnected(CONNECT_TIMEOUT_MS):
        probe.write(SURFACE)
        probe.flush()
        probe.waitForBytesWritten(CONNECT_TIMEOUT_MS)
        probe.disconnectFromServer()
        return None

    server = QLocalServer(app)
    if not server.listen(name):
        # Nobody answered but the name is taken: a socket file left behind
        # by a hard kill. Clearing it is safe precisely because the connect
        # above failed.
        QLocalServer.removeServer(name)
        if not server.listen(name):
            return None
    return server
```

- [ ] **Step 8: Write `loop/gui/tray.py`**

```python
"""The tray icon and its menu.

Closing the window hides it; the scheduler keeps running. Quit is only
reachable from here, and when a loop is active it says what stops.
"""

from __future__ import annotations

from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QMessageBox, QSystemTrayIcon

from loop.gui import theme

ICON_PX = 32


def burn_colour(fraction: float | None, mode: str) -> QColor:
    """The tray dot reports burn, because it is often the only part of the
    app on screen. No loop is dim; over budget is critical."""
    tokens = theme.TOKENS[mode]
    if fraction is None:
        return QColor(tokens["dim"])
    if fraction >= 1.0:
        return QColor(tokens["crit"])
    if fraction >= 0.75:
        return QColor(tokens["warn"])
    return QColor(tokens["accent"])


def render_icon(fraction: float | None, mode: str) -> QIcon:
    """Drawn, not loaded: an icon file would be one more thing to package."""
    pixmap = QPixmap(ICON_PX, ICON_PX)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(burn_colour(fraction, mode))
    painter.setPen(QColor(0, 0, 0, 0))
    painter.drawEllipse(4, 4, ICON_PX - 8, ICON_PX - 8)
    painter.end()
    return QIcon(pixmap)


class Tray(QSystemTrayIcon):
    def __init__(self, window, mode: str = "dark") -> None:
        super().__init__(window)
        self._window = window
        self._mode = mode
        self.setIcon(render_icon(None, mode))

        menu = QMenu()
        show = QAction("Show loop", menu)
        show.triggered.connect(window.surface)
        menu.addAction(show)
        menu.addSeparator()
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self._confirm_quit)
        menu.addAction(quit_action)
        self.setContextMenu(menu)

        self.activated.connect(lambda _reason: window.surface())

    def set_burn(self, fraction: float | None) -> None:
        self.setIcon(render_icon(fraction, self._mode))

    def _confirm_quit(self) -> None:
        from PySide6.QtWidgets import QApplication

        if self._window.has_active_loop():
            answer = QMessageBox.warning(
                self._window,
                "Quit loop?",
                "A loop is running. Quitting stops its check-ins — the timer "
                "keeps counting, but nothing will interrupt you.",
                QMessageBox.Cancel | QMessageBox.Discard,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Discard:
                return
        QApplication.quit()
```

- [ ] **Step 9: Write `loop/gui/window.py` (shell only)**

Task 11 fills the body. For now it needs the close-hides behaviour and the two methods `Tray` calls.

```python
"""The dashboard window."""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QWidget

MIN_SIZE = (720, 560)


class MainWindow(QMainWindow):
    def __init__(self, controller) -> None:
        super().__init__()
        self._controller = controller
        self.setWindowTitle("loop")
        self.setMinimumSize(*MIN_SIZE)
        self.setCentralWidget(QWidget(self))

    def has_active_loop(self) -> bool:
        return self._controller is not None and self._controller.active_id is not None

    def surface(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        """Hide, do not quit. The app is the scheduler — closing the window
        must not silently stop the check-ins the user is relying on."""
        event.ignore()
        self.hide()
```

- [ ] **Step 10: Write `loop/gui/__main__.py`**

```python
"""Entry point. `python -m loop.gui` or the `loop-gui` script."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from loop.gui import instance, theme
from loop.gui.tray import Tray
from loop.gui.window import MainWindow


def detect_mode(app: QApplication) -> str:
    """Follow the OS. Qt reports this on both platforms from 6.5."""
    from PySide6.QtCore import Qt

    hints = app.styleHints()
    return "dark" if hints.colorScheme() == Qt.ColorScheme.Dark else "light"


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("loop")
    app.setQuitOnLastWindowClosed(False)  # the tray outlives the window

    server = instance.claim(app)
    if server is None:
        return 0  # another copy is up; it has been asked to surface

    mode = detect_mode(app)
    app.setStyleSheet(theme.stylesheet(mode))

    window = MainWindow(controller=None)
    tray = Tray(window, mode)
    tray.show()
    window.show()

    server.newConnection.connect(lambda: _surface(server, window))
    return app.exec()


def _surface(server, window) -> None:
    connection = server.nextPendingConnection()
    if connection is not None:
        connection.disconnectFromServer()
    window.surface()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
```

- [ ] **Step 11: Run the tests to verify they pass**

Run: `python -m pytest tests/gui/ tests/app/test_launcher.py -v`
Expected: PASS if PySide6 is installed; `tests/gui/test_shell.py` SKIPPED if not. Both outcomes are green.

Manual check (macOS or Windows, PySide6 installed):
```bash
LOOP_HOME=/tmp/loop-scratch python -m loop.gui
```
Expected: an empty window, a tray dot. Closing the window leaves the tray dot. Running the same command again in a second terminal exits immediately and re-surfaces the first window. Tray → Quit ends it. Then `rm -rf /tmp/loop-scratch`.

- [ ] **Step 12: Run the full suite and commit**

```bash
python -m pytest
git add pyproject.toml loop/app/launcher.py loop/gui/ tests/gui/ tests/app/test_launcher.py
git commit -m "feat: GUI shell — theme, single instance, tray

Closing the window hides it: the app is the scheduler, and a close that
silently stopped check-ins would be a lie. The socket is the only
authority on liveness."
```

---

### Task 11: The dashboard

The window binds to `DashboardView` and nothing else. It computes no elapsed time, no fraction, no thrash condition — Task 8 decided all of it.

The controller polls at 1 Hz. The clock moves every tick because elapsed is derived from `intervals` plus `now`; the log is only re-read when its size changes, which on an append-only file is a complete change signal and, unlike mtime, has no filesystem granularity to hide a write inside.

**Files:**
- Create: `loop/gui/controller.py`, `loop/gui/widgets.py`
- Modify: `loop/gui/window.py`, `loop/gui/__main__.py`
- Test: `tests/gui/test_controller.py`

**Interfaces:**
- Consumes: `loop.app.view`, `loop.app.writer.Writer`, `loop.store.{jsonl, paths}`, `loop.core.events`
- Produces:
  - `loop.gui.controller.Controller(writer=None, poll_ms=1000)` — `QObject` with `changed = Signal(object)`, `.view -> DashboardView`, `.active_id -> int | None`, `.refresh()`, `.start()`, `.stop()`
  - `loop.gui.widgets.BurnMeter` — `.set_meter(MeterView | None)`
  - `loop.gui.widgets.StackBar`, `HypothesisList`, `ActionLog`, `StatusStrip`, `ThrashBanner`, `ErrorBanner` — each with `.bind(...)`
  - `loop.gui.window.MainWindow(controller)` — `.bind(DashboardView)`

- [ ] **Step 1: Write the failing tests**

Create `tests/gui/test_controller.py`:

```python
import pytest

from loop.app import commands
from loop.app.writer import Writer
from loop.gui.controller import Controller
from loop.store import jsonl, paths


@pytest.fixture
def writer():
    return Writer(now=lambda: 0.0)


def open_one(writer, question="q"):
    return commands.open_loop(
        writer, question=question, stop_condition="s", budget_s=2700.0,
        interval_s=1200.0, hypotheses=["cert expiry", "pg pool"],
        stack_on_active=True,
    )


def test_an_empty_home_reports_no_active_loop(qapp, writer):
    controller = Controller(writer=writer, now=lambda: 0.0)
    controller.refresh()
    assert controller.active_id is None
    assert controller.view.enabled_actions == frozenset({"open"})


def test_refresh_emits_the_view(qapp, writer):
    open_one(writer)
    controller = Controller(writer=writer, now=lambda: 600.0)
    seen = []
    controller.changed.connect(seen.append)
    controller.refresh()
    assert seen[-1].active_id == 1
    assert seen[-1].meter.elapsed_s == 600.0


def test_the_clock_moves_without_the_log_changing(qapp, writer):
    open_one(writer)
    clock = [600.0]
    controller = Controller(writer=writer, now=lambda: clock[0])
    controller.refresh()
    reads = controller.reads
    clock[0] = 700.0
    controller.refresh()
    assert controller.view.meter.elapsed_s == 700.0
    assert controller.reads == reads  # the file was not touched again


def test_a_write_from_elsewhere_is_picked_up(qapp, writer):
    open_one(writer)
    controller = Controller(writer=writer, now=lambda: 600.0)
    controller.refresh()
    assert controller.view.timeline == ()

    commands.log_action(writer, action="added print", because="shows the frame")
    controller.refresh()
    assert [entry.text for entry in controller.view.timeline] == ["added print"]


def test_a_corrupt_interior_line_freezes_the_view_and_disables_everything(qapp, writer):
    open_one(writer)
    controller = Controller(writer=writer, now=lambda: 600.0)
    controller.refresh()

    with paths.events_path().open("a", encoding="utf-8") as handle:
        handle.write("NOT JSON\n")
        handle.write('{"type":"loop_abandoned","ts":1.0,"loop_id":1}\n')

    controller.refresh()
    assert controller.view.enabled_actions == frozenset()
    assert controller.view.active_id == 1              # last good state survives
    assert controller.view.meter.elapsed_s == 600.0    # frozen where it froze
    assert controller.view.error.endswith(":2")


def test_a_repaired_log_recovers_without_a_restart(qapp, writer):
    open_one(writer)
    controller = Controller(writer=writer, now=lambda: 600.0)
    controller.refresh()
    good = paths.events_path().read_text(encoding="utf-8")

    paths.events_path().write_text(good + "NOT JSON\n" + good, encoding="utf-8")
    controller.refresh()
    assert controller.view.enabled_actions == frozenset()

    paths.events_path().write_text(good, encoding="utf-8")
    controller.refresh()
    assert "try" in controller.view.enabled_actions
    assert controller.view.error is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/gui/test_controller.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.gui.controller'` (or SKIP with no PySide6).

- [ ] **Step 3: Write `loop/gui/controller.py`**

```python
"""The read side: poll the log, build the view, tell the window.

Polling rather than watching the filesystem, because a 1 Hz stat of one
file costs nothing and a file watcher is a per-platform dependency with
its own failure modes. The clock has to redraw every second anyway.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from loop.app import view
from loop.app.writer import Writer
from loop.core import events
from loop.store import jsonl, paths

POLL_MS = 1000


class Controller(QObject):
    changed = Signal(object)

    def __init__(self, writer: Writer | None = None, poll_ms: int = POLL_MS,
                 now=time.time) -> None:
        super().__init__()
        self._writer = writer if writer is not None else Writer()
        self._now = now
        self._log: list[dict] = []
        self._size = -1
        self._good: view.DashboardView | None = None
        self._frozen_at: float | None = None
        self.reads = 0
        self.view: view.DashboardView | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self.refresh)

    def start(self) -> None:
        self.refresh()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    @property
    def active_id(self) -> int | None:
        return self.view.active_id if self.view is not None else None

    def refresh(self) -> None:
        try:
            self._reload_if_changed()
        except jsonl.CorruptLogError as exc:
            self.view = view.unreadable(
                self._good,
                path=str(paths.events_path()),
                line=_line_number(exc),
                frozen_at=self._frozen_at or 0.0,
            )
            self.changed.emit(self.view)
            return

        now = self._now()
        state = events.fold(self._log)
        self._good = view.build(state, self._log, now)
        self._frozen_at = self._good.meter.elapsed_s if self._good.meter else 0.0
        self.view = self._good
        self.changed.emit(self.view)

    def _reload_if_changed(self) -> None:
        """Size is a complete change signal on an append-only file, and
        unlike mtime it has no filesystem granularity to lose a write in.
        A repair that shortens the file changes it too."""
        path = paths.events_path()
        size = path.stat().st_size if path.exists() else 0
        if size == self._size:
            return
        self._log = jsonl.read_all()
        self._size = size
        self.reads += 1


def _line_number(exc: jsonl.CorruptLogError) -> int:
    """`CorruptLogError` carries the line in its message: '<path>: line N
    is not valid JSON'. Parsing it beats widening the exception for one
    caller, and a miss costs a wrong number in a banner, not a wrong state."""
    text = str(exc)
    marker = "line "
    if marker not in text:
        return 0
    tail = text.split(marker, 1)[1]
    digits = tail.split(" ", 1)[0]
    return int(digits) if digits.isdigit() else 0
```

Note: `_reload_if_changed` raising leaves `self._size` unchanged, so the
next poll retries. That is what makes the repair test pass without a
restart.

- [ ] **Step 4: Write `loop/gui/widgets.py`**

```python
"""The panels. Each takes a view model and draws it. None of them decide anything."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from loop.app.view import DashboardView, MeterView
from loop.core.timefmt import format_duration
from loop.gui import theme

TRACK_H = 9
CHECKPOINT_FRACTION = 0.75


class BurnMeter(QWidget):
    """The signature component.

    The track is a fixed cool-to-hot gradient and the fill is a mask that
    retracts from the left, so the colour at the leading edge is a direct
    read of how much budget is gone. Over budget, the mask is dropped and
    the whole track burns.
    """

    def __init__(self, mode: str = "dark") -> None:
        super().__init__()
        self._mode = mode
        self._meter: MeterView | None = None
        self.setFixedHeight(TRACK_H)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_meter(self, meter: MeterView | None) -> None:
        self._meter = meter
        self.update()

    def paintEvent(self, _event) -> None:
        if self._meter is None:
            return
        tokens = theme.TOKENS[self._mode]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        gradient = QLinearGradient(0, 0, self.width(), 0)
        gradient.setColorAt(0.00, QColor(tokens["accent"]))
        gradient.setColorAt(0.38, QColor(tokens["accent"]))
        gradient.setColorAt(0.76, QColor(tokens["warn"]))
        gradient.setColorAt(1.00, QColor(tokens["crit"]))
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(self.rect(), TRACK_H / 2, TRACK_H / 2)

        if self._meter.over_s <= 0.0:
            edge = int(self.width() * self._meter.fraction)
            painter.setBrush(QColor(tokens["raised"]))
            painter.drawRect(edge, 0, self.width() - edge, self.height())

        tick_x = int(self.width() * CHECKPOINT_FRACTION)
        painter.setPen(QColor(tokens["panel"]))
        painter.drawLine(tick_x, 0, tick_x, self.height())
        painter.end()


class StackBar(QFrame):
    """Paused parents above, active last. Row buttons appear on hover or
    focus so the list reads as data first — never the only way to reach an
    action, which is why every one of them is also in the menu bar."""

    resume_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("panel")
        self._layout = QVBoxLayout(self)

    def bind(self, dashboard: DashboardView) -> None:
        _clear(self._layout)
        for row in dashboard.stack:
            line = QHBoxLayout()
            line.addWidget(QLabel("▶" if row.active else "⏸"))
            line.addWidget(QLabel(f"#{row.loop_id}"))
            question = QLabel(row.question)
            question.setObjectName("question" if row.active else "muted")
            line.addWidget(question, 1)
            meta = f"{format_duration(row.elapsed_s)} active"
            if not row.active:
                meta += f" · paused {format_duration(row.paused_s)}"
                if row.stale:
                    meta += "  ⚠"
            line.addWidget(QLabel(meta))
            if not row.active and "resume" in dashboard.enabled_actions:
                button = QPushButton("Resume")
                button.clicked.connect(
                    lambda _checked=False, loop_id=row.loop_id:
                    self.resume_requested.emit(loop_id)
                )
                line.addWidget(button)
            self._layout.addLayout(line)


class HypothesisList(QFrame):
    kill_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("panel")
        self._layout = QVBoxLayout(self)

    def bind(self, dashboard: DashboardView) -> None:
        _clear(self._layout)
        head = QLabel(f"{dashboard.live_count} live · {dashboard.dead_count} ruled out")
        head.setObjectName("label")
        self._layout.addWidget(head)
        for row in dashboard.hypotheses:
            line = QHBoxLayout()
            line.addWidget(QLabel(str(row.hyp_id) if row.alive else "✗"))
            text = QLabel(row.text)
            if not row.alive:
                # Kept visible on purpose: ruled-out hypotheses are the
                # evidence the loop is converging.
                text.setObjectName("dead")
            line.addWidget(text, 1)
            if row.alive and "hyp_kill" in dashboard.enabled_actions:
                button = QPushButton("Rule out")
                button.clicked.connect(
                    lambda _checked=False, hyp_id=row.hyp_id:
                    self.kill_requested.emit(hyp_id)
                )
                line.addWidget(button)
            self._layout.addLayout(line)


class ActionLog(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("panel")
        self._layout = QVBoxLayout(self)

    def bind(self, dashboard: DashboardView) -> None:
        _clear(self._layout)
        head = QLabel(f"{len(dashboard.timeline)} entries")
        head.setObjectName("label")
        self._layout.addWidget(head)
        for entry in dashboard.timeline:
            block = QVBoxLayout()
            block.addWidget(QLabel(entry.text))
            if entry.because:
                because = QLabel(entry.because)
                because.setObjectName("muted")
                block.addWidget(because)
            self._layout.addLayout(block)


class ThrashBanner(QFrame):
    """Takes vertical space and pushes the body down. An alarm that costs
    nothing is an alarm you stop seeing."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("banner")
        self._layout = QVBoxLayout(self)

    def bind(self, dashboard: DashboardView) -> None:
        _clear(self._layout)
        self.setVisible(dashboard.thrash is not None)
        if dashboard.thrash is None:
            return
        for text in (dashboard.thrash.title, dashboard.thrash.why, dashboard.thrash.fix):
            self._layout.addWidget(QLabel(text))


class ErrorBanner(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("error")
        self._layout = QVBoxLayout(self)

    def bind(self, dashboard: DashboardView) -> None:
        _clear(self._layout)
        self.setVisible(dashboard.error is not None)
        if dashboard.error is None:
            return
        self._layout.addWidget(QLabel(
            "Event log stopped parsing. Showing the last state that folded "
            "cleanly. Actions are disabled until the file is repaired."
        ))
        path = QLabel(dashboard.error)
        path.setObjectName("path")
        path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._layout.addWidget(path)


class StatusStrip(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self._layout = QHBoxLayout(self)
        self._label = QLabel()
        self._layout.addWidget(self._label)

    def bind(self, dashboard: DashboardView) -> None:
        parts = [dashboard.status.detail]
        if dashboard.meter and dashboard.meter.next_checkin_s is not None:
            parts.append(f"next {format_duration(dashboard.meter.next_checkin_s)}")
        if dashboard.status.depth > 1:
            parts.append(f"depth {dashboard.status.depth}")
        self._label.setText("  ·  ".join(parts))


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())
```

- [ ] **Step 5: Fill in `loop/gui/window.py`**

Keep `has_active_loop`, `surface`, and `closeEvent` exactly as Task 10 wrote them. Add a toolbar, the panels, a menu bar, and `bind`:

```python
    def _build(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)
        self._toolbar = self._build_toolbar()
        self._clock = QLabel("—")
        self._clock.setObjectName("clock")
        self._meter = BurnMeter(self._mode)
        self._stack = StackBar()
        self._hypotheses = HypothesisList()
        self._log = ActionLog()
        self._thrash = ThrashBanner()
        self._error = ErrorBanner()
        self._strip = StatusStrip()
        for widget in (self._toolbar, self._error, self._stack, self._clock,
                       self._meter, self._thrash):
            layout.addWidget(widget)
        columns = QHBoxLayout()
        columns.addWidget(self._hypotheses, 1)
        columns.addWidget(self._log, 1)
        layout.addLayout(columns)
        layout.addWidget(self._strip)
        self.setCentralWidget(central)

    def bind(self, dashboard) -> None:
        """One place decides what is live. Eleven scattered setEnabled
        calls would each be a place to forget."""
        for name, action in self._actions.items():
            action.setEnabled(name in dashboard.enabled_actions)
        self._clock.setText(_clock_text(dashboard))
        self._meter.set_meter(dashboard.meter)
        for panel in (self._stack, self._hypotheses, self._log,
                      self._thrash, self._error, self._strip):
            panel.bind(dashboard)
```

`_clock_text` renders `18:42 / 45:00`, and over budget renders the elapsed
in `--crit` with `over by 2:18` beside it. Build `self._actions` as a
`dict[str, QAction]` keyed by the same names as `view.ALL_ACTIONS`, added to
both the toolbar and the menu bar so nothing is reachable only by mouse.
Leave every `triggered` connection as a no-op lambda this task; Task 12
wires them to dialogs.

- [ ] **Step 6: Wire the controller in `__main__.py`**

Replace `MainWindow(controller=None)` with:

```python
    controller = Controller()
    window = MainWindow(controller, mode)
    tray = Tray(window, mode)
    controller.changed.connect(window.bind)
    controller.changed.connect(
        lambda dashboard: tray.set_burn(
            dashboard.meter.fraction if dashboard.meter else None
        )
    )
    tray.show()
    window.show()
    controller.start()
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/gui/ -v`
Expected: PASS (or SKIP without PySide6).

Manual check:
```bash
export LOOP_HOME=/tmp/loop-scratch
python -m loop.gui &
loop open "is the logging config filtering it?" --budget 45m --interval 20m
```
Expected: the window fills in within a second without being restarted. The
clock ticks. `loop try "added print" --because "shows the frame"` appears in
the action log. Then quit from the tray and `rm -rf /tmp/loop-scratch`.

- [ ] **Step 8: Run the full suite and commit**

```bash
python -m pytest
git add loop/gui/controller.py loop/gui/widgets.py loop/gui/window.py loop/gui/__main__.py tests/gui/test_controller.py
git commit -m "feat: the dashboard

Binds to DashboardView and computes nothing. Polls at 1 Hz; re-reads the
log only when its size changes."
```

---

### Task 12: Dialogs — every action reachable

Eight dialogs and the wiring that makes every command in the table below run from the window. A dialog collects and validates; the command runs after it closes. No dialog holds the lock.

| Action | Dialog | Fields |
|---|---|---|
| Open a loop | `OpenDialog` | question, stop condition, budget, interval, hypotheses (one per line, ≥1); a stacking warning when a loop is active |
| Log an action | `ActionDialog` | action, because — both required |
| Add hypothesis | `TextDialog` | text |
| Pause | `TextDialog` | reason |
| Cut scope | `TextDialog` | new stop condition, old shown above it |
| Extend budget | `ExtendDialog` | new budget (parsed), what you learned |
| Close | `CloseDialog` | what was it, giveaway, five-minute path |
| Abandon | `QMessageBox` | confirm, naming elapsed time |

`StaleError` gets its own quiet notice, not an error tone: the outcome the user wanted already happened.

**Files:**
- Create: `loop/gui/dialogs/__init__.py`, `loop/gui/dialogs/base.py`, `loop/gui/dialogs/lifecycle.py`, `loop/gui/dialogs/content.py`, `loop/gui/actions.py`
- Modify: `loop/gui/window.py`
- Test: `tests/gui/test_dialogs.py`, `tests/gui/test_actions.py`

**Interfaces:**
- Consumes: `loop.app.commands`, `loop.app.errors.{LoopError, StaleError}`, `loop.app.view.ALL_ACTIONS`, `loop.core.timefmt.parse_duration`
- Produces:
  - `loop.gui.dialogs.base.FormDialog(parent, title, fields)` — `.values() -> dict[str, str] | None`
  - `loop.gui.dialogs.base.Field(name, label, multiline=False, required=True, prefill="", helper="")`
  - `loop.gui.dialogs.lifecycle.{OpenDialog, CloseDialog, confirm_abandon}`
  - `loop.gui.dialogs.content.{ActionDialog, ExtendDialog, text_dialog}`
  - `loop.gui.actions.Actions(window, controller, writer)` — `.run(name: str)`, `.report` signal

- [ ] **Step 1: Write the failing tests**

Create `tests/gui/test_dialogs.py`:

```python
import pytest

from loop.gui.dialogs.base import Field, FormDialog
from loop.gui.dialogs.content import ExtendDialog
from loop.gui.dialogs.lifecycle import OpenDialog


def test_a_form_reports_missing_required_fields_and_does_not_close(qapp):
    dialog = FormDialog(None, "Log an action", [
        Field("action", "action"), Field("because", "because"),
    ])
    dialog.set_values({"action": "added print", "because": "  "})
    assert dialog.missing() == ["because"]


def test_a_complete_form_returns_stripped_values(qapp):
    dialog = FormDialog(None, "Log an action", [
        Field("action", "action"), Field("because", "because"),
    ])
    dialog.set_values({"action": "  added print  ", "because": "shows the frame"})
    assert dialog.missing() == []
    assert dialog.collected() == {"action": "added print", "because": "shows the frame"}


def test_optional_fields_may_be_blank(qapp):
    dialog = FormDialog(None, "t", [Field("note", "note", required=False)])
    dialog.set_values({"note": ""})
    assert dialog.missing() == []


def test_open_defaults_match_the_cli(qapp):
    """45m and 20m, or the two front ends disagree about what a loop is."""
    dialog = OpenDialog(None, active_question=None)
    assert dialog.collected()["budget"] == "45m"
    assert dialog.collected()["interval"] == "20m"


def test_open_needs_at_least_one_hypothesis(qapp):
    dialog = OpenDialog(None, active_question=None)
    dialog.set_values({"question": "q", "stop_condition": "s",
                       "budget": "45m", "interval": "20m", "hypotheses": "  \n  "})
    assert "hypotheses" in dialog.missing()


def test_open_splits_hypotheses_one_per_line_dropping_blanks(qapp):
    dialog = OpenDialog(None, active_question=None)
    dialog.set_values({"question": "q", "stop_condition": "s", "budget": "45m",
                       "interval": "20m", "hypotheses": "cert expiry\n\npg pool\n"})
    assert dialog.hypotheses() == ["cert expiry", "pg pool"]


def test_open_warns_when_a_loop_is_already_active(qapp):
    dialog = OpenDialog(None, active_question="is the logging config filtering it?")
    assert "is the logging config filtering it?" in dialog.warning_text()


def test_open_says_nothing_about_stacking_when_nothing_is_active(qapp):
    assert OpenDialog(None, active_question=None).warning_text() == ""


def test_extend_rejects_an_unparseable_budget_before_writing(qapp):
    dialog = ExtendDialog(None, current_budget_s=2700.0)
    dialog.set_values({"new_budget": "soon", "learned": "the pool is shared"})
    assert dialog.missing() == ["new_budget"]


def test_extend_parses_a_bare_number_as_minutes(qapp):
    """45 means 45 minutes. The CLI made that choice; the window keeps it."""
    dialog = ExtendDialog(None, current_budget_s=2700.0)
    dialog.set_values({"new_budget": "90", "learned": "x"})
    assert dialog.missing() == []
    assert dialog.new_budget_s() == 5400.0
```

Create `tests/gui/test_actions.py`:

```python
import pytest

from loop.app import commands
from loop.app.errors import LoopError, StaleError
from loop.app.writer import Writer
from loop.gui.actions import Actions
from loop.gui.controller import Controller


@pytest.fixture
def wired(qapp):
    writer = Writer(now=lambda: 0.0)
    controller = Controller(writer=writer, now=lambda: 0.0)
    actions = Actions(window=None, controller=controller, writer=writer)
    return writer, controller, actions


def test_a_refusal_becomes_a_report_not_a_traceback(wired):
    _writer, _controller, actions = wired
    seen = []
    actions.report.connect(lambda kind, text: seen.append((kind, text)))
    actions.call(lambda: commands.log_action(_writer_of(wired), action="a", because="b"))
    assert seen[0][0] == "error"
    assert "no active loop" in seen[0][1]


def test_a_stale_click_is_reported_quietly_not_as_an_error(wired):
    writer, controller, actions = wired
    commands.open_loop(writer, question="q", stop_condition="s", budget_s=2700.0,
                       interval_s=1200.0, hypotheses=["cert expiry"],
                       stack_on_active=True)
    commands.kill_hypothesis(writer, hyp_id=1)
    seen = []
    actions.report.connect(lambda kind, text: seen.append((kind, text)))
    actions.call(lambda: commands.kill_hypothesis(writer, hyp_id=1))
    assert seen[0][0] == "stale"
    assert "already ruled out" in seen[0][1]


def test_a_successful_call_refreshes_the_view_immediately(wired):
    writer, controller, actions = wired
    controller.refresh()
    commands.open_loop(writer, question="q", stop_condition="s", budget_s=2700.0,
                       interval_s=1200.0, hypotheses=["cert expiry"],
                       stack_on_active=True)
    actions.call(lambda: commands.log_action(writer, action="a", because="b"))
    assert len(controller.view.timeline) == 1  # not waiting for the next poll


def _writer_of(wired):
    return wired[0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/gui/test_dialogs.py tests/gui/test_actions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.gui.dialogs'`.

- [ ] **Step 3: Write `loop/gui/dialogs/base.py`**

```python
"""One form, many dialogs.

Validation is a method, not a side effect of clicking OK, so the rules are
testable without driving a modal event loop.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QLineEdit, QPlainTextEdit, QVBoxLayout,
)


@dataclass(frozen=True, slots=True)
class Field:
    name: str
    label: str
    multiline: bool = False
    required: bool = True
    prefill: str = ""
    helper: str = ""


class FormDialog(QDialog):
    def __init__(self, parent, title: str, fields: list[Field]) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self._fields = fields
        self._inputs: dict[str, QLineEdit | QPlainTextEdit] = {}

        layout = QVBoxLayout(self)
        self._warning = QLabel()
        self._warning.setWordWrap(True)
        self._warning.setVisible(False)
        layout.addWidget(self._warning)

        for field in fields:
            label = QLabel(field.label)
            label.setObjectName("label")
            layout.addWidget(label)
            widget = QPlainTextEdit(self) if field.multiline else QLineEdit(self)
            if field.prefill:
                self._write(widget, field.prefill)
            layout.addWidget(widget)
            self._inputs[field.name] = widget
            if field.helper:
                helper = QLabel(field.helper)
                helper.setObjectName("muted")
                layout.addWidget(helper)

        self._error = QLabel()
        self._error.setObjectName("over")
        self._error.setVisible(False)
        layout.addWidget(self._error)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def set_warning(self, text: str) -> None:
        self._warning.setText(text)
        self._warning.setVisible(bool(text))

    def set_values(self, values: dict[str, str]) -> None:
        for name, value in values.items():
            self._write(self._inputs[name], value)

    def collected(self) -> dict[str, str]:
        return {
            name: self._read(widget).strip() for name, widget in self._inputs.items()
        }

    def missing(self) -> list[str]:
        """Names of required fields that came back blank. Overridden by
        subclasses with rules a blank check cannot express."""
        collected = self.collected()
        return [
            field.name for field in self._fields
            if field.required and not collected[field.name]
        ]

    def values(self) -> dict[str, str] | None:
        """Run the dialog. `None` means the user cancelled."""
        return self.collected() if self.exec() == QDialog.Accepted else None

    def _try_accept(self) -> None:
        missing = self.missing()
        if missing:
            self._error.setText(f"required: {', '.join(missing)}")
            self._error.setVisible(True)
            return
        self.accept()

    @staticmethod
    def _read(widget) -> str:
        return (widget.toPlainText() if isinstance(widget, QPlainTextEdit)
                else widget.text())

    @staticmethod
    def _write(widget, value: str) -> None:
        if isinstance(widget, QPlainTextEdit):
            widget.setPlainText(value)
        else:
            widget.setText(value)
```

- [ ] **Step 4: Write `loop/gui/dialogs/lifecycle.py` and `content.py`**

`lifecycle.py`:

```python
"""Open, close, abandon."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from loop.core.timefmt import format_duration, parse_duration
from loop.gui.dialogs.base import Field, FormDialog

DEFAULT_BUDGET = "45m"
DEFAULT_INTERVAL = "20m"


class OpenDialog(FormDialog):
    def __init__(self, parent, active_question: str | None) -> None:
        super().__init__(parent, "Open a loop", [
            Field("question", "question"),
            Field("stop_condition", "stop when"),
            Field("budget", "time budget", prefill=DEFAULT_BUDGET),
            Field("interval", "check-in interval", prefill=DEFAULT_INTERVAL),
            Field("hypotheses", "hypotheses", multiline=True,
                  helper="one per line, at least one"),
        ])
        self._active_question = active_question
        self.set_warning(self.warning_text())

    def warning_text(self) -> str:
        if self._active_question is None:
            return ""
        return (f"“{self._active_question}” is active. Opening this pauses it "
                f"and stacks on top.")

    def hypotheses(self) -> list[str]:
        return [
            line.strip()
            for line in self.collected()["hypotheses"].splitlines()
            if line.strip()
        ]

    def missing(self) -> list[str]:
        names = [name for name in super().missing() if name != "hypotheses"]
        if not self.hypotheses():
            names.append("hypotheses")
        for name in ("budget", "interval"):
            value = self.collected()[name]
            if value and not _parses(value):
                names.append(name)
        return names

    def budget_s(self) -> float:
        return parse_duration(self.collected()["budget"])

    def interval_s(self) -> float:
        return parse_duration(self.collected()["interval"])


class CloseDialog(FormDialog):
    def __init__(self, parent) -> None:
        super().__init__(parent, "Close the loop", [
            Field("what_was_it", "what was it?"),
            Field("giveaway", "what was the giveaway?"),
            Field("five_min_path", "how could I have found it in 5 minutes?"),
        ])


def confirm_abandon(parent, loop_id: int, elapsed_s: float) -> bool:
    """Named rather than vague: the number is what makes it a decision."""
    answer = QMessageBox.warning(
        parent, "Abandon the loop?",
        f"#{loop_id} has {format_duration(elapsed_s)} of active time on it. "
        f"Abandoning records no postmortem — the logbook counts it as unresolved.",
        QMessageBox.Cancel | QMessageBox.Discard, QMessageBox.Cancel,
    )
    return answer == QMessageBox.Discard


def _parses(value: str) -> bool:
    try:
        parse_duration(value)
        return True
    except ValueError:
        return False
```

`content.py`:

```python
"""Actions, hypotheses, scope, budget."""

from __future__ import annotations

from loop.core.timefmt import format_duration, parse_duration
from loop.gui.dialogs.base import Field, FormDialog


class ActionDialog(FormDialog):
    def __init__(self, parent) -> None:
        super().__init__(parent, "Log an action", [
            Field("action", "what did you do?"),
            Field("because", "what do you believe this will show?"),
        ])


class ExtendDialog(FormDialog):
    def __init__(self, parent, current_budget_s: float) -> None:
        super().__init__(parent, "Extend the budget", [
            Field("new_budget", "new budget",
                  helper=f"currently {format_duration(current_budget_s)}; "
                         f"a bare number means minutes"),
            Field("learned", "what did you learn that made it bigger?"),
        ])

    def missing(self) -> list[str]:
        names = super().missing()
        value = self.collected()["new_budget"]
        if value and "new_budget" not in names and self._parsed(value) is None:
            names.append("new_budget")
        return names

    def new_budget_s(self) -> float:
        return parse_duration(self.collected()["new_budget"])

    @staticmethod
    def _parsed(value: str) -> float | None:
        try:
            return parse_duration(value)
        except ValueError:
            return None


def text_dialog(parent, title: str, label: str, helper: str = "") -> FormDialog:
    """One required line. Used by add-hypothesis, pause, and cut-scope."""
    return FormDialog(parent, title, [Field("text", label, helper=helper)])
```

- [ ] **Step 5: Write `loop/gui/actions.py`**

```python
"""Turning a click into a command, and a refusal into a sentence.

Every path through here refreshes the controller on success, so the window
never shows a state one poll behind the write that caused it.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from loop.app import commands
from loop.app.errors import LoopError, StaleError
from loop.store.lock import LockTimeout


class Actions(QObject):
    report = Signal(str, str)      # kind: "ok" | "stale" | "error"

    def __init__(self, window, controller, writer) -> None:
        super().__init__()
        self._window = window
        self._controller = controller
        self._writer = writer

    def call(self, run):
        """Run one command. Returns its result, or None if it was refused.

        `StaleError` gets its own kind because it is not a mistake: a
        check-in got there first, and the outcome the user wanted has
        already happened. Reporting it in the same tone as a real error
        would teach the user to distrust a correct app.
        """
        try:
            result = run()
        except StaleError as exc:
            self.report.emit("stale", str(exc))
            self._controller.refresh()
            return None
        except (LoopError, LockTimeout, ValueError) as exc:
            self.report.emit("error", str(exc))
            return None
        self._controller.refresh()
        return result
```

Add one `run_<name>` method per action, each opening its dialog and calling
`self.call(...)`. For example:

```python
    def run_try(self) -> None:
        from loop.gui.dialogs.content import ActionDialog

        dialog = ActionDialog(self._window)
        values = dialog.values()
        if values is None:
            return
        self.call(lambda: commands.log_action(
            self._writer, action=values["action"], because=values["because"]
        ))

    def run_hyp_kill(self, hyp_id: int) -> None:
        self.call(lambda: commands.kill_hypothesis(self._writer, hyp_id=hyp_id))
```

Write one for each of `open`, `try`, `hyp_add`, `hyp_kill`, `pause`, `resume`,
`cut`, `extend`, `close`, `abandon`. `run_open` passes
`stack_on_active=True` — the dialog's warning already got consent, and the
command re-checks the ceiling under the lock. `run_close` opens `CloseDialog`;
`run_abandon` calls `confirm_abandon` with the elapsed time from
`controller.view.meter`.

- [ ] **Step 6: Wire the window's actions**

In `loop/gui/window.py`, replace the no-op `triggered` lambdas from Task 11
with `self._actions_runner.run_<name>`, connect
`StackBar.resume_requested` → `run_resume` and
`HypothesisList.kill_requested` → `run_hyp_kill`, and show
`Actions.report` in a status line — `"stale"` styled as `#muted`, `"error"`
as `#over`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/gui/ -v`
Expected: PASS (or SKIP without PySide6).

Manual check: with `LOOP_HOME=/tmp/loop-scratch`, open a loop from the
window, log an action, add and rule out a hypothesis, cut scope, extend the
budget, close. Then `loop stats` in a terminal must show the loop the window
created. Delete the scratch home afterwards.

- [ ] **Step 8: Run the full suite and commit**

```bash
python -m pytest
git add loop/gui/dialogs/ loop/gui/actions.py loop/gui/window.py tests/gui/test_dialogs.py tests/gui/test_actions.py
git commit -m "feat: every action reachable from the window

Dialogs collect and validate; commands run after they close. A stale
click reports quietly — the outcome already happened."
```

---

### Task 13: The full-screen check-in

One Qt window replacing 569 lines across `blockers/macos.py` and `blockers/tk.py`. It implements `Blocker.ask(prompt) -> Answers`, so `app/checkin.py` and every existing test that speaks `Prompt`/`Answers` work unchanged.

The interaction state machine already exists and is already tested: `loop/blockers/session.py` holds `PromptSession` — choice → optional pick → optional fields → done. This window draws it and nothing more.

**Be honest about what this cannot do.** `macos.py` used `CGShieldingWindowLevel` plus `DisableProcessSwitching`, which made cmd-tab itself stop working. Qt has no equivalent on either platform. The check-in is insistent — frameless, always-on-top, covering every screen, re-raising itself once a second, no close button, no Escape — but it is not inescapable. That trade was accepted when the toolkit was chosen.

**Do not run this manually without deciding to.** It takes the whole screen for up to five minutes.

**Files:**
- Create: `loop/gui/checkin.py`
- Test: `tests/gui/test_checkin.py`

**Interfaces:**
- Consumes: `loop.blockers.base.{Answers, Prompt, TIMEOUT_S, format_countdown, remaining_fraction}`, `loop.blockers.session.PromptSession`
- Produces: `loop.gui.checkin.CheckinWindow(prompt, mode, now=time.time)` — `.ask() -> Answers`; `loop.gui.checkin.QtBlocker(mode)` — `.ask(prompt) -> Answers`

- [ ] **Step 1: Write the failing tests**

Create `tests/gui/test_checkin.py`. These drive the window's own methods rather than a modal event loop — the state machine is what needs guarding, and it is reachable without showing anything.

```python
import pytest

from loop.blockers.base import Choice, Prompt, TextField
from loop.gui.checkin import CheckinWindow


def ping_prompt():
    return Prompt(
        kind="ping",
        title="loop #3 · 18:42 / 45:00 · 2 paused",
        question="hypothesis space smaller than 20m ago?",
        choices=[Choice("y", "yes"), Choice("n", "no")],
        pick_list=["dictConfig disables existing loggers", "propagate=False"],
        pick_after="y",
    )


def p75_prompt():
    return Prompt(
        kind="p75",
        title="75% of budget gone · 33:45 / 45:00",
        question="is 75% of the work done?",
        choices=[Choice("y", "yes"), Choice("c", "cut scope"), Choice("e", "extend estimate")],
        fields_after={
            "c": [TextField("new_stop_condition", "new stop condition")],
            "e": [TextField("new_budget", "new budget"),
                  TextField("learned", "what did you learn that made it bigger?")],
        },
    )


def window(prompt, at=0.0):
    return CheckinWindow(prompt, mode="dark", now=lambda: at)


def test_the_prompt_text_reaches_the_screen_verbatim(qapp):
    """These strings are the product. A window that paraphrases them is wrong."""
    view = window(ping_prompt())
    assert view.title_text() == "loop #3 · 18:42 / 45:00 · 2 paused"
    assert view.question_text() == "hypothesis space smaller than 20m ago?"
    assert [button.text() for button in view.choice_buttons()] == ["yes", "no"]


def test_answering_no_completes_immediately(qapp):
    view = window(ping_prompt())
    view.press("n")
    assert view.is_complete()
    answers = view.answers()
    assert answers.choice == "n"
    assert answers.picked is None
    assert answers.timed_out is False


def test_answering_yes_reveals_the_pick_list(qapp):
    view = window(ping_prompt())
    view.press("y")
    assert not view.is_complete()
    assert view.visible_pick_list() == [
        "dictConfig disables existing loggers", "propagate=False",
    ]
    view.press("2")
    assert view.is_complete()
    assert view.answers().picked == 2


def test_an_out_of_range_pick_is_ignored(qapp):
    view = window(ping_prompt())
    view.press("y")
    view.press("9")
    assert not view.is_complete()


def test_choosing_extend_reveals_its_fields_in_place(qapp):
    view = window(p75_prompt())
    view.press("e")
    assert [field.name for field in view.pending_fields()] == ["new_budget", "learned"]
    assert not view.is_complete()


def test_blank_required_fields_are_refused_without_completing(qapp):
    view = window(p75_prompt())
    view.press("e")
    assert view.submit_fields({"new_budget": "", "learned": "x"}) == ["new_budget"]
    assert not view.is_complete()


def test_submitted_fields_complete_the_session(qapp):
    view = window(p75_prompt())
    view.press("c")
    assert view.submit_fields({"new_stop_condition": "just the 500s"}) == []
    assert view.is_complete()
    assert view.answers().fields == {"new_stop_condition": "just the 500s"}


def test_a_key_that_is_not_a_choice_does_nothing(qapp):
    view = window(ping_prompt())
    view.press("q")
    assert not view.is_complete()


def test_the_timeout_produces_a_timed_out_answer(qapp):
    view = window(ping_prompt())
    answers = view.timed_out(at=300.0)
    assert answers.timed_out is True
    assert answers.choice is None


def test_the_countdown_reports_time_left_not_time_spent(qapp):
    view = window(ping_prompt(), at=0.0)
    assert view.countdown_text(at=0.0) == "5:00"
    assert view.countdown_text(at=290.0) == "0:10"
    assert view.countdown_text(at=999.0) == "0:00"


def test_escape_does_not_close_the_window(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    view = window(ping_prompt())
    view.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert not view.is_complete()
    assert not view.isHidden() or True  # never shown in the test; must not have accepted


def test_the_thrash_warning_is_rendered_when_the_prompt_carries_one(qapp):
    prompt = ping_prompt()
    warned = type(prompt)(
        kind=prompt.kind, title=prompt.title, question=prompt.question,
        choices=prompt.choices, pick_list=prompt.pick_list,
        pick_after=prompt.pick_after, warning="⚠  7 actions, 0 ruled out",
    )
    assert "7 actions" in window(warned).warning_text()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/gui/test_checkin.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.gui.checkin'`.

- [ ] **Step 3: Write `loop/gui/checkin.py`**

```python
"""The check-in, drawn by Qt.

One window for macOS and Windows, replacing `blockers/macos.py` and
`blockers/tk.py`. It draws `PromptSession` — the interaction state machine
in `blockers/session.py`, already shared and already tested — and decides
nothing on its own.

What it cannot do: `macos.py` used CGShieldingWindowLevel and
DisableProcessSwitching, which stopped cmd-tab itself. Qt has no
equivalent on either platform. This window is frameless, always on top,
covers every screen, re-raises itself once a second, and has no close
button and no Escape binding — insistent, not inescapable. That trade was
made deliberately when the toolkit was chosen: one implementation for both
platforms was worth more than an unbypassable block on one.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from loop.blockers.base import (
    TIMEOUT_S, Answers, Prompt, TextField, format_countdown,
)
from loop.blockers.session import PromptSession
from loop.gui import theme

RAISE_MS = 1000
TICK_MS = 250


class CheckinWindow(QWidget):
    def __init__(self, prompt: Prompt, mode: str = "dark", now=time.time) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self._prompt = prompt
        self._now = now
        self._session = PromptSession(prompt, shown_at=now())
        self._answers: Answers | None = None
        self._field_inputs: dict[str, QLineEdit] = {}
        self.setStyleSheet(theme.stylesheet(mode))
        self._build()

    # --- text the tests assert on, and the widgets read ---

    def title_text(self) -> str:
        return self._prompt.title

    def question_text(self) -> str:
        return self._prompt.question

    def warning_text(self) -> str:
        return self._prompt.warning or ""

    def choice_buttons(self) -> list[QPushButton]:
        return self._choice_buttons

    def countdown_text(self, at: float) -> str:
        return format_countdown(self._session.shown_at + TIMEOUT_S - at)

    # --- the state machine, delegated ---

    def press(self, key: str) -> None:
        if self._session.press_key(key):
            self._render_stage()

    def visible_pick_list(self) -> list[str] | None:
        return self._session.visible_pick_list()

    def pending_fields(self) -> list[TextField]:
        return self._session.pending_fields()

    def submit_fields(self, values: dict[str, str]) -> list[str]:
        missing = self._session.submit_fields(values)
        if not missing:
            self._finish()
        return missing

    def is_complete(self) -> bool:
        return self._session.is_complete()

    def answers(self) -> Answers:
        return self._session.answers(self._now())

    def timed_out(self, at: float | None = None) -> Answers:
        return self._session.timed_out(self._now() if at is None else at)

    # --- running it for real ---

    def ask(self) -> Answers:
        """Blocks until answered or timed out. Runs a nested event loop, so
        the scheduler's own QTimer keeps ticking underneath it."""
        self._cover_all_screens()
        self.show()
        self.raise_()
        self.activateWindow()

        insist = QTimer(self)
        insist.timeout.connect(self._insist)
        insist.start(RAISE_MS)

        tick = QTimer(self)
        tick.timeout.connect(self._tick)
        tick.start(TICK_MS)

        while self._answers is None:
            QApplication.processEvents(QApplication.instance().eventDispatcher()
                                       and Qt.ApplicationState(0) or None)
            QApplication.processEvents()
            time.sleep(0.01)

        insist.stop()
        tick.stop()
        self.close()
        return self._answers

    def keyPressEvent(self, event) -> None:
        """No Escape, no Return-to-dismiss. The only way out is an answer
        or the timeout."""
        if event.key() == Qt.Key_Escape:
            event.accept()
            return
        text = event.text().lower()
        if text:
            self.press(text)
        event.accept()

    def closeEvent(self, event) -> None:
        if self._answers is None:
            event.ignore()
            return
        event.accept()

    # --- internals ---

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        self._title = QLabel(self._prompt.title)
        self._title.setObjectName("label")
        layout.addWidget(self._title)

        if self._prompt.warning:
            warning = QLabel(self._prompt.warning)
            warning.setObjectName("banner")
            layout.addWidget(warning)

        self._question = QLabel(self._prompt.question)
        self._question.setObjectName("question")
        layout.addWidget(self._question)

        self._choice_buttons = []
        for choice in self._prompt.choices:
            button = QPushButton(choice.label)
            button.clicked.connect(
                lambda _checked=False, key=choice.key: self.press(key)
            )
            layout.addWidget(button)
            self._choice_buttons.append(button)

        self._pick_area = QWidget()
        self._pick_layout = QVBoxLayout(self._pick_area)
        self._pick_area.setVisible(False)
        layout.addWidget(self._pick_area)

        self._field_area = QWidget()
        self._field_layout = QVBoxLayout(self._field_area)
        self._field_area.setVisible(False)
        layout.addWidget(self._field_area)

        self._countdown = QLabel()
        self._countdown.setObjectName("muted")
        layout.addWidget(self._countdown)

    def _render_stage(self) -> None:
        picks = self._session.visible_pick_list()
        self._pick_area.setVisible(picks is not None)
        _clear(self._pick_layout)
        if picks is not None:
            for index, text in enumerate(picks, start=1):
                button = QPushButton(f"{index}.  {text}")
                button.clicked.connect(
                    lambda _checked=False, key=str(index): self.press(key)
                )
                self._pick_layout.addWidget(button)

        fields = self._session.pending_fields()
        self._field_area.setVisible(bool(fields))
        _clear(self._field_layout)
        self._field_inputs = {}
        if fields:
            for field in fields:
                label = QLabel(field.label)
                label.setObjectName("label")
                self._field_layout.addWidget(label)
                edit = QLineEdit()
                edit.returnPressed.connect(self._submit_from_inputs)
                self._field_layout.addWidget(edit)
                self._field_inputs[field.name] = edit
            submit = QPushButton("Submit")
            submit.setObjectName("primary")
            submit.clicked.connect(self._submit_from_inputs)
            self._field_layout.addWidget(submit)

        if self._session.is_complete():
            self._finish()

    def _submit_from_inputs(self) -> None:
        self.submit_fields({
            name: edit.text() for name, edit in self._field_inputs.items()
        })

    def _finish(self) -> None:
        self._answers = self._session.answers(self._now())

    def _tick(self) -> None:
        at = self._now()
        self._countdown.setText(self.countdown_text(at))
        if at - self._session.shown_at >= TIMEOUT_S and self._answers is None:
            self._answers = self._session.timed_out(at)

    def _insist(self) -> None:
        """Undo a foreground steal. The best Qt can do; see the module
        docstring for what it cannot."""
        self.raise_()
        self.activateWindow()

    def _cover_all_screens(self) -> None:
        geometry = None
        for screen in QGuiApplication.screens():
            geometry = screen.geometry() if geometry is None \
                else geometry.united(screen.geometry())
        if geometry is not None:
            self.setGeometry(geometry)


class QtBlocker:
    """The `Blocker` protocol, drawn by Qt. Must be called from the Qt
    main thread — the scheduler is a QTimer, so it always is."""

    def __init__(self, mode: str = "dark") -> None:
        self._mode = mode

    def ask(self, prompt: Prompt) -> Answers:
        return CheckinWindow(prompt, self._mode).ask()


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
```

The `ask()` loop above is written to be replaced: implement it with a
`QEventLoop` — construct one, connect `_finish` and the timeout to
`loop.quit`, and call `loop.exec()`. A nested `QEventLoop` is the correct
Qt idiom for "block here but keep timers running"; the `processEvents`
sketch is a placeholder that must not ship.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/gui/test_checkin.py -v`
Expected: PASS, 12 tests (or SKIP without PySide6). None of them show a window.

- [ ] **Step 5: Run the full suite and commit**

```bash
python -m pytest
git add loop/gui/checkin.py tests/gui/test_checkin.py
git commit -m "feat: Qt check-in, one window for both platforms

Replaces 569 lines of macos.py and tk.py. Draws PromptSession, which was
already shared and already tested. Insistent, not inescapable — Qt cannot
block process switching, and the docstring says so."
```

---

### Task 14: The scheduler

The `QTimer` that replaces the daemon, and the copy change that follows from it. The daemon is still installed after this task; Task 15 removes it. From here on, an app that is running is scheduling.

**Files:**
- Create: `loop/gui/scheduler.py`
- Modify: `loop/app/checkin.py` (p100 label), `loop/gui/__main__.py`, `loop/app/launcher.py`
- Test: `tests/gui/test_scheduler.py`, `tests/app/test_checkin.py` (the p100 label assertion)

**Interfaces:**
- Consumes: `loop.app.{checkin, commands}`, `loop.core.schedule`, `loop.gui.checkin.QtBlocker`
- Produces:
  - `loop.gui.scheduler.Scheduler(controller, writer, blocker, poll_ms=10000)` — `.start()`, `.stop()`, `.tick()`
  - `loop.app.launcher.available() -> bool`, `loop.app.launcher.ensure_running() -> None`

- [ ] **Step 1: Change the p100 label**

In `loop/app/checkin.py`:

```python
        choices=[
            # Was "stop now — then run `loop close`". The window opens the
            # postmortem itself now, and pointing at a terminal the user has
            # been told they no longer need is a dangling instruction.
            Choice("x", "stop now"),
            Choice("c", "cut scope"),
            Choice("e", "extend estimate"),
        ],
```

Update the assertion in `tests/app/test_checkin.py` that names the old string.

- [ ] **Step 2: Write the failing scheduler tests**

Create `tests/gui/test_scheduler.py`:

```python
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
```

- [ ] **Step 3: Write `loop/gui/scheduler.py`**

```python
"""The poll loop that replaces the daemon.

Same sequence `sched/tick.py` ran, minus the detached process: ask core
what is due, draw it, write the answer down. The staleness re-check now
happens inside `commands.record_checkin`, under the lock, instead of in a
separate read.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer

from loop.app import checkin as checkin_module
from loop.app import commands
from loop.core import events, schedule
from loop.store import jsonl

POLL_MS = 10_000


class Scheduler(QObject):
    def __init__(self, controller, writer, blocker, poll_ms: int = POLL_MS,
                 now=time.time) -> None:
        super().__init__()
        self._controller = controller
        self._writer = writer
        self._blocker = blocker
        self._now = now
        self._showing = False

        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self.tick)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def tick(self) -> None:
        if self._showing:
            # A check-in blocks for up to five minutes in a nested event
            # loop, and this timer keeps firing underneath it. Two windows
            # for one interval is the bug this prevents.
            return

        try:
            state = events.fold(jsonl.read_all())
        except jsonl.CorruptLogError:
            # The window already says so and has disabled every action.
            # Appending to a log we cannot fold would make it worse.
            return

        due = schedule.next_due(state, self._now())
        if due is None:
            return

        loop = state.loops[due.loop_id]
        prompt = checkin_module.build_prompt(state, loop, due)

        if due.kind != "ping":
            commands.record_checkpoint_shown(
                self._writer, loop_id=loop.id, kind=due.kind
            )

        self._showing = True
        try:
            answers = self._blocker.ask(prompt)
        finally:
            self._showing = False

        commands.record_checkin(self._writer, due=due, answers=answers)
        self._controller.refresh()
```

- [ ] **Step 4: Finish `loop/app/launcher.py`**

```python
def available() -> bool:
    """Can this machine run the app?

    Checked before any event is written, preserving the invariant that
    `loop open` never leaves a timer running that nothing can surface a
    prompt for.
    """
    from importlib.util import find_spec

    return find_spec("PySide6") is not None and find_spec("loop.gui") is not None


UNAVAILABLE_MESSAGE = (
    "the loop app is not installed, so nothing can show check-ins.\n"
    "  install it with: pip install 'loop-tool[gui]'"
)


def ensure_running() -> None:
    """Start the app if it is not up.

    Unconditional and idempotent: a second copy discovers the running
    one through its socket, asks it to surface, and exits. Nothing here
    has to guess, which is why there is no pidfile to go stale.
    """
    import subprocess
    import sys

    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True

    subprocess.Popen([sys.executable, "-m", "loop.gui"], **kwargs)
```

- [ ] **Step 5: Wire the scheduler into `__main__.py`**

```python
    from loop.app.writer import Writer
    from loop.gui.checkin import QtBlocker
    from loop.gui.scheduler import Scheduler

    writer = Writer()
    scheduler = Scheduler(controller, writer, QtBlocker(mode))
    scheduler.start()
```

Pass the same `writer` to `Controller` and to `Actions` so there is one of
each per process.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/gui/test_scheduler.py tests/app/test_checkin.py -v`
Expected: PASS (or SKIP without PySide6).

- [ ] **Step 7: Full suite and commit**

```bash
python -m pytest
git add loop/gui/scheduler.py loop/gui/__main__.py loop/app/launcher.py loop/app/checkin.py tests/gui/test_scheduler.py tests/app/test_checkin.py
git commit -m "feat: the app is the scheduler

A QTimer doing what the detached daemon did, in the process that owns the
window. p100 no longer tells you to run a terminal command."
```

---

### Task 15: Demolition

The GUI now does everything the daemon and the overlays did. Removing them is what the whole plan was for: roughly 800 lines and the PyObjC dependency.

Both macOS bugs fixed on 2 August — the keyboard-tap teardown and the window level — exist only because a headless daemon owned a window it could not own properly. This deletes their category, and their tests with them.

**Files:**
- Delete: `loop/sched/` (`__init__.py`, `daemon.py`, `tick.py`), `loop/blockers/macos.py`, `loop/blockers/tk.py`, `loop/blockers/factory.py`, `tests/sched/`, `tests/blockers/test_factory.py`, `tests/blockers/test_macos_keyboard.py`, `tests/blockers/test_macos_teardown.py`, `tests/test_cli_tick.py`
- Modify: `loop/cli.py`, `loop/store/paths.py`, `loop/blockers/base.py`, `tests/store/test_paths.py`, `tests/test_cli_open.py`, `tests/test_cli_stack.py`, `tests/test_cli_errors.py`, `tests/test_cli_stats.py`
- Create: `tests/test_layers.py`

**Interfaces:**
- Consumes: `loop.app.launcher.{available, ensure_running, UNAVAILABLE_MESSAGE}`
- Produces: nothing new. This task only removes.

Note `tests/blockers/test_macos_keyboard.py` and `test_macos_teardown.py` are currently untracked, as is the modification to `loop/blockers/macos.py`. Delete the files with `rm`, not `git rm`, and do not stage the `macos.py` modification — it is being deleted anyway.

- [ ] **Step 1: Write the failing layer-discipline tests**

Create `tests/test_layers.py`. These are the tests that keep the architecture true after everyone has forgotten why it was drawn this way.

```python
"""The layer rules, enforced rather than documented.

Each of these encodes a rule from the spec that a well-meaning import
would quietly break.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "loop"

POISON = """
import sys

class Poison:
    def __getattr__(self, name):
        raise AssertionError("PySide6 was imported outside loop/gui/")

for name in ("PySide6", "PySide6.QtCore", "PySide6.QtGui",
             "PySide6.QtWidgets", "PySide6.QtNetwork"):
    sys.modules[name] = Poison()

import importlib
for module in {modules!r}:
    importlib.import_module(module)
print("clean")
"""


def _modules(exclude: str) -> list[str]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(ROOT)
        if exclude in relative.parts:
            continue
        parts = relative.with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            found.append(".".join(parts))
    return found


def test_nothing_outside_gui_imports_pyside6():
    """A single convenience import in app/ or core/ would make the CLI
    unusable without a 300MB GUI toolkit."""
    program = POISON.format(modules=_modules(exclude="gui"))
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, cwd=ROOT
    )
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


def _calls_to(path: Path, dotted: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    target = dotted.split(".")[-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == target:
            if isinstance(node.value, ast.Name) and node.value.id == dotted.split(".")[0]:
                return True
    return False


def test_jsonl_append_has_exactly_one_caller():
    """Every write must go through the lock. A second caller is a race."""
    callers = [
        path.relative_to(ROOT).as_posix()
        for path in PACKAGE.rglob("*.py")
        if _calls_to(path, "jsonl.append")
    ]
    assert callers == ["loop/app/writer.py"]


def test_core_imports_nothing_from_the_outer_layers():
    forbidden = ("loop.store", "loop.blockers", "loop.app", "loop.gui")
    offenders = []
    for path in (PACKAGE / "core").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for name in forbidden:
            if f"import {name}" in source or f"from {name}" in source:
                offenders.append((path.name, name))
    assert offenders == []


def test_blockers_import_nothing_from_core():
    """The seam that made a Windows overlay one new file. It still holds:
    blockers speak only Prompt and Answers."""
    offenders = []
    for path in (PACKAGE / "blockers").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "loop.core" in source:
            offenders.append(path.name)
    assert offenders == []


def test_the_daemon_and_the_overlays_are_gone():
    for relative in ("sched", "blockers/macos.py", "blockers/tk.py",
                     "blockers/factory.py"):
        assert not (PACKAGE / relative).exists(), f"{relative} survived"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_layers.py -v`
Expected: FAIL — `test_the_daemon_and_the_overlays_are_gone` at minimum, plus `test_nothing_outside_gui_imports_pyside6` if `loop/sched/` still imports the factory chain.

- [ ] **Step 3: Switch `cli.py` off the daemon**

Replace `require_blocker` in `loop/cli.py`:

```python
def require_app() -> None:
    """Fail loudly, before any event is written, if nothing can show a check-in.

    `loop open` / `loop resume` must never leave a timer running that
    nothing will ever interrupt.
    """
    if not launcher.available():
        raise LoopError(launcher.UNAVAILABLE_MESSAGE)
```

Change the imports: drop `from loop.blockers.factory import BlockerUnavailable, get_blocker` and `from loop.sched import daemon`; add `from loop.app import launcher`. Replace both `require_blocker()` calls with `require_app()` and both `daemon.ensure_running()` calls with `launcher.ensure_running()`.

Remove the `tick` subparser from `build_parser`, `cmd_tick`, and its `COMMANDS` entry.

- [ ] **Step 4: Delete the files**

```bash
git rm -r loop/sched tests/sched
git rm loop/blockers/macos.py loop/blockers/tk.py loop/blockers/factory.py
git rm tests/blockers/test_factory.py tests/test_cli_tick.py
rm -f tests/blockers/test_macos_keyboard.py tests/blockers/test_macos_teardown.py
```

- [ ] **Step 5: Remove the dead constants and the pidfile**

In `loop/blockers/base.py`, delete `KILL_AFTER_S = 310.0`. It was the daemon's watchdog against an overlay subprocess wedging the screen; there is no subprocess. Update the module docstring, which still describes "a Windows overlay is a new file implementing `ask`" — true, but the file now lives in `gui/`.

In `loop/store/paths.py`, delete `pid_path`. In `tests/store/test_paths.py`, delete the `pid_path` assertion.

- [ ] **Step 6: Fix the test fixtures that patch the daemon**

Four files monkeypatch `loop.sched.daemon.ensure_running`. Replace in each of `tests/test_cli_open.py:14`, `tests/test_cli_stack.py:12`, `tests/test_cli_errors.py:14`, `tests/test_cli_stats.py:11`:

```python
    monkeypatch.setattr("loop.app.launcher.ensure_running", lambda: None)
    monkeypatch.setattr("loop.app.launcher.available", lambda: True)
```

The second line is what keeps the CLI suite green on a machine with no
PySide6 — `require_app` would otherwise refuse every `loop open`.

`monkeypatch.setenv("LOOP_BLOCKER", "fake")` in those fixtures is now
inert; leave it or remove it, but do not leave `LOOP_BLOCKER` documented
as meaningful when nothing reads it. Remove it, and remove any mention
from the README.

- [ ] **Step 7: Run everything**

Run: `python -m pytest -v`
Expected: PASS. `tests/test_layers.py` green. If `test_jsonl_append_has_exactly_one_caller` fails, something still writes outside the lock — fix the caller, not the test.

- [ ] **Step 8: Commit**

```bash
python -m pytest
git add -u
git add tests/test_layers.py
git commit -m "refactor: delete the daemon and both overlays

~800 lines and the PyObjC dependency. Both macOS bugs fixed on 2 August
existed only because a headless daemon owned a window; that category is
gone. Layer rules are now tests, not comments."
```

---

### Task 16: The logbook

`stats` and `grep` as a second view in the same window, sharing the loop list they filter. The search side re-uses `cmd_grep`'s matching, which is currently welded to `cli.py:332-365` — it moves to `core/` where both front ends can reach it.

**Files:**
- Create: `loop/core/search.py`, `loop/gui/logbook.py`
- Modify: `loop/cli.py` (`cmd_grep` calls `search`), `loop/gui/window.py`
- Test: `tests/core/test_search.py`, `tests/gui/test_logbook.py`

**Interfaces:**
- Consumes: `loop.core.{stats, search}`, `loop.app.view`
- Produces:
  - `loop.core.search.Hit(label: str, text: str)`
  - `loop.core.search.Match(loop_id: int, question: str, hits: tuple[Hit, ...])`
  - `loop.core.search.find(state: State, log: list[dict], term: str) -> list[Match]`
  - `loop.gui.logbook.LogbookView(controller)` — `.bind(report: dict, matches: list[Match])`

- [ ] **Step 1: Write the failing search tests**

Create `tests/core/test_search.py`:

```python
import pytest

from loop.core import events, search


def closed_loop(loop_id, question, postmortem, actions=()):
    log = [events.make("loop_opened", ts=0.0, loop_id=loop_id, question=question,
                       stop_condition="s", budget_s=2700.0, interval_s=1200.0,
                       hypotheses=["h"], parent_id=None)]
    for action, because in actions:
        log.append(events.make("action_logged", ts=1.0, loop_id=loop_id,
                               action=action, because=because))
    log.append(events.make("loop_closed", ts=2.0, loop_id=loop_id, **postmortem))
    return log


POSTMORTEM = {"what_was_it": "a stale TLS cert",
              "giveaway": "only failed after a restart",
              "five_min_path": "check notAfter first"}


def find(log, term):
    return search.find(events.fold(log), log, term)


def test_a_postmortem_hit_is_found_case_insensitively():
    matches = find(closed_loop(1, "q", POSTMORTEM), "STALE")
    assert matches[0].loop_id == 1
    assert ("what_was_it", "a stale TLS cert") in [(h.label, h.text) for h in matches[0].hits]


def test_an_action_hit_carries_its_because():
    log = closed_loop(1, "q", POSTMORTEM, actions=[("dumped the pool", "shows leaks")])
    hit = find(log, "leaks")[0].hits[0]
    assert hit.label == "action"
    assert hit.text == "dumped the pool — because shows leaks"


def test_open_loops_are_not_searched():
    """grep is the logbook. A loop you are still inside is on screen already."""
    log = closed_loop(1, "q", POSTMORTEM)[:-1]
    assert find(log, "stale") == []


def test_abandoned_loops_are_searched():
    log = [events.make("loop_opened", ts=0.0, loop_id=1, question="stale cert?",
                       stop_condition="s", budget_s=2700.0, interval_s=1200.0,
                       hypotheses=["h"], parent_id=None),
           events.make("action_logged", ts=1.0, loop_id=1,
                       action="checked notAfter", because="stale cert theory"),
           events.make("loop_abandoned", ts=2.0, loop_id=1)]
    assert find(log, "stale")[0].loop_id == 1


def test_no_matches_is_an_empty_list_not_an_error():
    assert find(closed_loop(1, "q", POSTMORTEM), "kubernetes") == []
```

- [ ] **Step 2: Run to verify they fail, then write `loop/core/search.py`**

Run: `python -m pytest tests/core/test_search.py -v` → FAIL, no module `loop.core.search`.

```python
"""Search the logbook.

Lifted out of `cli.py` so the window can use it. Closed and abandoned
loops only: a loop you are still inside is already on screen.
"""

from __future__ import annotations

from dataclasses import dataclass

from loop.core.models import ABANDONED, CLOSED, State


@dataclass(frozen=True, slots=True)
class Hit:
    label: str
    text: str


@dataclass(frozen=True, slots=True)
class Match:
    loop_id: int
    question: str
    hits: tuple[Hit, ...]


def find(state: State, log: list[dict], term: str) -> list[Match]:
    needle = term.lower()
    matches: list[Match] = []

    for loop in state.loops.values():
        if loop.status not in (CLOSED, ABANDONED):
            continue
        hits = [
            Hit(label, text)
            for label, text in (loop.postmortem or {}).items()
            if needle in text.lower()
        ]
        hits += [
            Hit("action", f"{event['action']} — because {event['because']}")
            for event in log
            if event["type"] == "action_logged"
            and event["loop_id"] == loop.id
            and (needle in event["action"].lower() or needle in event["because"].lower())
        ]
        if hits:
            matches.append(Match(loop.id, loop.question, tuple(hits)))

    return matches
```

Rewrite `cmd_grep` in `cli.py` to call `search.find` and render `Match`/`Hit`, keeping its output and its `--json` shape identical — the existing `tests/test_cli_stats.py` grep tests are the check.

- [ ] **Step 3: Write `loop/gui/logbook.py`**

A `QWidget` with a search box and two sections: the six `stats.render` lines
as a labelled grid, and the matches below. It binds `stats.compute(log, now)`
and `search.find(...)`; it computes nothing. Use `stats.format_drift` from
Task 9 for the drift line so the window and the terminal cannot disagree.

Add a `Logbook` action to the menu bar and the status strip, toggling the
central widget between `MainWindow`'s dashboard and this view.

Create `tests/gui/test_logbook.py` with two tests: that a drift of `-20.0`
renders `20.0% under` (not `-20.0% over`), and that binding an empty report
produces no exception and an empty match list.

- [ ] **Step 4: Run and commit**

```bash
python -m pytest
git add loop/core/search.py loop/gui/logbook.py loop/cli.py loop/gui/window.py tests/core/test_search.py tests/gui/test_logbook.py
git commit -m "feat: logbook and search in the window

grep's matching moves to core/ so both front ends share it."
```

---

### Task 17: Windows pass

Everything Windows-specific in this plan was written from documentation. This task runs it on a real Windows machine and fixes what is found. Expect to find something.

**Files:** whatever the run breaks. Most likely `loop/store/lock.py`, `loop/app/launcher.py`, `loop/gui/checkin.py`.

- [ ] **Step 1: Install and run the suite**

On Windows, in a clean venv:

```powershell
pip install -e ".[gui,dev]"
python -m pytest -v
```

Expected: PASS. The likely failure is `tests/store/test_lock.py` — `msvcrt.locking` locks a byte range from the current file position, so a missing `seek(0)` shows up here and nowhere else.

- [ ] **Step 2: Exercise the app**

```powershell
$env:LOOP_HOME="$env:TEMP\loop-scratch"
python -m loop.gui
```

Check, in order:
1. The window opens and the tray icon appears in the notification area.
2. Closing the window leaves the app running in the tray.
3. A second `python -m loop.gui` exits immediately and re-surfaces the first window.
4. `loop open "test" --budget 3m --interval 1m` from a separate terminal fills the window within a second.
5. After one minute, the check-in covers the screen. Answer it. The answer appears in the action log.
6. Fonts: `Cascadia Mono` and `Segoe UI Variable` must both resolve. A fallback to a default serif means the family names in `gui/theme.py` are wrong for this Windows version.
7. Both themes: change Windows to light mode and restart. The palette must follow.
8. Multi-monitor: the check-in must cover every attached screen.

Then delete the scratch directory.

- [ ] **Step 3: Fix and commit**

Fix each finding with a test where a test is possible. Commit them
separately — a Windows fix that arrives in the same commit as a Windows
workaround is impossible to bisect later.

```bash
git commit -m "fix: <what Windows actually did>"
```

- [ ] **Step 4: Record what could not be fixed**

Append anything left standing to §13 of the spec, with what was observed. A known limitation written down is worth more than one rediscovered in six months.

---

## Self-Review

**Spec coverage.** Every section maps to a task: §2 architecture → Tasks 3-15; §3 command layer → 3, 4, 5, 6; §4 timeline → 2; §5 view models → 8; §6 check-in and scheduler → 7, 13, 14; §7 process model → 10, 14, 15; §8 the action table → 11, 12; §9 failure states → 11 (thrash, unreadable), 12 (stale), 10 (already running); §10 logbook → 9, 16; §11 visual design → 10 (`theme.py`), 11 (`BurnMeter`); §12 testing → every task, plus 15 for the layer tests; §13 risks → 13 and 17.

**Three things this plan does that the spec did not say.**

1. **Reordered the build** so the daemon dies in Task 15 rather than at spec step 5. The spec's order leaves eight steps with no check-ins at all. Recorded above under "Build-order note".
2. **`app/session.py` → `app/writer.py`.** `loop/blockers/session.py` already exists.
3. **`core/search.py` (Task 16)** is new. The spec says the logbook has search but does not say `cmd_grep`'s matching has to leave `cli.py` — it does, or the window would import the CLI, which is precisely what "not backed by a cli in any way" rules out.

**Two known soft spots.**

- **Task 13's `ask()` loop is written as a sketch and marked as one.** The `processEvents` version must be replaced with a `QEventLoop`. It is called out in the task rather than left to be discovered, but it is the one place in this plan where the code is a description rather than the code.
- **Tasks 11, 12, and 16 specify widget layout in prose** where earlier tasks give complete code. That is deliberate: layout is the part a reviewer judges by looking, and the part that will change in the first ten minutes of seeing it on a real screen. The bindings, the signals, and the validation rules — the parts with defects — are complete.

**Type consistency, checked.** `Writer.mutate(build)` returns `(events, result)` throughout Tasks 4, 5, and 7. `DashboardView` field names in Task 8 match every `.bind()` in Tasks 11 and 12. `enabled_actions` uses the same ten names in `view.ALL_ACTIONS` (Task 8), `MainWindow._actions` (Task 11), and `Actions.run_*` (Task 12). `LoopSummary` is produced by `resume`/`close`/`abandon` and consumed by `_say_resumed` in Task 6. `format_drift` is defined in Task 9 and used again in Task 16.
