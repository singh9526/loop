"""The overlay must hand the machine back when it dismisses.

Regression: `run()` made the daemon a front-most `Accessory` application
and nothing ever undid it. Once `app.run()` returned, the daemon slept in
plain Python with no run loop pumping — leaving the user's front-most
application permanently unresponsive. The answer was recorded correctly and
the Mac still appeared frozen.

These tests drive `_release_ui` against a recording stand-in for
NSApplication. Nothing here builds a window or starts a run loop.
"""

from __future__ import annotations

import pytest

macos = pytest.importorskip(
    "loop.blockers.macos", reason="PyObjC not installed"
)

from loop.blockers.base import Choice, Prompt  # noqa: E402


class _FakeApp:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def setPresentationOptions_(self, options):
        self.calls.append(("presentation", options))

    def deactivate(self):
        self.calls.append(("deactivate",))

    def setActivationPolicy_(self, policy):
        self.calls.append(("policy", policy))

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


class _FakeWindow:
    def __init__(self) -> None:
        self.ordered_out = 0
        self.closed = 0

    def orderOut_(self, _sender):
        self.ordered_out += 1

    def close(self):
        self.closed += 1


def _overlay(monkeypatch) -> macos._Overlay:
    """An overlay with its AppKit dependencies replaced, built without
    touching `__init__` — which would call `NSApplication.sharedApplication`."""
    monkeypatch.setattr(macos, "_drain_run_loop", lambda *a, **k: None)

    overlay = macos._Overlay.__new__(macos._Overlay)
    overlay.prompt = Prompt(
        kind="ping", title="t", question="q", choices=[Choice("y", "yes")]
    )
    overlay.session = macos.PromptSession(overlay.prompt, shown_at=0.0)
    overlay.result = None
    overlay.windows = [_FakeWindow(), _FakeWindow()]
    overlay.field_views = {"a": object()}
    overlay.body = object()
    overlay.bar = object()
    overlay.countdown = object()
    overlay.monitor = None
    overlay._main_window = object()
    overlay._killer = None
    overlay._countdown_timer = None
    overlay.app = _FakeApp()
    return overlay


def test_release_ui_deactivates_and_drops_activation_policy(monkeypatch):
    overlay = _overlay(monkeypatch)

    overlay._release_ui()

    assert ("deactivate",) in overlay.app.calls
    assert (
        "policy",
        macos.NSApplicationActivationPolicyProhibited,
    ) in overlay.app.calls


def test_release_ui_restores_presentation_before_dropping_policy(monkeypatch):
    """Order is load-bearing: the Dock and menu bar must come back while the
    process is still a UI application."""
    overlay = _overlay(monkeypatch)

    overlay._release_ui()
    names = overlay.app.names()

    assert names.index("presentation") < names.index("deactivate")
    assert names.index("deactivate") < names.index("policy")


def test_release_ui_closes_every_window_not_just_the_main_one(monkeypatch):
    overlay = _overlay(monkeypatch)
    windows = list(overlay.windows)

    overlay._release_ui()

    assert [w.ordered_out for w in windows] == [1, 1]
    assert [w.closed for w in windows] == [1, 1]
    assert overlay.windows == []


def test_release_ui_is_idempotent(monkeypatch):
    """`run()`'s `finally` is the only caller today, but a second call must
    never double-close a window or re-drop an already-dropped policy."""
    overlay = _overlay(monkeypatch)
    windows = list(overlay.windows)

    overlay._release_ui()
    overlay._release_ui()

    assert [w.closed for w in windows] == [1, 1]
    assert overlay.body is None
    assert overlay.field_views == {}


def test_finish_does_not_tear_down_the_ui(monkeypatch):
    """`_finish` runs inside the event monitor, inside `app.run()`. Tearing
    down there would be re-entrant; it may only stop the run loop."""
    overlay = _overlay(monkeypatch)
    posted: list = []
    monkeypatch.setattr(macos, "_wake_event", lambda: object())
    overlay.app.stop_ = lambda sender: overlay.app.calls.append(("stop",))
    overlay.app.postEvent_atStart_ = lambda event, at_start: posted.append(event)
    windows = list(overlay.windows)

    overlay._finish(overlay.session.timed_out(at=1.0))

    assert ("stop",) in overlay.app.calls
    assert posted, "stop_ needs a following event to take effect"
    assert [w.closed for w in windows] == [0, 0]
    assert "policy" not in overlay.app.names()
