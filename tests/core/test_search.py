import pytest

from loop.core import events, search


def closed_loop(loop_id, question, postmortem, actions=()):
    log = [events.make("loop_opened", ts=0.0, loop_id=loop_id, question=question,
                       stop_condition="s", budget_s=2700.0, interval_s=1200.0,
                       hypotheses=["h"], parent_id=None)]
    for action, because in actions:
        log.append(events.make("action_logged", ts=1.0, loop_id=loop_id,
                               action=action, because=because))
    log.append(events.make("loop_closed", ts=2.0, loop_id=loop_id, **postmortem))
    return log


POSTMORTEM = {"what_was_it": "a stale TLS cert",
              "giveaway": "only failed after a restart",
              "five_min_path": "check notAfter first"}


def find(log, term):
    return search.find(events.fold(log), log, term)


def test_a_postmortem_hit_is_found_case_insensitively():
    matches = find(closed_loop(1, "q", POSTMORTEM), "STALE")
    assert matches[0].loop_id == 1
    assert ("what_was_it", "a stale TLS cert") in [(h.label, h.text) for h in matches[0].hits]


def test_an_action_hit_carries_its_because():
    log = closed_loop(1, "q", POSTMORTEM, actions=[("dumped the pool", "shows leaks")])
    hit = find(log, "leaks")[0].hits[0]
    assert hit.label == "action"
    assert hit.text == "dumped the pool — because shows leaks"


def test_open_loops_are_not_searched():
    """grep is the logbook. A loop you are still inside is on screen already."""
    log = closed_loop(1, "q", POSTMORTEM)[:-1]
    assert find(log, "stale") == []


def test_abandoned_loops_are_searched():
    log = [events.make("loop_opened", ts=0.0, loop_id=1, question="stale cert?",
                       stop_condition="s", budget_s=2700.0, interval_s=1200.0,
                       hypotheses=["h"], parent_id=None),
           events.make("action_logged", ts=1.0, loop_id=1,
                       action="checked notAfter", because="stale cert theory"),
           events.make("loop_abandoned", ts=2.0, loop_id=1)]
    assert find(log, "stale")[0].loop_id == 1


def test_no_matches_is_an_empty_list_not_an_error():
    assert find(closed_loop(1, "q", POSTMORTEM), "kubernetes") == []
