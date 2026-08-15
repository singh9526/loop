"""Every test in this package needs a Qt that may not be installed.

The suite must pass on a machine with no PySide6 — these skip, and the
coverage that matters lives in tests/app/.
"""

import os

import pytest

pytest.importorskip("PySide6", reason="GUI tests need the [gui] extra")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
