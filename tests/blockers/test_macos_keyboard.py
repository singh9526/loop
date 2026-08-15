"""The overlay must be able to receive typing.

Regression: the shields were plain `NSWindow`s with `NSBorderlessWindowMask`.
AppKit refuses key status to borderless windows, and only the key window
routes keystrokes to a first responder — so the `cut scope` / `extend
estimate` text fields appeared, accepted `makeFirstResponder_`, and then
swallowed every keystroke. A full-screen block with no way to answer it.

Nothing here orders a window on screen or starts a run loop.
"""

from __future__ import annotations

import pytest

macos = pytest.importorskip(
    "loop.blockers.macos", reason="PyObjC not installed"
)

from AppKit import (  # noqa: E402
    NSBackingStoreBuffered,
    NSBorderlessWindowMask,
    NSWindow,
)

RECT = ((0, 0), (400, 300))


def _window(cls):
    return cls.alloc().initWithContentRect_styleMask_backing_defer_(
        RECT, NSBorderlessWindowMask, NSBackingStoreBuffered, False
    )


def test_plain_borderless_window_cannot_take_key_status():
    """The premise of the fix. If AppKit ever changes this, the override
    below stops being load-bearing and this test says so."""
    assert _window(NSWindow).canBecomeKeyWindow() is False


def test_shield_window_can_take_key_status():
    window = _window(macos._ShieldWindow)

    assert window.canBecomeKeyWindow() is True
    assert window.canBecomeMainWindow() is True


class _RecordingWindow:
    """Accepts any AppKit message and records the ones under test."""

    def __init__(self, screen) -> None:
        self.screen = screen
        self.made_key = 0
        self.ordered_front = 0
        self.first_responder = None

    def makeKeyAndOrderFront_(self, _sender):
        self.made_key += 1

    def orderFront_(self, _sender):
        self.ordered_front += 1

    def makeFirstResponder_(self, view):
        self.first_responder = view

    def contentView(self):
        return self.screen

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def _build_on_screens(monkeypatch, screens, main_index):
    """Run `_build_windows` against fake screens, recording windows."""
    built: list[_RecordingWindow] = []

    class _Alloc:
        def initWithContentRect_styleMask_backing_defer_screen_(
            self, frame, mask, backing, defer, screen
        ):
            window = _RecordingWindow(screen)
            built.append(window)
            return window

    class _FakeWindowClass:
        @staticmethod
        def alloc():
            return _Alloc()

    class _FakeScreens:
        @staticmethod
        def screens():
            return screens

        @staticmethod
        def mainScreen():
            return screens[main_index]

    monkeypatch.setattr(macos, "_ShieldWindow", _FakeWindowClass)
    monkeypatch.setattr(macos, "NSScreen", _FakeScreens)
    monkeypatch.setattr(macos, "CGShieldingWindowLevel", lambda: 0)
    monkeypatch.setattr(
        macos, "NSView", type("V", (), {"alloc": staticmethod(lambda: _Alloc2())})
    )
    monkeypatch.setattr(macos._Overlay, "_build_controls", lambda self, c, f: None)

    overlay = macos._Overlay.__new__(macos._Overlay)
    overlay.windows = []
    overlay._main_window = None
    overlay._build_windows()
    return overlay, built


class _Alloc2:
    def initWithFrame_(self, _frame):
        return object()


class _FakeScreen:
    def __init__(self, name) -> None:
        self.name = name

    def frame(self):
        return ((0, 0), (100, 100))


def test_only_the_controls_window_takes_key_status(monkeypatch):
    """Every shield can become key now, so ordering them all in with
    `makeKeyAndOrderFront_` would hand the keyboard to whichever display
    came last out of `screens()`."""
    screens = [_FakeScreen("left"), _FakeScreen("right")]
    overlay, built = _build_on_screens(monkeypatch, screens, main_index=0)

    main = built[0]
    other = built[1]
    assert overlay._main_window is main
    assert main.made_key >= 1
    assert other.made_key == 0
    assert other.ordered_front == 1


def test_controls_window_reclaims_key_when_it_is_not_last(monkeypatch):
    """`screens()` gives no ordering promise: the main screen can come
    first and still must end up holding the keyboard."""
    screens = [_FakeScreen("left"), _FakeScreen("middle"), _FakeScreen("right")]
    overlay, built = _build_on_screens(monkeypatch, screens, main_index=0)

    assert built[0].made_key >= 1
    assert [w.made_key for w in built[1:]] == [0, 0]


def test_single_screen_still_takes_key(monkeypatch):
    screens = [_FakeScreen("only")]
    overlay, built = _build_on_screens(monkeypatch, screens, main_index=0)

    assert built[0].made_key >= 1
    assert built[0].ordered_front == 0
