"""Every test in this package needs a Qt that may not be installed.

The suite must pass on a machine with no PySide6 — these skip, and the
coverage that matters lives in tests/app/.
"""

import pytest

pytest.importorskip("PySide6", reason="GUI tests need the [gui] extra")


@pytest.fixture(scope="session", autouse=True)
def offscreen():
    """Force the offscreen platform; never merely default to it.

    `os.environ.setdefault` left a developer who exports QT_QPA_PLATFORM
    running the suite on their real display — and `tests/gui/test_checkin.py`
    calls `CheckinWindow.ask()`, which covers every screen for five
    minutes. Setting it is the only thing standing between a test run and
    that window.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("QT_QPA_PLATFORM", "offscreen")
        yield


@pytest.fixture(scope="session")
def qapp(offscreen):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    # A hard stop rather than a comment: if anything ever creates the
    # application before `offscreen` runs, every window in this package
    # would be a real one.
    assert app.platformName() == "offscreen", (
        f"refusing to run GUI tests on the {app.platformName()!r} platform"
    )
    yield app


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
