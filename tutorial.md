# `loop open` — tutorial

## What it does

Opens a debugging loop. Starts a timer. Launches the desktop app (if it is not
already running), which interrupts you with a full-screen check-in at fixed
points to ask whether you are making progress.

The point: you cannot tell you are thrashing from inside the thrash. The tool asks
from outside.

## Install / invocation

The CLI lives in the project venv:

```bash
/Users/dilpreetsingh/Programming/Projects/loop/.venv/bin/loop
```

To get a bare `loop` on your PATH, symlink it into a directory already on PATH:

```bash
ln -s /Users/dilpreetsingh/Programming/Projects/loop/.venv/bin/loop ~/.local/bin/loop
```

The venv shebang is absolute, so the symlink self-selects the right interpreter —
no `activate` needed, works in scripts and subshells.

## Basic run

```bash
loop open "why is the stack trace empty?"
```

Three prompts follow. A real session:

```
  → stop condition? stack trace shows real caller
  → time budget? [45m] 30m
  → hypotheses? (one per line, blank line to finish)
  → 1. async wrapper swallows frame
  → 2. logging config filters it
  → 3.
  #1 open. timer running. 2 hypotheses live.
```

- **stop condition** — required. What makes this done. Not "fix bug" — an
  observable.
- **time budget** — Enter accepts the 45m default.
- **hypotheses** — at least one, blank line ends the list. These are what pings ask
  you to kill.

## Duration syntax

A bare number means **minutes**, deliberately (`loop/core/timefmt.py`).

| Input   | Means      |
| ------- | ---------- |
| `45`    | 45 minutes |
| `30m`   | 30 minutes |
| `2h`    | 2 hours    |
| `1h30m` | 90 minutes |
| `90s`   | 90 seconds |

Durations must be positive. Anything unparseable re-prompts.

## Flags

```bash
loop open "why is the stack trace empty?" --budget 30m --interval 10m
```

- `--budget` skips the budget prompt.
- `--interval` sets ping spacing. Defaults to **20m**, and is never prompted for —
  the flag is the only way to set it.
- `--json` emits the raw event and suppresses all prose.

Stop condition and hypotheses have no flags. They are always interactive.

## What happens after

The app polls every 10 seconds. There are three interrupt types, all measured on
**active** elapsed time — a loop that sat paused overnight has not been running for
eight hours.

**ping** — every `interval_s`:

```
loop #1 · 20m / 30m
hypothesis space smaller than 20m ago?   [y/n]
```

`y` then asks which hypothesis you killed. `n` is recorded as no progress.

**p75** — at 75% of budget:

```
is 75% of the work done?   [y] [c]ut scope [e]xtend estimate
```

**p100** — at 100% of budget:

```
budget is gone. what now?   [x] stop now  [c]ut scope  [e]xtend estimate
```

`x` records the decision and opens the postmortem there and then — the same
form as Actions ▸ Close Loop…, no terminal needed. `c` asks for a new stop
condition. `e` asks for a new budget plus *what did you learn that made it
bigger?* — that answer is what `loop stats` mines for estimate drift.

Timing details:

- Overlay times out at 300s.
- An unanswered checkpoint re-fires after 10 minutes.
- Pings are suppressed within 3 minutes of a checkpoint so the two never collide.
- Once p100 is reached, p75 is moot — only the highest passed boundary is live.

## Thrash detection

Fires inside the ping overlay when either condition holds
(`loop/core/thrash.py`):

- at least 30m elapsed **and** at least 5 actions **and** 0 hypotheses eliminated
- the last 3 pings were all `n`

```
⚠  7 actions, 0 ruled out, 45m elapsed.
   you are thrashing.
   → step away 5m, or escalate.
```

`loop try` is what feeds the actions counter — without it, detection is half-blind.

## Stacking

Running `loop open` while a loop is active pauses the parent and stacks on top:

```
  ⚠  #1 "why is the stack trace empty?" is active
  → pause it and stack this on top? [y/n]
```

- Depth 3 or more warns: "you are context switching, not working."
- Depth 5 is refused outright — close or abandon something first.

Ctrl-D at any prompt aborts and writes nothing; the parent stays active and the log
is untouched.

## Full cycle

```bash
loop open "why is the stack trace empty?" --budget 30m
loop try "added print in wrapper" --because "will show the swallowed frame"
loop hyp add "third-party lib strips frames"
loop hyp kill 2
loop status
loop close      # postmortem prompts
loop stats
```

`--because` is required on `try`. It forces you to state the belief before the
action, so a dead belief is visible later.

Other commands: `ls` (the stack), `pause` / `resume`, `abandon`, `grep <term>`
(search closed loops), `stats` (the logbook).

## Safe practice run

`LOOP_HOME` keeps a practice run's events out of your real data directory:

```bash
LOOP_HOME=/tmp/looptut loop open "test"
```

There is no headless bypass from the CLI anymore — `loop open` requires the
desktop app (`pip install 'loop-tool[gui]'`) and it will draw the real
full-screen check-in when a ping or checkpoint comes due. Use a short
`--interval` and `--budget` to keep a practice run brief rather than trying
to avoid the window.

Cleaning up: quit the app (tray/menu-bar icon → Quit) before deleting the
directory — there is no daemon or pidfile to kill by hand.

```bash
rm -rf /tmp/looptut
```

## Data

Real data lives at `~/.loop/events.jsonl` (macOS), append-only. Every command
appends one event; all state is a fold over that log. Two events from a real run:

```json
{"budget_s":1800.0,"hypotheses":["async wrapper swallows frame","logging config filters it"],"interval_s":1200.0,"loop_id":1,"parent_id":null,"question":"why is the stack trace empty?","stop_condition":"stack trace shows real caller","ts":1785836609.68,"type":"loop_opened"}
{"action":"added print in wrapper","because":"will show the swallowed frame","loop_id":1,"ts":1785836613.71,"type":"action_logged"}
```

Environment overrides:

- `LOOP_HOME` — data directory.

`loop open` / `loop resume` fail loudly at startup if the desktop app is not
installed, rather than arming a timer nothing can ever surface a check-in for.
