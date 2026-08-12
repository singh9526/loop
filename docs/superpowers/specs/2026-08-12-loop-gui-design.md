# `loop` — the desktop app

**Design spec — 12 August 2026**

Supersedes the daemon and overlay sections of
`2026-08-02-loop-thrash-detector-design.md` (§4 "The daemon", §7 "macOS hard
block", §8 "Portable tkinter blocker"). Everything else in that spec —
the event log, the fold, the schedule, the thrash detector — stands unchanged.

Visual design: <https://claude.ai/design/p/e156fbc6-34d0-4944-865a-4709cb731364>
(8 cards: Foundations/Tokens; Components/Budget meter, Panels, Dialogs;
Screens/Dashboard, Check-in, Logbook, Failure states). The palette and layout
rules are restated in §11 so this document survives the link.

---

## 1. What this is

A standalone desktop app for macOS and Windows that does everything `loop`
does, without a terminal. Not a viewer for a CLI-driven workflow: a full
front end that opens loops, logs actions, kills hypotheses, cuts scope,
extends budgets, closes loops with a postmortem, and — the part that matters —
**schedules and draws its own check-ins**.

The CLI keeps working and keeps its tests. It is a second front end onto the
same event log, not a dependency of the first.

### Why this is not a small change

Every mutation in `loop` is currently welded to blocking terminal I/O.
`cmd_open` calls `prompts.ask_yes_no` / `ask_text` / `ask_duration` /
`ask_lines` inline (`cli.py:127-140`); `cmd_close` calls `ask_text` three times
(`cli.py:280-282`); `cmd_pause` and `cmd_resume` each call `ask_text`
(`cli.py:215`, `cli.py:231`). Every one of them reads the argparse `args`
namespace and writes to stdout through `say()`.

There is no function a window can call. So the deliverable is not "a GUI on
top of `loop`" — it is a headless command layer that both front ends sit on,
with the GUI built against it.

### Scope

**In:** every command the CLI has, as a UI action; the app as the scheduler,
resident in the tray/menu bar; a Qt full-screen check-in replacing both
overlays; write-concurrency correctness now that three processes can append;
the logbook and search as a second view; a single-instance guard.

**Out:** installers, code signing, notarization, auto-update. Linux. Any
network or sync. The app must not *prevent* packaging — no reads relative to
`__file__` outside the package, no absolute paths — but shipping a signed
binary is a separate spec.

**Platforms:** macOS and Windows, from one codebase. Linux is reachable
because nothing platform-specific is written outside `store/lock.py` and the
launcher's spawn flags, but it is not tested or claimed.

---

## 2. Architecture

```
loop/
  core/                  # PURE — unchanged, plus one new module
    models.py
    events.py
    schedule.py
    thrash.py
    stats.py             # one rendering fix (§10)
    timefmt.py
    timeline.py          # NEW — the action-log projection
  store/
    jsonl.py             # append now runs under a lock
    paths.py             # pid_path() deleted, lock_path() added
    lock.py              # NEW — the only platform branch in the write path
  app/                   # NEW — headless. no Qt, no argparse, no stdout
    errors.py            # LoopError, StaleError
    session.py           # read -> validate -> append, all under one lock
    commands.py          # the eleven mutations, as functions
    checkin.py           # Prompt <-> Answers <-> events (moved from sched/tick.py)
    view.py              # pure view models the window renders
    launcher.py          # is the app available / spawn it
  gui/                   # NEW — the only place PySide6 is imported
    __main__.py          # entry point, single-instance guard
    tray.py              # tray / menu bar, hide-vs-quit
    window.py            # dashboard
    dialogs/             # open, action, hypothesis, close, abandon, cut, extend
    checkin.py           # the full-screen check-in
    scheduler.py         # QTimer -> schedule.next_due -> checkin -> append
    logbook.py           # stats and search
  blockers/
    base.py              # survives: the check-in contract
    fake.py              # survives: scripted answers for tests
  cli.py                 # survives: now calls app/commands.py
  prompts.py             # survives: terminal input for the CLI only
```

### Deleted

| File | Lines | Why |
|---|---|---|
| `sched/daemon.py` | 159 | The app is the scheduler. No detached process. |
| `sched/tick.py` | 172 | `build_prompt`/`answers_to_events` move to `app/checkin.py`; the orchestration is `gui/scheduler.py`. |
| `blockers/macos.py` | 414 | One Qt check-in serves both platforms. |
| `blockers/tk.py` | 155 | Same. |
| `blockers/factory.py` | 70 | Its job was choosing between three platform overlays. There is one, and `gui/scheduler.py` constructs it directly — honouring `LOOP_BLOCKER=fake` for tests, which is the only branch that remains. Routing the choice through `blockers/` would make that package import `gui/`, and PySide6 with it. |

`loop tick` is removed from the CLI. It was documented as internal and existed
only to drive the daemon. The `macos` extra (PyObjC) is removed from
`pyproject.toml`.

Both macOS bugs fixed on 2 August — the keyboard-tap teardown and the window
level — exist only because a headless daemon owns a window it cannot own
properly. Deleting the daemon deletes their whole category.

### The layer rules

The three original rules stand:

- `core/` imports nothing from `store/`, `blockers/`, `app/`, or `gui/`.
- `blockers/` imports nothing from `core/`.
- Only `cli.py` and `app/` may wire layers together.

Two new ones:

- **`gui/` is the only package that may import PySide6.** Nothing in `core/`,
  `store/`, `app/`, or `blockers/` may import it, directly or transitively.
  Enforced by a test that imports every non-`gui` module with `PySide6`
  poisoned in `sys.modules`.
- **`jsonl.append` has exactly one caller: `app/session.py`.** Every write in
  the process tree goes through the lock. Enforced by a grep test.

---

## 3. The command layer

`app/commands.py` holds one function per mutation. Each takes already-collected
values — never a prompt, never an argparse namespace — and returns a result
object. No printing, no input, no Qt.

```python
def open_loop(session, *, question, stop_condition, budget_s, interval_s,
              hypotheses, stack_on_active: bool) -> Opened: ...
def log_action(session, *, action, because) -> Logged: ...
def add_hypothesis(session, *, text) -> HypothesisAdded: ...
def kill_hypothesis(session, *, hyp_id) -> HypothesisKilled: ...
def pause(session, *, reason) -> Paused: ...
def resume(session, *, loop_id: int | None) -> Resumed: ...
def close(session, *, what_was_it, giveaway, five_min_path) -> Closed: ...
def abandon(session) -> Abandoned: ...
def cut_scope(session, *, new_stop_condition) -> ScopeCut: ...
def extend_budget(session, *, new_budget_s, learned) -> BudgetExtended: ...
def answer_checkin(session, *, due, answers) -> CheckinRecorded: ...
```

Each result carries what a front end needs to report — the new loop id, the
live-hypothesis count, the elapsed time at close, the id of a parent that got
resumed. The CLI turns a result into `say()` lines; the GUI turns the same
result into a status line and a repaint. Neither can produce a message the
other cannot. Every result also serialises to the dict its `cmd_*` function
returns today, so `--json` output is byte-identical across the rewiring; the
existing `--json` tests are the check.

`stack_on_active` is the GUI's checkbox and the CLI's `ask_yes_no` — the
decision is the caller's, the consequence is the command's.

### Session: read, validate, append

`app/session.py` owns the only mutation path.

```python
class Session:
    def mutate(self, build: Callable[[State, float], tuple[list[dict], R]]) -> R:
        with lock.exclusive(paths.lock_path()):
            state = events.fold(jsonl.read_all())
            written, result = build(state, time.time())
            for event in written:
                jsonl.append(event)
            return result
```

`build` returns both the events to append and the result the caller reports —
computed from the same freshly-read state, inside the same lock hold. A
command that computed its result afterwards, from a second read, could report
a number that a concurrent write had already changed.

Three properties follow, and all three are load-bearing:

**Multi-event mutations are atomic.** Opening a loop on top of an active one
emits `loop_paused` then `loop_opened`. `fold` raises `InvariantError` if a
`loop_opened` lands while `active_id` is set (`events.py:92`), so a log with
the pause missing is a log that will not load. Closing a child emits
`loop_closed` then `loop_resumed` for the parent. Both pairs are written
inside one lock hold.

**Preconditions are checked against freshly-read state, never against what the
window last painted.** `build` receives the state it will be appended to. This
is where the "state moved under you" refusal comes from: a click on *rule out
hypothesis 2* runs `build`, which finds hypothesis 2 already dead and raises
`StaleError("A check-in ruled out …")`. The GUI shows the notice and repaints.
Nothing is appended. This is the same check `cmd_hyp` does today at
`cli.py:197` — it moves inside the lock, which is the only place it was ever
sound.

**`LoopError` and `StaleError` both mean "no events written".** `build` either
returns a complete list or raises. There is no partial write.

### The lock

`store/lock.py` is a context manager over a dedicated file, `<loop_home>/lock`
— not the event log itself, because taking a lock on a file that is
simultaneously opened in append mode and closed on every write is a race
waiting to be found.

```python
@contextmanager
def exclusive(path: Path, timeout_s: float = 10.0): ...
```

- POSIX: `fcntl.flock(fd, LOCK_EX)`.
- Windows: `msvcrt.locking(fd, LK_LOCK, 1)`, which itself retries once a
  second ten times before raising `OSError`; the wrapper converts that to
  `LockTimeout` rather than letting a bare `OSError` reach a dialog.

The lock is what makes `jsonl.py`'s existing comment true on Windows. That
comment claims a line append is atomic "on POSIX for payloads under PIPE_BUF"
— accurate, and irrelevant on Windows, which offers no such guarantee.
With one writer at a time the guarantee is no longer needed on either
platform, and the comment is rewritten to say so.

Lock hold time is bounded by a full log read and a small number of appends —
microseconds to low milliseconds. No user-facing wait ever happens under it:
dialogs collect input first, then call the command.

### The CLI, rewired

`cli.py` keeps its argparse tree, its `--json`, its exit codes, and its
messages. Every `cmd_*` becomes: collect answers with `prompts`, call the
command, render the result. `LoopError` moves to `app/errors.py`;
`cli.py` imports it. `require_blocker()` is replaced by `launcher.available()`
(§7). The existing CLI test suite is the regression net and must pass
unchanged except for `loop tick`, whose tests are deleted with it.

---

## 4. The action log projection

`fold` collapses actions to a counter (`Loop.actions: int`, `events.py:133`).
The dashboard shows an action *log* — text, `because`, timestamp — so it needs
a second projection over the same events. It goes in `core/`, pure, taking the
same `list[dict]` `fold` takes.

```python
# core/timeline.py
@dataclass(frozen=True, slots=True)
class Entry:
    ts: float
    kind: str            # action | hypothesis | ruled_out | ping | checkpoint
                         # | cut | extend | paused | resumed
    text: str
    because: str | None

def entries(log: list[dict], loop_id: int) -> list[Entry]: ...
```

One rule needs stating because it is not local: a `hypothesis_eliminated`
immediately preceded by a `ping_answered` carrying the same `eliminated` id is
attributed to the check-in (`because = "answered at the 20m check-in"`).
`_ping_events` emits exactly that pair, adjacent and same-timestamp
(`sched/tick.py:97-105`). Anything else is a manual kill and carries no
`because`. This is why the projection reads the log rather than the folded
state — the attribution lives in the adjacency, and the fold discards it.

---

## 5. View models

`app/view.py` turns `State` + `now` into plain frozen dataclasses that describe
what is on screen. No Qt, no formatting decisions the widgets can second-guess.

```python
@dataclass(frozen=True, slots=True)
class MeterView:
    elapsed_s: float
    budget_s: float
    fraction: float          # clamped to 1.0; `over` carries the rest
    over_s: float
    next_checkin_s: float | None

@dataclass(frozen=True, slots=True)
class DashboardView:
    active: LoopView | None
    paused: list[StackRow]
    hypotheses: list[HypothesisRow]
    timeline: list[Entry]
    meter: MeterView | None
    thrash: ThrashView | None
    enabled_actions: frozenset[str]
    status: StatusView
```

`enabled_actions` holds the command names from the table in §8 —
`"open"`, `"try"`, `"hyp_add"`, `"hyp_kill"`, `"pause"`, `"resume"`, `"cut"`,
`"extend"`, `"close"`, `"abandon"`. Each control declares which name it needs
and asks once. It is how the log-unreadable state disables every button from
one place instead of from eleven `setEnabled` calls scattered through the
widget tree.

This is the layer that makes the GUI testable without a display. Every claim
the dashboard makes — the meter fraction, the paused-longer-than-a-day mark,
the thrash banner's counts, which buttons are live — is asserted against
`app/view.py` in ordinary unit tests. The widgets only bind.

---

## 6. The check-in

`app/checkin.py` receives `build_prompt` and `answers_to_events` from
`sched/tick.py` unchanged, minus one copy fix. They already speak only
`core` and `blockers.base`, and they are already tested.

`gui/checkin.py` implements `Blocker.ask(prompt) -> Answers` as a Qt window.
One implementation, both platforms, replacing 569 lines across `macos.py` and
`tk.py`.

**Insistence, honestly described.** The window is frameless, on top, sized to
cover every attached screen, with no close affordance and no Escape binding.
A 1-second timer calls `raise_()` and `activateWindow()` so a foreground steal
is undone. It cannot do what `macos.py` did: `CGShieldingWindowLevel` plus
`DisableProcessSwitching` made cmd-tab itself stop working. **Qt has no
equivalent, on either platform.** The check-in is insistent, not inescapable.
This was raised before the toolkit was chosen and accepted deliberately — one
codebase for both platforms was worth more than an unbypassable block on one.

**Timeout.** `TIMEOUT_S = 300.0` stands, now enforced by a `QTimer` in the same
process rather than by a parent watching a child. `KILL_AFTER_S = 310.0` is
deleted: it was the daemon's watchdog against an overlay subprocess wedging
the screen, and there is no subprocess. The regression this trades for is real
and recorded in §13.

**The p100 copy change.** `sched/tick.py:66` reads:

```python
Choice("x", "stop now — then run `loop close`"),
```

That instruction points at a terminal the user has been told they no longer
need. It becomes `Choice("x", "stop now")`, and the scheduler opens the
postmortem dialog directly once the checkpoint event is written. The CLI's
behaviour is unchanged — it still expects `loop close` next — but the string
is no longer the place that says so.

Every other prompt string is preserved verbatim, because they are the product:

- ping title `loop #{id} · {elapsed} / {budget} · {n} paused`, question
  `hypothesis space smaller than {interval} ago?`, choices `y`/`n`, pick list
  revealed after `y`.
- p75 title `75% of budget gone · {span}`, question `is 75% of the work done?`,
  choices `y` / `c cut scope` / `e extend estimate`.
- p100 title `budget gone · {span}`, question `budget is gone. what now?`.
- `CUT_FIELDS` / `EXTEND_FIELDS` unchanged.

### The scheduler

`gui/scheduler.py` is a `QTimer` at `POLL_S = 10.0`, doing per tick what
`sched/tick.py:tick` did:

1. Fold the log.
2. `schedule.next_due(state, now)`. Nothing due → return.
3. For a checkpoint, append `checkpoint_shown` first (forensics, unchanged).
4. `app/checkin.build_prompt` → `gui/checkin.ask` → blocks the user, not the
   Qt event loop's timers.
5. **Re-fold and confirm `active_id` still equals `due.loop_id`.** A check-in
   can sit for five minutes; if the loop was closed or paused from a terminal
   meanwhile, the answers are stale and are discarded silently. This is
   `sched/tick.py:167` and it survives verbatim.
6. `answers_to_events` → append through `session.mutate`.

The clock on the dashboard updates every second without touching the disk:
elapsed is `elapsed_from_intervals(loop.intervals, time.time())`, and
`intervals` only changes when the log does. The log is re-folded when its size
changes, checked on the same 1-second timer — size alone is a sufficient
change signal on an append-only file, and unlike mtime it has no filesystem
granularity to lose a write inside. A `loop try` typed in a terminal shows up
in the window within a second.

---

## 7. Process model

**One process. It is the app and the scheduler.** Closing the window hides it
to the tray; the scheduler keeps running. Quit is only reachable from the tray
menu, and when a loop is active it confirms first, naming the consequence:
check-ins stop.

**Single instance,** because two copies would fire two check-ins per interval
and contend on every append. The guard is a `QLocalServer` named from a hash of
`loop_home()` — so a throwaway `LOOP_HOME` never collides with the real one.
On startup the app tries to connect; a live server means another copy exists,
so it sends `raise`, and exits 0. The running copy shows its window. A stale
socket file (POSIX, after SIGKILL) is detected by a failed connect followed by
a failed listen, and cleared with `QLocalServer.removeServer`.

There is no pidfile and no heartbeat. `paths.pid_path()` is deleted. The
liveness question has exactly one authority, and it is the socket.

**`loop open` and `loop resume` from a terminal launch the app** if it is not
running, which is what `daemon.ensure_running()` did (`cli.py:168`,
`cli.py:237`). `app/launcher.py` replaces it:

- `available()` — is PySide6 importable? Called *before* any event is written,
  preserving the existing invariant that `loop open` never leaves a timer
  running that nothing can surface a prompt for. Failure message names the
  install: `pip install 'loop-tool[gui]'`.
- `ensure_running()` — spawn `python -m loop.gui` detached
  (`start_new_session` on POSIX, `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`
  on Windows, exactly as `daemon._spawn`). The spawn is unconditional and
  idempotent: a second copy discovers the server, raises the first window,
  and exits. Nothing needs to guess whether one is already up.

Other CLI commands do not launch anything. `loop pause` should not open a
window.

---

## 8. Every action, and where it lives

| Action | Toolbar | Row | Menu | Dialog |
|---|---|---|---|---|
| Open a loop | — | — | ✔ ⌘⇧N | question, stop condition, budget, interval, hypotheses; a stacking warning when one is active |
| Log an action | ✔ ⌘⇧A | — | ✔ | action, because (both required) |
| Add hypothesis | ✔ | ✔ | ✔ | text |
| Rule out hypothesis | — | ✔ | ✔ | none — direct |
| Pause | ✔ | — | ✔ | reason |
| Resume | — | ✔ | ✔ | none — direct |
| Cut scope | ✔ | — | ✔ | new stop condition, old shown above it |
| Extend budget | ✔ | — | ✔ | new budget, what you learned |
| Close | ✔ | — | ✔ | what was it / giveaway / five-minute path |
| Abandon | ✔ | — | ✔ | confirm, naming elapsed time |
| Logbook | status strip | — | ✔ | — |
| Search | status strip | — | ✔ | — |

Row controls are revealed on hover or keyboard focus so the stack and the
hypothesis list read as data first. Nothing is hover-only: every row action
also has a menu item and a keyboard path. That is the accessibility floor, not
a nicety — a hover-only control does not exist for a keyboard user.

Multi-field actions are modal dialogs, per the input-model decision. A dialog
collects, validates, and returns; the command runs after it closes. No dialog
holds the lock.

---

## 9. Failure states

Four, each reachable from a real condition rather than a hypothetical.

**Thrashing.** `thrash.detect` fires on 30 minutes with 5+ actions and nothing
ruled out, or three consecutive `no` pings. The banner takes vertical space
between the meter and the columns and pushes the body down — an alarm that
costs nothing is an alarm you stop seeing. It shows the counts that triggered
it (`7 actions · 0 ruled out · 47m elapsed`) so the claim is auditable.

**Log unreadable.** `CorruptLogError` from an interior line. The last state
that folded cleanly stays on screen behind the notice — a stale clock beats a
blank window — with the exact file and line named for hand repair, and the
time the state was frozen at. `enabled_actions` empties: **every mutation is
disabled.** Appending to a log that cannot be folded makes the damage worse,
and the app is the last thing that should do that. The fold is retried on each
poll, so repairing the file in an editor recovers the window with no restart.

**The state moved under you.** A `StaleError` from §3. A small dialog naming
what actually happened and when, then a repaint. Deliberately not an error
tone: the outcome the user wanted already occurred.

**Already running.** The second launch, from §7. It surfaces the first window
rather than starting, which is also the right behaviour for a second click on
the Dock icon.

---

## 10. The logbook

A second view in the same window, not a second window: `stats` and `grep`
share the loop list they filter.

`core/stats.py` is unchanged except for one rendering bug that this spec
inherits and fixes. `stats.py:112` prints:

```python
f"  {'estimate drift':<20}: median {drift['median_pct']}% over budget | "
```

`median_pct` is signed (`stats.py:74`), so a loop that finished early prints
`median -99.7% over budget`. The fix renders the sign as a word — `18% over` /
`18% under` — in both the CLI and the logbook view, from one helper so they
cannot drift apart. Pre-existing bug, fixed here because this spec redraws the
output that contains it.

---

## 11. Visual design

Restated so the spec stands without the design link.

**Ground and ink.** Light: `--ink:#F1F5F5`, `--panel:#FFFFFF`,
`--raised:#E7EDED`, `--line:#D0DBDB`, `--text:#13201F`, `--muted:#576C71`,
`--dim:#8598A0`. Dark: `--ink:#0C1214`, `--panel:#121B1E`, `--raised:#19252A`,
`--line:#26363C`, `--text:#DCE8EA`, `--muted:#7B9198`, `--dim:#506469`.
Accent `#0F857C` light, `#5AD1C8` dark. Semantic: ok `#2C8A4E`/`#5FCB7E`, warn
`#A96F16`/`#E4A33C`, critical `#C04630`/`#E4644E`.

**Type.** System faces only, because PySide6 has to render them too and
bundling a face for parity with a web preview is a cost with no return: mono
is `SF Mono` / `Cascadia Mono`, UI is `SF Pro Text` / `Segoe UI Variable`.
Numbers that line up in columns — clocks, budgets, counts — are tabular.

**The burn meter** is the one signature component. The track is a fixed
cool→warm→hot gradient and the fill is a *mask* that retracts from the left,
so the colour at the leading edge is a direct read of how much budget is gone.
The 75% checkpoint is a tick on the track, not a separate widget. Over budget,
the mask is removed entirely and the whole track burns.

**Both themes are first-class,** on both platforms, following the OS
preference. Qt's palette is driven from the same token table the previews use,
defined once in Python.

---

## 12. Testing

The existing constraints hold: **no test may spawn the app, open a window, or
require a GUI toolkit to be installed**, and no test may write to the user's
real `~/.loop`. The lock test spawns bare `python -c` children — it is testing
cross-process file locking, which cannot be tested in one process — and that is
the only place any subprocess appears.

| Layer | How |
|---|---|
| `core/timeline.py` | Pure. Events in, entries out, including the check-in attribution pairing. |
| `store/lock.py` | Two subprocesses appending 200 lines each under contention; assert 400 well-formed lines, none interleaved, none lost. This is the one test that spawns processes, and it spawns Python, not the app. |
| `app/commands.py` | The whole surface, against a temp `LOOP_HOME`. Every command, every `LoopError`, every `StaleError`. |
| `app/session.py` | A `build` that raises leaves the log byte-identical. A multi-event mutation is all-or-nothing. |
| `app/checkin.py` | The moved tests from `tests/sched/test_tick.py`, plus the p100 label. |
| `app/view.py` | Every dashboard claim: meter fraction, over-budget, stale-pause mark, thrash counts, `enabled_actions` empty when the log is unreadable. |
| `cli.py` | Unchanged suite. It is the regression net for the rewiring. |
| Layer discipline | Import every non-`gui` module with `PySide6` poisoned in `sys.modules`; assert no `jsonl.append` caller outside `app/session.py`. |
| `gui/` | `pytest.importorskip("PySide6")`. A handful of construct-and-teardown smoke tests, offscreen platform. Skipped by default. Coverage lives in `app/`. |

The asymmetry is deliberate: `gui/` is thin enough that if `app/view.py` and
`app/commands.py` are right, the widgets are bindings. Anything in `gui/` that
needs a test is a sign that logic leaked down.

---

## 13. Known risks

**Qt cannot stop app switching.** cmd-tab and alt-tab work during a check-in.
The block is insistent, not absolute. Accepted when the toolkit was chosen.

**No external watchdog.** With the check-in in-process, a wedged Qt event loop
takes the timeout with it. The daemon's `KILL_AFTER_S` could not have survived
the architecture — it existed to kill a child — but its absence is a real
reduction in worst-case robustness, and it is written down rather than papered
over.

**PySide6 is a large dependency** — a few hundred megabytes installed. Core
stays dependency-free and the CLI keeps working without it; the GUI is an
optional extra, `loop-tool[gui]`.

**The lock is advisory.** `flock` protects against processes that take it.
Every writer in this codebase does, and the grep test keeps it that way, but
an editor writing to `events.jsonl` will not.

**Windows is designed for, not yet tested on.** The lock branch, the spawn
flags, the tray, and the full-screen geometry all have Windows paths in this
spec written from documentation. A real pass on Windows is the last build step
and is expected to find something.

---

## 14. Build order

Each step ends green.

1. `store/lock.py`; `jsonl.append` under the lock; contention test.
2. `core/timeline.py` and its tests.
3. `app/errors.py`, `app/session.py`, `app/commands.py` — all eleven mutations,
   headless, fully tested.
4. `app/launcher.py` — `available()` and `ensure_running()`, spawning
   `python -m loop.gui`. It has nothing to launch yet; step 8 gives it one.
5. `cli.py` rewired onto `app/commands.py`, `require_blocker()` replaced by
   `launcher.available()`, `daemon.ensure_running()` by
   `launcher.ensure_running()`. `loop tick` removed with its tests. Delete
   `sched/daemon.py` and `blockers/factory.py` — both are now unreachable —
   with `tests/sched/test_daemon.py` and `tests/blockers/test_factory.py`. The
   rest of the CLI suite passes unchanged.
6. `app/checkin.py` — move `build_prompt` / `answers_to_events` out of
   `sched/tick.py`, apply the p100 copy change, move the tests. `sched/` is
   now empty and goes.
7. `app/view.py` and its tests.
8. `stats.py` drift-sign fix.
9. `gui/__main__.py`, `gui/tray.py` — single-instance guard, tray, an empty
   window that quits cleanly. `pyproject.toml` gains `gui`, loses `macos`.
10. `gui/window.py` — the dashboard, bound to `app/view.py`.
11. `gui/dialogs/` — all eight.
12. `gui/checkin.py` — the full-screen check-in.
13. `gui/scheduler.py` — the poll loop, wired to the check-in.
14. `gui/logbook.py` — stats and search.
15. Delete `blockers/macos.py`, `blockers/tk.py` and their tests; add the
    layer-discipline tests.
16. Windows pass: lock, spawn, tray, geometry, fonts.
