import pytest

from loop.gui import instance


def test_the_first_claim_succeeds_and_the_second_is_refused(qapp):
    server = instance.claim(qapp)
    assert server is not None
    try:
        assert instance.claim(qapp) is None
    finally:
        server.close()


def test_a_stale_socket_is_reclaimed(qapp, monkeypatch):
    """A SIGKILLed copy leaves a socket file behind on POSIX. The next
    launch must take it over, not refuse to start forever."""
    server = instance.claim(qapp)
    assert server is not None
    server.close()          # closed without removing, like a hard kill
    reclaimed = instance.claim(qapp)
    assert reclaimed is not None
    reclaimed.close()


def test_a_socket_that_never_answers_is_removed_and_reclaimed(qapp, monkeypatch):
    """Decision-function version of the stale-socket case: both probes
    come back empty, so removeServer must run and the retry must
    succeed."""
    from PySide6.QtNetwork import QLocalServer

    probe_calls = []
    monkeypatch.setattr(instance, "_probe", lambda name: probe_calls.append(name) or False)

    listen_calls = []

    def fake_listen(self, name):
        listen_calls.append(name)
        return len(listen_calls) == 2  # fails first, succeeds after removeServer

    monkeypatch.setattr(QLocalServer, "listen", fake_listen)

    removed = []
    monkeypatch.setattr(QLocalServer, "removeServer", staticmethod(lambda name: removed.append(name)))

    result = instance.claim(qapp)

    assert result is not None
    assert removed, "a socket nobody answers must be cleared"
    assert len(probe_calls) == 2, "must re-probe before removing"


def test_a_racing_winner_is_not_stolen_from(qapp, monkeypatch):
    """Two near-simultaneous launches can both fail their first probe and
    both attempt listen(); the loser's listen() fails because the winner
    just bound the name. That failure looks identical to a stale socket
    from here — the only way to tell them apart is to re-probe. If the
    re-probe now gets an answer, the name is live and must not be
    stolen."""
    from PySide6.QtNetwork import QLocalServer

    probe_calls = []

    def fake_probe(name):
        probe_calls.append(name)
        # First probe: nothing answers yet (so we attempt listen).
        # Second probe, after our listen() lost the race: the winner
        # who just bound the name answers.
        return len(probe_calls) == 2

    monkeypatch.setattr(instance, "_probe", fake_probe)
    monkeypatch.setattr(QLocalServer, "listen", lambda self, name: False)

    removed = []
    monkeypatch.setattr(QLocalServer, "removeServer", staticmethod(lambda name: removed.append(name)))

    result = instance.claim(qapp)

    assert result is None, "must hand off to the racing winner"
    assert removed == [], "must not steal the winner's live socket"
    assert len(probe_calls) == 2


def test_a_genuine_bind_failure_raises_rather_than_pretending_success(qapp, monkeypatch):
    """If listen() still fails after a confirmed-empty re-probe and a
    removeServer, that is a real failure (permissions, fd exhaustion,
    ...) — not a live incumbent. claim() must not swallow it as None."""
    from PySide6.QtNetwork import QLocalServer

    monkeypatch.setattr(instance, "_probe", lambda name: False)
    monkeypatch.setattr(QLocalServer, "listen", lambda self, name: False)
    monkeypatch.setattr(QLocalServer, "removeServer", staticmethod(lambda name: None))

    with pytest.raises(instance.ClaimError):
        instance.claim(qapp)


def test_main_reports_a_genuine_bind_failure_instead_of_exiting_silently(qapp, monkeypatch, capsys):
    """A bind failure and a hand-off must not look the same from outside:
    one is a launch that did nothing and should say so."""
    import loop.gui.__main__ as gui_main

    def fail_claim(app):
        raise instance.ClaimError("boom")

    monkeypatch.setattr(instance, "claim", fail_claim)

    rc = gui_main.main(argv=[])

    assert rc != 0
    assert "boom" in capsys.readouterr().err


def test_main_exits_zero_silently_when_another_copy_is_running(qapp, monkeypatch, capsys):
    """The genuine hand-off case stays quiet: this is the contrast case
    to the bind-failure test above."""
    import loop.gui.__main__ as gui_main

    monkeypatch.setattr(instance, "claim", lambda app: None)

    rc = gui_main.main(argv=[])

    assert rc == 0
    assert capsys.readouterr().err == ""


def test_main_wires_the_whole_app_up_and_starts_the_scheduler(qapp, monkeypatch):
    """The happy path of `main()`, which nothing else reaches: both tests
    above short-circuit at `claim`.

    Deleting `scheduler.start()` left every other test in this suite green
    while shipping an app that opens loops and then never interrupts you —
    which is the entire product. Nothing asserted the `Scheduler` got the
    process's one `Writer`, or that a single one of the five signal
    connections below existed. `QApplication.exec` is stubbed, so this
    drives everything up to (and not including) the event loop.
    """
    from PySide6.QtNetwork import QLocalServer
    from PySide6.QtWidgets import QApplication

    import loop.gui.__main__ as gui_main
    from loop.gui.checkin import QtBlocker

    # A real QLocalServer that never listens: `main` only connects to its
    # `newConnection`, and binding a name here would fight the real guard.
    server = QLocalServer()
    monkeypatch.setattr(instance, "claim", lambda app: server)
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)

    built = {}

    def spy(name, factory):
        def make(*args, **kwargs):
            built[name] = factory(*args, **kwargs)
            return built[name]
        return make

    for name, attribute in (("controller", "Controller"), ("window", "MainWindow"),
                            ("tray", "Tray"), ("scheduler", "Scheduler")):
        monkeypatch.setattr(gui_main, attribute,
                            spy(name, getattr(gui_main, attribute)))

    # Patched on the class in its own module: the spy above replaced the
    # *name* `gui_main.Tray`, not the class it still calls.
    from loop.gui import tray as tray_module

    burns = []
    monkeypatch.setattr(tray_module.Tray, "set_burn",
                        lambda self, fraction: burns.append(fraction))
    postmortems = []
    monkeypatch.setattr("loop.gui.dialogs.lifecycle.CloseDialog",
                        lambda parent: postmortems.append(parent) or _NoDialog())

    stylesheet = qapp.styleSheet()
    try:
        assert gui_main.main(argv=[]) == 0

        controller = built["controller"]
        window = built["window"]
        scheduler = built["scheduler"]

        # The scheduler is running, on the app's one writer, drawing Qt.
        assert scheduler._timer.isActive(), "the app would never interrupt anyone"
        assert scheduler._writer is controller.writer
        assert scheduler._controller is controller
        assert isinstance(scheduler._blocker, QtBlocker)

        # controller.changed -> window.bind, and -> tray.set_burn
        assert window._dashboard is controller.view
        assert window._actions["open"].isEnabled()
        assert not window._actions["close"].isEnabled()
        assert burns == [None], "the tray dot must follow the view too"

        # scheduler.report -> window.show_report
        scheduler.report.emit("error", "could not be saved")
        assert window._report.text() == "could not be saved"

        # scheduler.busy_changed -> the window's and the tray's lockouts
        scheduler.busy_changed.emit(True)
        assert not any(action.isEnabled() for action in window._actions.values())
        assert built["tray"]._checkin_active is True
        scheduler.busy_changed.emit(False)
        assert window._actions["open"].isEnabled()

        # scheduler.postmortem -> the close dialog
        scheduler.postmortem.emit()
        assert postmortems == [window], "p100's `stop now` must open the postmortem"

        # server.newConnection -> the second launch's hand-off
        window.hide()
        server.newConnection.emit()
        assert window.isVisible(), "a second launch must surface the first window"
    finally:
        built["scheduler"].stop()
        built["controller"].stop()
        built["window"].close()
        server.close()
        qapp.setStyleSheet(stylesheet)


class _NoDialog:
    def values(self):
        return None


def test_closing_the_window_hides_it_instead_of_quitting(qapp):
    from PySide6.QtGui import QCloseEvent
    from loop.gui.window import MainWindow

    window = MainWindow(controller=None)
    window.show()
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert window.isHidden()
