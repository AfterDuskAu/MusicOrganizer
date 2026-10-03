"""Deezer, for reading a public playlist or album by its link (imports). Never its audio.

Deezer's API tells anyone what's in a public playlist or an album: there's no sign-in
and no key, so nothing about the owner is kept or sent. A private playlist can't be
read this way; Deezer says it has no such playlist.

The links the owner pastes:

- the address of a playlist or album page: `https://www.deezer.com/en/playlist/123`,
  with or without the language, or `deezer.com/album/456`;
- a share link from Deezer's apps (`https://link.deezer.com/s/…`), which is followed to
  the page it leads to. Only Deezer's own addresses are followed;
- the number by itself, for a playlist.

Checked live on 2026-10-03: `/playlist/{id}` and `/album/{id}` give a `title`;
`/playlist/{id}/tracks` and `/album/{id}/tracks` give pages of `data` with `total` and
`next` (an address for the next page). Each track has `type` "track", `title` (with its
version, e.g. "Song (Live)"), `duration` in seconds, `explicit_lyrics`, `artist.name`
(the main artist only), and in a playlist `album.title`. A problem is an answer of
`{"error": {"type", "message", "code"}}`, sent with HTTP 200: code 800 ("no data") for a
playlist that isn't there or isn't public, 4 for too many questions at once. Old
`deezer.page.link` share links answer 404: Deezer switched them off in 2025.

Nothing here is written anywhere. In replay mode (`MUSICORG_REPLAY_DIR`, every test)
the network is never reached: `_http` and `_final_address` refuse.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from musicorg.errors import ReplayMissError, UserError

log = logging.getLogger(__name__)

API = "https://api.deezer.com"
PAGE_HOSTS = frozenset({"deezer.com", "www.deezer.com"})
SHARE_HOSTS = frozenset({"link.deezer.com"})
# Firebase's short links, which Deezer's apps gave out until 2025. They don't work now.
OLD_SHARE_HOSTS = frozenset({"deezer.page.link", "dzr.page.link"})
# "/en/playlist/123", "/playlist/123", "/fr-ca/album/456".
PAGE_PATH = re.compile(r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?(playlist|album)/(\d{1,20})/?", re.I)
KINDS = ("playlist", "album")
TIMEOUT_S = 20
PAGE = 100  # songs asked for in one question
MAX_TRACKS = 3000  # read from one playlist (thirty questions)
PAGE_PAUSE_S = 0.2  # between pages: Deezer allows 50 questions in 5 seconds
REPLAY_ENV = "MUSICORG_REPLAY_DIR"
QUOTA = 4
NO_DATA = 800


class DeezerError(UserError):
    """Deezer said no, or couldn't be reached. The message is for the owner."""


# ---- the only places the network is reached ----------------------------------------------


def _http(url: str, params: dict[str, Any] | None = None) -> Any:
    """One question to Deezer's API: the JSON answer, or None."""
    if os.environ.get(REPLAY_ENV):
        raise ReplayMissError("Replay mode: Deezer is never reached. Stand in for deezer._http.")
    import requests

    try:
        response = requests.get(url, params=params, timeout=TIMEOUT_S)
    except requests.exceptions.RequestException:
        raise DeezerError(
            "Deezer couldn't be reached. Check the internet connection and try again."
        ) from None
    if response.status_code != 200:
        raise DeezerError(f"Deezer answered with an error (HTTP {response.status_code}).")
    try:
        return response.json()
    except ValueError:
        return None


def _final_address(url: str) -> str:
    """Where a share link leads, after Deezer's redirects. Nothing is read from the page."""
    if os.environ.get(REPLAY_ENV):
        raise ReplayMissError(
            "Replay mode: Deezer is never reached. Stand in for deezer._final_address."
        )
    import requests

    try:
        with requests.get(url, timeout=TIMEOUT_S, allow_redirects=True, stream=True) as response:
            return str(response.url)
    except requests.exceptions.RequestException:
        raise DeezerError(
            "Deezer couldn't be reached to open that share link. Check the internet "
            "connection and try again."
        ) from None


# ---- which playlist ----------------------------------------------------------------------


def parse_link(link: str) -> tuple[str, str] | None:
    """(`playlist` or `album`, its number) for a page's address or a playlist's number.
    None for a share link, which has to be followed first (`where`)."""
    text = link.strip()
    if not text:
        raise DeezerError("Paste a playlist's link first.")
    if text.isdigit():
        return "playlist", text
    url = urlsplit(text if "://" in text else "https://" + text)
    host = (url.hostname or "").lower()
    if host in OLD_SHARE_HOSTS:
        raise DeezerError(
            "That's one of Deezer's old share links, which Deezer has switched off. Open the "
            "playlist in Deezer and choose Share → Copy link again."
        )
    if host in SHARE_HOSTS:
        return None
    if host not in PAGE_HOSTS:
        raise DeezerError("That isn't a Deezer link.")
    return _from_page(url.path)


def _from_page(path: str) -> tuple[str, str]:
    found = PAGE_PATH.fullmatch(path)
    if found is None:
        raise DeezerError(
            "That link is to a song, an artist or a page, not a playlist or an album. Open "
            "the playlist, then Share → Copy link."
        )
    return found.group(1).lower(), found.group(2)


def where(link: str) -> tuple[str, str]:
    """What a link the owner pasted points at, following a share link if it is one."""
    known = parse_link(link)
    if known is not None:
        return known
    text = link.strip()
    url = urlsplit(text if "://" in text else "https://" + text)
    final = urlsplit(
        _final_address(urlunsplit(("https", url.hostname or "", url.path, url.query, "")))
    )
    if (final.hostname or "").lower() not in PAGE_HOSTS or not PAGE_PATH.fullmatch(final.path):
        raise DeezerError(
            "That share link didn't lead to a playlist or an album on Deezer. Open it in your "
            "browser, and paste the address it ends up at (deezer.com/…/playlist/…)."
        )
    return _from_page(final.path)


# ---- reading it --------------------------------------------------------------------------


def _get(path_or_url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """One question to Deezer: `path_or_url` is under `API`, or an address Deezer gave
    for the next page."""
    url = path_or_url if path_or_url.startswith(API + "/") else API + path_or_url
    if not url.startswith(API + "/"):
        raise DeezerError("Deezer gave an address for more that isn't Deezer's.")
    body = _http(url, params)
    if not isinstance(body, dict):
        raise DeezerError("Deezer gave an answer that can't be read.")
    problem = body.get("error")
    if problem:
        code = problem.get("code") if isinstance(problem, dict) else None
        if code == NO_DATA:
            raise DeezerError(
                "Deezer doesn't have that playlist, or it's private. Only a public playlist can "
                "be read without signing in: in Deezer, make it public, then try again."
            )
        if code == QUOTA:
            raise DeezerError("Deezer asked us to slow down. Try again in a few seconds.")
        words = problem.get("message") if isinstance(problem, dict) else None
        said = f': "{words}"' if isinstance(words, str) and words else ""
        raise DeezerError(f"Deezer answered with an error{said}.")
    return body


def playlist(link: str, *, pause: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """A public playlist or an album, by its link: its name and its songs, each as an
    import's track (a title, the main artist, the album, the length and whether it's
    explicit). `more` says it's longer than was read. One question for its name, and
    one for every hundred songs."""
    kind, number = where(link)
    about = _get(f"/{kind}/{number}")
    name = about.get("title") if isinstance(about.get("title"), str) else None
    album = name if kind == "album" else None
    tracks: list[dict[str, Any]] = []
    more = False
    page: dict[str, Any] | None = _get(f"/{kind}/{number}/tracks", {"index": 0, "limit": PAGE})
    while page is not None:
        for entry in page.get("data") or []:
            song = _song(entry, album)
            if song is not None:
                tracks.append(song)
        if len(tracks) >= MAX_TRACKS:
            more = page.get("next") is not None or len(tracks) > MAX_TRACKS
            break
        following = page.get("next")
        if not isinstance(following, str) or not following:
            break
        pause(PAGE_PAUSE_S)
        page = _get(following)
    return {
        "name": (name or "").strip() or ("Deezer Album" if kind == "album" else "Deezer Playlist"),
        "tracks": tracks[:MAX_TRACKS],
        "more": more,
    }


def _song(entry: Any, album: str | None) -> dict[str, Any] | None:
    if not isinstance(entry, dict) or entry.get("type", "track") != "track":
        return None
    title = entry.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    artist = entry.get("artist") if isinstance(entry.get("artist"), dict) else {}
    name = artist.get("name")
    on = entry.get("album") if isinstance(entry.get("album"), dict) else {}
    length = entry.get("duration")
    explicit = entry.get("explicit_lyrics")
    return {
        "title": title.strip(),
        "artists": [name] if isinstance(name, str) and name else [],
        "album": on.get("title") if isinstance(on.get("title"), str) else album,
        "duration_s": length
        if isinstance(length, int) and not isinstance(length, bool) and length > 0
        else None,
        "is_explicit": explicit if isinstance(explicit, bool) else None,
    }
