"""The tray icon and its menu.

Closing the window hides it; the scheduler keeps running. Quit is only
reachable from here, and when a loop is active it says what stops.
"""

from __future__ import annotations

from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QMessageBox, QSystemTrayIcon

from loop.gui import theme

ICON_PX = 32


def burn_colour(fraction: float | None, mode: str) -> QColor:
    """The tray dot reports burn, because it is often the only part of the
    app on screen. No loop is dim; over budget is critical."""
    tokens = theme.TOKENS[mode]
    if fraction is None:
        return QColor(tokens["dim"])
    if fraction >= 1.0:
        return QColor(tokens["crit"])
    if fraction >= 0.75:
        return QColor(tokens["warn"])
    return QColor(tokens["accent"])


def render_icon(fraction: float | None, mode: str) -> QIcon:
    """Drawn, not loaded: an icon file would be one more thing to package."""
    pixmap = QPixmap(ICON_PX, ICON_PX)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(burn_colour(fraction, mode))
    painter.setPen(QColor(0, 0, 0, 0))
    painter.drawEllipse(4, 4, ICON_PX - 8, ICON_PX - 8)
    painter.end()
    return QIcon(pixmap)


class Tray(QSystemTrayIcon):
    def __init__(self, window, mode: str = "dark") -> None:
        super().__init__(window)
        self._window = window
        self._mode = mode
        self.setIcon(render_icon(None, mode))

        menu = QMenu()
        show = QAction("Show loop", menu)
        show.triggered.connect(window.surface)
        menu.addAction(show)
        menu.addSeparator()
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self._confirm_quit)
        menu.addAction(quit_action)
        self.setContextMenu(menu)

        self.activated.connect(lambda _reason: window.surface())

    def set_burn(self, fraction: float | None) -> None:
        self.setIcon(render_icon(fraction, self._mode))

    def _confirm_quit(self) -> None:
        from PySide6.QtWidgets import QApplication

        if self._window.has_active_loop():
            answer = QMessageBox.warning(
                self._window,
                "Quit loop?",
                "A loop is running. Quitting stops its check-ins — the timer "
                "keeps counting, but nothing will interrupt you.",
                QMessageBox.Cancel | QMessageBox.Discard,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Discard:
                return
        QApplication.quit()
