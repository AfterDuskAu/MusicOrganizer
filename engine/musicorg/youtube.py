"""The one gate to YouTube (CLAUDE.md rule 8). Step 06: search and metadata only.

- `search_songs(query)`: YouTube Music's "songs" search, as `Candidate`s.
- `get_track(video_id)`: one track, for a link the owner pastes (step 07).
- `get_album(browse_id)` and `find_track(album, ...)`: album artist, year and track
  numbers for downloads (step 09a).

Every request goes through one rate limiter per process (`limiter()`): at most one
request per 1.5 s (±0.5 s jitter), exponential backoff when YouTube refuses (HTTP 429)
or the connection fails, and after 3 such failures in a row a `YouTubePausedError`.

**Replay mode:** with MUSICORG_REPLAY_DIR set, answers come only from responses recorded
there (scripts/record_ytm.py writes them), and a request with no recording raises
`ReplayMissError`. Tests always run in replay mode, so they never reach the network.

Facts from the recorded responses (ytmusicapi 1.12.3), which the code relies on:
- Song search results carry `videoId`, `title`, `artists[].name`, `album.name`/`.id`,
  `duration_seconds`, `isExplicit`, `videoType` and `thumbnails`. Official audio tracks
  are `MUSIC_VIDEO_TYPE_ATV`. `limit` is a minimum: 20 results come back for 10.
- `get_watch_playlist` tracks have `length` ("3:55"), not `duration_seconds`, and no
  `isExplicit`.
- An album's tracks can carry *different* videoIds from the ones search gives for the
  same songs (often the music video, `MUSIC_VIDEO_TYPE_OMV`), so `find_track` falls back
  to the title and duration. There's no disc number.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, TypeVar

from musicorg.errors import ReplayMissError, YouTubeError, YouTubePausedError
from musicorg.normalize import compare_key

log = logging.getLogger(__name__)

REPLAY_ENV = "MUSICORG_REPLAY_DIR"
OFFICIAL_AUDIO = "MUSIC_VIDEO_TYPE_ATV"
SEARCH_CACHE_DAYS = 30

MIN_INTERVAL_S = 1.5
JITTER_S = 0.5
MAX_FAILURES = 3  # in a row, then pause
BACKOFF_S = 5.0  # after the first failure; doubles after each further one
PAUSE = timedelta(minutes=30)

T = TypeVar("T")


# ---- what comes back -------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """A YouTube Music track that might be a rip's official version."""

    video_id: str
    title: str
    artists: tuple[str, ...]
    album: str | None = None
    album_browse_id: str | None = None
    duration_s: int | None = None
    is_explicit: bool | None = None  # None: YouTube Music didn't say
    video_type: str | None = None
    year: str | None = None
    thumbnail: str | None = None  # the largest

    @property
    def is_official_audio(self) -> bool:
        return self.video_type == OFFICIAL_AUDIO

    @property
    def link(self) -> str:
        return f"https://music.youtube.com/watch?v={self.video_id}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["artists"] = list(self.artists)
        data["is_official_audio"] = self.is_official_audio
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Candidate:
        return cls(
            video_id=data["video_id"],
            title=data["title"],
            artists=tuple(data.get("artists") or ()),
            album=data.get("album"),
            album_browse_id=data.get("album_browse_id"),
            duration_s=data.get("duration_s"),
            is_explicit=data.get("is_explicit"),
            video_type=data.get("video_type"),
            year=data.get("year"),
            thumbnail=data.get("thumbnail"),
        )


@dataclass(frozen=True)
class AlbumTrack:
    video_id: str | None
    title: str
    track_number: int | None
    duration_s: int | None
    is_explicit: bool | None
    video_type: str | None


@dataclass(frozen=True)
class Album:
    browse_id: str
    title: str
    artists: tuple[str, ...]
    year: str | None
    track_count: int | None
    is_explicit: bool | None
    tracks: tuple[AlbumTrack, ...]


class SearchCache(Protocol):
    """Where raw search responses are kept (the library index's `search_cache`)."""

    def cached_search(self, key: str, *, max_age_days: float) -> Any | None: ...

    def put_search(self, key: str, response: Any) -> None: ...


# ---- the calls -------------------------------------------------------------------------


def query_key(query: str) -> str:
    """The form a search is cached and recorded under: `compare_key` of the query."""
    return compare_key(query)


def search_songs(
    query: str, limit: int = 10, *, cache: SearchCache | None = None, refresh: bool = False
) -> list[Candidate]:
    """YouTube Music's song search. A cached response younger than 30 days is used
    unless `refresh`; a fresh one is cached."""
    key = query_key(query)
    raw = (
        None
        if refresh or cache is None
        else cache.cached_search(key, max_age_days=SEARCH_CACHE_DAYS)
    )
    if raw is None:
        raw = _fetch(
            "search", key, lambda client: client.search(query, filter="songs", limit=limit)
        )
        if cache is not None:
            cache.put_search(key, raw)
    if not isinstance(raw, list):
        raise YouTubeError(f"YouTube Music's search for {query!r} gave an answer we can't read.")
    found = [c for c in (_from_search(r) for r in raw) if c is not None]
    return found[:limit]


def get_track(video_id: str) -> Candidate | None:
    """One track by its videoId, or None if YouTube Music doesn't have it."""
    from ytmusicapi.exceptions import YTMusicServerError

    try:
        raw = _fetch(
            "watch", video_id, lambda client: client.get_watch_playlist(videoId=video_id, limit=1)
        )
    except YTMusicServerError as exc:
        # A video that doesn't exist: "No content returned by the server".
        if is_slow_down(exc):
            raise
        log.info("get_track(%s): %s", video_id, exc)
        return None
    tracks = raw.get("tracks") if isinstance(raw, dict) else None
    if not tracks:
        return None
    track = tracks[0]
    if track.get("videoId") != video_id:
        # YouTube Music answered with a different track (e.g. the video is gone).
        log.info("get_track(%s) returned %s instead", video_id, track.get("videoId"))
        return None
    return _from_track(track, duration=parse_length(track.get("length")))


def get_album(browse_id: str) -> Album:
    raw = _fetch("album", browse_id, lambda client: client.get_album(browse_id))
    if not isinstance(raw, dict) or not isinstance(raw.get("tracks"), list):
        raise YouTubeError(f"YouTube Music's album {browse_id} gave an answer we can't read.")
    tracks = tuple(
        AlbumTrack(
            video_id=t.get("videoId"),
            title=t.get("title") or "",
            track_number=_int(t.get("trackNumber")),
            duration_s=_int(t.get("duration_seconds")) or parse_length(t.get("duration")),
            is_explicit=t.get("isExplicit") if isinstance(t.get("isExplicit"), bool) else None,
            video_type=t.get("videoType"),
        )
        for t in raw["tracks"]
        if isinstance(t, dict)
    )
    return Album(
        browse_id=browse_id,
        title=raw.get("title") or "",
        artists=_names(raw.get("artists")),
        year=str(raw["year"]) if raw.get("year") else None,
        track_count=_int(raw.get("trackCount")),
        is_explicit=raw.get("isExplicit") if isinstance(raw.get("isExplicit"), bool) else None,
        tracks=tracks,
    )


def find_track(
    album: Album, *, video_id: str, title: str, duration_s: int | None
) -> tuple[int | None, int | None]:
    """(track number, track count) for a song on an album: by videoId, else by the one
    track with the same normalised title and a duration within 2 s. Never a guess: when
    neither finds exactly one, both are None and it's logged."""
    total = album.track_count or (len(album.tracks) or None)
    for track in album.tracks:
        if track.video_id == video_id and track.track_number:
            return track.track_number, total
    if duration_s is not None:
        key = compare_key(title)
        same = [
            t
            for t in album.tracks
            if t.track_number
            and compare_key(t.title) == key
            and t.duration_s is not None
            and abs(t.duration_s - duration_s) <= 2
        ]
        if len(same) == 1:
            return same[0].track_number, total
    log.info("Couldn't place %s (%r) on the album %s; no track number", video_id, title,
             album.browse_id)  # fmt: skip
    return None, None


def parse_length(text: Any) -> int | None:
    """ "3:07" → 187, "1:02:03" → 3723; None for anything else."""
    if not isinstance(text, str) or not re.fullmatch(r"\d+(?::\d{1,2}){1,2}", text.strip()):
        return None
    seconds = 0
    for part in text.strip().split(":"):
        seconds = seconds * 60 + int(part)
    return seconds


# ---- responses → values ----------------------------------------------------------------


def _from_search(result: Any) -> Candidate | None:
    if not isinstance(result, dict) or result.get("resultType", "song") != "song":
        return None
    duration = _int(result.get("duration_seconds")) or parse_length(result.get("duration"))
    return _from_track(result, duration=duration)


def _from_track(track: dict[str, Any], *, duration: int | None) -> Candidate | None:
    video_id, title = track.get("videoId"), track.get("title")
    if not isinstance(video_id, str) or not video_id or not isinstance(title, str):
        return None
    album = track.get("album") if isinstance(track.get("album"), dict) else {}
    explicit = track.get("isExplicit")
    thumbnails = [t for t in track.get("thumbnails") or [] if isinstance(t, dict) and t.get("url")]
    largest = max(thumbnails, key=lambda t: t.get("width") or 0, default=None)
    return Candidate(
        video_id=video_id,
        title=title,
        artists=_names(track.get("artists")),
        album=album.get("name"),
        album_browse_id=album.get("id"),
        duration_s=duration,
        is_explicit=explicit if isinstance(explicit, bool) else None,
        video_type=track.get("videoType"),
        year=str(track["year"]) if track.get("year") else None,
        thumbnail=largest["url"] if largest else None,
    )


def _names(artists: Any) -> tuple[str, ...]:
    if not isinstance(artists, list):
        return ()
    return tuple(
        a["name"] for a in artists if isinstance(a, dict) and isinstance(a.get("name"), str)
    )


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# ---- the rate limiter ------------------------------------------------------------------


class RateLimiter:
    """At most one request per `interval` seconds (± `jitter`), shared by every thread.
    `call()` retries with exponential backoff when YouTube refuses or the connection
    fails, and raises `YouTubePausedError` after `max_failures` in a row."""

    def __init__(
        self,
        interval: float = MIN_INTERVAL_S,
        jitter: float = JITTER_S,
        *,
        max_failures: int = MAX_FAILURES,
        backoff: float = BACKOFF_S,
        pause: timedelta = PAUSE,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        uniform: Callable[[float, float], float] = random.uniform,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.interval = interval
        self.jitter = jitter
        self.max_failures = max_failures
        self.backoff = backoff
        self.pause = pause
        self._clock, self._sleep, self._uniform, self._now = clock, sleep, uniform, now
        self._lock = threading.Lock()
        self._next_at = 0.0
        self.failures = 0  # in a row
        self.requests = 0  # made by this process
        self.paused_until: datetime | None = None

    def wait(self) -> None:
        """Wait for this request's turn."""
        with self._lock:
            now = self._clock()
            if now < self._next_at:
                self._sleep(self._next_at - now)
            gap = max(0.0, self.interval + self._uniform(-self.jitter, self.jitter))
            self._next_at = self._clock() + gap
            self.requests += 1

    def call(self, request: Callable[[], T]) -> T:
        if self.paused_until is not None and self._now() < self.paused_until:
            raise paused_error(self.paused_until)
        while True:
            self.wait()
            try:
                result = request()
            except Exception as exc:
                if not is_slow_down(exc):
                    raise
                self.failures += 1
                log.warning("YouTube request failed (%d in a row): %s", self.failures, exc)
                if self.failures >= self.max_failures:
                    self.failures = 0
                    self.paused_until = self._now() + self.pause
                    raise paused_error(self.paused_until) from exc
                self._sleep(self.backoff * 2 ** (self.failures - 1))
                continue
            self.failures = 0
            return result


def is_slow_down(exc: BaseException) -> bool:
    """YouTube refusing us (HTTP 429, or a 5xx), or the network failing, as opposed to a
    request that can never work."""
    import requests
    from ytmusicapi.exceptions import YTMusicServerError

    if isinstance(exc, requests.exceptions.RequestException):
        return True  # connection errors and timeouts
    if isinstance(exc, YTMusicServerError):
        return bool(re.search(r"HTTP (?:429|5\d\d)", str(exc)))
    # ytmusicapi reads the body as JSON before checking the status, so a 429 served as
    # an HTML page arrives as a JSON error.
    return isinstance(exc, json.JSONDecodeError)


def paused_error(resume_at: datetime) -> YouTubePausedError:
    local = resume_at.astimezone()
    clock = local.strftime("%I:%M%p").lstrip("0").lower()
    return YouTubePausedError(
        f"YouTube is slowing us down; try again after {clock}. Everything done so far is "
        "kept, and running the command again carries on where it stopped.",
        resume_at,
    )


_LIMITER = RateLimiter()


def limiter() -> RateLimiter:
    """The process's one rate limiter (thumbnails and downloads use it too)."""
    return _LIMITER


# ---- live calls and replay -------------------------------------------------------------

_client: Any = None
_client_lock = threading.Lock()


def _fetch(kind: str, key: str, live: Callable[[Any], Any]) -> Any:
    replay = os.environ.get(REPLAY_ENV)
    if replay:
        return read_recording(Path(replay), kind, key)
    return fetch_live(live)


def fetch_live(live: Callable[[Any], Any]) -> Any:
    """Run one ytmusicapi call through the rate limiter (also used to record)."""
    return limiter().call(lambda: live(_ytmusic()))


def _ytmusic() -> Any:
    global _client
    with _client_lock:
        if _client is None:
            from ytmusicapi import YTMusic

            _client = YTMusic()  # unauthenticated; no network until the first request
        return _client


def recording_path(folder: Path, kind: str, key: str) -> Path:
    """Where the response to one request is recorded: `<kind>/<slug>-<hash>.json`."""
    slug = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")[:48].strip("-") or "x"
    digest = hashlib.sha1(f"{kind}\n{key}".encode(), usedforsecurity=False).hexdigest()[:10]
    return folder / kind / f"{slug}-{digest}.json"


def read_recording(folder: Path, kind: str, key: str) -> Any:
    path = recording_path(folder, kind, key)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ReplayMissError(
            f"Replay mode: no recorded YouTube Music {kind} for {key!r} (expected {path}). "
            "Record it with scripts/record_ytm.py."
        ) from None
    if record.get("kind") != kind or record.get("key") != key:
        raise ReplayMissError(f"Replay mode: {path} holds a different request.")
    if record.get("error"):
        # The request failed when it was recorded; fail the same way.
        from ytmusicapi.exceptions import YTMusicServerError

        raise YTMusicServerError(record["error"])
    return record["response"]
