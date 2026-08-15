"""Entry point. `python -m loop.gui` or the `loop-gui` script."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from loop.gui import instance, theme
from loop.gui.controller import Controller
from loop.gui.tray import Tray
from loop.gui.window import MainWindow


def detect_mode(app: QApplication) -> str:
    """Follow the OS. Qt reports this on both platforms from 6.5."""
    from PySide6.QtCore import Qt

    hints = app.styleHints()
    return "dark" if hints.colorScheme() == Qt.ColorScheme.Dark else "light"


def main(argv: list[str] | None = None) -> int:
    # An existing instance is reused rather than always constructing one:
    # QApplication is a process-wide singleton, and reusing it is what
    # lets tests drive main() inside a session that already has a qapp.
    # In a real launch nothing pre-exists, so this is always the
    # QApplication(argv) branch there.
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("loop")
    app.setQuitOnLastWindowClosed(False)  # the tray outlives the window

    try:
        server = instance.claim(app)
    except instance.ClaimError as exc:
        # A genuine bind failure is not a hand-off: silence here would be
        # a launch that does nothing, with no way for the user to tell
        # that from success.
        print(f"loop-gui: {exc}", file=sys.stderr)
        return 1
    if server is None:
        return 0  # another copy is up; it has been asked to surface

    mode = detect_mode(app)
    app.setStyleSheet(theme.stylesheet(mode))

    controller = Controller()
    window = MainWindow(controller, mode)
    tray = Tray(window, mode)
    controller.changed.connect(window.bind)
    controller.changed.connect(
        lambda dashboard: tray.set_burn(
            dashboard.meter.fraction if dashboard.meter else None
        )
    )
    tray.show()
    window.show()
    controller.start()

    server.newConnection.connect(lambda: _surface(server, window))
    return app.exec()


def _surface(server, window) -> None:
    connection = server.nextPendingConnection()
    if connection is not None:
        connection.disconnectFromServer()
    window.surface()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
