"""imports (v0.3): a playlist from elsewhere, each song found on YouTube Music.

YouTube Music's answers are recordings or stand-ins, so nothing here reaches the network.
"""

from __future__ import annotations

from typing import Any

import pytest
from index_support import candidate
from test_discover import own
from test_rpc import Capture, code, opened, out, result, root, server  # noqa: F401  (fixtures)

from musicorg import imports, pipeline, queue, rpc, youtube
from musicorg.errors import UserError, YouTubeError
from musicorg.index import open_index
from musicorg.library import Library
from musicorg.youtube import Candidate

JAZZ = "RDCLAK5uy_neriXH6JbZPr7Pf4LOi5bGQP-_lWRZXs4"  # a recorded playlist: 10 songs, 2 videos
VIDEO = "MUSIC_VIDEO_TYPE_OMV"


def find(lib: Library, *tracks: dict[str, Any]) -> list[dict[str, Any]]:
    with open_index(lib.paths, write=True) as index:
        return imports.find(lib, index, list(tracks))


def named(title: str, *artists: str, seconds: int | None = 200, **more: Any) -> dict[str, Any]:
    return {"title": title, "artists": list(artists), "duration_s": seconds, **more}


def from_youtube(found: Candidate) -> dict[str, Any]:
    return named(found.title, *found.artists, seconds=found.duration_s, candidate=found.to_dict())


@pytest.fixture
def searches(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Candidate]]:
    """YouTube Music's song search, answered from this dict by what the query has in it."""
    answers: dict[str, list[Candidate]] = {}
    asked: list[str] = []

    def search(query: str, limit: int = 10, **_: Any) -> list[Candidate]:
        asked.append(query)
        return [c for words, found in answers.items() if words in query.lower() for c in found]

    monkeypatch.setattr(youtube, "search_songs", search)
    answers["__asked__"] = asked  # type: ignore[assignment]
    return answers


# ---- the link ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "link",
    [
        "https://music.youtube.com/playlist?list=PLabc_DEF-123456789",
        "https://www.youtube.com/playlist?list=PLabc_DEF-123456789&si=whatever",
        "music.youtube.com/watch?v=melodyAAAAA&list=PLabc_DEF-123456789",
        "https://music.youtube.com/browse/VLPLabc_DEF-123456789",
        "  PLabc_DEF-123456789 ",
        "VLPLabc_DEF-123456789",
    ],
)
def test_a_playlists_link_in_any_of_its_shapes(link: str) -> None:
    assert imports.youtube_playlist_id(link) == "PLabc_DEF-123456789"


@pytest.mark.parametrize(
    ("link", "says"),
    [
        ("", "Paste a playlist's link"),
        ("https://open.spotify.com/playlist/37i9dQZF1DX", "isn't a YouTube"),
        ("https://music.youtube.com/watch?v=melodyAAAAA", "not a playlist"),
        ("https://music.youtube.com/playlist?list=LM", "only\nyou can open".replace("\n", " ")),
        ("not a link at all", "doesn't look like"),
    ],
)
def test_a_link_that_isnt_a_playlist_says_what_to_do(link: str, says: str) -> None:
    with pytest.raises(UserError, match=says):
        imports.youtube_playlist_id(link)


# ---- reading it --------------------------------------------------------------------------


def test_a_youtube_playlist_is_read_with_its_name_and_songs() -> None:
    found = imports.from_youtube(f"https://music.youtube.com/playlist?list={JAZZ}")
    assert found["source"] == "youtube" and found["name"]
    assert len(found["tracks"]) == 12
    first = found["tracks"][0]
    assert set(first) == {"title", "artists", "album", "duration_s", "is_explicit", "candidate"}
    assert first["candidate"]["video_id"] and first["artists"]
    assert first["candidate"]["title"] == first["title"]
    kinds = [track["candidate"]["video_type"] for track in found["tracks"]]
    assert kinds.count(youtube.OFFICIAL_AUDIO) == 10 and kinds.count(VIDEO) == 2


def test_a_playlist_youtube_wont_show(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(kind: str, key: str, live: Any) -> Any:
        raise KeyError("contents")  # what ytmusicapi does with a private playlist's page

    monkeypatch.setattr(youtube, "_fetch", refuse)
    with pytest.raises(YouTubeError, match="Unlisted or Public"):
        youtube.playlist("PLabc_DEF-123456789")
    with pytest.raises(YouTubeError, match="isn't a playlist's id"):
        youtube.playlist("not an id!")


# ---- finding the songs -------------------------------------------------------------------


def test_songs_from_a_youtube_playlist_need_no_search(
    lib: Library, searches: dict[str, list[Candidate]]
) -> None:
    mine, new = (
        candidate("ownedAAAAAA", "Mine", ("Band",)),
        candidate("newBBBBBBBB", "New", ("Band",)),
    )
    own(lib, ("Mine", "Band", "ownedAAAAAA"), ("Elsewhere", "Other Band", None))
    # The owner's copy under another YouTube id, or from their own files, still counts.
    same = candidate("otherCCCCCC", "Elsewhere", ("Other Band",))
    found = find(lib, from_youtube(mine), from_youtube(new), from_youtube(same))
    assert [f["state"] for f in found] == ["owned", "found", "owned"]
    assert [found[0]["track_id"], found[2]["track_id"]] == ["t_0", "t_1"]
    assert found[1]["candidate"]["video_id"] == "newBBBBBBBB"
    assert searches["__asked__"] == []


def test_a_music_video_is_swapped_for_the_song(
    lib: Library, searches: dict[str, list[Candidate]]
) -> None:
    video = candidate("videoAAAAAA", "Tune", ("Band",), 251, video_type=VIDEO)
    searches["band tune"] = [
        candidate("liveBBBBBBB", "Tune (Live)", ("Band",), 251),
        candidate("songCCCCCCC", "Tune", ("Band",), 214),  # the song: another length
    ]
    (found,) = find(lib, from_youtube(video))
    assert found["state"] == "found" and found["candidate"]["video_id"] == "songCCCCCCC"
    # Nothing on YouTube Music is the song: it's not found, not the video itself.
    searches.clear()
    assert find(lib, from_youtube(video)) == [{"state": "not_found"}]


def test_a_song_named_by_another_service(
    lib: Library, searches: dict[str, list[Candidate]]
) -> None:
    own(lib, ("Got It", "Band", None))
    searches["band certain"] = [candidate("certainAAAA", "Certain", ("Band",), 200)]
    searches["band longer"] = [candidate("longerBBBBB", "Longer", ("Band",), 260)]
    searches["band video only"] = [
        candidate("uploadCCCCC", "Video Only", ("Band",), 200, video_type=VIDEO)
    ]
    found = find(
        lib,
        named("Got It", "Band"),
        named("Certain", "Band"),
        named("Longer", "Band"),
        named("Video Only", "Band"),
        named("Nowhere", "Nobody"),
    )
    assert [f["state"] for f in found] == ["owned", "found", "unsure", "unsure", "not_found"]
    assert found[0] == {"state": "owned", "track_id": "t_0"}
    assert found[2]["why"] == "The length is different."
    assert found[3]["why"] == "It isn't youtube music's official audio.".capitalize()
    assert "why" not in found[1]
    asked = searches["__asked__"]
    assert not any("got it" in query.lower() for query in asked)  # owned: no search


def test_a_song_already_waiting_to_download(
    lib: Library, searches: dict[str, list[Candidate]], monkeypatch: pytest.MonkeyPatch
) -> None:
    new = candidate("newBBBBBBBB", "New", ("Band",))
    monkeypatch.setattr(youtube, "get_track", lambda video_id: new)
    with open_index(lib.paths, write=True) as index:
        pipeline.apply(lib, index, pipeline.plan_download(lib, index, ["newBBBBBBBB"]).plan_id)
    assert queue.downloads(lib.paths)[0]["state"] == "queued"
    (found,) = find(lib, from_youtube(new))
    assert found["state"] == "queued" and found["candidate"]["video_id"] == "newBBBBBBBB"


def test_what_isnt_a_song_is_refused(lib: Library) -> None:
    for bad in ({}, {"title": ""}, {"title": "X", "artists": "Band"},
                {"title": "X", "artists": [], "candidate": {"title": "X"}}):  # fmt: skip
        with pytest.raises(UserError):
            find(lib, bad)
    with pytest.raises(UserError, match="too many"):
        find(lib, *[named(f"Song {n}", "Band") for n in range(imports.FIND_AT_ONCE + 1)])


# ---- over RPC ----------------------------------------------------------------------------


def test_import_over_rpc(opened: rpc.Server, out: Capture) -> None:  # noqa: F811
    read = result(opened, "import.playlist", source="youtube", link=JAZZ)
    assert len(read["tracks"]) == 12
    songs = [t for t in read["tracks"] if t["candidate"]["video_type"] == youtube.OFFICIAL_AUDIO]
    found = result(opened, "import.find", tracks=songs[:3], token="page")["found"]
    assert [f["state"] for f in found] == ["found"] * 3
    assert [note["done"] for note in out.notes("import.progress")] == [1, 2, 3]
    assert out.notes("import.progress")[0] == {"token": "page", "done": 1, "of": 3}
    assert code(opened, "import.playlist", source="spotify", link=JAZZ) == rpc.INVALID_PARAMS
    assert code(opened, "import.playlist", source="youtube", link="nonsense here") == rpc.USER_ERROR
    assert code(opened, "import.find") == rpc.INVALID_PARAMS
