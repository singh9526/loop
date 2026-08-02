# `loop` Thrash Detector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a CLI tool that gates debugging sessions with blocking full-screen check-ins and records whether the hypothesis space is actually shrinking.

**Architecture:** An append-only JSONL event log is the single source of truth; all state is `fold(events)`. A pure `core/` package (zero I/O, zero OS imports) computes elapsed time, what check-in is due, thrash, and stats. A detached daemon polls `core/` every 10 seconds and renders due prompts through a swappable `Blocker` — tkinter everywhere, PyObjC on macOS for a genuine screen block.

**Tech Stack:** Python 3.11+, standard library only, `pytest` for tests, optional `pyobjc-framework-Cocoa` + `pyobjc-framework-Quartz` on macOS.

**Spec:** `docs/superpowers/specs/2026-08-02-loop-thrash-detector-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **Python 3.11+.** Use `X | None` unions, `@dataclass(slots=True)` where convenient.
- **Standard library only.** The sole exception is PyObjC, declared as the optional extra `[macos]`. `pytest` is a dev dependency. No other third-party runtime imports, ever.
- **`core/` imports nothing from `store/`, `blockers/`, or `sched/`, and no OS-specific module.** It may import `dataclasses`, `typing`, `math`, `statistics`. It may not import `os`, `sys`, `subprocess`, `pathlib`, or any GUI toolkit.
- **`blockers/` imports nothing from `core/`.** It speaks only `Prompt` and `Answers` from `blockers/base.py`.
- **`cli.py` and `sched/` are the only modules that wire the three layers together.**
- **All internal durations are seconds, as `float`.** Never store minutes. Never store formatted strings.
- **All timestamps are Unix seconds, as `float`**, from `time.time()`.
- **Scheduling decisions read active elapsed, never a raw wall clock.** Only `core/schedule.py` and `core/models.py` do elapsed arithmetic.
- **`LOOP_HOME` overrides the data directory.** Every test sets it to a `tmp_path`.
- **`LOOP_BLOCKER` overrides blocker selection** — values `tk`, `macos`, `fake`.
- **Overlay timeout is exactly `300.0` seconds. The independent kill timer is exactly `310.0` seconds.**
- **`MAX_STACK_DEPTH = 5` (refuse), `WARN_STACK_DEPTH = 3` (warn).**
- **`CHECKPOINT_RETRY_S = 600.0`, `COLLISION_WINDOW_S = 180.0`.**
- **Default budget 45 minutes, default interval 20 minutes.**
- Commit after every task. Conventional Commits, imperative subject, ≤50 chars.

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, `loop` console script, `[macos]` extra, pytest config |
| `loop/core/timefmt.py` | `parse_duration` / `format_duration`. Pure string↔seconds |
| `loop/core/models.py` | `Hypothesis`, `Loop`, `State` dataclasses + `elapsed_from_intervals` |
| `loop/core/events.py` | Event field table, `make()` constructor, `fold()` |
| `loop/core/schedule.py` | Active clock, `Due`, `next_due()`, collision + retry rules |
| `loop/core/thrash.py` | `detect()` → `Thrash` |
| `loop/core/stats.py` | `compute()` → dict, `render()` → str |
| `loop/store/paths.py` | Per-platform data dir, `events_path()`, `pid_path()` |
| `loop/store/jsonl.py` | `append()`, `read_all()`, truncated-tail tolerance |
| `loop/blockers/base.py` | `Choice`, `TextField`, `Prompt`, `Answers`, `Blocker` protocol, countdown helpers |
| `loop/blockers/session.py` | The choice → pick → fields state machine, shared by both overlays |
| `loop/blockers/fake.py` | Scripted answers for tests and `LOOP_BLOCKER=fake` |
| `loop/blockers/tk.py` | Portable tkinter overlay |
| `loop/blockers/macos.py` | PyObjC hard block |
| `loop/blockers/factory.py` | Platform + env → blocker instance |
| `loop/sched/tick.py` | `Due` → `Prompt`, run blocker, `Answers` → events |
| `loop/prompts.py` | Terminal input helpers, all routed through `builtins.input` |
| `loop/render.py` | Human-readable views of state for `status` and `ls` |
| `loop/cli.py` | argparse dispatch and the command functions |
| `loop/sched/daemon.py` | Detached spawn, pidfile heartbeat, 10-second poll loop |
| `docs/manual-smoke-checklist.md` | The overlay behaviours that cannot be automated |

Four files are not named in the spec's §2 tree, each for a reason:

- `loop/core/timefmt.py` — `--budget 45m` parsing and `34m / 45m` rendering are needed by the CLI, the tick, and stats. Three copies would be three chances to disagree.
- `loop/blockers/session.py` — the interaction is a small state machine identical in both overlays. Extracting it makes the fiddly part testable without a display and reduces each GUI file to drawing.
- `loop/prompts.py` and `loop/render.py` — splitting these out of `cli.py` keeps that file to dispatch and command bodies. Rolled in, it would be the largest file in the project by a wide margin and the hardest to edit reliably.

---

## Task 1: Project scaffold and the event store

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `loop/__init__.py`, `loop/core/__init__.py`, `loop/store/__init__.py`, `loop/blockers/__init__.py`, `loop/sched/__init__.py`
- Create: `loop/store/paths.py`, `loop/store/jsonl.py`
- Test: `tests/store/test_paths.py`, `tests/store/test_jsonl.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `paths.loop_home() -> Path`, `paths.events_path() -> Path`, `paths.pid_path() -> Path`, `jsonl.append(event: dict, path: Path | None = None) -> None`, `jsonl.read_all(path: Path | None = None) -> list[dict]`.

- [ ] **Step 1: Create the package skeleton**

```bash
mkdir -p loop/core loop/store loop/blockers loop/sched tests/store tests/core tests/sched docs
touch loop/__init__.py loop/core/__init__.py loop/store/__init__.py loop/blockers/__init__.py loop/sched/__init__.py
touch tests/__init__.py tests/store/__init__.py tests/core/__init__.py tests/sched/__init__.py
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "loop-tool"
version = "0.1.0"
description = "A thrash detector for debugging"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
macos = ["pyobjc-framework-Cocoa>=10.0", "pyobjc-framework-Quartz>=10.0"]
dev = ["pytest>=8.0"]

[project.scripts]
loop = "loop.cli:main"

[tool.setuptools.packages.find]
include = ["loop*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 3: Write `.gitignore`**

```gitignore
__pycache__/
*.egg-info/
.pytest_cache/
build/
dist/
.venv/
```

- [ ] **Step 4: Write the failing test for `paths.py`**

`tests/store/test_paths.py`:

```python
import importlib

from loop.store import paths


def test_loop_home_honours_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path / "custom"))
    assert paths.loop_home() == tmp_path / "custom"


def test_loop_home_is_created_on_access(tmp_path, monkeypatch):
    target = tmp_path / "made-on-demand"
    monkeypatch.setenv("LOOP_HOME", str(target))
    paths.loop_home()
    assert target.is_dir()


def test_darwin_default_is_dot_loop(tmp_path, monkeypatch):
    monkeypatch.delenv("LOOP_HOME", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.loop_home() == tmp_path / ".loop"


def test_linux_default_respects_xdg(tmp_path, monkeypatch):
    monkeypatch.delenv("LOOP_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(paths.sys, "platform", "linux")
    assert paths.loop_home() == tmp_path / "xdg" / "loop"


def test_events_and_pid_live_under_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
    assert paths.events_path() == tmp_path / "events.jsonl"
    assert paths.pid_path() == tmp_path / "daemon.pid"
```

- [ ] **Step 5: Run it and confirm it fails**

Run: `python -m pytest tests/store/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.store.paths'`

- [ ] **Step 6: Implement `loop/store/paths.py`**

```python
"""Resolve the data directory for each platform.

`LOOP_HOME` overrides everything. Tests rely on that override, so it is
checked before any platform logic.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def loop_home() -> Path:
    """Return the data directory, creating it if it does not exist."""
    override = os.environ.get("LOOP_HOME")
    if override:
        home = Path(override)
    elif sys.platform == "darwin":
        home = Path.home() / ".loop"
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA")
        home = (Path(base) if base else Path.home() / "AppData" / "Roaming") / "loop"
    else:
        base = os.environ.get("XDG_DATA_HOME")
        home = (Path(base) if base else Path.home() / ".local" / "share") / "loop"

    home.mkdir(parents=True, exist_ok=True)
    return home


def events_path() -> Path:
    return loop_home() / "events.jsonl"


def pid_path() -> Path:
    return loop_home() / "daemon.pid"
```

- [ ] **Step 7: Run the tests and confirm they pass**

Run: `python -m pytest tests/store/test_paths.py -v`
Expected: PASS, 5 tests

- [ ] **Step 8: Write the failing test for `jsonl.py`**

`tests/store/test_jsonl.py`:

```python
import pytest

from loop.store import jsonl


def test_append_then_read_round_trips(tmp_path):
    path = tmp_path / "events.jsonl"
    jsonl.append({"type": "a", "ts": 1.0}, path)
    jsonl.append({"type": "b", "ts": 2.0}, path)
    assert jsonl.read_all(path) == [
        {"type": "a", "ts": 1.0},
        {"type": "b", "ts": 2.0},
    ]


def test_read_all_on_missing_file_is_empty(tmp_path):
    assert jsonl.read_all(tmp_path / "nope.jsonl") == []


def test_truncated_final_line_is_tolerated(tmp_path):
    path = tmp_path / "events.jsonl"
    jsonl.append({"type": "a", "ts": 1.0}, path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type": "b", "ts":')
    assert jsonl.read_all(path) == [{"type": "a", "ts": 1.0}]


def test_corrupt_interior_line_raises(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"type": "a"}\nNOT JSON\n{"type": "c"}\n', encoding="utf-8")
    with pytest.raises(jsonl.CorruptLogError) as excinfo:
        jsonl.read_all(path)
    assert "line 2" in str(excinfo.value)


def test_blank_lines_are_skipped(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"type": "a"}\n\n{"type": "c"}\n', encoding="utf-8")
    assert jsonl.read_all(path) == [{"type": "a"}, {"type": "c"}]
```

- [ ] **Step 9: Run it and confirm it fails**

Run: `python -m pytest tests/store/test_jsonl.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.store.jsonl'`

- [ ] **Step 10: Implement `loop/store/jsonl.py`**

A truncated *final* line is what a crash mid-write produces, so it is tolerated. A corrupt *interior* line means real damage and must be loud — silently skipping it would drop history the tool exists to preserve.

```python
"""The append-only event log.

One JSON object per line. Appends are a single `write` of a line, which is
atomic on POSIX for payloads under PIPE_BUF, so a crash can damage at most
the final line.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from loop.store import paths


class CorruptLogError(Exception):
    """An interior line of the log could not be parsed."""


def append(event: dict, path: Path | None = None) -> None:
    target = path if path is not None else paths.events_path()
    line = json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def read_all(path: Path | None = None) -> list[dict]:
    target = path if path is not None else paths.events_path()
    if not target.exists():
        return []

    raw = target.read_text(encoding="utf-8").splitlines()
    events: list[dict] = []
    for index, line in enumerate(raw, start=1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            if index == len(raw):
                break  # truncated tail from a crash mid-append
            raise CorruptLogError(f"{target}: line {index} is not valid JSON") from exc
    return events
```

- [ ] **Step 11: Run the tests and confirm they pass**

Run: `python -m pytest tests/store -v`
Expected: PASS, 10 tests

- [ ] **Step 12: Commit**

```bash
git add pyproject.toml .gitignore loop tests
git commit -m "feat: add package scaffold and event store"
```

---

## Task 2: Duration parsing and formatting

**Files:**
- Create: `loop/core/timefmt.py`
- Test: `tests/core/test_timefmt.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `parse_duration(text: str) -> float` (seconds; raises `ValueError`), `format_duration(seconds: float) -> str`.

- [ ] **Step 1: Write the failing test**

`tests/core/test_timefmt.py`:

```python
import pytest

from loop.core.timefmt import format_duration, parse_duration


@pytest.mark.parametrize(
    "text,expected",
    [
        ("45m", 2700.0),
        ("2h", 7200.0),
        ("1h30m", 5400.0),
        ("90s", 90.0),
        ("45", 2700.0),          # bare number means minutes
        (" 45m ", 2700.0),
        ("1H30M", 5400.0),
    ],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "-5m", "0", "5x", "m30"])
def test_parse_duration_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_duration(text)


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0.0, "0m"),
        (59.0, "0m"),
        (60.0, "1m"),
        (2700.0, "45m"),
        (3600.0, "1h"),
        (5400.0, "1h30m"),
        (90000.0, "1d1h"),
    ],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python -m pytest tests/core/test_timefmt.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.core.timefmt'`

- [ ] **Step 3: Implement `loop/core/timefmt.py`**

```python
"""Duration strings to seconds and back.

A bare number means minutes, because the tool's unit of thought is minutes
and `--budget 45` should not silently mean 45 seconds.
"""

from __future__ import annotations

import re

_UNITS = {"h": 3600.0, "m": 60.0, "s": 1.0}
_PATTERN = re.compile(r"(\d+)\s*([hms])", re.IGNORECASE)


def parse_duration(text: str) -> float:
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("empty duration")

    if cleaned.isdigit():
        seconds = float(cleaned) * 60.0
    else:
        matches = _PATTERN.findall(cleaned)
        if not matches or _PATTERN.sub("", cleaned).strip():
            raise ValueError(f"cannot parse duration: {text!r}")
        seconds = sum(float(value) * _UNITS[unit.lower()] for value, unit in matches)

    if seconds <= 0:
        raise ValueError(f"duration must be positive: {text!r}")
    return seconds


def format_duration(seconds: float) -> str:
    total_minutes = int(seconds // 60)
    days, rest = divmod(total_minutes, 1440)
    hours, minutes = divmod(rest, 60)

    if days:
        return f"{days}d{hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h{minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/core/test_timefmt.py -v`
Expected: PASS, 20 tests

- [ ] **Step 5: Commit**

```bash
git add loop/core/timefmt.py tests/core/test_timefmt.py
git commit -m "feat: add duration parsing and formatting"
```

---

## Task 3: Domain models and the active clock

**Files:**
- Create: `loop/core/models.py`
- Test: `tests/core/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Hypothesis`, `Loop`, `State` dataclasses; `elapsed_from_intervals(intervals: list[list[float | None]], at: float) -> float`; constants `MAX_STACK_DEPTH`, `WARN_STACK_DEPTH`.

`Loop.intervals` is a list of `[start, end]` pairs recording the wall-clock spans during which the loop was active. The final pair has `end is None` while the loop is running. This list is the *only* record of elapsed time — there is no stored counter to drift.

- [ ] **Step 1: Write the failing test**

`tests/core/test_models.py`:

```python
from loop.core.models import Hypothesis, Loop, State, elapsed_from_intervals


def test_elapsed_of_a_closed_span():
    assert elapsed_from_intervals([[100.0, 160.0]], at=500.0) == 60.0


def test_elapsed_of_an_open_span_runs_to_now():
    assert elapsed_from_intervals([[100.0, None]], at=250.0) == 150.0


def test_paused_time_never_accrues():
    # active 100-160, paused 160-1000, active again 1000-1030
    intervals = [[100.0, 160.0], [1000.0, None]]
    assert elapsed_from_intervals(intervals, at=1030.0) == 90.0


def test_many_pause_resume_cycles_sum_correctly():
    intervals = [[0.0, 10.0], [100.0, 130.0], [500.0, 505.0], [900.0, None]]
    assert elapsed_from_intervals(intervals, at=920.0) == 10.0 + 30.0 + 5.0 + 20.0


def test_no_intervals_is_zero():
    assert elapsed_from_intervals([], at=99.0) == 0.0


def test_at_before_open_span_start_does_not_go_negative():
    assert elapsed_from_intervals([[100.0, None]], at=50.0) == 0.0


def test_loop_live_hypotheses_excludes_killed():
    loop = Loop(
        id=1,
        question="q",
        stop_condition="s",
        budget_s=2700.0,
        interval_s=1200.0,
        parent_id=None,
        opened_at=0.0,
        original_budget_s=2700.0,
        hypotheses=[
            Hypothesis(id=1, text="cert expiry", alive=False),
            Hypothesis(id=2, text="env var missing", alive=True),
        ],
    )
    assert [h.id for h in loop.live_hypotheses()] == [2]


def test_state_active_loop_is_none_when_nothing_active():
    assert State(loops={}, active_id=None).active_loop() is None
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python -m pytest tests/core/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.core.models'`

- [ ] **Step 3: Implement `loop/core/models.py`**

```python
"""Domain objects. Pure data plus the arithmetic that belongs to it.

No I/O, no OS imports. `elapsed_from_intervals` lives here rather than in
`schedule.py` because `events.fold` also needs it, and importing schedule
from events would be circular.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_STACK_DEPTH = 5
WARN_STACK_DEPTH = 3

ACTIVE = "active"
PAUSED = "paused"
CLOSED = "closed"
ABANDONED = "abandoned"


def elapsed_from_intervals(intervals: list[list[float | None]], at: float) -> float:
    """Sum the active spans up to `at`. Paused gaps contribute nothing."""
    total = 0.0
    for start, end in intervals:
        stop = at if end is None else end
        if stop > start:
            total += stop - start
    return total


@dataclass(slots=True)
class Hypothesis:
    id: int
    text: str
    alive: bool = True


@dataclass(slots=True)
class Loop:
    id: int
    question: str
    stop_condition: str
    budget_s: float
    interval_s: float
    parent_id: int | None
    opened_at: float
    original_budget_s: float
    hypotheses: list[Hypothesis] = field(default_factory=list)
    status: str = ACTIVE
    intervals: list[list[float | None]] = field(default_factory=list)

    actions: int = 0
    eliminated: int = 0

    last_ping_elapsed: float = 0.0
    recent_ping_answers: list[bool] = field(default_factory=list)
    pings_answered: int = 0
    pings_timed_out: int = 0

    checkpoints_answered: set[str] = field(default_factory=set)
    checkpoints_pending: dict[str, float] = field(default_factory=dict)
    extensions: int = 0

    closed_at: float | None = None
    postmortem: dict[str, str] | None = None

    def live_hypotheses(self) -> list[Hypothesis]:
        return [h for h in self.hypotheses if h.alive]

    def elapsed(self, at: float) -> float:
        return elapsed_from_intervals(self.intervals, at)


@dataclass(slots=True)
class State:
    loops: dict[int, Loop]
    active_id: int | None

    def active_loop(self) -> Loop | None:
        return self.loops.get(self.active_id) if self.active_id is not None else None

    def paused_loops(self) -> list[Loop]:
        """Paused loops, most recently paused first."""
        paused = [lp for lp in self.loops.values() if lp.status == PAUSED]
        return sorted(paused, key=_last_active_end, reverse=True)


def _last_active_end(loop: Loop) -> float:
    if not loop.intervals:
        return loop.opened_at
    end = loop.intervals[-1][1]
    return loop.opened_at if end is None else end
```

`checkpoints_answered` and `checkpoints_pending` are keyed `f"{kind}:{budget_s}"`, which is what makes an extension re-arm both checkpoints for free: a new budget produces new keys.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/core/test_models.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add loop/core/models.py tests/core/test_models.py
git commit -m "feat: add domain models and active clock"
```

---

## Task 4: Event constructors and `fold`

**Files:**
- Create: `loop/core/events.py`
- Test: `tests/core/test_events.py`

**Interfaces:**
- Consumes: `loop.core.models` (`Loop`, `Hypothesis`, `State`, `elapsed_from_intervals`, status constants).
- Produces: `EVENT_FIELDS: dict[str, tuple[str, ...]]`, `make(type: str, ts: float, loop_id: int | None = None, **fields) -> dict`, `fold(events: list[dict]) -> State`, `stack_depth(state: State) -> int`, `next_loop_id(state: State) -> int`, `next_hypothesis_id(loop: Loop) -> int`, `checkpoint_key(kind: str, budget_s: float) -> str`, exception `InvariantError`.

`fold` enforces one invariant and one only: **at most one loop is active at any point in the log.** Everything else the CLI is responsible for. `fold` does not auto-resume parents — the CLI emits an explicit `loop_resumed` when it pops the stack, so the log stays a literal record of what happened rather than something that needs replaying to understand.

`fold` intentionally ignores `checkpoint_shown`. That event exists so a crash while an overlay is up leaves a trace in the log; it carries no state.

- [ ] **Step 1: Write the failing test for `make`**

`tests/core/test_events.py`:

```python
import pytest

from loop.core import events
from loop.core.models import ABANDONED, ACTIVE, CLOSED, PAUSED


def test_make_builds_a_flat_dict():
    event = events.make("loop_paused", ts=10.0, loop_id=1, reason="prod incident")
    assert event == {
        "type": "loop_paused",
        "ts": 10.0,
        "loop_id": 1,
        "reason": "prod incident",
    }


def test_make_rejects_unknown_type():
    with pytest.raises(ValueError, match="unknown event type"):
        events.make("loop_exploded", ts=1.0, loop_id=1)


def test_make_rejects_missing_field():
    with pytest.raises(ValueError, match="reason"):
        events.make("loop_paused", ts=1.0, loop_id=1)


def test_make_rejects_extra_field():
    with pytest.raises(ValueError, match="mood"):
        events.make("loop_paused", ts=1.0, loop_id=1, reason="x", mood="bad")
```

- [ ] **Step 2: Write the failing tests for `fold`**

Append to `tests/core/test_events.py`:

```python
def opened(loop_id=1, ts=0.0, parent_id=None, budget_s=2700.0, hypotheses=("a", "b")):
    return events.make(
        "loop_opened",
        ts=ts,
        loop_id=loop_id,
        question=f"q{loop_id}",
        stop_condition=f"s{loop_id}",
        budget_s=budget_s,
        interval_s=1200.0,
        hypotheses=list(hypotheses),
        parent_id=parent_id,
    )


def test_fold_opens_an_active_loop():
    state = events.fold([opened()])
    loop = state.active_loop()
    assert state.active_id == 1
    assert loop.status == ACTIVE
    assert loop.intervals == [[0.0, None]]
    assert [h.text for h in loop.hypotheses] == ["a", "b"]
    assert [h.id for h in loop.hypotheses] == [1, 2]
    assert loop.original_budget_s == 2700.0


def test_fold_pause_closes_the_interval_and_clears_active():
    state = events.fold(
        [opened(), events.make("loop_paused", ts=600.0, loop_id=1, reason="lunch")]
    )
    assert state.active_id is None
    assert state.loops[1].status == PAUSED
    assert state.loops[1].intervals == [[0.0, 600.0]]


def test_fold_resume_opens_a_new_interval_and_rebases_the_ping():
    state = events.fold(
        [
            opened(),
            events.make("ping_answered", ts=1200.0, loop_id=1, smaller=True,
                        eliminated=None, shown_at=1200.0),
            events.make("loop_paused", ts=1500.0, loop_id=1, reason="lunch"),
            events.make("loop_resumed", ts=9000.0, loop_id=1),
        ]
    )
    loop = state.loops[1]
    assert state.active_id == 1
    assert loop.intervals == [[0.0, 1500.0], [9000.0, None]]
    # elapsed at resume is 1500s, so the next ping is a full interval from there
    assert loop.last_ping_elapsed == 1500.0


def test_fold_rejects_two_active_loops():
    with pytest.raises(events.InvariantError):
        events.fold([opened(loop_id=1), opened(loop_id=2)])


def test_fold_allows_stacking_after_an_explicit_pause():
    state = events.fold(
        [
            opened(loop_id=1),
            events.make("loop_paused", ts=100.0, loop_id=1, reason="prod incident"),
            opened(loop_id=2, ts=100.0, parent_id=1),
        ]
    )
    assert state.active_id == 2
    assert state.loops[2].parent_id == 1
    assert events.stack_depth(state) == 2


def test_fold_keeps_hypothesis_ids_stable_after_a_kill():
    state = events.fold(
        [
            opened(hypotheses=("a", "b", "c")),
            events.make("hypothesis_eliminated", ts=10.0, loop_id=1, hyp_id=2),
            events.make("hypothesis_added", ts=20.0, loop_id=1, hyp_id=4, text="d"),
        ]
    )
    loop = state.loops[1]
    assert [(h.id, h.alive) for h in loop.hypotheses] == [
        (1, True), (2, False), (3, True), (4, True)
    ]
    assert [h.id for h in loop.live_hypotheses()] == [1, 3, 4]
    assert loop.eliminated == 1


def test_fold_counts_actions_and_ping_answers():
    state = events.fold(
        [
            opened(),
            events.make("action_logged", ts=10.0, loop_id=1, action="restart pg",
                        because="pool exhausted"),
            events.make("ping_answered", ts=1200.0, loop_id=1, smaller=False,
                        eliminated=None, shown_at=1200.0),
            events.make("ping_unanswered", ts=2400.0, loop_id=1, shown_at=2400.0),
        ]
    )
    loop = state.loops[1]
    assert loop.actions == 1
    assert loop.pings_answered == 1
    assert loop.pings_timed_out == 1
    assert loop.recent_ping_answers == [False]
    assert loop.last_ping_elapsed == 2400.0


def test_fold_tracks_checkpoint_answers_and_pending_retries():
    key = events.checkpoint_key("p75", 2700.0)
    state = events.fold(
        [
            opened(),
            events.make("checkpoint_unanswered", ts=2025.0, loop_id=1, kind="p75",
                        shown_at=2025.0),
        ]
    )
    assert state.loops[1].checkpoints_pending == {key: 2025.0}

    state = events.fold(
        [
            opened(),
            events.make("checkpoint_unanswered", ts=2025.0, loop_id=1, kind="p75",
                        shown_at=2025.0),
            events.make("checkpoint_answered", ts=2700.0, loop_id=1, kind="p75",
                        on_track=True, decision="continue"),
        ]
    )
    assert state.loops[1].checkpoints_pending == {}
    assert state.loops[1].checkpoints_answered == {key}


def test_fold_extension_rearms_checkpoints_via_new_keys():
    state = events.fold(
        [
            opened(),
            events.make("checkpoint_answered", ts=2025.0, loop_id=1, kind="p75",
                        on_track=False, decision="extend"),
            events.make("budget_extended", ts=2025.0, loop_id=1, old_budget_s=2700.0,
                        new_budget_s=5400.0, learned="pool is not the issue"),
        ]
    )
    loop = state.loops[1]
    assert loop.budget_s == 5400.0
    assert loop.original_budget_s == 2700.0
    assert loop.extensions == 1
    assert events.checkpoint_key("p75", 5400.0) not in loop.checkpoints_answered


def test_fold_scope_cut_replaces_the_stop_condition():
    state = events.fold(
        [
            opened(),
            events.make("scope_cut", ts=2025.0, loop_id=1, old_stop_condition="s1",
                        new_stop_condition="comes up once"),
        ]
    )
    assert state.loops[1].stop_condition == "comes up once"


def test_fold_close_and_abandon_end_the_interval():
    closed = events.fold(
        [
            opened(),
            events.make("loop_closed", ts=3000.0, loop_id=1, what_was_it="cert",
                        giveaway="tls handshake", five_min_path="openssl s_client"),
        ]
    )
    assert closed.active_id is None
    assert closed.loops[1].status == CLOSED
    assert closed.loops[1].intervals == [[0.0, 3000.0]]
    assert closed.loops[1].postmortem["giveaway"] == "tls handshake"

    abandoned = events.fold(
        [opened(), events.make("loop_abandoned", ts=3000.0, loop_id=1)]
    )
    assert abandoned.loops[1].status == ABANDONED


def test_fold_pop_then_resume_parent_restores_the_stack():
    state = events.fold(
        [
            opened(loop_id=1),
            events.make("loop_paused", ts=100.0, loop_id=1, reason="prod incident"),
            opened(loop_id=2, ts=100.0, parent_id=1),
            events.make("loop_closed", ts=500.0, loop_id=2, what_was_it="bad deploy",
                        giveaway="500s only on /checkout", five_min_path="check rollout"),
            events.make("loop_resumed", ts=500.0, loop_id=1),
        ]
    )
    assert state.active_id == 1
    assert events.stack_depth(state) == 1
    assert state.loops[1].intervals == [[0.0, 100.0], [500.0, None]]


def test_stack_depth_counts_open_loops_only():
    state = events.fold(
        [
            opened(loop_id=1),
            events.make("loop_paused", ts=10.0, loop_id=1, reason="a"),
            opened(loop_id=2, ts=10.0, parent_id=1),
            events.make("loop_paused", ts=20.0, loop_id=2, reason="b"),
            opened(loop_id=3, ts=20.0, parent_id=2),
            events.make("loop_abandoned", ts=30.0, loop_id=3),
        ]
    )
    assert events.stack_depth(state) == 2
    assert events.next_loop_id(state) == 4


def test_paused_loops_are_most_recently_paused_first():
    state = events.fold(
        [
            opened(loop_id=1),
            events.make("loop_paused", ts=10.0, loop_id=1, reason="a"),
            opened(loop_id=2, ts=10.0, parent_id=1),
            events.make("loop_paused", ts=20.0, loop_id=2, reason="b"),
        ]
    )
    assert [lp.id for lp in state.paused_loops()] == [2, 1]
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `python -m pytest tests/core/test_events.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.core.events'`

- [ ] **Step 4: Implement `loop/core/events.py`**

```python
"""Event constructors and the fold that turns a log into state.

The event table is data, not fourteen near-identical constructor functions.
`make` validates that the fields supplied are exactly the fields declared,
which catches a typo at write time rather than three weeks later when
`fold` silently ignores it.
"""

from __future__ import annotations

from loop.core.models import (
    ABANDONED,
    ACTIVE,
    CLOSED,
    PAUSED,
    Hypothesis,
    Loop,
    State,
    elapsed_from_intervals,
)


class InvariantError(Exception):
    """The log describes a state the domain forbids."""


EVENT_FIELDS: dict[str, tuple[str, ...]] = {
    "loop_opened": (
        "question", "stop_condition", "budget_s", "interval_s", "hypotheses", "parent_id",
    ),
    "loop_paused": ("reason",),
    "loop_resumed": (),
    "action_logged": ("action", "because"),
    "hypothesis_added": ("hyp_id", "text"),
    "hypothesis_eliminated": ("hyp_id",),
    "ping_answered": ("smaller", "eliminated", "shown_at"),
    "ping_unanswered": ("shown_at",),
    "checkpoint_shown": ("kind",),
    "checkpoint_answered": ("kind", "on_track", "decision"),
    "checkpoint_unanswered": ("kind", "shown_at"),
    "scope_cut": ("old_stop_condition", "new_stop_condition"),
    "budget_extended": ("old_budget_s", "new_budget_s", "learned"),
    "loop_closed": ("what_was_it", "giveaway", "five_min_path"),
    "loop_abandoned": (),
}


def make(type: str, ts: float, loop_id: int | None = None, **fields) -> dict:
    if type not in EVENT_FIELDS:
        raise ValueError(f"unknown event type: {type!r}")

    expected = set(EVENT_FIELDS[type])
    supplied = set(fields)
    if missing := sorted(expected - supplied):
        raise ValueError(f"{type} is missing fields: {', '.join(missing)}")
    if extra := sorted(supplied - expected):
        raise ValueError(f"{type} got unexpected fields: {', '.join(extra)}")

    event = {"type": type, "ts": ts, "loop_id": loop_id}
    event.update(fields)
    return event


def checkpoint_key(kind: str, budget_s: float) -> str:
    return f"{kind}:{budget_s}"


def stack_depth(state: State) -> int:
    return sum(1 for lp in state.loops.values() if lp.status in (ACTIVE, PAUSED))


def next_loop_id(state: State) -> int:
    return max(state.loops, default=0) + 1


def next_hypothesis_id(loop: Loop) -> int:
    return max((h.id for h in loop.hypotheses), default=0) + 1


def fold(events: list[dict]) -> State:
    state = State(loops={}, active_id=None)
    for event in events:
        _apply(state, event)
    return state


def _apply(state: State, event: dict) -> None:
    kind = event["type"]
    ts = event["ts"]
    loop_id = event["loop_id"]

    if kind == "loop_opened":
        if state.active_id is not None:
            raise InvariantError(
                f"loop {loop_id} opened while loop {state.active_id} is still active"
            )
        state.loops[loop_id] = Loop(
            id=loop_id,
            question=event["question"],
            stop_condition=event["stop_condition"],
            budget_s=event["budget_s"],
            interval_s=event["interval_s"],
            parent_id=event["parent_id"],
            opened_at=ts,
            original_budget_s=event["budget_s"],
            hypotheses=[
                Hypothesis(id=index, text=text)
                for index, text in enumerate(event["hypotheses"], start=1)
            ],
            intervals=[[ts, None]],
        )
        state.active_id = loop_id
        return

    loop = state.loops[loop_id]

    if kind == "loop_paused":
        _end_interval(loop, ts)
        loop.status = PAUSED
        state.active_id = None

    elif kind == "loop_resumed":
        if state.active_id is not None:
            raise InvariantError(
                f"loop {loop_id} resumed while loop {state.active_id} is still active"
            )
        loop.status = ACTIVE
        loop.intervals.append([ts, None])
        # Rebase: returning to a loop must never fire a ping immediately.
        loop.last_ping_elapsed = loop.elapsed(ts)
        state.active_id = loop_id

    elif kind == "action_logged":
        loop.actions += 1

    elif kind == "hypothesis_added":
        loop.hypotheses.append(Hypothesis(id=event["hyp_id"], text=event["text"]))

    elif kind == "hypothesis_eliminated":
        for hypothesis in loop.hypotheses:
            if hypothesis.id == event["hyp_id"] and hypothesis.alive:
                hypothesis.alive = False
                loop.eliminated += 1
                break

    elif kind == "ping_answered":
        loop.last_ping_elapsed = loop.elapsed(ts)
        loop.pings_answered += 1
        loop.recent_ping_answers.append(bool(event["smaller"]))

    elif kind == "ping_unanswered":
        loop.last_ping_elapsed = loop.elapsed(ts)
        loop.pings_timed_out += 1

    elif kind == "checkpoint_shown":
        pass  # recorded for forensics only; carries no state

    elif kind == "checkpoint_answered":
        key = checkpoint_key(event["kind"], loop.budget_s)
        loop.checkpoints_answered.add(key)
        loop.checkpoints_pending.pop(key, None)

    elif kind == "checkpoint_unanswered":
        key = checkpoint_key(event["kind"], loop.budget_s)
        loop.checkpoints_pending[key] = loop.elapsed(ts)

    elif kind == "scope_cut":
        loop.stop_condition = event["new_stop_condition"]

    elif kind == "budget_extended":
        loop.budget_s = event["new_budget_s"]
        loop.extensions += 1

    elif kind in ("loop_closed", "loop_abandoned"):
        _end_interval(loop, ts)
        loop.status = CLOSED if kind == "loop_closed" else ABANDONED
        loop.closed_at = ts
        if kind == "loop_closed":
            loop.postmortem = {
                "what_was_it": event["what_was_it"],
                "giveaway": event["giveaway"],
                "five_min_path": event["five_min_path"],
            }
        if state.active_id == loop_id:
            state.active_id = None

    else:  # pragma: no cover - EVENT_FIELDS and _apply are kept in step
        raise ValueError(f"fold has no handler for {kind!r}")


def _end_interval(loop: Loop, ts: float) -> None:
    if loop.intervals and loop.intervals[-1][1] is None:
        loop.intervals[-1][1] = ts
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `python -m pytest tests/core/test_events.py -v`
Expected: PASS, 19 tests

- [ ] **Step 6: Add a guard test that the table and the fold cannot drift apart**

Append to `tests/core/test_events.py`:

```python
def test_every_declared_event_type_has_a_fold_handler():
    base = [opened()]
    samples = {
        "loop_paused": {"reason": "x"},
        "action_logged": {"action": "a", "because": "b"},
        "hypothesis_added": {"hyp_id": 3, "text": "c"},
        "hypothesis_eliminated": {"hyp_id": 1},
        "ping_answered": {"smaller": True, "eliminated": None, "shown_at": 1.0},
        "ping_unanswered": {"shown_at": 1.0},
        "checkpoint_shown": {"kind": "p75"},
        "checkpoint_answered": {"kind": "p75", "on_track": True, "decision": "continue"},
        "checkpoint_unanswered": {"kind": "p75", "shown_at": 1.0},
        "scope_cut": {"old_stop_condition": "a", "new_stop_condition": "b"},
        "budget_extended": {"old_budget_s": 1.0, "new_budget_s": 2.0, "learned": "x"},
        "loop_closed": {"what_was_it": "a", "giveaway": "b", "five_min_path": "c"},
        "loop_abandoned": {},
    }
    untested = set(events.EVENT_FIELDS) - set(samples) - {"loop_opened", "loop_resumed"}
    assert untested == set(), f"no fold sample for: {sorted(untested)}"

    for kind, fields in samples.items():
        events.fold(base + [events.make(kind, ts=50.0, loop_id=1, **fields)])
```

- [ ] **Step 7: Run the full core suite**

Run: `python -m pytest tests/core -v`
Expected: PASS, 48 tests

- [ ] **Step 8: Commit**

```bash
git add loop/core/events.py tests/core/test_events.py
git commit -m "feat: add event constructors and fold"
```

---

## Task 5: The scheduler — what check-in is due

**Files:**
- Create: `loop/core/schedule.py`
- Test: `tests/core/test_schedule.py`

**Interfaces:**
- Consumes: `loop.core.models` (`Loop`, `State`), `loop.core.events` (`checkpoint_key`).
- Produces: `Due` dataclass (`kind: str`, `loop_id: int`, `elapsed: float`), `next_due(state: State, now: float) -> Due | None`, `active_elapsed(loop: Loop, now: float) -> float`, `checkpoint_boundary(loop: Loop, kind: str) -> float`, constants `CHECKPOINT_FRACTIONS`, `CHECKPOINT_RETRY_S`, `COLLISION_WINDOW_S`.

Three rules from the spec fall out of the active clock rather than needing their own code, and the tests below prove it:

- **Sleep/wake catch-up** — elapsed jumps by hours, one ping fires, and answering it rebases `last_ping_elapsed` to the current elapsed. A backlog is structurally impossible because only one `Due` is ever returned per call.
- **Resume rebasing** — `fold` already sets `last_ping_elapsed` on `loop_resumed`.
- **Extension re-arming** — checkpoint keys embed the budget, so a new budget produces keys that have never been answered.

- [ ] **Step 1: Write the failing tests**

`tests/core/test_schedule.py`:

```python
from loop.core import events, schedule

BUDGET = 2700.0     # 45m
INTERVAL = 1200.0   # 20m


def opened(ts=0.0, loop_id=1, parent_id=None, budget_s=BUDGET):
    return events.make(
        "loop_opened",
        ts=ts,
        loop_id=loop_id,
        question="staging deploy fails at startup",
        stop_condition="comes up clean twice in a row",
        budget_s=budget_s,
        interval_s=INTERVAL,
        hypotheses=["cert expiry", "env var missing", "pg conn pool"],
        parent_id=parent_id,
    )


def due_for(log, now):
    return schedule.next_due(events.fold(log), now)


def test_nothing_is_due_before_the_first_interval():
    assert due_for([opened()], now=1199.0) is None


def test_ping_is_due_at_the_interval():
    due = due_for([opened()], now=1200.0)
    assert due.kind == "ping"
    assert due.loop_id == 1
    assert due.elapsed == 1200.0


def test_no_active_loop_means_nothing_is_due():
    log = [opened(), events.make("loop_paused", ts=100.0, loop_id=1, reason="lunch")]
    assert due_for(log, now=99_999.0) is None


def test_paused_time_never_makes_a_ping_due():
    log = [opened(), events.make("loop_paused", ts=60.0, loop_id=1, reason="lunch")]
    log.append(events.make("loop_resumed", ts=100_000.0, loop_id=1))
    # 60s of active time before the pause, 100s after it
    assert due_for(log, now=100_100.0) is None


def test_resume_rebases_the_next_ping():
    log = [
        opened(),
        events.make("ping_answered", ts=1200.0, loop_id=1, smaller=True,
                    eliminated=1, shown_at=1200.0),
        events.make("hypothesis_eliminated", ts=1200.0, loop_id=1, hyp_id=1),
        events.make("loop_paused", ts=2300.0, loop_id=1, reason="prod incident"),
        events.make("loop_resumed", ts=90_000.0, loop_id=1),
    ]
    # elapsed at resume is 2300s; a ping must not fire until 3500s of active time
    assert due_for(log, now=90_000.0 + 1199.0) is None
    assert due_for(log, now=90_000.0 + 1200.0).kind == "ping"


def test_sleep_and_wake_fires_exactly_one_ping_then_rebases():
    log = [opened()]
    now = 4.0 * 3600.0  # machine slept for four hours while the loop was active

    first = due_for(log, now)
    assert first.kind == "ping"

    log.append(
        events.make("ping_answered", ts=now, loop_id=1, smaller=False,
                    eliminated=None, shown_at=now)
    )
    assert due_for(log, now) is None
    assert due_for(log, now + 1199.0) is None
    assert due_for(log, now + 1200.0).kind == "ping"


def test_machine_sleep_while_active_still_accrues_elapsed():
    # No pause event, so the four-hour gap counts. p100 is long overdue.
    assert due_for([opened()], now=4.0 * 3600.0).kind in ("ping", "p100")


def test_p75_fires_at_three_quarters_of_the_budget():
    log = [
        opened(),
        events.make("ping_answered", ts=2020.0, loop_id=1, smaller=True,
                    eliminated=None, shown_at=2020.0),
    ]
    assert due_for(log, now=2024.0) is None
    assert due_for(log, now=2025.0).kind == "p75"


def test_p100_wins_when_both_checkpoints_are_due():
    assert due_for([opened()], now=2700.0).kind == "p100"


def test_an_answered_checkpoint_does_not_fire_again():
    log = [
        opened(),
        events.make("checkpoint_answered", ts=2025.0, loop_id=1, kind="p75",
                    on_track=True, decision="continue"),
        events.make("ping_answered", ts=2025.0, loop_id=1, smaller=True,
                    eliminated=None, shown_at=2025.0),
    ]
    assert due_for(log, now=2100.0) is None


def test_a_timed_out_checkpoint_refires_after_ten_minutes():
    log = [
        opened(),
        events.make("ping_answered", ts=2020.0, loop_id=1, smaller=True,
                    eliminated=None, shown_at=2020.0),
        events.make("checkpoint_unanswered", ts=2025.0, loop_id=1, kind="p75",
                    shown_at=2025.0),
    ]
    assert due_for(log, now=2025.0 + 599.0) is None
    assert due_for(log, now=2025.0 + 600.0).kind == "p75"


def test_p75_is_moot_once_p100_has_been_passed_and_answered():
    log = [
        opened(),
        events.make("checkpoint_answered", ts=2700.0, loop_id=1, kind="p100",
                    on_track=False, decision="close"),
        events.make("ping_answered", ts=2700.0, loop_id=1, smaller=True,
                    eliminated=None, shown_at=2700.0),
    ]
    # p75's boundary is also behind us and was never answered, but the budget
    # is gone — asking the 75% question now would be noise.
    assert due_for(log, now=2800.0) is None


def test_p75_is_suppressed_while_p100_is_inside_its_retry_backoff():
    log = [
        opened(),
        events.make("ping_answered", ts=2690.0, loop_id=1, smaller=True,
                    eliminated=None, shown_at=2690.0),
        events.make("checkpoint_unanswered", ts=2700.0, loop_id=1, kind="p100",
                    shown_at=2700.0),
    ]
    assert due_for(log, now=2700.0 + 599.0) is None
    assert due_for(log, now=2700.0 + 600.0).kind == "p100"


def test_a_missed_checkpoint_fires_as_soon_as_the_loop_is_active_again():
    log = [
        opened(),
        events.make("loop_paused", ts=1000.0, loop_id=1, reason="prod incident"),
        events.make("loop_resumed", ts=50_000.0, loop_id=1),
    ]
    # elapsed crosses 2025s only after 1025s of the resumed span
    assert due_for(log, now=50_000.0 + 1024.0) is None
    assert due_for(log, now=50_000.0 + 1025.0).kind == "p75"


def test_a_ping_within_three_minutes_of_a_checkpoint_is_skipped():
    # ping answered at 720s, so the next ping is due at 1920s; p75 lands at 2025s
    log = [
        opened(),
        events.make("ping_answered", ts=720.0, loop_id=1, smaller=True,
                    eliminated=None, shown_at=720.0),
    ]
    assert due_for(log, now=1920.0) is None      # 105s before p75 — suppressed
    assert due_for(log, now=2024.0) is None
    assert due_for(log, now=2025.0).kind == "p75"


def test_a_ping_more_than_three_minutes_before_a_checkpoint_still_fires():
    # ping answered at 600s, next ping at 1800s, which is 225s before p75
    log = [
        opened(),
        events.make("ping_answered", ts=600.0, loop_id=1, smaller=True,
                    eliminated=None, shown_at=600.0),
    ]
    assert due_for(log, now=1800.0).kind == "ping"


def test_extension_rearms_both_checkpoints():
    log = [
        opened(),
        events.make("checkpoint_answered", ts=2025.0, loop_id=1, kind="p75",
                    on_track=False, decision="extend"),
        events.make("budget_extended", ts=2025.0, loop_id=1, old_budget_s=BUDGET,
                    new_budget_s=5400.0, learned="pool is not the issue"),
        events.make("ping_answered", ts=2025.0, loop_id=1, smaller=False,
                    eliminated=None, shown_at=2025.0),
    ]
    assert due_for(log, now=4049.0) is None            # before the new p75
    assert due_for(log, now=4050.0).kind == "p75"      # 0.75 * 5400
    log.append(
        events.make("checkpoint_answered", ts=4050.0, loop_id=1, kind="p75",
                    on_track=True, decision="continue")
    )
    assert due_for(log, now=5400.0).kind == "p100"


def test_several_pause_resume_cycles_sum_towards_the_budget():
    log = [
        opened(),
        events.make("loop_paused", ts=1000.0, loop_id=1, reason="a"),
        events.make("loop_resumed", ts=10_000.0, loop_id=1),
        events.make("loop_paused", ts=11_000.0, loop_id=1, reason="b"),
        events.make("loop_resumed", ts=20_000.0, loop_id=1),
    ]
    state = events.fold(log)
    assert schedule.active_elapsed(state.loops[1], now=20_700.0) == 2700.0
    assert schedule.next_due(state, now=20_700.0).kind == "p100"
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/core/test_schedule.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.core.schedule'`

- [ ] **Step 3: Implement `loop/core/schedule.py`**

```python
"""What check-in, if any, is due right now.

Every decision here reads active elapsed. Nothing in this module may look at
a wall clock other than through the `now` argument, and `now` is only ever
used to close the open interval.
"""

from __future__ import annotations

from dataclasses import dataclass

from loop.core.events import checkpoint_key
from loop.core.models import Loop, State

CHECKPOINT_FRACTIONS: tuple[tuple[str, float], ...] = (("p100", 1.0), ("p75", 0.75))
CHECKPOINT_RETRY_S = 600.0
COLLISION_WINDOW_S = 180.0


@dataclass(frozen=True, slots=True)
class Due:
    kind: str        # "ping" | "p75" | "p100"
    loop_id: int
    elapsed: float


def active_elapsed(loop: Loop, now: float) -> float:
    return loop.elapsed(now)


def checkpoint_boundary(loop: Loop, kind: str) -> float:
    fraction = dict(CHECKPOINT_FRACTIONS)[kind]
    return fraction * loop.budget_s


def next_due(state: State, now: float) -> Due | None:
    loop = state.active_loop()
    if loop is None:
        return None

    elapsed = active_elapsed(loop, now)

    checkpoint = _checkpoint_due(loop, elapsed)
    if checkpoint is not None:
        return Due(kind=checkpoint, loop_id=loop.id, elapsed=elapsed)

    if _ping_due(loop, elapsed) and not _near_checkpoint(loop, elapsed):
        return Due(kind="ping", loop_id=loop.id, elapsed=elapsed)

    return None


def _checkpoint_due(loop: Loop, elapsed: float) -> str | None:
    """Only the highest boundary already passed is live.

    Once elapsed reaches p100, p75 is moot — it asks a weaker version of the
    same question about a budget that is already gone. So the first passed
    boundary decides the answer, whether that answer is "show it", "already
    answered", or "still inside its retry backoff".
    """
    for kind, fraction in CHECKPOINT_FRACTIONS:
        if elapsed < fraction * loop.budget_s:
            continue
        key = checkpoint_key(kind, loop.budget_s)
        if key in loop.checkpoints_answered:
            return None
        pending_at = loop.checkpoints_pending.get(key)
        if pending_at is not None and elapsed - pending_at < CHECKPOINT_RETRY_S:
            return None
        return kind
    return None


def _ping_due(loop: Loop, elapsed: float) -> bool:
    return elapsed - loop.last_ping_elapsed >= loop.interval_s


def _near_checkpoint(loop: Loop, elapsed: float) -> bool:
    """True when an unanswered checkpoint lands within the next three minutes.

    Only boundaries ahead of us matter. A boundary already behind us either
    returned from `_checkpoint_due`, or is inside its retry backoff — and
    during a backoff a ping is the right thing to show.
    """
    for kind, fraction in CHECKPOINT_FRACTIONS:
        boundary = fraction * loop.budget_s
        if checkpoint_key(kind, loop.budget_s) in loop.checkpoints_answered:
            continue
        if 0.0 <= boundary - elapsed <= COLLISION_WINDOW_S:
            return True
    return False
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/core/test_schedule.py -v`
Expected: PASS, 18 tests

- [ ] **Step 5: Run the whole suite to check nothing regressed**

Run: `python -m pytest -v`
Expected: PASS, 76 tests

- [ ] **Step 6: Commit**

```bash
git add loop/core/schedule.py tests/core/test_schedule.py
git commit -m "feat: add check-in scheduler"
```

---

## Task 6: The thrash detector

**Files:**
- Create: `loop/core/thrash.py`
- Test: `tests/core/test_thrash.py`

**Interfaces:**
- Consumes: `loop.core.models` (`Loop`), `loop.core.timefmt` (`format_duration`).
- Produces: `Thrash` dataclass (`firing: bool`, `panel: str | None`), `detect(loop: Loop, elapsed: float) -> Thrash`, constants `MIN_ELAPSED_S = 1800.0`, `MIN_ACTIONS = 5`, `NEGATIVE_PING_STREAK = 3`.

- [ ] **Step 1: Write the failing test**

`tests/core/test_thrash.py`:

```python
from loop.core import thrash
from loop.core.models import Loop


def make_loop(actions=0, eliminated=0, answers=()):
    return Loop(
        id=14,
        question="staging deploy fails at startup",
        stop_condition="comes up clean twice in a row",
        budget_s=2700.0,
        interval_s=1200.0,
        parent_id=None,
        opened_at=0.0,
        original_budget_s=2700.0,
        actions=actions,
        eliminated=eliminated,
        recent_ping_answers=list(answers),
    )


def test_quiet_when_nothing_is_wrong():
    assert thrash.detect(make_loop(actions=2, eliminated=1), elapsed=600.0).firing is False


def test_fires_on_many_actions_and_no_eliminations():
    result = thrash.detect(make_loop(actions=7, eliminated=0), elapsed=3120.0)
    assert result.firing is True
    assert "7 actions" in result.panel
    assert "0 ruled out" in result.panel
    assert "52m elapsed" in result.panel
    assert "you are thrashing" in result.panel


def test_does_not_fire_one_second_under_thirty_minutes():
    assert thrash.detect(make_loop(actions=7), elapsed=1799.0).firing is False


def test_fires_exactly_at_thirty_minutes():
    assert thrash.detect(make_loop(actions=5), elapsed=1800.0).firing is True


def test_does_not_fire_with_four_actions():
    assert thrash.detect(make_loop(actions=4), elapsed=3600.0).firing is False


def test_an_elimination_clears_the_action_condition():
    assert thrash.detect(make_loop(actions=9, eliminated=1), elapsed=3600.0).firing is False


def test_fires_on_three_consecutive_negative_pings():
    result = thrash.detect(make_loop(answers=[True, False, False, False]), elapsed=600.0)
    assert result.firing is True
    assert "3 pings" in result.panel


def test_two_negative_pings_are_not_enough():
    assert thrash.detect(make_loop(answers=[False, False]), elapsed=600.0).firing is False


def test_a_positive_ping_breaks_the_streak():
    loop = make_loop(answers=[False, False, True])
    assert thrash.detect(loop, elapsed=600.0).firing is False


def test_a_long_pause_does_not_push_a_loop_over_the_threshold():
    # 25 active minutes, regardless of how long the loop sat paused
    assert thrash.detect(make_loop(actions=8), elapsed=1500.0).firing is False
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python -m pytest tests/core/test_thrash.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.core.thrash'`

- [ ] **Step 3: Implement `loop/core/thrash.py`**

```python
"""Flailing has a precise signature: actions climbing, eliminations flat.

`elapsed` is always active elapsed — a loop that sat paused overnight has
not been thrashing for eight hours.
"""

from __future__ import annotations

from dataclasses import dataclass

from loop.core.models import Loop
from loop.core.timefmt import format_duration

MIN_ELAPSED_S = 1800.0
MIN_ACTIONS = 5
NEGATIVE_PING_STREAK = 3


@dataclass(frozen=True, slots=True)
class Thrash:
    firing: bool
    panel: str | None = None


def detect(loop: Loop, elapsed: float) -> Thrash:
    if _no_progress_despite_activity(loop, elapsed):
        return Thrash(
            firing=True,
            panel=(
                f"⚠  {loop.actions} actions, {loop.eliminated} ruled out, "
                f"{format_duration(elapsed)} elapsed.\n"
                "   you are thrashing.\n"
                "   → step away 5m, or escalate."
            ),
        )

    if _negative_streak(loop):
        return Thrash(
            firing=True,
            panel=(
                f"⚠  last {NEGATIVE_PING_STREAK} pings: no, no, no.\n"
                "   you are thrashing.\n"
                "   → step away 5m, or escalate."
            ),
        )

    return Thrash(firing=False)


def _no_progress_despite_activity(loop: Loop, elapsed: float) -> bool:
    return (
        elapsed >= MIN_ELAPSED_S
        and loop.actions >= MIN_ACTIONS
        and loop.eliminated == 0
    )


def _negative_streak(loop: Loop) -> bool:
    recent = loop.recent_ping_answers[-NEGATIVE_PING_STREAK:]
    return len(recent) == NEGATIVE_PING_STREAK and not any(recent)
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/core/test_thrash.py -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Commit**

```bash
git add loop/core/thrash.py tests/core/test_thrash.py
git commit -m "feat: add thrash detector"
```

---

## Task 7: CLI foundation — `open`, `try`, `hyp`, `status`

**Files:**
- Create: `loop/prompts.py`, `loop/render.py`, `loop/cli.py`
- Test: `tests/test_prompts.py`, `tests/test_cli_open.py`

**Interfaces:**
- Consumes: `loop.core.events`, `loop.core.models`, `loop.core.schedule`, `loop.core.timefmt`, `loop.store.jsonl`.
- Produces:
  - `prompts.ask_text(label, *, required=True) -> str`, `prompts.ask_yes_no(label) -> bool`, `prompts.ask_lines(label, *, minimum=1) -> list[str]`, `prompts.ask_duration(label) -> float`
  - `render.status_lines(state, now) -> list[str]`, `render.stack_lines(state, now) -> list[str]`
  - `cli.main(argv: list[str] | None = None) -> int`, `cli.LoopError`
  - `cli.load_state() -> State`, `cli.emit(event: dict) -> None`

Stdin prompts go through `builtins.input`, which is what makes every CLI test a `monkeypatch` of one function rather than a subprocess.

- [ ] **Step 1: Write the failing test for `prompts.py`**

`tests/test_prompts.py`:

```python
import pytest

from loop import prompts


def feed(monkeypatch, answers):
    queue = list(answers)
    monkeypatch.setattr("builtins.input", lambda _="": queue.pop(0))


def test_ask_text_returns_the_line(monkeypatch):
    feed(monkeypatch, ["comes up clean twice"])
    assert prompts.ask_text("stop condition?") == "comes up clean twice"


def test_ask_text_reprompts_until_non_empty(monkeypatch):
    feed(monkeypatch, ["", "   ", "finally"])
    assert prompts.ask_text("stop condition?") == "finally"


def test_ask_text_allows_empty_when_not_required(monkeypatch):
    feed(monkeypatch, [""])
    assert prompts.ask_text("optional?", required=False) == ""


@pytest.mark.parametrize("reply,expected", [("y", True), ("Y", True), ("n", False)])
def test_ask_yes_no(monkeypatch, reply, expected):
    feed(monkeypatch, [reply])
    assert prompts.ask_yes_no("stack it?") is expected


def test_ask_yes_no_reprompts_on_garbage(monkeypatch):
    feed(monkeypatch, ["maybe", "y"])
    assert prompts.ask_yes_no("stack it?") is True


def test_ask_lines_collects_until_blank(monkeypatch):
    feed(monkeypatch, ["cert expiry", "env var missing", ""])
    assert prompts.ask_lines("hypotheses?") == ["cert expiry", "env var missing"]


def test_ask_lines_requires_the_minimum(monkeypatch):
    feed(monkeypatch, ["", "cert expiry", ""])
    assert prompts.ask_lines("hypotheses?", minimum=1) == ["cert expiry"]


def test_ask_duration_parses_and_reprompts(monkeypatch):
    feed(monkeypatch, ["not a time", "45m"])
    assert prompts.ask_duration("time budget?") == 2700.0


def test_eof_raises_aborted(monkeypatch):
    def raise_eof(_=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)
    with pytest.raises(prompts.Aborted):
        prompts.ask_text("stop condition?")
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python -m pytest tests/test_prompts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.prompts'`

- [ ] **Step 3: Implement `loop/prompts.py`**

```python
"""Terminal prompts.

Every prompt reads through `builtins.input` so that tests replace one
function instead of driving a subprocess.
"""

from __future__ import annotations

from loop.core.timefmt import parse_duration


class Aborted(Exception):
    """The user pressed Ctrl-D or Ctrl-C at a prompt."""


def _read(label: str) -> str:
    try:
        return input(f"  → {label} ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise Aborted() from exc


def ask_text(label: str, *, required: bool = True) -> str:
    while True:
        value = _read(label)
        if value or not required:
            return value
        print("     required.")


def ask_yes_no(label: str) -> bool:
    while True:
        value = _read(f"{label} [y/n]").lower()
        if value in ("y", "yes"):
            return True
        if value in ("n", "no"):
            return False
        print("     answer y or n.")


def ask_lines(label: str, *, minimum: int = 1) -> list[str]:
    print(f"  → {label} (one per line, blank line to finish)")
    collected: list[str] = []
    while True:
        value = _read(f"{len(collected) + 1}.")
        if value:
            collected.append(value)
            continue
        if len(collected) >= minimum:
            return collected
        print(f"     at least {minimum} required.")


def ask_duration(label: str) -> float:
    while True:
        try:
            return parse_duration(_read(label))
        except ValueError as exc:
            print(f"     {exc}")
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_prompts.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Implement `loop/render.py`**

There is no separate test for this file — it is exercised through the CLI tests in Step 7, which assert on the rendered output.

```python
"""Human-readable views of state. No I/O, no decisions."""

from __future__ import annotations

from loop.core import events, schedule
from loop.core.models import PAUSED, State
from loop.core.timefmt import format_duration

STALE_PAUSE_S = 86_400.0


def status_lines(state: State, now: float) -> list[str]:
    loop = state.active_loop()
    if loop is None:
        return ["no active loop."]

    elapsed = schedule.active_elapsed(loop, now)
    depth = events.stack_depth(state)
    header = f"#{loop.id}  {loop.question}"
    if depth > 1:
        header += f"   ({depth - 1} paused)"

    lines = [
        header,
        f"  stop condition : {loop.stop_condition}",
        f"  elapsed        : {format_duration(elapsed)} / {format_duration(loop.budget_s)}",
        f"  actions        : {loop.actions}  ruled out: {loop.eliminated}",
        "  hypotheses     :",
    ]
    for hypothesis in loop.hypotheses:
        mark = " " if hypothesis.alive else "✗"
        lines.append(f"    {mark} {hypothesis.id}  {hypothesis.text}")
    return lines


def stack_lines(state: State, now: float) -> list[str]:
    loop = state.active_loop()
    lines: list[str] = []
    if loop is not None:
        lines.append(
            f"  ▶ #{loop.id}  {loop.question}"
            f"   {format_duration(schedule.active_elapsed(loop, now))} active"
        )
    for paused in state.paused_loops():
        paused_for = now - _paused_since(paused)
        stale = "  ⚠" if paused_for >= STALE_PAUSE_S else ""
        lines.append(
            f"    #{paused.id}  {paused.question}"
            f"   {format_duration(schedule.active_elapsed(paused, now))} active"
            f" · paused {format_duration(paused_for)}{stale}"
        )
    return lines or ["  stack is empty."]


def _paused_since(loop) -> float:
    assert loop.status == PAUSED
    return loop.intervals[-1][1]
```

- [ ] **Step 6: Write the failing tests for the CLI**

`tests/test_cli_open.py`:

```python
import json

import pytest

from loop import cli
from loop.core import events
from loop.store import jsonl


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
    monkeypatch.setenv("LOOP_BLOCKER", "fake")
    return tmp_path


def feed(monkeypatch, answers):
    queue = list(answers)
    monkeypatch.setattr("builtins.input", lambda _="": queue.pop(0))


def log():
    return jsonl.read_all()


def test_open_writes_a_loop_opened_event(monkeypatch, capsys):
    feed(monkeypatch, ["comes up clean twice", "45m", "cert expiry", "pg pool", ""])
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


def test_open_budget_flag_skips_the_budget_prompt(monkeypatch):
    feed(monkeypatch, ["comes up clean twice", "cert expiry", ""])
    assert cli.main(["open", "q", "--budget", "30m", "--interval", "10m"]) == 0
    assert log()[0]["budget_s"] == 1800.0
    assert log()[0]["interval_s"] == 600.0


def test_open_json_prints_the_event(monkeypatch, capsys):
    feed(monkeypatch, ["s", "45m", "h", ""])
    cli.main(["open", "q", "--json"])
    assert json.loads(capsys.readouterr().out)["type"] == "loop_opened"


def test_try_requires_a_belief(monkeypatch, capsys):
    feed(monkeypatch, ["s", "45m", "h", ""])
    cli.main(["open", "q"])
    with pytest.raises(SystemExit):
        cli.main(["try", "restart the pg container"])


def test_try_records_action_and_belief(monkeypatch):
    feed(monkeypatch, ["s", "45m", "h", ""])
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


def test_hyp_add_gets_the_next_id(monkeypatch):
    feed(monkeypatch, ["s", "45m", "one", "two", ""])
    cli.main(["open", "q"])
    cli.main(["hyp", "add", "three"])
    assert log()[-1]["hyp_id"] == 3


def test_hyp_kill_emits_elimination(monkeypatch):
    feed(monkeypatch, ["s", "45m", "one", "two", ""])
    cli.main(["open", "q"])
    assert cli.main(["hyp", "kill", "2"]) == 0
    assert log()[-1]["type"] == "hypothesis_eliminated"
    assert log()[-1]["hyp_id"] == 2


def test_hyp_kill_rejects_an_unknown_id(monkeypatch, capsys):
    feed(monkeypatch, ["s", "45m", "one", ""])
    cli.main(["open", "q"])
    assert cli.main(["hyp", "kill", "9"]) == 1
    assert "no live hypothesis 9" in capsys.readouterr().err


def test_status_shows_elapsed_and_hypotheses(monkeypatch, capsys):
    feed(monkeypatch, ["comes up clean twice", "45m", "cert expiry", "pg pool", ""])
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
```

- [ ] **Step 7: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_cli_open.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.cli'`

- [ ] **Step 8: Implement `loop/cli.py`**

`cmd_open` already contains the full stacking flow. Task 8 adds no branch to it — it only supplies the commands that pop the stack again.

```python
"""Argument parsing and command dispatch."""

from __future__ import annotations

import argparse
import json
import sys
import time

from loop import prompts, render
from loop.core import events
from loop.core.models import MAX_STACK_DEPTH, WARN_STACK_DEPTH, State
from loop.core.timefmt import format_duration
from loop.store import jsonl

DEFAULT_BUDGET_S = 2700.0
DEFAULT_INTERVAL_S = 1200.0


class LoopError(Exception):
    """A user-facing error. Printed to stderr; exit code 1."""


def load_state() -> State:
    return events.fold(jsonl.read_all())


def emit(event: dict) -> None:
    jsonl.append(event)


def require_active(state: State):
    loop = state.active_loop()
    if loop is None:
        raise LoopError("no active loop. run `loop open \"<question>\"` first.")
    return loop


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loop", description="a thrash detector for debugging")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    subparsers = parser.add_subparsers(dest="command", required=True)

    opener = subparsers.add_parser("open", help="open a loop")
    opener.add_argument("question")
    opener.add_argument("--budget", help="time budget, e.g. 45m")
    opener.add_argument("--interval", help="check-in interval, e.g. 20m")

    trier = subparsers.add_parser("try", help="record an action and the belief behind it")
    trier.add_argument("action")
    trier.add_argument("--because", required=True, help="what you believe this will show")

    hyp = subparsers.add_parser("hyp", help="manage hypotheses")
    hyp_sub = hyp.add_subparsers(dest="hyp_command", required=True)
    hyp_add = hyp_sub.add_parser("add")
    hyp_add.add_argument("text")
    hyp_kill = hyp_sub.add_parser("kill")
    hyp_kill.add_argument("hyp_id", type=int)

    subparsers.add_parser("status", help="show the active loop")
    return parser


def cmd_open(args, state: State, now: float) -> dict:
    active = state.active_loop()
    parent_id = active.id if active is not None else None
    if active is not None:
        depth = events.stack_depth(state)
        if depth >= MAX_STACK_DEPTH:
            raise LoopError(
                f"stack depth {depth}. close or abandon something before opening another."
            )
        print(f"  ⚠  #{active.id} \"{active.question}\" is active")
        if not prompts.ask_yes_no("pause it and stack this on top?"):
            raise LoopError("aborted.")
        emit(events.make("loop_paused", ts=now, loop_id=active.id, reason=args.question))
        print(f"  #{active.id} paused at "
              f"{format_duration(active.elapsed(now))} active.")
        state = load_state()

    stop_condition = prompts.ask_text("stop condition?")
    budget_s = (
        prompts.parse_duration(args.budget) if args.budget else prompts.ask_duration("time budget?")
    )
    interval_s = prompts.parse_duration(args.interval) if args.interval else DEFAULT_INTERVAL_S
    hypotheses = prompts.ask_lines("hypotheses?", minimum=1)

    event = events.make(
        "loop_opened",
        ts=now,
        loop_id=events.next_loop_id(state),
        question=args.question,
        stop_condition=stop_condition,
        budget_s=budget_s,
        interval_s=interval_s,
        hypotheses=hypotheses,
        parent_id=parent_id,
    )
    emit(event)

    depth = events.stack_depth(load_state())
    print(f"  #{event['loop_id']} open. timer running. "
          f"{len(hypotheses)} hypotheses live.")
    if depth > 1:
        print(f"  stack depth {depth}.")
    if depth >= WARN_STACK_DEPTH:
        print("  ⚠  you are context switching, not working.")
    return event


def cmd_try(args, state: State, now: float) -> dict:
    loop = require_active(state)
    event = events.make(
        "action_logged", ts=now, loop_id=loop.id, action=args.action, because=args.because
    )
    emit(event)
    print(f"  logged. {loop.actions + 1} actions this loop.")
    return event


def cmd_hyp(args, state: State, now: float) -> dict:
    loop = require_active(state)
    if args.hyp_command == "add":
        event = events.make(
            "hypothesis_added",
            ts=now,
            loop_id=loop.id,
            hyp_id=events.next_hypothesis_id(loop),
            text=args.text,
        )
        emit(event)
        print(f"  hypothesis {event['hyp_id']} added. "
              f"{len(loop.live_hypotheses()) + 1} live.")
        return event

    if not any(h.id == args.hyp_id and h.alive for h in loop.hypotheses):
        raise LoopError(f"no live hypothesis {args.hyp_id}.")
    event = events.make("hypothesis_eliminated", ts=now, loop_id=loop.id, hyp_id=args.hyp_id)
    emit(event)
    print(f"  hypothesis {args.hyp_id} ruled out. "
          f"{len(loop.live_hypotheses()) - 1} live.")
    return event


def cmd_status(args, state: State, now: float) -> dict:
    for line in render.status_lines(state, now):
        print(line)
    loop = state.active_loop()
    return {"active_id": state.active_id, "elapsed_s": loop.elapsed(now) if loop else None}


COMMANDS = {
    "open": cmd_open,
    "try": cmd_try,
    "hyp": cmd_hyp,
    "status": cmd_status,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = time.time()
    try:
        result = COMMANDS[args.command](args, load_state(), now)
    except (LoopError, prompts.Aborted) as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
```

- [ ] **Step 9: Re-export `parse_duration` from `prompts`**

`cmd_open` calls `prompts.parse_duration`. Add the import to `loop/prompts.py` so it is part of that module's surface:

```python
from loop.core.timefmt import parse_duration  # noqa: F401  (re-exported for cli)
```

This line replaces the existing `from loop.core.timefmt import parse_duration` at the top of the file.

- [ ] **Step 10: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_cli_open.py -v`
Expected: PASS, 11 tests

- [ ] **Step 11: Run the whole suite**

Run: `python -m pytest`
Expected: PASS, 96 tests

- [ ] **Step 12: Commit**

```bash
git add loop/prompts.py loop/render.py loop/cli.py tests/test_prompts.py tests/test_cli_open.py
git commit -m "feat: add cli with open, try, hyp, status"
```

---

## Task 8: The stack — `pause`, `resume`, `ls`, `close`, `abandon`

**Files:**
- Modify: `loop/cli.py` (add five subparsers and five command functions to `build_parser` and `COMMANDS`)
- Test: `tests/test_cli_stack.py`

**Interfaces:**
- Consumes: everything Task 7 produced.
- Produces: `cli.cmd_pause`, `cli.cmd_resume`, `cli.cmd_ls`, `cli.cmd_close`, `cli.cmd_abandon`, and `cli.pop_to_parent(state, loop, now) -> int | None`.

**The pop rule, stated once so it is not reinvented in two places:** `close` and `abandon` both call `pop_to_parent`. It resumes `loop.parent_id` when that loop is still paused, and otherwise resumes nothing — leaving zero active loops, which is the normal end state.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli_stack.py`:

```python
import pytest

from loop import cli
from loop.core import events
from loop.store import jsonl


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
    monkeypatch.setenv("LOOP_BLOCKER", "fake")


def feed(monkeypatch, answers):
    queue = list(answers)
    monkeypatch.setattr("builtins.input", lambda _="": queue.pop(0))


def open_loop(monkeypatch, question, stack=False):
    answers = (["y"] if stack else []) + ["stop cond", "45m", "a hypothesis", ""]
    feed(monkeypatch, answers)
    cli.main(["open", question])


def state():
    return events.fold(jsonl.read_all())


POSTMORTEM = ["it was the cert", "tls handshake reset", "openssl s_client"]


def test_pause_requires_a_reason_and_clears_active(monkeypatch):
    open_loop(monkeypatch, "staging deploy fails")
    feed(monkeypatch, ["prod incident"])
    assert cli.main(["pause"]) == 0

    current = state()
    assert current.active_id is None
    assert jsonl.read_all()[-1]["reason"] == "prod incident"


def test_pause_with_no_active_loop_fails(capsys):
    assert cli.main(["pause"]) == 1
    assert "no active loop" in capsys.readouterr().err


def test_resume_without_an_id_picks_the_most_recently_paused(monkeypatch):
    open_loop(monkeypatch, "first")
    feed(monkeypatch, ["a"])
    cli.main(["pause"])
    open_loop(monkeypatch, "second")
    feed(monkeypatch, ["b"])
    cli.main(["pause"])

    assert cli.main(["resume"]) == 0
    assert state().active_id == 2


def test_resume_jumps_to_an_arbitrary_id(monkeypatch):
    open_loop(monkeypatch, "first")
    feed(monkeypatch, ["a"])
    cli.main(["pause"])
    open_loop(monkeypatch, "second")
    feed(monkeypatch, ["b"])
    cli.main(["pause"])

    assert cli.main(["resume", "1"]) == 0
    assert state().active_id == 1


def test_resume_pauses_whatever_is_active_first(monkeypatch):
    open_loop(monkeypatch, "first")
    feed(monkeypatch, ["a"])
    cli.main(["pause"])
    open_loop(monkeypatch, "second")

    feed(monkeypatch, ["switching back"])
    assert cli.main(["resume", "1"]) == 0
    current = state()
    assert current.active_id == 1
    assert current.loops[2].status == "paused"


def test_resume_rejects_a_loop_that_is_not_paused(monkeypatch, capsys):
    open_loop(monkeypatch, "first")
    assert cli.main(["resume", "1"]) == 1
    assert "not paused" in capsys.readouterr().err


def test_open_stacks_and_records_the_parent(monkeypatch, capsys):
    open_loop(monkeypatch, "staging deploy fails")
    open_loop(monkeypatch, "prod 500s on /checkout", stack=True)

    current = state()
    assert current.active_id == 2
    assert current.loops[2].parent_id == 1
    assert current.loops[1].status == "paused"
    assert "stack depth 2" in capsys.readouterr().out


def test_open_refuses_at_max_depth(monkeypatch, capsys):
    open_loop(monkeypatch, "one")
    for question in ("two", "three", "four"):
        open_loop(monkeypatch, question, stack=True)
    assert events.stack_depth(state()) == 4

    feed(monkeypatch, ["y"])
    assert cli.main(["open", "five"]) == 1
    assert "stack depth" in capsys.readouterr().err


def test_open_warns_at_depth_three(monkeypatch, capsys):
    open_loop(monkeypatch, "one")
    open_loop(monkeypatch, "two", stack=True)
    open_loop(monkeypatch, "three", stack=True)
    assert "context switching, not working" in capsys.readouterr().out


def test_close_requires_all_three_postmortem_answers(monkeypatch):
    open_loop(monkeypatch, "staging deploy fails")
    feed(monkeypatch, ["", "it was the cert", "tls handshake reset", "openssl s_client"])
    assert cli.main(["close"]) == 0
    assert jsonl.read_all()[-1]["what_was_it"] == "it was the cert"


def test_close_pops_and_resumes_the_parent(monkeypatch, capsys):
    open_loop(monkeypatch, "staging deploy fails")
    open_loop(monkeypatch, "prod 500s", stack=True)

    feed(monkeypatch, POSTMORTEM)
    assert cli.main(["close"]) == 0

    current = state()
    assert current.active_id == 1
    assert current.loops[2].status == "closed"
    assert "resumed #1" in capsys.readouterr().out


def test_closing_the_bottom_loop_leaves_nothing_active(monkeypatch):
    open_loop(monkeypatch, "only loop")
    feed(monkeypatch, POSTMORTEM)
    cli.main(["close"])
    assert state().active_id is None
    assert events.stack_depth(state()) == 0


def test_abandon_needs_no_postmortem_and_pops(monkeypatch):
    open_loop(monkeypatch, "staging deploy fails")
    open_loop(monkeypatch, "prod 500s", stack=True)
    assert cli.main(["abandon"]) == 0
    current = state()
    assert current.loops[2].status == "abandoned"
    assert current.active_id == 1


def test_close_does_not_resume_a_parent_that_is_already_closed(monkeypatch):
    open_loop(monkeypatch, "parent")
    open_loop(monkeypatch, "child", stack=True)
    # abandon the child, resume parent, then abandon the parent too
    cli.main(["abandon"])
    cli.main(["abandon"])
    assert state().active_id is None


def test_ls_shows_the_stack(monkeypatch, capsys):
    open_loop(monkeypatch, "staging deploy fails")
    open_loop(monkeypatch, "prod 500s on /checkout", stack=True)
    cli.main(["ls"])
    out = capsys.readouterr().out
    assert "▶ #2  prod 500s on /checkout" in out
    assert "#1  staging deploy fails" in out
    assert "paused" in out


def test_ls_on_an_empty_stack(capsys):
    assert cli.main(["ls"]) == 0
    assert "stack is empty" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_cli_stack.py -v`
Expected: FAIL — `argparse` exits with "invalid choice: 'pause'"

- [ ] **Step 3: Add the subparsers to `build_parser`**

Insert directly before `subparsers.add_parser("status", ...)`:

```python
    pauser = subparsers.add_parser("pause", help="pause the active loop")
    pauser.add_argument("--reason", help="what interrupted; prompted if omitted")

    resumer = subparsers.add_parser("resume", help="resume a paused loop")
    resumer.add_argument("loop_id", nargs="?", type=int)

    subparsers.add_parser("ls", help="show the loop stack")
    subparsers.add_parser("close", help="close the active loop with a postmortem")
    subparsers.add_parser("abandon", help="abandon the active loop")
```

- [ ] **Step 4: Add the command functions to `loop/cli.py`**

Insert before the `COMMANDS` table:

```python
def cmd_pause(args, state: State, now: float) -> dict:
    loop = require_active(state)
    reason = args.reason or prompts.ask_text("what interrupted?")
    event = events.make("loop_paused", ts=now, loop_id=loop.id, reason=reason)
    emit(event)
    print(f"  #{loop.id} paused at {format_duration(loop.elapsed(now))} active.")
    return event


def cmd_resume(args, state: State, now: float) -> dict:
    target = _resume_target(args, state)

    active = state.active_loop()
    if active is not None:
        if active.id == target.id:
            raise LoopError(f"#{target.id} is already active.")
        reason = prompts.ask_text("what interrupted?")
        emit(events.make("loop_paused", ts=now, loop_id=active.id, reason=reason))

    event = events.make("loop_resumed", ts=now, loop_id=target.id)
    emit(event)
    print(f"  → resumed #{target.id} · "
          f"{format_duration(target.elapsed(now))} / {format_duration(target.budget_s)} · "
          f"next ping {format_duration(target.interval_s)}")
    return event


def _resume_target(args, state: State):
    if args.loop_id is None:
        paused = state.paused_loops()
        if not paused:
            raise LoopError("nothing to resume.")
        return paused[0]

    target = state.loops.get(args.loop_id)
    if target is None:
        raise LoopError(f"no loop #{args.loop_id}.")
    if target.status != PAUSED:
        raise LoopError(f"#{args.loop_id} is {target.status}, not paused.")
    return target


def pop_to_parent(state: State, loop, now: float) -> int | None:
    """Resume the parent if it is still paused. Returns the resumed loop id."""
    if loop.parent_id is None:
        return None
    parent = state.loops.get(loop.parent_id)
    if parent is None or parent.status != PAUSED:
        return None
    emit(events.make("loop_resumed", ts=now, loop_id=parent.id))
    return parent.id


def cmd_close(args, state: State, now: float) -> dict:
    loop = require_active(state)
    event = events.make(
        "loop_closed",
        ts=now,
        loop_id=loop.id,
        what_was_it=prompts.ask_text("what was it?"),
        giveaway=prompts.ask_text("what was the giveaway?"),
        five_min_path=prompts.ask_text("how could I have found it in 5 minutes?"),
    )
    emit(event)
    print(f"  closed #{loop.id}. {format_duration(loop.elapsed(now))}. "
          f"{loop.eliminated} hypotheses ruled out. pattern saved.")
    _report_pop(load_state(), loop, now)
    return event


def cmd_abandon(args, state: State, now: float) -> dict:
    loop = require_active(state)
    event = events.make("loop_abandoned", ts=now, loop_id=loop.id)
    emit(event)
    print(f"  abandoned #{loop.id} at {format_duration(loop.elapsed(now))} active.")
    _report_pop(load_state(), loop, now)
    return event


def _report_pop(state: State, loop, now: float) -> None:
    resumed_id = pop_to_parent(state, loop, now)
    if resumed_id is None:
        return
    parent = state.loops[resumed_id]
    print(f"  → resumed #{resumed_id} · "
          f"{format_duration(parent.elapsed(now))} / {format_duration(parent.budget_s)} · "
          f"next ping {format_duration(parent.interval_s)}")


def cmd_ls(args, state: State, now: float) -> dict:
    for line in render.stack_lines(state, now):
        print(line)
    return {
        "active_id": state.active_id,
        "paused_ids": [lp.id for lp in state.paused_loops()],
    }
```

- [ ] **Step 5: Extend the imports and the `COMMANDS` table**

Change the models import line to include `PAUSED`:

```python
from loop.core.models import MAX_STACK_DEPTH, PAUSED, WARN_STACK_DEPTH, State
```

Replace the `COMMANDS` table with:

```python
COMMANDS = {
    "open": cmd_open,
    "try": cmd_try,
    "hyp": cmd_hyp,
    "pause": cmd_pause,
    "resume": cmd_resume,
    "ls": cmd_ls,
    "close": cmd_close,
    "abandon": cmd_abandon,
    "status": cmd_status,
}
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_cli_stack.py -v`
Expected: PASS, 16 tests

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest`
Expected: PASS, 112 tests

- [ ] **Step 8: Commit**

```bash
git add loop/cli.py tests/test_cli_stack.py
git commit -m "feat: add loop stack with pause and resume"
```

---

## Task 9: The blocker contract, the fake, and the factory

**Files:**
- Create: `loop/blockers/base.py`, `loop/blockers/fake.py`, `loop/blockers/factory.py`
- Test: `tests/blockers/__init__.py`, `tests/blockers/test_base.py`, `tests/blockers/test_factory.py`

**Interfaces:**
- Consumes: nothing. **This package must not import `loop.core`.**
- Produces:
  - `base.Choice(key, label)`, `base.TextField(name, label, required=True)`, `base.Prompt(...)`, `base.Answers(...)`, `base.Blocker` protocol
  - `base.TIMEOUT_S = 300.0`, `base.KILL_AFTER_S = 310.0`
  - `base.format_countdown(remaining_s) -> str`, `base.remaining_fraction(remaining_s, total_s) -> float`
  - `fake.FakeBlocker(answers: list[Answers])` with attribute `.prompts: list[Prompt]`
  - `factory.get_blocker() -> Blocker`

- [ ] **Step 1: Write the failing test for `base.py`**

`tests/blockers/test_base.py`:

```python
import pytest

from loop.blockers import base


def test_timeouts_are_the_specified_values():
    assert base.TIMEOUT_S == 300.0
    assert base.KILL_AFTER_S == 310.0


@pytest.mark.parametrize(
    "remaining,expected",
    [(300.0, "5:00"), (192.0, "3:12"), (59.4, "0:59"), (0.0, "0:00"), (-3.0, "0:00")],
)
def test_format_countdown(remaining, expected):
    assert base.format_countdown(remaining) == expected


@pytest.mark.parametrize(
    "remaining,total,expected",
    [(300.0, 300.0, 1.0), (150.0, 300.0, 0.5), (0.0, 300.0, 0.0), (-5.0, 300.0, 0.0)],
)
def test_remaining_fraction(remaining, total, expected):
    assert base.remaining_fraction(remaining, total) == expected


def test_prompt_defaults_are_usable_without_optional_parts():
    prompt = base.Prompt(
        kind="ping",
        title="loop #14 · 42m / 45m",
        question="hypothesis space smaller than 20m ago?",
        choices=[base.Choice("y", "yes"), base.Choice("n", "no")],
    )
    assert prompt.pick_list is None
    assert prompt.pick_after is None
    assert prompt.fields_after == {}
    assert prompt.warning is None
    assert prompt.timeout_s == 300.0
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python -m pytest tests/blockers/test_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.blockers.base'`

- [ ] **Step 3: Implement `loop/blockers/base.py`**

```python
"""The contract between the scheduler and whatever draws on the screen.

Nothing in `loop.blockers` may import `loop.core`. That is the seam: a
Windows overlay is a new file implementing `ask`, and the domain logic
never learns which one ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

TIMEOUT_S = 300.0
KILL_AFTER_S = 310.0


@dataclass(frozen=True, slots=True)
class Choice:
    key: str        # a single keystroke, e.g. "y"
    label: str


@dataclass(frozen=True, slots=True)
class TextField:
    name: str
    label: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class Prompt:
    kind: str                                  # "ping" | "p75" | "p100"
    title: str
    question: str
    choices: list[Choice]
    pick_list: list[str] | None = None
    pick_after: str | None = None              # reveal pick_list after this key
    fields_after: dict[str, list[TextField]] = field(default_factory=dict)
    warning: str | None = None
    timeout_s: float = TIMEOUT_S


@dataclass(frozen=True, slots=True)
class Answers:
    timed_out: bool
    choice: str | None
    picked: int | None                         # 1-based index into pick_list
    fields: dict[str, str]
    shown_at: float
    answered_at: float


class Blocker(Protocol):
    def ask(self, prompt: Prompt) -> Answers: ...


def format_countdown(remaining_s: float) -> str:
    remaining = max(0, int(remaining_s))
    return f"{remaining // 60}:{remaining % 60:02d}"


def remaining_fraction(remaining_s: float, total_s: float) -> float:
    if total_s <= 0:
        return 0.0
    return max(0.0, min(1.0, remaining_s / total_s))
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/blockers/test_base.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Implement `loop/blockers/fake.py`**

```python
"""A blocker that answers from a script. Used by tests and `LOOP_BLOCKER=fake`."""

from __future__ import annotations

import json
import os
import time

from loop.blockers.base import Answers, Prompt


class FakeBlocker:
    """Returns queued answers in order; times out once the queue is empty."""

    def __init__(self, answers: list[Answers] | None = None) -> None:
        self._queue = list(answers or [])
        self.prompts: list[Prompt] = []

    def ask(self, prompt: Prompt) -> Answers:
        self.prompts.append(prompt)
        now = time.time()
        if not self._queue:
            return Answers(
                timed_out=True, choice=None, picked=None, fields={},
                shown_at=now, answered_at=now + prompt.timeout_s,
            )
        answer = self._queue.pop(0)
        return Answers(
            timed_out=answer.timed_out,
            choice=answer.choice,
            picked=answer.picked,
            fields=dict(answer.fields),
            shown_at=now,
            answered_at=now,
        )


def from_env() -> FakeBlocker:
    """Build a FakeBlocker from the JSON file named by LOOP_FAKE_ANSWERS.

    The file holds a list of objects, each with any of `timed_out`,
    `choice`, `picked`, `fields`. Missing keys take their neutral value.
    """
    path = os.environ.get("LOOP_FAKE_ANSWERS")
    if not path:
        return FakeBlocker([])

    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)

    return FakeBlocker(
        [
            Answers(
                timed_out=item.get("timed_out", False),
                choice=item.get("choice"),
                picked=item.get("picked"),
                fields=item.get("fields", {}),
                shown_at=0.0,
                answered_at=0.0,
            )
            for item in raw
        ]
    )
```

- [ ] **Step 6: Write the failing test for `factory.py`**

`tests/blockers/test_factory.py`:

```python
import json

import pytest

from loop.blockers import factory
from loop.blockers.fake import FakeBlocker


def test_env_override_selects_the_fake(monkeypatch):
    monkeypatch.setenv("LOOP_BLOCKER", "fake")
    assert isinstance(factory.get_blocker(), FakeBlocker)


def test_fake_reads_scripted_answers(tmp_path, monkeypatch):
    script = tmp_path / "answers.json"
    script.write_text(json.dumps([{"choice": "y", "picked": 2}]), encoding="utf-8")
    monkeypatch.setenv("LOOP_BLOCKER", "fake")
    monkeypatch.setenv("LOOP_FAKE_ANSWERS", str(script))

    blocker = factory.get_blocker()
    answer = blocker.ask(_prompt())
    assert answer.choice == "y"
    assert answer.picked == 2
    assert answer.timed_out is False


def test_fake_times_out_once_the_script_runs_dry(monkeypatch):
    monkeypatch.setenv("LOOP_BLOCKER", "fake")
    monkeypatch.delenv("LOOP_FAKE_ANSWERS", raising=False)
    assert factory.get_blocker().ask(_prompt()).timed_out is True


def test_unknown_override_is_rejected(monkeypatch):
    monkeypatch.setenv("LOOP_BLOCKER", "hologram")
    with pytest.raises(ValueError, match="hologram"):
        factory.get_blocker()


def test_non_darwin_falls_back_to_tk(monkeypatch):
    monkeypatch.delenv("LOOP_BLOCKER", raising=False)
    monkeypatch.setattr(factory.sys, "platform", "linux")
    calls = []
    monkeypatch.setattr(factory, "_tk_blocker", lambda: calls.append("tk") or "TK")
    assert factory.get_blocker() == "TK"
    assert calls == ["tk"]


def test_darwin_without_pyobjc_falls_back_to_tk(monkeypatch, capsys):
    monkeypatch.delenv("LOOP_BLOCKER", raising=False)
    monkeypatch.setattr(factory.sys, "platform", "darwin")
    monkeypatch.setattr(factory, "_macos_blocker", _raise_import_error)
    monkeypatch.setattr(factory, "_tk_blocker", lambda: "TK")
    assert factory.get_blocker() == "TK"
    assert "soft block" in capsys.readouterr().err


def _raise_import_error():
    raise ImportError("no pyobjc")


def _prompt():
    from loop.blockers.base import Choice, Prompt

    return Prompt(kind="ping", title="t", question="q", choices=[Choice("y", "yes")])
```

- [ ] **Step 7: Run it and confirm it fails**

Run: `python -m pytest tests/blockers/test_factory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.blockers.factory'`

- [ ] **Step 8: Implement `loop/blockers/factory.py`**

The `_tk_blocker` and `_macos_blocker` indirections exist so the tests above can substitute them without importing a GUI toolkit. `blockers/tk.py` arrives in Task 10 and `blockers/macos.py` in Task 13; until then the fallbacks raise `ImportError`, which is exactly the path the tests drive.

```python
"""Pick a blocker for this machine.

`LOOP_BLOCKER` overrides everything, which is how tests and day-to-day
development avoid a full-screen window every twenty minutes.
"""

from __future__ import annotations

import os
import sys


def _tk_blocker():
    from loop.blockers.tk import TkBlocker

    return TkBlocker()


def _macos_blocker():
    from loop.blockers.macos import MacOSBlocker

    return MacOSBlocker()


def _fake_blocker():
    from loop.blockers.fake import from_env

    return from_env()


def get_blocker():
    override = os.environ.get("LOOP_BLOCKER")
    if override == "fake":
        return _fake_blocker()
    if override == "tk":
        return _tk_blocker()
    if override == "macos":
        return _macos_blocker()
    if override:
        raise ValueError(f"unknown LOOP_BLOCKER: {override!r}")

    if sys.platform == "darwin":
        try:
            return _macos_blocker()
        except ImportError:
            print(
                "  pyobjc not installed — using a soft block. "
                "install with: pip install 'loop-tool[macos]'",
                file=sys.stderr,
            )
    return _tk_blocker()
```

- [ ] **Step 9: Run the tests and confirm they pass**

Run: `python -m pytest tests/blockers -v`
Expected: PASS, 17 tests

- [ ] **Step 10: Commit**

```bash
git add loop/blockers tests/blockers
git commit -m "feat: add blocker contract, fake, and factory"
```

---

## Task 10: The prompt session and the tkinter overlay

**Files:**
- Create: `loop/blockers/session.py`, `loop/blockers/tk.py`
- Test: `tests/blockers/test_session.py`

**Interfaces:**
- Consumes: `loop.blockers.base`.
- Produces: `session.PromptSession(prompt, shown_at)` with `.stage`, `.choice`, `.picked`, `.press_key(key) -> bool`, `.visible_pick_list()`, `.pending_fields()`, `.submit_fields(values) -> list[str]`, `.is_complete()`, `.answers(answered_at)`, `.timed_out(at)`; `tk.TkBlocker`.

**Why a separate session object:** the interaction is a small state machine — a choice may reveal a pick list, which may reveal text fields — and it is identical for tkinter and PyObjC. Extracting it means the tricky part is tested without a display, and the two GUI files are reduced to drawing whatever stage the session reports.

- [ ] **Step 1: Write the failing test**

`tests/blockers/test_session.py`:

```python
import pytest

from loop.blockers.base import Choice, Prompt, TextField
from loop.blockers.session import PromptSession


def ping_prompt():
    return Prompt(
        kind="ping",
        title="loop #14 · 42m / 45m",
        question="hypothesis space smaller than 20m ago?",
        choices=[Choice("y", "yes"), Choice("n", "no")],
        pick_list=["cert expiry", "env var missing", "pg conn pool"],
        pick_after="y",
    )


def checkpoint_prompt():
    return Prompt(
        kind="p75",
        title="75% of budget gone · 34m / 45m",
        question="is 75% of the work done?",
        choices=[Choice("y", "yes"), Choice("c", "cut scope"), Choice("e", "extend")],
        fields_after={
            "c": [TextField("new_stop_condition", "new stop condition")],
            "e": [
                TextField("new_budget", "new budget"),
                TextField("learned", "what did you learn that made it bigger?"),
            ],
        },
    )


def test_a_plain_choice_completes_immediately():
    session = PromptSession(ping_prompt(), shown_at=100.0)
    assert session.stage == "choice"
    assert session.press_key("n") is True
    assert session.is_complete()

    answers = session.answers(answered_at=103.0)
    assert answers.choice == "n"
    assert answers.picked is None
    assert answers.fields == {}
    assert answers.timed_out is False
    assert (answers.shown_at, answers.answered_at) == (100.0, 103.0)


def test_an_unknown_key_is_ignored():
    session = PromptSession(ping_prompt(), shown_at=0.0)
    assert session.press_key("q") is False
    assert session.stage == "choice"


def test_yes_reveals_the_pick_list():
    session = PromptSession(ping_prompt(), shown_at=0.0)
    session.press_key("y")
    assert session.stage == "pick"
    assert session.visible_pick_list() == ["cert expiry", "env var missing", "pg conn pool"]
    assert not session.is_complete()

    assert session.press_key("2") is True
    assert session.is_complete()
    assert session.answers(answered_at=1.0).picked == 2


def test_the_pick_list_is_hidden_before_the_revealing_choice():
    assert PromptSession(ping_prompt(), shown_at=0.0).visible_pick_list() is None


def test_an_out_of_range_pick_is_ignored():
    session = PromptSession(ping_prompt(), shown_at=0.0)
    session.press_key("y")
    assert session.press_key("7") is False
    assert session.stage == "pick"


def test_a_choice_with_fields_moves_to_the_field_stage():
    session = PromptSession(checkpoint_prompt(), shown_at=0.0)
    session.press_key("c")
    assert session.stage == "fields"
    assert [f.name for f in session.pending_fields()] == ["new_stop_condition"]
    assert not session.is_complete()

    assert session.submit_fields({"new_stop_condition": "comes up once"}) == []
    assert session.is_complete()
    assert session.answers(answered_at=5.0).fields == {"new_stop_condition": "comes up once"}


def test_missing_required_fields_are_reported_and_do_not_complete():
    session = PromptSession(checkpoint_prompt(), shown_at=0.0)
    session.press_key("e")
    errors = session.submit_fields({"new_budget": "90m", "learned": "   "})
    assert errors == ["learned"]
    assert not session.is_complete()

    assert session.submit_fields({"new_budget": "90m", "learned": "pool is fine"}) == []
    assert session.is_complete()


def test_a_choice_with_no_fields_on_a_field_prompt_completes():
    session = PromptSession(checkpoint_prompt(), shown_at=0.0)
    session.press_key("y")
    assert session.is_complete()
    assert session.answers(answered_at=1.0).choice == "y"


def test_timed_out_answers_carry_nothing():
    session = PromptSession(ping_prompt(), shown_at=100.0)
    session.press_key("y")
    answers = session.timed_out(at=400.0)
    assert answers.timed_out is True
    assert answers.choice is None
    assert answers.picked is None
    assert answers.fields == {}
    assert (answers.shown_at, answers.answered_at) == (100.0, 400.0)


def test_keys_are_ignored_once_complete():
    session = PromptSession(ping_prompt(), shown_at=0.0)
    session.press_key("n")
    assert session.press_key("y") is False
    assert session.answers(answered_at=1.0).choice == "n"


def test_answers_before_completion_is_an_error():
    session = PromptSession(ping_prompt(), shown_at=0.0)
    with pytest.raises(RuntimeError):
        session.answers(answered_at=1.0)
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python -m pytest tests/blockers/test_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.blockers.session'`

- [ ] **Step 3: Implement `loop/blockers/session.py`**

```python
"""The interaction state machine, shared by every overlay.

choice → (optional pick) → (optional fields) → done.
"""

from __future__ import annotations

from loop.blockers.base import Answers, Prompt, TextField

CHOICE = "choice"
PICK = "pick"
FIELDS = "fields"
DONE = "done"


class PromptSession:
    def __init__(self, prompt: Prompt, shown_at: float) -> None:
        self.prompt = prompt
        self.shown_at = shown_at
        self.stage = CHOICE
        self.choice: str | None = None
        self.picked: int | None = None
        self.fields: dict[str, str] = {}

    def press_key(self, key: str) -> bool:
        """Feed a keystroke. Returns True when it advanced the session."""
        if self.stage == CHOICE:
            return self._press_choice(key)
        if self.stage == PICK:
            return self._press_pick(key)
        return False

    def _press_choice(self, key: str) -> bool:
        if key not in {choice.key for choice in self.prompt.choices}:
            return False
        self.choice = key
        self.stage = self._stage_after_choice(key)
        return True

    def _stage_after_choice(self, key: str) -> str:
        if self.prompt.pick_after == key and self.prompt.pick_list:
            return PICK
        if self.prompt.fields_after.get(key):
            return FIELDS
        return DONE

    def _press_pick(self, key: str) -> bool:
        if not key.isdigit():
            return False
        index = int(key)
        if not 1 <= index <= len(self.prompt.pick_list or []):
            return False
        self.picked = index
        self.stage = FIELDS if self.prompt.fields_after.get(self.choice or "") else DONE
        return True

    def visible_pick_list(self) -> list[str] | None:
        return self.prompt.pick_list if self.stage == PICK else None

    def pending_fields(self) -> list[TextField]:
        if self.stage != FIELDS:
            return []
        return self.prompt.fields_after.get(self.choice or "", [])

    def submit_fields(self, values: dict[str, str]) -> list[str]:
        """Returns the names of required fields that came back blank."""
        missing = [
            field.name
            for field in self.pending_fields()
            if field.required and not values.get(field.name, "").strip()
        ]
        if missing:
            return missing
        self.fields = {name: value.strip() for name, value in values.items()}
        self.stage = DONE
        return []

    def is_complete(self) -> bool:
        return self.stage == DONE

    def answers(self, answered_at: float) -> Answers:
        if not self.is_complete():
            raise RuntimeError("session is not complete")
        return Answers(
            timed_out=False,
            choice=self.choice,
            picked=self.picked,
            fields=dict(self.fields),
            shown_at=self.shown_at,
            answered_at=answered_at,
        )

    def timed_out(self, at: float) -> Answers:
        return Answers(
            timed_out=True, choice=None, picked=None, fields={},
            shown_at=self.shown_at, answered_at=at,
        )
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/blockers/test_session.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Implement `loop/blockers/tk.py`**

This file has no automated test — a full-screen window cannot be asserted on in CI. It is covered by the manual checklist in Task 13. Keep it thin: every decision belongs to `PromptSession`, and this file only draws and forwards keystrokes.

```python
"""The portable overlay. Weaker than the macOS block — Alt-Tab escapes it.

Ships on macOS, Windows, and Linux. Draws whatever stage the session
reports and forwards keystrokes; it makes no decisions of its own.
"""

from __future__ import annotations

import time
import tkinter as tk

from loop.blockers.base import Answers, Prompt, format_countdown
from loop.blockers.session import FIELDS, PICK, PromptSession

BG = "#101014"
FG = "#e6e6e6"
DIM = "#8a8a94"
WARN = "#ff5f56"
BAR_BG = "#2a2a33"
BAR_FG = "#7aa2f7"


class TkBlocker:
    def ask(self, prompt: Prompt) -> Answers:
        return _Overlay(prompt).run()


class _Overlay:
    def __init__(self, prompt: Prompt) -> None:
        self.prompt = prompt
        self.session = PromptSession(prompt, shown_at=time.time())
        self.result: Answers | None = None
        self.entries: dict[str, tk.Entry] = {}

        self.root = tk.Tk()
        self.root.configure(bg=BG)
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

        self.body = tk.Frame(self.root, bg=BG)
        self.body.place(relx=0.5, rely=0.45, anchor="center")

        self.bar_canvas = tk.Canvas(
            self.root, height=10, bg=BAR_BG, highlightthickness=0
        )
        self.bar_canvas.place(relx=0.5, rely=0.86, anchor="center", relwidth=0.5)
        self.countdown = tk.Label(self.root, bg=BG, fg=DIM, font=("Menlo", 16))
        self.countdown.place(relx=0.5, rely=0.90, anchor="center")

        self.root.bind("<Key>", self._on_key)
        self.root.after(100, self._grab)
        self.root.after(0, self._tick)

    def run(self) -> Answers:
        self._render()
        self.root.mainloop()
        if self.result is None:  # mainloop ended without a decision
            self.result = self.session.timed_out(at=time.time())
        return self.result

    def _grab(self) -> None:
        self.root.focus_force()
        try:
            self.root.grab_set_global()
        except tk.TclError:
            pass  # not honoured on this platform; the overlay is still on top

    def _tick(self) -> None:
        remaining = self.prompt.timeout_s - (time.time() - self.session.shown_at)
        if remaining <= 0:
            self.result = self.session.timed_out(at=time.time())
            self.root.destroy()
            return

        self.countdown.configure(text=f"{format_countdown(remaining)} left")
        self.bar_canvas.delete("all")
        width = max(1, self.bar_canvas.winfo_width())
        filled = int(width * remaining / self.prompt.timeout_s)
        self.bar_canvas.create_rectangle(0, 0, filled, 10, fill=BAR_FG, width=0)
        self.root.after(200, self._tick)

    def _on_key(self, event: "tk.Event") -> None:
        if self.session.stage == FIELDS:
            if event.keysym == "Return":
                self._submit_fields()
            return
        if self.session.press_key(event.char.lower()):
            self._render()
            self._finish_if_done()

    def _submit_fields(self) -> None:
        values = {name: entry.get() for name, entry in self.entries.items()}
        missing = self.session.submit_fields(values)
        if missing:
            for name in missing:
                self.entries[name].configure(highlightbackground=WARN, highlightthickness=2)
            return
        self._finish_if_done()

    def _finish_if_done(self) -> None:
        if self.session.is_complete():
            self.result = self.session.answers(answered_at=time.time())
            self.root.destroy()

    def _render(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        self.entries.clear()

        self._label(self.prompt.title, size=18, colour=DIM)
        self._label("", size=8)
        self._label(self.prompt.question, size=32)
        self._label("", size=8)
        self._label(
            "   ".join(f"[{c.key}] {c.label}" for c in self.prompt.choices),
            size=22, colour=BAR_FG,
        )

        if self.session.stage == PICK:
            self._label("", size=8)
            self._label("which died?", size=18, colour=DIM)
            for index, text in enumerate(self.session.visible_pick_list() or [], start=1):
                self._label(f"{index}  {text}", size=20)

        if self.session.stage == FIELDS:
            for field in self.session.pending_fields():
                self._label("", size=6)
                self._label(field.label, size=18, colour=DIM)
                entry = tk.Entry(
                    self.body, font=("Menlo", 20), width=40,
                    bg=BAR_BG, fg=FG, insertbackground=FG, relief="flat",
                )
                entry.pack(pady=4)
                self.entries[field.name] = entry
            self._label("", size=6)
            self._label("press return to submit", size=14, colour=DIM)
            next(iter(self.entries.values())).focus_set()

        if self.prompt.warning:
            self._label("", size=12)
            self._label(self.prompt.warning, size=18, colour=WARN)

    def _label(self, text: str, *, size: int, colour: str = FG) -> None:
        tk.Label(
            self.body, text=text, bg=BG, fg=colour,
            font=("Menlo", size), justify="left",
        ).pack(anchor="w")
```

- [ ] **Step 6: Verify the module imports cleanly**

Run: `python -c "import loop.blockers.tk; print('ok')"`
Expected: `ok` (on a machine with tkinter available; on a bare Linux box install `python3-tk` first)

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest`
Expected: PASS, 140 tests

- [ ] **Step 8: Commit**

```bash
git add loop/blockers/session.py loop/blockers/tk.py tests/blockers/test_session.py
git commit -m "feat: add prompt session and tkinter overlay"
```

---

## Task 11: The tick — turning a `Due` into a prompt and back into events

**Files:**
- Create: `loop/sched/tick.py`
- Modify: `loop/cli.py` (add the `tick` subparser and `cmd_tick`)
- Test: `tests/sched/test_tick.py`

**Interfaces:**
- Consumes: `loop.core.{events,schedule,thrash,timefmt}`, `loop.blockers.{base,factory}`, `loop.store.jsonl`.
- Produces: `tick.build_prompt(state, loop, due) -> Prompt`, `tick.answers_to_events(loop, due, answers, now) -> list[dict]`, `tick.tick(now=None, blocker=None) -> Due | None`.

**Three decisions this task locks in, each a place the spec left room:**

1. **`checkpoint_answered` is always emitted before `scope_cut` or `budget_extended`.** `fold` keys a checkpoint on the loop's budget *at the time of the event*, so answering first marks `p75:2700` done and the extension then creates `p75:5400`, which has never been answered. Reversing the order would silently swallow the re-arm.
2. **The p100 `close` choice does not run the postmortem.** Three free-text answers under a five-minute countdown is the wrong place for the one thing that must be written carefully. The overlay records `decision="close"` and tells the user to run `loop close`; the checkpoint is marked answered so it does not re-fire.
3. **An unparseable budget on an extension emits `checkpoint_unanswered`.** The checkpoint then re-fires in ten minutes and the user gets another attempt. Guessing a number on their behalf would corrupt the one statistic the tool exists to produce.

- [ ] **Step 1: Write the failing tests**

`tests/sched/test_tick.py`:

```python
import pytest

from loop.blockers.base import Answers
from loop.blockers.fake import FakeBlocker
from loop.core import events, schedule
from loop.sched import tick as tick_module
from loop.store import jsonl

BUDGET = 2700.0
INTERVAL = 1200.0


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))


def answer(choice=None, picked=None, fields=None, timed_out=False):
    return Answers(
        timed_out=timed_out, choice=choice, picked=picked,
        fields=fields or {}, shown_at=0.0, answered_at=0.0,
    )


def seed(extra=(), budget_s=BUDGET):
    jsonl.append(
        events.make(
            "loop_opened", ts=0.0, loop_id=1,
            question="staging deploy fails at startup",
            stop_condition="comes up clean twice in a row",
            budget_s=budget_s, interval_s=INTERVAL,
            hypotheses=["cert expiry", "env var missing", "pg conn pool"],
            parent_id=None,
        )
    )
    for event in extra:
        jsonl.append(event)


def log():
    return jsonl.read_all()


def state():
    return events.fold(log())


def test_nothing_due_does_nothing():
    seed()
    blocker = FakeBlocker([answer(choice="n")])
    assert tick_module.tick(now=10.0, blocker=blocker) is None
    assert blocker.prompts == []
    assert len(log()) == 1


def test_ping_prompt_carries_title_question_and_live_hypotheses():
    seed([events.make("hypothesis_eliminated", ts=5.0, loop_id=1, hyp_id=2)])
    blocker = FakeBlocker([answer(choice="n")])
    tick_module.tick(now=INTERVAL, blocker=blocker)

    prompt = blocker.prompts[0]
    assert prompt.kind == "ping"
    assert prompt.title == "loop #1 · 20m / 45m"
    assert prompt.question == "hypothesis space smaller than 20m ago?"
    assert [c.key for c in prompt.choices] == ["y", "n"]
    assert prompt.pick_list == ["cert expiry", "pg conn pool"]
    assert prompt.pick_after == "y"
    assert prompt.warning is None
    assert prompt.timeout_s == 300.0


def test_ping_title_shows_paused_count():
    seed([
        events.make("loop_paused", ts=1.0, loop_id=1, reason="prod incident"),
        events.make(
            "loop_opened", ts=1.0, loop_id=2, question="prod 500s",
            stop_condition="s", budget_s=BUDGET, interval_s=INTERVAL,
            hypotheses=["a"], parent_id=1,
        ),
    ])
    blocker = FakeBlocker([answer(choice="n")])
    tick_module.tick(now=1.0 + INTERVAL, blocker=blocker)
    assert blocker.prompts[0].title.endswith("· 1 paused")


def test_ping_no_logs_a_single_event():
    seed()
    tick_module.tick(now=INTERVAL, blocker=FakeBlocker([answer(choice="n")]))
    last = log()[-1]
    assert last["type"] == "ping_answered"
    assert last["smaller"] is False
    assert last["eliminated"] is None
    assert state().loops[1].pings_answered == 1


def test_ping_yes_with_a_pick_also_eliminates_the_hypothesis():
    seed()
    tick_module.tick(now=INTERVAL, blocker=FakeBlocker([answer(choice="y", picked=3)]))

    kinds = [event["type"] for event in log()[-2:]]
    assert kinds == ["ping_answered", "hypothesis_eliminated"]
    assert log()[-2]["eliminated"] == 3
    assert log()[-1]["hyp_id"] == 3
    assert state().loops[1].eliminated == 1


def test_pick_indexes_the_live_list_not_the_full_list():
    seed([events.make("hypothesis_eliminated", ts=5.0, loop_id=1, hyp_id=1)])
    # live list is now [env var missing (2), pg conn pool (3)]; picking 1 means id 2
    tick_module.tick(now=INTERVAL, blocker=FakeBlocker([answer(choice="y", picked=1)]))
    assert log()[-1]["hyp_id"] == 2


def test_a_timed_out_ping_is_recorded_as_unanswered():
    seed()
    tick_module.tick(now=INTERVAL, blocker=FakeBlocker([answer(timed_out=True)]))
    assert log()[-1]["type"] == "ping_unanswered"
    assert state().loops[1].pings_timed_out == 1


def test_the_thrash_panel_rides_the_ping_prompt():
    actions = [
        events.make("action_logged", ts=float(index), loop_id=1,
                    action=f"a{index}", because="b")
        for index in range(1, 8)
    ]
    # a long budget keeps the checkpoints out of the way; this test is about the ping
    seed(actions, budget_s=99_999.0)
    blocker = FakeBlocker([answer(choice="n")])
    tick_module.tick(now=3120.0, blocker=blocker)  # 52m elapsed

    assert blocker.prompts[0].kind == "ping"
    assert "you are thrashing" in blocker.prompts[0].warning
    assert "7 actions, 0 ruled out, 52m elapsed" in blocker.prompts[0].warning


def test_p75_prompt_offers_continue_cut_and_extend():
    seed([events.make("ping_answered", ts=2020.0, loop_id=1, smaller=True,
                      eliminated=None, shown_at=2020.0)])
    blocker = FakeBlocker([answer(choice="y")])
    tick_module.tick(now=2025.0, blocker=blocker)

    prompt = blocker.prompts[0]
    assert prompt.kind == "p75"
    assert prompt.title == "75% of budget gone · 33m / 45m"
    assert prompt.question == "is 75% of the work done?"
    assert [c.key for c in prompt.choices] == ["y", "c", "e"]
    assert [f.name for f in prompt.fields_after["c"]] == ["new_stop_condition"]
    assert [f.name for f in prompt.fields_after["e"]] == ["new_budget", "learned"]
    assert log()[-2]["type"] == "checkpoint_shown"
    assert log()[-1] == {
        "type": "checkpoint_answered", "ts": log()[-1]["ts"], "loop_id": 1,
        "kind": "p75", "on_track": True, "decision": "continue",
    }


def test_cutting_scope_records_both_conditions():
    seed([events.make("ping_answered", ts=2020.0, loop_id=1, smaller=True,
                      eliminated=None, shown_at=2020.0)])
    blocker = FakeBlocker(
        [answer(choice="c", fields={"new_stop_condition": "comes up once"})]
    )
    tick_module.tick(now=2025.0, blocker=blocker)

    assert log()[-2]["decision"] == "cut"
    assert log()[-2]["on_track"] is False
    assert log()[-1] == {
        "type": "scope_cut", "ts": log()[-1]["ts"], "loop_id": 1,
        "old_stop_condition": "comes up clean twice in a row",
        "new_stop_condition": "comes up once",
    }
    assert state().loops[1].stop_condition == "comes up once"


def test_extending_records_the_new_budget_and_rearms_the_checkpoints():
    seed([events.make("ping_answered", ts=2020.0, loop_id=1, smaller=True,
                      eliminated=None, shown_at=2020.0)])
    blocker = FakeBlocker(
        [answer(choice="e", fields={"new_budget": "90m", "learned": "pool is fine"})]
    )
    tick_module.tick(now=2025.0, blocker=blocker)

    assert [event["type"] for event in log()[-2:]] == [
        "checkpoint_answered", "budget_extended",
    ]
    assert log()[-1]["old_budget_s"] == BUDGET
    assert log()[-1]["new_budget_s"] == 5400.0
    assert log()[-1]["learned"] == "pool is fine"

    current = state()
    assert current.loops[1].budget_s == 5400.0
    assert schedule.next_due(current, now=4050.0).kind == "p75"


def test_an_unparseable_budget_defers_the_checkpoint():
    seed([events.make("ping_answered", ts=2020.0, loop_id=1, smaller=True,
                      eliminated=None, shown_at=2020.0)])
    blocker = FakeBlocker(
        [answer(choice="e", fields={"new_budget": "soon", "learned": "x"})]
    )
    tick_module.tick(now=2025.0, blocker=blocker)

    assert log()[-1]["type"] == "checkpoint_unanswered"
    assert state().loops[1].budget_s == BUDGET
    assert schedule.next_due(state(), now=2025.0 + 600.0).kind == "p75"


def test_p100_offers_close_and_records_it_without_a_postmortem():
    seed([events.make("ping_answered", ts=2690.0, loop_id=1, smaller=True,
                      eliminated=None, shown_at=2690.0)])
    blocker = FakeBlocker([answer(choice="x")])
    tick_module.tick(now=BUDGET, blocker=blocker)

    prompt = blocker.prompts[0]
    assert prompt.kind == "p100"
    assert [c.key for c in prompt.choices] == ["x", "c", "e"]
    assert log()[-1]["decision"] == "close"
    # the loop stays open; the postmortem happens in the terminal, not here
    assert state().loops[1].status == "active"
    assert schedule.next_due(state(), now=BUDGET + 1.0) is None


def test_a_timed_out_checkpoint_is_recorded_and_refires():
    seed([events.make("ping_answered", ts=2690.0, loop_id=1, smaller=True,
                      eliminated=None, shown_at=2690.0)])
    tick_module.tick(now=BUDGET, blocker=FakeBlocker([answer(timed_out=True)]))
    assert log()[-1]["type"] == "checkpoint_unanswered"
    assert schedule.next_due(state(), now=BUDGET + 599.0) is None
    assert schedule.next_due(state(), now=BUDGET + 600.0).kind == "p100"
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/sched/test_tick.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.sched.tick'`

- [ ] **Step 3: Implement `loop/sched/tick.py`**

```python
"""One scheduler tick: ask core what is due, render it, write the answer down.

This is one of only two places (with `cli.py`) that is allowed to touch
core, blockers, and the store in the same breath.
"""

from __future__ import annotations

import time

from loop.blockers.base import Answers, Choice, Prompt, TextField
from loop.blockers.factory import get_blocker
from loop.core import events, schedule, thrash
from loop.core.models import Loop, State
from loop.core.timefmt import format_duration, parse_duration
from loop.store import jsonl

PING_QUESTION = "hypothesis space smaller than 20m ago?"

CUT_FIELDS = [TextField("new_stop_condition", "new stop condition")]
EXTEND_FIELDS = [
    TextField("new_budget", "new budget"),
    TextField("learned", "what did you learn that made it bigger?"),
]


def build_prompt(state: State, loop: Loop, due: schedule.Due) -> Prompt:
    span = f"{format_duration(due.elapsed)} / {format_duration(loop.budget_s)}"

    if due.kind == "ping":
        paused = events.stack_depth(state) - 1
        title = f"loop #{loop.id} · {span}"
        if paused > 0:
            title += f" · {paused} paused"
        detected = thrash.detect(loop, due.elapsed)
        return Prompt(
            kind="ping",
            title=title,
            question=PING_QUESTION,
            choices=[Choice("y", "yes"), Choice("n", "no")],
            pick_list=[h.text for h in loop.live_hypotheses()],
            pick_after="y",
            warning=detected.panel,
        )

    if due.kind == "p75":
        return Prompt(
            kind="p75",
            title=f"75% of budget gone · {span}",
            question="is 75% of the work done?",
            choices=[
                Choice("y", "yes"),
                Choice("c", "cut scope"),
                Choice("e", "extend estimate"),
            ],
            fields_after={"c": CUT_FIELDS, "e": EXTEND_FIELDS},
        )

    return Prompt(
        kind="p100",
        title=f"budget gone · {span}",
        question="budget is gone. what now?",
        choices=[
            Choice("x", "stop now — then run `loop close`"),
            Choice("c", "cut scope"),
            Choice("e", "extend estimate"),
        ],
        fields_after={"c": CUT_FIELDS, "e": EXTEND_FIELDS},
    )


def answers_to_events(loop: Loop, due: schedule.Due, answers: Answers, now: float) -> list[dict]:
    if due.kind == "ping":
        return _ping_events(loop, answers, now)
    return _checkpoint_events(loop, due, answers, now)


def _ping_events(loop: Loop, answers: Answers, now: float) -> list[dict]:
    if answers.timed_out:
        return [
            events.make("ping_unanswered", ts=now, loop_id=loop.id,
                        shown_at=answers.shown_at)
        ]

    smaller = answers.choice == "y"
    hyp_id = None
    if smaller and answers.picked is not None:
        live = loop.live_hypotheses()
        if 1 <= answers.picked <= len(live):
            hyp_id = live[answers.picked - 1].id

    written = [
        events.make("ping_answered", ts=now, loop_id=loop.id, smaller=smaller,
                    eliminated=hyp_id, shown_at=answers.shown_at)
    ]
    if hyp_id is not None:
        written.append(
            events.make("hypothesis_eliminated", ts=now, loop_id=loop.id, hyp_id=hyp_id)
        )
    return written


def _checkpoint_events(loop: Loop, due: schedule.Due, answers: Answers, now: float) -> list[dict]:
    deferred = [
        events.make("checkpoint_unanswered", ts=now, loop_id=loop.id,
                    kind=due.kind, shown_at=answers.shown_at)
    ]
    if answers.timed_out:
        return deferred

    if answers.choice == "c":
        return _answered(loop, due, now, decision="cut", on_track=False) + [
            events.make("scope_cut", ts=now, loop_id=loop.id,
                        old_stop_condition=loop.stop_condition,
                        new_stop_condition=answers.fields["new_stop_condition"])
        ]

    if answers.choice == "e":
        try:
            new_budget_s = parse_duration(answers.fields["new_budget"])
        except ValueError:
            return deferred  # re-fires in ten minutes; never guess the number
        return _answered(loop, due, now, decision="extend", on_track=False) + [
            events.make("budget_extended", ts=now, loop_id=loop.id,
                        old_budget_s=loop.budget_s, new_budget_s=new_budget_s,
                        learned=answers.fields["learned"])
        ]

    decision = "close" if answers.choice == "x" else "continue"
    return _answered(loop, due, now, decision=decision, on_track=decision == "continue")


def _answered(loop: Loop, due: schedule.Due, now: float, *, decision: str, on_track: bool) -> list[dict]:
    """Always first in the returned list: fold keys the checkpoint on the
    budget in force at this instant, which is what makes an extension re-arm."""
    return [
        events.make("checkpoint_answered", ts=now, loop_id=loop.id,
                    kind=due.kind, on_track=on_track, decision=decision)
    ]


def tick(now: float | None = None, blocker=None) -> schedule.Due | None:
    at = time.time() if now is None else now
    state = events.fold(jsonl.read_all())

    due = schedule.next_due(state, at)
    if due is None:
        return None

    loop = state.loops[due.loop_id]
    prompt = build_prompt(state, loop, due)

    if due.kind != "ping":
        jsonl.append(events.make("checkpoint_shown", ts=at, loop_id=loop.id, kind=due.kind))

    answers = (blocker or get_blocker()).ask(prompt)
    for event in answers_to_events(loop, due, answers, time.time() if now is None else now):
        jsonl.append(event)
    return due
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/sched/test_tick.py -v`
Expected: PASS, 15 tests

- [ ] **Step 5: Expose `loop tick` on the CLI**

In `build_parser`, before the `status` subparser:

```python
    subparsers.add_parser("tick", help="internal: run one scheduler tick")
```

Add the command function before the `COMMANDS` table:

```python
def cmd_tick(args, state: State, now: float) -> dict:
    from loop.sched.tick import tick

    due = tick(now=None)
    return {"fired": None if due is None else due.kind}
```

Add `"tick": cmd_tick,` to `COMMANDS`.

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest`
Expected: PASS, 155 tests

- [ ] **Step 7: Commit**

```bash
git add loop/sched/tick.py loop/cli.py tests/sched/test_tick.py
git commit -m "feat: add scheduler tick and check-in prompts"
```

---

## Task 12: The daemon

**Files:**
- Create: `loop/sched/daemon.py`
- Modify: `loop/cli.py` (call `daemon.ensure_running()` from `cmd_open` and `cmd_resume`)
- Test: `tests/sched/test_daemon.py`

**Interfaces:**
- Consumes: `loop.sched.tick`, `loop.core.events`, `loop.store.{jsonl,paths}`.
- Produces: `daemon.POLL_S = 10.0`, `daemon.STALE_AFTER_S`, `daemon.is_running() -> bool`, `daemon.ensure_running() -> None`, `daemon.stop() -> None`, `daemon.run(*, poll_s=POLL_S, blocker=None, sleep_fn=time.sleep, now_fn=time.time) -> None`.

`sleep_fn` and `now_fn` are injected so the tests drive a fake clock. There is no iteration cap — the daemon's only exit conditions are "no loop is active" and "the pidfile is no longer mine", and a test that needs it to stop makes one of those true.

**Liveness is a heartbeat, not a signal.** The pidfile holds `{"pid": ..., "heartbeat": ...}` and the daemon rewrites it every tick. `is_running()` is "the file exists and its heartbeat is younger than three polls." This avoids `os.kill(pid, 0)`, which does not work on Windows, and keeps the spawn call the only platform branch outside `blockers/` — exactly as the spec requires.

**Shutdown is the pidfile too.** Each tick the daemon checks the file still names its own PID; if it is gone or claimed by someone else, it exits. So `stop()` is a file deletion and needs no signals.

- [ ] **Step 1: Write the failing tests**

`tests/sched/test_daemon.py`:

```python
import json

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
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/sched/test_daemon.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.sched.daemon'`

- [ ] **Step 3: Implement `loop/sched/daemon.py`**

```python
"""The detached poller.

A singleton, not bound to a loop id: it services whatever loop is active
and exits when none is. It holds no state between ticks — every tick
re-reads the log — so pausing, resuming, stacking, and closing from any
terminal all work without signalling it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from loop.core import events
from loop.sched.tick import tick
from loop.store import jsonl, paths

POLL_S = 10.0
STALE_AFTER_S = 3 * POLL_S


def is_running() -> bool:
    record = _read_pidfile()
    if record is None:
        return False
    return time.time() - record.get("heartbeat", 0.0) < STALE_AFTER_S


def ensure_running() -> None:
    if not is_running():
        _spawn()


def stop() -> None:
    paths.pid_path().unlink(missing_ok=True)


def run(
    *,
    poll_s: float = POLL_S,
    blocker=None,
    sleep_fn=time.sleep,
    now_fn=time.time,
) -> None:
    pid = os.getpid()
    try:
        while True:
            state = events.fold(jsonl.read_all())
            if state.active_id is None:
                return

            _write_heartbeat(pid, now_fn())
            tick(now=now_fn(), blocker=blocker)
            sleep_fn(poll_s)

            if not _owns_pidfile(pid):
                return
    finally:
        if _owns_pidfile(pid):
            stop()


def _spawn() -> None:
    """The only platform branch outside `loop/blockers/`."""
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

    subprocess.Popen([sys.executable, "-m", "loop.sched.daemon"], **kwargs)


def _read_pidfile() -> dict | None:
    path = paths.pid_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_heartbeat(pid: int, at: float) -> None:
    paths.pid_path().write_text(
        json.dumps({"pid": pid, "heartbeat": at}), encoding="utf-8"
    )


def _owns_pidfile(pid: int) -> bool:
    record = _read_pidfile()
    return record is not None and record.get("pid") == pid


if __name__ == "__main__":  # pragma: no cover
    run()
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/sched/test_daemon.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Start the daemon from `open` and `resume`**

In `loop/cli.py`, add the import:

```python
from loop.sched import daemon
```

At the end of `cmd_open`, immediately before `return event`:

```python
    daemon.ensure_running()
```

At the end of `cmd_resume`, immediately before `return event`:

```python
    daemon.ensure_running()
```

- [ ] **Step 6: Stop the CLI tests from spawning real daemons**

Add to both `tests/test_cli_open.py` and `tests/test_cli_stack.py`, inside the existing `home` fixture:

```python
    monkeypatch.setattr("loop.sched.daemon.ensure_running", lambda: None)
```

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest`
Expected: PASS, 166 tests

- [ ] **Step 8: Walking skeleton — drive it by hand**

This is the moment the project's central question gets answered, so do it for real rather than trusting the tests.

```bash
export LOOP_HOME=/tmp/loop-smoke
export LOOP_BLOCKER=tk
python -m loop.cli open "smoke test the walking skeleton" --budget 4m --interval 1m
# answer the prompts, then wait one minute for the overlay
python -m loop.cli status
python -m loop.cli close
```

Confirm: the overlay appears unprompted after a minute, `y` reveals the hypothesis list, the countdown bar drains, and closing the loop makes the daemon exit within ten seconds (`ls /tmp/loop-smoke/daemon.pid` is gone).

- [ ] **Step 9: Commit**

```bash
git add loop/sched/daemon.py loop/cli.py tests/sched/test_daemon.py tests/test_cli_open.py tests/test_cli_stack.py
git commit -m "feat: add detached tick daemon"
```

---

## Task 13: The macOS hard block

**Files:**
- Create: `loop/blockers/macos.py`, `docs/manual-smoke-checklist.md`
- Test: none automated — see the checklist. This is stated plainly rather than faked with a mock that proves nothing.

**Interfaces:**
- Consumes: `loop.blockers.base`, `loop.blockers.session`, PyObjC.
- Produces: `macos.MacOSBlocker`.

**The three escape guarantees, and why each is separate:**

1. The countdown timer ends the overlay at `TIMEOUT_S` and returns a timed-out answer.
2. A second `NSTimer`, scheduled at `KILL_AFTER_S` and sharing no state with the first, calls `os._exit(1)`. It must remain a genuinely independent timer — not a branch inside the countdown callback — because its entire purpose is to fire when the countdown handler is wedged.
3. The daemon is an unprivileged user process, so `pkill -f loop.sched.daemon` over SSH ends it and any reboot clears it.

- [ ] **Step 1: Install the optional dependency**

```bash
pip install 'pyobjc-framework-Cocoa>=10.0' 'pyobjc-framework-Quartz>=10.0'
```

- [ ] **Step 2: Implement `loop/blockers/macos.py`**

```python
"""The genuine block: a shielding window on every display, Cmd-Tab disabled.

Imports PyObjC at module import time, not at fire time, so that the daemon
pays the half-second cost at startup and the overlay appears instantly when
it is due.
"""

from __future__ import annotations

import os
import time

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSApplicationPresentationDisableForceQuit,
    NSApplicationPresentationDisableProcessSwitching,
    NSApplicationPresentationDisableSessionTermination,
    NSApplicationPresentationHideDock,
    NSApplicationPresentationHideMenuBar,
    NSBackingStoreBuffered,
    NSBorderlessWindowMask,
    NSColor,
    NSEventMaskKeyDown,
    NSEvent,
    NSFont,
    NSProgressIndicator,
    NSProgressIndicatorBarStyle,
    NSScreen,
    NSTextField,
    NSTimer,
    NSView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
)
from Quartz import CGShieldingWindowLevel

from loop.blockers.base import KILL_AFTER_S, Answers, Prompt, format_countdown
from loop.blockers.session import FIELDS, PICK, PromptSession

PRESENTATION_OPTIONS = (
    NSApplicationPresentationHideDock
    | NSApplicationPresentationHideMenuBar
    | NSApplicationPresentationDisableProcessSwitching
    | NSApplicationPresentationDisableForceQuit
    | NSApplicationPresentationDisableSessionTermination
)

BG = (0.063, 0.063, 0.078, 0.97)
FG = (0.90, 0.90, 0.90, 1.0)
DIM = (0.54, 0.54, 0.58, 1.0)
WARN = (1.0, 0.37, 0.34, 1.0)
ACCENT = (0.48, 0.635, 0.968, 1.0)


class MacOSBlocker:
    def ask(self, prompt: Prompt) -> Answers:
        return _Overlay(prompt).run()


class _Overlay:
    def __init__(self, prompt: Prompt) -> None:
        self.prompt = prompt
        self.session = PromptSession(prompt, shown_at=time.time())
        self.result: Answers | None = None
        self.windows: list[NSWindow] = []
        self.field_views: dict[str, NSTextField] = {}
        self.body: NSTextField | None = None
        self.bar: NSProgressIndicator | None = None
        self.countdown: NSTextField | None = None
        self.monitor = None
        self.app = NSApplication.sharedApplication()

    # -- lifecycle ---------------------------------------------------------

    def run(self) -> Answers:
        self.app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        self._build_windows()
        self._render()

        self.app.setPresentationOptions_(PRESENTATION_OPTIONS)
        self.app.activateIgnoringOtherApps_(True)

        self.monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown, self._on_key
        )
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.2, True, self._on_countdown)
        # Guarantee 2: shares no state with the countdown, so it fires even if
        # the countdown handler is wedged. Never fold this into _on_countdown.
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            KILL_AFTER_S, False, lambda _timer: os._exit(1)
        )

        self.app.run()

        if self.result is None:
            self.result = self.session.timed_out(at=time.time())
        return self.result

    def _finish(self, answers: Answers) -> None:
        self.result = answers
        if self.monitor is not None:
            NSEvent.removeMonitor_(self.monitor)
            self.monitor = None
        self.app.setPresentationOptions_(0)
        for window in self.windows:
            window.orderOut_(None)
        self.windows.clear()
        self.app.stop_(None)
        # stop_ only takes effect after the next event, so post one.
        NSApplication.sharedApplication().postEvent_atStart_(_wake_event(), True)

    # -- windows -----------------------------------------------------------

    def _build_windows(self) -> None:
        main_screen = NSScreen.mainScreen()
        for screen in NSScreen.screens():
            window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_screen_(
                screen.frame(), NSBorderlessWindowMask, NSBackingStoreBuffered, False, screen
            )
            window.setLevel_(CGShieldingWindowLevel())
            window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorStationary
                | NSWindowCollectionBehaviorFullScreenAuxiliary
            )
            window.setOpaque_(False)
            window.setBackgroundColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(*BG))
            window.setIgnoresMouseEvents_(True)

            content = NSView.alloc().initWithFrame_(screen.frame())
            window.setContentView_(content)

            if screen == main_screen:
                self._build_controls(content, screen.frame())

            window.makeKeyAndOrderFront_(None)
            self.windows.append(window)

    def _build_controls(self, content: NSView, frame) -> None:
        width = frame.size.width * 0.7
        left = (frame.size.width - width) / 2

        self.body = _label(
            ((left, frame.size.height * 0.28), (width, frame.size.height * 0.55)),
            size=22, colour=FG,
        )
        content.addSubview_(self.body)

        self.bar = NSProgressIndicator.alloc().initWithFrame_(
            ((left, frame.size.height * 0.16), (width, 12))
        )
        self.bar.setStyle_(NSProgressIndicatorBarStyle)
        self.bar.setIndeterminate_(False)
        self.bar.setMinValue_(0.0)
        self.bar.setMaxValue_(self.prompt.timeout_s)
        content.addSubview_(self.bar)

        self.countdown = _label(
            ((left, frame.size.height * 0.11), (width, 26)), size=16, colour=DIM
        )
        content.addSubview_(self.countdown)

    # -- rendering ---------------------------------------------------------

    def _render(self) -> None:
        lines = [self.prompt.title, "", self.prompt.question, ""]
        lines.append("   ".join(f"[{c.key}] {c.label}" for c in self.prompt.choices))

        if self.session.stage == PICK:
            lines += ["", "which died?"]
            lines += [
                f"  {index}  {text}"
                for index, text in enumerate(self.session.visible_pick_list() or [], start=1)
            ]

        if self.session.stage == FIELDS:
            lines.append("")
            self._show_fields()
            lines.append("type your answer, then press return")

        if self.prompt.warning:
            lines += ["", self.prompt.warning]

        self.body.setStringValue_("\n".join(lines))

    def _show_fields(self) -> None:
        if self.field_views:
            return
        content = self.windows[0].contentView()
        frame = content.frame()
        width = frame.size.width * 0.7
        left = (frame.size.width - width) / 2

        for index, field in enumerate(self.session.pending_fields()):
            y = frame.size.height * 0.26 - index * 76
            content.addSubview_(_label(((left, y + 34), (width, 22)), size=15, colour=DIM,
                                       text=field.label))
            entry = NSTextField.alloc().initWithFrame_(((left, y), (width, 30)))
            entry.setFont_(NSFont.monospacedSystemFontOfSize_weight_(18, 0))
            entry.setBezeled_(True)
            entry.setEditable_(True)
            content.addSubview_(entry)
            self.field_views[field.name] = entry

        first = next(iter(self.field_views.values()))
        self.windows[0].makeFirstResponder_(first)

    # -- events ------------------------------------------------------------

    def _on_countdown(self, _timer) -> None:
        remaining = self.prompt.timeout_s - (time.time() - self.session.shown_at)
        if remaining <= 0:
            self._finish(self.session.timed_out(at=time.time()))
            return
        self.bar.setDoubleValue_(remaining)
        self.countdown.setStringValue_(f"{format_countdown(remaining)} left")

    def _on_key(self, event):
        if self.session.stage == FIELDS:
            if event.keyCode() == 36:  # return
                values = {
                    name: str(view.stringValue())
                    for name, view in self.field_views.items()
                }
                if not self.session.submit_fields(values):
                    self._finish(self.session.answers(answered_at=time.time()))
                return None
            return event  # let the text field have the keystroke

        if self.session.press_key(str(event.characters() or "").lower()):
            self._render()
            if self.session.is_complete():
                self._finish(self.session.answers(answered_at=time.time()))
        return None  # swallow everything else


def _label(rect, *, size: float, colour, text: str = "") -> NSTextField:
    view = NSTextField.alloc().initWithFrame_(rect)
    view.setStringValue_(text)
    view.setBezeled_(False)
    view.setDrawsBackground_(False)
    view.setEditable_(False)
    view.setSelectable_(False)
    view.setFont_(NSFont.monospacedSystemFontOfSize_weight_(size, 0))
    view.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(*colour))
    return view


def _wake_event():
    from AppKit import NSApplicationDefined, NSEvent, NSPoint

    return NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
        NSApplicationDefined, NSPoint(0, 0), 0, 0, 0, None, 0, 0, 0
    )
```

- [ ] **Step 3: Confirm the module imports and the factory picks it**

Run:

```bash
python -c "from loop.blockers.factory import get_blocker; print(type(get_blocker()).__name__)"
```

Expected: `MacOSBlocker` on macOS with PyObjC installed. If it prints `TkBlocker` and a "soft block" notice, PyObjC is missing — that fallback is correct behaviour, but Step 4 needs the real one.

- [ ] **Step 4: Write `docs/manual-smoke-checklist.md`**

```markdown
# Manual smoke checklist

The overlays cannot be honestly automated — a full-screen shielding window
has no assertion surface. Run this by hand after touching
`loop/blockers/macos.py` or `loop/blockers/tk.py`.

Set up a throwaway log so the real one stays clean:

```bash
export LOOP_HOME=/tmp/loop-smoke
export LOOP_BLOCKER=macos     # or tk
python -m loop.cli open "smoke test" --budget 4m --interval 1m
```

## The 20-minute ping (here: 1 minute)

- [ ] The overlay appears without being asked for, within ten seconds of the interval.
- [ ] It covers **every** display. Check with an external monitor attached.
- [ ] It stays on top after switching Spaces with Ctrl-→.
- [ ] The menu bar and Dock are hidden.
- [ ] **Cmd-Tab does nothing.** This is the one that matters.
- [ ] Cmd-Q does nothing.
- [ ] The countdown bar drains smoothly and the label counts down `5:00 → 0:00`.
- [ ] Pressing `n` dismisses it instantly and writes `ping_answered` with `smaller: false`.
- [ ] Pressing `y` reveals the numbered live hypotheses; pressing `2` dismisses and
      writes both `ping_answered` and `hypothesis_eliminated`.
- [ ] Ignoring it entirely dismisses at 5:00 and writes `ping_unanswered`.

## The 75% checkpoint (here: 3 minutes)

- [ ] Fires at 75% of the budget, and the ping due near it is suppressed.
- [ ] `y` dismisses and logs `decision: continue`.
- [ ] `c` reveals one text field; submitting writes `checkpoint_answered` **then**
      `scope_cut`, in that order.
- [ ] `e` reveals two text fields; the overlay refuses to submit while `learned`
      is blank, and the field is highlighted.
- [ ] After extending to 8m, both p75 and p100 fire again against the new budget.
- [ ] Typing a nonsense budget such as `soon` logs `checkpoint_unanswered`,
      and the checkpoint returns ten minutes later.

## The 100% checkpoint

- [ ] Fires when the budget is exhausted, offering `x` / `c` / `e`.
- [ ] `x` logs `decision: close` and does **not** fire again.

## Escape guarantees

- [ ] With the overlay up, `pkill -f loop.sched.daemon` from another machine
      over SSH clears the screen and restores the menu bar and Dock.
- [ ] Add `time.sleep(600)` to the top of `_on_countdown`, rebuild, and confirm
      the independent kill timer terminates the process at 5:10. **Remove the
      sleep afterwards.**

## Cleanup

```bash
rm -rf /tmp/loop-smoke
```
```

- [ ] **Step 5: Run the checklist**

Work through `docs/manual-smoke-checklist.md` on a machine with an external display attached. Fix anything that fails before committing.

- [ ] **Step 6: Commit**

```bash
git add loop/blockers/macos.py docs/manual-smoke-checklist.md
git commit -m "feat: add macos hard block overlay"
```

---

## Task 14: `stats` and `grep`

**Files:**
- Create: `loop/core/stats.py`
- Modify: `loop/core/events.py` (rename `_apply` to `apply` and export it), `loop/cli.py` (add `stats` and `grep`)
- Test: `tests/core/test_stats.py`, `tests/test_cli_stats.py`

**Interfaces:**
- Consumes: `loop.core.{events,models,thrash,timefmt}`.
- Produces: `stats.compute(log: list[dict], now: float) -> dict`, `stats.render(report: dict) -> list[str]`; `events.apply(state, event) -> None`.

**A spec correction, made here deliberately.** §11 of the spec defines *followed* as "every ping answered with no timeouts, **and** closed via `loop close`", and separately reports a "% resolved" for each bucket. Those two cannot both hold: if being closed is a precondition of *followed*, then *followed* is 100% resolved by construction and the column is dead. The buckets are therefore split along protocol adherence only, and "resolved" measures the ending:

- **followed** — no ping timeouts and no checkpoint timeouts, however the loop ended.
- **abandoned** — at least one timeout of either kind.
- **resolved** — ended via `loop close` with a postmortem, rather than `loop abandon`.

Both percentages now carry information, and the classification stays mechanical.

- [ ] **Step 1: Export `apply` from `loop/core/events.py`**

Rename `_apply` to `apply` and update the call inside `fold`:

```python
def fold(events: list[dict]) -> State:
    state = State(loops={}, active_id=None)
    for event in events:
        apply(state, event)
    return state


def apply(state: State, event: dict) -> None:
```

`stats` replays the log one event at a time to find thrash episodes, and needs the same stepper `fold` uses rather than a second copy of it.

- [ ] **Step 2: Write the failing test**

`tests/core/test_stats.py`:

```python
from loop.core import events, stats

BUDGET = 2700.0
INTERVAL = 1200.0


def opened(loop_id, ts, budget_s=BUDGET, parent_id=None):
    return events.make(
        "loop_opened", ts=ts, loop_id=loop_id, question=f"q{loop_id}",
        stop_condition="s", budget_s=budget_s, interval_s=INTERVAL,
        hypotheses=["a", "b", "c"], parent_id=parent_id,
    )


def ping(loop_id, ts, smaller=True):
    return events.make("ping_answered", ts=ts, loop_id=loop_id, smaller=smaller,
                       eliminated=None, shown_at=ts)


def closed(loop_id, ts):
    return events.make("loop_closed", ts=ts, loop_id=loop_id, what_was_it="w",
                       giveaway="g", five_min_path="p")


def test_empty_log_reports_zeroes():
    report = stats.compute([], now=0.0)
    assert report["followed"]["count"] == 0
    assert report["abandoned"]["count"] == 0
    assert report["ping_response"] == {"answered": 0, "timed_out": 0}


def test_a_clean_closed_loop_is_followed_and_resolved():
    log = [opened(1, 0.0), ping(1, 1200.0), closed(1, 1800.0)]
    report = stats.compute(log, now=1800.0)
    assert report["followed"]["count"] == 1
    assert report["followed"]["median_s"] == 1800.0
    assert report["followed"]["resolved_pct"] == 100.0
    assert report["abandoned"]["count"] == 0


def test_a_ping_timeout_moves_the_loop_to_abandoned_protocol():
    log = [
        opened(1, 0.0),
        events.make("ping_unanswered", ts=1200.0, loop_id=1, shown_at=1200.0),
        closed(1, 1800.0),
    ]
    report = stats.compute(log, now=1800.0)
    assert report["followed"]["count"] == 0
    assert report["abandoned"]["count"] == 1
    assert report["abandoned"]["resolved_pct"] == 100.0


def test_a_checkpoint_timeout_also_counts_as_abandoned_protocol():
    log = [
        opened(1, 0.0),
        events.make("checkpoint_unanswered", ts=2025.0, loop_id=1, kind="p75",
                    shown_at=2025.0),
        events.make("loop_abandoned", ts=2100.0, loop_id=1),
    ]
    report = stats.compute(log, now=2100.0)
    assert report["abandoned"]["count"] == 1
    assert report["abandoned"]["resolved_pct"] == 0.0


def test_a_loop_with_zero_pings_is_still_classified():
    report = stats.compute([opened(1, 0.0), closed(1, 300.0)], now=300.0)
    assert report["followed"]["count"] == 1


def test_open_loops_are_excluded():
    assert stats.compute([opened(1, 0.0)], now=600.0)["followed"]["count"] == 0


def test_median_uses_active_elapsed_not_wall_clock():
    log = [
        opened(1, 0.0),
        events.make("loop_paused", ts=600.0, loop_id=1, reason="lunch"),
        events.make("loop_resumed", ts=200_000.0, loop_id=1),
        closed(1, 200_600.0),
    ]
    assert stats.compute(log, now=200_600.0)["followed"]["median_s"] == 1200.0


def test_estimate_drift_measures_the_original_budget():
    log = [
        opened(1, 0.0),
        events.make("checkpoint_answered", ts=2025.0, loop_id=1, kind="p75",
                    on_track=False, decision="extend"),
        events.make("budget_extended", ts=2025.0, loop_id=1, old_budget_s=BUDGET,
                    new_budget_s=5400.0, learned="x"),
        closed(1, 5400.0),
    ]
    drift = stats.compute(log, now=5400.0)["estimate_drift"]
    assert drift["median_pct"] == 100.0          # 5400s actual against a 2700s promise
    assert drift["extensions"] == 1
    assert drift["loops_with_extensions"] == 1


def test_ping_response_totals_across_loops():
    log = [
        opened(1, 0.0), ping(1, 1200.0),
        events.make("ping_unanswered", ts=2400.0, loop_id=1, shown_at=2400.0),
        closed(1, 2500.0),
    ]
    assert stats.compute(log, now=2500.0)["ping_response"] == {
        "answered": 1, "timed_out": 1,
    }


def test_interruption_metrics():
    log = [
        opened(1, 0.0),
        events.make("loop_paused", ts=600.0, loop_id=1, reason="prod"),
        opened(2, 600.0, parent_id=1),
        events.make("loop_closed", ts=900.0, loop_id=2, what_was_it="w",
                    giveaway="g", five_min_path="p"),
        events.make("loop_resumed", ts=900.0, loop_id=1),
        closed(1, 1200.0),
    ]
    interruptions = stats.compute(log, now=1200.0)["interruptions"]
    assert interruptions["median_pauses"] == 0.5   # loop 1 paused once, loop 2 never
    assert interruptions["median_pause_s"] == 300.0
    assert interruptions["max_depth"] == 2


def test_thrash_episodes_count_contiguous_stretches_not_pings():
    actions = [
        events.make("action_logged", ts=float(index), loop_id=1,
                    action=f"a{index}", because="b")
        for index in range(1, 8)
    ]
    log = [opened(1, 0.0, budget_s=99_999.0), *actions]
    log += [ping(1, 1900.0, smaller=False), ping(1, 3100.0, smaller=False)]
    log += [events.make("hypothesis_eliminated", ts=3200.0, loop_id=1, hyp_id=1)]
    log += [ping(1, 4400.0, smaller=True), closed(1, 4500.0)]

    episodes = stats.compute(log, now=4500.0)["thrash_episodes"]
    assert episodes["total"] == 1


def test_render_produces_the_documented_lines():
    lines = stats.render(stats.compute([opened(1, 0.0), closed(1, 600.0)], now=600.0))
    joined = "\n".join(lines)
    for label in (
        "protocol followed", "protocol abandoned", "thrash episodes",
        "estimate drift", "ping response", "interruptions",
    ):
        assert label in joined
```

- [ ] **Step 3: Run it and confirm it fails**

Run: `python -m pytest tests/core/test_stats.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loop.core.stats'`

- [ ] **Step 4: Implement `loop/core/stats.py`**

```python
"""The logbook's read side.

Classification is mechanical on purpose: a number you cannot argue with
later is the entire reason the log is worth keeping.
"""

from __future__ import annotations

from statistics import median

from loop.core import events as events_module
from loop.core import thrash
from loop.core.models import ABANDONED, CLOSED, State
from loop.core.timefmt import format_duration


def compute(log: list[dict], now: float) -> dict:
    state = State(loops={}, active_id=None)
    episodes = 0
    firing = {}
    max_depth = 0

    for event in log:
        events_module.apply(state, event)
        max_depth = max(max_depth, events_module.stack_depth(state))

        loop_id = event["loop_id"]
        if loop_id is None:
            continue
        loop = state.loops[loop_id]
        was_firing = firing.get(loop_id, False)
        is_firing = thrash.detect(loop, loop.elapsed(event["ts"])).firing
        if is_firing and not was_firing:
            episodes += 1
        firing[loop_id] = is_firing

    terminal = [lp for lp in state.loops.values() if lp.status in (CLOSED, ABANDONED)]
    followed = [lp for lp in terminal if _followed(lp)]
    abandoned = [lp for lp in terminal if not _followed(lp)]

    return {
        "followed": _bucket(followed),
        "abandoned": _bucket(abandoned),
        "thrash_episodes": {"total": episodes},
        "estimate_drift": _drift(terminal),
        "ping_response": {
            "answered": sum(lp.pings_answered for lp in state.loops.values()),
            "timed_out": sum(lp.pings_timed_out for lp in state.loops.values()),
        },
        "interruptions": _interruptions(terminal, max_depth),
    }


def _followed(loop) -> bool:
    return loop.pings_timed_out == 0 and not loop.checkpoints_pending


def _elapsed_at_close(loop) -> float:
    return loop.elapsed(loop.closed_at if loop.closed_at is not None else 0.0)


def _bucket(loops: list) -> dict:
    if not loops:
        return {"count": 0, "median_s": 0.0, "resolved_pct": 0.0}
    resolved = sum(1 for lp in loops if lp.status == CLOSED)
    return {
        "count": len(loops),
        "median_s": median(_elapsed_at_close(lp) for lp in loops),
        "resolved_pct": round(100.0 * resolved / len(loops), 1),
    }


def _drift(terminal: list) -> dict:
    closed = [lp for lp in terminal if lp.status == CLOSED and lp.original_budget_s > 0]
    ratios = [
        100.0 * (_elapsed_at_close(lp) / lp.original_budget_s - 1.0) for lp in closed
    ]
    extended = [lp for lp in terminal if lp.extensions]
    return {
        "median_pct": round(median(ratios), 1) if ratios else 0.0,
        "extensions": sum(lp.extensions for lp in terminal),
        "loops_with_extensions": len(extended),
    }


def _interruptions(terminal: list, max_depth: int) -> dict:
    if not terminal:
        return {"median_pauses": 0.0, "median_pause_s": 0.0, "max_depth": max_depth}

    pause_counts = [max(0, len(lp.intervals) - 1) for lp in terminal]
    gaps = [
        lp.intervals[index + 1][0] - lp.intervals[index][1]
        for lp in terminal
        for index in range(len(lp.intervals) - 1)
    ]
    return {
        "median_pauses": median(pause_counts),
        "median_pause_s": median(gaps) if gaps else 0.0,
        "max_depth": max_depth,
    }


def render(report: dict) -> list[str]:
    def bucket_line(label: str, data: dict) -> str:
        return (
            f"  {label:<20}: {data['count']} loops | "
            f"median {format_duration(data['median_s'])} | "
            f"{data['resolved_pct']}% resolved"
        )

    drift = report["estimate_drift"]
    interruptions = report["interruptions"]
    return [
        "",
        bucket_line("protocol followed", report["followed"]),
        bucket_line("protocol abandoned", report["abandoned"]),
        f"  {'thrash episodes':<20}: {report['thrash_episodes']['total']}",
        f"  {'estimate drift':<20}: median {drift['median_pct']}% over budget | "
        f"{drift['extensions']} extensions across {drift['loops_with_extensions']} loops",
        f"  {'ping response':<20}: {report['ping_response']['answered']} answered, "
        f"{report['ping_response']['timed_out']} timed out",
        f"  {'interruptions':<20}: median {interruptions['median_pauses']} pauses/loop | "
        f"median pause {format_duration(interruptions['median_pause_s'])} | "
        f"max depth {interruptions['max_depth']}",
        "",
    ]
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `python -m pytest tests/core/test_stats.py -v`
Expected: PASS, 12 tests

- [ ] **Step 6: Write the failing CLI test**

`tests/test_cli_stats.py`:

```python
import pytest

from loop import cli
from loop.core import events
from loop.store import jsonl


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
    monkeypatch.setattr("loop.sched.daemon.ensure_running", lambda: None)


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
```

- [ ] **Step 7: Add `stats` and `grep` to `loop/cli.py`**

In `build_parser`, before the `status` subparser:

```python
    subparsers.add_parser("stats", help="the logbook")
    grepper = subparsers.add_parser("grep", help="search closed loops")
    grepper.add_argument("term")
```

Add the command functions before the `COMMANDS` table:

```python
def cmd_stats(args, state: State, now: float) -> dict:
    from loop.core import stats

    report = stats.compute(jsonl.read_all(), now)
    for line in stats.render(report):
        print(line)
    return report


def cmd_grep(args, state: State, now: float) -> dict:
    from loop.core.models import ABANDONED, CLOSED

    term = args.term.lower()
    matches: list[dict] = []

    for loop in state.loops.values():
        if loop.status not in (CLOSED, ABANDONED):
            continue
        hits = [
            (label, text)
            for label, text in (loop.postmortem or {}).items()
            if term in text.lower()
        ]
        hits += [
            ("action", f"{event['action']} — because {event['because']}")
            for event in jsonl.read_all()
            if event["type"] == "action_logged"
            and event["loop_id"] == loop.id
            and (term in event["action"].lower() or term in event["because"].lower())
        ]
        if hits:
            matches.append({"loop_id": loop.id, "question": loop.question, "hits": hits})

    if not matches:
        print(f"  no matches for {args.term!r}.")
        return {"matches": []}

    for match in matches:
        print(f"  #{match['loop_id']}  {match['question']}")
        for label, text in match["hits"]:
            print(f"      {label}: {text}")
    return {"matches": matches}
```

Add `"stats": cmd_stats,` and `"grep": cmd_grep,` to `COMMANDS`.

- [ ] **Step 8: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_cli_stats.py -v`
Expected: PASS, 5 tests

- [ ] **Step 9: Run the whole suite**

Run: `python -m pytest`
Expected: PASS, 194 tests

- [ ] **Step 10: Update the spec to match the corrected stats definition**

In `docs/superpowers/specs/2026-08-02-loop-thrash-detector-design.md` §11, replace:

> **followed** = every ping answered with no timeouts, and closed via `loop close`.
> **abandoned** = everything else.

with:

> **followed** = no ping timeouts and no checkpoint timeouts, however the loop
> ended. **abandoned** = at least one timeout of either kind. **resolved** = ended
> via `loop close` with a postmortem rather than `loop abandon`. Splitting the
> buckets on protocol adherence alone keeps the resolved column meaningful —
> if being closed were a precondition of *followed*, that column would read
> 100% by construction.

- [ ] **Step 11: Commit**

```bash
git add loop/core/stats.py loop/core/events.py loop/cli.py tests/core/test_stats.py tests/test_cli_stats.py docs/superpowers/specs/2026-08-02-loop-thrash-detector-design.md
git commit -m "feat: add stats and grep"
```

---

## Done

At this point every command in the spec's §10 exists, the log records every event in §3, and the overlays gate the work as described in §6. The deferred items — confidence-stated-versus-correct, launchd registration, Windows and Linux hard blocks, anything team-shaped — are out of MVP scope by decision, not by omission.
