"""Argument parsing and command dispatch."""

from __future__ import annotations

import argparse
import json
import sys
import time

from loop import prompts, render
from loop.app import commands, launcher
from loop.app.errors import LoopError  # noqa: F401  (re-exported; callers import it from here)
from loop.app.writer import Writer
from loop.core import events
from loop.core.models import MAX_STACK_DEPTH, WARN_STACK_DEPTH, State
from loop.core.timefmt import format_duration
from loop.store import jsonl, lock

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


def load_state() -> State:
    """The display read. Every write goes through `Writer.mutate`, which
    re-reads under the lock; this state is only ever advisory."""
    return events.fold(jsonl.read_all())


def require_active(state: State) -> None:
    """Advisory copy of the check every mutating command makes under the lock.

    Same reason as the two copies in `cmd_open`: a command that would refuse
    must refuse *before* it asks the user three questions. The copy inside
    `commands` is the authority — only it sees the state being written to.
    """
    if state.active_loop() is None:
        raise LoopError("no active loop. run `loop open \"<question>\"` first.")


def require_app() -> None:
    """Fail loudly, before any event is written, if nothing can show a check-in.

    `loop open` / `loop resume` must never leave a timer running that
    nothing will ever interrupt.
    """
    if not launcher.available():
        raise LoopError(launcher.UNAVAILABLE_MESSAGE)


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

    subparsers.add_parser("stats", help="the logbook", parents=[common])
    grepper = subparsers.add_parser(
        "grep", help="search closed and abandoned loops", parents=[common]
    )
    grepper.add_argument("term")

    subparsers.add_parser("status", help="show the active loop", parents=[common])
    return parser


def cmd_open(args, state: State, now: float) -> dict:
    require_app()

    # Advisory copies of two checks open_loop makes under the lock. They run
    # here so a refusal costs four keystrokes, not forty. The copies inside
    # open_loop are the authority — only they see the state being written to.
    active = state.active_loop()
    if active is not None:
        depth = events.stack_depth(state)
        if depth >= MAX_STACK_DEPTH:
            raise LoopError(
                f"stack depth {depth}. close or abandon something before opening another."
            )
        say(f"  ⚠  #{active.id} \"{active.question}\" is active")
        if not prompts.ask_yes_no("pause it and stack this on top?"):
            raise LoopError("aborted.")

    # Every answer is collected before anything is written. An abort here
    # (Ctrl-D at any remaining prompt) must leave the parent active and the
    # log untouched — not paused with nothing running underneath it.
    stop_condition = prompts.ask_text("stop condition?")
    budget_s = (
        prompts.parse_duration(args.budget)
        if args.budget
        else prompts.ask_duration("time budget?", default=DEFAULT_BUDGET_S)
    )
    interval_s = prompts.parse_duration(args.interval) if args.interval else DEFAULT_INTERVAL_S
    hypotheses = prompts.ask_lines("hypotheses?", minimum=1)

    result = commands.open_loop(
        Writer(now=lambda: now),
        question=args.question,
        stop_condition=stop_condition,
        budget_s=budget_s,
        interval_s=interval_s,
        hypotheses=hypotheses,
        stack_on_active=True,
    )

    if result.paused_id is not None:
        say(f"  #{result.paused_id} paused at "
            f"{format_duration(result.paused_elapsed_s)} active.")
    say(f"  #{result.loop_id} open. timer running. "
        f"{result.hypotheses} hypotheses live.")
    if result.depth > 1:
        say(f"  stack depth {result.depth}.")
    if result.depth >= WARN_STACK_DEPTH:
        say("  ⚠  you are context switching, not working.")
    launcher.ensure_running()
    return result.event


def cmd_try(args, state: State, now: float) -> dict:
    result = commands.log_action(
        Writer(now=lambda: now), action=args.action, because=args.because
    )
    say(f"  logged. {result.actions} actions this loop.")
    return result.event


def cmd_hyp(args, state: State, now: float) -> dict:
    if args.hyp_command == "add":
        result = commands.add_hypothesis(Writer(now=lambda: now), text=args.text)
        say(f"  hypothesis {result.hyp_id} added. {result.live} live.")
        return result.event

    result = commands.kill_hypothesis(Writer(now=lambda: now), hyp_id=args.hyp_id)
    say(f"  hypothesis {result.hyp_id} ruled out. {result.live} live.")
    return result.event


def cmd_status(args, state: State, now: float) -> dict:
    for line in render.status_lines(state, now):
        say(line)
    loop = state.active_loop()
    return {"active_id": state.active_id, "elapsed_s": loop.elapsed(now) if loop else None}


def cmd_pause(args, state: State, now: float) -> dict:
    require_active(state)
    reason = args.reason or prompts.ask_text("what interrupted?")
    result = commands.pause(Writer(now=lambda: now), reason=reason)
    say(f"  #{result.loop_id} paused at {format_duration(result.elapsed_s)} active.")
    return result.event


def cmd_resume(args, state: State, now: float) -> dict:
    require_app()

    # Asked here, before the write, because `resume` needs the answer and
    # only the terminal can collect it. `args.loop_id` may be None, in which
    # case the comparison is against None and the reason is asked — matching
    # today, where the prompt fires whenever an active loop exists and is
    # not the target.
    active = state.active_loop()
    pause_reason = (
        prompts.ask_text("what interrupted?")
        if active is not None and active.id != args.loop_id
        else None
    )
    result = commands.resume(
        Writer(now=lambda: now), loop_id=args.loop_id, pause_reason=pause_reason
    )
    _say_resumed(result.summary)
    launcher.ensure_running()
    return result.event


def _say_resumed(summary) -> None:
    """The resumed-status line, shared by `resume` and the close/abandon pop."""
    say(f"  → resumed #{summary.loop_id} · "
        f"{format_duration(summary.elapsed_s)} / {format_duration(summary.budget_s)} · "
        f"next ping {format_duration(summary.interval_s)}")


def cmd_close(args, state: State, now: float) -> dict:
    require_active(state)
    result = commands.close(
        Writer(now=lambda: now),
        what_was_it=prompts.ask_text("what was it?"),
        giveaway=prompts.ask_text("what was the giveaway?"),
        five_min_path=prompts.ask_text("how could I have found it in 5 minutes?"),
    )
    say(f"  closed #{result.loop_id}. {format_duration(result.elapsed_s)}. "
        f"{result.eliminated} hypotheses ruled out. pattern saved.")
    if result.resumed is not None:
        _say_resumed(result.resumed)
    return result.event


def cmd_abandon(args, state: State, now: float) -> dict:
    result = commands.abandon(Writer(now=lambda: now))
    say(f"  abandoned #{result.loop_id} at {format_duration(result.elapsed_s)} active.")
    if result.resumed is not None:
        _say_resumed(result.resumed)
    return result.event


def cmd_ls(args, state: State, now: float) -> dict:
    for line in render.stack_lines(state, now):
        say(line)
    return {
        "active_id": state.active_id,
        "paused_ids": [lp.id for lp in state.paused_loops()],
    }


def cmd_stats(args, state: State, now: float) -> dict:
    from loop.core import stats

    report = stats.compute(jsonl.read_all(), now)
    for line in stats.render(report):
        say(line)
    return report


def cmd_grep(args, state: State, now: float) -> dict:
    from loop.core.models import ABANDONED, CLOSED

    term = args.term.lower()
    log = jsonl.read_all()  # read once; re-reading per loop was O(loops * log)
    matches: list[dict] = []

    for loop in state.loops.values():
        if loop.status not in (CLOSED, ABANDONED):
            continue
        hits = [
            (label, text)
            for label, text in (loop.postmortem or {}).items()
            if term in text.lower()
        ]
        hits += [
            ("action", f"{event['action']} — because {event['because']}")
            for event in log
            if event["type"] == "action_logged"
            and event["loop_id"] == loop.id
            and (term in event["action"].lower() or term in event["because"].lower())
        ]
        if hits:
            matches.append({"loop_id": loop.id, "question": loop.question, "hits": hits})

    if not matches:
        say(f"  no matches for {args.term!r}.")
        return {"matches": []}

    for match in matches:
        say(f"  #{match['loop_id']}  {match['question']}")
        for label, text in match["hits"]:
            say(f"      {label}: {text}")
    return {"matches": matches}


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
    "stats": cmd_stats,
    "grep": cmd_grep,
}


def main(argv: list[str] | None = None) -> int:
    global _QUIET
    args = build_parser().parse_args(argv)
    _QUIET = args.json
    # One clock reading per invocation, threaded into every command's Writer
    # as `Writer(now=lambda: now)`. A CLI command is a single instant from
    # the user's point of view, and the minutes they spend answering
    # `close`'s postmortem prompts are not minutes the loop was running: a
    # Writer left on its own `time.time` would stamp `loop_closed` at the
    # moment of the last answer, inflating `closed_at`, elapsed-at-close, and
    # every estimate-drift figure derived from them.
    now = time.time()
    try:
        result = COMMANDS[args.command](args, load_state(), now)
    except (LoopError, prompts.Aborted) as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1
    except lock.LockTimeout as exc:
        # Not a LoopError: it is raised by the store, under Writer.mutate,
        # and would otherwise reach the user as a traceback.
        print(f"  {exc}", file=sys.stderr)
        return 1
    except jsonl.CorruptLogError as exc:
        print(
            f"  {exc}\n"
            "  the damaged tail is recoverable: everything before that line "
            "is intact — trim the bad tail by hand and re-run.",
            file=sys.stderr,
        )
        return 1
    except (events.InvariantError, ValueError) as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
