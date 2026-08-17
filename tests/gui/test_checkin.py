"""The full-screen check-in.

Most tests drive the window's own methods. The few that call `ask()` rely
on `conftest.py` forcing QT_QPA_PLATFORM=offscreen, where `show()` draws
into a buffer and no window exists — on a real platform `ask()` covers
every display for five minutes, so that fixture is load-bearing, not
hygiene.

Every wait carries a watchdog: `pyproject.toml` sets no test timeout, so a
regression that stops something settling would otherwise hang pytest
forever instead of failing.
"""

from __future__ import annotations

import dataclasses
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import QFrame, QLabel, QPushButton

from loop.blockers.base import Choice, Prompt, TextField
from loop.gui.checkin import CheckinWindow, QtBlocker

WATCHDOG_MS = 2000


class Clock:
    """A `now` the test moves by hand; the window never reads the wall."""

    def __init__(self, at: float = 0.0) -> None:
        self.at = at

    def __call__(self) -> float:
        return self.at


def ping_prompt():
    return Prompt(
        kind="ping",
        title="loop #3 · 18:42 / 45:00 · 2 paused",
        question="hypothesis space smaller than 20m ago?",
        choices=[Choice("y", "yes"), Choice("n", "no")],
        pick_list=["dictConfig disables existing loggers", "propagate=False"],
        pick_after="y",
    )


def p75_prompt():
    return Prompt(
        kind="p75",
        title="75% of budget gone · 33:45 / 45:00",
        question="is 75% of the work done?",
        choices=[Choice("y", "yes"), Choice("c", "cut scope"), Choice("e", "extend estimate")],
        fields_after={
            "c": [TextField("new_stop_condition", "new stop condition")],
            "e": [TextField("new_budget", "new budget"),
                  TextField("learned", "what did you learn that made it bigger?")],
        },
    )


def window(prompt, at=0.0):
    return CheckinWindow(prompt, mode="dark", now=lambda: at)


def polished(view):
    """Stylesheet-derived font properties do not exist until a polish.

    Reading `font()` on an unpolished widget reports the application
    default and quietly hides whatever the stylesheet is about to do to
    it — which is how an uppercased title survived the first round of
    tests.
    """
    view.ensurePolished()
    for widget in view.findChildren(QLabel) + view.findChildren(QPushButton):
        widget.ensurePolished()
    return view


def strings_on_screen(view):
    return view.findChildren(QLabel) + view.findChildren(QPushButton)


def watchdog(view, late):
    """Break a wait that should already be over, and mark that it had to.

    `view.settled.emit()` quits the nested loop from outside; `late`
    records that nothing else did. The assertion on `late` is what turns
    an infinite hang into a two-second failure.
    """
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: (late.append(True), view.settled.emit()))
    timer.start(WATCHDOG_MS)
    return timer


def test_the_prompt_text_reaches_the_screen_verbatim(qapp):
    """These strings are the product, read off the widgets rather than off
    the `Prompt` the test just handed in — asserting the dataclass fields
    proves only that Python assignment works.

    A stylesheet that uppercases is a paraphrase: `QLabel#label` carries
    `text-transform: uppercase`, Qt honours it as a font capitalization,
    and the title rendered `LOOP #3 · 18:42 / 45:00 · 2 PAUSED`.
    """
    view = polished(window(ping_prompt()))
    assert view._title.text() == "loop #3 · 18:42 / 45:00 · 2 paused"
    assert view._question.text() == "hypothesis space smaller than 20m ago?"
    assert [button.text() for button in view.choice_buttons()] == ["yes", "no"]
    assert view._title.font().capitalization() != QFont.AllUppercase
    # The accessors and the widgets must not be able to disagree.
    assert view.title_text() == view._title.text()
    assert view.question_text() == view._question.text()


def test_no_string_in_the_overlay_is_transformed_by_the_stylesheet(qapp):
    """Every stage, not just the first: the `[y]` hints, the pick lead and
    the field labels all wore `#label` too."""
    revealed_picks = window(ping_prompt())
    revealed_picks.press("y")
    revealed_fields = window(p75_prompt())
    revealed_fields.press("c")
    for view in (revealed_picks, revealed_fields):
        for widget in strings_on_screen(polished(view)):
            assert widget.font().capitalization() != QFont.AllUppercase, widget.text()

    assert "which died?" in [w.text() for w in strings_on_screen(revealed_picks)]
    assert "new stop condition" in [w.text() for w in strings_on_screen(revealed_fields)]
    assert "[y]" in [w.text() for w in strings_on_screen(revealed_picks)]


def test_the_overlay_renders_at_display_sizes(qapp):
    """Proof the object names reach the overlay rules — and the reason
    they exist: 18px question text on a window covering the whole display
    is not a check-in anyone reads."""
    view = polished(window(ping_prompt()))
    assert view._question.font().pixelSize() == 32
    assert view._title.font().pixelSize() == 18
    assert view._countdown.font().pixelSize() == 16
    assert view.choice_buttons()[0].font().pixelSize() == 22


def test_answering_no_completes_immediately(qapp):
    view = window(ping_prompt())
    view.press("n")
    assert view.is_complete()
    answers = view.answers()
    assert answers.choice == "n"
    assert answers.picked is None
    assert answers.timed_out is False


def test_answering_yes_reveals_the_pick_list(qapp):
    view = window(ping_prompt())
    view.press("y")
    assert not view.is_complete()
    assert view.visible_pick_list() == [
        "dictConfig disables existing loggers", "propagate=False",
    ]
    view.press("2")
    assert view.is_complete()
    assert view.answers().picked == 2


def test_an_out_of_range_pick_is_ignored(qapp):
    view = window(ping_prompt())
    view.press("y")
    view.press("9")
    assert not view.is_complete()


def test_choosing_extend_reveals_its_fields_in_place(qapp):
    view = window(p75_prompt())
    view.press("e")
    assert [field.name for field in view.pending_fields()] == ["new_budget", "learned"]
    assert not view.is_complete()


def test_blank_required_fields_are_refused_without_completing(qapp):
    view = window(p75_prompt())
    view.press("e")
    assert view.submit_fields({"new_budget": "", "learned": "x"}) == ["new_budget"]
    assert not view.is_complete()
    assert view._missing.text() == "required: new_budget"


def test_a_second_attempt_clears_the_first_attempts_complaint(qapp):
    """A field flagged on attempt one must stop being flagged once it is
    filled in — the bug `tk.py` carried a comment about."""
    view = window(p75_prompt())
    view.press("e")
    view.submit_fields({"new_budget": "", "learned": "x"})
    assert view.submit_fields({"new_budget": "1h", "learned": "x"}) == []
    assert view._missing.text() == ""


def test_insisting_does_not_steal_the_caret_out_of_a_field(qapp):
    """The re-raise runs once a second. If it took focus unconditionally it
    would empty the caret out of the field being typed into."""
    view = window(p75_prompt())
    view.press("e")
    edit = view._field_inputs["new_budget"]
    assert view.focusWidget() is edit
    view._insist()
    assert view.focusWidget() is edit


def test_submitted_fields_complete_the_session(qapp):
    view = window(p75_prompt())
    view.press("c")
    assert view.submit_fields({"new_stop_condition": "just the 500s"}) == []
    assert view.is_complete()
    assert view.answers().fields == {"new_stop_condition": "just the 500s"}


def test_fields_submitted_when_none_were_asked_for_are_refused(qapp):
    """`PromptSession.submit_fields` never checks its stage: on a ping
    window it would take `{"junk": "value"}`, mark the session done and
    settle it as a `ping_answered` with no choice at all. `session.py`
    stays a toolkit-free state machine with its own tests, so the door is
    shut here rather than in it.
    """
    view = window(ping_prompt())
    assert view.submit_fields({"junk": "value"}) == []
    assert not view.is_complete()
    assert view.result() is None


def test_a_key_that_is_not_a_choice_does_nothing(qapp):
    view = window(ping_prompt())
    view.press("q")
    assert not view.is_complete()


def test_the_timeout_produces_a_timed_out_answer(qapp):
    view = window(ping_prompt())
    answers = view.timed_out(at=300.0)
    assert answers.timed_out is True
    assert answers.choice is None


def test_the_countdown_reports_time_left_not_time_spent(qapp):
    view = window(ping_prompt(), at=0.0)
    assert view.countdown_text(at=0.0) == "5:00"
    assert view.countdown_text(at=290.0) == "0:10"
    assert view.countdown_text(at=999.0) == "0:00"


def test_a_tick_paints_the_time_left(qapp):
    clock = Clock()
    view = CheckinWindow(ping_prompt(), mode="dark", now=clock)
    clock.at = 290.0
    view._tick()
    assert view._countdown.text() == "0:10 left"
    assert view.result() is None


def test_a_tick_past_the_deadline_expires_the_check_in(qapp):
    """Wall-clock time is the authority, not the single-shot timer: a
    machine that slept through the deadline still times out."""
    clock = Clock()
    view = CheckinWindow(ping_prompt(), mode="dark", now=clock)
    clock.at = 3600.0
    view._tick()
    assert view.result().timed_out is True


def test_escape_does_not_close_the_window(qapp):
    view = window(ping_prompt())
    view.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert not view.is_complete()
    assert view.result() is None


def test_a_keystroke_reaches_the_session_through_the_key_event(qapp):
    view = window(ping_prompt())
    view.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_N, Qt.NoModifier, "N"))
    assert view.result().choice == "n"


def test_the_thrash_warning_is_rendered_when_the_prompt_carries_one(qapp):
    prompt = dataclasses.replace(ping_prompt(), warning="⚠  7 actions, 0 ruled out")
    assert "7 actions" in window(prompt).warning_text()


def test_the_warning_is_drawn_as_the_themed_banner_frame(qapp):
    """`theme.py` styles `QFrame#banner`, not `QLabel#banner`. A label
    wearing that object name renders unstyled."""
    prompt = dataclasses.replace(ping_prompt(), warning="⚠  7 actions, 0 ruled out")
    view = window(prompt)
    assert isinstance(view._banner, QFrame)
    assert view._banner.objectName() == "banner"
    assert window(ping_prompt())._banner is None


# --- the outcome: exactly one winner, readable after the loop exits ---


def test_a_timeout_cannot_overwrite_an_answer(qapp):
    """`_settle` is the only writer of the result and the first caller
    wins, so a deadline that fires while the answer is unwinding is inert."""
    view = window(ping_prompt())
    view.press("n")
    view._expire()
    assert view.result().timed_out is False
    assert view.result().choice == "n"


def test_an_answer_cannot_overwrite_a_timeout(qapp):
    """The other direction, and the session must not move either — a
    keystroke after the timeout would otherwise complete a session whose
    answer nobody will ever read."""
    view = window(ping_prompt())
    view._expire()
    view.press("n")  # a completing keystroke, the one that could overwrite
    view.submit_fields({"anything": "at all"})
    assert view.result().timed_out is True
    assert view.result().choice is None
    assert not view.is_complete()


def test_the_wait_returns_when_the_answer_arrives(qapp):
    """The nested QEventLoop, driven with nothing on screen: a zero-delay
    timer answers, `settled` quits the loop, and the outcome is readable
    the instant it returns."""
    view = window(ping_prompt())
    late = []
    guard = watchdog(view, late)
    QTimer.singleShot(0, lambda: view.press("n"))
    answers = view._block_until_settled()
    guard.stop()
    assert late == [], "the answer did not quit the loop; the watchdog did"
    assert answers.choice == "n"
    assert answers is view.result()


def test_the_wait_returns_when_the_deadline_passes(qapp):
    view = window(ping_prompt())
    late = []
    guard = watchdog(view, late)
    QTimer.singleShot(0, view._expire)
    answers = view._block_until_settled()
    guard.stop()
    assert late == [], "the timeout did not quit the loop; the watchdog did"
    assert answers.timed_out is True
    assert answers is view.result()


def test_the_wait_does_not_block_when_the_answer_landed_first(qapp):
    """The hang this guards against: connecting `settled` to `quit` after
    it has already been emitted leaves `exec()` with nothing to quit it.
    The watchdog turns that hang into a failed assertion."""
    view = window(ping_prompt())
    view.press("n")

    late = []
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(lambda: (late.append(True), view.settled.emit()))
    watchdog.start(2000)
    answers = view._block_until_settled()
    watchdog.stop()

    assert late == [], "entered an event loop only the watchdog could break"
    assert answers.choice == "n"


# --- the real ask(), offscreen ---


def test_ask_reaches_the_wait_already_settled_when_the_deadline_has_passed(qapp):
    """The ordering nothing else in this file can reach.

    `ask()` arms the timers and the first tick expires the check-in, so
    `_block_until_settled` is entered *already settled* — the exact case
    guard (a) exists for. Every other test settles the window by hand
    before the wait, which is the same state arrived at a different way.
    Without guard (a) this hangs, on a window covering the whole display;
    the watchdog makes that a failure instead.
    """
    view = CheckinWindow(dataclasses.replace(ping_prompt(), timeout_s=0.0), mode="dark")
    settled_on_entry = []
    wait = view._block_until_settled

    def spy():
        settled_on_entry.append(view.result() is not None)
        return wait()

    view._block_until_settled = spy
    late = []
    guard = watchdog(view, late)
    answers = view.ask()
    guard.stop()

    assert settled_on_entry == [True], "the timers did not settle it before the wait"
    assert late == [], "entered an event loop that was never going to be quit"
    assert answers.timed_out is True
    assert not view.isVisible()


def test_the_deadline_timer_never_rounds_the_window_down(qapp):
    """The deterministic half of the test below, which was flaky.

    `int(remaining * 1000)` truncates, so a deadline landing on a
    fractional millisecond fires *before* it: `_expire` runs early and
    `answered_at - shown_at` comes back short of `timeout_s`. Measured
    offscreen on this machine: 13 failures in 300 runs of the test below
    with `int`, 0 in 300 with `math.ceil`. Rounding up overruns by at most
    a millisecond; rounding down ends the check-in early, which is the one
    direction that can be wrong.
    """
    clock = Clock(1000.0)
    prompt = dataclasses.replace(ping_prompt(), timeout_s=0.0505)
    view = CheckinWindow(prompt, mode="dark", now=clock)
    view._start_timers()
    try:
        deadline = view._timers[0]
        assert deadline.isSingleShot(), "the first timer is the deadline"
        assert deadline.interval() >= prompt.timeout_s * 1000
    finally:
        view._stop_timers()


def test_the_blocker_runs_the_whole_window_and_returns_the_timeout(qapp):
    """`QtBlocker.ask` end to end — show, timers, nested loop, teardown —
    against a deadline short enough to sit in a test. The production
    prompt keeps `TIMEOUT_S`; only this one shortens it."""
    prompt = dataclasses.replace(ping_prompt(), timeout_s=0.05)
    start = time.perf_counter()
    answers = QtBlocker(mode="dark").ask(prompt)
    assert time.perf_counter() - start < 30.0, "it waited on something else"
    assert answers.timed_out is True
    assert answers.choice is None
    assert answers.answered_at - answers.shown_at >= 0.05


def test_the_blocker_returns_the_answer_a_keystroke_gave_it(qapp):
    """The same path, ending in an answer rather than a timeout: the
    window has to be up and taking keys for this to settle at all."""
    prompt = dataclasses.replace(ping_prompt(), timeout_s=30.0)
    view = CheckinWindow(prompt, mode="dark")
    late = []
    guard = watchdog(view, late)
    QTimer.singleShot(0, lambda: view.press("y"))
    QTimer.singleShot(0, lambda: view.press("1"))
    answers = view.ask()
    guard.stop()
    assert late == [], "the keystrokes never settled it"
    assert (answers.choice, answers.picked) == ("y", 1)
    assert not view.isVisible()
