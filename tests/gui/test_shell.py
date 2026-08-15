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


def test_closing_the_window_hides_it_instead_of_quitting(qapp):
    from PySide6.QtGui import QCloseEvent
    from loop.gui.window import MainWindow

    window = MainWindow(controller=None)
    window.show()
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert window.isHidden()
