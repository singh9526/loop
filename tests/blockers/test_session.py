import pytest

from loop.blockers.base import Choice, Prompt, TextField
from loop.blockers.session import PromptSession


def ping_prompt():
    return Prompt(
        kind="ping",
        title="loop #14 · 42m / 45m",
        question="hypothesis space smaller than 20m ago?",
        choices=[Choice("y", "yes"), Choice("n", "no")],
        pick_list=["cert expiry", "env var missing", "pg conn pool"],
        pick_after="y",
    )


def checkpoint_prompt():
    return Prompt(
        kind="p75",
        title="75% of budget gone · 34m / 45m",
        question="is 75% of the work done?",
        choices=[Choice("y", "yes"), Choice("c", "cut scope"), Choice("e", "extend")],
        fields_after={
            "c": [TextField("new_stop_condition", "new stop condition")],
            "e": [
                TextField("new_budget", "new budget"),
                TextField("learned", "what did you learn that made it bigger?"),
            ],
        },
    )


def test_a_plain_choice_completes_immediately():
    session = PromptSession(ping_prompt(), shown_at=100.0)
    assert session.stage == "choice"
    assert session.press_key("n") is True
    assert session.is_complete()

    answers = session.answers(answered_at=103.0)
    assert answers.choice == "n"
    assert answers.picked is None
    assert answers.fields == {}
    assert answers.timed_out is False
    assert (answers.shown_at, answers.answered_at) == (100.0, 103.0)


def test_an_unknown_key_is_ignored():
    session = PromptSession(ping_prompt(), shown_at=0.0)
    assert session.press_key("q") is False
    assert session.stage == "choice"


def test_yes_reveals_the_pick_list():
    session = PromptSession(ping_prompt(), shown_at=0.0)
    session.press_key("y")
    assert session.stage == "pick"
    assert session.visible_pick_list() == ["cert expiry", "env var missing", "pg conn pool"]
    assert not session.is_complete()

    assert session.press_key("2") is True
    assert session.is_complete()
    assert session.answers(answered_at=1.0).picked == 2


def test_the_pick_list_is_hidden_before_the_revealing_choice():
    assert PromptSession(ping_prompt(), shown_at=0.0).visible_pick_list() is None


def test_an_out_of_range_pick_is_ignored():
    session = PromptSession(ping_prompt(), shown_at=0.0)
    session.press_key("y")
    assert session.press_key("7") is False
    assert session.stage == "pick"


def test_a_choice_with_fields_moves_to_the_field_stage():
    session = PromptSession(checkpoint_prompt(), shown_at=0.0)
    session.press_key("c")
    assert session.stage == "fields"
    assert [f.name for f in session.pending_fields()] == ["new_stop_condition"]
    assert not session.is_complete()

    assert session.submit_fields({"new_stop_condition": "comes up once"}) == []
    assert session.is_complete()
    assert session.answers(answered_at=5.0).fields == {"new_stop_condition": "comes up once"}


def test_missing_required_fields_are_reported_and_do_not_complete():
    session = PromptSession(checkpoint_prompt(), shown_at=0.0)
    session.press_key("e")
    errors = session.submit_fields({"new_budget": "90m", "learned": "   "})
    assert errors == ["learned"]
    assert not session.is_complete()

    assert session.submit_fields({"new_budget": "90m", "learned": "pool is fine"}) == []
    assert session.is_complete()


def test_a_choice_with_no_fields_on_a_field_prompt_completes():
    session = PromptSession(checkpoint_prompt(), shown_at=0.0)
    session.press_key("y")
    assert session.is_complete()
    assert session.answers(answered_at=1.0).choice == "y"


def test_timed_out_answers_carry_nothing():
    session = PromptSession(ping_prompt(), shown_at=100.0)
    session.press_key("y")
    answers = session.timed_out(at=400.0)
    assert answers.timed_out is True
    assert answers.choice is None
    assert answers.picked is None
    assert answers.fields == {}
    assert (answers.shown_at, answers.answered_at) == (100.0, 400.0)


def test_keys_are_ignored_once_complete():
    session = PromptSession(ping_prompt(), shown_at=0.0)
    session.press_key("n")
    assert session.press_key("y") is False
    assert session.answers(answered_at=1.0).choice == "n"


def test_answers_before_completion_is_an_error():
    session = PromptSession(ping_prompt(), shown_at=0.0)
    with pytest.raises(RuntimeError):
        session.answers(answered_at=1.0)
