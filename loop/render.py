"""Human-readable views of state. No I/O, no decisions."""

from __future__ import annotations

from loop.core import events, schedule
from loop.core.models import State, paused_for
from loop.core.timefmt import format_duration

STALE_PAUSE_S = 86_400.0


def status_lines(state: State, now: float) -> list[str]:
    loop = state.active_loop()
    if loop is None:
        return ["no active loop."]

    elapsed = schedule.active_elapsed(loop, now)
    depth = events.stack_depth(state)
    header = f"#{loop.id}  {loop.question}"
    if depth > 1:
        header += f"   ({depth - 1} paused)"

    lines = [
        header,
        f"  stop condition : {loop.stop_condition}",
        f"  elapsed        : {format_duration(elapsed)} / {format_duration(loop.budget_s)}",
        f"  actions        : {loop.actions}  ruled out: {loop.eliminated}",
        "  hypotheses     :",
    ]
    for hypothesis in loop.hypotheses:
        mark = " " if hypothesis.alive else "✗"
        lines.append(f"    {mark} {hypothesis.id}  {hypothesis.text}")
    return lines


def stack_lines(state: State, now: float) -> list[str]:
    loop = state.active_loop()
    lines: list[str] = []
    if loop is not None:
        lines.append(
            f"  ▶ #{loop.id}  {loop.question}"
            f"   {format_duration(schedule.active_elapsed(loop, now))} active"
        )
    for paused in state.paused_loops():
        wait = paused_for(paused, now)
        stale = "  ⚠" if wait >= STALE_PAUSE_S else ""
        lines.append(
            f"    #{paused.id}  {paused.question}"
            f"   {format_duration(schedule.active_elapsed(paused, now))} active"
            f" · paused {format_duration(wait)}{stale}"
        )
    return lines or ["  stack is empty."]
