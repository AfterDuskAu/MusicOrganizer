"""fileops.write_tags: the verified tag write (contract 6.7), its journal, crash recovery
and exact undo, the cover kept for undo, and the MUSICORG_ID rule."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fileops_support import (
    SimulatedCrash,
    crash_in,
    files_in,
    image,
    lines_of,
    place,
    recover,
    staged,
)
from mutagen.mp4 import MP4

from musicorg import fileops, tags
from musicorg.errors import FileInUseError, IntegrityError, SourceChangedError, UserError
from musicorg.library import Library
from musicorg.tags import REMOVE, TrackTags

REL = "Artist/Album (2021)/01 Song"


@pytest.fixture
def track(lib: Library, samples: dict[str, Path]) -> Callable[[str], Path]:
    """A sample file of the given kind in the library's Music folder."""

    def make(kind: str = "m4a") -> Path:
        return place(samples[kind], lib.paths.music / "Artist" / "Album (2021)" / f"01 Song.{kind}")

    return make


def retag(lib: Library, path: Path, changes: TrackTags) -> str:
    with fileops.batch(lib, "demo") as b:
        assert fileops.write_tags(b, path, changes)
    return b.batch_id


def patch_tag_write(monkeypatch: pytest.MonkeyPatch, after_write: Callable[[Path], Any]) -> None:
    """Make the tag write on the staged copy do something more afterwards."""
    real = tags.write_tags

    def write(path: Path, changes: TrackTags) -> None:
        real(path, changes)
        after_write(path)

    monkeypatch.setattr(tags, "write_tags", write)


@pytest.mark.parametrize("kind", ["m4a", "mp3", "flac"])
def test_a_verified_write(lib: Library, track: Callable[[str], Path], kind: str) -> None:
    path = track(kind)
    audio = tags.audio_hash(path)
    retag(lib, path, TrackTags(title="Song", lyrics="la la", cover=image()))
    got = tags.read_tags(path)
    assert (got.title, got.lyrics, got.cover) == ("Song", "la la", image())
    assert tags.audio_hash(path) == audio
    [intent] = lines_of(lib, "intent")
    assert intent["op"] == "write_tags"
    assert intent["audio_md5"] == audio
    assert intent["after"]["title"] == "Song"
    assert "title" not in intent["before"]
    assert files_in(lib.paths.staging) == []


def test_nothing_to_change(lib: Library, track: Callable[[str], Path]) -> None:
    path = track()
    retag(lib, path, TrackTags(title="Song"))
    with fileops.batch(lib, "demo") as b:
        assert not fileops.write_tags(b, path, TrackTags(title="Song"))
    assert len(lines_of(lib, "intent")) == 1


def test_a_write_that_changes_the_audio_is_refused(
    lib: Library, track: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch, audio: Any
) -> None:
    path = track()
    before = path.read_bytes()
    patch_tag_write(monkeypatch, lambda staged: shutil.copyfile(audio.melody_b_m4a, staged))
    with pytest.raises(IntegrityError, match="would have changed its audio"):
        retag(lib, path, TrackTags(title="Song"))
    assert path.read_bytes() == before
    assert files_in(lib.paths.staging) == []
    assert lines_of(lib, "intent") == []


def test_tags_that_dont_read_back_are_refused(
    lib: Library, track: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = track()
    before = path.read_bytes()
    real = tags.write_tags
    monkeypatch.setattr(tags, "write_tags", lambda p, changes: real(p, TrackTags(title="Other")))
    with pytest.raises(IntegrityError, match="didn't read back"):
        retag(lib, path, TrackTags(title="Song"))
    assert path.read_bytes() == before
    assert files_in(lib.paths.staging) == []


def test_a_file_that_changes_meanwhile_is_left_alone(
    lib: Library, track: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = track()

    def touch_original(staged: Path) -> None:
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 5_000_000_000))

    patch_tag_write(monkeypatch, touch_original)
    with pytest.raises(SourceChangedError, match="changed while its tags were being written"):
        retag(lib, path, TrackTags(title="Song"))
    assert tags.read_tags(path).title is None
    assert files_in(lib.paths.staging) == []
    assert len(lines_of(lib, "failed")) == 1


def test_a_crash_before_the_intent_leaves_only_scratch(
    lib: Library, track: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = track()
    before = path.read_bytes()

    def crash(p: Path, changes: TrackTags) -> None:
        raise SimulatedCrash("mid tag write")

    monkeypatch.setattr(tags, "write_tags", crash)
    with pytest.raises(SimulatedCrash), fileops.batch(lib, "demo") as b:
        fileops.write_tags(b, path, TrackTags(title="Song"))
    assert path.read_bytes() == before
    assert lines_of(lib, "intent") == []
    assert recover(lib) == []
    assert len(files_in(lib.paths.staging)) == 1  # scratch, which clean_staging removes
    assert fileops.clean_staging(lib, older_than_hours=0) == 1


@pytest.mark.parametrize(("after", "decision", "title"), [
    (False, "rolled_back", None),
    (True, "completed", "Song"),
])  # fmt: skip
def test_a_crash_around_the_swap(
    lib: Library,
    track: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
    after: bool,
    decision: str,
    title: str | None,
) -> None:
    path = track()
    with monkeypatch.context() as m:
        crash_in(m, "_replace", after=after)
        with pytest.raises(SimulatedCrash), fileops.batch(lib, "demo") as b:
            fileops.write_tags(b, path, TrackTags(title="Song"))
    assert [d.decision for d in recover(lib)] == [decision]
    assert tags.read_tags(path).title == title
    assert files_in(lib.paths.staging) == []


@pytest.mark.parametrize("kind", ["m4a", "mp3"])
def test_undo_removes_added_lyrics_and_cover(
    lib: Library, track: Callable[[str], Path], kind: str
) -> None:
    path = track(kind)
    audio = tags.audio_hash(path)
    assert (tags.read_tags(path).lyrics, tags.read_tags(path).cover) == (None, None)
    batch_id = retag(lib, path, TrackTags(lyrics="la la la", cover=image()))
    fileops.undo(lib, batch_id)
    got = tags.read_tags(path)
    assert (got.lyrics, got.cover, got.cover_mime) == (None, None, None)
    assert tags.audio_hash(path) == audio


def test_undo_puts_back_a_replaced_cover(lib: Library, track: Callable[[str], Path]) -> None:
    path = track()
    old, new = image("PNG", (0, 0, 255)), image("JPEG", (255, 0, 0))
    retag(lib, path, TrackTags(cover=old, title="Old"))
    batch_id = retag(lib, path, TrackTags(cover=new, title="New"))
    kept = list(lib.paths.undo_art.iterdir())
    assert [p.suffix for p in kept] == [".png"]
    assert kept[0].read_bytes() == old
    fileops.undo(lib, batch_id)
    got = tags.read_tags(path)
    assert (got.cover, got.cover_mime, got.title) == (old, "image/png", "Old")


def test_undo_leaves_fields_changed_again_later(lib: Library, track: Callable[[str], Path]) -> None:
    path = track()
    first = retag(lib, path, TrackTags(title="New", album="B"))
    retag(lib, path, TrackTags(album="C"))
    result = fileops.undo(lib, first)
    got = tags.read_tags(path)
    assert (got.title, got.album) == (None, "C")
    assert "album" in result.steps[0].note


def test_undo_restores_every_field_exactly(lib: Library, track: Callable[[str], Path]) -> None:
    path = track("mp3")
    retag(lib, path, TrackTags(title="Old", artist="A", track=1, track_total=9, genre="Pop"))
    before = tags.read_tags(path)
    batch_id = retag(
        lib,
        path,
        TrackTags(title="New", artist=REMOVE, track=2, lyrics="la", explicit=True, only_copy=True),
    )
    fileops.undo(lib, batch_id)
    assert tags.read_tags(path) == before


def test_the_track_id_never_changes(lib: Library, track: Callable[[str], Path]) -> None:
    path = track()
    first_id = tags.new_track_id()
    added = retag(lib, path, TrackTags(musicorg_id=first_id, title="Song"))
    before = path.read_bytes()
    for change in (TrackTags(musicorg_id=tags.new_track_id()), TrackTags(musicorg_id=REMOVE)):
        with pytest.raises(UserError, match="id never changes"):
            retag(lib, path, change)
    assert path.read_bytes() == before

    undo = fileops.undo(lib, added)  # the undo of the batch that added it may remove it
    assert tags.read_tags(path).musicorg_id is None
    assert undo.undo_batch_id is not None
    fileops.undo(lib, undo.undo_batch_id)
    assert tags.read_tags(path).musicorg_id == first_id


def test_a_staged_file_can_be_tagged_before_it_is_committed(
    lib: Library, samples: dict[str, Path]
) -> None:
    with fileops.batch(lib, "demo") as b:
        download = staged(b, "dl.m4a", samples["m4a"].read_bytes())
        fileops.write_tags(b, download, TrackTags(title="Song"))
        landed = fileops.commit(b, download, f"{REL}.m4a")
    assert tags.read_tags(landed).title == "Song"
    fileops.undo(lib, b.batch_id)  # the tag write's file has moved on: skipped
    assert files_in(lib.paths.music) == []


def test_files_whose_tags_arent_written(
    lib: Library, track: Callable[[str], Path], samples: dict[str, Path]
) -> None:
    webm = place(samples["webm"], lib.paths.music / "A" / "x.webm")
    with fileops.batch(lib, "demo") as b, pytest.raises(UserError, match="doesn't write tags"):
        fileops.write_tags(b, webm, TrackTags(title="x"))
    assert lines_of(lib, "intent") == []


def test_mp4_tags_stay_readable_by_mutagen(lib: Library, track: Callable[[str], Path]) -> None:
    path = track()
    retag(lib, path, TrackTags(title="Song", source="youtube_music", schema=1))
    atoms = MP4(path).tags
    assert atoms is not None
    assert atoms["©nam"] == ["Song"]
    assert atoms["----:com.apple.iTunes:MUSICORG_SOURCE"] == [b"youtube_music"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows locks open files")
def test_windows_a_file_open_in_another_app(
    lib: Library, track: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = track()
    before = path.read_bytes()
    monkeypatch.setattr(fileops, "_sleep", lambda seconds: None)
    with open(path, "rb"):  # another app has it open
        with pytest.raises(FileInUseError, match="open in another app"):
            retag(lib, path, TrackTags(title="Song"))
    assert path.read_bytes() == before
    assert files_in(lib.paths.staging) == []
