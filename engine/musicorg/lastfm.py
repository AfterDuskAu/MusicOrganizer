"""Last.fm, for what the owner has listened to: their most played and loved songs.

Two things use it. Discover starts radios from the owner's most played songs there (a
seed of kind `lastfm`), so years of listening on Spotify or anywhere else that
"scrobbled" to Last.fm count, not only what's been played in this app. And Import
Playlists can bring their Loved Tracks or their most played across as a playlist. It's
the only module that talks to Last.fm, it only asks questions, and never its audio.

**Setting it up.** Last.fm answers about any profile that's public, with no sign-in and
no password. But every question needs an API key, and each app's owner makes their own
(free, at last.fm/api/account/create). The owner pastes that key and their username
once (`connect`). Both are kept on this computer (`accounts.json` in the app's settings
folder, readable by this user only), per profile, never in the repo or the library.

Nothing here is logged or raised with the key or the username in it: `_http` never
passes on the words of a network error, which hold the address that was asked for. In
replay mode (`MUSICORG_REPLAY_DIR`, every test) the network is never reached.

Checked against Last.fm's documentation on 2026-10-03 (`user.getInfo`,
`user.getTopTracks`, `user.getLovedTracks`, with `format=json`). No real answer has been
recorded: there was no key to ask with. What's allowed for, from how Last.fm's JSON is
known to behave: numbers arrive as text ("215"), a list of one arrives as the one thing
and not a list, and a problem is `{"error": <code>, "message"}` with an HTTP error.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from musicorg import config
from musicorg.errors import ReplayMissError, UserError

log = logging.getLogger(__name__)

SERVICE = "lastfm"
API = "https://ws.audioscrobbler.com/2.0/"
USER_AGENT = "MusicOrganizer (a personal music library app)"
API_KEY = re.compile(r"[0-9a-f]{32}")
USER = re.compile(r"[A-Za-z0-9_-]{2,40}")
PROFILE_HOSTS = frozenset({"last.fm", "www.last.fm"})
TIMEOUT_S = 20
PAGE = 200  # songs asked for in one question
MAX_TRACKS = 1000  # read from one list (five questions)
PAGE_PAUSE_S = 0.25  # between pages: Last.fm allows five questions a second
REPLAY_ENV = "MUSICORG_REPLAY_DIR"
PERIODS = ("7day", "1month", "3month", "6month", "12month", "overall")
# The lists Import Playlists offers (docs/ENGINE_API.md → "Last.fm list"), and the name
# the owner's playlist gets.
LISTS = {
    "loved": "Last.fm Loved Tracks",
    "top_7day": "Last.fm Top Tracks (7 Days)",
    "top_1month": "Last.fm Top Tracks (Month)",
    "top_3month": "Last.fm Top Tracks (3 Months)",
    "top_6month": "Last.fm Top Tracks (6 Months)",
    "top_12month": "Last.fm Top Tracks (Year)",
    "top_overall": "Last.fm Top Tracks (All Time)",
}
WHERE = "Settings → Profile → Last.fm"

# Last.fm's error codes (last.fm/api/errorcodes).
NOT_FOUND, BAD_KEY, OFFLINE, UNAVAILABLE, LOGIN_NEEDED, KEY_SUSPENDED, TOO_FAST = (
    6, 10, 11, 16, 17, 26, 29,
)  # fmt: skip


class LastfmError(UserError):
    """Last.fm said no, or couldn't be reached. The message is for the owner."""


# ---- the one place the network is reached ------------------------------------------------


def _http(params: dict[str, Any]) -> tuple[int, Any]:
    """One question to Last.fm: (status, the JSON body or None)."""
    if os.environ.get(REPLAY_ENV):
        raise ReplayMissError("Replay mode: Last.fm is never reached. Stand in for lastfm._http.")
    import requests

    try:
        response = requests.get(
            API, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S
        )
    except requests.exceptions.RequestException:
        # Not the exception's own words: they hold the address, and the key is in it.
        raise LastfmError(
            "Last.fm couldn't be reached. Check the internet connection and try again."
        ) from None
    try:
        body = response.json()
    except ValueError:
        body = None
    return response.status_code, body


def _ask(method: str, key: str, user: str, **more: Any) -> dict[str, Any]:
    status_code, body = _http(
        {"method": method, "user": user, "api_key": key, "format": "json", **more}
    )
    if isinstance(body, dict) and body.get("error") is not None:
        raise LastfmError(_problem(body, key))
    if status_code != 200 or not isinstance(body, dict):
        raise LastfmError(f"Last.fm answered with an error (HTTP {status_code}).")
    return body


def _problem(body: dict[str, Any], key: str) -> str:
    code = body.get("error")
    words = body.get("message") if isinstance(body.get("message"), str) else ""
    if code == BAD_KEY:
        return (
            "Last.fm didn't accept that API key. Copy the one called “API key” (not the "
            "Shared secret) from last.fm/api/accounts."
        )
    if code == KEY_SUSPENDED:
        return "Last.fm has suspended that API key. Make a new one at last.fm/api/account/create."
    if code == TOO_FAST:
        return "Last.fm asked us to slow down. Try again in a minute."
    if code in (OFFLINE, UNAVAILABLE):
        return "Last.fm is having trouble right now. Try again in a few minutes."
    if code == LOGIN_NEEDED:
        return (
            "That Last.fm profile keeps its listening private, so it can't be read. On "
            "last.fm: Settings → Privacy, and turn off hiding your listening."
        )
    if code == NOT_FOUND and "not found" in words.lower():
        return "Last.fm has no user of that name. It's the name in your profile's address."
    said = words.replace(key, "…").strip()
    return (
        f"Last.fm answered with an error: “{said}”."
        if said
        else ("Last.fm answered with an error.")
    )


# ---- what's saved ------------------------------------------------------------------------


def _saved() -> tuple[str, str]:
    """(username, API key), or a LastfmError saying where to set it up."""
    account = config.load_accounts().get(SERVICE) or {}
    user, key = account.get("user"), account.get("api_key")
    if not isinstance(user, str) or not isinstance(key, str) or not user or not key:
        raise LastfmError(f"Last.fm isn't set up yet. Set it up under {WHERE}.")
    return user, key


def status() -> dict[str, Any]:
    """Whether Last.fm is set up, and for whom, for the app's Settings. Never the key."""
    account = config.load_accounts().get(SERVICE) or {}
    user, key = account.get("user"), account.get("api_key")
    has_key = isinstance(key, str) and bool(key)
    name = user if isinstance(user, str) and user else None
    return {"user": name, "has_key": has_key, "connected": has_key and name is not None}


def username(text: str) -> str:
    """A username as typed, or taken from a profile's address (last.fm/user/NAME)."""
    name = text.strip()
    if "/" in name:
        url = urlsplit(name if "://" in name else "https://" + name)
        parts = [part for part in url.path.split("/") if part]
        if (url.hostname or "").lower() in PROFILE_HOSTS and len(parts) >= 2 and parts[0] == "user":
            name = parts[1]
    if not USER.fullmatch(name):
        raise LastfmError(
            "That doesn't look like a Last.fm username. It's the name in your profile's "
            "address: last.fm/user/…"
        )
    return name


def connect(user: str, api_key: str | None = None) -> dict[str, Any]:
    """Save the owner's Last.fm username and API key, once Last.fm has answered for
    them. `api_key`: None keeps the one saved before (to change only the username)."""
    name = username(user)
    saved = config.load_accounts().get(SERVICE) or {}
    key = (api_key or "").strip().lower() or saved.get("api_key") or ""
    if not isinstance(key, str) or not API_KEY.fullmatch(key):
        raise LastfmError(
            "That isn't a Last.fm API key. It's the 32 letters and digits called “API key” "
            "(not the Shared secret) on your API account's page at Last.fm."
        )
    about = _ask("user.getInfo", key, name).get("user")
    spelled = about.get("name") if isinstance(about, dict) else None
    config.save_account(
        SERVICE, {"user": spelled if isinstance(spelled, str) and spelled else name, "api_key": key}
    )
    log.info("Last.fm: set up for this profile.")
    return status()


def forget() -> dict[str, Any]:
    """Forget Last.fm for this profile: the username and the key."""
    config.save_account(SERVICE, None)
    return status()


# ---- the owner's songs there -------------------------------------------------------------


def top_tracks(
    period: str = "overall",
    limit: int = 50,
    *,
    pause: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, Any]], bool]:
    """The owner's most played songs over `period`, the most played first, each as an
    import's track with `plays`. (songs, whether there are more than were read)."""
    if period not in PERIODS:
        raise LastfmError(f"A Last.fm period is one of: {', '.join(PERIODS)}.")
    return _read("user.getTopTracks", "toptracks", limit, pause, period=period)


def loved_tracks(
    limit: int = MAX_TRACKS, *, pause: Callable[[float], None] = time.sleep
) -> tuple[list[dict[str, Any]], bool]:
    """The songs the owner marked as loved, the newest first."""
    return _read("user.getLovedTracks", "lovedtracks", limit, pause)


def playlist(list_id: str, *, pause: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """One of the owner's Last.fm lists (`LISTS`) as an import: its name and songs."""
    if list_id not in LISTS:
        raise LastfmError(f"A Last.fm list is one of: {', '.join(LISTS)}.")
    if list_id == "loved":
        tracks, more = loved_tracks(pause=pause)
    else:
        tracks, more = top_tracks(list_id.removeprefix("top_"), MAX_TRACKS, pause=pause)
    return {"name": LISTS[list_id], "tracks": tracks, "more": more}


def _read(
    method: str, inside: str, limit: int, pause: Callable[[float], None], **more: Any
) -> tuple[list[dict[str, Any]], bool]:
    user, key = _saved()
    limit = max(1, min(limit, MAX_TRACKS))
    tracks: list[dict[str, Any]] = []
    page_number = 1
    while True:
        body = _ask(method, key, user, limit=min(PAGE, limit), page=page_number, **more)
        found = body.get(inside) if isinstance(body.get(inside), dict) else {}
        listed = found.get("track")
        entries = listed if isinstance(listed, list) else [listed]  # one song comes bare
        added = [song for song in (_song(entry) for entry in entries) if song is not None]
        tracks += added
        about = found.get("@attr") if isinstance(found.get("@attr"), dict) else {}
        pages = _number(about.get("totalPages")) or page_number
        if not added or len(tracks) >= limit or page_number >= pages:
            break
        page_number += 1
        pause(PAGE_PAUSE_S)
    return tracks[:limit], len(tracks) > limit or (bool(added) and page_number < pages)


def _number(value: Any) -> int | None:
    """Last.fm's numbers arrive as text."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _song(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    title = entry.get("name")
    if not isinstance(title, str) or not title.strip():
        return None
    by = entry.get("artist")
    artist = (by.get("name") or by.get("#text")) if isinstance(by, dict) else by
    length = _number(entry.get("duration"))
    return {
        "title": title.strip(),
        "artists": [artist.strip()] if isinstance(artist, str) and artist.strip() else [],
        "album": None,
        "duration_s": length if length else None,  # "0" is Last.fm not knowing
        "is_explicit": None,
        "plays": _number(entry.get("playcount")),
    }
