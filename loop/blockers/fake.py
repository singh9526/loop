"""A blocker that answers from a script. Used by tests."""

from __future__ import annotations

import json
import os
import time

from loop.blockers.base import Answers, Prompt


class FakeBlocker:
    """Returns queued answers in order; times out once the queue is empty."""

    def __init__(self, answers: list[Answers] | None = None) -> None:
        self._queue = list(answers or [])
        self.prompts: list[Prompt] = []

    def ask(self, prompt: Prompt) -> Answers:
        self.prompts.append(prompt)
        now = time.time()
        if not self._queue:
            return Answers(
                timed_out=True, choice=None, picked=None, fields={},
                shown_at=now, answered_at=now + prompt.timeout_s,
            )
        answer = self._queue.pop(0)
        return Answers(
            timed_out=answer.timed_out,
            choice=answer.choice,
            picked=answer.picked,
            fields=dict(answer.fields),
            shown_at=now,
            answered_at=now,
        )


def from_env() -> FakeBlocker:
    """Build a FakeBlocker from the JSON file named by LOOP_FAKE_ANSWERS.

    The file holds a list of objects, each with any of `timed_out`,
    `choice`, `picked`, `fields`. Missing keys take their neutral value.
    """
    path = os.environ.get("LOOP_FAKE_ANSWERS")
    if not path:
        return FakeBlocker([])

    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)

    return FakeBlocker(
        [
            Answers(
                timed_out=item.get("timed_out", False),
                choice=item.get("choice"),
                picked=item.get("picked"),
                fields=item.get("fields", {}),
                shown_at=0.0,
                answered_at=0.0,
            )
            for item in raw
        ]
    )
