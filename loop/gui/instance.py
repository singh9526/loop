"""One app per LOOP_HOME.

Two copies would fire two check-ins for one interval and contend on every
append. The socket is the only authority on liveness — no pidfile, no
heartbeat, nothing that can go stale in a way that lies.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from loop.app.launcher import server_name

CONNECT_TIMEOUT_MS = 300
SURFACE = b"surface\n"


class ClaimError(RuntimeError):
    """The socket could not be bound for a reason other than a live
    incumbent — permissions, fd exhaustion, and the like. The caller must
    surface this, not treat it like a quiet hand-off."""


def _probe(name: str) -> bool:
    """Return True if a live instance answered `name` — and was asked to
    surface, as a side effect of getting an answer at all."""
    probe = QLocalSocket()
    probe.connectToServer(name)
    if not probe.waitForConnected(CONNECT_TIMEOUT_MS):
        return False
    probe.write(SURFACE)
    probe.flush()
    probe.waitForBytesWritten(CONNECT_TIMEOUT_MS)
    probe.disconnectFromServer()
    return True


def claim(app: QCoreApplication) -> QLocalServer | None:
    """Become the single instance, or ask the incumbent to show itself.

    Returns the server on success, `None` when another copy answered and
    was asked to surface. Raises `ClaimError` when the name could not be
    bound for any other reason — that is a real failure and must not be
    mistaken for a hand-off.
    """
    name = server_name()

    if _probe(name):
        return None

    server = QLocalServer(app)
    if server.listen(name):
        return server

    # The name is taken but nobody answered just now. Two explanations:
    # a socket file left behind by a hard kill (safe to reclaim), or a
    # second launch racing this one, which bound the name in the gap
    # between our probe above and the listen() just now (must not steal
    # it). Re-probe before touching anything — only a socket that still
    # answers nothing is safe to remove.
    if _probe(name):
        return None

    QLocalServer.removeServer(name)
    if server.listen(name):
        return server

    raise ClaimError(f"could not bind single-instance socket {name!r}")
