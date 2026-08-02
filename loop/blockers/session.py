"""The interaction state machine, shared by every overlay.

choice → (optional pick) → (optional fields) → done.
"""

from __future__ import annotations

from loop.blockers.base import Answers, Prompt, TextField

CHOICE = "choice"
PICK = "pick"
FIELDS = "fields"
DONE = "done"


class PromptSession:
    def __init__(self, prompt: Prompt, shown_at: float) -> None:
        self.prompt = prompt
        self.shown_at = shown_at
        self.stage = CHOICE
        self.choice: str | None = None
        self.picked: int | None = None
        self.fields: dict[str, str] = {}

    def press_key(self, key: str) -> bool:
        """Feed a keystroke. Returns True when it advanced the session."""
        if self.stage == CHOICE:
            return self._press_choice(key)
        if self.stage == PICK:
            return self._press_pick(key)
        return False

    def _press_choice(self, key: str) -> bool:
        if key not in {choice.key for choice in self.prompt.choices}:
            return False
        self.choice = key
        self.stage = self._stage_after_choice(key)
        return True

    def _stage_after_choice(self, key: str) -> str:
        if self.prompt.pick_after == key and self.prompt.pick_list:
            return PICK
        if self.prompt.fields_after.get(key):
            return FIELDS
        return DONE

    def _press_pick(self, key: str) -> bool:
        if not key.isdigit():
            return False
        index = int(key)
        if not 1 <= index <= len(self.prompt.pick_list or []):
            return False
        self.picked = index
        self.stage = FIELDS if self.prompt.fields_after.get(self.choice or "") else DONE
        return True

    def visible_pick_list(self) -> list[str] | None:
        return self.prompt.pick_list if self.stage == PICK else None

    def pending_fields(self) -> list[TextField]:
        if self.stage != FIELDS:
            return []
        return self.prompt.fields_after.get(self.choice or "", [])

    def submit_fields(self, values: dict[str, str]) -> list[str]:
        """Returns the names of required fields that came back blank."""
        missing = [
            field.name
            for field in self.pending_fields()
            if field.required and not values.get(field.name, "").strip()
        ]
        if missing:
            return missing
        self.fields = {name: value.strip() for name, value in values.items()}
        self.stage = DONE
        return []

    def is_complete(self) -> bool:
        return self.stage == DONE

    def answers(self, answered_at: float) -> Answers:
        if not self.is_complete():
            raise RuntimeError("session is not complete")
        return Answers(
            timed_out=False,
            choice=self.choice,
            picked=self.picked,
            fields=dict(self.fields),
            shown_at=self.shown_at,
            answered_at=answered_at,
        )

    def timed_out(self, at: float) -> Answers:
        return Answers(
            timed_out=True, choice=None, picked=None, fields={},
            shown_at=self.shown_at, answered_at=at,
        )
