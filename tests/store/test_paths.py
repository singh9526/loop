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


def test_events_and_lock_live_under_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_HOME", str(tmp_path))
    assert paths.events_path() == tmp_path / "events.jsonl"
    assert paths.lock_path() == tmp_path / "lock"
