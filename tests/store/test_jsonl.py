import pytest

from loop.store import jsonl


def test_append_then_read_round_trips(tmp_path):
    path = tmp_path / "events.jsonl"
    jsonl.append({"type": "a", "ts": 1.0}, path)
    jsonl.append({"type": "b", "ts": 2.0}, path)
    assert jsonl.read_all(path) == [
        {"type": "a", "ts": 1.0},
        {"type": "b", "ts": 2.0},
    ]


def test_read_all_on_missing_file_is_empty(tmp_path):
    assert jsonl.read_all(tmp_path / "nope.jsonl") == []


def test_truncated_final_line_is_tolerated(tmp_path):
    path = tmp_path / "events.jsonl"
    jsonl.append({"type": "a", "ts": 1.0}, path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type": "b", "ts":')
    assert jsonl.read_all(path) == [{"type": "a", "ts": 1.0}]


def test_a_torn_tail_is_repaired_by_the_next_append(tmp_path):
    """The tail `read_all` forgives must not become interior corruption.

    Without a repair in `append`, the next event is concatenated onto the
    partial line — the write looks like it succeeded and silently is not
    there — and the append after that pushes the merged line off the end,
    where it stops being the forgiven tail and becomes a permanent
    `CorruptLogError`: no dashboard, no check-ins, every CLI command
    refusing, from one torn byte.
    """
    path = tmp_path / "events.jsonl"
    jsonl.append({"type": "a", "ts": 1.0}, path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type": "b", "ts":')      # crash mid-append

    jsonl.append({"type": "c", "ts": 3.0}, path)
    assert jsonl.read_all(path) == [{"type": "a", "ts": 1.0}, {"type": "c", "ts": 3.0}]

    jsonl.append({"type": "d", "ts": 4.0}, path)  # where it used to go permanent
    assert jsonl.read_all(path) == [
        {"type": "a", "ts": 1.0},
        {"type": "c", "ts": 3.0},
        {"type": "d", "ts": 4.0},
    ]


def test_a_whole_but_unterminated_tail_is_completed_not_thrown_away(tmp_path):
    """A crash between the event's bytes and its newline leaves a line
    `read_all` parses and returns today. Repairing the tail must finish
    that line, not delete an event the log already counts."""
    path = tmp_path / "events.jsonl"
    jsonl.append({"type": "a", "ts": 1.0}, path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"b","ts":2.0}')    # every byte but the newline

    assert jsonl.read_all(path) == [{"type": "a", "ts": 1.0}, {"type": "b", "ts": 2.0}]

    jsonl.append({"type": "c", "ts": 3.0}, path)
    assert jsonl.read_all(path) == [
        {"type": "a", "ts": 1.0},
        {"type": "b", "ts": 2.0},
        {"type": "c", "ts": 3.0},
    ]


def test_a_torn_tail_longer_than_one_read_block_is_still_repaired(tmp_path):
    """The backwards scan for the last newline is chunked; a partial line
    bigger than one chunk must not defeat it."""
    path = tmp_path / "events.jsonl"
    jsonl.append({"type": "a", "ts": 1.0}, path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"b","note":"' + "x" * 20_000)

    jsonl.append({"type": "c", "ts": 3.0}, path)
    assert jsonl.read_all(path) == [{"type": "a", "ts": 1.0}, {"type": "c", "ts": 3.0}]


def test_a_first_ever_append_onto_a_torn_only_line_is_repaired(tmp_path):
    """No preceding newline anywhere in the file — the backwards scan runs
    off the front and must truncate the whole thing."""
    path = tmp_path / "events.jsonl"
    path.write_text('{"type": "a", "ts":', encoding="utf-8")
    jsonl.append({"type": "b", "ts": 2.0}, path)
    assert jsonl.read_all(path) == [{"type": "b", "ts": 2.0}]


def test_invalid_utf8_is_a_corrupt_log_not_a_unicode_decode_error(tmp_path):
    """`UnicodeDecodeError` is not a `CorruptLogError`, so the GUI's two
    catch sites (`controller.refresh`, `Scheduler.tick`) miss it entirely:
    the dashboard freezes with no unreadable banner and the check-ins stop,
    with the traceback going to a DEVNULL'd stderr."""
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"type":"a"}\n{"type":"\xff\xfe"}\n{"type":"c"}\n')
    with pytest.raises(jsonl.CorruptLogError) as excinfo:
        jsonl.read_all(path)
    assert "line 2" in str(excinfo.value)


def test_corrupt_interior_line_raises(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"type": "a"}\nNOT JSON\n{"type": "c"}\n', encoding="utf-8")
    with pytest.raises(jsonl.CorruptLogError) as excinfo:
        jsonl.read_all(path)
    assert "line 2" in str(excinfo.value)


def test_blank_lines_are_skipped(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"type": "a"}\n\n{"type": "c"}\n', encoding="utf-8")
    assert jsonl.read_all(path) == [{"type": "a"}, {"type": "c"}]
