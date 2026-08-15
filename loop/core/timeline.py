"""The action log, projected from events.

`fold` collapses actions to a counter, which is the right shape for the
scheduler and the wrong shape for a screen. This walks the same events and
keeps the text.

It reads the log rather than the folded state for one reason: a
hypothesis that a check-in ruled out is only distinguishable from one you
ruled out by hand by its adjacency to the `ping_answered` that carried it,
and folding throws that away.
"""

from __future__ import annotations

from dataclasses import dataclass

from loop.core.timefmt import format_duration

ACTION = "action"
HYPOTHESIS = "hypothesis"
RULED_OUT = "ruled_out"
PING = "ping"
CHECKPOINT = "checkpoint"
CUT = "cut"
EXTEND = "extend"
PAUSED = "paused"
RESUMED = "resumed"

CHECKIN_BECAUSE = "answered at the check-in"

# The loop's own boundaries are drawn by the header, not the log.
IGNORED = frozenset({"loop_opened", "loop_closed", "loop_abandoned", "checkpoint_shown"})


@dataclass(frozen=True, slots=True)
class Entry:
    ts: float
    kind: str
    text: str
    because: str | None = None


def entries(log: list[dict], loop_id: int) -> list[Entry]:
    out: list[Entry] = []
    names: dict[int, str] = {}
    pending_kill: int | None = None

    for event in log:
        if event.get("loop_id") != loop_id:
            continue
        kind = event["type"]

        if kind == "loop_opened":
            names = {
                index: text
                for index, text in enumerate(event["hypotheses"], start=1)
            }
        elif kind == "hypothesis_added":
            names[event["hyp_id"]] = event["text"]

        entry = _entry(event, kind, names, pending_kill)
        pending_kill = (
            event["eliminated"]
            if kind == "ping_answered" and event["eliminated"] is not None
            else None
        )
        if entry is not None:
            out.append(entry)

    return out


def _entry(event: dict, kind: str, names: dict[int, str], pending_kill: int | None):
    ts = event["ts"]

    if kind in IGNORED:
        return None

    if kind == "action_logged":
        return Entry(ts, ACTION, event["action"], event["because"])

    if kind == "hypothesis_added":
        return Entry(ts, HYPOTHESIS, f"added §{event['hyp_id']} {event['text']}")

    if kind == "hypothesis_eliminated":
        hyp_id = event["hyp_id"]
        because = CHECKIN_BECAUSE if pending_kill == hyp_id else None
        return Entry(ts, RULED_OUT, f"ruled out §{hyp_id} {names.get(hyp_id, '')}".rstrip(), because)

    if kind == "ping_answered":
        shape = "smaller" if event["smaller"] else "not smaller"
        return Entry(ts, PING, f"check-in: hypothesis space {shape}")

    if kind == "ping_unanswered":
        return Entry(ts, PING, "check-in: timed out")

    if kind == "checkpoint_answered":
        return Entry(ts, CHECKPOINT, f"{event['kind']}: {event['decision']}")

    if kind == "checkpoint_unanswered":
        return Entry(ts, CHECKPOINT, f"{event['kind']}: timed out")

    if kind == "scope_cut":
        return Entry(ts, CUT, f"cut scope to {event['new_stop_condition']}")

    if kind == "budget_extended":
        return Entry(
            ts, EXTEND,
            f"extended budget to {format_duration(event['new_budget_s'])}",
            event["learned"],
        )

    if kind == "loop_paused":
        return Entry(ts, PAUSED, f"paused: {event['reason']}")

    if kind == "loop_resumed":
        return Entry(ts, RESUMED, "resumed")

    raise ValueError(f"timeline has no handler for {kind!r}")
