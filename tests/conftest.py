"""Shared pytest fixtures."""

import pytest


@pytest.fixture
def feed(monkeypatch):
    """Queue scripted answers that `builtins.input` pops in order."""

    def _feed(answers):
        queue = list(answers)
        monkeypatch.setattr("builtins.input", lambda _="": queue.pop(0))

    return _feed
