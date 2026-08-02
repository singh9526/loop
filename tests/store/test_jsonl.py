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
