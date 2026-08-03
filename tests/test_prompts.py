import pytest

from loop import prompts


def test_ask_text_returns_the_line(feed):
    feed(["comes up clean twice"])
    assert prompts.ask_text("stop condition?") == "comes up clean twice"


def test_ask_text_reprompts_until_non_empty(feed):
    feed(["", "   ", "finally"])
    assert prompts.ask_text("stop condition?") == "finally"


def test_ask_text_allows_empty_when_not_required(feed):
    feed([""])
    assert prompts.ask_text("optional?", required=False) == ""


@pytest.mark.parametrize("reply,expected", [("y", True), ("Y", True), ("n", False)])
def test_ask_yes_no(feed, reply, expected):
    feed([reply])
    assert prompts.ask_yes_no("stack it?") is expected


def test_ask_yes_no_reprompts_on_garbage(feed):
    feed(["maybe", "y"])
    assert prompts.ask_yes_no("stack it?") is True


def test_ask_lines_collects_until_blank(feed):
    feed(["cert expiry", "env var missing", ""])
    assert prompts.ask_lines("hypotheses?") == ["cert expiry", "env var missing"]


def test_ask_lines_requires_the_minimum(feed):
    feed(["", "cert expiry", ""])
    assert prompts.ask_lines("hypotheses?", minimum=1) == ["cert expiry"]


def test_ask_duration_parses_and_reprompts(feed):
    feed(["not a time", "45m"])
    assert prompts.ask_duration("time budget?") == 2700.0


def test_eof_raises_aborted(monkeypatch):
    def raise_eof(_=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)
    with pytest.raises(prompts.Aborted):
        prompts.ask_text("stop condition?")


# --- ruling 1: DEFAULT_BUDGET_S must be load-bearing ---


def test_ask_duration_returns_default_on_blank_line(feed):
    feed([""])
    assert prompts.ask_duration("time budget?", default=2700.0) == 2700.0


def test_ask_duration_blank_rejected_without_default(feed):
    feed(["", "45m"])
    assert prompts.ask_duration("time budget?") == 2700.0


def test_ask_duration_prompt_shows_the_default(monkeypatch):
    seen = {}

    def fake_input(prompt=""):
        seen["prompt"] = prompt
        return "45m"

    monkeypatch.setattr("builtins.input", fake_input)
    prompts.ask_duration("time budget?", default=2700.0)
    assert "[45m]" in seen["prompt"]


# --- Important 5: reprompts must never land on stdout, which --json owns ---


def test_ask_text_reprompt_goes_to_stderr(feed, capsys):
    feed(["", "finally"])
    prompts.ask_text("stop condition?")
    out, err = capsys.readouterr()
    assert "required." in err
    assert "required." not in out


def test_ask_yes_no_reprompt_goes_to_stderr(feed, capsys):
    feed(["maybe", "y"])
    prompts.ask_yes_no("stack it?")
    out, err = capsys.readouterr()
    assert "answer y or n." in err
    assert "answer y or n." not in out


def test_ask_lines_minimum_reprompt_goes_to_stderr(feed, capsys):
    feed(["", "cert expiry", ""])
    prompts.ask_lines("hypotheses?", minimum=1)
    out, err = capsys.readouterr()
    assert "at least 1 required." in err
    assert "at least 1 required." not in out


def test_ask_duration_parse_error_goes_to_stderr(feed, capsys):
    feed(["not a time", "45m"])
    prompts.ask_duration("time budget?")
    out, err = capsys.readouterr()
    assert "cannot parse duration" in err
    assert "cannot parse duration" not in out
