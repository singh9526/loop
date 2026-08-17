"""Search the logbook.

Lifted out of `cli.py` so the window can use it. Closed and abandoned
loops only: a loop you are still inside is already on screen.
"""

from __future__ import annotations

from dataclasses import dataclass

from loop.core.models import ABANDONED, CLOSED, State


@dataclass(frozen=True, slots=True)
class Hit:
    label: str
    text: str


@dataclass(frozen=True, slots=True)
class Match:
    loop_id: int
    question: str
    hits: tuple[Hit, ...]


def find(state: State, log: list[dict], term: str) -> list[Match]:
    needle = term.lower()
    matches: list[Match] = []

    for loop in state.loops.values():
        if loop.status not in (CLOSED, ABANDONED):
            continue
        hits = [
            Hit(label, text)
            for label, text in (loop.postmortem or {}).items()
            if needle in text.lower()
        ]
        hits += [
            Hit("action", f"{event['action']} — because {event['because']}")
            for event in log
            if event["type"] == "action_logged"
            and event["loop_id"] == loop.id
            and (needle in event["action"].lower() or needle in event["because"].lower())
        ]
        if hits:
            matches.append(Match(loop.id, loop.question, tuple(hits)))

    return matches
