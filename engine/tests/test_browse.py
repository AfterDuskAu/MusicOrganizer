"""browse (v0.2): the library's tracks as the app shows them, and a track's lyrics."""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from test_rpc import code, out, result, server  # noqa: F401  (out and server are fixtures)

from musicorg import browse, rpc, scan, tags
from musicorg.errors import NotFoundError, OutsideLibraryError
from musicorg.index import open_index
from musicorg.library import Library

SONG = "Music/Band/Album (2020)/01 Song.m4a"
OTHER = "Music/Band/Album (2020)/02 Other.mp3"


def add(lib: Library, samples: dict[str, Path], rel: str, **details: object) -> Path:
    path = lib.root.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(samples[path.suffix[1:]], path)
    tags.write_tags(path, tags.TrackTags(**details))  # type: ignore[arg-type]
    return path


@pytest.fixture
def filled(lib: Library, samples: dict[str, Path]) -> Library:
    song = add(lib, samples, SONG, title="Song", artist="Band", album_artist="Band",
               album="Album", year=2020, track=1, disc=1, genre="Rock", explicit=True,
               musicorg_id="t_1", match="auto_details", source_format="mp3",
               source_bitrate=320)  # fmt: skip
    song.with_suffix(".lrc").write_text("[00:01.00]Made-up line\n", encoding="utf-8")
    (song.parent / "cover.jpg").write_bytes(b"\xff\xd8cover")
    add(lib, samples, OTHER, title="Other", artist="Band feat. Guest", album="Album",
        lyrics="Made-up words")  # fmt: skip
    with open_index(lib.paths, write=True) as index:
        scan.scan_library(lib, index)
    return lib


def listed(lib: Library) -> dict[str, dict]:
    with open_index(lib.paths, write=True) as index:
        return {t["path"]: t for t in browse.tracks(lib, index)}


def test_tracks_carry_what_a_screen_needs(filled: Library) -> None:
    found = listed(filled)
    assert list(found) == [SONG, OTHER]
    song = found[SONG]
    assert song["duration_s"] == pytest.approx(3, abs=0.5)
    del song["duration_s"]
    assert song == {
        "track_id": "t_1", "path": SONG, "title": "Song", "artist": "Band",
        "album_artist": "Band", "album": "Album", "year": 2020, "track": 1, "disc": 1,
        "genre": "Rock", "explicit": True, "match": "auto_details", "format": "mp3",
        "bitrate_kbps": 320, "embedded_cover": False, "only_copy": False,
        "cover": "Music/Band/Album (2020)/cover.jpg", "lyrics": "synced",
    }  # fmt: skip
    other = found[OTHER]
    assert (other["lyrics"], other["explicit"], other["year"]) == ("plain", False, None)
    assert other["cover"] == song["cover"]  # one cover.jpg per album folder


def test_details_are_read_once_then_come_from_the_index(
    filled: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = listed(filled)
    monkeypatch.setattr(tags, "read_tags", lambda path: pytest.fail("read the file again"))
    assert listed(filled) == first


def test_a_changed_file_is_read_again(filled: Library) -> None:
    listed(filled)
    tags.write_tags(filled.root.joinpath(*OTHER.split("/")), tags.TrackTags(title="Renamed"))
    assert listed(filled)[OTHER]["title"] == "Renamed"


def test_a_missing_file_is_left_out_and_a_bare_one_uses_its_name(
    filled: Library, samples: dict[str, Path]
) -> None:
    filled.root.joinpath(*OTHER.split("/")).unlink()
    bare = filled.paths.music / "Unknown Artist" / "Unsorted" / "Some Name.mp3"
    bare.parent.mkdir(parents=True)
    shutil.copyfile(samples["bare.mp3"], bare)
    with open_index(filled.paths, write=True) as index:
        scan.scan_library(filled, index)
    found = listed(filled)
    assert OTHER not in found
    track = found["Music/Unknown Artist/Unsorted/Some Name.mp3"]
    assert (track["title"], track["artist"], track["cover"], track["lyrics"]) == (
        "Some Name", None, None, "none")  # fmt: skip


def test_lyrics(filled: Library) -> None:
    assert browse.lyrics(filled, SONG) == {"synced": "[00:01.00]Made-up line\n", "plain": None}
    assert browse.lyrics(filled, OTHER) == {"synced": None, "plain": "Made-up words"}


def test_lyrics_only_for_songs_in_the_library(filled: Library, tmp_path: Path) -> None:
    with pytest.raises(NotFoundError):
        browse.lyrics(filled, "Music/Band/Nothing.mp3")
    with pytest.raises(NotFoundError):
        browse.lyrics(filled, "Music/Band/Album (2020)/cover.jpg")
    for outside in ("../outside.mp3", ".musicorg/state.json", "Music/../../x.mp3"):
        with pytest.raises(OutsideLibraryError):
            browse.lyrics(filled, outside)


def test_an_index_from_v0_1_gains_the_column_in_place(filled: Library) -> None:
    """Version 1 → 2 must not need a rebuild: that would throw away the matcher's work."""
    with closing(sqlite3.connect(filled.paths.index_file)) as conn, conn:
        conn.execute("ALTER TABLE library_tracks DROP COLUMN details_json")
        conn.execute("PRAGMA user_version = 1")
    assert list(listed(filled)) == [SONG, OTHER]
    with closing(sqlite3.connect(filled.paths.index_file)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone() == (2,)


def test_rpc_methods(server: rpc.Server, filled: Library) -> None:  # noqa: F811
    result(server, "engine.hello", client="pytest", client_version="1")
    assert code(server, "library.tracks") == rpc.USER_ERROR  # no library open yet
    filled.close()
    result(server, "library.open", root=str(filled.root))
    found = result(server, "library.tracks")
    assert found["root"] == str(filled.root)
    assert [t["path"] for t in found["tracks"]] == [SONG, OTHER]
    assert result(server, "library.lyrics", path=OTHER)["plain"] == "Made-up words"
    assert code(server, "library.lyrics", path="../x.mp3") == rpc.OUTSIDE
    assert code(server, "library.lyrics", path="Music/none.mp3") == rpc.NOT_FOUND
    assert code(server, "library.lyrics") == rpc.INVALID_PARAMS
