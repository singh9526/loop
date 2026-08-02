"""Argument parsing and command dispatch."""

from __future__ import annotations

import argparse
import json
import sys
import time

from loop import prompts, render
from loop.core import events
from loop.core.models import MAX_STACK_DEPTH, WARN_STACK_DEPTH, State
from loop.core.timefmt import format_duration
from loop.store import jsonl

DEFAULT_BUDGET_S = 2700.0
DEFAULT_INTERVAL_S = 1200.0

# Shared so every subcommand accepts `--json` after its own arguments
# (design spec: "every command accepts --json"). Any subparser this file
# registers must pass `parents=[common]` — later tasks (8, 11, 14) adding
# subparsers to this file need to follow the same pattern.
common = argparse.ArgumentParser(add_help=False)
common.add_argument("--json", action="store_true", help="machine-readable output")

_QUIET = False


def say(message: str) -> None:
    """Human-facing line. Silent under --json, which owns stdout."""
    if not _QUIET:
        print(message)


class LoopError(Exception):
    """A user-facing error. Printed to stderr; exit code 1."""


def load_state() -> State:
    return events.fold(jsonl.read_all())


def emit(event: dict) -> None:
    jsonl.append(event)


def require_active(state: State):
    loop = state.active_loop()
    if loop is None:
        raise LoopError("no active loop. run `loop open \"<question>\"` first.")
    return loop


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loop", description="a thrash detector for debugging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    opener = subparsers.add_parser("open", help="open a loop", parents=[common])
    opener.add_argument("question")
    opener.add_argument("--budget", help="time budget, e.g. 45m")
    opener.add_argument("--interval", help="check-in interval, e.g. 20m")

    trier = subparsers.add_parser(
        "try", help="record an action and the belief behind it", parents=[common]
    )
    trier.add_argument("action")
    trier.add_argument("--because", required=True, help="what you believe this will show")

    hyp = subparsers.add_parser("hyp", help="manage hypotheses")
    hyp_sub = hyp.add_subparsers(dest="hyp_command", required=True)
    # `--json` on the leaf parsers, not on `hyp` itself: once `hyp_sub`
    # consumes "add"/"kill", the remaining tokens (including a trailing
    # `--json`) are parsed by hyp_add/hyp_kill, never by `hyp`.
    hyp_add = hyp_sub.add_parser("add", parents=[common])
    hyp_add.add_argument("text")
    hyp_kill = hyp_sub.add_parser("kill", parents=[common])
    hyp_kill.add_argument("hyp_id", type=int)

    subparsers.add_parser("status", help="show the active loop", parents=[common])
    return parser


def cmd_open(args, state: State, now: float) -> dict:
    active = state.active_loop()
    parent_id = active.id if active is not None else None
    if active is not None:
        depth = events.stack_depth(state)
        if depth >= MAX_STACK_DEPTH:
            raise LoopError(
                f"stack depth {depth}. close or abandon something before opening another."
            )
        say(f"  ⚠  #{active.id} \"{active.question}\" is active")
        if not prompts.ask_yes_no("pause it and stack this on top?"):
            raise LoopError("aborted.")
        emit(events.make("loop_paused", ts=now, loop_id=active.id, reason=args.question))
        say(f"  #{active.id} paused at "
            f"{format_duration(active.elapsed(now))} active.")
        state = load_state()

    stop_condition = prompts.ask_text("stop condition?")
    budget_s = (
        prompts.parse_duration(args.budget)
        if args.budget
        else prompts.ask_duration("time budget?", default=DEFAULT_BUDGET_S)
    )
    interval_s = prompts.parse_duration(args.interval) if args.interval else DEFAULT_INTERVAL_S
    hypotheses = prompts.ask_lines("hypotheses?", minimum=1)

    event = events.make(
        "loop_opened",
        ts=now,
        loop_id=events.next_loop_id(state),
        question=args.question,
        stop_condition=stop_condition,
        budget_s=budget_s,
        interval_s=interval_s,
        hypotheses=hypotheses,
        parent_id=parent_id,
    )
    emit(event)

    depth = events.stack_depth(load_state())
    say(f"  #{event['loop_id']} open. timer running. "
        f"{len(hypotheses)} hypotheses live.")
    if depth > 1:
        say(f"  stack depth {depth}.")
    if depth >= WARN_STACK_DEPTH:
        say("  ⚠  you are context switching, not working.")
    return event


def cmd_try(args, state: State, now: float) -> dict:
    loop = require_active(state)
    event = events.make(
        "action_logged", ts=now, loop_id=loop.id, action=args.action, because=args.because
    )
    emit(event)
    say(f"  logged. {loop.actions + 1} actions this loop.")
    return event


def cmd_hyp(args, state: State, now: float) -> dict:
    loop = require_active(state)
    if args.hyp_command == "add":
        event = events.make(
            "hypothesis_added",
            ts=now,
            loop_id=loop.id,
            hyp_id=events.next_hypothesis_id(loop),
            text=args.text,
        )
        emit(event)
        say(f"  hypothesis {event['hyp_id']} added. "
            f"{len(loop.live_hypotheses()) + 1} live.")
        return event

    if not any(h.id == args.hyp_id and h.alive for h in loop.hypotheses):
        raise LoopError(f"no live hypothesis {args.hyp_id}.")
    event = events.make("hypothesis_eliminated", ts=now, loop_id=loop.id, hyp_id=args.hyp_id)
    emit(event)
    say(f"  hypothesis {args.hyp_id} ruled out. "
        f"{len(loop.live_hypotheses()) - 1} live.")
    return event


def cmd_status(args, state: State, now: float) -> dict:
    for line in render.status_lines(state, now):
        say(line)
    loop = state.active_loop()
    return {"active_id": state.active_id, "elapsed_s": loop.elapsed(now) if loop else None}


COMMANDS = {
    "open": cmd_open,
    "try": cmd_try,
    "hyp": cmd_hyp,
    "status": cmd_status,
}


def main(argv: list[str] | None = None) -> int:
    global _QUIET
    args = build_parser().parse_args(argv)
    _QUIET = args.json
    now = time.time()
    try:
        result = COMMANDS[args.command](args, load_state(), now)
    except (LoopError, prompts.Aborted) as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
