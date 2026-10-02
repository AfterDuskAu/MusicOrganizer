"""Spotify, for reading the owner's own playlists (imports, v0.3). Never its audio.

**Signing in** is Spotify's "Authorization Code with PKCE" flow, the one for an app
that can't keep a secret:

1. The owner registers an app of their own at developer.spotify.com (Spotify requires
   it, and since February 2026 requires that owner to have Spotify Premium) and gives
   this engine its Client ID. A Client ID isn't a password, but it's kept on this
   computer all the same, never in the repo.
2. `begin_sign_in()` starts listening on this computer (127.0.0.1 only) and returns an
   address at accounts.spotify.com for the browser. The owner signs in *there*: their
   password never passes through this engine.
3. Spotify sends the browser back to this computer with a one-time code, which is
   exchanged for a refresh token. That token is all that's kept (`accounts.json` in
   the app's settings folder, readable by this user only). It can read playlists and
   Liked Songs, and nothing else: see `SCOPES`.

**Reading** is then three questions: which playlists there are, what's in one, and
what's in Liked Songs. Spotify only gives the songs of playlists the owner made or
collaborates on; a followed playlist of someone else's is listed, but can't be read.

Nothing here is logged or raised with a token, a code or the Client ID in it. In replay
mode (`MUSICORG_REPLAY_DIR`, every test) the network is never reached: `_http` refuses.

Checked against Spotify's documentation on 2026-10-03, after its February 2026 changes
(`/playlists/{id}/items` replaced `/tracks`; `tracks` became `items` in playlist
objects). Both spellings are read, so an answer in the older shape still works.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from musicorg import config
from musicorg.errors import ReplayMissError, UserError

log = logging.getLogger(__name__)

SERVICE = "spotify"
AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"  # secrets-ok: a public address
API = "https://api.spotify.com/v1"
# Read the owner's playlists (their own, and ones they collaborate on) and their Liked
# Songs. Nothing that changes anything, and nothing about what they play.
SCOPES = ("playlist-read-private", "playlist-read-collaborative", "user-library-read")
# Spotify sends the browser back here. It must be registered for the owner's app
# exactly as written, and "localhost" isn't accepted: it has to be 127.0.0.1.
REDIRECT_PORT = 36463
REDIRECT_PATH = "/callback"
SIGN_IN_WAIT_S = 300.0  # how long the listener waits for the browser to come back
TIMEOUT_S = 20
PAGE = 50  # the most Spotify gives in one answer
MAX_TRACKS = 3000  # read from one playlist (sixty requests)
PAGE_PAUSE_S = 0.2  # between the pages of one playlist
LIKED = "liked"  # our own name for Liked Songs, which isn't a playlist to Spotify
CLIENT_ID = re.compile(r"[0-9A-Za-z]{32}")
PLAYLIST_ID = re.compile(r"[0-9A-Za-z]{10,40}")
REPLAY_ENV = "MUSICORG_REPLAY_DIR"

Done = Callable[[str | None], None]  # None: signed in. Otherwise why not, in plain words.


class SpotifyError(UserError):
    """Spotify said no, or couldn't be reached. The message is for the owner."""


# ---- the one place the network is reached ------------------------------------------------


def _http(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    data: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], Any]:
    """One request to Spotify: (status, headers, the JSON body or None)."""
    if os.environ.get(REPLAY_ENV):
        raise ReplayMissError("Replay mode: Spotify is never reached. Stand in for spotify._http.")
    import requests

    try:
        response = requests.request(
            method, url, headers=headers, params=params, data=data, timeout=TIMEOUT_S
        )
    except requests.exceptions.RequestException:
        # Not the exception's own words: they can hold the address that was asked for.
        raise SpotifyError(
            "Spotify couldn't be reached. Check the internet connection and try again."
        ) from None
    try:
        body = response.json()
    except ValueError:
        body = None
    return response.status_code, {k.lower(): v for k, v in response.headers.items()}, body


# ---- what's saved ------------------------------------------------------------------------

_lock = threading.Lock()
_access: tuple[str, float] | None = None  # an access token, and when it stops working


def redirect_uri(port: int = REDIRECT_PORT) -> str:
    return f"http://127.0.0.1:{port}{REDIRECT_PATH}"


def status() -> dict[str, Any]:
    """Whether Spotify is set up and signed in to, for the app's Settings."""
    account = config.load_accounts().get(SERVICE) or {}
    client_id = account.get("client_id")
    name = account.get("name")
    return {
        "client_id": client_id if isinstance(client_id, str) and client_id else None,
        "signed_in": bool(account.get("refresh_token")),
        "name": name if isinstance(name, str) and name else None,
        "redirect_uri": redirect_uri(),
    }


def sign_out() -> dict[str, Any]:
    """Forget the sign-in on this computer. The Client ID is kept, to sign in again."""
    global _access
    cancel_sign_in()
    account = config.load_accounts().get(SERVICE) or {}
    kept = {"client_id": account["client_id"]} if account.get("client_id") else None
    config.save_account(SERVICE, kept)
    with _lock:
        _access = None
    return status()


def _forget_access() -> None:
    global _access
    with _lock:
        _access = None


# ---- signing in --------------------------------------------------------------------------


def challenge(verifier: str) -> str:
    """PKCE's code challenge: the verifier's SHA-256, in URL-safe base64 without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorize_url(client_id: str, verifier: str, state: str, port: int = REDIRECT_PORT) -> str:
    query = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri(port),
        "code_challenge_method": "S256",
        "code_challenge": challenge(verifier),
        "scope": " ".join(SCOPES),
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(query)}"


class _SignIn:
    """One sign-in on its way: what was sent to Spotify, and the listener for its answer."""

    def __init__(self, client_id: str, port: int, done: Done) -> None:
        self.client_id, self.port, self.done = client_id, port, done
        # 86 characters of letters, digits, "-" and "_": inside what PKCE allows.
        self.verifier = secrets.token_urlsafe(64)
        self.state = secrets.token_urlsafe(24)
        self.finished = threading.Event()
        self.server: HTTPServer | None = None

    @property
    def url(self) -> str:
        return authorize_url(self.client_id, self.verifier, self.state, self.port)

    def is_ours(self, query: dict[str, list[str]]) -> bool:
        """Whether an answer carries this sign-in's own `state`. One that doesn't isn't
        Spotify answering us (another page on this computer, or an old tab): it's
        turned away, and the sign-in carries on waiting."""
        given = (query.get("state") or [""])[0]
        return hmac.compare_digest(given.encode(), self.state.encode())

    def answer(self, query: dict[str, list[str]]) -> tuple[bool, str]:
        """Spotify sent the browser back with `query`. (Signed in?, what to tell them.)"""
        if query.get("error"):
            return False, "Spotify wasn't given permission, so nothing was signed in to."
        code = (query.get("code") or [""])[0]
        if not code:
            return False, "Spotify's answer had no code in it. Start again from Music Organizer."
        try:
            _finish(self.client_id, code, self.verifier, redirect_uri(self.port))
        except UserError as exc:
            return False, exc.message
        return True, "You're signed in. You can close this tab and go back to Music Organizer."


_pending: _SignIn | None = None


def begin_sign_in(
    client_id: str | None,
    done: Done,
    *,
    port: int = REDIRECT_PORT,
    wait_s: float = SIGN_IN_WAIT_S,
) -> str:
    """Start signing in. Returns the address to open in the browser; `done` is called
    once, from another thread, when Spotify has answered (or nobody came back in time).

    `client_id`: the owner's app's, the first time or to change it; None uses the one
    saved before."""
    global _pending
    saved = config.load_accounts().get(SERVICE) or {}
    chosen = (client_id or "").strip() or saved.get("client_id") or ""
    if not CLIENT_ID.fullmatch(chosen):
        raise SpotifyError(
            "That isn't a Spotify Client ID. It's the 32 letters and digits on your app's "
            "page at developer.spotify.com (not the Client secret)."
        )
    if chosen != saved.get("client_id"):
        config.save_account(SERVICE, {"client_id": chosen})  # a new app: the old sign-in goes
        _forget_access()
    cancel_sign_in()
    attempt = _SignIn(chosen, port, done)
    try:
        attempt.server = _Listener(("127.0.0.1", port), attempt)
    except OSError:
        raise SpotifyError(
            f"Another program on this computer is using port {port}, which Spotify's answer "
            "comes back on. Close it, or restart the computer, and try again."
        ) from None
    _pending = attempt
    threading.Thread(
        target=_listen, args=(attempt, wait_s), name="spotify-sign-in", daemon=True
    ).start()
    return attempt.url


def cancel_sign_in() -> None:
    """Stop waiting for a sign-in that was started (a new one takes its place, or the
    engine is stopping). Its `done` isn't called."""
    global _pending
    attempt, _pending = _pending, None
    if attempt is not None:
        attempt.finished.set()


def _listen(attempt: _SignIn, wait_s: float) -> None:
    server = attempt.server
    assert server is not None
    server.timeout = 0.5
    deadline = time.monotonic() + wait_s
    try:
        while not attempt.finished.is_set() and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    global _pending
    if _pending is attempt:  # nobody came back in time (an answer, or a cancel, clears it)
        _pending = None
        attempt.done("Nobody came back from Spotify's sign-in page in time. Try again.")


class _Listener(HTTPServer):
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], attempt: _SignIn) -> None:
        super().__init__(address, _Handler)
        self.attempt = attempt


class _Handler(BaseHTTPRequestHandler):
    server: _Listener

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path != REDIRECT_PATH:
            self._page(404, "Nothing here.")
            return
        global _pending
        attempt = self.server.attempt
        query = parse_qs(url.query)
        if not attempt.is_ours(query):
            self._page(
                400, "That answer wasn't for this sign-in. Start again from Music Organizer."
            )
            return
        signed_in, words = attempt.answer(query)
        self._page(200 if signed_in else 400, words)
        if _pending is attempt:
            _pending = None
            attempt.finished.set()
            attempt.done(None if signed_in else words)

    def _page(self, code: int, words: str) -> None:
        body = (
            "<!doctype html><meta charset='utf-8'><title>Music Organizer</title>"
            "<body style='font: 17px -apple-system, sans-serif; margin: 15% auto; "
            f"max-width: 32em; text-align: center'><p>{_escape(words)}</p></body>"
        ).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        # The request line holds the one-time code: it's never written down.
        log.info("Spotify sign-in: the browser came back.")


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _finish(client_id: str, code: str, verifier: str, redirect: str) -> None:
    """Exchange Spotify's one-time code for tokens, and save the sign-in."""
    global _access
    status_code, _, body = _http(
        "POST",
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    tokens = body if isinstance(body, dict) else {}
    access, refresh = tokens.get("access_token"), tokens.get("refresh_token")
    if status_code != 200 or not isinstance(access, str) or not isinstance(refresh, str):
        raise SpotifyError(f"Spotify didn't finish the sign-in{_said(body)}. Try again.")
    with _lock:
        _access = (access, time.monotonic() + _lifetime(tokens))
    config.save_account(SERVICE, {"client_id": client_id, "refresh_token": refresh})
    try:
        me = _get("/me")
    except UserError as exc:  # signed in all the same; the name is only for show
        log.info("Spotify: couldn't read the account's name: %s", exc.message)
        return
    name = me.get("display_name") or me.get("id")
    if isinstance(name, str) and name:
        config.save_account(
            SERVICE, {"client_id": client_id, "refresh_token": refresh, "name": name}
        )


def _lifetime(tokens: dict[str, Any]) -> float:
    seconds = tokens.get("expires_in")
    usable = seconds if isinstance(seconds, int | float) and seconds > 120 else 3600
    return float(usable) - 60  # asked for again a minute early


# ---- asking Spotify ----------------------------------------------------------------------


def _access_token() -> str:
    global _access
    with _lock:
        if _access is not None and time.monotonic() < _access[1]:
            return _access[0]
    account = config.load_accounts().get(SERVICE) or {}
    client_id, refresh = account.get("client_id"), account.get("refresh_token")
    if not isinstance(client_id, str) or not isinstance(refresh, str) or not refresh:
        raise SpotifyError("Spotify isn't signed in to. Sign in under Settings → Accounts.")
    status_code, _, body = _http(
        "POST",
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id},
    )
    tokens = body if isinstance(body, dict) else {}
    access = tokens.get("access_token")
    if status_code == 400 and tokens.get("error") == "invalid_grant":
        sign_out()
        raise SpotifyError(
            "Your Spotify sign-in has run out. Sign in again under Settings → Accounts."
        )
    if status_code != 200 or not isinstance(access, str):
        raise SpotifyError(f"Spotify wouldn't renew the sign-in{_said(body)}. Try again later.")
    renewed = tokens.get("refresh_token")
    if isinstance(renewed, str) and renewed and renewed != refresh:
        config.save_account(SERVICE, {**account, "refresh_token": renewed})
    with _lock:
        _access = (access, time.monotonic() + _lifetime(tokens))
    return access


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """One question to Spotify's Web API, signed in. `path` is under `API`, or a whole
    address Spotify gave for the next page."""
    url = path if path.startswith(API + "/") else API + path
    if not url.startswith(API + "/"):
        raise SpotifyError("Spotify gave an address for more that isn't Spotify's.")
    status_code, headers, body = 0, {}, None
    for attempt in (1, 2):
        token = _access_token()
        status_code, headers, body = _http(
            "GET", url, headers={"Authorization": f"Bearer {token}"}, params=params
        )
        if status_code != 401 or attempt == 2:
            break
        _forget_access()  # the token ran out early: ask for a new one, once
    if status_code == 200 and isinstance(body, dict):
        return body
    if status_code == 401:
        raise SpotifyError("Spotify didn't accept the sign-in. Sign in again under Settings.")
    if status_code == 403:
        raise SpotifyError(
            f"Spotify refused{_said(body)}. Two usual reasons: the app at "
            "developer.spotify.com belongs to an account without Spotify Premium, or this "
            "Spotify account isn't listed under the app's User Management."
        )
    if status_code == 404:
        raise SpotifyError("Spotify doesn't have that playlist (any more).")
    if status_code == 429:
        wait = headers.get("retry-after", "")
        after = f" in {wait} seconds" if wait.isdigit() else " in a few minutes"
        raise SpotifyError(f"Spotify asked us to slow down. Try again{after}.")
    raise SpotifyError(f"Spotify answered with an error (HTTP {status_code}{_said(body)}).")


def _said(body: Any) -> str:
    """Spotify's own words for what went wrong, when it gave any: ": …"."""
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    words = body.get("error_description")
    if isinstance(error, dict):
        words = error.get("message")
    elif not isinstance(words, str) and isinstance(error, str):
        words = error
    return f': "{words}"' if isinstance(words, str) and words else ""


# ---- the owner's playlists ---------------------------------------------------------------


def playlists(*, pause: Callable[[float], None] = time.sleep) -> list[dict[str, Any]]:
    """The owner's playlists, Liked Songs first: `{id, name, owner, total, readable}`.
    `readable` is False for a playlist of someone else's that the owner only follows:
    Spotify lists it, but doesn't give its songs."""
    me = _get("/me")
    my_id = me.get("id")
    my_name = me.get("display_name") or my_id
    liked = _get("/me/tracks", {"limit": 1})
    found = [
        {"id": LIKED, "name": "Liked Songs", "owner": my_name, "total": _count(liked),
         "readable": True}
    ]  # fmt: skip
    page: dict[str, Any] | None = _get("/me/playlists", {"limit": PAGE})
    while page is not None:
        for item in page.get("items") or []:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
            # "items" since February 2026; "tracks" before.
            inside = (
                item.get("items") if isinstance(item.get("items"), dict) else item.get("tracks")
            )
            found.append({
                "id": item["id"],
                "name": item.get("name") if isinstance(item.get("name"), str) else "Playlist",
                "owner": owner.get("display_name") or owner.get("id"),
                "total": _count(inside),
                "readable": owner.get("id") == my_id or item.get("collaborative") is True,
            })  # fmt: skip
        page = _next(page, pause)
    return found


def playlist(playlist_id: str, *, pause: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """One playlist's name and songs (or Liked Songs, for `LIKED`), each as an import's
    track: a title, artists, album, length and whether it's explicit. Podcast episodes
    and the owner's local files aren't songs Spotify can name, and are left out.
    `more` says the playlist is longer than was read."""
    if playlist_id == LIKED:
        name = "Liked Songs"
        page: dict[str, Any] | None = _get("/me/tracks", {"limit": PAGE})
    elif PLAYLIST_ID.fullmatch(playlist_id):
        about = _get(f"/playlists/{playlist_id}")
        name = about.get("name") if isinstance(about.get("name"), str) else "Playlist"
        page = _get(f"/playlists/{playlist_id}/items", {"limit": PAGE})
    else:
        raise SpotifyError("That isn't a Spotify playlist.")
    tracks: list[dict[str, Any]] = []
    more = False
    while page is not None:
        for entry in page.get("items") or []:
            song = _song(entry)
            if song is not None:
                tracks.append(song)
        if len(tracks) >= MAX_TRACKS:
            more = page.get("next") is not None or len(tracks) > MAX_TRACKS
            break
        page = _next(page, pause)
    return {"name": name or "Playlist", "tracks": tracks[:MAX_TRACKS], "more": more}


def _next(page: dict[str, Any], pause: Callable[[float], None]) -> dict[str, Any] | None:
    following = page.get("next")
    if not isinstance(following, str) or not following:
        return None
    pause(PAGE_PAUSE_S)
    return _get(following)


def _count(paging: Any) -> int | None:
    total = paging.get("total") if isinstance(paging, dict) else None
    return total if isinstance(total, int) and not isinstance(total, bool) else None


def _song(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict) or entry.get("is_local") is True:
        return None
    # "item" since February 2026; "track" before, and still in Liked Songs.
    track = entry.get("item") if isinstance(entry.get("item"), dict) else entry.get("track")
    if not isinstance(track, dict) or track.get("type", "track") != "track":
        return None
    title = track.get("name")
    if not isinstance(title, str) or not title.strip() or track.get("is_local") is True:
        return None
    artists = [
        artist["name"]
        for artist in track.get("artists") or []
        if isinstance(artist, dict) and isinstance(artist.get("name"), str) and artist["name"]
    ]
    album = track.get("album") if isinstance(track.get("album"), dict) else {}
    length = track.get("duration_ms")
    explicit = track.get("explicit")
    return {
        "title": title,
        "artists": artists,
        "album": album.get("name") if isinstance(album.get("name"), str) else None,
        "duration_s": round(length / 1000)
        if isinstance(length, int | float) and not isinstance(length, bool)
        else None,
        "is_explicit": explicit if isinstance(explicit, bool) else None,
    }
