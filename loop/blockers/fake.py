"""A blocker that answers from a script. Used by tests."""

from __future__ import annotations

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
