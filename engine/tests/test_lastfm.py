"""lastfm: the owner's most played and loved songs, for Discover and for imports.

Last.fm is never reached: `lastfm._http` is stood in for by answers in the shapes
Last.fm's documentation gives (checked 2026-10-03). No real answer has been recorded:
there was no API key to ask with when this was written.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_rpc import Capture, code, opened, out, result, root, server  # noqa: F401  (fixtures)

from musicorg import config, imports, lastfm, rpc
from musicorg.errors import ReplayMissError

KEY = "0123456789abcdef" * 2  # made up at runtime: the shape of an API key
USER = "listener"


def top(name: str, artist: str, plays: int, seconds: int = 200) -> dict[str, Any]:
    """A song as `user.getTopTracks` gives it: its numbers as text."""
    return {"name": name, "duration": str(seconds), "playcount": str(plays), "mbid": "",
            "artist": {"name": artist, "mbid": "", "url": "https://www.last.fm/music/x"},
            "@attr": {"rank": "1"}}  # fmt: skip


def loved(name: str, artist: str) -> dict[str, Any]:
    return {"name": name, "artist": {"name": artist, "mbid": "", "url": ""},
            "date": {"uts": "1700000000", "#text": "14 Nov 2023, 22:13"}}  # fmt: skip


class FakeLastfm:
    """Answers `lastfm._http` from what the test set up, and remembers what was asked."""

    def __init__(self) -> None:
        self.asked: list[dict[str, Any]] = []
        self.top: dict[str, list[dict[str, Any]]] = {}  # by period
        self.loved: list[dict[str, Any]] = []
        self.problem: tuple[int, dict[str, Any]] | None = None

    def __call__(self, params: dict[str, Any]) -> tuple[int, Any]:
        self.asked.append(dict(params))
        if self.problem is not None:
            return self.problem
        if params["api_key"] != KEY:
            return 403, {
                "error": 10,
                "message": "Invalid API key - You must be granted a valid key",
            }
        if params["user"].lower() != USER:
            return 404, {"error": 6, "message": "User not found"}
        if params["method"] == "user.getInfo":
            return 200, {"user": {"name": "Listener", "playcount": "12345"}}
        if params["method"] == "user.getTopTracks":
            return 200, self._page("toptracks", self.top.get(params["period"], []), params)
        return 200, self._page("lovedtracks", self.loved, params)

    @staticmethod
    def _page(inside: str, songs: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
        size, page = params["limit"], params["page"]
        cut = songs[(page - 1) * size : page * size]
        pages = max(1, -(-len(songs) // size))
        about = {"user": USER, "page": str(page), "perPage": str(size),
                 "totalPages": str(pages), "total": str(len(songs))}  # fmt: skip
        # Last.fm gives a list of one as the one thing, not a list.
        return {inside: {"track": cut[0] if len(cut) == 1 else cut, "@attr": about}}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeLastfm:
    found = FakeLastfm()
    monkeypatch.setattr(lastfm, "_http", found)
    monkeypatch.setattr(lastfm, "PAGE_PAUSE_S", 0.0)
    return found


def set_up() -> None:
    config.save_account("lastfm", {"user": "Listener", "api_key": KEY})


# ---- setting it up -----------------------------------------------------------------------


def test_nothing_is_set_up_at_first() -> None:
    assert lastfm.status() == {"user": None, "has_key": False, "connected": False}
    with pytest.raises(lastfm.LastfmError, match="isn't set up"):
        lastfm.top_tracks()


def test_connecting_saves_the_name_as_lastfm_spells_it(fake: FakeLastfm) -> None:
    assert lastfm.connect(" listener ", KEY.upper()) == {
        "user": "Listener", "has_key": True, "connected": True,
    }  # fmt: skip
    assert config.load_accounts()["lastfm"] == {"user": "Listener", "api_key": KEY}
    assert fake.asked[0]["method"] == "user.getInfo" and fake.asked[0]["format"] == "json"


def test_a_profiles_address_gives_the_username(fake: FakeLastfm) -> None:
    assert lastfm.username("https://www.last.fm/user/listener/library") == "listener"
    assert lastfm.username("last.fm/user/Some_One-2") == "Some_One-2"
    for bad in ("", "x", "two words", "https://example.invalid/user/listener"):
        with pytest.raises(lastfm.LastfmError, match="username"):
            lastfm.username(bad)


def test_the_username_can_change_without_the_key_again(fake: FakeLastfm) -> None:
    set_up()
    config.save_account("lastfm", {"user": "Old", "api_key": KEY})
    assert lastfm.connect("listener")["user"] == "Listener"
    assert fake.asked[-1]["api_key"] == KEY


def test_what_isnt_a_key_or_a_user_is_refused_and_nothing_is_saved(fake: FakeLastfm) -> None:
    with pytest.raises(lastfm.LastfmError, match="isn't a Last.fm API key"):
        lastfm.connect(USER, "the-shared-secret")
    with pytest.raises(lastfm.LastfmError, match="isn't a Last.fm API key"):
        lastfm.connect(USER)  # no key given, and none saved
    assert fake.asked == []
    with pytest.raises(lastfm.LastfmError, match="didn't accept that API key"):
        lastfm.connect(USER, "f" * 32)
    with pytest.raises(lastfm.LastfmError, match="no user of that name"):
        lastfm.connect("nobody", KEY)
    assert lastfm.status()["connected"] is False


def test_forgetting(fake: FakeLastfm) -> None:
    set_up()
    assert lastfm.forget() == {"user": None, "has_key": False, "connected": False}
    assert "lastfm" not in config.load_accounts()


def test_the_key_is_never_in_what_the_owner_is_told(fake: FakeLastfm) -> None:
    set_up()
    for code_, words in ((29, "Rate limit exceeded"), (16, "Try again"), (17, "Login required"),
                         (26, "Suspended"), (8, f"Operation failed for key {KEY}")):  # fmt: skip
        fake.problem = (400, {"error": code_, "message": words})
        with pytest.raises(lastfm.LastfmError) as told:
            lastfm.top_tracks()
        assert KEY not in told.value.message and "listener" not in told.value.message.lower()
    fake.problem = (500, None)
    with pytest.raises(lastfm.LastfmError, match="HTTP 500"):
        lastfm.loved_tracks()


def test_the_network_is_never_reached_in_tests() -> None:
    with pytest.raises(ReplayMissError):
        lastfm._http({"method": "user.getInfo"})


# ---- the songs ---------------------------------------------------------------------------


def test_most_played_most_first_with_their_plays(fake: FakeLastfm) -> None:
    set_up()
    fake.top["6month"] = [top("One", "Band", 40, 215), top("Two", "Other", 12, 0)]
    songs, more = lastfm.top_tracks("6month", 50)
    assert more is False
    assert songs == [
        {"title": "One", "artists": ["Band"], "album": None, "duration_s": 215,
         "is_explicit": None, "plays": 40},
        {"title": "Two", "artists": ["Other"], "album": None, "duration_s": None,  # "0": unknown
         "is_explicit": None, "plays": 12},
    ]  # fmt: skip
    assert fake.asked[-1]["period"] == "6month" and fake.asked[-1]["user"] == "Listener"
    with pytest.raises(lastfm.LastfmError, match="period"):
        lastfm.top_tracks("fortnight")


def test_a_list_of_one_and_of_none(fake: FakeLastfm) -> None:
    set_up()
    fake.top["overall"] = [top("Only", "Band", 3)]
    assert [s["title"] for s in lastfm.top_tracks()[0]] == ["Only"]
    assert lastfm.loved_tracks() == ([], False)


def test_long_lists_are_read_in_pages_up_to_the_limit(
    fake: FakeLastfm, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_up()
    monkeypatch.setattr(lastfm, "PAGE", 2)
    fake.loved = [loved(f"Song {n}", "Band") for n in range(5)]
    songs, more = lastfm.loved_tracks()
    assert [s["title"] for s in songs] == [f"Song {n}" for n in range(5)] and more is False
    assert [a["page"] for a in fake.asked] == [1, 2, 3]
    songs, more = lastfm.loved_tracks(3)
    assert len(songs) == 3 and more is True


def test_the_lists_import_playlists_offers(fake: FakeLastfm) -> None:
    set_up()
    fake.loved = [loved("Dear", "Band")]
    fake.top["12month"] = [top("Year", "Band", 9)]
    assert lastfm.playlist("loved")["name"] == "Last.fm Loved Tracks"
    year = imports.from_lastfm("top_12month")
    assert year["source"] == "lastfm" and year["name"] == "Last.fm Top Tracks (Year)"
    assert year["tracks"] == [
        {"title": "Year", "artists": ["Band"], "album": None, "duration_s": 200,
         "is_explicit": None}
    ]  # fmt: skip
    with pytest.raises(lastfm.LastfmError, match="list"):
        lastfm.playlist("top_fortnight")


# ---- over RPC ----------------------------------------------------------------------------


def test_lastfm_over_rpc(opened: rpc.Server, fake: FakeLastfm) -> None:  # noqa: F811
    assert result(opened, "account.status")["lastfm"] == {
        "user": None, "has_key": False, "connected": False,
    }  # fmt: skip
    assert code(opened, "import.playlist", source="lastfm", list="loved") == rpc.USER_ERROR
    assert code(opened, "account.connect", service="spotify", user=USER) == rpc.INVALID_PARAMS
    assert code(opened, "account.connect", service="lastfm", user=USER, api_key="no") == (
        rpc.USER_ERROR
    )
    connected = result(opened, "account.connect", service="lastfm", user=USER, api_key=KEY)
    assert connected["lastfm"] == {"user": "Listener", "has_key": True, "connected": True}
    assert "spotify" in connected and KEY not in str(connected)

    fake.loved = [loved("Dear", "Band")]
    read = result(opened, "import.playlist", source="lastfm", list="loved")
    assert read["name"] == "Last.fm Loved Tracks" and len(read["tracks"]) == 1
    assert code(opened, "import.playlist", source="lastfm") == rpc.INVALID_PARAMS

    gone = result(opened, "account.sign_out", service="lastfm")
    assert gone["lastfm"]["connected"] is False
    assert code(opened, "account.sign_out", service="deezer") == rpc.INVALID_PARAMS
