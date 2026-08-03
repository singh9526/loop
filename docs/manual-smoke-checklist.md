# Manual smoke checklist

## Prerequisites

This checklist needs a real macOS GUI session with the relevant toolkit
installed. Neither is present in the environment this file was authored in
(no PyObjC, no `_tkinter`), so **every item below is currently unrun** — this
document has not been executed even once. Treat it as unverified until
someone runs it by hand.

- **macOS overlay** (`LOOP_BLOCKER=macos`): install PyObjC —
  `pip install -e '.[macos]'` (equivalently
  `pip install 'pyobjc-framework-Cocoa>=10.0' 'pyobjc-framework-Quartz>=10.0'`).
- **tk overlay** (`LOOP_BLOCKER=tk`): needs a Python built with `_tkinter` —
  e.g. `brew install python-tk@3.13` on macOS, or the distro's `python3-tk`
  package on Linux. Many Python builds (including this project's dev venv)
  omit it.

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
      is blank. On the tk overlay the blank field is highlighted; the macOS
      overlay refuses silently, with no highlight — that is a known
      difference between the two, not a bug to chase here.
- [ ] After extending to 8m, both p75 and p100 fire again against the new budget.
- [ ] Typing a nonsense budget such as `soon` logs `checkpoint_unanswered`,
      and the checkpoint returns ten minutes later.
- [ ] **Multi-monitor:** with an external display attached, put focus on the
      *non-primary* screen (e.g. click into a window there) just before a
      checkpoint fires. The title/body text, the `c`/`e` text-entry fields,
      and the keyboard focus must all land on that same screen — not split
      across displays. This exercises the overlay tracking one specific
      window rather than assuming the first display in screen order.

## The 100% checkpoint

- [ ] Fires when the budget is exhausted, offering `x` / `c` / `e`.
- [ ] `x` logs `decision: close` and does **not** fire again.

## Escape guarantees

- [ ] With the overlay up, `pkill -f loop.sched.daemon` from another machine
      over SSH clears the screen and restores the menu bar and Dock.
- [ ] Add `time.sleep(600)` to the top of `_on_countdown`, rebuild, and confirm
      the kill timer still terminates the process at 5:10 — even though the
      Cocoa run loop itself is completely frozen for the full 600 seconds.
      This is what the kill timer being a `threading.Timer` on its own OS
      thread (not a second `NSTimer` sharing the run loop) actually buys:
      a same-run-loop timer could not survive this test, since the sleep
      blocks the one thread both timers would otherwise share. **Remove the
      sleep afterwards.**

## Cleanup

```bash
rm -rf /tmp/loop-smoke
```
