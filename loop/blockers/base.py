"""The contract between the scheduler and whatever draws a check-in on screen.

Nothing here may import from the core package. That is the seam: a new
platform's check-in is a new file implementing `ask`, and the domain
logic never learns which one ran. Today that file is `gui/checkin.py`,
not anything under `loop/blockers/` — this module only defines the
shared contract (`Prompt`, `Answers`) and the pieces every implementation
reuses (`format_countdown`, `remaining_fraction`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

TIMEOUT_S = 300.0


@dataclass(frozen=True, slots=True)
class Choice:
    key: str        # a single keystroke, e.g. "y"
    label: str


@dataclass(frozen=True, slots=True)
class TextField:
    name: str
    label: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class Prompt:
    kind: str                                  # "ping" | "p75" | "p100"
    title: str
    question: str
    choices: list[Choice]
    pick_list: list[str] | None = None
    pick_after: str | None = None              # reveal pick_list after this key
    fields_after: dict[str, list[TextField]] = field(default_factory=dict)
    warning: str | None = None
    timeout_s: float = TIMEOUT_S


@dataclass(frozen=True, slots=True)
class Answers:
    timed_out: bool
    choice: str | None
    picked: int | None                         # 1-based index into pick_list
    fields: dict[str, str]
    shown_at: float
    answered_at: float


class Blocker(Protocol):
    def ask(self, prompt: Prompt) -> Answers: ...


def format_countdown(remaining_s: float) -> str:
    remaining = max(0, int(remaining_s))
    return f"{remaining // 60}:{remaining % 60:02d}"


def remaining_fraction(remaining_s: float, total_s: float) -> float:
    if total_s <= 0:
        return 0.0
    return max(0.0, min(1.0, remaining_s / total_s))
