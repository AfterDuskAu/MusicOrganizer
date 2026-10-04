"""browse (v0.2): the library's tracks as the app shows them, and a track's lyrics."""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from test_rpc import code, out, result, server  # noqa: F401  (out and server are fixtures)

from musicorg import browse, fileops, rpc, scan, tags
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
        "genre": "Rock", "explicit": True, "match": "auto_details", "acquired": None,
        "format": "mp3",
        "bitrate_kbps": 320, "embedded_cover": False, "only_copy": False,
        "source": None, "source_id": None, "video": False, "height": None,
        "cover": "Music/Band/Album (2020)/cover.jpg", "lyrics": "synced",
    }  # fmt: skip
    other = found[OTHER]
    assert (other["lyrics"], other["explicit"], other["year"]) == ("plain", False, None)
    assert other["cover"] == song["cover"]  # one cover.jpg per album folder


def test_a_saved_video_is_marked_as_one(filled: Library, video_mp4: Path) -> None:
    rel = "Music/Videos/Band/Song.mp4"
    video = filled.root.joinpath(*rel.split("/"))
    video.parent.mkdir(parents=True)
    shutil.copyfile(video_mp4, video)
    details = tags.TrackTags(title="Song", artist="Band", musicorg_id="t_v",
                             source="youtube_music", source_id="videoVVVVVV")  # fmt: skip
    tags.write_tags(video, details)
    (video.parent / "cover.jpg").write_bytes(b"\xff\xd8stray")  # not a video's cover
    with open_index(filled.paths, write=True) as index:
        scan.scan_library(filled, index)
        by_path = {t["path"]: t for t in browse.tracks(filled, index)}
        again = {t["path"]: t for t in browse.tracks(filled, index)}  # from the index now
    for found in (by_path, again):
        assert (found[rel]["video"], found[rel]["height"], found[rel]["cover"]) == (True, 240, None)
        assert (found[SONG]["video"], found[SONG]["height"]) == (False, None)
        assert found[SONG]["cover"] == "Music/Band/Album (2020)/cover.jpg"
    assert by_path[rel]["source_id"] == "videoVVVVVV" and by_path[rel]["duration_s"] > 19


def test_a_songs_version_comes_from_its_tag_and_its_title(
    lib: Library, samples: dict[str, Path]
) -> None:
    waiting = {"source": "rip_copy", "match": "unconfirmed"}  # a copy not identified yet
    song = add(lib, samples, SONG, title="Song R", musicorg_id="t_1", version=["remix"],
               origin_path="/somewhere/rips/Song R.mp3", **waiting)  # fmt: skip
    assert browse.version_tokens(lib, SONG) == ("remix",)  # tag and title agree: said once
    # The title alone says it, read as the owner writes a remix. (A title they typed
    # before the tag was kept in step with it has no tag.)
    tags.write_tags(song, tags.TrackTags(version=tags.REMOVE, origin_path="/rips/Song.mp3"))
    assert browse.version_tokens(lib, SONG) == ("remix",)
    tags.write_tags(song, tags.TrackTags(title="Song (Somebody Remix) (slowed)"))
    assert browse.version_tokens(lib, SONG) == ("remix:somebody", "slowed")
    # The tag says more than the title does: both count.
    tags.write_tags(song, tags.TrackTags(title="Song", version=["live:wembley"]))
    assert browse.version_tokens(lib, SONG) == ("live:wembley",)
    add(lib, samples, OTHER, title="Other")  # no version tag, and no rip behind it
    assert browse.version_tokens(lib, OTHER) == ()
    with pytest.raises(OutsideLibraryError):
        browse.version_tokens(lib, "../elsewhere.mp3")


def test_only_an_unconfirmed_copy_with_no_version_tag_is_read_by_its_rips_name(
    lib: Library, samples: dict[str, Path]
) -> None:
    """A copy made before 2026-10-04 carries the plain title while its rip is called
    "Song R": until `plan tidy` gives it its name back, the rip's name still counts.
    Once a song is found, or the owner has named it, the rip's old name says nothing."""
    rip = "/somewhere/rips/Song R.mp3"
    song = add(lib, samples, SONG, title="Song", musicorg_id="t_1", source="rip_copy",
               match="unconfirmed", origin_path=rip)  # fmt: skip
    assert browse.version_tokens(lib, SONG) == ("remix",)
    tags.write_tags(song, tags.TrackTags(origin_path="C:\\Rips\\Band - Song (Somebody Remix).mp3"))
    assert browse.version_tokens(lib, SONG) == ("remix:somebody",)
    # It has a version tag: that's what it is, whatever the rip was called.
    tags.write_tags(song, tags.TrackTags(version=["live"], origin_path=rip))
    assert browse.version_tokens(lib, SONG) == ("live",)
    # The same when its title names its version (a copy that kept its rip's own title).
    tags.write_tags(song, tags.TrackTags(title="Song (Acoustic)", version=tags.REMOVE))
    assert browse.version_tokens(lib, SONG) == ("acoustic",)
    tags.write_tags(song, tags.TrackTags(title="Song"))
    assert browse.version_tokens(lib, SONG) == ("remix",)
    # Found: the owner chose the plain official track for the rip "Song R".
    for match in ("auto_details", "user_details", "auto_exact", "user_confirmed"):
        tags.write_tags(song, tags.TrackTags(match=match))
        assert browse.version_tokens(lib, SONG) == (), match
    # Kept as an only copy: with the owner's own names, or with no match tag at all.
    tags.write_tags(song, tags.TrackTags(match="manual"))
    assert browse.version_tokens(lib, SONG) == ()
    tags.write_tags(song, tags.TrackTags(match=tags.REMOVE, only_copy=True))
    assert browse.version_tokens(lib, SONG) == ()


def test_only_a_title_the_owner_named_is_read_for_their_mark(
    lib: Library, samples: dict[str, Path]
) -> None:
    """ "Lost Boy R" is the owner's remix of "Lost Boy". An official title that happens
    to end in " R" is just that title: YouTube Music never writes the owner's mark."""
    song = add(lib, samples, SONG, title="Vitamin R", musicorg_id="t_1", source="rip_copy")
    for match in ("unconfirmed", "manual", tags.REMOVE):
        tags.write_tags(song, tags.TrackTags(match=match))
        assert browse.version_tokens(lib, SONG) == ("remix",), match
    for match in ("auto_details", "user_details"):
        tags.write_tags(song, tags.TrackTags(match=match))
        assert browse.version_tokens(lib, SONG) == (), match
    tags.write_tags(song, tags.TrackTags(match="auto_exact", source="youtube_music"))
    assert browse.version_tokens(lib, SONG) == ()
    tags.write_tags(song, tags.TrackTags(match=tags.REMOVE))  # a download: no rip behind it
    assert browse.version_tokens(lib, SONG) == ()
    # An official title's own version words are read as ever.
    tags.write_tags(song, tags.TrackTags(title="Vitamin R (Somebody Remix)"))
    assert browse.version_tokens(lib, SONG) == ("remix:somebody",)
    assert browse.owner_named("rip_copy", None) and browse.owner_named("rip_copy", "manual")
    assert not browse.owner_named("youtube_music", None)
    assert not browse.owner_named(None, None)  # not a file the engine made


def test_a_title_typed_in_edit_details_isnt_read_past_to_the_rips_name(
    lib: Library, samples: dict[str, Path]
) -> None:
    """The owner takes the R off "Song R" in Edit Details: it isn't a remix. That leaves
    just what a copy made before 2026-10-04 looks like (a plain title, no version tag,
    a rip called "Song R"). The journal remembers who wrote the title, and a title the
    owner typed is the last word."""
    waiting = {"source": "rip_copy", "match": "unconfirmed"}
    song = add(lib, samples, SONG, title="Song", musicorg_id="t_1",
               origin_path="/somewhere/rips/Song R.mp3", **waiting)  # fmt: skip
    assert browse.typed_titles(lib) == {}
    # The engine's own title, not repaired yet: the rip's name still counts.
    assert browse.version_tokens(lib, SONG) == ("remix",)

    # A title the engine writes (any batch that isn't an edit) isn't the owner's.
    with fileops.batch(lib, "tidy") as b:
        fileops.write_tags(b, song, tags.TrackTags(title="Song R", version=["remix"]))
    assert browse.typed_titles(lib) == {}

    with fileops.batch(lib, "edit") as b:  # Edit Details: the R comes off, and its tag
        fileops.write_tags(b, song, tags.TrackTags(title="Song", version=tags.REMOVE))
    assert browse.typed_titles(lib) == {"t_1": {"Song"}}
    assert browse.version_tokens(lib, SONG) == ()
    assert browse.typed_by_owner(browse.typed_titles(lib), "t_1", "Song")
    assert not browse.typed_by_owner(browse.typed_titles(lib), "t_2", "Song")

    # An edit that is undone was never the owner's last word. (An edit that changes
    # another field types no title.)
    with fileops.batch(lib, "edit") as b:
        fileops.write_tags(b, song, tags.TrackTags(genre="Rock"))
    with fileops.batch(lib, "edit") as undone:
        fileops.write_tags(undone, song, tags.TrackTags(title="Something Else"))
    assert browse.typed_titles(lib) == {"t_1": {"Song", "Something Else"}}
    fileops.undo(lib, undone.batch_id)
    assert tags.read_tags(song).title == "Song"
    assert browse.typed_titles(lib) == {"t_1": {"Song"}}


def test_the_video_lookup_reads_a_library_songs_version_from_its_file(
    server: rpc.Server,  # noqa: F811
    lib: Library,
    samples: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`youtube.video` with a `path`, with nothing stubbed in between: the versions the
    video search is given are the ones the file says, by the one rule."""
    from musicorg import youtube

    asked: list[tuple[str, ...]] = []

    def find_nothing(title: str, artist: str, **kw: object) -> None:
        asked.append(kw["versions"])  # type: ignore[arg-type]

    monkeypatch.setattr(youtube, "find_video", find_nothing)
    song = add(lib, samples, SONG, title="Song R", musicorg_id="t_1", version=["remix"],
               source="rip_copy", match="unconfirmed", origin_path="/rips/Song R.mp3")  # fmt: skip
    lib.close()
    result(server, "engine.hello", client="pytest", client_version="1")
    result(server, "library.open", root=str(lib.root))

    def versions_asked() -> tuple[str, ...]:
        found = result(server, "youtube.video", title="Song", artist="Band", path=SONG)
        assert found == {"found": False}
        return asked[-1]

    assert versions_asked() == ("remix",)
    # A copy made before 2026-10-04, not repaired yet: its rip's name still says remix.
    tags.write_tags(song, tags.TrackTags(title="Song", version=tags.REMOVE))
    assert versions_asked() == ("remix",)
    # Found: the owner chose the plain official track. The rip's old name says nothing.
    tags.write_tags(song, tags.TrackTags(match="user_details"))
    assert versions_asked() == ()


def test_a_tracks_match_comes_from_what_its_tags_said(lib: Library) -> None:
    """The index's `match` column is empty on rows written before it existed, whatever
    their tags say; the details read off the tags hold the truth."""
    assert browse.row_match({"match": "unconfirmed", "details_json": None}) == "unconfirmed"
    stale = {"match": None, "details_json": '{"v": 2, "match": "auto_details"}'}
    assert browse.row_match(stale) == "auto_details"
    assert browse.row_match({"match": None, "details_json": '{"genre": "Rock"}'}) is None
    assert browse.row_match({"match": "manual", "details_json": "not json"}) == "manual"
    assert browse.row_match({"match": "manual", "details_json": '{"match": null}'}) is None
    assert browse.row_match({}) is None


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


@pytest.mark.parametrize("version", [1, 2])
def test_an_older_index_gains_its_columns_in_place(filled: Library, version: int) -> None:
    """Versions 1 and 2 must not need a rebuild: that would throw away the matcher's work."""
    with closing(sqlite3.connect(filled.paths.index_file)) as conn, conn:
        conn.execute("ALTER TABLE library_tracks DROP COLUMN match")
        if version == 1:
            conn.execute("ALTER TABLE library_tracks DROP COLUMN details_json")
        conn.execute(f"PRAGMA user_version = {version}")
    assert list(listed(filled)) == [SONG, OTHER]
    with closing(sqlite3.connect(filled.paths.index_file)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone() == (3,)
        assert conn.execute("SELECT match FROM library_tracks").fetchall()


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
