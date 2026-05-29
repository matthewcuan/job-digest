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
