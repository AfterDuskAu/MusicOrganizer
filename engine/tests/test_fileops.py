"""fileops: the path guard, the journal and batches, each operation, and crash recovery.

Each operation is tested for: success, a crash between its intent and acting, a crash
between acting and `done`, a name collision, undo, and refusing a file outside the
library. "Crashes" raise SimulatedCrash, which fileops doesn't roll back in the same run,
so what's left is exactly what a dead process would leave for recovery.
"""

from __future__ import annotations

import errno
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fileops_support import (
    FakeTags,
    SimulatedCrash,
    audio_of,
    crash_in,
    crash_tag_write,
    files_in,
    journal,
    lines_of,
    put,
    recover,
    sha256,
    staged,
    symlink_or_skip,
    tagged,
    tree,
)

from musicorg import fileops, library
from musicorg.errors import (
    CrossVolumeError,
    FileInUseError,
    FileOperationError,
    IntegrityError,
    NotFoundError,
    NotImplementedYetError,
    OutsideLibraryError,
    SourceChangedError,
    UserError,
)
from musicorg.library import Library

TRACK = "Artist/Album (2020)/01 Song.m4a"


@pytest.fixture
def rip(tmp_path: Path) -> Path:
    """One of the owner's rips, outside the library."""
    return put(tmp_path / "rips" / "Artist - Song.mp3", b"ID3" + bytes(range(256)) * 400)


@pytest.fixture
def tags(monkeypatch: pytest.MonkeyPatch) -> FakeTags:
    fake = FakeTags()
    monkeypatch.setattr(fileops, "tag_access", fake)
    return fake


def music(lib: Library, rel: str = TRACK) -> Path:
    return lib.paths.music.joinpath(*rel.split("/"))


def replaced(lib: Library, rel: str = TRACK) -> Path:
    return lib.paths.replaced.joinpath(*rel.split("/"))


def library_files(lib: Library) -> list[str]:
    """The files in Music/ and _Replaced/."""
    return [f"Music/{f}" for f in files_in(lib.paths.music)] + [
        f"_Replaced/{f}" for f in files_in(lib.paths.replaced)
    ]


def decisions(lib: Library) -> list[str]:
    return [d.decision for d in recover(lib)]


# ---- the guard -------------------------------------------------------------------------


@pytest.mark.parametrize("folder", ["Music", "_Replaced", "_Staging", ".musicorg"])
def test_guard_accepts_paths_in_managed_folders(lib: Library, folder: str) -> None:
    path = lib.root / folder / "A" / "b.m4a"
    assert fileops.guard(lib.paths, path) == path


@pytest.mark.parametrize(
    "rel",
    [
        "Music",
        "_Replaced",
        "_Staging",
        ".musicorg",
        ".",
        "Reports/report.csv",
        "Music2/x.m4a",
        "Music x/y.m4a",
        "Music/../x.m4a",
        "Music/../../x.m4a",
        "_Staging/../Music",
    ],
)
def test_guard_refuses_everything_else(lib: Library, rel: str) -> None:
    with pytest.raises(OutsideLibraryError) as info:
        fileops.guard(lib.paths, lib.root / rel)
    assert "left it alone" in info.value.message


def test_guard_refuses_other_folders(lib: Library, rip: Path) -> None:
    with pytest.raises(OutsideLibraryError):
        fileops.guard(lib.paths, rip)


def test_guard_allows_only_the_folders_asked_for(lib: Library) -> None:
    path = lib.paths.replaced / "x.m4a"
    assert fileops.guard(lib.paths, path, fileops.REPLACED) == path
    with pytest.raises(OutsideLibraryError, match="Music folder"):
        fileops.guard(lib.paths, path, fileops.MUSIC)


def test_guard_follows_links(lib: Library, rip: Path) -> None:
    symlink_or_skip(lib.paths.music / "link.mp3", rip)
    symlink_or_skip(lib.paths.music / "Elsewhere", rip.parent)
    for path in (
        lib.paths.music / "link.mp3",
        lib.paths.music / "Elsewhere" / rip.name,
        lib.paths.music / "Elsewhere" / "new.m4a",
    ):
        with pytest.raises(OutsideLibraryError):
            fileops.guard(lib.paths, path)


def test_guard_refuses_a_music_folder_that_leads_elsewhere(lib: Library, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    lib.paths.music.rmdir()
    symlink_or_skip(lib.paths.music, elsewhere)
    with pytest.raises(OutsideLibraryError):
        fileops.guard(lib.paths, lib.paths.music / "x.m4a")


@pytest.mark.parametrize("operation", ["trash", "move", "supersede", "write_tags"])
def test_operations_refuse_files_outside_the_library(
    lib: Library, rip: Path, tags: FakeTags, operation: str
) -> None:
    before, info = rip.read_bytes(), rip.stat()
    with fileops.batch(lib, "demo") as b, pytest.raises(OutsideLibraryError):
        if operation == "trash":
            fileops.trash(b, rip)
        elif operation == "move":
            fileops.move(b, rip, "Artist/x.mp3")
        elif operation == "supersede":
            fileops.supersede(b, rip)
        else:
            fileops.write_tags(b, rip, {"title": "x"})
    assert rip.read_bytes() == before
    assert rip.stat().st_mtime_ns == info.st_mtime_ns
    assert lines_of(lib, "intent") == []
    assert tree(lib.paths.music) == []


def test_a_link_to_outside_is_refused_as_source_and_destination(lib: Library, rip: Path) -> None:
    link = lib.paths.music / "A" / "linked.mp3"
    link.parent.mkdir()
    symlink_or_skip(link, rip)
    symlink_or_skip(lib.paths.music / "Elsewhere", rip.parent)
    before = sorted(os.listdir(rip.parent))
    with fileops.batch(lib, "demo") as b:
        for attempt in (
            lambda: fileops.supersede(b, link),
            lambda: fileops.move(b, link, "B/x.mp3"),
            lambda: fileops.trash(b, link),
            lambda: fileops.write_sidecar(b, link, ".lrc", b"[00:01.00]la"),
            lambda: fileops.commit(b, staged(b, "dl.m4a"), "Elsewhere/new.m4a"),
            lambda: fileops.copy_in(b, rip, "Elsewhere/new.mp3"),
        ):
            with pytest.raises(OutsideLibraryError):
                attempt()
    assert sorted(os.listdir(rip.parent)) == before
    assert link.is_symlink()
    assert lines_of(lib, "intent") == []


# ---- batches and the journal -----------------------------------------------------------


def test_batches_need_the_library_open_for_writing(lib: Library) -> None:
    with library.open(lib.root, write=False) as read_only:
        lib.close()
        with pytest.raises(RuntimeError, match="open for writing"):
            with fileops.batch(read_only, "demo"):
                pass


def test_unknown_batch_kind(lib: Library) -> None:
    with pytest.raises(ValueError, match="batch kind"), fileops.batch(lib, "tidy"):
        pass


def test_a_batch_is_journaled(lib: Library, rip: Path) -> None:
    with fileops.batch(lib, "demo") as b:
        fileops.copy_in(b, rip, "Artist/Unsorted/Song.mp3")
    assert re.fullmatch(r"b_\d{8}-\d{6}-[0-9a-f]{6}", b.batch_id)
    assert [line["type"] for line in journal(lib)] == [
        "batch_start",
        "intent",
        "reserved",
        "done",
        "batch_end",
    ]
    start, end = lines_of(lib, "batch_start")[0], lines_of(lib, "batch_end")[0]
    assert start["kind"] == "demo"
    assert start["open"] is False
    assert end["summary"] == {"operations": {"copy_in": 1}, "failed": 0}
    assert {line["batch_id"] for line in journal(lib)} == {b.batch_id}
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    assert [p.name for p in lib.paths.journal.iterdir()] == [f"{today}.jsonl"]


def test_journal_paths_are_relative_to_the_library(lib: Library, rip: Path) -> None:
    with fileops.batch(lib, "demo") as b:
        fileops.copy_in(b, rip, "Artist/Unsorted/Song.mp3")
    intent = lines_of(lib, "intent")[0]
    assert intent["dst"] == "Music/Artist/Unsorted/Song.mp3"
    assert intent["new_dirs"] == ["Music/Artist", "Music/Artist/Unsorted"]
    assert intent["origin"] == str(rip)  # outside the library: absolute
    assert lines_of(lib, "done")[0]["path"] == "Music/Artist/Unsorted/Song.mp3"


def test_an_error_ends_the_batch_with_it(lib: Library) -> None:
    with pytest.raises(ValueError), fileops.batch(lib, "demo"):
        raise ValueError("something broke")
    assert "something broke" in lines_of(lib, "batch_end")[0]["error"]
    assert fileops.list_batches(lib)[0].status == "closed"


def test_the_intent_is_on_disk_before_anything_changes(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    real_sync, real_reserve = fileops._sync_file, fileops._reserve

    def sync(fd: int) -> None:
        events.append("fsync")
        real_sync(fd)

    def reserve(target: Path) -> Path:
        assert journal(lib)[-1]["type"] == "intent"
        events.append("act")
        return real_reserve(target)

    monkeypatch.setattr(fileops, "_sync_file", sync)
    monkeypatch.setattr(fileops, "_reserve", reserve)
    with fileops.batch(lib, "demo") as b:
        src = put(fileops.stage_path(b, "dl.m4a"))
        events.clear()
        fileops.commit(b, src, TRACK)
    assert events[:3] == ["fsync", "act", "fsync"]  # intent, reserve, reserved


@pytest.mark.skipif(sys.platform != "darwin", reason="F_FULLFSYNC is macOS only")
def test_macos_flushes_to_the_disk_itself(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import fcntl

    calls = []
    real = fcntl.fcntl

    def recording(fd: int, command: int, *args: object) -> object:
        calls.append(command)
        return real(fd, command, *args)

    monkeypatch.setattr(fileops, "_FULL_FSYNC", sys.platform == "darwin")
    monkeypatch.setattr(fcntl, "fcntl", recording)
    with open(tmp_path / "x", "wb") as f:
        f.write(b"line")
        fileops._sync_file(f.fileno())
    assert calls == [fcntl.F_FULLFSYNC]


def test_a_damaged_last_line_is_ignored(lib: Library, rip: Path) -> None:
    with fileops.batch(lib, "demo") as b:
        fileops.copy_in(b, rip, "A/Song.mp3")
    with open(next(lib.paths.journal.glob("*.jsonl")), "ab") as f:
        f.write(b'{"type": "intent", "batch_id": "b_2026')
    batches = fileops.read_journal(lib)
    assert list(batches) == [b.batch_id]
    assert batches[b.batch_id].ops[0].status == "done"
    assert recover(lib) == []


def test_an_open_batch_stays_open_until_closed(lib: Library, tmp_path: Path) -> None:
    first, second = put(tmp_path / "1.mp3", b"one"), put(tmp_path / "2.mp3", b"two")
    b = fileops.open_batch(lib, "adopt")
    fileops.copy_in(b, first, "A/1.mp3")
    lib.close()

    # A later process: recovery leaves the open batch alone, and a job joins it.
    with library.open(lib.root, write=True) as again:
        assert fileops.read_journal(again)[b.batch_id].status == "open"
        joined = fileops.resume_batch(again, b.batch_id)
        fileops.copy_in(joined, second, "A/2.mp3")
        assert [op.op_id for op in fileops.read_journal(again)[b.batch_id].ops] == [1, 2]

        fileops.close_batch(again, b.batch_id)
        record = fileops.read_journal(again)[b.batch_id]
        assert record.status == "closed"
        assert record.end is not None
        assert record.end["summary"] == {"operations": {"copy_in": 2}, "failed": 0}
        fileops.close_batch(again, b.batch_id)  # already closed: nothing happens
        assert len(lines_of(again, "batch_end")) == 1
        with pytest.raises(UserError, match="already ended"):
            fileops.resume_batch(again, b.batch_id)
        with pytest.raises(NotFoundError):
            fileops.resume_batch(again, "b_20200101-000000-000000")


def test_list_batches(lib: Library, rip: Path) -> None:
    with fileops.batch(lib, "demo") as first:
        fileops.copy_in(first, rip, "A/1.mp3")
        fileops.copy_in(first, rip, "A/2.mp3")
    with fileops.batch(lib, "demo") as second:
        fileops.supersede(second, music(lib, "A/1.mp3"))
    batches = fileops.list_batches(lib)
    assert [b.batch_id for b in batches] == [second.batch_id, first.batch_id]
    assert batches[1].operations == {"copy_in": 2}
    assert batches[0].operations == {"supersede": 1}
    assert batches[0].status == "closed"
    assert [b.batch_id for b in fileops.list_batches(lib, limit=1)] == [second.batch_id]


# ---- commit ----------------------------------------------------------------------------


def test_commit(lib: Library) -> None:
    with fileops.batch(lib, "demo") as b:
        src = staged(b, "dl.m4a", b"audio")
        landed = fileops.commit(b, src, TRACK)
    assert landed == music(lib)
    assert landed.read_bytes() == b"audio"
    assert not src.exists()
    assert lines_of(lib, "intent")[0]["op"] == "commit"


def test_commit_collision(lib: Library) -> None:
    put(music(lib), b"already here")
    put(music(lib, "Artist/Album (2020)/01 song (2).M4A"), b"this one too")
    with fileops.batch(lib, "demo") as b:
        landed = fileops.commit(b, staged(b, "dl.m4a", b"new"), TRACK)
    assert landed.name == "01 Song (3).m4a"  # names are compared ignoring case
    assert landed.read_bytes() == b"new"
    assert music(lib).read_bytes() == b"already here"


def test_commit_crash_before_acting(lib: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as m:
        crash_in(m, "_reserve")
        with pytest.raises(SimulatedCrash), fileops.batch(lib, "demo") as b:
            src = staged(b, "dl.m4a")
            fileops.commit(b, src, TRACK)
    assert decisions(lib) == ["rolled_back"]
    assert src.exists()  # the pipeline's download stays for a retry
    assert tree(lib.paths.music) == []
    assert fileops.read_journal(lib)[b.batch_id].status == "interrupted"


def test_commit_crash_after_reserving(lib: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as m:
        crash_in(m, "_replace")
        with pytest.raises(SimulatedCrash), fileops.batch(lib, "demo") as b:
            src = staged(b, "dl.m4a")
            fileops.commit(b, src, TRACK)
    assert music(lib).stat().st_size == 0  # the reserved name
    assert decisions(lib) == ["rolled_back"]
    assert src.exists()
    assert tree(lib.paths.music) == []  # reserved name and new folders removed


def test_commit_crash_before_done(lib: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as m:
        crash_in(m, "_replace", after=True)
        with pytest.raises(SimulatedCrash), fileops.batch(lib, "demo") as b:
            fileops.commit(b, staged(b, "dl.m4a", b"audio"), TRACK)
    assert decisions(lib) == ["completed"]
    assert music(lib).read_bytes() == b"audio"
    assert lines_of(lib, "recovered")[0]["path"] == f"Music/{TRACK}"
    op = fileops.read_journal(lib)[b.batch_id].ops[0]
    assert op.status == "done"
    fileops.undo(lib, b.batch_id)  # a recovered operation can be undone like any other
    assert replaced(lib).read_bytes() == b"audio"


def test_commit_error_rolls_back_at_once(lib: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    def in_use(src: Path, dst: Path) -> None:
        raise FileInUseError("That file is open in another app; close it and try again.")

    monkeypatch.setattr(fileops, "_replace", in_use)
    with pytest.raises(FileInUseError), fileops.batch(lib, "demo") as b:
        src = staged(b, "dl.m4a")
        fileops.commit(b, src, TRACK)
    assert src.exists()
    assert tree(lib.paths.music) == []
    assert "open in another app" in lines_of(lib, "failed")[0]["error"]
    assert lines_of(lib, "batch_end")[0]["summary"] == {"operations": {}, "failed": 1}
    assert recover(lib) == []


def test_commit_undo(lib: Library) -> None:
    with fileops.batch(lib, "demo") as b:
        fileops.commit(b, staged(b, "dl.m4a", b"audio"), TRACK)
    fileops.undo(lib, b.batch_id)
    assert tree(lib.paths.music) == []  # the folders it made are gone too
    assert replaced(lib).read_bytes() == b"audio"


def test_commit_reports_a_file_where_a_folder_goes(lib: Library) -> None:
    put(music(lib, "Artist"), b"a file named like the artist folder")
    with fileops.batch(lib, "demo") as b, pytest.raises(UserError, match="in the way"):
        fileops.commit(b, staged(b, "dl.m4a"), TRACK)
    assert files_in(lib.paths.music) == ["Artist"]


def test_commit_refuses_other_sources_and_targets(lib: Library, rip: Path, tmp_path: Path) -> None:
    in_music = put(music(lib), b"library file")
    with fileops.batch(lib, "demo") as b:
        for source in (in_music, rip):
            with pytest.raises(OutsideLibraryError):
                fileops.commit(b, source, "B/x.m4a")
        src = staged(b, "dl.m4a")
        for bad in ("../x.m4a", "A/../../x.m4a", str(tmp_path / "x.m4a"), ""):
            with pytest.raises(OutsideLibraryError):
                fileops.commit(b, src, bad)
    assert src.exists()
    assert in_music.read_bytes() == b"library file"
    assert lines_of(lib, "intent") == []


# ---- copy_in ---------------------------------------------------------------------------


def test_copy_in(lib: Library, rip: Path) -> None:
    before, info = sha256(rip), rip.stat()
    with fileops.batch(lib, "demo") as b:
        landed = fileops.copy_in(b, rip, "Artist/Unsorted/Song.mp3")
    assert landed == music(lib, "Artist/Unsorted/Song.mp3")
    assert sha256(landed) == before
    # The original is untouched.
    assert sha256(rip) == before
    assert (rip.stat().st_size, rip.stat().st_mtime_ns) == (info.st_size, info.st_mtime_ns)
    assert files_in(lib.paths.staging) == []
    assert lines_of(lib, "done")[0]["sha256"] == before


def test_copy_in_collision(lib: Library, rip: Path) -> None:
    put(music(lib, "A/Song.mp3"), b"another song")
    with fileops.batch(lib, "demo") as b:
        landed = fileops.copy_in(b, rip, "A/Song.mp3")
    assert landed.name == "Song (2).mp3"
    assert music(lib, "A/Song.mp3").read_bytes() == b"another song"


def test_copy_in_crash_before_acting(
    lib: Library, rip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as m:
        crash_in(m, "_copy_file")
        with pytest.raises(SimulatedCrash), fileops.batch(lib, "demo") as b:
            fileops.copy_in(b, rip, "A/Song.mp3")
    assert decisions(lib) == ["rolled_back"]
    assert tree(lib.paths.music) == []
    assert files_in(lib.paths.staging) == []


def test_copy_in_crash_mid_copy(lib: Library, rip: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def half_a_copy(src: Path, dest: Path) -> str:
        dest.write_bytes(src.read_bytes()[:100])
        raise SimulatedCrash("mid-copy")

    with monkeypatch.context() as m:
        m.setattr(fileops, "_copy_file", half_a_copy)
        with pytest.raises(SimulatedCrash), fileops.batch(lib, "demo") as b:
            fileops.copy_in(b, rip, "A/Song.mp3")
    assert len(files_in(lib.paths.staging)) == 1
    assert decisions(lib) == ["rolled_back"]
    assert files_in(lib.paths.staging) == []  # the partial copy is gone
    assert tree(lib.paths.music) == []


def test_copy_in_crash_before_done(
    lib: Library, rip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as m:
        crash_in(m, "_replace", after=True)
        with pytest.raises(SimulatedCrash), fileops.batch(lib, "demo") as b:
            fileops.copy_in(b, rip, "A/Song.mp3")
    assert decisions(lib) == ["completed"]
    assert sha256(music(lib, "A/Song.mp3")) == sha256(rip)


def test_copy_in_verifies_the_copy(
    lib: Library, rip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = sha256(rip)
    monkeypatch.setattr(fileops, "sha256_file", lambda path: "0" * 64)
    with pytest.raises(IntegrityError, match="didn't match"), fileops.batch(lib, "demo") as b:
        fileops.copy_in(b, rip, "A/Song.mp3")
    assert tree(lib.paths.music) == []
    assert files_in(lib.paths.staging) == []
    assert sha256(rip) == before
    assert len(lines_of(lib, "failed")) == 1


def test_copy_in_refuses_a_source_that_changes(
    lib: Library, rip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = fileops._copy_file

    def copy_then_change(src: Path, dest: Path) -> str:
        digest = real(src, dest)
        with open(src, "ab") as f:  # another app writes to it meanwhile
            f.write(b"more")
        return digest

    monkeypatch.setattr(fileops, "_copy_file", copy_then_change)
    with pytest.raises(SourceChangedError), fileops.batch(lib, "demo") as b:
        fileops.copy_in(b, rip, "A/Song.mp3")
    assert tree(lib.paths.music) == []
    assert files_in(lib.paths.staging) == []


def test_copy_in_undo(lib: Library, rip: Path) -> None:
    before = sha256(rip)
    with fileops.batch(lib, "demo") as b:
        fileops.copy_in(b, rip, "A/Song.mp3")
    fileops.undo(lib, b.batch_id)
    assert tree(lib.paths.music) == []
    assert sha256(replaced(lib, "A/Song.mp3")) == before
    assert sha256(rip) == before


def test_copy_in_refuses_odd_sources(lib: Library, tmp_path: Path) -> None:
    in_library = put(music(lib, "A/x.mp3"))
    with fileops.batch(lib, "demo") as b:
        with pytest.raises(UserError, match="already inside the library"):
            fileops.copy_in(b, in_library, "B/x.mp3")
        with pytest.raises(UserError, match="no file"):
            fileops.copy_in(b, tmp_path / "missing.mp3", "B/x.mp3")
        with pytest.raises(UserError, match="isn't a file"):
            fileops.copy_in(b, tmp_path, "B/x.mp3")
    assert lines_of(lib, "intent") == []


# ---- supersede -------------------------------------------------------------------------


def test_supersede(lib: Library) -> None:
    original = put(music(lib), b"old")
    with fileops.batch(lib, "demo") as b:
        gone_to = fileops.supersede(b, original)
    assert gone_to == replaced(lib)
    assert gone_to.read_bytes() == b"old"
    assert not original.exists()


def test_supersede_collision(lib: Library) -> None:
    put(replaced(lib), b"replaced last year")
    with fileops.batch(lib, "demo") as b:
        gone_to = fileops.supersede(b, put(music(lib), b"old"))
    assert gone_to.name == "01 Song (2).m4a"
    assert replaced(lib).read_bytes() == b"replaced last year"


@pytest.mark.parametrize(("point", "after", "decision"), [
    ("_reserve", False, "rolled_back"),
    ("_replace", False, "rolled_back"),
    ("_replace", True, "completed"),
])  # fmt: skip
def test_supersede_crash(
    lib: Library, monkeypatch: pytest.MonkeyPatch, point: str, after: bool, decision: str
) -> None:
    original = put(music(lib), b"old")
    with monkeypatch.context() as m:
        crash_in(m, point, after=after)
        with pytest.raises(SimulatedCrash), fileops.batch(lib, "demo") as b:
            fileops.supersede(b, original)
    assert decisions(lib) == [decision]
    if decision == "completed":
        assert library_files(lib) == [f"_Replaced/{TRACK}"]
    else:
        assert library_files(lib) == [f"Music/{TRACK}"]
        assert tree(lib.paths.replaced) == []


def test_supersede_undo(lib: Library) -> None:
    original = put(music(lib), b"old")
    with fileops.batch(lib, "demo") as b:
        fileops.supersede(b, original)
    fileops.undo(lib, b.batch_id)
    assert original.read_bytes() == b"old"
    assert tree(lib.paths.replaced) == []


# ---- move ------------------------------------------------------------------------------


def test_move(lib: Library) -> None:
    original = put(music(lib, "Old Artist/Song.m4a"), b"song")
    with fileops.batch(lib, "demo") as b:
        landed = fileops.move(b, original, TRACK)
    assert landed == music(lib)
    assert landed.read_bytes() == b"song"
    assert not original.exists()


def test_move_collision(lib: Library) -> None:
    put(music(lib), b"another")
    with fileops.batch(lib, "demo") as b:
        landed = fileops.move(b, put(music(lib, "Old/Song.m4a"), b"song"), TRACK)
    assert landed.name == "01 Song (2).m4a"
    assert music(lib).read_bytes() == b"another"


def test_move_onto_itself_does_nothing(lib: Library) -> None:
    original = put(music(lib), b"song")
    with fileops.batch(lib, "demo") as b:
        assert fileops.move(b, original, TRACK) == original
    assert lines_of(lib, "intent") == []


@pytest.mark.parametrize(("point", "after", "decision"), [
    ("_reserve", False, "rolled_back"),
    ("_replace", False, "rolled_back"),
    ("_replace", True, "completed"),
])  # fmt: skip
def test_move_crash(
    lib: Library, monkeypatch: pytest.MonkeyPatch, point: str, after: bool, decision: str
) -> None:
    original = put(music(lib, "Old/Song.m4a"), b"song")
    with monkeypatch.context() as m:
        crash_in(m, point, after=after)
        with pytest.raises(SimulatedCrash), fileops.batch(lib, "demo") as b:
            fileops.move(b, original, TRACK)
    assert decisions(lib) == [decision]
    expected = f"Music/{TRACK}" if decision == "completed" else "Music/Old/Song.m4a"
    assert library_files(lib) == [expected]


def test_move_undo(lib: Library) -> None:
    original = put(music(lib, "Old/Song.m4a"), b"song")
    with fileops.batch(lib, "demo") as b:
        fileops.move(b, original, TRACK)
    fileops.undo(lib, b.batch_id)
    assert tree(lib.paths.music) == ["Old/", "Old/Song.m4a"]


# ---- how moves happen ------------------------------------------------------------------


def test_a_file_that_appears_after_the_name_check_is_never_overwritten(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = put(music(lib, "A/placeholder"), b"").parent / "01 Song.m4a"
    real = fileops._taken_names

    def racing(folder: Path) -> set[str]:
        names = real(folder) - {"01 song.m4a"}  # the check doesn't see it...
        if not target.exists():
            put(target, b"theirs")  # ...because it appears right after
        return names

    monkeypatch.setattr(fileops, "_taken_names", racing)
    with fileops.batch(lib, "demo") as b:
        landed = fileops.commit(b, staged(b, "dl.m4a", b"ours"), "A/01 Song.m4a")
    assert landed.name == "01 Song (2).m4a"
    assert landed.read_bytes() == b"ours"
    assert target.read_bytes() == b"theirs"


def test_windows_retries_a_file_in_use(lib: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fileops, "_RETRY_PERMISSION_ERRORS", True)
    pauses: list[float] = []
    monkeypatch.setattr(fileops, "_sleep", pauses.append)
    real, failures = os.replace, iter([True, True])

    def flaky(src: str | Path, dst: str | Path) -> None:
        if next(failures, False):
            raise PermissionError(errno.EACCES, "The process cannot access the file")
        real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    with fileops.batch(lib, "demo") as b:
        landed = fileops.commit(b, staged(b, "dl.m4a", b"audio"), TRACK)
    assert landed.read_bytes() == b"audio"
    assert pauses == [fileops.PERMISSION_RETRY_S] * 2


def test_windows_gives_up_on_a_file_that_stays_in_use(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fileops, "_RETRY_PERMISSION_ERRORS", True)
    pauses: list[float] = []
    monkeypatch.setattr(fileops, "_sleep", pauses.append)

    def in_use(src: str | Path, dst: str | Path) -> None:
        raise PermissionError(errno.EACCES, "The process cannot access the file")

    monkeypatch.setattr(os, "replace", in_use)
    original = put(music(lib), b"song")
    with pytest.raises(FileInUseError, match="open in another app"):
        with fileops.batch(lib, "demo") as b:
            fileops.supersede(b, original)
    assert len(pauses) == fileops.PERMISSION_RETRIES
    assert sum(pauses) == pytest.approx(2.0)
    assert original.read_bytes() == b"song"
    assert tree(lib.paths.replaced) == []


def test_other_permission_errors_are_plain_english(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fileops, "_RETRY_PERMISSION_ERRORS", False)

    def denied(src: str | Path, dst: str | Path) -> None:
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(os, "replace", denied)
    original = put(music(lib), b"song")
    with pytest.raises(FileOperationError, match="Couldn't move 01 Song.m4a"):
        with fileops.batch(lib, "demo") as b:
            fileops.supersede(b, original)
    assert original.exists()


def test_moves_never_cross_drives(lib: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    def other_drive(src: str | Path, dst: str | Path) -> None:
        raise OSError(errno.EXDEV, "Cross-device link")

    monkeypatch.setattr(os, "replace", other_drive)
    with pytest.raises(CrossVolumeError, match="different drives"):
        with fileops.batch(lib, "demo") as b:
            src = staged(b, "dl.m4a")
            fileops.commit(b, src, TRACK)
    assert src.exists()
    assert tree(lib.paths.music) == []


# ---- trash -----------------------------------------------------------------------------


def test_trash(lib: Library, fake_trash) -> None:
    doomed = put(music(lib), b"song")
    with fileops.batch(lib, "demo") as b:
        fileops.trash(b, doomed)
    assert not doomed.exists()
    assert fake_trash.sent == [doomed]


def test_trash_crash_before_acting(lib: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    doomed = put(music(lib), b"song")
    with monkeypatch.context() as m:
        crash_in(m, "_send_to_trash")
        with pytest.raises(SimulatedCrash), fileops.batch(lib, "demo") as b:
            fileops.trash(b, doomed)
    assert decisions(lib) == ["rolled_back"]
    assert doomed.exists()


def test_trash_crash_before_done(lib: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    doomed = put(music(lib), b"song")
    with monkeypatch.context() as m:
        crash_in(m, "_send_to_trash", after=True)
        with pytest.raises(SimulatedCrash), fileops.batch(lib, "demo") as b:
            fileops.trash(b, doomed)
    assert decisions(lib) == ["completed"]
    assert not doomed.exists()


def test_trash_failure_leaves_the_file(lib: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(path: Path) -> None:
        raise OSError("The Trash is full")

    monkeypatch.setattr(fileops, "_send_to_trash", refuse)
    doomed = put(music(lib), b"song")
    with pytest.raises(FileOperationError, match="still in the library"):
        with fileops.batch(lib, "demo") as b:
            fileops.trash(b, doomed)
    assert doomed.exists()
    assert len(lines_of(lib, "failed")) == 1


def test_trash_takes_files_only(lib: Library) -> None:
    put(music(lib))
    with fileops.batch(lib, "demo") as b:
        with pytest.raises(UserError, match="isn't a file"):
            fileops.trash(b, music(lib, "Artist"))
        with pytest.raises(OutsideLibraryError):
            fileops.trash(b, lib.paths.music)
    assert files_in(lib.paths.music) == [TRACK]


def test_trash_undo_is_by_hand(lib: Library) -> None:
    with fileops.batch(lib, "demo") as b:
        fileops.trash(b, put(music(lib)))
    result = fileops.undo(lib, b.batch_id)
    assert [(s.status, s.path) for s in result.steps] == [("manual", f"Music/{TRACK}")]
    assert "Trash by hand" in result.steps[0].note
    assert result.undo_batch_id is None  # nothing to do, so no undo batch


# ---- write_tags (the journal side; step 04 makes the write verified) --------------------


def test_write_tags(lib: Library, tags: FakeTags) -> None:
    track = tagged(music(lib), {"title": "Old", "artist": "Someone"})
    with fileops.batch(lib, "demo") as b:
        assert fileops.write_tags(
            b, track, {"title": "New", "album": "Album", "artist": fileops.REMOVE}
        )
        assert not fileops.write_tags(b, track, {"title": "New"})  # nothing to change
    assert tags.read(track) == {"title": "New", "album": "Album"}
    [intent] = lines_of(lib, "intent")
    assert intent["before"] == {"title": "Old", "artist": "Someone"}
    assert intent["after"] == {"title": "New", "album": "Album"}


@pytest.mark.parametrize(("after", "decision"), [(False, "rolled_back"), (True, "completed")])
def test_write_tags_crash(
    lib: Library, tags: FakeTags, monkeypatch: pytest.MonkeyPatch, after: bool, decision: str
) -> None:
    track = tagged(music(lib), {"title": "Old"})
    with monkeypatch.context() as m:
        crash_tag_write(m, tags, after=after)
        with pytest.raises(SimulatedCrash), fileops.batch(lib, "demo") as b:
            fileops.write_tags(b, track, {"title": "New"})
    assert decisions(lib) == [decision]
    assert tags.read(track) == {"title": "New" if after else "Old"}


def test_write_tags_undo_restores_exactly(lib: Library, tags: FakeTags) -> None:
    track = tagged(music(lib), {"title": "Song"}, audio="the audio")
    with fileops.batch(lib, "demo") as b:
        fileops.write_tags(b, track, {"lyrics": "la la", "cover": "sha256-of-a-cover"})
    fileops.undo(lib, b.batch_id)
    assert tags.read(track) == {"title": "Song"}  # fields added by the batch are gone
    assert audio_of(track) == "the audio"


def test_write_tags_undo_keeps_later_changes(lib: Library, tags: FakeTags) -> None:
    track = tagged(music(lib), {"title": "Old", "album": "A"})
    with fileops.batch(lib, "demo") as first:
        fileops.write_tags(first, track, {"title": "New", "album": "B"})
    with fileops.batch(lib, "demo") as second:
        fileops.write_tags(second, track, {"album": "C"})
    result = fileops.undo(lib, first.batch_id)
    assert tags.read(track) == {"title": "Old", "album": "C"}
    assert "album" in result.steps[0].note


def test_write_tags_waits_for_tag_support(lib: Library) -> None:
    track = tagged(music(lib), {"title": "Song"})
    with fileops.batch(lib, "demo") as b, pytest.raises(NotImplementedYetError, match="04"):
        fileops.write_tags(b, track, {"title": "New"})
    assert lines_of(lib, "intent") == []


def test_interrupted_tag_writes_wait_for_tag_support(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    tags = FakeTags()
    track = tagged(music(lib), {"title": "Old"})
    with monkeypatch.context() as m:
        m.setattr(fileops, "tag_access", tags)
        crash_tag_write(m, tags, after=True)
        with pytest.raises(SimulatedCrash), fileops.batch(lib, "demo") as b:
            fileops.write_tags(b, track, {"title": "New"})
    assert recover(lib) == []  # can't read tags yet: left for later
    assert fileops.read_journal(lib)[b.batch_id].ops[0].status == "pending"
    monkeypatch.setattr(fileops, "tag_access", tags)
    assert decisions(lib) == ["completed"]


# ---- write_sidecar ---------------------------------------------------------------------

LYRICS = b"[00:01.00]First line\n[00:04.50]Second line\n"


def test_write_sidecar(lib: Library) -> None:
    track = put(music(lib))
    with fileops.batch(lib, "demo") as b:
        side = fileops.write_sidecar(b, track, ".lrc", LYRICS)
        cover = fileops.write_sidecar(b, track, "cover.jpg", b"\xff\xd8jpeg")
    assert side == music(lib, "Artist/Album (2020)/01 Song.lrc")
    assert side.read_bytes() == LYRICS
    assert cover == music(lib, "Artist/Album (2020)/cover.jpg")
    assert files_in(lib.paths.staging) == []


def test_an_identical_sidecar_is_left_as_it_is(lib: Library) -> None:
    track = put(music(lib))
    existing = put(track.with_suffix(".lrc"), LYRICS)
    with fileops.batch(lib, "demo") as b:
        assert fileops.write_sidecar(b, track, ".lrc", LYRICS) == existing
    assert lines_of(lib, "intent") == []


def test_a_different_lrc_is_superseded(lib: Library) -> None:
    track = put(music(lib))
    put(track.with_suffix(".lrc"), b"[00:01.00]Old lyrics\n")
    with fileops.batch(lib, "demo") as b:
        fileops.write_sidecar(b, track, ".lrc", LYRICS)
    assert track.with_suffix(".lrc").read_bytes() == LYRICS
    assert (
        replaced(lib, "Artist/Album (2020)/01 Song.lrc").read_bytes().startswith(b"[00:01.00]Old")
    )
    assert [line["op"] for line in lines_of(lib, "intent")] == ["supersede", "write_sidecar"]


def test_a_different_cover_is_left_alone(lib: Library) -> None:
    track = put(music(lib))
    cover = put(track.parent / "cover.jpg", b"the owner's choice")
    with fileops.batch(lib, "demo") as b:
        assert fileops.write_sidecar(b, track, "cover.jpg", b"\xff\xd8other") is None
    assert cover.read_bytes() == b"the owner's choice"
    assert lines_of(lib, "intent") == []


def test_a_sidecar_whose_name_is_taken_meanwhile_gets_another(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    track = put(music(lib))
    real = fileops._write_bytes_new

    def write_then_race(path: Path, data: bytes) -> None:
        real(path, data)
        put(track.with_suffix(".lrc"), b"appeared meanwhile")

    monkeypatch.setattr(fileops, "_write_bytes_new", write_then_race)
    with fileops.batch(lib, "demo") as b:
        side = fileops.write_sidecar(b, track, ".lrc", LYRICS)
    assert side is not None
    assert side.name == "01 Song (2).lrc"
    assert track.with_suffix(".lrc").read_bytes() == b"appeared meanwhile"


@pytest.mark.parametrize(("point", "after", "decision"), [
    ("_write_bytes_new", False, "rolled_back"),
    ("_reserve", False, "rolled_back"),
    ("_replace", False, "rolled_back"),
    ("_replace", True, "completed"),
])  # fmt: skip
def test_write_sidecar_crash(
    lib: Library, monkeypatch: pytest.MonkeyPatch, point: str, after: bool, decision: str
) -> None:
    track = put(music(lib))
    with monkeypatch.context() as m:
        crash_in(m, point, after=after)
        with pytest.raises(SimulatedCrash), fileops.batch(lib, "demo") as b:
            fileops.write_sidecar(b, track, ".lrc", LYRICS)
    assert decisions(lib) == [decision]
    lrc = track.with_suffix(".lrc")
    if decision == "completed":
        assert lrc.read_bytes() == LYRICS
    else:
        assert not lrc.exists()
    assert files_in(lib.paths.staging) == []


def test_write_sidecar_undo_brings_the_old_one_back(lib: Library) -> None:
    track = put(music(lib))
    lrc = put(track.with_suffix(".lrc"), b"[00:01.00]Old lyrics\n")
    with fileops.batch(lib, "demo") as b:
        fileops.write_sidecar(b, track, ".lrc", LYRICS)
    result = fileops.undo(lib, b.batch_id)
    assert [s.action for s in result.steps] == ["supersede", "restore"]
    assert lrc.read_bytes() == b"[00:01.00]Old lyrics\n"
    assert files_in(lib.paths.replaced) == ["Artist/Album (2020)/01 Song (2).lrc"]
    assert replaced(lib, "Artist/Album (2020)/01 Song (2).lrc").read_bytes() == LYRICS


def test_write_sidecar_refusals(lib: Library, rip: Path) -> None:
    track = put(music(lib))
    with fileops.batch(lib, "demo") as b:
        with pytest.raises(ValueError, match="empty"):
            fileops.write_sidecar(b, track, ".lrc", b"")
        for suffix in ("lrc", ".m4a", "../x.lrc", "folder.jpg"):
            with pytest.raises(ValueError, match="suffix"):
                fileops.write_sidecar(b, track, suffix, LYRICS)
        with pytest.raises(OutsideLibraryError):
            fileops.write_sidecar(b, rip, ".lrc", LYRICS)
    assert sorted(os.listdir(rip.parent)) == [rip.name]
    assert files_in(lib.paths.music) == [TRACK]


# ---- recovery --------------------------------------------------------------------------


def test_recovery_runs_when_the_library_is_opened(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as m:
        crash_in(m, "_replace", after=True)
        with pytest.raises(SimulatedCrash), fileops.batch(lib, "demo") as b:
            fileops.commit(b, staged(b, "dl.m4a", b"audio"), TRACK)
    lib.close()
    with library.open(lib.root, write=True) as again:
        record = fileops.read_journal(again)[b.batch_id]
        assert record.status == "interrupted"
        assert record.ops[0].status == "done"
        assert fileops.recover_journal(again.paths) == []  # nothing left the second time


CRASH_MID_MOVE = """
import os
import sys
from pathlib import Path

from musicorg import fileops, library

real_replace = fileops._replace


def replace_then_die(src, dst):
    real_replace(src, dst)
    os._exit(3)  # the process dies after the move, before journaling it as done


fileops._replace = replace_then_die
with library.open(Path(sys.argv[1]), write=True) as lib:
    with fileops.batch(lib, "demo") as b:
        fileops.copy_in(b, Path(sys.argv[2]), "Artist/Unsorted/Song.mp3")
"""


def test_a_real_crash_is_recovered_by_the_next_process(lib: Library, rip: Path) -> None:
    lib.close()
    child = subprocess.run(
        [sys.executable, "-c", CRASH_MID_MOVE, str(lib.root), str(rip)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert child.returncode == 3, child.stderr
    [record] = fileops.read_journal(lib).values()
    assert (record.status, record.ops[0].status) == ("open", "pending")

    with library.open(lib.root, write=True) as again:  # recovery runs here
        [record] = fileops.read_journal(again).values()
        assert (record.status, record.ops[0].status) == ("interrupted", "done")
        assert sha256(music(again, "Artist/Unsorted/Song.mp3")) == sha256(rip)
        fileops.undo(again, record.batch_id)
        assert tree(again.paths.music) == []


def test_recovery_removes_a_reservation_the_journal_missed(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as m:
        crash_in(m, "_reserve", after=True)  # reserved, then died before journaling it
        with pytest.raises(SimulatedCrash), fileops.batch(lib, "demo") as b:
            fileops.commit(b, staged(b, "dl.m4a"), TRACK)
    assert music(lib).exists()
    assert lines_of(lib, "reserved") == []
    assert decisions(lib) == ["rolled_back"]
    assert tree(lib.paths.music) == []


def test_recovery_never_removes_a_file_with_something_in_it(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as m:
        crash_in(m, "_replace")
        with pytest.raises(SimulatedCrash), fileops.batch(lib, "demo") as b:
            src = staged(b, "dl.m4a")
            fileops.commit(b, src, TRACK)
    music(lib).write_bytes(b"someone saved something here")
    assert decisions(lib) == ["rolled_back"]
    assert music(lib).read_bytes() == b"someone saved something here"
    assert src.exists()


def test_recovery_leaves_running_batches_alone(lib: Library) -> None:
    with fileops.batch(lib, "demo") as b:
        assert recover(lib) == []
        assert fileops.read_journal(lib)[b.batch_id].status == "open"
    assert fileops.read_journal(lib)[b.batch_id].status == "closed"


@pytest.mark.integration
def test_trash_uses_the_real_trash(lib: Library) -> None:
    """Puts one small, clearly named test file in the real Trash. Runs only with
    MUSICORG_INTEGRATION=1, never in CI."""
    doomed = put(
        lib.paths.music / "Music Organizer test" / "trash-test (safe to delete).txt",
        "A test file from Music Organizer's tests. It's safe to delete.",
    )
    with fileops.batch(lib, "demo") as b:
        fileops.trash(b, doomed)
    assert not doomed.exists()
    assert fileops.list_batches(lib)[0].operations == {"trash": 1}
