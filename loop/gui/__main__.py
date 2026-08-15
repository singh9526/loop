"""Entry point. `python -m loop.gui` or the `loop-gui` script."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from loop.gui import instance, theme
from loop.gui.tray import Tray
from loop.gui.window import MainWindow


def detect_mode(app: QApplication) -> str:
    """Follow the OS. Qt reports this on both platforms from 6.5."""
    from PySide6.QtCore import Qt

    hints = app.styleHints()
    return "dark" if hints.colorScheme() == Qt.ColorScheme.Dark else "light"


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("loop")
    app.setQuitOnLastWindowClosed(False)  # the tray outlives the window

    server = instance.claim(app)
    if server is None:
        return 0  # another copy is up; it has been asked to surface

    mode = detect_mode(app)
    app.setStyleSheet(theme.stylesheet(mode))

    window = MainWindow(controller=None)
    tray = Tray(window, mode)
    tray.show()
    window.show()

    server.newConnection.connect(lambda: _surface(server, window))
    return app.exec()


def _surface(server, window) -> None:
    connection = server.nextPendingConnection()
    if connection is not None:
        connection.disconnectFromServer()
    window.surface()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
