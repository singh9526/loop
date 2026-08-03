# `loop` — thrash detector for debugging

**Design spec — 2 August 2026**

Source idea: `loop-tool-idea.md`. This spec covers the MVP only.

---

## 1. What this is

A CLI tool that treats an **open loop** — a question you don't yet have the answer
to — as the core object, and gates your work on it with blocking full-screen
check-ins.

Existing productivity tools assume the problem is distraction. This one assumes
the opposite: that you work hard and finish what you start, and that your failure
mode is productive-looking avoidance — effort that doesn't shrink the hypothesis
space. That failure is invisible to any tool that measures activity, because from
the outside it is indistinguishable from good work. The signal that separates them
is whether hypotheses got eliminated, and that is computable.

The tool's real output is the logbook: a record of how long loops actually take
versus how long you estimated, how often you thrashed, and what the giveaway was
each time.

### MVP boundary

**In:** `open` / `try` / `close` / `abandon` / `pause` / `resume` / `ls` / `status` /
`hyp` / `stats` / `grep`; append-only event log; a loop stack for handling
interruptions; detached tick daemon; 20-minute blocking ping; 75% and 100% budget
checkpoints with forced scope-cut or estimate-extension; thrash detector; macOS
hard block plus a portable tkinter fallback.

**Out:** mobile anything; sync; a daemon HTTP API; team/incident-response features;
confidence-stated-vs-correct calibration (needs a field not captured on `open`);
launchd/systemd registration.

**Target platforms:** macOS first, with Windows and Linux reachable by writing one
new file. Desktop only — permanently. Mobile is explicitly not a goal, because no
mobile OS permits an app to block the screen, so the core mechanic cannot exist
there.

---

## 2. Architecture

Three seams carry the entire portability story.

```
loop/
  cli.py                 # arg parse, dispatch
  core/                  # PURE: zero OS imports, zero I/O
    models.py            # Loop, Hypothesis, Event
    events.py            # event types + fold(events) -> state
    schedule.py          # next ping, checkpoint math, sleep/wake catch-up
    thrash.py            # detector
    stats.py             # aggregates
  store/
    jsonl.py             # append-only log
    paths.py             # ~/.loop | XDG | %APPDATA%
  blockers/
    base.py              # Prompt / Answers / Blocker protocol
    tk.py                # stdlib tkinter — mac + win + linux
    macos.py             # PyObjC hard block
    fake.py              # scripted answers, for tests
    factory.py           # platform + LOOP_BLOCKER env
  sched/
    daemon.py            # detached tick process
    tick.py              # one tick, idempotent
```

**The rules that make the seams real:**

- `core/` imports nothing from `os`-specific modules, nothing from `blockers/`,
  nothing from `store/`. It takes events in and returns decisions out.
- `blockers/` imports nothing from `core/`. It speaks only `Prompt` and `Answers`.
- `cli.py` and `sched/` are the only modules that wire the three together.

Consequence: porting to Windows means writing `blockers/windows.py` and nothing
else. Core logic and its tests are untouched.

### Language and dependencies

Python 3.11+. Standard library only, except PyObjC on macOS
(`pyobjc-framework-Cocoa`, `pyobjc-framework-Quartz`), which is an optional extra —
if it is missing, the tool falls back to the tkinter blocker and prints a one-line
notice that the block is soft.

---

## 3. Storage

One append-only file: `~/.loop/events.jsonl`. One JSON object per line. State is
`fold(events)`.

No cache file, no `state.json`, no database, no schema migrations. The log stays in
the hundreds of lines for years, so a full replay is sub-millisecond and there is
no reason to cache anything. A crash mid-write can damage at most the final line,
never the history. The file is greppable by hand, which is what makes `loop grep`
trivial and what makes the log survivable when the tool itself has a bug.

`store/paths.py` resolves the root directory per platform: `~/.loop` on macOS,
`$XDG_DATA_HOME/loop` (falling back to `~/.local/share/loop`) on Linux,
`%APPDATA%\loop` on Windows. `LOOP_HOME` overrides all of them — tests use it.

### Event types

Every event carries `type`, `ts` (Unix seconds, float), and `loop_id` where
applicable.

| Event | Fields |
|---|---|
| `loop_opened` | `id`, `question`, `stop_condition`, `budget_s`, `interval_s`, `hypotheses[]`, `parent_id` (or null) |
| `loop_paused` | `reason` |
| `loop_resumed` | — |
| `action_logged` | `action`, `because` |
| `hypothesis_added` | `hyp_id`, `text` |
| `hypothesis_eliminated` | `hyp_id` |
| `ping_answered` | `smaller` (bool), `eliminated` (`hyp_id` or null), `shown_at` |
| `ping_unanswered` | `shown_at` |
| `checkpoint_shown` | `kind` (`p75` \| `p100`) |
| `checkpoint_answered` | `kind`, `on_track` (bool), `decision` (`continue` \| `cut` \| `extend` \| `close`) |
| `checkpoint_unanswered` | `kind`, `shown_at` |
| `scope_cut` | `old_stop_condition`, `new_stop_condition` |
| `budget_extended` | `old_budget_s`, `new_budget_s`, `learned` |
| `loop_closed` | `what_was_it`, `giveaway`, `five_min_path` |
| `loop_abandoned` | — |

Hypothesis IDs are 1-based per loop and stable for that loop's lifetime; killing
hypothesis 2 does not renumber 3.

`loop_paused` and `loop_resumed` are what `core/schedule.py` folds into the active
clock. They are the only source of truth for how much of a budget has been spent —
there is no stored elapsed counter to drift out of sync.

A `ping_answered` with a non-null `eliminated` also emits a
`hypothesis_eliminated`. Two events, because they answer different questions:
one is "did the scheduled check-in happen", the other is "did the search space
shrink". Stats needs both independently.

---

## 4. The loop stack, the active clock, and the scheduler

### The stack

Interruptions are real and unavoidable — a production incident arrives while you
are mid-debug. Loops therefore form a **stack**, not a set.

`loop open` while a loop is active offers to push: the current loop auto-pauses and
the new one becomes active. `loop close` or `loop abandon` pops and auto-resumes
the parent. Each loop records the `parent_id` it was stacked on.

**Exactly one loop is active at any moment.** That invariant is load-bearing — it
is the entire premise of the tool — and the stack preserves it rather than
weakening it. Only the active loop has a running timer, receives pings, or can
reach a checkpoint. Paused loops are inert.

`loop resume <id>` jumps to any paused loop, not only the one directly beneath, for
the common case where an interruption outlives the task it interrupted.

### Pause friction

Pause is the obvious escape hatch: an inconvenient checkpoint becomes a pause, and
the gate turns ornamental. Three counterweights:

- `loop pause` requires one typed line — what interrupted? Stacking via `loop open`
  uses the new loop's question as that reason, so the common path stays one command.
- Stack depth warns at 3 (`you are context switching, not working`) and **refuses at
  5**. This is a debugging tool, not a task list.
- Pauses are surfaced in `loop stats` as interruptions-per-loop and median pause
  duration. A pause that is really avoidance shows up as a pattern there.

`loop ls` flags any loop paused over 24 hours with `⚠`. No overlay fires for it —
a stale loop is not an emergency, and an unscheduled interruption on a day you
aren't working on that loop would be noise.

### The active clock

**Budget and ping intervals consume active time only.** A loop's `elapsed` is the
sum of the wall-clock intervals during which it was active. Paused time is free.

Machine sleep while a loop is active *does* count — you walked away, and the budget
you committed to was real time. Sleep while paused is irrelevant, since paused time
never accrues.

Every timing rule below, plus the thrash detector, operates on active elapsed.
`core/schedule.py` takes the event log and computes this; nothing else may read a
raw wall clock to make a scheduling decision.

### The daemon

`loop open` spawns a detached daemon process. On POSIX that is
`Popen(start_new_session=True)`; on Windows, `DETACHED_PROCESS |
CREATE_NEW_PROCESS_GROUP`. This is the only platform branch outside `blockers/`.
Detaching means the daemon survives closing the terminal.

The daemon is a **singleton, not bound to a loop id**. It services whatever loop is
currently active and exits when none is. It ticks every 10 seconds: read the log,
ask `core/schedule.py` what is due, run the blocker on its own main thread (both GUI
toolkits require this), append the resulting event, and exit once the log shows no
active loop.

`~/.loop/daemon.pid` holds the running daemon's PID. `loop open` starts one if none
is running, and kills a stale one first. Because the daemon re-reads the log every
tick rather than holding state, pausing, resuming, stacking, and closing all work
from any terminal without signalling it — the daemon simply notices within 10
seconds.

### Timing rules

- **Ping:** every `interval` of active time (default 20m, configurable per loop via
  `--interval`), measured against the last ping event.
- **p75:** at `0.75 × current_budget` of active elapsed. Fires at most once per
  distinct budget value.
- **p100:** at `current_budget` of active elapsed. Fires at most once per distinct
  budget value.
- **On resume:** the next ping is rebased to `now + interval`. Returning to a loop
  must never fire a ping immediately — that would punish resuming, which is the
  behaviour we want.
- **Extension re-arms both.** After extending 45m → 90m, p75 is recomputed as
  67.5m and both checkpoints become eligible again. This is deliberate: a new
  budget is a new commitment and deserves the same gates.

### Sleep and wake

If `now - next_ping_at > interval`, the machine was asleep or you were away. Fire
**exactly one** ping and rebase the schedule from now. A backlog of six pings after
a lunch break would train you to dismiss them, which destroys the mechanic.

Checkpoints are not rebased — they are absolute positions in the budget, and a
missed p75 fires as soon as the machine is awake.

### Collision

If a ping falls within 3 minutes of a checkpoint, skip the ping. The checkpoint
supersedes it. The number of interruptions per hour must stay predictable.

---

## 5. The blocker interface

```python
# blockers/base.py

@dataclass(frozen=True)
class Choice:
    key: str          # single keystroke, e.g. "y"
    label: str

@dataclass(frozen=True)
class TextField:
    name: str
    label: str
    required: bool = True

@dataclass(frozen=True)
class Prompt:
    kind: str                      # "ping" | "p75" | "p100"
    title: str                     # "loop #14 · 42m / 45m"
    question: str
    choices: list[Choice]
    pick_list: list[str] | None    # numbered list, e.g. live hypotheses
    pick_after: str | None         # show pick_list only after this choice key
    fields_after: dict[str, list[TextField]]   # choice key -> fields it reveals
    warning: str | None            # thrash panel, rendered red
    timeout_s: int = 300

@dataclass(frozen=True)
class Answers:
    timed_out: bool
    choice: str | None
    picked: int | None             # 1-based index into pick_list
    fields: dict[str, str]
    shown_at: float
    answered_at: float

class Blocker(Protocol):
    def ask(self, prompt: Prompt) -> Answers: ...
```

`core/` builds `Prompt` objects and interprets `Answers`. It never learns which
blocker ran.

---

## 6. The three overlays

All three: full screen, 5:00 countdown with a visible progress bar, self-destruct
at zero, and log the timeout as data rather than discarding it.

The countdown is not decoration. It is the mechanism — it forces the thought to
completion inside a bounded window and shows how much of that window is left.

### 20-minute ping

The title line carries stack depth whenever more than one loop is open, so an
interrupted context is never invisible at the moment you are asked to judge it:
`loop #15 · 12m / 30m · 2 paused`.

```
┌─ loop #14 · 42m / 45m ───────────────────┐
│                                          │
│  hypothesis space smaller than 20m ago?  │
│                                          │
│          [ y ]        [ n ]              │
│                                          │
│  ── y → which died? ──                   │
│   1  cert expiry                         │
│   2  env var missing                     │
│   3  pg conn pool                        │
│                                          │
│  ██████████████░░░░░░  3:12 left         │
└──────────────────────────────────────────┘
```

`y` reveals the numbered list of live hypotheses; one more keystroke records which
died. That second keystroke is the only input thrash detection has — without it,
"eliminations" is a number nothing ever feeds.

`n` logs and dismisses immediately.

When the thrash detector fires, a red panel is added to this same overlay. No
second popup — the interruption budget stays fixed.

### 75% checkpoint

```
⚠  75% of budget gone · 34m / 45m
   is 75% of the work done?   [y] [n]

   ── n → pick one, no third option ──

   [c]  cut scope
        old: "comes up clean twice in a row"
        new: _____________________________
        → old condition logged as deferred

   [e]  extend estimate
        new budget:  ______
        what did you learn that made it bigger?
        _________________________________
```

`y` logs and dismisses. `n` forces a binary choice with mandatory typing:

- **cut** — type a new, smaller stop condition. The old one is recorded as
  deferred. Typing it makes the cut a specific commitment instead of a vague
  intention to hurry.
- **extend** — type a new budget *and* one line on what you learned that made it
  bigger. The second field is the point: an extension that costs nothing to make
  will be made every time, and the checkpoint exists precisely to catch that.

### 100% checkpoint

Budget exhausted. Three choices: `close` (drops into the postmortem), `cut`, or
`extend` — the latter two identical to p75.

### Timeout behaviour

A timed-out ping logs `ping_unanswered`. A timed-out checkpoint logs
`checkpoint_unanswered` and **re-fires after 10 minutes** — unlike a ping, a
checkpoint is a decision the loop cannot proceed without, so it is not allowed to
be silently skipped.

---

## 7. macOS hard block

`blockers/macos.py`, via PyObjC:

- One `NSWindow` per `NSScreen`, at `CGShieldingWindowLevel()` — above the menu
  bar, the Dock, and full-screen apps.
- `collectionBehavior = canJoinAllSpaces | stationary | fullScreenAuxiliary`, so
  switching Spaces does not escape it.
- `NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)` then
  `activateIgnoringOtherApps_(True)`.
- `NSApp.setPresentationOptions_` with `HideDock | HideMenuBar |
  DisableProcessSwitching | DisableForceQuit | DisableSessionTermination`.
  `DisableProcessSwitching` is what actually kills Cmd-Tab.
- A local key monitor handles `y`, `n`, `1`–`9`, `c`, `e`.
- Countdown: `NSProgressIndicator` plus a label, driven by a 1-second `NSTimer`.
- Multi-display: every screen is covered. The screen with the mouse shows the
  controls; the others show the same content dimmed.

### Escape hatch

There is deliberately no keyboard escape. Under stress, any escape becomes the
default path, which would make the gate ornamental.

Three independent guarantees ensure a bug in this code can never trap the user at
their own machine:

1. The overlay always self-destructs at 5:00 and logs the timeout.
2. A separate `NSTimer` at 5:10 calls `os._exit()` unconditionally. It fires even
   if the countdown handler is wedged, because it shares no state with it.
3. The daemon is a plain unprivileged user process. `pkill -f loop.sched.daemon`
   over SSH ends it, and any reboot clears it.

Guarantee 2 must be implemented as a genuinely independent timer, not a branch
inside the countdown callback. That independence is the whole point of it.

---

## 8. Portable tkinter blocker

`blockers/tk.py` implements the same `Prompt`/`Answers` contract using only the
standard library: `Toplevel` with `-fullscreen` and `-topmost`, `focus_force`, and
`grab_set_global` where the platform honours it.

It is a weaker block — Alt-Tab and Cmd-Tab still escape it. That is accepted. It
ships on day one, covers all three desktops, and serves as the macOS fallback when
PyObjC is unavailable.

`blockers/factory.py` selects: macOS with PyObjC present → `macos`; anything else →
`tk`. `LOOP_BLOCKER=tk|macos|fake` overrides, which is how tests and development
avoid getting a full-screen window every 20 minutes.

---

## 9. Thrash detector

A pure function over one loop's events:

```
thrashing if:
    active_elapsed >= 30m  and  actions_logged >= 5  and  hypotheses_eliminated == 0
  or
    the last 3 pings were all answered "n"
```

`active_elapsed`, not wall clock — a loop paused overnight has not been thrashing
for eight hours.

Two conditions because they catch different shapes of the same failure. The first
catches a long session of activity that ruled nothing out. The second catches a
session where you are answering honestly and the answer keeps being no.

When it fires, the ping overlay gains:

```
⚠  7 actions, 0 ruled out, 52m elapsed.
   you are thrashing.
   → step away 5m, or escalate.
```

---

## 10. CLI

```
loop open "<question>" [--budget 45m] [--interval 20m]
    prompts for: stop condition, time budget, hypotheses (at least one)
    a flag supplied on the command line skips its prompt; everything else is
    still asked, and stop condition and hypotheses have no flags at all —
    they must be typed, every time
    if a loop is active, offers to pause it and stack this one on top

loop try "<action>" --because "<belief>"
    both arguments mandatory; refuses to record an action without a belief

loop pause                 # prompts: what interrupted?
loop resume [<id>]         # defaults to the loop directly beneath on the stack
loop ls                    # the stack: active loop, paused loops, pause durations

loop hyp add "<text>"
loop hyp kill <n>
loop status                # active elapsed, budget, live hypotheses, action/elimination ratio
loop close                 # prompts: what was it? giveaway? how in 5 minutes?
loop abandon               # no postmortem; counted as abandoned in stats
loop stats
loop grep "<term>"         # searches postmortems and actions across closed loops
loop tick                  # internal: run one scheduler tick
```

Every command accepts `--json`.

```
$ loop open "prod 500s on /checkout"
  ⚠  #14 "staging deploy fails" is active
     → pause it and stack this on top? [y/n] y
     what interrupted? _______________

  #14 paused at 34m active.
  #15 open. stack depth 2.

$ loop close        # closes #15
  → resumed #14 · 34m / 45m · next ping 20m

$ loop ls
  ▶ #15  prod 500s on /checkout      12m active
    #14  staging deploy fails         34m active · paused 12m
    #11  flaky integration test        8m active · paused 3d  ⚠
```

**One loop is active at a time**, always. Others may be paused on the stack, but
they are inert. `loop close` and `loop abandon` both pop and auto-resume the
parent.

Three edges, specified so they are not invented at implementation time:

- **Nothing left to pop.** Closing the bottom loop leaves nothing active. The
  daemon exits and `loop ls` reports an empty stack. This is the normal end state,
  not an error.
- **Zero active loops.** `loop pause` on the only loop is legal — it is how you step
  away without abandoning. With nothing active, no timer runs and no overlay can
  fire. `loop resume` with no argument then picks the most recently paused loop.
- **Daemon lifecycle.** The daemon exits whenever no loop is active and is
  respawned by `loop open` or `loop resume`. Nothing else starts or stops it.

`loop close` cannot complete without all three postmortem answers. This is the
mechanism that builds the pattern library, so it does not get a skip flag.
`loop abandon` is the honest way out, and stats counts it as such.

---

## 11. Stats

```
protocol followed   : N loops | median Xm | Y% resolved
protocol abandoned  : N loops | median Xm | Y% resolved
thrash episodes     : N  (last 30d vs prior 30d)
estimate drift      : median X% over budget | N extensions across M loops
ping response       : N answered, M timed out
interruptions       : median N pauses/loop | median pause Xm | max depth reached N
```

**followed** = no ping timeouts and no checkpoint timeouts, however the loop
ended. **abandoned** = at least one timeout of either kind. **resolved** = ended
via `loop close` with a postmortem rather than `loop abandon`. Splitting the
buckets on protocol adherence alone keeps the resolved column meaningful —
if being closed were a precondition of *followed*, that column would read
100% by construction.

Every duration in this view is **active elapsed**. A loop that sat paused for two
days but took 40 active minutes reports 40 minutes, because that is the number your
estimates need to be calibrated against.

`estimate drift` is the median of `active_elapsed_at_close / budget_at_loop_opened
- 1` across closed loops — actual time versus the budget you first committed to,
not the budget you ended up with. The extension count beside it is the number of
`budget_extended` events over the number of loops that produced at least one.

`interruptions` is the line to watch if pause becomes the avoidance route. Pauses
per loop climbing while eliminations stay flat is the same thrash signature one
level up.

`thrash episodes` counts one episode per contiguous stretch in which the detector
was firing, so a single bad loop that stays thrashing for an hour counts once, not
once per ping.

Confidence-stated-versus-correct is deferred. It requires a confidence field on
`open`, which adds friction to the command that most needs to stay fast.

---

## 12. Testing

`core/` is pure, so it is tested directly with table tests. Required cases:

- `schedule.py`, wall clock: sleep/wake catch-up fires exactly one ping and rebases;
  extension re-arms p75 and p100; ping within 3 minutes of a checkpoint is skipped;
  a missed checkpoint fires on wake; a timed-out checkpoint re-fires after 10
  minutes.
- `schedule.py`, active clock: paused time never accrues to elapsed; a loop paused
  across a p75 boundary fires it only after resuming; resume rebases the next ping
  to `now + interval`; several pause/resume cycles sum correctly; machine sleep
  while active *does* accrue.
- `events.py`: `fold` over every event type; hypothesis IDs stay stable after a
  kill; stack invariants — at most one active loop at any point in the log, close
  and abandon both auto-resume the parent, `parent_id` chains reconstruct `loop ls`
  ordering, depth cap refuses a sixth push.
- `thrash.py`: both trigger conditions, and the boundaries either side of each;
  a long pause does not push a loop over the 30-minute threshold.
- `stats.py`: followed/abandoned classification, including the edge case of a loop
  with zero pings; interruption metrics over loops with zero and many pauses.

`store/`: temp-directory round-trip via `LOOP_HOME`; a truncated final line is
tolerated on read.

`FakeBlocker` returns scripted `Answers`, which gives full end-to-end daemon tests
against a fake clock with no GUI at all.

The real overlays get a manual smoke checklist — multi-display coverage, the
countdown, timeout behaviour, and that Cmd-Tab is genuinely blocked. This part
cannot be honestly automated, so it is not pretended otherwise.

---

## 13. Build order

1. `core/models` + `core/events` + `store/jsonl` + `open` / `try` / `close` /
   `status` — **usable on day one; the log starts filling immediately**
2. The stack: `pause` / `resume` / `ls`, stacked `open`, depth cap, and the active
   clock in `core/schedule` — **before the daemon, because every later timing rule
   is written against active elapsed and retrofitting that is a rewrite**
3. `blockers/base` + `FakeBlocker` + `blockers/tk` + `loop tick`
4. `sched/daemon` + the rest of `core/schedule` + the 20-minute ping —
   **walking skeleton**
5. p75 and p100 checkpoints, cut and extend
6. `core/thrash` + the warning panel
7. `blockers/macos` hard block
8. `stats` + `grep`

Steps 1–4 answer the one question that can kill this project: is a hard block every
20 minutes survivable in a real debugging session? That answer arrives before steps
5–8 are built, which is the point of the ordering.

---

## 14. Known risks

**Self-report tools have a brutal graveyard.** The person who most needs this is in
the state where they will not open it. The mitigation is the only one available:
every interaction under three seconds, in the terminal already open, and the
check-ins arrive without being asked for. If the friction turns out to be
intolerable, the walking skeleton reveals it in week one rather than week four.

**The log can become the avoidance.** Tidying loops instead of debugging is the
same failure the tool exists to catch, wearing the tool as a costume. There is no
code fix for this; it is named here so it can be recognised.

**Pause is the designed-in escape hatch.** Real interruptions demand it, so it
exists — but nothing stops it being used to dodge a checkpoint, and a paused loop
answers no questions. The counterweights are a mandatory reason, the depth cap, and
the `interruptions` line in stats. If pauses-per-loop climbs while eliminations stay
flat, the mechanism has been captured, and the fix is to tighten the depth cap
rather than to add more code.

**Time-to-first-overlay.** PyObjC import is roughly half a second. Acceptable for a
scheduled interrupt, but the daemon must import it at startup rather than at fire
time, so the overlay appears instantly when due.
