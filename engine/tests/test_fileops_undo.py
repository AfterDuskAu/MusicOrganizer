"""fileops.undo: dry runs, a whole replace-style batch put back exactly, repeated and
resumed undos, the queue hook, open batches, and what can't be undone automatically."""

from __future__ import annotations

from pathlib import Path

import pytest
from fileops_support import (
    SimulatedCrash,
    crash_in,
    files_in,
    journal,
    lines_of,
    put,
    recover,
    staged,
    tree,
)

from musicorg import fileops
from musicorg.errors import FileInUseError, NotFoundError, UndoError
from musicorg.library import Library

TRACK = "Artist/Album (2020)/01 Song.m4a"
LYRICS = b"[00:01.00]New lyrics\n"


@pytest.fixture
def rips(tmp_path: Path) -> list[Path]:
    return [put(tmp_path / "rips" / f"Song {n}.mp3", f"rip {n} ".encode() * 500) for n in (1, 2, 3)]


def music(lib: Library, rel: str = TRACK) -> Path:
    return lib.paths.music.joinpath(*rel.split("/"))


def contents(folder: Path) -> dict[str, bytes]:
    return {name: folder.joinpath(name).read_bytes() for name in files_in(folder)}


def adopt_all(lib: Library, rips: list[Path]) -> str:
    with fileops.batch(lib, "demo") as b:
        for rip in rips:
            fileops.copy_in(b, rip, f"Unknown Artist/Unsorted/{rip.name}")
    return b.batch_id


def test_unknown_batch(lib: Library) -> None:
    for batch_id in ("b_20200101-000000-000000", "nonsense"):
        with pytest.raises(NotFoundError, match="journal list"):
            fileops.undo(lib, batch_id)


def test_dry_run_changes_nothing(lib: Library, rips: list[Path]) -> None:
    old = put(music(lib), b"old")
    with fileops.batch(lib, "demo") as b:
        fileops.copy_in(b, rips[0], "A/1.mp3")
        fileops.supersede(b, old)
    before_music, before_journal = contents(lib.paths.music), journal(lib)

    result = fileops.undo(lib, b.batch_id, dry_run=True)
    assert result.dry_run
    assert result.undo_batch_id is None
    assert [(s.op_id, s.action, s.status) for s in result.steps] == [
        (2, "restore", "planned"),
        (1, "supersede", "planned"),
    ]
    assert result.steps[0].to == f"Music/{TRACK}"
    assert result.steps[1].to == "_Replaced/A/1.mp3"
    assert contents(lib.paths.music) == before_music
    assert journal(lib) == before_journal


def test_a_replace_batch_is_undone_exactly(lib: Library) -> None:
    """Supersede the old file and its lyrics; commit the new one with new lyrics and a
    cover. Undo puts Music/ back exactly as it was."""
    old = put(music(lib), b"old rip")
    put(old.with_suffix(".lrc"), b"[00:01.00]Old lyrics\n")
    before = contents(lib.paths.music)
    with fileops.batch(lib, "demo") as b:
        fileops.supersede(b, old)
        new = fileops.commit(b, staged(b, "dl.m4a", b"official"), TRACK)
        fileops.write_sidecar(b, new, ".lrc", LYRICS)
        fileops.write_sidecar(b, new, "cover.jpg", b"\xff\xd8cover")
    assert contents(lib.paths.music) != before

    result = fileops.undo(lib, b.batch_id)
    assert all(s.status == "done" for s in result.steps)
    assert contents(lib.paths.music) == before
    assert contents(lib.paths.replaced) == {
        "Artist/Album (2020)/01 Song (2).lrc": LYRICS,
        "Artist/Album (2020)/01 Song (2).m4a": b"official",
        "Artist/Album (2020)/cover.jpg": b"\xff\xd8cover",
    }


def test_undo_is_a_journaled_batch_of_its_own(lib: Library, rips: list[Path]) -> None:
    batch_id = adopt_all(lib, rips)
    result = fileops.undo(lib, batch_id)
    assert result.undo_batch_id is not None
    start = lines_of(lib, "batch_start")[-1]
    assert (start["batch_id"], start["kind"], start["undo_of"]) == (
        result.undo_batch_id,
        "undo",
        batch_id,
    )
    undo_intents = [i for i in lines_of(lib, "intent") if i["batch_id"] == result.undo_batch_id]
    assert [i["undoes"] for i in undo_intents] == [3, 2, 1]
    listed = {b.batch_id: b for b in fileops.list_batches(lib)}
    assert listed[batch_id].undone_by == [result.undo_batch_id]
    assert listed[result.undo_batch_id].undo_of == batch_id


def test_undoing_twice_does_nothing_more(lib: Library, rips: list[Path]) -> None:
    batch_id = adopt_all(lib, rips)
    first = fileops.undo(lib, batch_id)
    starts = len(lines_of(lib, "batch_start"))
    again = fileops.undo(lib, batch_id)
    assert again.undo_batch_id is None
    assert {s.status for s in again.steps} == {"skipped"}
    assert all(f"Already undone (batch {first.undo_batch_id})" in s.note for s in again.steps)
    assert len(lines_of(lib, "batch_start")) == starts


def test_an_undo_can_be_undone(lib: Library, rips: list[Path]) -> None:
    batch_id = adopt_all(lib, rips[:1])
    undo = fileops.undo(lib, batch_id)
    assert undo.undo_batch_id is not None
    fileops.undo(lib, undo.undo_batch_id)
    assert files_in(lib.paths.music) == ["Unknown Artist/Unsorted/Song 1.mp3"]
    assert tree(lib.paths.replaced) == []


def test_undo_skips_files_that_are_gone(lib: Library, rips: list[Path]) -> None:
    batch_id = adopt_all(lib, rips[:2])
    music(lib, "Unknown Artist/Unsorted/Song 2.mp3").unlink()
    result = fileops.undo(lib, batch_id)
    assert [s.status for s in result.steps] == ["skipped", "done"]
    assert "no longer there" in result.steps[0].note
    assert files_in(lib.paths.replaced) == ["Unknown Artist/Unsorted/Song 1.mp3"]


def test_undo_stops_at_a_problem_and_carries_on_later(
    lib: Library, rips: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    batch_id = adopt_all(lib, rips)
    real, calls = fileops._replace, []

    def second_one_in_use(src: Path, dst: Path) -> None:
        calls.append(src)
        if len(calls) == 2:
            raise FileInUseError("That file is open in another app; close it and try again.")
        real(src, dst)

    with monkeypatch.context() as m:
        m.setattr(fileops, "_replace", second_one_in_use)
        with pytest.raises(UndoError, match="carries on where it stopped"):
            fileops.undo(lib, batch_id)
    assert len(files_in(lib.paths.music)) == 2
    assert len(files_in(lib.paths.replaced)) == 1

    result = fileops.undo(lib, batch_id)
    assert [s.status for s in result.steps] == ["skipped", "done", "done"]
    assert tree(lib.paths.music) == []
    assert len(files_in(lib.paths.replaced)) == 3


class FakeJobs:
    def __init__(self, queued: int, running: int) -> None:
        self.queued, self.running_now = queued, running
        self.cancelled: list[str] = []

    def cancel_queued(self, batch_id: str) -> int:
        self.cancelled.append(batch_id)
        count, self.queued = self.queued, 0
        return count

    def running(self, batch_id: str) -> int:
        return self.running_now


def test_undo_cancels_queued_jobs_and_waits_for_a_running_one(
    lib: Library, rips: list[Path]
) -> None:
    batch_id = adopt_all(lib, rips[:1])
    jobs = FakeJobs(queued=3, running=1)
    fileops.undo(lib, batch_id, dry_run=True, jobs=jobs)
    assert jobs.cancelled == []  # a dry run changes nothing, the queue included

    with pytest.raises(UndoError, match="running right now"):
        fileops.undo(lib, batch_id, jobs=jobs)
    assert jobs.cancelled == [batch_id]
    assert len(files_in(lib.paths.music)) == 1  # nothing undone yet

    jobs.running_now = 0
    result = fileops.undo(lib, batch_id, jobs=jobs)
    assert result.steps[0].status == "done"
    assert tree(lib.paths.music) == []


def test_undo_reports_how_many_jobs_it_cancelled(lib: Library, rips: list[Path]) -> None:
    batch_id = adopt_all(lib, rips[:1])
    result = fileops.undo(lib, batch_id, jobs=FakeJobs(queued=4, running=0))
    assert result.cancelled_jobs == 4
    assert result.to_dict()["cancelled_jobs"] == 4


def test_undo_closes_an_open_batch(lib: Library, rips: list[Path]) -> None:
    b = fileops.open_batch(lib, "adopt")
    fileops.copy_in(b, rips[0], "A/1.mp3")
    result = fileops.undo(lib, b.batch_id)
    record = fileops.read_journal(lib)[b.batch_id]
    assert record.status == "closed"
    assert record.end is not None
    assert record.end["closed_by"] == result.undo_batch_id


def test_a_running_batch_cant_be_undone(lib: Library, rips: list[Path]) -> None:
    with fileops.batch(lib, "demo") as b:
        fileops.copy_in(b, rips[0], "A/1.mp3")
        with pytest.raises(UndoError, match="still running"):
            fileops.undo(lib, b.batch_id)


def test_an_interrupted_operation_is_left_for_a_person(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = put(music(lib), b"song")
    with monkeypatch.context() as m:
        crash_in(m, "_replace")  # reserved, then died before the move
        with pytest.raises(SimulatedCrash), fileops.batch(lib, "demo") as b:
            fileops.move(b, original, "Elsewhere/Song.m4a")
    reserved = music(lib, "Elsewhere/Song.m4a")
    reserved.write_bytes(b"something else arrived")  # now nothing adds up
    original.unlink()
    assert recover(lib) == []  # recovery can't tell, so it leaves it
    result = fileops.undo(lib, b.batch_id)
    assert [s.status for s in result.steps] == ["skipped"]
    assert "Check this file by hand" in result.steps[0].note


def test_undo_to_dict_matches_the_rpc_shape(lib: Library, rips: list[Path]) -> None:
    batch_id = adopt_all(lib, rips[:1])
    data = fileops.undo(lib, batch_id, dry_run=True).to_dict()
    assert set(data) == {"batch_id", "dry_run", "undo_batch_id", "cancelled_jobs", "operations"}
    assert set(data["operations"][0]) == {
        "op_id",
        "op",
        "action",
        "path",
        "to",
        "status",
        "note",
    }
