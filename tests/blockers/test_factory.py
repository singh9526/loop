import json

import pytest

from loop.blockers import factory
from loop.blockers.fake import FakeBlocker


def test_env_override_selects_the_fake(monkeypatch):
    monkeypatch.setenv("LOOP_BLOCKER", "fake")
    assert isinstance(factory.get_blocker(), FakeBlocker)


def test_fake_reads_scripted_answers(tmp_path, monkeypatch):
    script = tmp_path / "answers.json"
    script.write_text(json.dumps([{"choice": "y", "picked": 2}]), encoding="utf-8")
    monkeypatch.setenv("LOOP_BLOCKER", "fake")
    monkeypatch.setenv("LOOP_FAKE_ANSWERS", str(script))

    blocker = factory.get_blocker()
    answer = blocker.ask(_prompt())
    assert answer.choice == "y"
    assert answer.picked == 2
    assert answer.timed_out is False


def test_fake_times_out_once_the_script_runs_dry(monkeypatch):
    monkeypatch.setenv("LOOP_BLOCKER", "fake")
    monkeypatch.delenv("LOOP_FAKE_ANSWERS", raising=False)
    assert factory.get_blocker().ask(_prompt()).timed_out is True


def test_unknown_override_is_rejected(monkeypatch):
    monkeypatch.setenv("LOOP_BLOCKER", "hologram")
    with pytest.raises(ValueError, match="hologram"):
        factory.get_blocker()


def test_non_darwin_falls_back_to_tk(monkeypatch):
    monkeypatch.delenv("LOOP_BLOCKER", raising=False)
    monkeypatch.setattr(factory.sys, "platform", "linux")
    calls = []
    monkeypatch.setattr(factory, "_tk_blocker", lambda: calls.append("tk") or "TK")
    assert factory.get_blocker() == "TK"
    assert calls == ["tk"]


def test_darwin_without_pyobjc_falls_back_to_tk(monkeypatch, capsys):
    monkeypatch.delenv("LOOP_BLOCKER", raising=False)
    monkeypatch.setattr(factory.sys, "platform", "darwin")
    monkeypatch.setattr(factory, "_macos_blocker", _raise_import_error)
    monkeypatch.setattr(factory, "_tk_blocker", lambda: "TK")
    assert factory.get_blocker() == "TK"
    assert "soft block" in capsys.readouterr().err


def _raise_import_error():
    raise ImportError("no pyobjc")


def _prompt():
    from loop.blockers.base import Choice, Prompt

    return Prompt(kind="ping", title="t", question="q", choices=[Choice("y", "yes")])
