from loop.core import events, timeline


def opened(loop_id=1, hypotheses=("cert expiry", "pg pool")):
    return events.make(
        "loop_opened", ts=0.0, loop_id=loop_id,
        question="staging deploy fails at startup",
        stop_condition="comes up clean twice",
        budget_s=2700.0, interval_s=1200.0,
        hypotheses=list(hypotheses), parent_id=None,
    )


def test_the_loops_own_boundaries_produce_no_entries():
    log = [opened(), events.make("loop_closed", ts=9.0, loop_id=1,
                                 what_was_it="a", giveaway="b", five_min_path="c")]
    assert timeline.entries(log, 1) == []


def test_an_action_carries_its_because():
    log = [opened(), events.make("action_logged", ts=5.0, loop_id=1,
                                 action="added print in wrapper",
                                 because="will show the swallowed frame")]
    assert timeline.entries(log, 1) == [
        timeline.Entry(ts=5.0, kind=timeline.ACTION,
                       text="added print in wrapper",
                       because="will show the swallowed frame")
    ]


def test_other_loops_are_excluded():
    log = [
        opened(loop_id=1),
        events.make("action_logged", ts=5.0, loop_id=1, action="mine", because="x"),
        events.make("action_logged", ts=6.0, loop_id=2, action="theirs", because="y"),
    ]
    assert [entry.text for entry in timeline.entries(log, 1)] == ["mine"]


def test_a_manual_kill_names_the_hypothesis_and_has_no_because():
    log = [opened(), events.make("hypothesis_eliminated", ts=7.0, loop_id=1, hyp_id=2)]
    entry, = timeline.entries(log, 1)
    assert entry.kind == timeline.RULED_OUT
    assert entry.text == "ruled out §2 pg pool"
    assert entry.because is None


def test_a_kill_that_followed_its_ping_is_attributed_to_the_checkin():
    log = [
        opened(),
        events.make("ping_answered", ts=7.0, loop_id=1, smaller=True,
                    eliminated=2, shown_at=6.0),
        events.make("hypothesis_eliminated", ts=7.0, loop_id=1, hyp_id=2),
    ]
    ping, killed = timeline.entries(log, 1)
    assert ping.kind == timeline.PING
    assert ping.text == "check-in: hypothesis space smaller"
    assert killed.because == timeline.CHECKIN_BECAUSE


def test_a_kill_that_did_not_follow_its_ping_is_not_attributed():
    """Same events, one action wedged between. The adjacency is the evidence."""
    log = [
        opened(),
        events.make("ping_answered", ts=7.0, loop_id=1, smaller=True,
                    eliminated=2, shown_at=6.0),
        events.make("action_logged", ts=7.5, loop_id=1, action="a", because="b"),
        events.make("hypothesis_eliminated", ts=8.0, loop_id=1, hyp_id=2),
    ]
    assert timeline.entries(log, 1)[-1].because is None


def test_a_ping_answered_no_reads_as_no_progress():
    log = [opened(), events.make("ping_answered", ts=7.0, loop_id=1, smaller=False,
                                 eliminated=None, shown_at=6.0)]
    assert timeline.entries(log, 1)[0].text == "check-in: hypothesis space not smaller"


def test_an_unanswered_ping_is_recorded():
    log = [opened(), events.make("ping_unanswered", ts=7.0, loop_id=1, shown_at=6.0)]
    assert timeline.entries(log, 1)[0].text == "check-in: timed out"


def test_a_late_hypothesis_is_named_by_its_own_event():
    log = [
        opened(),
        events.make("hypothesis_added", ts=3.0, loop_id=1, hyp_id=3, text="dns"),
        events.make("hypothesis_eliminated", ts=4.0, loop_id=1, hyp_id=3),
    ]
    added, killed = timeline.entries(log, 1)
    assert added.text == "added §3 dns"
    assert killed.text == "ruled out §3 dns"


def test_checkpoints_scope_cuts_extensions_pauses_and_resumes_all_render():
    log = [
        opened(),
        events.make("checkpoint_shown", ts=10.0, loop_id=1, kind="p75"),
        events.make("checkpoint_answered", ts=11.0, loop_id=1, kind="p75",
                    on_track=False, decision="cut"),
        events.make("scope_cut", ts=11.0, loop_id=1,
                    old_stop_condition="old", new_stop_condition="just the 500s"),
        events.make("checkpoint_unanswered", ts=20.0, loop_id=1, kind="p100", shown_at=19.0),
        events.make("budget_extended", ts=21.0, loop_id=1, old_budget_s=2700.0,
                    new_budget_s=3600.0, learned="the pool is shared"),
        events.make("loop_paused", ts=22.0, loop_id=1, reason="standup"),
        events.make("loop_resumed", ts=23.0, loop_id=1),
    ]
    texts = [entry.text for entry in timeline.entries(log, 1)]
    assert texts == [
        "p75: cut",
        "cut scope to just the 500s",
        "p100: timed out",
        "extended budget to 1h",
        "paused: standup",
        "resumed",
    ]
    assert timeline.entries(log, 1)[3].because == "the pool is shared"


def test_checkpoint_shown_alone_produces_nothing():
    log = [opened(), events.make("checkpoint_shown", ts=10.0, loop_id=1, kind="p75")]
    assert timeline.entries(log, 1) == []
