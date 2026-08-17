"""Proof that simulating Windows for `loop/store/lock.py` cannot desync
`LockTimeout`'s identity from the two GUI modules that import it by name.

`loop/gui/actions.py` and `loop/gui/scheduler.py` both do
`from loop.store.lock import LockTimeout` — a frozen name binding, taken
once at import time. `tests/store/test_lock_windows.py` simulates the
Windows branch of `lock.py` by `exec_module`-ing a fresh copy of its
code into a throwaway module object, never into
`sys.modules['loop.store.lock']` — specifically so the real module, and
every class already bound out of it anywhere in the process, is never
touched. An earlier version of that fixture used `importlib.reload()` on
the real module instead, which rebinds `LockTimeout` to a new class
object on every reload; `actions.py`/`scheduler.py`'s already-bound
reference would silently go stale, and `except LockTimeout:` in either
file would stop catching a `LockTimeout` a reloaded `lock.py` raises.

This lives under `tests/gui/`, not `tests/store/`, specifically so it
can import `loop.gui.actions`/`loop.gui.scheduler` and check their bound
reference directly — the thing a reader of `tests/store/
test_lock_windows.py` alone cannot see, since that file must stay
PySide6-free.
"""

from __future__ import annotations

import importlib.util
import sys
import types

import loop.gui.actions as actions_module
import loop.gui.scheduler as scheduler_module
from loop.store import lock as real_lock


class _Handle:
    def fileno(self) -> int:
        return 3

    def seek(self, offset: int) -> None:
        pass


def _simulate_windows_once() -> None:
    """The same isolation technique `tests/store/test_lock_windows.py`
    uses: `exec_module` a fresh copy of `loop/store/lock.py` with the
    Windows branch active, touching `sys.modules['loop.store.lock']`
    never. Exercises `_try_acquire` once so this is a real run of the
    Windows code, not just an import of it."""
    fake_msvcrt = types.ModuleType("msvcrt")
    fake_msvcrt.LK_NBLCK = 2
    fake_msvcrt.LK_UNLCK = 0
    fake_msvcrt.locking = lambda fd, mode, nbytes: None  # always "succeeds"

    spec = importlib.util.find_spec("loop.store.lock")
    module = importlib.util.module_from_spec(spec)
    original_platform = sys.platform
    original_msvcrt = sys.modules.get("msvcrt")
    sys.platform = "win32"
    sys.modules["msvcrt"] = fake_msvcrt
    try:
        spec.loader.exec_module(module)
        assert module._try_acquire(_Handle()) is True
    finally:
        sys.platform = original_platform
        if original_msvcrt is None:
            sys.modules.pop("msvcrt", None)
        else:
            sys.modules["msvcrt"] = original_msvcrt


def test_actions_and_scheduler_keep_the_real_locktimeout_after_a_windows_simulation():
    before = real_lock.LockTimeout
    # Sanity: both modules already agree with the real module, before
    # any simulation runs at all.
    assert actions_module.LockTimeout is before
    assert scheduler_module.LockTimeout is before

    _simulate_windows_once()

    # The assertion that matters: still true afterwards. Under the
    # reload-based approach this module's docstring describes, the
    # first of these three would already be false — reloading the real
    # module rebinds `LockTimeout` there, away from `before`, while
    # `actions_module.LockTimeout`/`scheduler_module.LockTimeout` (bound
    # once, at their own import time) would stay pointed at the old
    # class, no longer matching `real_lock.LockTimeout`.
    assert real_lock.LockTimeout is before
    assert actions_module.LockTimeout is before
    assert scheduler_module.LockTimeout is before
