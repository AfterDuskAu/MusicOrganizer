"""index: index.sqlite, the rebuildable cache: schema version, WAL, read-only opens, stable
item ids, and plain-English errors for a damaged or newer index."""

from __future__ import annotations

import sqlite3
import unicodedata
from contextlib import closing

import pytest

from musicorg import index as index_module
from musicorg.errors import LibraryIndexError
from musicorg.index import Index, item_id, open_index
from musicorg.library import Library


def an_item(source_id: str = "s_000000000001", rel: str = "A - B.mp3") -> dict[str, object]:
    return {
        "id": item_id(source_id, rel),
        "source_id": source_id,
        "rel_path": rel,
        "size": 10,
        "mtime_ns": 20,
        "ext": ".mp3",
        "parsed_version_json": ["remix:x"],
        "flags_json": [],
        "parse_confidence": 0.3,
        "scanned_at": "2026-09-29T00:00:00Z",
    }


def test_item_ids_are_stable() -> None:
    first = item_id("s_abc", "Folder/Song.mp3")
    assert first == item_id("s_abc", "Folder/Song.mp3")
    assert first.startswith("i_") and len(first) == 18
    assert first != item_id("s_abd", "Folder/Song.mp3")
    decomposed = unicodedata.normalize("NFD", "Café/Beyoncé.mp3")
    assert item_id("s_abc", decomposed) == item_id("s_abc", "Café/Beyoncé.mp3")


def test_a_new_index(lib: Library) -> None:
    with open_index(lib.paths, write=True) as index:
        conn = index.conn
        assert conn is not None
        assert conn.execute("PRAGMA user_version").fetchone()[0] == index_module.SCHEMA_VERSION
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        index.put_source("s_000000000001", "/rips", "2026-09-29T00:00:00Z")
        index.put_items([an_item()])
        item = index.item(item_id("s_000000000001", "A - B.mp3"))
    assert item is not None
    assert item["parsed_version_json"] == ["remix:x"]  # JSON columns come back as values
    assert item["state"] == "new"


def test_reading_while_a_writer_has_it_open(lib: Library) -> None:
    with open_index(lib.paths, write=True) as writer:
        writer.put_source("s_000000000001", "/rips", "2026-09-29T00:00:00Z")
        writer.put_items([an_item()])
        with open_index(lib.paths, write=False) as reader:
            assert reader.counts_by_state() == {"new": 1}
            assert reader.low_confidence_count(0.5) == 1
            with pytest.raises(RuntimeError, match="read-only"):
                reader.set_state(item_id("s_000000000001", "A - B.mp3"), "review")


def test_a_missing_index_reads_as_empty(lib: Library) -> None:
    lib.paths.index_file.unlink(missing_ok=True)
    with open_index(lib.paths, write=False) as reader:
        assert reader.counts_by_state() == {}
        assert reader.items() == []
        assert reader.library_track_count() == 0
    assert not lib.paths.index_file.exists()  # a read never creates it


def test_items_of_a_removed_source_go_with_it(lib: Library) -> None:
    with open_index(lib.paths, write=True) as index:
        index.put_source("s_000000000001", "/rips", "2026-09-29T00:00:00Z")
        index.put_items([an_item(rel=f"{n}.mp3") for n in range(3)])
        assert index.remove_source("s_000000000001") == 3
        assert index.items() == []


def test_a_newer_index_is_refused(lib: Library) -> None:
    with open_index(lib.paths, write=True) as index:
        assert index.conn is not None
        index.conn.execute(f"PRAGMA user_version = {index_module.SCHEMA_VERSION + 1}")
    with pytest.raises(LibraryIndexError, match="newer version"):
        Index(lib.paths, write=True)


def test_a_damaged_index_says_how_to_fix_it(lib: Library) -> None:
    lib.paths.index_file.write_bytes(b"this is not a database" * 100)
    for suffix in ("-wal", "-shm"):
        (lib.paths.index_file.parent / f"index.sqlite{suffix}").unlink(missing_ok=True)
    with pytest.raises(LibraryIndexError, match="musicorg index rebuild"):
        Index(lib.paths, write=True)
    with Index(lib.paths, write=False) as reader, pytest.raises(LibraryIndexError):
        reader.counts_by_state()


def test_reset_empties_only_the_index(lib: Library) -> None:
    lib.paths.queue_file.write_bytes(b"queue")
    with open_index(lib.paths, write=True) as index:
        index.put_source("s_000000000001", "/rips", "2026-09-29T00:00:00Z")
        index.put_items([an_item()])
        index.reset()
        assert index.items() == []
        assert index.sources() == {}
    assert lib.paths.queue_file.read_bytes() == b"queue"
    with closing(sqlite3.connect(lib.paths.index_file)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone() == (index_module.SCHEMA_VERSION,)


# ---- the order of an item's candidates --------------------------------------------------


def a_candidate(video_id: str, score: float, **payload: object) -> dict[str, object]:
    return {"id": f"c_{video_id}", "video_id": video_id, "score": score, "reasons": [],
            "payload": {"video_id": video_id, "title": video_id, **payload}}  # fmt: skip


def test_candidates_come_best_first_and_a_pasted_link_before_them(lib: Library) -> None:
    with open_index(lib.paths, write=True) as index:
        index.put_source("s_000000000001", "/rips", "2026-09-29T00:00:00Z")
        item = an_item()
        iid = str(item["id"])
        index.put_items([item])
        plain = [a_candidate("low", 0.61), a_candidate("tie1", 0.8), a_candidate("top", 0.97),
                 a_candidate("tie2", 0.8)]  # fmt: skip

        # No mark: the best score first, and equal scores in the order they were stored.
        index.set_match(iid, "review", [], plain)
        by_score = ["top", "tie1", "tie2", "low"]
        assert [c["video_id"] for c in index.candidates(iid)] == by_score
        assert [c["video_id"] for c in index.all_candidates()[iid]] == by_score
        # A mark that isn't true is no mark.
        index.set_match(iid, "review", [], [*plain, a_candidate("off", 0.3, pasted=False)])
        assert [c["video_id"] for c in index.candidates(iid)] == [*by_score, "off"]

        # The owner's pasted link, marked: first whatever its score, the rest as before.
        index.set_match(iid, "review", [], [*plain, a_candidate("link", 0.3, pasted=True)])
        assert [c["video_id"] for c in index.candidates(iid)] == ["link", *by_score]
        assert [c["video_id"] for c in index.all_candidates()[iid]] == ["link", *by_score]
        assert index.candidates(iid)[0]["score"] == 0.3  # its real score

        # A new match result replaces every row: nothing of the mark is left.
        index.set_match(iid, "review", [], [*plain, a_candidate("link", 0.3)])
        assert [c["video_id"] for c in index.candidates(iid)] == [*by_score, "link"]
