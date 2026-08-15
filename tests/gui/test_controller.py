import pytest

from loop.app import commands
from loop.app.writer import Writer
from loop.gui.controller import Controller
from loop.store import jsonl, paths


@pytest.fixture
def writer():
    return Writer(now=lambda: 0.0)


def open_one(writer, question="q"):
    return commands.open_loop(
        writer, question=question, stop_condition="s", budget_s=2700.0,
        interval_s=1200.0, hypotheses=["cert expiry", "pg pool"],
        stack_on_active=True,
    )


def test_an_empty_home_reports_no_active_loop(qapp, writer):
    controller = Controller(writer=writer, now=lambda: 0.0)
    controller.refresh()
    assert controller.active_id is None
    assert controller.view.enabled_actions == frozenset({"open"})


def test_refresh_emits_the_view(qapp, writer):
    open_one(writer)
    controller = Controller(writer=writer, now=lambda: 600.0)
    seen = []
    controller.changed.connect(seen.append)
    controller.refresh()
    assert seen[-1].active_id == 1
    assert seen[-1].meter.elapsed_s == 600.0


def test_the_clock_moves_without_the_log_changing(qapp, writer):
    open_one(writer)
    clock = [600.0]
    controller = Controller(writer=writer, now=lambda: clock[0])
    controller.refresh()
    reads = controller.reads
    clock[0] = 700.0
    controller.refresh()
    assert controller.view.meter.elapsed_s == 700.0
    assert controller.reads == reads  # the file was not touched again


def test_a_write_from_elsewhere_is_picked_up(qapp, writer):
    open_one(writer)
    controller = Controller(writer=writer, now=lambda: 600.0)
    controller.refresh()
    assert controller.view.timeline == ()

    commands.log_action(writer, action="added print", because="shows the frame")
    controller.refresh()
    assert [entry.text for entry in controller.view.timeline] == ["added print"]


def test_a_corrupt_interior_line_freezes_the_view_and_disables_everything(qapp, writer):
    open_one(writer)
    controller = Controller(writer=writer, now=lambda: 600.0)
    controller.refresh()

    with paths.events_path().open("a", encoding="utf-8") as handle:
        handle.write("NOT JSON\n")
        handle.write('{"type":"loop_abandoned","ts":1.0,"loop_id":1}\n')

    controller.refresh()
    assert controller.view.enabled_actions == frozenset()
    assert controller.view.active_id == 1              # last good state survives
    assert controller.view.meter.elapsed_s == 600.0    # frozen where it froze
    assert controller.view.error.endswith(":2")


def test_a_repaired_log_recovers_without_a_restart(qapp, writer):
    open_one(writer)
    controller = Controller(writer=writer, now=lambda: 600.0)
    controller.refresh()
    good = paths.events_path().read_text(encoding="utf-8")

    paths.events_path().write_text(good + "NOT JSON\n" + good, encoding="utf-8")
    controller.refresh()
    assert controller.view.enabled_actions == frozenset()

    paths.events_path().write_text(good, encoding="utf-8")
    controller.refresh()
    assert "try" in controller.view.enabled_actions
    assert controller.view.error is None
