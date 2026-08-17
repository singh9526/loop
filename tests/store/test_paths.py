import importlib

from loop.store import paths


def test_loop_home_honours_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path / "custom"))
    assert paths.loop_home() == tmp_path / "custom"


def test_loop_home_is_created_on_access(tmp_path, monkeypatch):
    target = tmp_path / "made-on-demand"
    monkeypatch.setenv("LOOP_HOME", str(target))
    paths.loop_home()
    assert target.is_dir()


def test_darwin_default_is_dot_loop(tmp_path, monkeypatch):
    monkeypatch.delenv("LOOP_HOME", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.loop_home() == tmp_path / ".loop"


def test_linux_default_respects_xdg(tmp_path, monkeypatch):
    monkeypatch.delenv("LOOP_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(paths.sys, "platform", "linux")
    assert paths.loop_home() == tmp_path / "xdg" / "loop"


def test_windows_default_uses_appdata(tmp_path, monkeypatch):
    """No test exercised this branch before Task 17 — `sys.platform ==
    "win32"` was reachable only on a real Windows machine, and the darwin/
    linux branches were the only ones simulated."""
    monkeypatch.delenv("LOOP_HOME", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setattr(paths.sys, "platform", "win32")
    assert paths.loop_home() == tmp_path / "Roaming" / "loop"


def test_windows_without_appdata_falls_back_to_the_profile(tmp_path, monkeypatch):
    """`APPDATA` is set by the OS on every real Windows session, but a
    stripped-down environment (a service, a container, a test) might not
    have it — the fallback must still land somewhere sane."""
    monkeypatch.delenv("LOOP_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.loop_home() == tmp_path / "AppData" / "Roaming" / "loop"


def test_events_and_lock_live_under_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
    assert paths.events_path() == tmp_path / "events.jsonl"
    assert paths.lock_path() == tmp_path / "lock"
