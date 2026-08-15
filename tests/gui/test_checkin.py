"""The full-screen check-in.

Every test drives the window's own methods. None of them show it — `ask()`
takes over the whole display for five minutes. The nested event loop is
still exercised for real, through `_block_until_settled`, driven by
zero-delay timers with nothing on screen.
"""

from __future__ import annotations

import dataclasses

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame

from loop.blockers.base import Choice, Prompt, TextField
from loop.gui.checkin import CheckinWindow


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


def test_the_prompt_text_reaches_the_screen_verbatim(qapp):
    """These strings are the product. A window that paraphrases them is wrong."""
    view = window(ping_prompt())
    assert view.title_text() == "loop #3 · 18:42 / 45:00 · 2 paused"
    assert view.question_text() == "hypothesis space smaller than 20m ago?"
    assert [button.text() for button in view.choice_buttons()] == ["yes", "no"]


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
    QTimer.singleShot(0, lambda: view.press("n"))
    answers = view._block_until_settled()
    assert answers.choice == "n"
    assert answers is view.result()


def test_the_wait_returns_when_the_deadline_passes(qapp):
    view = window(ping_prompt())
    QTimer.singleShot(0, view._expire)
    answers = view._block_until_settled()
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
