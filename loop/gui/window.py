"""The dashboard window."""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QWidget

MIN_SIZE = (720, 560)


class MainWindow(QMainWindow):
    def __init__(self, controller) -> None:
        super().__init__()
        self._controller = controller
        self.setWindowTitle("loop")
        self.setMinimumSize(*MIN_SIZE)
        self.setCentralWidget(QWidget(self))

    def has_active_loop(self) -> bool:
        return self._controller is not None and self._controller.active_id is not None

    def surface(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        """Hide, do not quit. The app is the scheduler — closing the window
        must not silently stop the check-ins the user is relying on."""
        event.ignore()
        self.hide()
