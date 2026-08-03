"""Argument parsing and command dispatch."""

from __future__ import annotations

import argparse
import json
import sys
import time

from loop import prompts, render
from loop.core import events
from loop.core.models import MAX_STACK_DEPTH, PAUSED, WARN_STACK_DEPTH, State
from loop.core.timefmt import format_duration
from loop.sched import daemon
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

    pauser = subparsers.add_parser("pause", help="pause the active loop", parents=[common])
    pauser.add_argument("--reason", help="what interrupted; prompted if omitted")

    resumer = subparsers.add_parser("resume", help="resume a paused loop", parents=[common])
    resumer.add_argument("loop_id", nargs="?", type=int)

    subparsers.add_parser("ls", help="show the loop stack", parents=[common])
    subparsers.add_parser("close", help="close the active loop with a postmortem", parents=[common])
    subparsers.add_parser("abandon", help="abandon the active loop", parents=[common])

    subparsers.add_parser("tick", help="internal: run one scheduler tick", parents=[common])

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
    daemon.ensure_running()
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


def cmd_pause(args, state: State, now: float) -> dict:
    loop = require_active(state)
    reason = args.reason or prompts.ask_text("what interrupted?")
    event = events.make("loop_paused", ts=now, loop_id=loop.id, reason=reason)
    emit(event)
    say(f"  #{loop.id} paused at {format_duration(loop.elapsed(now))} active.")
    return event


def cmd_resume(args, state: State, now: float) -> dict:
    target = _resume_target(args, state)

    active = state.active_loop()
    if active is not None:
        if active.id == target.id:
            raise LoopError(f"#{target.id} is already active.")
        reason = prompts.ask_text("what interrupted?")
        emit(events.make("loop_paused", ts=now, loop_id=active.id, reason=reason))

    event = events.make("loop_resumed", ts=now, loop_id=target.id)
    emit(event)
    _say_resumed(target, now)
    daemon.ensure_running()
    return event


def _resume_target(args, state: State):
    if args.loop_id is None:
        paused = state.paused_loops()
        if not paused:
            raise LoopError("nothing to resume.")
        return paused[0]

    target = state.loops.get(args.loop_id)
    if target is None:
        raise LoopError(f"no loop #{args.loop_id}.")
    if target.status != PAUSED:
        raise LoopError(f"#{args.loop_id} is {target.status}, not paused.")
    return target


def _say_resumed(loop, now: float) -> None:
    """The resumed-status line, shared by `resume` and the close/abandon pop."""
    say(f"  → resumed #{loop.id} · "
        f"{format_duration(loop.elapsed(now))} / {format_duration(loop.budget_s)} · "
        f"next ping {format_duration(loop.interval_s)}")


def pop_to_parent(state: State, loop, now: float) -> int | None:
    """Resume the parent if it is still paused. Returns the resumed loop id."""
    if loop.parent_id is None:
        return None
    parent = state.loops.get(loop.parent_id)
    if parent is None or parent.status != PAUSED:
        return None
    emit(events.make("loop_resumed", ts=now, loop_id=parent.id))
    return parent.id


def cmd_close(args, state: State, now: float) -> dict:
    loop = require_active(state)
    event = events.make(
        "loop_closed",
        ts=now,
        loop_id=loop.id,
        what_was_it=prompts.ask_text("what was it?"),
        giveaway=prompts.ask_text("what was the giveaway?"),
        five_min_path=prompts.ask_text("how could I have found it in 5 minutes?"),
    )
    emit(event)
    say(f"  closed #{loop.id}. {format_duration(loop.elapsed(now))}. "
        f"{loop.eliminated} hypotheses ruled out. pattern saved.")
    _report_pop(load_state(), loop, now)
    return event


def cmd_abandon(args, state: State, now: float) -> dict:
    loop = require_active(state)
    event = events.make("loop_abandoned", ts=now, loop_id=loop.id)
    emit(event)
    say(f"  abandoned #{loop.id} at {format_duration(loop.elapsed(now))} active.")
    _report_pop(load_state(), loop, now)
    return event


def _report_pop(state: State, loop, now: float) -> None:
    resumed_id = pop_to_parent(state, loop, now)
    if resumed_id is None:
        return
    _say_resumed(state.loops[resumed_id], now)


def cmd_ls(args, state: State, now: float) -> dict:
    for line in render.stack_lines(state, now):
        say(line)
    return {
        "active_id": state.active_id,
        "paused_ids": [lp.id for lp in state.paused_loops()],
    }


def cmd_tick(args, state: State, now: float) -> dict:
    from loop.sched.tick import tick

    due = tick(now=None)
    return {"fired": None if due is None else due.kind}


COMMANDS = {
    "open": cmd_open,
    "try": cmd_try,
    "hyp": cmd_hyp,
    "pause": cmd_pause,
    "resume": cmd_resume,
    "ls": cmd_ls,
    "close": cmd_close,
    "abandon": cmd_abandon,
    "status": cmd_status,
    "tick": cmd_tick,
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
