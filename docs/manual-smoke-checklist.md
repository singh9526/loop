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

## While a check-in is up: nothing else may run

The overlay is always-on-top, but on macOS that does **not** cover the menu
bar or the status-item area, and on both platforms a keyboard shortcut can
still reach a window behind it. Every one of the ten actions opens an
*application-modal* dialog, which would render beneath the overlay while
`_insist()` re-raises over it once a second and modality blocks keys to the
check-in — and `ask()` cannot take the overlay down until that invisible
dialog is dismissed. There is no watchdog process left to kill either
window. `Scheduler.busy_changed` is what refuses this; confirm it by hand:

- [ ] With a check-in on screen, pull down **Actions** in the menu bar.
      Every item is greyed out — **Close Loop…** in particular cannot be
      chosen, and no dialog appears anywhere.
- [ ] Click the tray/status-item icon and choose **Quit** while the
      check-in is up. Nothing happens: no confirmation box (it would be
      invisible under the overlay), no quit, and the check-in is still
      answerable.
- [ ] Answer the check-in. The menu items are live again immediately —
      including the row buttons on the dashboard (rule-out, resume) —
      and the tray's Quit confirms as usual.

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

## Windows-only checks

Task 17 audited every platform-dependent path by reading and by
simulating `sys.platform`/`os.name` in tests — there is no Windows
machine in that pass, so **none of the 13 items below has actually
run.** Work through them in order on a real Windows box before calling
Windows support done. Items 1–2 are the original build plan's own
Step 1/Step 2, restated here as their own checklist entries rather than
left implicit — a prior draft of this section dropped them, along with
items 3 and 6, silently. The rest are specific things the audit could
not settle from macOS, not a vague "test on Windows" reminder.

1. [ ] **Install and run the automated suite on Windows.** Clean venv:
       `pip install -e ".[gui,dev]"` then `python -m pytest -v`.
       Expected: all pass. This is the first real confirmation that
       anything below is even reachable — `tests/store/
       test_lock_windows.py` and `tests/app/test_launcher.py`'s Windows
       tests only ever ran *simulated* on macOS; this is where they run
       for real.
2. [ ] **`msvcrt.locking`'s real errno behaviour under contention.**
       `loop/store/lock.py`'s Windows `_try_acquire` now catches only
       `PermissionError` (Task 17's fix). That rests on Microsoft's
       documented `_locking` errno list — `EACCES` on a non-blocking
       contended lock, `EBADF` on a bad handle, `EDEADLOCK` only for the
       blocking `LK_LOCK`/`LK_RLCK` modes this code does not use — never
       executed. Confirm a second process's `LK_NBLCK` attempt on an
       already-locked byte range really does raise `PermissionError`
       (not some other `OSError`) on this Windows version/filesystem.
3. [ ] **The two-process concurrency tests, through the real `msvcrt`
       path.** `tests/store/test_lock.py::
       test_concurrent_appends_do_not_interleave_or_vanish` and
       `::test_a_held_lock_times_out_a_second_acquirer` spawn real
       subprocesses and contain no platform branch of their own — they
       run as part of item 1's full suite, but call them out
       specifically: they are the ones that prove the lock actually
       serializes writers under Windows locking, not just that
       `_try_acquire` returns the right booleans in isolation.
4. [ ] **`%APPDATA%\loop`.** Confirm `loop_home()` resolves to the real
       per-user roaming folder, including when the Windows username
       contains a space or a non-ASCII character.
5. [ ] **Exercise the app end to end (brief's Step 2).**
       ```powershell
       $env:LOOP_HOME="$env:TEMP\loop-scratch"
       python -m loop.gui
       ```
       The window opens and a tray icon appears in the notification
       area; closing the window leaves the app running in the tray (see
       "The dashboard and tray" above for the generic version of this
       check — do it once here specifically on Windows, not just read
       as already covered because the words match).
6. [ ] **Second-instance hand-off, on real Windows named pipes.** A
       second `python -m loop.gui` exits immediately and re-surfaces the
       first window. `loop/gui/instance.py`'s `claim()`/`_probe()` logic
       is platform-agnostic and its *decision logic* is tested (pinned
       by `tests/gui/test_shell.py`), but real `QLocalServer`/
       `QLocalSocket` named-pipe creation, connection, and teardown
       timing on Windows was never exercised.
7. [ ] **Fonts** (`loop/gui/theme.py`). `Cascadia Mono` and `Segoe UI
       Variable Text` must both resolve — check the clock/countdown
       (`#clock`/`#overlay_countdown`, mono) and the body text (sans)
       visually render in those faces, not a fallback. This matters more
       than it looks: confirmed from this machine (platform-independent,
       not a guess) that Qt's style-sheet engine does **not** implement
       CSS's generic `monospace`/`sans-serif` keywords — they are the
       last entries in both font stacks and are inert. If none of the
       named faces resolve on a given Windows build, the fallback is not
       a generic serif/sans-serif, it is Qt's default application font,
       which need not be monospaced at all. `Consolas` and `Segoe UI`
       (the two pre-Windows-11-safe entries) ship with every supported
       Windows version, so this should not be reachable — confirming
       that is the point of this item.
8. [ ] **Theme follows the OS setting.** Change Windows to light mode,
       relaunch, confirm the palette follows (`detect_mode` reads
       `QStyleHints.colorScheme()`, added in Qt 6.5; `pyproject.toml`
       pins `PySide6>=6.6`, so the API should exist — never confirmed
       against a real Windows light/dark toggle).
9. [ ] **Multi-monitor with mixed DPI.** `_cover_all_screens()` unions
       `QGuiApplication.screens()` geometry — a Qt API, not
       platform-specific code — but Windows commonly runs monitors at
       different per-monitor scale factors (100%/150%/200%) in a way
       macOS's Retina scaling does not typically mix on one desktop.
       Confirm the check-in still covers every screen edge-to-edge with
       no gap or overlap at the seam between differently-scaled
       monitors.
10. [ ] **Single-instance reclaim after a hard kill.** Start the app,
        kill it from Task Manager (End Task, not a clean quit), then
        launch again immediately. On POSIX a killed process can leave a
        stale Unix-domain socket file that `loop/gui/instance.py`'s
        `removeServer` + re-probe exists to clear. Windows named pipes
        are released by the kernel when the owning process dies, with
        no leftover pipe-file analogue — so this path is expected to
        rarely or never actually execute there, and the next launch
        should succeed on the first `listen()`. Confirm the reclaim is
        at least as fast as on macOS, not slower or stuck.
11. [ ] **Check-in window in the taskbar / Alt-Tab.** Task 13 dropped
        `Qt.Tool` in favour of `Qt.Window` specifically because macOS
        hides `Qt.Tool` windows on app deactivation. Windows has no
        equivalent auto-hide behaviour, but `Qt.Tool` on Windows also
        suppresses the taskbar entry and Alt-Tab entry — so this
        window, being `Qt.Window`, will show both there in a way it
        would not have under `Qt.Tool`. Confirm that reads as
        acceptable (it does not affect always-on-top or the
        once-a-second re-raise) rather than as a surprise.
12. [ ] **No console flash on auto-launch.** `loop open` from a
        terminal running `python.exe` (not `pythonw.exe`) calls
        `launcher.ensure_running()`, which spawns
        `[sys.executable, "-m", "loop.gui"]` with
        `creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`
        instead of POSIX's `start_new_session`. Confirm no black
        console window flashes or lingers when the app auto-launches
        this way.
13. [ ] **Tray icon click behaviour.** Windows distinguishes left-click
        (`Trigger`) from right-click (`Context`) more strictly than
        macOS does; `Tray` in `loop/gui/tray.py` calls
        `window.surface()` on *every* activation reason, including a
        right-click that is also opening the context menu. Confirm
        this does not look broken (e.g. the window popping up jarringly
        under the menu) on Windows.

Then delete the scratch directory (`$env:LOOP_HOME`, item 5) the same
way the generic checklist's own cleanup does below.

## Cleanup

```bash
rm -rf /tmp/loop-smoke
```
