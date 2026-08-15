"""The genuine block: a shielding window on every display, Cmd-Tab disabled.

Imports PyObjC at module import time, not at fire time, so that the daemon
pays the half-second cost at startup and the overlay appears instantly when
it is due.
"""

from __future__ import annotations

import os
import threading
import time

from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSApplicationActivationPolicyProhibited,
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
    NSRunLoop,
    NSScreen,
    NSTextField,
    NSTimer,
    NSView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
)
from Foundation import NSDate
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

DRAIN_S = 0.25

BG = (0.063, 0.063, 0.078, 0.97)
FG = (0.90, 0.90, 0.90, 1.0)
DIM = (0.54, 0.54, 0.58, 1.0)
WARN = (1.0, 0.37, 0.34, 1.0)  # missing-required-field marker, set in _mark_missing_fields


class _ShieldWindow(NSWindow):
    """A borderless window that will accept keyboard focus.

    AppKit refuses key status to `NSBorderlessWindowMask` windows by
    default, and only the key window routes keystrokes to a first
    responder. Without these overrides the `cut scope` and `extend
    estimate` text fields draw, take `makeFirstResponder_`, and then
    silently swallow every keystroke — a full-screen block with no way to
    answer it and no way out but the 300s timeout.
    """

    def canBecomeKeyWindow(self) -> bool:
        return True

    def canBecomeMainWindow(self) -> bool:
        return True


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
        self._main_window: NSWindow | None = None
        self._killer: threading.Timer | None = None
        self._countdown_timer: NSTimer | None = None
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
        self._countdown_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.2, True, self._on_countdown
        )
        # Guarantee 2: lives off the run loop entirely, on its own OS thread,
        # so it fires even if the run loop itself is wedged. A second NSTimer
        # cannot do that — it would share the same run loop as the countdown
        # timer and be blocked by whatever blocked it. Never fold this into
        # _on_countdown, and never reschedule it on the run loop.
        self._killer = threading.Timer(KILL_AFTER_S, lambda: os._exit(1))
        self._killer.daemon = True
        self._killer.start()

        try:
            self.app.run()
        finally:
            # Runs on every dismissal path (answered, timed out) and on any
            # exception unwinding through app.run(). All UI teardown lives
            # here and nowhere else: this is the one point where `app.run()`
            # is guaranteed to have returned, which is what makes it both
            # safe to drain the run loop and the last moment anything can.
            self._killer.cancel()
            self._release_ui()

        if self.result is None:
            self.result = self.session.timed_out(at=time.time())
        return self.result

    def _invalidate_countdown(self) -> None:
        """Safe to call any number of times, including after the timer is
        already gone — the `None` guard, not `NSTimer.invalidate()` alone,
        is what makes a second call from the `finally` in `run()` a no-op."""
        if self._countdown_timer is not None:
            self._countdown_timer.invalidate()
            self._countdown_timer = None

    def _release_ui(self) -> None:
        """Give the screen, the keyboard, and the Dock back.

        Between prompts the daemon sleeps in plain Python — nothing pumps
        this NSApplication's run loop, for `POLL_S` at a time and for the
        whole gap between check-ins. Anything left activated here is
        therefore a front-most application that answers no events, which is
        precisely what a spinning beachball and an apparently frozen Mac
        are. Deactivating and dropping back to `Prohibited` leaves the
        process registered as no kind of application at all, so being
        un-pumped is harmless; `run()` re-registers it as `Accessory` for
        the next prompt.

        Every step is idempotent, and the ordering is load-bearing: hand the
        windows back before dropping the activation policy, and drain before
        clearing the Python references the drained events might touch.
        """
        self._invalidate_countdown()
        if self.monitor is not None:
            NSEvent.removeMonitor_(self.monitor)
            self.monitor = None
        self.app.setPresentationOptions_(0)
        for window in self.windows:
            window.orderOut_(None)
            window.close()
        self.windows.clear()
        self.app.deactivate()
        _drain_run_loop()
        self.app.setActivationPolicy_(NSApplicationActivationPolicyProhibited)
        self.field_views.clear()
        self.body = None
        self.bar = None
        self.countdown = None
        self._main_window = None

    def _finish(self, answers: Answers) -> None:
        """Called from inside the event monitor, so from inside `app.run()`.

        It only records the answer and asks the run loop to end — tearing
        the UI down from here would mean doing it re-entrantly. `run()`'s
        `finally` owns that, and is reached within microseconds.
        """
        self.result = answers
        self._invalidate_countdown()
        self.app.stop_(None)
        # stop_ only takes effect after the next event, so post one.
        # `self.app` is `sharedApplication()` — there is only ever one.
        self.app.postEvent_atStart_(_wake_event(), True)

    # -- windows -----------------------------------------------------------

    def _build_windows(self) -> None:
        screens = list(NSScreen.screens())
        main_screen = NSScreen.mainScreen()
        if main_screen is None or main_screen not in screens:
            # `mainScreen()` can return nil, or something not in `screens()`
            # (a display that just went to sleep, an odd multi-monitor
            # transition). Without a fallback, `_build_controls` never runs,
            # `self.body` stays None, and the next `_render()` call raises
            # an AttributeError that kills the daemon.
            main_screen = screens[0] if screens else None

        for screen in screens:
            window = _ShieldWindow.alloc().initWithContentRect_styleMask_backing_defer_screen_(
                screen.frame(), NSBorderlessWindowMask, NSBackingStoreBuffered, False, screen
            )
            window.setLevel_(CGShieldingWindowLevel())
            window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorStationary
                | NSWindowCollectionBehaviorFullScreenAuxiliary
            )
            # `_release_ui` calls `close()` to actually dispose of the
            # window rather than only hiding it. The default would then
            # release an object PyObjC still holds a reference to.
            window.setReleasedWhenClosed_(False)
            window.setOpaque_(False)
            window.setBackgroundColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(*BG))

            is_main = screen == main_screen
            # Every display is a click-through shield except the one holding
            # the controls — that one must accept clicks, or a user on the
            # extend flow can never click into the text fields.
            window.setIgnoresMouseEvents_(not is_main)

            content = NSView.alloc().initWithFrame_(screen.frame())
            window.setContentView_(content)

            if is_main:
                self._main_window = window
                self._build_controls(content, screen.frame())

            # Only the window holding the controls may take key status.
            # Every shield can now accept it, so ordering them all in with
            # `makeKeyAndOrderFront_` would hand the keyboard to whichever
            # display happens to come last out of `screens()` — on a
            # multi-monitor desk, usually not the one with the text fields.
            if is_main:
                window.makeKeyAndOrderFront_(None)
            else:
                window.orderFront_(None)
            self.windows.append(window)

        # `screens()` does not promise the main screen comes last, and
        # ordering a shield in front can still displace key status.
        if self._main_window is not None:
            self._main_window.makeKeyAndOrderFront_(None)

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
        content = self._main_window.contentView()
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
            entry.setSelectable_(True)
            content.addSubview_(entry)
            self.field_views[field.name] = entry

        # Chain Tab through every field, wrapping the last back to the
        # first — `EXTEND_FIELDS` has two required fields, and without this
        # chain there is no keyboard path from the first to the second.
        views = list(self.field_views.values())
        for current, following in zip(views, views[1:]):
            current.setNextKeyView_(following)
        if views:
            views[-1].setNextKeyView_(views[0])
            # `makeFirstResponder_` only routes keystrokes if this window is
            # the key window. The fields are created a keystroke after the
            # windows were built, by which time anything could have taken
            # key — so claim it here rather than trusting `_build_windows`.
            self.app.activateIgnoringOtherApps_(True)
            self._main_window.makeKeyAndOrderFront_(None)
            self._main_window.makeFirstResponder_(views[0])

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
                missing = self.session.submit_fields(values)
                self._mark_missing_fields(missing)
                if not missing:
                    self._finish(self.session.answers(answered_at=time.time()))
                return None
            return event  # let the text field have the keystroke

        if self.session.press_key(str(event.characters() or "").lower()):
            self._render()
            if self.session.is_complete():
                self._finish(self.session.answers(answered_at=time.time()))
        return None  # swallow everything else

    def _mark_missing_fields(self, missing: list[str]) -> None:
        """Give a missing required field a visible marker — otherwise a user
        who cannot find the empty field is wedged in a full-screen block
        until the 300s timeout with no feedback at all. Every field is
        re-marked on every attempt, not just the missing ones, so a field
        highlighted on an earlier attempt is cleared the moment it is
        filled in, rather than staying red for the rest of the prompt."""
        warn = NSColor.colorWithCalibratedRed_green_blue_alpha_(*WARN)
        default = NSColor.textBackgroundColor()
        for name, view in self.field_views.items():
            view.setBackgroundColor_(warn if name in missing else default)


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


def _drain_run_loop(seconds: float = DRAIN_S) -> None:
    """Pump queued events so the teardown reaches the window server.

    `app.run()` has already returned by the time this is called, and the
    daemon will not pump again until the next prompt — so an ordered-out
    window or a restored menu bar that is still sitting in the queue would
    stay queued. This is the only thing that delivers them.
    """
    NSRunLoop.currentRunLoop().runUntilDate_(
        NSDate.dateWithTimeIntervalSinceNow_(seconds)
    )


def _wake_event():
    from AppKit import NSApplicationDefined, NSEvent, NSPoint

    return NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
        NSApplicationDefined, NSPoint(0, 0), 0, 0, 0, None, 0, 0, 0
    )
