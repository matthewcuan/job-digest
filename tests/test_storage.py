"""JsonlStorage round-trip and edge cases."""
from __future__ import annotations

from job_aggregator.storage import JsonlStorage
from tests.helpers import make_job


def test_roundtrip_new_and_seen(tmp_path):
    store = JsonlStorage(tmp_path / "seen.jsonl")
    assert store.load_seen() == set()

    written = store.record([make_job("a"), make_job("b")])
    assert written == 2
    assert store.load_seen() == {"a", "b"}

    # Appends accumulate across calls.
    store.record([make_job("c")])
    assert store.load_seen() == {"a", "b", "c"}


def test_record_empty_is_noop(tmp_path):
    store = JsonlStorage(tmp_path / "seen.jsonl")
    assert store.record([]) == 0
    assert not (tmp_path / "seen.jsonl").exists()


def test_reset_clears(tmp_path):
    store = JsonlStorage(tmp_path / "seen.jsonl")
    store.record([make_job("a")])
    store.reset()
    assert store.load_seen() == set()
    # reset on a missing file is harmless
    store.reset()


def test_malformed_lines_are_skipped(tmp_path):
    path = tmp_path / "seen.jsonl"
    path.write_text('not json\n\n{"job_id": "z", "title": "T"}\n', encoding="utf-8")
    assert JsonlStorage(path).load_seen() == {"z"}


def test_creates_parent_dir(tmp_path):
    store = JsonlStorage(tmp_path / "nested" / "dir" / "seen.jsonl")
    store.record([make_job("a")])
    assert store.load_seen() == {"a"}


def test_prune_removes_old_keeps_recent_and_garbage(tmp_path):
    import json
    from datetime import timedelta

    from job_aggregator.util import now_utc

    path = tmp_path / "seen.jsonl"
    old = (now_utc() - timedelta(days=200)).isoformat()
    recent = now_utc().isoformat()
    path.write_text(
        json.dumps({"job_id": "old", "first_seen": old}) + "\n"
        + json.dumps({"job_id": "recent", "first_seen": recent}) + "\n"
        + "garbage line\n",
        encoding="utf-8",
    )
    store = JsonlStorage(path)
    removed = store.prune(90)
    assert removed == 1
    assert store.load_seen() == {"recent"}        # old dropped
    assert "garbage line" in path.read_text(encoding="utf-8")  # unparseable kept


def test_prune_missing_file_or_zero(tmp_path):
    store = JsonlStorage(tmp_path / "missing.jsonl")
    assert store.prune(90) == 0
    store.record([make_job("a")])
    assert store.prune(0) == 0  # zero/None retention is a no-op
