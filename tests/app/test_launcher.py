import importlib.util
import subprocess
import sys

from loop.app import launcher


def test_server_name_is_stable_for_one_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
    assert launcher.server_name() == launcher.server_name()


def test_two_homes_get_different_names(tmp_path, monkeypatch):
    """A throwaway LOOP_HOME must never surface the window watching the real one."""
    monkeypatch.setenv("LOOP_HOME", str(tmp_path / "a"))
    first = launcher.server_name()
    monkeypatch.setenv("LOOP_HOME", str(tmp_path / "b"))
    assert launcher.server_name() != first


def test_server_name_is_a_legal_socket_name(tmp_path, monkeypatch):
    """No separators: Qt turns this into a filesystem path on POSIX."""
    monkeypatch.setenv("LOOP_HOME", str(tmp_path / "with spaces" / "and/slashes"))
    name = launcher.server_name()
    assert name.startswith("loop-")
    assert name.replace("loop-", "").isalnum()
    assert len(name) <= 32


# --- available ------------------------------------------------------------
#
# `available()` must be importable and callable on a machine with no
# PySide6 — this module lives in the Qt-free `app/` layer — so these tests
# drive it through `find_spec`, never a real import of `loop.gui`.


def test_available_true_when_both_pyside6_and_gui_are_found(monkeypatch):
    monkeypatch.setattr(
        importlib.util, "find_spec",
        lambda name: object() if name in ("PySide6", "loop.gui") else None,
    )
    assert launcher.available() is True


def test_available_false_without_pyside6(monkeypatch):
    monkeypatch.setattr(
        importlib.util, "find_spec",
        lambda name: None if name == "PySide6" else object(),
    )
    assert launcher.available() is False


def test_available_false_without_the_gui_package(monkeypatch):
    monkeypatch.setattr(
        importlib.util, "find_spec",
        lambda name: None if name == "loop.gui" else object(),
    )
    assert launcher.available() is False


# --- ensure_running ---------------------------------------------------------
#
# Never lets a real subprocess spawn: `subprocess.Popen` is replaced before
# `ensure_running` is called, so these tests can run on a machine with no
# PySide6 and never put a window (or a second pytest process) anywhere.


def test_ensure_running_spawns_the_gui_module(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kwargs: calls.append((argv, kwargs)))

    launcher.ensure_running()

    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == [sys.executable, "-m", "loop.gui"]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL


def test_ensure_running_uses_creation_flags_not_start_new_session_on_windows(monkeypatch):
    """`start_new_session` is a POSIX-only Popen kwarg (it calls
    `setsid()`); Windows detaches a spawned process via `creationflags`
    instead. This machine's `subprocess` module does not define the
    Windows constants at all (confirmed: `hasattr(subprocess,
    'DETACHED_PROCESS')` is False on macOS), so the branch is simulated
    by adding them rather than by finding them already there."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "DETACHED_PROCESS", 0x00000008, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kwargs: calls.append((argv, kwargs)))

    launcher.ensure_running()

    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == [sys.executable, "-m", "loop.gui"]
    assert "start_new_session" not in kwargs
    assert kwargs["creationflags"] == 0x00000008 | 0x00000200
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL


def test_ensure_running_is_unconditional_not_deduplicated(monkeypatch):
    """Unlike `sched.daemon.ensure_running`, this never checks liveness
    itself — the socket handshake in the spawned process is what decides
    whether a second copy hands off instead of taking over. So two calls
    spawn two processes, and that is by design, not a bug to dedupe."""
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kwargs: calls.append(argv))

    launcher.ensure_running()
    launcher.ensure_running()

    assert len(calls) == 2
