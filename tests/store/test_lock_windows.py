"""The Windows branch of `loop/store/lock.py`, simulated.

There is no Windows machine here and this box has no `msvcrt` to import,
so `lock.py`'s `if sys.platform == "win32":` — evaluated once, at import
time — has never picked the `msvcrt.locking` implementation on this
machine at all. A runtime `monkeypatch.setattr(sys, "platform", "win32")`
after `lock` is already imported has no effect on `_try_acquire`/
`_release`, which were bound to the POSIX `fcntl` versions the moment the
module loaded and stay that way for the rest of the process.

The only way to exercise the Windows branch from here is to fake
`msvcrt`, set `sys.platform`, and load a fresh copy of the module's code
so the top-level `if` runs again with those in place.

**That fresh copy is loaded into its own throwaway module object,
never into `sys.modules['loop.store.lock']`.** An earlier version of
this fixture used `importlib.reload()` on the *real* module instead —
which re-executes `class LockTimeout(Exception): ...` in place, handing
back a brand-new class object every time. `loop/gui/actions.py` and
`loop/gui/scheduler.py` both do `from loop.store.lock import
LockTimeout` — a frozen name binding, taken once at their own import
time. Reloading the real module after that binding exists does not
update it: `raise LockTimeout(...)` inside a reloaded `lock.py`
constructs an instance of the *new* class, while `actions.py`'s `except
LockTimeout:` still checks against the *old* one — and stops catching
it, silently. Restoring `sys.platform` and reloading *again* afterwards
does not fix this either; it just produces a *third* class object,
still different from whatever `actions.py`/`scheduler.py` are holding.
Today's alphabetical test collection order (`tests/gui` before
`tests/store`) happened to import those two modules before this file's
reload ever ran, so nothing observed the mismatch — which is exactly
the kind of thing that breaks the moment collection order changes
(a subset run, a random-order plugin, ...). See
`test_the_simulation_never_touches_the_real_modules_identity` below,
and `tests/gui/test_lock_windows_identity.py`, which pins the
`actions.py`/`scheduler.py` side directly.

Loading into a throwaway module object sidesteps the whole class of
problem: `sys.modules['loop.store.lock']` — and every class already
bound out of it, anywhere in the process — is never touched, regardless
of what order tests run in.

This proves the *decision logic* — which exception is contention and
which is a genuine fault — behaves as intended when Python's own errno
mapping (verified separately: `OSError(errno.EACCES, ...)` really is a
`PermissionError`; `OSError(errno.EBADF, ...)` is not) is what
`msvcrt.locking` hands back. It does not, and cannot, prove that real
Windows `msvcrt.locking` actually raises those errnos for those
conditions — that is unverified; see the Task 17 report.
"""

from __future__ import annotations

import errno
import importlib.util
import sys
import time
import types

import pytest

from loop.store import lock as real_lock


class _FakeHandle:
    """Stands in for the open file handle. Only `.fileno()` and `.seek()`
    are used by either platform's `_try_acquire`/`_release`."""

    def __init__(self, fd: int = 3) -> None:
        self._fd = fd
        self.seeks: list[int] = []

    def fileno(self) -> int:
        return self._fd

    def seek(self, offset: int) -> None:
        self.seeks.append(offset)


def _fake_msvcrt() -> types.ModuleType:
    fake = types.ModuleType("msvcrt")
    fake.LK_NBLCK = 2
    fake.LK_UNLCK = 0
    fake.calls = []
    fake.raises = None  # an exception instance, or None to succeed

    def locking(fd, mode, nbytes):
        fake.calls.append((fd, mode, nbytes))
        if fake.raises is not None:
            raise fake.raises

    fake.locking = locking
    return fake


def _load_windows_lock(fake_msvcrt: types.ModuleType):
    """Execute `loop/store/lock.py`'s code into a fresh, independent
    module object — not `sys.modules['loop.store.lock']` — with
    `sys.platform`/`msvcrt` patched only for the duration of this one
    `exec_module` call. The real module, and every reference anyone else
    in the process already holds into it, is never touched."""
    spec = importlib.util.find_spec("loop.store.lock")
    module = importlib.util.module_from_spec(spec)
    original_platform = sys.platform
    original_msvcrt = sys.modules.get("msvcrt")
    sys.platform = "win32"
    sys.modules["msvcrt"] = fake_msvcrt
    try:
        spec.loader.exec_module(module)
    finally:
        sys.platform = original_platform
        if original_msvcrt is None:
            sys.modules.pop("msvcrt", None)
        else:
            sys.modules["msvcrt"] = original_msvcrt
    return module


@pytest.fixture
def win32_lock():
    fake = _fake_msvcrt()
    module = _load_windows_lock(fake)
    yield module, fake


def test_the_fixture_really_engages_the_msvcrt_branch(win32_lock):
    """Sanity check on the harness itself: a successful `_try_acquire`
    must have gone through the fake `msvcrt.locking`, not `fcntl.flock`."""
    mod, fake = win32_lock
    assert mod._try_acquire(_FakeHandle(fd=3)) is True
    assert fake.calls == [(3, fake.LK_NBLCK, 1)]


def test_the_simulation_never_touches_the_real_modules_identity():
    """The hazard the module docstring describes, made concrete and
    checked directly rather than trusted. This is the test that would
    have failed against the fixture's previous `importlib.reload()`
    implementation: reloading the real module rebinds `LockTimeout` (and
    every function) to new objects, so `real_lock.LockTimeout is before`
    below would already be false immediately after the simulation runs.

    See `tests/gui/test_lock_windows_identity.py` for the same guarantee
    checked from the consuming side — `loop.gui.actions`/`scheduler`'s
    own bound `LockTimeout` reference — which needs PySide6 and so
    cannot live in this file."""
    before_platform = sys.platform
    before_msvcrt = sys.modules.get("msvcrt")
    before_timeout_cls = real_lock.LockTimeout
    before_try_acquire = real_lock._try_acquire
    before_release = real_lock._release

    fake = _fake_msvcrt()
    fake.raises = PermissionError(errno.EACCES, "already locked")
    simulated = _load_windows_lock(fake)
    assert simulated is not real_lock  # a genuinely separate module object
    assert simulated._try_acquire(_FakeHandle()) is False  # actually run, not just loaded

    assert sys.platform == before_platform
    assert sys.modules.get("msvcrt") is before_msvcrt
    assert real_lock.LockTimeout is before_timeout_cls
    assert real_lock._try_acquire is before_try_acquire
    assert real_lock._release is before_release


def test_lock_contention_is_retried_not_raised(win32_lock):
    """`LK_NBLCK` on an already-locked range fails with errno EACCES,
    which Python's OSError constructor turns into PermissionError. That
    is contention — the Windows analogue of POSIX's BlockingIOError —
    and `_try_acquire` must swallow it into `False` (retry), not let it
    escape."""
    mod, fake = win32_lock
    fake.raises = PermissionError(errno.EACCES, "already locked")
    assert mod._try_acquire(_FakeHandle()) is False


def test_a_genuine_fault_is_not_caught_as_contention(win32_lock):
    """The defect this pins: a bare `except OSError` would also swallow a
    bad handle or any other fault, silently retrying it. Only the
    EACCES/PermissionError contention case may be caught here."""
    mod, fake = win32_lock
    fake.raises = OSError(errno.EBADF, "bad file descriptor")
    with pytest.raises(OSError) as excinfo:
        mod._try_acquire(_FakeHandle())
    assert excinfo.value.errno == errno.EBADF
    assert not isinstance(excinfo.value, PermissionError)


def test_exclusive_surfaces_a_genuine_windows_fault_immediately(win32_lock, tmp_path):
    """End to end through `exclusive()`: a real fault must not wait out
    `timeout_s` and come back as `LockTimeout` — it must come back as
    itself, right away. Before the fix (`except OSError`), this waited
    the full `timeout_s` and raised `LockTimeout` instead."""
    mod, fake = win32_lock
    fake.raises = OSError(errno.EBADF, "bad file descriptor")
    started = time.monotonic()
    with pytest.raises(OSError) as excinfo:
        with mod.exclusive(tmp_path / "lock", timeout_s=5.0):
            pass
    assert not isinstance(excinfo.value, mod.LockTimeout)
    assert time.monotonic() - started < 1.0


def test_exclusive_retries_contention_until_it_clears(win32_lock, tmp_path):
    mod, fake = win32_lock
    attempts: list[int] = []

    def locking(fd, mode, nbytes):
        attempts.append(mode)
        if mode == fake.LK_NBLCK and attempts.count(fake.LK_NBLCK) < 3:
            raise PermissionError(errno.EACCES, "already locked")

    fake.locking = locking
    with mod.exclusive(tmp_path / "lock"):
        pass
    assert attempts.count(fake.LK_NBLCK) == 3


def test_release_seeks_to_zero_before_unlocking(win32_lock):
    """`msvcrt.locking` locks/unlocks a byte range starting at the
    current file position. An unlock issued from the wrong position
    releases nothing, so `_release` must reposition first — mirroring
    the same hazard on the acquire side (see `test_lock.py`)."""
    mod, fake = win32_lock
    handle = _FakeHandle()
    mod._release(handle)
    assert handle.seeks == [0]
    assert fake.calls == [(3, fake.LK_UNLCK, 1)]
