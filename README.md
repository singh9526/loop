# loop

A thrash detector for debugging.

You cannot tell you're thrashing from inside the thrash. `loop` starts a
timer on a debugging session, interrupts you at fixed points with a
full-screen check-in, and asks whether you're actually making progress —
from outside the loop you're stuck in.

<video src="docs/media/demo.mp4" controls muted width="100%"></video>

*(GitHub sometimes takes a moment to render the player above — [direct
link to the clip](docs/media/demo.mp4) if it doesn't load.)*

## Why

Debugging thrash — re-trying variations of the same fix, re-reading the
same stack trace, changing things without a hypothesis — is invisible to
the person doing it. `loop` externalizes the check: it makes you state a
stop condition and a hypothesis up front, then pings you on a schedule to
ask if the hypothesis space actually shrank.

## Install

```bash
pip install 'loop-tool[gui]'
```

The desktop app (a small cross-platform Qt overlay) is required — `loop
open` refuses to start a timer nothing can surface a check-in for.

## Quick start

```bash
loop open "why is the stack trace empty?" --budget 30m --interval 10m
```

Answers three prompts (stop condition, time budget, hypotheses), then runs
the loop:

```bash
loop try "added print in wrapper" --because "will show the swallowed frame"
loop hyp add "third-party lib strips frames"
loop hyp kill 2
loop status
loop close      # postmortem prompts
loop stats
```

Full walkthrough, flags, and the thrash-detection rules: [tutorial.md](tutorial.md).

## How it works

- **ping** — every `--interval`, asks if the hypothesis space is smaller
  than it was.
- **p75 / p100** — at 75% and 100% of budget, forces a decision: cut scope,
  extend the estimate, or stop.
- **thrash warning** — fires when 30+ minutes and 5+ actions have passed
  with zero hypotheses eliminated, or the last three pings all said "no
  progress."

All state is a fold over an append-only event log at `~/.loop/events.jsonl`
— every command appends one event, nothing is mutated in place.

## Data & privacy

Everything stays local. `LOOP_HOME` overrides the data directory, useful
for a sandboxed practice run:

```bash
LOOP_HOME=/tmp/looptut loop open "test"
```

## Development

```bash
git clone https://github.com/singh9526/loop.git
cd loop
pip install -e '.[gui,dev]'
pytest
```
