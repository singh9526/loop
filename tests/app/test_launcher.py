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
