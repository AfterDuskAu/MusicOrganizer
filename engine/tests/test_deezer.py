"""deezer: a public playlist or album, read by its link, for imports.

Deezer is never reached: `deezer._http` is stood in for by answers recorded from
Deezer's API on 2026-10-03 (`fixtures/deezer/`, cut down to the fields that are read,
with two more that aren't). The album is 14 songs in two pages of 7; the playlist is
the first 3 of 100.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_rpc import Capture, code, opened, out, result, root, server  # noqa: F401  (fixtures)

from musicorg import deezer, imports, rpc
from musicorg.errors import ReplayMissError

FIXTURES = Path(__file__).parent / "fixtures" / "deezer"
ALBUM, PLAYLIST = "302127", "3155776842"


def recorded(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeDeezer:
    """Answers `deezer._http` from the recordings, and remembers what was asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.answers: dict[str, Any] = {
            f"/album/{ALBUM}": recorded(f"album-{ALBUM}.json"),
            f"/album/{ALBUM}/tracks?index=0&limit=7": recorded(f"album-{ALBUM}-tracks-0.json"),
            f"/album/{ALBUM}/tracks?limit=7&index=7": recorded(f"album-{ALBUM}-tracks-7.json"),
            f"/playlist/{PLAYLIST}": recorded(f"playlist-{PLAYLIST}.json"),
            f"/playlist/{PLAYLIST}/tracks?index=0&limit=3": recorded(
                f"playlist-{PLAYLIST}-tracks-0.json"
            ),
        }

    def __call__(self, url: str, params: dict[str, Any] | None = None) -> Any:
        path = url.removeprefix(deezer.API)
        if params:
            path += "?" + "&".join(f"{key}={value}" for key, value in params.items())
        self.asked.append(path)
        return self.answers.get(path, {"error": {"type": "DataException", "message": "no data",
                                                 "code": 800}})  # fmt: skip


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeDeezer:
    found = FakeDeezer()
    monkeypatch.setattr(deezer, "_http", found)
    monkeypatch.setattr(deezer, "PAGE_PAUSE_S", 0.0)
    return found


# ---- the link ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("link", "what"),
    [
        ("https://www.deezer.com/en/playlist/3155776842", ("playlist", PLAYLIST)),
        ("https://www.deezer.com/playlist/3155776842?utm_source=deezer", ("playlist", PLAYLIST)),
        ("deezer.com/fr-ca/album/302127/", ("album", ALBUM)),
        ("  3155776842 ", ("playlist", PLAYLIST)),
        ("https://link.deezer.com/s/abcDEF123", None),  # a share link: followed first
    ],
)
def test_a_links_playlist_or_album(link: str, what: tuple[str, str] | None) -> None:
    assert deezer.parse_link(link) == what


@pytest.mark.parametrize(
    ("link", "says"),
    [
        ("", "Paste"),
        ("https://open.spotify.com/playlist/abc", "isn't a Deezer link"),
        ("https://www.deezer.com/en/track/3135556", "not a playlist"),
        ("https://www.deezer.com/en/artist/27", "not a playlist"),
        ("https://deezer.page.link/abcDEF", "switched off"),
    ],
)
def test_a_link_that_isnt_a_playlist_says_what_to_do(link: str, says: str) -> None:
    with pytest.raises(deezer.DeezerError, match=says):
        deezer.parse_link(link)


def test_a_share_link_is_followed_to_its_page(
    fake: FakeDeezer, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = []

    def leads_to(url: str) -> str:
        asked.append(url)
        return f"https://www.deezer.com/en/album/{ALBUM}?host=0&utm_campaign=clipboard"

    monkeypatch.setattr(deezer, "_final_address", leads_to)
    assert deezer.where("link.deezer.com/s/abcDEF123") == ("album", ALBUM)
    assert asked == ["https://link.deezer.com/s/abcDEF123"]


@pytest.mark.parametrize(
    "ends_at", ["https://www.deezer.com/deezer-links-404", "https://example.invalid/playlist/1"]
)
def test_a_share_link_that_leads_nowhere(ends_at: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deezer, "_final_address", lambda url: ends_at)
    with pytest.raises(deezer.DeezerError, match="didn't lead to a playlist"):
        deezer.where("https://link.deezer.com/s/abcDEF123")


# ---- reading -----------------------------------------------------------------------------


def test_an_album_is_read_page_after_page(
    fake: FakeDeezer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deezer, "PAGE", 7)
    found = deezer.playlist(f"https://www.deezer.com/en/album/{ALBUM}")
    assert found["name"] == "Discovery" and found["more"] is False
    assert len(found["tracks"]) == 14
    assert found["tracks"][0] == {
        "title": "One More Time",
        "artists": ["Daft Punk"],
        "album": "Discovery",  # an album's songs don't each name it: the album's own name
        "duration_s": 320,
        "is_explicit": False,
    }
    assert found["tracks"][7]["title"] == "High Life"
    assert fake.asked == [
        f"/album/{ALBUM}",
        f"/album/{ALBUM}/tracks?index=0&limit=7",
        f"/album/{ALBUM}/tracks?limit=7&index=7",  # the address Deezer gave for the next page
    ]


def test_a_playlist_longer_than_is_read_says_so(
    fake: FakeDeezer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deezer, "PAGE", 3)
    monkeypatch.setattr(deezer, "MAX_TRACKS", 3)
    found = deezer.playlist(PLAYLIST)
    assert found["name"] == "Top Worldwide" and found["more"] is True
    assert len(found["tracks"]) == 3
    assert all(track["album"] and track["artists"] for track in found["tracks"])


def test_what_isnt_a_song_is_left_out() -> None:
    assert deezer._song({"type": "episode", "title": "A podcast"}, None) is None
    assert deezer._song({"type": "track", "title": "  "}, None) is None
    assert deezer._song({"title": "Song", "duration": 0, "artist": None}, "Album") == {
        "title": "Song", "artists": [], "album": "Album", "duration_s": None, "is_explicit": None,
    }  # fmt: skip


def test_a_playlist_deezer_wont_show(fake: FakeDeezer) -> None:
    with pytest.raises(deezer.DeezerError, match="private"):
        deezer.playlist("https://www.deezer.com/en/playlist/1")
    fake.answers["/playlist/2"] = {"error": {"type": "Exception", "message": "Quota", "code": 4}}
    with pytest.raises(deezer.DeezerError, match="slow down"):
        deezer.playlist("2")
    fake.answers["/playlist/3"] = {"error": {"type": "Exception", "message": "Odd", "code": 1}}
    with pytest.raises(deezer.DeezerError, match='"Odd"'):
        deezer.playlist("3")
    fake.answers["/playlist/4"] = ["not", "an", "object"]
    with pytest.raises(deezer.DeezerError, match="can't be read"):
        deezer.playlist("4")


def test_only_deezers_own_address_is_asked_for_more(fake: FakeDeezer) -> None:
    fake.answers["/playlist/5"] = {"title": "Odd"}
    fake.answers[f"/playlist/5/tracks?index=0&limit={deezer.PAGE}"] = {
        "data": [{"type": "track", "title": "Song"}],
        "next": "https://example.invalid/more",
    }
    with pytest.raises(deezer.DeezerError, match="isn't Deezer's"):
        deezer.playlist("5")


def test_the_network_is_never_reached_in_tests() -> None:
    with pytest.raises(ReplayMissError):
        deezer._http(deezer.API + "/playlist/1")
    with pytest.raises(ReplayMissError):
        deezer._final_address("https://link.deezer.com/s/abc")


# ---- as an import, and over RPC ----------------------------------------------------------


def test_a_deezer_playlist_as_an_import(fake: FakeDeezer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deezer, "PAGE", 7)
    read = imports.from_deezer(f"deezer.com/album/{ALBUM}")
    assert read["source"] == "deezer" and read["name"] == "Discovery"
    assert len(read["tracks"]) == 14 and read["more"] is False


def test_deezer_over_rpc(
    opened: rpc.Server,  # noqa: F811
    fake: FakeDeezer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deezer, "PAGE", 7)
    read = result(opened, "import.playlist", source="deezer", link=f"deezer.com/album/{ALBUM}")
    assert [track["title"] for track in read["tracks"]][:2] == ["One More Time", "Aerodynamic"]
    assert code(opened, "import.playlist", source="deezer") == rpc.INVALID_PARAMS
    assert code(opened, "import.playlist", source="deezer", link="nonsense") == rpc.USER_ERROR
    assert code(opened, "import.playlist", source="tidal", link="x") == rpc.INVALID_PARAMS
