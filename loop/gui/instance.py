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


def claim(app: QCoreApplication) -> QLocalServer | None:
    """Become the single instance, or ask the incumbent to show itself.

    Returns the server on success, `None` when another copy answered.
    """
    name = server_name()

    probe = QLocalSocket()
    probe.connectToServer(name)
    if probe.waitForConnected(CONNECT_TIMEOUT_MS):
        probe.write(SURFACE)
        probe.flush()
        probe.waitForBytesWritten(CONNECT_TIMEOUT_MS)
        probe.disconnectFromServer()
        return None

    server = QLocalServer(app)
    if not server.listen(name):
        # Nobody answered but the name is taken: a socket file left behind
        # by a hard kill. Clearing it is safe precisely because the connect
        # above failed.
        QLocalServer.removeServer(name)
        if not server.listen(name):
            return None
    return server
