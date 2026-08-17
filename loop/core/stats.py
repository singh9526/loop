"""The logbook's read side.

Classification is mechanical on purpose: a number you cannot argue with
later is the entire reason the log is worth keeping.
"""

from __future__ import annotations

from statistics import median

from loop.core import events as events_module
from loop.core import thrash
from loop.core.models import ABANDONED, CLOSED, State, pause_gaps
from loop.core.timefmt import format_duration


def compute(log: list[dict], now: float) -> dict:
    state = State(loops={}, active_id=None)
    episodes = 0
    firing = {}
    max_depth = 0

    for event in log:
        events_module.apply(state, event)
        max_depth = max(max_depth, events_module.stack_depth(state))

        loop_id = event["loop_id"]
        loop = state.loops[loop_id]
        was_firing = firing.get(loop_id, False)
        is_firing = thrash.detect(loop, loop.elapsed(event["ts"])).firing
        if is_firing and not was_firing:
            episodes += 1
        firing[loop_id] = is_firing

    terminal = [lp for lp in state.loops.values() if lp.status in (CLOSED, ABANDONED)]
    followed = [lp for lp in terminal if _followed(lp)]
    abandoned = [lp for lp in terminal if not _followed(lp)]

    return {
        "followed": _bucket(followed),
        "abandoned": _bucket(abandoned),
        "thrash_episodes": {"total": episodes},
        "estimate_drift": _drift(terminal),
        "ping_response": {
            "answered": sum(lp.pings_answered for lp in state.loops.values()),
            "timed_out": sum(lp.pings_timed_out for lp in state.loops.values()),
        },
        "interruptions": _interruptions(terminal, max_depth),
    }


def _followed(loop) -> bool:
    return loop.pings_timed_out == 0 and loop.checkpoints_timed_out == 0


def _elapsed_at_close(loop) -> float:
    return loop.elapsed(loop.closed_at if loop.closed_at is not None else 0.0)


def _bucket(loops: list) -> dict:
    if not loops:
        return {"count": 0, "median_s": 0.0, "resolved_pct": 0.0}
    resolved = sum(1 for lp in loops if lp.status == CLOSED)
    return {
        "count": len(loops),
        "median_s": median(_elapsed_at_close(lp) for lp in loops),
        "resolved_pct": round(100.0 * resolved / len(loops), 1),
    }


def _drift(terminal: list) -> dict:
    closed = [lp for lp in terminal if lp.status == CLOSED and lp.original_budget_s > 0]
    ratios = [
        100.0 * (_elapsed_at_close(lp) / lp.original_budget_s - 1.0) for lp in closed
    ]
    extended = [lp for lp in terminal if lp.extensions]
    return {
        "median_pct": round(median(ratios), 1) if ratios else 0.0,
        "extensions": sum(lp.extensions for lp in terminal),
        "loops_with_extensions": len(extended),
    }


def _interruptions(terminal: list, max_depth: int) -> dict:
    if not terminal:
        return {"median_pauses": 0.0, "median_pause_s": 0.0, "max_depth": max_depth}

    pause_counts = [max(0, len(lp.intervals) - 1) for lp in terminal]
    gaps = [gap for lp in terminal for gap in pause_gaps(lp)]
    return {
        "median_pauses": median(pause_counts),
        "median_pause_s": median(gaps) if gaps else 0.0,
        "max_depth": max_depth,
    }


def format_drift(median_pct: float) -> str:
    """Signed drift as a word.

    `median_pct` is signed, so rendering it into a fixed "over budget"
    suffix makes a loop that finished early read as -99.7% over.
    """
    return f"{abs(median_pct)}% {'under' if median_pct < 0 else 'over'}"


def render(report: dict) -> list[str]:
    def bucket_line(label: str, data: dict) -> str:
        return (
            f"  {label:<20}: {data['count']} loops | "
            f"median {format_duration(data['median_s'])} | "
            f"{data['resolved_pct']}% resolved"
        )

    drift = report["estimate_drift"]
    interruptions = report["interruptions"]
    return [
        "",
        bucket_line("protocol followed", report["followed"]),
        bucket_line("protocol abandoned", report["abandoned"]),
        f"  {'thrash episodes':<20}: {report['thrash_episodes']['total']}",
        f"  {'estimate drift':<20}: median {format_drift(drift['median_pct'])} budget | "
        f"{drift['extensions']} extensions across {drift['loops_with_extensions']} loops",
        f"  {'ping response':<20}: {report['ping_response']['answered']} answered, "
        f"{report['ping_response']['timed_out']} timed out",
        f"  {'interruptions':<20}: median {interruptions['median_pauses']} pauses/loop | "
        f"median pause {format_duration(interruptions['median_pause_s'])} | "
        f"max depth {interruptions['max_depth']}",
        "",
    ]
