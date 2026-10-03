"""artist: the Artist page (an artist's YouTube Music page, with what the owner has).

YouTube Music's answers are recordings (fixtures/ytm/artists, artist-page, playlist,
album), made on 2026-10-03 with scripts/record_ytm.py, so nothing here reaches the
network.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_discover import IN_THE_END, NUMB, own
from test_rpc import Capture, code, opened, out, result, root, server  # noqa: F401  (fixtures)

from musicorg import artist, rpc, youtube
from musicorg.errors import ReplayMissError, UserError, YouTubeError
from musicorg.index import open_index
from musicorg.library import Library

LINKIN_PARK = "UCxgN32UVVztKAQd2HkXzBtw"
ALL_SONGS = "OLAK5uy_k7mmOSpP63ccIRmgod4PkMPARvWuYw6dA"  # recorded: its first 8 songs
FROM_ZERO = "MPREb_d1UkStdzUrN"  # an album of theirs, recorded whole: 11 songs


def info(lib: Library, **asked: Any) -> dict[str, Any]:
    with open_index(lib.paths, write=True) as index:
        return artist.info(lib, index, **asked)


# ---- finding the artist ------------------------------------------------------------------


def test_an_artist_is_found_by_name_however_its_typed() -> None:
    assert youtube.find_artist("Linkin Park") == (LINKIN_PARK, "Linkin Park")
    assert youtube.find_artist("  ") is None
    assert youtube.find_artist("Zzyzx Qwfp Band") is None  # YouTube Music found nobody


def test_a_loosely_typed_name_takes_youtube_musics_best_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    found = [
        {"artist": "JAY-Z", "browseId": "UCaaaaaaaaaaaaaaaaaaaaaa", "radioId": "RDEM1"},
        {"artist": "Jay Zed", "browseId": "UCbbbbbbbbbbbbbbbbbbbbbb", "radioId": "RDEM2"},
    ]
    monkeypatch.setattr(youtube, "_artist_search", lambda name, cache: found)
    assert youtube.find_artist("jayz") == ("UCaaaaaaaaaaaaaaaaaaaaaa", "JAY-Z")
    # The artist of exactly that name comes before the first one found.
    assert youtube.find_artist("jay zed") == ("UCbbbbbbbbbbbbbbbbbbbbbb", "Jay Zed")


# ---- the page ----------------------------------------------------------------------------


def test_the_page_says_who_they_are_and_what_theyve_made(lib: Library) -> None:
    page = info(lib, name="linkin park")
    assert page["found"] is True and page["name"] == "Linkin Park"
    assert page["artist_id"] == LINKIN_PARK
    assert page["description"].startswith("Linkin Park is an American rock band")
    assert page["subscribers"] == "25.4M" and page["monthly_audience"] == "170M"
    assert page["views"].endswith("views")
    assert page["thumbnail"].startswith("https://")
    assert [song["title"] for song in page["songs"]] == [
        "In the End", "Numb", "Somewhere I Belong", "What I've Done", "Faint",
    ]  # fmt: skip
    first = page["songs"][0]
    assert first["video_id"] == IN_THE_END[2] and first["artists"] == ["Linkin Park"]
    assert first["is_official_audio"] is True and first["owned"] is False
    assert page["songs_playlist_id"] == ALL_SONGS  # without the "VL" its page writes
    assert len(page["albums"]) == 10 and len(page["singles"]) == 10
    assert page["albums"][0] == {
        "browse_id": FROM_ZERO, "title": "From Zero", "year": "2024", "kind": None,
        "is_explicit": True, "thumbnail": page["albums"][0]["thumbnail"],
    }  # fmt: skip
    assert page["singles"][0]["kind"] == "Single"
    assert page["related"][0]["name"] == "Slipknot"
    assert page["related"][0]["monthly_audience"] == "28.3M"
    assert page["related"][0]["artist_id"].startswith("UC")
    assert page["owned_songs"] == 0


def test_the_page_marks_what_the_owner_has(lib: Library) -> None:
    # One by its YouTube id, one a rip known only by its name.
    own(lib, IN_THE_END, ("Numb", "Linkin Park", None), ("Faint", "Somebody Else", None))
    page = info(lib, artist_id=LINKIN_PARK)  # by id: no search first
    owned = {song["title"]: song["owned"] for song in page["songs"]}
    assert owned == {
        "In the End": True, "Numb": True, "Somewhere I Belong": False,
        "What I've Done": False, "Faint": False,  # another artist's song of that name
    }  # fmt: skip
    assert page["owned_songs"] == 2


def test_the_page_is_kept_so_asking_again_asks_youtube_nothing(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = info(lib, name="Linkin Park")

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("YouTube Music was asked again")

    monkeypatch.setattr(youtube, "_fetch", refuse)
    assert info(lib, name="Linkin Park") == before
    assert info(lib, artist_id=LINKIN_PARK) == before


def test_an_artist_youtube_music_doesnt_have(lib: Library) -> None:
    assert info(lib, name="Zzyzx Qwfp Band") == {"found": False, "name": "Zzyzx Qwfp Band"}
    with pytest.raises(UserError, match="Type an artist's name"):
        info(lib, name="  ")
    with pytest.raises(YouTubeError, match="isn't an artist's id"):
        info(lib, artist_id="not an id")
    with pytest.raises(ReplayMissError):  # an id nothing was recorded for
        info(lib, artist_id="UCcccccccccccccccccccccc")


def test_a_page_that_isnt_an_artists_is_a_plain_error(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    def trips(kind: str, key: str, live: Any) -> Any:
        raise KeyError("musicImmersiveHeaderRenderer")  # ytmusicapi on a page of another kind

    monkeypatch.setattr(youtube, "_fetch", trips)
    with pytest.raises(YouTubeError, match="no artist page"):
        info(lib, artist_id="UCcccccccccccccccccccccc")


def test_only_whats_read_is_kept() -> None:
    raw = {
        "name": "Band", "description": "About them.", "subscribers": "1M", "views": "9 views",
        "monthlyListeners": None, "channelId": "UCx", "shuffleId": "RDAO", "subscribed": False,
        "thumbnails": [{"url": "https://e.invalid/s", "width": 100},
                       {"url": "https://e.invalid/l", "width": 900}],
        "songs": {"browseId": "VLOLAK5uy_x", "results": [
            {"videoId": "abcdefghijk", "title": "Song", "artists": [{"name": "Band", "id": "UC1"}],
             "likeStatus": "INDIFFERENT", "feedbackTokens": {"add": "x"}}]},
        "albums": {"browseId": "MPAD", "params": "p", "results": [
            {"browseId": "MPREb_1", "title": "Record", "year": "2001", "audioPlaylistId": "OLAK"}]},
        "singles": None,
        "videos": {"results": [{"videoId": "v"}]},
        "related": {"results": [{"browseId": "UC2", "title": "Other", "subscribers": "2M",
                                 "thumbnails": []}]},
    }  # fmt: skip
    kept = youtube.trim_artist_page(raw)
    assert set(kept) == {"name", "description", "subscribers", "monthlyListeners", "views",
                         "thumbnails", "songs", "albums", "singles", "related"}  # fmt: skip
    assert kept["thumbnails"] == [{"url": "https://e.invalid/l", "width": 900}]
    assert kept["songs"]["results"] == [
        {"videoId": "abcdefghijk", "title": "Song", "artists": [{"name": "Band", "id": "UC1"}]}
    ]
    assert kept["albums"]["results"] == [
        {"browseId": "MPREb_1", "title": "Record", "year": "2001", "type": None,
         "isExplicit": None, "thumbnails": []}
    ]  # fmt: skip
    assert kept["singles"] == {"results": []}
    assert youtube.trim_artist_page(None) == {}


# ---- all their songs, and an album -------------------------------------------------------


def test_every_song_of_theirs_with_lengths(lib: Library) -> None:
    own(lib, NUMB)
    with open_index(lib.paths, write=True) as index:
        found = artist.songs(lib, index, "VL" + ALL_SONGS)  # as the page's own link writes it
    assert found["more"] is False
    assert [song["title"] for song in found["songs"]][:3] == [
        "In the End", "Numb", "Somewhere I Belong",
    ]  # fmt: skip
    assert len(found["songs"]) == 8
    assert all(song["duration_s"] for song in found["songs"])
    assert [song["owned"] for song in found["songs"]][:3] == [False, True, False]


def test_an_albums_songs_in_its_order(lib: Library) -> None:
    with open_index(lib.paths, write=True) as index:
        found = artist.album(lib, index, FROM_ZERO)
        with pytest.raises(UserError, match="isn't an album's id"):
            artist.album(lib, index, "not an id!")
    assert found["title"] == "From Zero" and found["year"] == "2024"
    assert found["artists"] == ["Linkin Park"] and found["thumbnail"].startswith("https://")
    assert len(found["songs"]) == 11
    second = found["songs"][1]
    assert second["title"] == "The Emptiness Machine" and second["artists"] == ["Linkin Park"]
    assert second["album"] == "From Zero" and second["album_browse_id"] == FROM_ZERO
    assert second["duration_s"] and second["owned"] is False


# ---- over RPC ----------------------------------------------------------------------------


def test_the_artist_page_over_rpc(opened: rpc.Server) -> None:  # noqa: F811
    assert opened.lib is not None
    own(opened.lib, IN_THE_END)
    page = result(opened, "artist.info", name="Linkin Park")
    assert page["name"] == "Linkin Park" and page["songs"][0]["owned"] is True
    assert result(opened, "artist.info", artist_id=LINKIN_PARK) == page
    assert result(opened, "artist.info", name="Zzyzx Qwfp Band")["found"] is False
    assert code(opened, "artist.info") == rpc.INVALID_PARAMS
    assert code(opened, "artist.info", name="A", artist_id=LINKIN_PARK) == rpc.INVALID_PARAMS
    assert len(result(opened, "artist.songs", playlist_id=ALL_SONGS)["songs"]) == 8
    assert code(opened, "artist.songs") == rpc.INVALID_PARAMS
    assert result(opened, "artist.album", browse_id=FROM_ZERO)["title"] == "From Zero"
    assert code(opened, "artist.album", browse_id="no such thing!") == rpc.USER_ERROR
    # The songs go back as they came, so a download plan asks YouTube nothing more.
    song = page["songs"][1]
    plan = result(opened, "plan.create", kind="download",
                  options={"video_ids": [song["video_id"]], "candidates": [song]})  # fmt: skip
    assert plan["summary"]["operations"] == 1
