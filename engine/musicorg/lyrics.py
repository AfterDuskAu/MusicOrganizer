"""Lyrics (step 10): synced lyrics for the `.lrc` sidecar, plain lyrics for the tags.

The owner asked for every source that allows it (2026-09-30), tried in this order:

1. **LRCLIB** (lrclib.net, free, no key): `GET /api/get` with `track_name`,
   `artist_name`, `album_name` (left out when the album is unknown) and `duration` in
   seconds; 404 when it has nothing. Then `GET /api/search` with `track_name` and
   `artist_name`, accepting a result only if its length is within 2 s and its artist and
   title compare equal. Fields: `trackName`, `artistName`, `albumName`, `duration`,
   `instrumental`, `plainLyrics`, `syncedLyrics`. Checked against the research's 1,357
   saved LRCLIB answers (2026-09-29/30). At most one request a second, with a
   descriptive User-Agent, as LRCLIB asks.
2. **YouTube Music** (the lyrics its app shows, supplied by Musixmatch or LyricFind),
   through `youtube.get_lyrics`, for a track whose official videoId is known. Often
   timed.

Genius and Musixmatch's own API need paid keys or forbid automatic copying, so they
aren't used.

Rules:

- **Version guard:** a track with a remix, live, sped up, slowed, nightcore or extended
  token whose LRCLIB record's title lacks that token gets plain lyrics only
  (`version_uncertain`). YouTube Music's lyrics belong to the exact track, so they
  aren't guarded this way.
- **Timing guard:** synced lyrics are only written when the file is within 2 s of the
  length they were timed for. A rip with a music-video intro (the owner's own audio,
  step 09c) would otherwise show every line too early. It gets plain lyrics instead.
- Every synced text is checked: each line's time parses, times never go backwards, and
  only `[ar:]`, `[ti:]` and `[offset:]` headers appear. Anything else is rejected.
- Answers (including "not found") are cached in the index for 30 days. Replay mode
  (`MUSICORG_REPLAY_DIR`) answers from recordings and never reaches the network.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from musicorg import __version__, youtube
from musicorg.errors import ReplayMissError, YouTubeError
from musicorg.normalize import compare_key

log = logging.getLogger(__name__)

API = "https://lrclib.net/api/"
USER_AGENT = f"MusicOrganizer/{__version__} (https://github.com/AfterDuskAu/MusicOrganizer)"
TIMEOUT_S = 30
MIN_INTERVAL_S = 1.0  # LRCLIB asks for no more than about one request a second
CACHE_DAYS = 30
DURATION_TOLERANCE_S = 2
GUARDED_TOKENS = ("remix", "live", "sped up", "slowed", "nightcore", "extended")
HEADERS_ALLOWED = ("ar", "ti", "offset")
UNKNOWN_ALBUMS = frozenset({"", "unsorted", "unknown album"})

STATUSES = ("synced", "plain", "instrumental", "not_found")

_LINE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\](.*)")
_HEADER = re.compile(r"\[([a-z]+):(.*)\]\s*", re.IGNORECASE)


@dataclass(frozen=True)
class Query:
    """What's known about the track whose lyrics are wanted."""

    title: str
    artist: str
    album: str | None
    duration_s: float | None  # of the file itself
    video_id: str | None = None  # its official YouTube Music track, if known
    official_s: float | None = None  # the official track's length, if known
    versions: tuple[str, ...] = ()  # MUSICORG_VERSION tokens, e.g. "remix:adventure club"


@dataclass(frozen=True)
class Found:
    """What was found: `status` is synced, plain, instrumental or not_found. `synced` is
    checked LRC text. `note` says why synced lyrics weren't used, if they weren't."""

    status: str
    plain: str | None = None
    synced: str | None = None
    source: str | None = None
    note: str | None = None


def find(query: Query, *, cache: Any | None = None) -> Found:
    """Lyrics for one track, from LRCLIB then YouTube Music (see the module docstring)."""
    plain: tuple[str, str] | None = None  # (text, source)
    note: str | None = None
    record = _lrclib(query, cache)
    if record is not None:
        if record.get("instrumental"):
            return Found("instrumental", source="LRCLIB")
        synced = check_lrc(record.get("syncedLyrics"))
        text = _text(record.get("plainLyrics")) or _plain_from(synced)
        why = _version_problem(query, str(record.get("trackName") or "")) or _timing_problem(
            query, _number(record.get("duration"))
        )
        if synced and not why:
            return Found("synced", plain=text, synced=synced, source="LRCLIB")
        note = why or note
        if text:
            plain = (text, "LRCLIB")

    if query.video_id:
        try:
            track = youtube.get_lyrics(query.video_id, cache=cache)
        except (YouTubeError, ReplayMissError) as exc:
            log.info("No YouTube Music lyrics for %s: %s", query.video_id, exc)
            track = None
        if track is not None:
            source = f"YouTube Music ({track.source})" if track.source else "YouTube Music"
            timing = _timing_problem(query, query.official_s)
            if track.lines and not timing:
                synced = check_lrc(to_lrc(track.lines))
                if synced:
                    return Found("synced", plain=track.plain, synced=synced, source=source)
            note = note or timing
            if plain is None and track.plain:
                plain = (track.plain, source)

    if plain is not None:
        return Found("plain", plain=plain[0], source=plain[1], note=note)
    return Found("not_found")


# ---- LRCLIB ----------------------------------------------------------------------------


def _lrclib(query: Query, cache: Any | None) -> dict[str, Any] | None:
    """LRCLIB's record for the track: /api/get, then /api/search. None if neither has it."""
    if not query.title or not query.artist:
        return None
    seconds = query.duration_s or query.official_s
    if seconds:
        params: dict[str, Any] = {"track_name": query.title, "artist_name": query.artist,
                                  "duration": int(round(seconds))}  # fmt: skip
        if query.album and query.album.strip().casefold() not in UNKNOWN_ALBUMS:
            params["album_name"] = query.album
        found = _call("get", params, cache)
        if isinstance(found, dict) and found.get("id"):
            return found
    results = _call("search", {"track_name": query.title, "artist_name": query.artist}, cache)
    if not isinstance(results, list):
        return None
    for record in results:
        if isinstance(record, dict) and _same_track(query, record):
            return record
    return None


def _same_track(query: Query, record: dict[str, Any]) -> bool:
    seconds = query.duration_s or query.official_s
    duration = _number(record.get("duration"))
    if seconds is None or duration is None or abs(duration - seconds) > DURATION_TOLERANCE_S:
        return False
    return compare_key(str(record.get("artistName") or "")) == compare_key(query.artist) and (
        compare_key(str(record.get("trackName") or "")) == compare_key(query.title)
    )


class _Limiter:
    def __init__(self, interval: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:  # fmt: skip
        self.interval, self._clock, self._sleep = interval, clock, sleep
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            if now < self._next_at:
                self._sleep(self._next_at - now)
            self._next_at = self._clock() + self.interval


_LIMITER = _Limiter(MIN_INTERVAL_S)


def _call(path: str, params: dict[str, Any], cache: Any | None) -> Any:
    """One LRCLIB request: from the cache, a recording (replay mode), or the network. A
    404 is an answer ("not found") and is cached like any other."""
    key = f"lrclib {path} " + "&".join(f"{k}={params[k]}" for k in sorted(params))
    if cache is not None:
        hit = cache.cached_search(key, max_age_days=CACHE_DAYS)
        if hit is not None:
            return hit.get("body") if isinstance(hit, dict) else None
    replay = os.environ.get(youtube.REPLAY_ENV)
    if replay:
        body = youtube.read_recording(Path(replay), "lrclib", key)
    else:
        body = _live(path, params)
    if cache is not None:
        cache.put_search(key, {"body": body})
    return body


def _live(path: str, params: dict[str, Any]) -> Any:
    import requests

    _LIMITER.wait()
    try:
        response = requests.get(API + path, params=params, timeout=TIMEOUT_S,
                                headers={"User-Agent": USER_AGENT})  # fmt: skip
    except requests.exceptions.RequestException as exc:
        raise YouTubeError(f"LRCLIB couldn't be reached: {exc}") from exc
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise YouTubeError(f"LRCLIB answered HTTP {response.status_code}; try again later.")
    try:
        return response.json()
    except ValueError as exc:
        raise YouTubeError("LRCLIB gave an answer we can't read.") from exc


# ---- guards ----------------------------------------------------------------------------


def _version_problem(query: Query, track_name: str) -> str | None:
    """A guarded version token on the track that LRCLIB's record title lacks."""
    name = track_name.casefold()
    for token in query.versions:
        kind = token.split(":", 1)[0].strip().casefold()
        if kind in GUARDED_TOKENS and kind not in name:
            return f"version_uncertain: the track is a {kind} version, LRCLIB's record isn't"
    return None


def _timing_problem(query: Query, timed_for: float | None) -> str | None:
    """Synced lyrics timed for a different length than the file (e.g. a video intro)."""
    if query.duration_s is None or timed_for is None:
        return None
    if abs(query.duration_s - timed_for) > DURATION_TOLERANCE_S:
        return (f"timing: the file is {query.duration_s:.0f} s, the lyrics were timed for "
                f"{timed_for:.0f} s")  # fmt: skip
    return None


# ---- LRC -------------------------------------------------------------------------------


def check_lrc(text: Any) -> str | None:
    """`text` if it's valid LRC, else None: every non-blank line is `[mm:ss.xx]words` or
    an `[ar:]`, `[ti:]` or `[offset:]` header, and the times never go backwards."""
    if not isinstance(text, str) or not text.strip():
        return None
    last = -1.0
    timed = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        stamp = _LINE.fullmatch(line)
        if stamp is not None:
            minutes, seconds, fraction = stamp.group(1), stamp.group(2), stamp.group(3) or "0"
            if int(seconds) >= 60:
                return None
            at = int(minutes) * 60 + int(seconds) + int(fraction) / 10 ** len(fraction)
            if at < last:
                return None
            last = at
            timed += 1
            continue
        header = _HEADER.fullmatch(line)
        if header is None or header.group(1).casefold() not in HEADERS_ALLOWED:
            return None
    return text if timed else None


def to_lrc(lines: Iterable[tuple[int, str]]) -> str:
    """Timed lines (milliseconds, text) as LRC."""
    out = []
    for ms, words in lines:
        centis = max(0, int(ms)) // 10
        out.append(f"[{centis // 6000:02d}:{centis // 100 % 60:02d}.{centis % 100:02d}]{words}")
    return "\n".join(out) + "\n"


def _plain_from(synced: str | None) -> str | None:
    if not synced:
        return None
    words = [m.group(4).strip() for m in map(_LINE.fullmatch, synced.splitlines()) if m]
    return "\n".join(words).strip() or None


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
