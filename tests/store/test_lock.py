import json
import subprocess
import sys
import textwrap
import time

import pytest

from loop.store import lock


def test_exclusive_creates_the_lock_file(tmp_path):
    target = tmp_path / "nested" / "lock"
    with lock.exclusive(target):
        pass
    assert target.exists()


def test_exclusive_is_reentrant_across_sequential_holds(tmp_path):
    target = tmp_path / "lock"
    for _ in range(3):
        with lock.exclusive(target):
            pass


HOLD_PROGRAM = textwrap.dedent("""
    import sys, time
    from pathlib import Path
    from loop.store import lock
    with lock.exclusive(Path(sys.argv[1])):
        print("held", flush=True)
        time.sleep(5)
""")


def test_a_held_lock_times_out_a_second_acquirer(tmp_path):
    target = tmp_path / "lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_PROGRAM, str(target)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        started = time.monotonic()
        with pytest.raises(lock.LockTimeout):
            with lock.exclusive(target, timeout_s=0.5):
                pass
        assert time.monotonic() - started < 4.0
    finally:
        holder.kill()
        holder.wait()


def test_concurrent_appends_do_not_interleave_or_vanish(tmp_path):
    """The whole point of the lock: two processes, 200 lines each, 400 intact."""
    events_path = tmp_path / "events.jsonl"
    lock_path = tmp_path / "lock"
    program = textwrap.dedent("""
        import json, sys
        from pathlib import Path
        from loop.store import jsonl, lock
        events_path, lock_path, tag = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
        for index in range(200):
            with lock.exclusive(lock_path):
                jsonl.append({"tag": tag, "index": index, "pad": "x" * 600}, events_path)
    """)
    workers = [
        subprocess.Popen([sys.executable, "-c", program, str(events_path), str(lock_path), tag])
        for tag in ("a", "b")
    ]
    for worker in workers:
        assert worker.wait(timeout=60) == 0

    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 400
    parsed = [json.loads(line) for line in lines]  # raises if a line is torn
    assert sorted(entry["index"] for entry in parsed if entry["tag"] == "a") == list(range(200))
    assert sorted(entry["index"] for entry in parsed if entry["tag"] == "b") == list(range(200))
