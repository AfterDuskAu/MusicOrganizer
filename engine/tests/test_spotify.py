"""spotify: signing in (PKCE) and reading the owner's playlists, for imports.

Spotify is never reached: `spotify._http` is stood in for by a script of answers in the
shapes Spotify's documentation gives (checked 2026-10-03, after the February 2026
changes). No real response has been recorded yet: nobody had signed in when this was
written. The listener for Spotify's answer is tried for real, on this computer only.
"""

from __future__ import annotations

import http.client
import json
import threading
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from test_rpc import Capture, code, opened, out, result, root, server  # noqa: F401  (fixtures)

from musicorg import config, imports, rpc, spotify
from musicorg.errors import ReplayMissError

CLIENT = "0123456789abcdef" * 2  # made up at runtime: the shape of a Client ID
ACCESS, REFRESH = "access-" + "a" * 8, "refresh-" + "r" * 8


class FakeSpotify:
    """Answers `spotify._http` from what the test set up, and remembers what was asked."""

    def __init__(self) -> None:
        self.asked: list[dict[str, Any]] = []
        self.answers: dict[str, list[tuple[int, dict[str, str], Any]]] = {}
        self.token: tuple[int, Any] = (
            200,
            {"access_token": ACCESS, "token_type": "Bearer", "expires_in": 3600,
             "refresh_token": REFRESH, "scope": " ".join(spotify.SCOPES)},
        )  # fmt: skip

    def answer(self, path: str, body: Any, status: int = 200, **headers: str) -> None:
        self.answers.setdefault(path, []).append((status, headers, body))

    def __call__(self, method: str, url: str, **kw: Any) -> tuple[int, dict[str, str], Any]:
        self.asked.append({"method": method, "url": url, **kw})
        if url == spotify.TOKEN_URL:
            return self.token[0], {}, self.token[1]
        path = url.removeprefix(spotify.API)
        waiting = self.answers.get(path)
        assert waiting, f"nothing set up for {path}"
        return waiting.pop(0) if len(waiting) > 1 else waiting[0]

    def gets(self) -> list[str]:
        return [a["url"].removeprefix(spotify.API) for a in self.asked if a["method"] == "GET"]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSpotify:
    found = FakeSpotify()
    monkeypatch.setattr(spotify, "_http", found)
    monkeypatch.setattr(spotify, "_access", None)
    monkeypatch.setattr(spotify, "_pending", None)
    monkeypatch.setattr(spotify, "PAGE_PAUSE_S", 0.0)
    return found


def signed_in() -> None:
    config.save_account("spotify", {"client_id": CLIENT, "refresh_token": REFRESH, "name": "Me"})


def track(name: str, *artists: str, ms: int = 200_000, **more: Any) -> dict[str, Any]:
    return {"type": "track", "name": name, "artists": [{"name": a} for a in artists],
            "album": {"name": "Album"}, "duration_ms": ms, "explicit": False, **more}  # fmt: skip


# ---- signing in --------------------------------------------------------------------------


def test_the_code_challenge_is_pkces() -> None:
    # The example in RFC 7636, appendix B.
    assert (
        spotify.challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_the_address_for_the_browser() -> None:
    url = urlsplit(spotify.authorize_url(CLIENT, "v" * 50, "the-state"))
    assert f"{url.scheme}://{url.netloc}{url.path}" == spotify.AUTHORIZE_URL
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert query == {
        "client_id": CLIENT,
        "response_type": "code",
        "redirect_uri": f"http://127.0.0.1:{spotify.REDIRECT_PORT}/callback",
        "code_challenge_method": "S256",
        "code_challenge": spotify.challenge("v" * 50),
        "scope": "playlist-read-private playlist-read-collaborative user-library-read",
        "state": "the-state",
    }
    assert "localhost" not in spotify.redirect_uri()  # Spotify doesn't accept it


def come_back(port: int, query: str) -> tuple[int, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", f"/callback?{query}")
        response = conn.getresponse()
        return response.status, response.read().decode()
    finally:
        conn.close()


def free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_signing_in_from_start_to_finish(fake: FakeSpotify) -> None:
    fake.answer("/me", {"id": "me", "display_name": "Me Myself"})
    said: list[str | None] = []
    over = threading.Event()

    def done(problem: str | None) -> None:
        said.append(problem)
        over.set()

    port = free_port()
    url = spotify.begin_sign_in(f"  {CLIENT} ", done, port=port, wait_s=20)
    sent = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert sent["redirect_uri"] == f"http://127.0.0.1:{port}/callback"
    assert spotify.status()["signed_in"] is False and spotify.status()["client_id"] == CLIENT

    # An answer with another state isn't ours: nothing is signed in to, and we keep waiting.
    status, page = come_back(port, "code=stolen&state=someone-elses")
    assert status == 400 and "wasn't for this sign-in" in page
    status, page = come_back(port, f"code=the-code&state={sent['state']}")
    assert status == 200 and "You're signed in" in page
    assert over.wait(10) and said == [None]

    exchange = next(a for a in fake.asked if a["url"] == spotify.TOKEN_URL)
    assert exchange["method"] == "POST"
    assert exchange["data"]["grant_type"] == "authorization_code"
    assert exchange["data"]["code"] == "the-code" and exchange["data"]["client_id"] == CLIENT
    assert exchange["data"]["redirect_uri"] == sent["redirect_uri"]
    assert spotify.challenge(exchange["data"]["code_verifier"]) == sent["code_challenge"]
    assert "client_secret" not in exchange["data"]  # PKCE: there is no secret
    assert spotify.status() == {"client_id": CLIENT, "signed_in": True, "name": "Me Myself",
                                "redirect_uri": spotify.redirect_uri()}  # fmt: skip
    # What's kept: the refresh token, never the access token; for this user only.
    saved = json.loads(config.accounts_path().read_text(encoding="utf-8"))
    assert saved == {"profiles": {"default": {"spotify": {
        "client_id": CLIENT, "refresh_token": REFRESH, "name": "Me Myself"}}}}  # fmt: skip
    assert ACCESS not in config.accounts_path().read_text(encoding="utf-8")
    if config.os.name != "nt":
        assert config.accounts_path().stat().st_mode & 0o077 == 0

    # Signing out forgets the sign-in and keeps the Client ID.
    assert spotify.sign_out()["signed_in"] is False
    assert json.loads(config.accounts_path().read_text(encoding="utf-8")) == {
        "profiles": {"default": {"spotify": {"client_id": CLIENT}}}
    }


def test_a_sign_in_that_was_turned_down_or_never_finished(fake: FakeSpotify) -> None:
    said: list[str | None] = []
    over = threading.Event()

    def done(problem: str | None) -> None:
        said.append(problem)
        over.set()

    port = free_port()
    url = spotify.begin_sign_in(CLIENT, done, port=port, wait_s=20)
    state = parse_qs(urlsplit(url).query)["state"][0]
    status, page = come_back(port, f"error=access_denied&state={state}")
    assert status == 400 and "wasn't given permission" in page
    assert over.wait(10) and said == [
        "Spotify wasn't given permission, so nothing was signed in to."
    ]
    assert spotify.status()["signed_in"] is False

    # Nobody comes back: the listener gives up, and says so.
    said.clear()
    over.clear()
    spotify.begin_sign_in(None, done, port=free_port(), wait_s=0.2)  # the saved Client ID
    assert over.wait(10) and "in time" in (said[0] or "")
    # Spotify refuses the code: said in plain words, and nothing is saved.
    said.clear()
    over.clear()
    fake.token = (400, {"error": "invalid_grant", "error_description": "Invalid code"})
    port = free_port()
    url = spotify.begin_sign_in(None, done, port=port, wait_s=20)
    state = parse_qs(urlsplit(url).query)["state"][0]
    status, page = come_back(port, f"code=bad&state={state}")
    assert status == 400 and "didn&#" not in page and "Invalid code" in page
    assert over.wait(10) and spotify.status()["signed_in"] is False


def test_what_isnt_a_client_id_and_a_port_in_use(fake: FakeSpotify) -> None:
    for bad in (None, "", "the client secret?", CLIENT[:-1]):
        with pytest.raises(spotify.SpotifyError, match="isn't a Spotify Client ID"):
            spotify.begin_sign_in(bad, lambda problem: None, port=free_port())
    import socket

    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        with pytest.raises(spotify.SpotifyError, match="using port"):
            spotify.begin_sign_in(CLIENT, lambda problem: None, port=taken.getsockname()[1])


def test_tests_never_reach_spotify() -> None:
    with pytest.raises(ReplayMissError):
        spotify._http("GET", spotify.API + "/me")


def test_each_profile_has_its_own_sign_in(
    fake: FakeSpotify, monkeypatch: pytest.MonkeyPatch
) -> None:
    signed_in()  # the first profile (no name given, as from the command line)
    assert spotify.status()["signed_in"] is True and config.current_profile() == "default"

    # Someone else's profile: not signed in, and it can't see the first one's Client ID.
    monkeypatch.setenv(config.PROFILE_ENV, "p_1a2b3c4d")
    spotify._forget_access()
    assert spotify.status() == {"client_id": None, "signed_in": False, "name": None,
                                "redirect_uri": spotify.redirect_uri()}  # fmt: skip
    with pytest.raises(spotify.SpotifyError, match="isn't signed in"):
        spotify._get("/me")
    config.save_account("spotify", {"client_id": CLIENT, "refresh_token": "hers", "name": "C"})
    assert spotify.status()["name"] == "C"
    spotify.sign_out()

    # Back in the first profile, everything is as it was left.
    monkeypatch.delenv(config.PROFILE_ENV)
    assert config.load_accounts()["spotify"]["refresh_token"] == REFRESH
    saved = json.loads(config.accounts_path().read_text(encoding="utf-8"))["profiles"]
    assert set(saved) == {"default", "p_1a2b3c4d"}
    assert saved["p_1a2b3c4d"] == {"spotify": {"client_id": CLIENT}}
    # A profile name that isn't one (it would be part of a file's keys) is the first profile.
    monkeypatch.setenv(config.PROFILE_ENV, "../../etc")
    assert config.current_profile() == "default"


# ---- asking -------------------------------------------------------------------------------


def test_the_sign_in_is_renewed_and_a_new_token_is_kept(fake: FakeSpotify) -> None:
    signed_in()
    fake.token = (200, {"access_token": ACCESS, "expires_in": 3600, "refresh_token": "newer"})
    fake.answer("/me", {"id": "me"})
    assert spotify._get("/me") == {"id": "me"}
    renew = fake.asked[0]
    assert renew["data"] == {"grant_type": "refresh_token", "refresh_token": REFRESH,
                             "client_id": CLIENT}  # fmt: skip
    assert fake.asked[1]["headers"] == {"Authorization": f"Bearer {ACCESS}"}
    saved = config.load_accounts()["spotify"]
    assert saved["refresh_token"] == "newer" and saved["name"] == "Me"
    spotify._get("/me")
    assert len([a for a in fake.asked if a["url"] == spotify.TOKEN_URL]) == 1  # still good


def test_a_token_that_ran_out_early_is_asked_for_again_once(fake: FakeSpotify) -> None:
    signed_in()
    fake.answer("/me", {"error": {"status": 401, "message": "The access token expired"}}, 401)
    fake.answer("/me", {"id": "me"})
    assert spotify._get("/me") == {"id": "me"}
    assert len([a for a in fake.asked if a["url"] == spotify.TOKEN_URL]) == 2


def test_what_spotify_refuses_is_said_plainly(fake: FakeSpotify) -> None:
    with pytest.raises(spotify.SpotifyError, match="isn't signed in"):
        spotify._get("/me")
    signed_in()
    fake.answer("/a", {"error": {"status": 403, "message": "Forbidden"}}, 403)
    fake.answer("/b", None, 429, **{"retry-after": "30"})
    fake.answer("/c", {"error": {"status": 404, "message": "Not found"}}, 404)
    fake.answer("/d", None, 503)
    for path, says in (("/a", "Premium"), ("/b", "in 30 seconds"), ("/c", "any more"),
                       ("/d", "HTTP 503")):  # fmt: skip
        with pytest.raises(spotify.SpotifyError, match=says):
            spotify._get(path)
    with pytest.raises(spotify.SpotifyError, match="isn't Spotify's"):
        spotify._get("https://elsewhere.example/v1/me")
    # The sign-in was taken back at Spotify: it's forgotten here, and it says what to do.
    spotify._forget_access()
    fake.token = (400, {"error": "invalid_grant", "error_description": "Refresh token revoked"})
    with pytest.raises(spotify.SpotifyError, match="run out"):
        spotify._get("/me")
    assert spotify.status()["signed_in"] is False


# ---- playlists ---------------------------------------------------------------------------


def test_the_owners_playlists_liked_songs_first(fake: FakeSpotify) -> None:
    signed_in()
    fake.answer("/me", {"id": "me", "display_name": "Me"})
    fake.answer("/me/tracks", {"items": [], "total": 412, "next": None})
    fake.answer("/me/playlists", {
        "items": [
            {"id": "mine00000001", "name": "Road Trip", "owner": {"id": "me", "display_name": "Me"},
             "collaborative": False, "items": {"total": 80}},
            {"id": "theirs000001", "name": "Top Hits", "owner": {"id": "spotify"},
             "collaborative": False, "tracks": {"total": 50}},  # the older spelling
            None,
        ],
        "next": spotify.API + "/me/playlists?offset=50&limit=50",
    })  # fmt: skip
    fake.answer("/me/playlists?offset=50&limit=50", {
        "items": [{"id": "shared000001", "name": "Ours", "owner": {"id": "friend"},
                   "collaborative": True, "items": {"total": 7}}],
        "next": None,
    })  # fmt: skip
    found = spotify.playlists()
    assert [(p["id"], p["name"], p["total"], p["readable"]) for p in found] == [
        ("liked", "Liked Songs", 412, True),
        ("mine00000001", "Road Trip", 80, True),
        ("theirs000001", "Top Hits", 50, False),  # only followed: Spotify won't give its songs
        ("shared000001", "Ours", 7, True),
    ]
    assert found[2]["owner"] == "spotify" and found[0]["owner"] == "Me"


def test_a_playlists_songs(fake: FakeSpotify) -> None:
    signed_in()
    fake.answer("/playlists/mine00000001", {"name": "Road Trip", "items": {"total": 5}})
    fake.answer("/playlists/mine00000001/items", {
        "items": [
            {"item": track("Tune", "Band", "Guest", ms=214_400, explicit=True)},
            {"track": track("Older Shape", "Band")},
            {"item": track("On My Disk", "Me"), "is_local": True},
            {"item": {"type": "episode", "name": "A Podcast"}},
            {"item": None},
        ],
        "next": spotify.API + "/playlists/mine00000001/items?offset=50",
    })  # fmt: skip
    fake.answer("/playlists/mine00000001/items?offset=50", {
        "items": [{"item": track("Last", "Other")}], "next": None,
    })  # fmt: skip
    found = spotify.playlist("mine00000001")
    assert found["name"] == "Road Trip" and found["more"] is False
    assert found["tracks"] == [
        {"title": "Tune", "artists": ["Band", "Guest"], "album": "Album", "duration_s": 214,
         "is_explicit": True},
        {"title": "Older Shape", "artists": ["Band"], "album": "Album", "duration_s": 200,
         "is_explicit": False},
        {"title": "Last", "artists": ["Other"], "album": "Album", "duration_s": 200,
         "is_explicit": False},
    ]  # fmt: skip
    with pytest.raises(spotify.SpotifyError, match="isn't a Spotify playlist"):
        spotify.playlist("../me")


def test_liked_songs_and_a_playlist_too_long_to_read_whole(
    fake: FakeSpotify, monkeypatch: pytest.MonkeyPatch
) -> None:
    signed_in()
    monkeypatch.setattr(spotify, "MAX_TRACKS", 3)
    fake.answer("/me/tracks", {
        "items": [{"added_at": "2026-01-01T00:00:00Z", "track": track(f"Song {n}", "Band")}
                  for n in range(4)],
        "next": spotify.API + "/me/tracks?offset=50",
    })  # fmt: skip
    found = imports.from_spotify("liked")
    assert found["source"] == "spotify" and found["name"] == "Liked Songs"
    assert [t["title"] for t in found["tracks"]] == ["Song 0", "Song 1", "Song 2"]
    assert found["more"] is True
    assert "/me/tracks?offset=50" not in fake.gets()  # it stopped at the most


# ---- over RPC ----------------------------------------------------------------------------


def test_accounts_over_rpc(  # noqa: F811
    opened: rpc.Server,  # noqa: F811
    out: Capture,  # noqa: F811
    fake: FakeSpotify,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert result(opened, "account.status") == {
        "spotify": {"client_id": None, "signed_in": False, "name": None,
                    "redirect_uri": spotify.redirect_uri()}
    }  # fmt: skip
    assert code(opened, "account.sign_in", service="spotify", client_id="nope") == rpc.USER_ERROR
    assert code(opened, "account.sign_in", service="apple") == rpc.INVALID_PARAMS
    assert code(opened, "import.playlists", source="spotify") == rpc.USER_ERROR  # not signed in

    started: dict[str, Any] = {}

    def begin(client_id: str | None, done: Any, **_: Any) -> str:
        started.update(client_id=client_id, done=done)
        return "https://accounts.spotify.com/authorize?made=up"

    monkeypatch.setattr(spotify, "begin_sign_in", begin)
    answer = result(opened, "account.sign_in", service="spotify", client_id=CLIENT)
    assert answer == {"authorize_url": "https://accounts.spotify.com/authorize?made=up"}
    started["done"](None)
    started["done"]("It didn't work.")
    assert out.notes("account.changed") == [
        {"service": "spotify", "signed_in": True, "problem": None},
        {"service": "spotify", "signed_in": False, "problem": "It didn't work."},
    ]

    signed_in()
    fake.answer("/me", {"id": "me", "display_name": "Me"})
    fake.answer("/me/tracks", {"items": [], "total": 2, "next": None})
    fake.answer("/me/playlists", {"items": [], "next": None})
    assert [p["id"] for p in result(opened, "import.playlists", source="spotify")["playlists"]] == [
        "liked"
    ]
    fake.answers.pop("/me/tracks")  # the count's answer; now its songs are asked for
    fake.answer("/me/tracks", {"items": [{"track": track("Tune", "Band")}], "next": None})
    read = result(opened, "import.playlist", source="spotify", playlist_id="liked")
    assert read["name"] == "Liked Songs" and read["tracks"][0]["title"] == "Tune"
    assert result(opened, "account.sign_out", service="spotify")["spotify"]["signed_in"] is False
    assert code(opened, "import.playlist", source="apple", link="x") == rpc.INVALID_PARAMS
