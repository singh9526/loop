import pytest

from loop.core import events
from loop.core.models import ABANDONED, ACTIVE, CLOSED, PAUSED


def test_make_builds_a_flat_dict():
    event = events.make("loop_paused", ts=10.0, loop_id=1, reason="prod incident")
    assert event == {
        "type": "loop_paused",
        "ts": 10.0,
        "loop_id": 1,
        "reason": "prod incident",
    }


def test_make_rejects_unknown_type():
    with pytest.raises(ValueError, match="unknown event type"):
        events.make("loop_exploded", ts=1.0, loop_id=1)


def test_make_rejects_missing_field():
    with pytest.raises(ValueError, match="reason"):
        events.make("loop_paused", ts=1.0, loop_id=1)


def test_make_rejects_extra_field():
    with pytest.raises(ValueError, match="mood"):
        events.make("loop_paused", ts=1.0, loop_id=1, reason="x", mood="bad")


def opened(loop_id=1, ts=0.0, parent_id=None, budget_s=2700.0, hypotheses=("a", "b")):
    return events.make(
        "loop_opened",
        ts=ts,
        loop_id=loop_id,
        question=f"q{loop_id}",
        stop_condition=f"s{loop_id}",
        budget_s=budget_s,
        interval_s=1200.0,
        hypotheses=list(hypotheses),
        parent_id=parent_id,
    )


def test_fold_opens_an_active_loop():
    state = events.fold([opened()])
    loop = state.active_loop()
    assert state.active_id == 1
    assert loop.status == ACTIVE
    assert loop.intervals == [[0.0, None]]
    assert [h.text for h in loop.hypotheses] == ["a", "b"]
    assert [h.id for h in loop.hypotheses] == [1, 2]
    assert loop.original_budget_s == 2700.0


def test_fold_pause_closes_the_interval_and_clears_active():
    state = events.fold(
        [opened(), events.make("loop_paused", ts=600.0, loop_id=1, reason="lunch")]
    )
    assert state.active_id is None
    assert state.loops[1].status == PAUSED
    assert state.loops[1].intervals == [[0.0, 600.0]]


def test_fold_resume_opens_a_new_interval_and_rebases_the_ping():
    state = events.fold(
        [
            opened(),
            events.make("ping_answered", ts=1200.0, loop_id=1, smaller=True,
                        eliminated=None, shown_at=1200.0),
            events.make("loop_paused", ts=1500.0, loop_id=1, reason="lunch"),
            events.make("loop_resumed", ts=9000.0, loop_id=1),
        ]
    )
    loop = state.loops[1]
    assert state.active_id == 1
    assert loop.intervals == [[0.0, 1500.0], [9000.0, None]]
    # elapsed at resume is 1500s, so the next ping is a full interval from there
    assert loop.last_ping_elapsed == 1500.0


def test_fold_rejects_two_active_loops():
    with pytest.raises(events.InvariantError):
        events.fold([opened(loop_id=1), opened(loop_id=2)])


def test_fold_allows_stacking_after_an_explicit_pause():
    state = events.fold(
        [
            opened(loop_id=1),
            events.make("loop_paused", ts=100.0, loop_id=1, reason="prod incident"),
            opened(loop_id=2, ts=100.0, parent_id=1),
        ]
    )
    assert state.active_id == 2
    assert state.loops[2].parent_id == 1
    assert events.stack_depth(state) == 2


def test_fold_keeps_hypothesis_ids_stable_after_a_kill():
    state = events.fold(
        [
            opened(hypotheses=("a", "b", "c")),
            events.make("hypothesis_eliminated", ts=10.0, loop_id=1, hyp_id=2),
            events.make("hypothesis_added", ts=20.0, loop_id=1, hyp_id=4, text="d"),
        ]
    )
    loop = state.loops[1]
    assert [(h.id, h.alive) for h in loop.hypotheses] == [
        (1, True), (2, False), (3, True), (4, True)
    ]
    assert [h.id for h in loop.live_hypotheses()] == [1, 3, 4]
    assert loop.eliminated == 1


def test_fold_counts_actions_and_ping_answers():
    state = events.fold(
        [
            opened(),
            events.make("action_logged", ts=10.0, loop_id=1, action="restart pg",
                        because="pool exhausted"),
            events.make("ping_answered", ts=1200.0, loop_id=1, smaller=False,
                        eliminated=None, shown_at=1200.0),
            events.make("ping_unanswered", ts=2400.0, loop_id=1, shown_at=2400.0),
        ]
    )
    loop = state.loops[1]
    assert loop.actions == 1
    assert loop.pings_answered == 1
    assert loop.pings_timed_out == 1
    assert loop.recent_ping_answers == [False]
    assert loop.last_ping_elapsed == 2400.0


def test_fold_tracks_checkpoint_answers_and_pending_retries():
    key = events.checkpoint_key("p75", 2700.0)
    state = events.fold(
        [
            opened(),
            events.make("checkpoint_unanswered", ts=2025.0, loop_id=1, kind="p75",
                        shown_at=2025.0),
        ]
    )
    assert state.loops[1].checkpoints_pending == {key: 2025.0}

    state = events.fold(
        [
            opened(),
            events.make("checkpoint_unanswered", ts=2025.0, loop_id=1, kind="p75",
                        shown_at=2025.0),
            events.make("checkpoint_answered", ts=2700.0, loop_id=1, kind="p75",
                        on_track=True, decision="continue"),
        ]
    )
    assert state.loops[1].checkpoints_pending == {}
    assert state.loops[1].checkpoints_answered == {key}


def test_fold_extension_rearms_checkpoints_via_new_keys():
    state = events.fold(
        [
            opened(),
            events.make("checkpoint_answered", ts=2025.0, loop_id=1, kind="p75",
                        on_track=False, decision="extend"),
            events.make("budget_extended", ts=2025.0, loop_id=1, old_budget_s=2700.0,
                        new_budget_s=5400.0, learned="pool is not the issue"),
        ]
    )
    loop = state.loops[1]
    assert loop.budget_s == 5400.0
    assert loop.original_budget_s == 2700.0
    assert loop.extensions == 1
    assert events.checkpoint_key("p75", 5400.0) not in loop.checkpoints_answered


def test_fold_scope_cut_replaces_the_stop_condition():
    state = events.fold(
        [
            opened(),
            events.make("scope_cut", ts=2025.0, loop_id=1, old_stop_condition="s1",
                        new_stop_condition="comes up once"),
        ]
    )
    assert state.loops[1].stop_condition == "comes up once"


def test_fold_close_and_abandon_end_the_interval():
    closed = events.fold(
        [
            opened(),
            events.make("loop_closed", ts=3000.0, loop_id=1, what_was_it="cert",
                        giveaway="tls handshake", five_min_path="openssl s_client"),
        ]
    )
    assert closed.active_id is None
    assert closed.loops[1].status == CLOSED
    assert closed.loops[1].intervals == [[0.0, 3000.0]]
    assert closed.loops[1].postmortem["giveaway"] == "tls handshake"

    abandoned = events.fold(
        [opened(), events.make("loop_abandoned", ts=3000.0, loop_id=1)]
    )
    assert abandoned.loops[1].status == ABANDONED


def test_fold_pop_then_resume_parent_restores_the_stack():
    state = events.fold(
        [
            opened(loop_id=1),
            events.make("loop_paused", ts=100.0, loop_id=1, reason="prod incident"),
            opened(loop_id=2, ts=100.0, parent_id=1),
            events.make("loop_closed", ts=500.0, loop_id=2, what_was_it="bad deploy",
                        giveaway="500s only on /checkout", five_min_path="check rollout"),
            events.make("loop_resumed", ts=500.0, loop_id=1),
        ]
    )
    assert state.active_id == 1
    assert events.stack_depth(state) == 1
    assert state.loops[1].intervals == [[0.0, 100.0], [500.0, None]]


def test_stack_depth_counts_open_loops_only():
    state = events.fold(
        [
            opened(loop_id=1),
            events.make("loop_paused", ts=10.0, loop_id=1, reason="a"),
            opened(loop_id=2, ts=10.0, parent_id=1),
            events.make("loop_paused", ts=20.0, loop_id=2, reason="b"),
            opened(loop_id=3, ts=20.0, parent_id=2),
            events.make("loop_abandoned", ts=30.0, loop_id=3),
        ]
    )
    assert events.stack_depth(state) == 2
    assert events.next_loop_id(state) == 4


def test_paused_loops_are_most_recently_paused_first():
    state = events.fold(
        [
            opened(loop_id=1),
            events.make("loop_paused", ts=10.0, loop_id=1, reason="a"),
            opened(loop_id=2, ts=10.0, parent_id=1),
            events.make("loop_paused", ts=20.0, loop_id=2, reason="b"),
        ]
    )
    assert [lp.id for lp in state.paused_loops()] == [2, 1]


def test_every_declared_event_type_has_a_fold_handler():
    base = [opened()]
    samples = {
        "loop_paused": {"reason": "x"},
        "action_logged": {"action": "a", "because": "b"},
        "hypothesis_added": {"hyp_id": 3, "text": "c"},
        "hypothesis_eliminated": {"hyp_id": 1},
        "ping_answered": {"smaller": True, "eliminated": None, "shown_at": 1.0},
        "ping_unanswered": {"shown_at": 1.0},
        "checkpoint_shown": {"kind": "p75"},
        "checkpoint_answered": {"kind": "p75", "on_track": True, "decision": "continue"},
        "checkpoint_unanswered": {"kind": "p75", "shown_at": 1.0},
        "scope_cut": {"old_stop_condition": "a", "new_stop_condition": "b"},
        "budget_extended": {"old_budget_s": 1.0, "new_budget_s": 2.0, "learned": "x"},
        "loop_closed": {"what_was_it": "a", "giveaway": "b", "five_min_path": "c"},
        "loop_abandoned": {},
    }
    untested = set(events.EVENT_FIELDS) - set(samples) - {"loop_opened", "loop_resumed"}
    assert untested == set(), f"no fold sample for: {sorted(untested)}"

    for kind, fields in samples.items():
        events.fold(base + [events.make(kind, ts=50.0, loop_id=1, **fields)])
