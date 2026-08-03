"""Terminal prompts.

Every prompt reads through `builtins.input` so that tests replace one
function instead of driving a subprocess.
"""

from __future__ import annotations

import sys

from loop.core.timefmt import format_duration, parse_duration  # noqa: F401  (re-exported for cli)


class Aborted(Exception):
    """The user pressed Ctrl-D or Ctrl-C at a prompt."""


def _read(label: str) -> str:
    try:
        return input(f"  → {label} ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise Aborted() from exc


def ask_text(label: str, *, required: bool = True) -> str:
    while True:
        value = _read(label)
        if value or not required:
            return value
        print("     required.", file=sys.stderr)


def ask_yes_no(label: str) -> bool:
    while True:
        value = _read(f"{label} [y/n]").lower()
        if value in ("y", "yes"):
            return True
        if value in ("n", "no"):
            return False
        print("     answer y or n.", file=sys.stderr)


def ask_lines(label: str, *, minimum: int = 1) -> list[str]:
    print(f"  → {label} (one per line, blank line to finish)", file=sys.stderr)
    collected: list[str] = []
    while True:
        value = _read(f"{len(collected) + 1}.")
        if value:
            collected.append(value)
            continue
        if len(collected) >= minimum:
            return collected
        print(f"     at least {minimum} required.", file=sys.stderr)


def ask_duration(label: str, *, default: float | None = None) -> float:
    prompt = label if default is None else f"{label} [{format_duration(default)}]"
    while True:
        value = _read(prompt)
        if not value and default is not None:
            return default
        try:
            return parse_duration(value)
        except ValueError as exc:
            print(f"     {exc}", file=sys.stderr)
