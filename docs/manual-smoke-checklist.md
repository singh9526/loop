# Manual smoke checklist

## Prerequisites

This checklist needs a real GUI session with PySide6 installed
(`pip install -e '.[gui]'`). The environment this file was authored in has
no display, so **every item below is currently unrun** — this document has
not been executed even once. Treat it as unverified until someone runs it
by hand.

There is exactly one check-in window now — `loop/gui/checkin.py`,
drawn by Qt — used on both macOS and Windows. It replaces the old
`blockers/macos.py` (PyObjC) and `blockers/tk.py` (Tk) overlays, both
deleted. There is no daemon: the app itself polls for due check-ins
(`loop/gui/scheduler.py`, every 10s) and draws them from the same
process that owns the dashboard and tray icon.

A full-screen shielding window has no honest automated assertion surface
for "is this actually on screen, on top, and taking focus" — that is what
this checklist is for. Run it by hand after touching `loop/gui/checkin.py`
or `loop/gui/scheduler.py`.

Set up a throwaway log so the real one stays clean:

```bash
export LOOP_HOME=/tmp/loop-smoke
python -m loop.cli open "smoke test" --budget 4m --interval 1m
python -m loop.gui
```

`loop open` starts the timer and launches the app if it is not already
running (`loop.app.launcher.ensure_running`); running `python -m loop.gui`
directly is equivalent and more visible while testing. A second launch of
either should not open a second window — it hands off to the first and
asks it to surface (`loop/gui/instance.py`).

## The dashboard and tray

- [ ] The dashboard window and a tray/menu-bar icon both appear.
- [ ] The tray icon's dot changes color as burn crosses 75% and 100%
      (`loop/gui/tray.py::burn_colour`).
- [ ] Closing the dashboard window hides it, not quits the app — the tray
      icon remains and check-ins keep firing.
- [ ] The tray menu's "Show loop" re-opens the dashboard.
- [ ] Quitting from the tray while a loop is active asks for confirmation
      first.
- [ ] Launching a second `python -m loop.gui` while the first is up does
      not open a second window; it raises the existing one.

## The 20-minute ping (here: 1 minute)

- [ ] The check-in appears without being asked for, within about ten
      seconds of the interval (the scheduler polls every 10s).
- [ ] **It covers every display.** Check with an external monitor attached
      — the window unions all screens' geometry (`_cover_all_screens`).
- [ ] It stays on top and re-raises itself roughly once a second
      (`_insist`) — try switching Spaces or clicking another app to steal
      focus, and confirm it comes back.
- [ ] It has no title bar, no close button, and Escape does nothing.
- [ ] The countdown label counts down `5:00 → 0:00`.
- [ ] Pressing `n` dismisses it instantly and writes `ping_answered` with
      `smaller: false`.
- [ ] Pressing `y` reveals the numbered live hypotheses under "which
      died?"; pressing a number dismisses and writes both `ping_answered`
      and `hypothesis_eliminated`.
- [ ] Ignoring it entirely dismisses at 5:00 and writes `ping_unanswered`.
- [ ] **Legibility at arm's length:** the title/question/countdown use the
      overlay type scale in `theme.py` (32px question, larger than the
      dashboard's own text). Confirm it actually reads comfortably from a
      normal viewing distance on a real display — this was a deliberate
      design call and is the first thing worth re-checking here.

## The 75% checkpoint (here: 3 minutes)

- [ ] Fires at 75% of the budget, and the ping due near it is suppressed.
- [ ] `y` dismisses and logs `decision: continue`.
- [ ] `c` reveals one text field; submitting writes `checkpoint_answered`
      **then** `scope_cut`, in that order.
- [ ] `e` reveals two text fields; submitting with `learned` blank
      re-states "required: learned" on screen rather than refusing
      silently, and does **not** clear what was already typed into
      `new_budget`.
- [ ] After extending to 8m, both p75 and p100 fire again against the new
      budget.
- [ ] Typing a nonsense budget such as `soon` logs
      `checkpoint_unanswered`, and the checkpoint returns ten minutes
      later.
- [ ] **Multi-monitor:** with an external display attached, put focus on
      the *non-primary* screen (e.g. click into a window there) just
      before a checkpoint fires. The title/body text, the `c`/`e`
      text-entry fields, and the keyboard focus must all land on the
      union window that covers both screens — typing must go straight
      into the field without an extra click.
- [ ] **Focus delivery:** click into the `new_stop_condition` or
      `new_budget` field, type a few characters, and confirm the
      once-a-second re-raise (`_insist`) does not steal the caret away
      mid-sentence.

## The 100% checkpoint

- [ ] Fires when the budget is exhausted, offering `x` / `c` / `e`.
- [ ] `x` logs `decision: close` and does **not** fire again.

## Escape guarantees (and their absence)

Unlike the deleted macOS overlay, the Qt window is **insistent, not
inescapable** — this is a deliberate trade-off (one implementation for
both platforms) documented in `loop/gui/checkin.py`'s module docstring.
Confirm the trade actually looks the way the docstring claims:

- [ ] Cmd-Tab / Alt-Tab still works — switching away is possible, unlike
      the old macOS overlay which disabled it outright.
- [ ] The menu bar and Dock (macOS) or taskbar (Windows) are **not**
      hidden — only the check-in window itself is drawn.
- [ ] Quitting the app entirely (tray → Quit, confirming if a loop is
      active) clears the window immediately; there is no separate daemon
      process to kill and no pidfile to go stale.
- [ ] If the app is killed hard (e.g. `kill -9` the `loop.gui` process),
      the window disappears with it — no leftover shielding window, no
      leftover tray icon (may need a Space/session switch to confirm the
      tray icon itself clears, which is OS-dependent and not this app's
      code).

## Cleanup

```bash
rm -rf /tmp/loop-smoke
```
