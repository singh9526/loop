"""The genuine block: a shielding window on every display, Cmd-Tab disabled.

Imports PyObjC at module import time, not at fire time, so that the daemon
pays the half-second cost at startup and the overlay appears instantly when
it is due.
"""

from __future__ import annotations

import os
import threading
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
        self._main_window: NSWindow | None = None
        self._killer: threading.Timer | None = None
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
            # Runs on every normal dismissal path (answered, timed out) and
            # on any exception unwinding through app.run() — a completed
            # prompt can never be killed by a stray fire ten seconds later.
            self._killer.cancel()

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
                self._main_window = window
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
            content.addSubview_(entry)
            self.field_views[field.name] = entry

        first = next(iter(self.field_views.values()))
        self._main_window.makeFirstResponder_(first)

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
