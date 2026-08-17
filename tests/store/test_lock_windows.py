"""The Windows branch of `loop/store/lock.py`, simulated.

There is no Windows machine here and this box has no `msvcrt` to import,
so `lock.py`'s `if sys.platform == "win32":` — evaluated once, at import
time — has never picked the `msvcrt.locking` implementation on this
machine at all. A runtime `monkeypatch.setattr(sys, "platform", "win32")`
after `lock` is already imported has no effect on `_try_acquire`/
`_release`, which were bound to the POSIX `fcntl` versions the moment the
module loaded and stay that way for the rest of the process.

The only way to exercise the Windows branch from here is to fake
`msvcrt`, set `sys.platform`, and *reload* `loop.store.lock` so the
top-level `if` runs again — then reload it back to the real platform
before the test ends, in a bare `finally`, so a failing assertion still
leaves every later test in the session looking at the real POSIX
implementation instead of a module stuck pretending to be on Windows.

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
import importlib
import sys
import time
import types

import pytest

from loop.store import lock as lock_module


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


@pytest.fixture
def win32_lock():
    """Reload `loop.store.lock` as if imported on Windows, with a fake
    `msvcrt` whose `locking()` is scripted per test via `fake.raises` /
    `fake.locking`."""
    original_platform = sys.platform
    original_msvcrt = sys.modules.get("msvcrt")

    fake_msvcrt = types.ModuleType("msvcrt")
    fake_msvcrt.LK_NBLCK = 2
    fake_msvcrt.LK_UNLCK = 0
    fake_msvcrt.calls = []
    fake_msvcrt.raises = None  # an exception instance, or None to succeed

    def locking(fd, mode, nbytes):
        fake_msvcrt.calls.append((fd, mode, nbytes))
        if fake_msvcrt.raises is not None:
            raise fake_msvcrt.raises

    fake_msvcrt.locking = locking

    sys.platform = "win32"
    sys.modules["msvcrt"] = fake_msvcrt
    importlib.reload(lock_module)
    try:
        yield lock_module, fake_msvcrt
    finally:
        sys.platform = original_platform
        if original_msvcrt is None:
            sys.modules.pop("msvcrt", None)
        else:
            sys.modules["msvcrt"] = original_msvcrt
        importlib.reload(lock_module)


def test_the_fixture_really_engages_the_msvcrt_branch(win32_lock):
    """Sanity check on the harness itself: a successful `_try_acquire`
    must have gone through the fake `msvcrt.locking`, not `fcntl.flock`."""
    mod, fake = win32_lock
    assert mod._try_acquire(_FakeHandle(fd=3)) is True
    assert fake.calls == [(3, fake.LK_NBLCK, 1)]


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
