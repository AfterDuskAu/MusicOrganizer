"""The one gate to YouTube (CLAUDE.md rule 8).

- `search_songs(query)`: YouTube Music's "songs" search, as `Candidate`s.
- `get_track(video_id)`: one track, for a link the owner pastes (step 07).
- `get_album(browse_id)` and `find_track(album, ...)`: album artist, year and track
  numbers for downloads (step 09a).
- `download_audio(video_id, dest_dir)`: format 140 through yt-dlp (step 09a). Only the
  queue calls it (rule 8: downloads happen only through the throttled queue).

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

from musicorg import tools
from musicorg.config import app_dirs, ensure_app_dir
from musicorg.errors import (
    DownloadError,
    FormatUnavailableError,
    ReplayMissError,
    VideoUnavailableError,
    YouTubeError,
    YouTubePausedError,
    YouTubeRefusedError,
)
from musicorg.naming import STAGING_DIR
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


ALBUM_KEEP = ("title", "artists", "year", "trackCount", "isExplicit", "thumbnails")
ALBUM_TRACK_KEEP = ("videoId", "title", "trackNumber", "duration_seconds", "duration",
                    "isExplicit", "videoType")  # fmt: skip


def get_album(browse_id: str, *, cache: SearchCache | None = None) -> Album:
    """An album's details. With `cache` (the index), an answer younger than 30 days is
    reused, so a library with many songs from one album asks YouTube Music once (step
    09c). Only the fields the engine uses are kept (thumbnails too, for step 10's art)."""
    key = f"album {browse_id}"
    raw = None if cache is None else cache.cached_search(key, max_age_days=SEARCH_CACHE_DAYS)
    if raw is None:
        raw = _fetch("album", browse_id, lambda client: client.get_album(browse_id))
        if cache is not None and isinstance(raw, dict) and isinstance(raw.get("tracks"), list):
            kept = {k: raw.get(k) for k in ALBUM_KEEP}
            kept["tracks"] = [
                {k: t.get(k) for k in ALBUM_TRACK_KEEP}
                for t in raw["tracks"]
                if isinstance(t, dict)
            ]
            cache.put_search(key, kept)
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


# ---- downloads (step 09a) --------------------------------------------------------------

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
DOWNLOAD_FORMAT = "140"  # AAC in M4A, ~128 kbps. No fallback (CLAUDE.md rule 6).
SOCKET_TIMEOUT_S = 30

# What yt-dlp's error messages say, lower-cased, checked in this order.
_REFUSED = ("not a bot", "http error 429", "too many requests")
_UNAVAILABLE = (
    "confirm your age", "age-restricted", "age restricted", "inappropriate for some users",
    "private video", "video unavailable", "this video is unavailable",
    "this video is not available", "has been removed", "been terminated", "your country",
    "geo restrict", "members-only", "members only", "join this channel", "premieres in",
    "live event will begin", "copyright",
)  # fmt: skip
_FORMAT = ("requested format is not available",)
_EMPTY = ("downloaded file is empty",)
_NETWORK = (
    "http error 403", "http error 5", "timed out", "timeout", "connection", "unreachable",
    "name resolution", "getaddrinfo", "ssl", "incompleteread", "remote end closed",
    "unable to download",
)  # fmt: skip

ProgressHook = Callable[[int, int | None], None]


def download_audio(
    video_id: str, dest_dir: Path, *, progress: ProgressHook | None = None
) -> tuple[Path, dict[str, Any]]:
    """Download a video's audio as format 140 into `dest_dir` (a folder from
    `fileops.stage_dir`; yt-dlp writes nowhere else, `.part` files included). Returns the
    file and yt-dlp's info; the caller reads the delivered format from
    `info["format_id"]` and never assumes 140.

    Raises YouTubeRefusedError (the queue pauses), VideoUnavailableError (only this job),
    FormatUnavailableError (format 140 not offered), or DownloadError (`network` set for
    network-level failures). Goes through the shared rate limiter.
    """
    if not VIDEO_ID.fullmatch(video_id):
        raise YouTubeError(f"{video_id!r} isn't a YouTube video id.")
    dest = Path(dest_dir)
    if not dest.is_dir() or STAGING_DIR not in dest.parts:
        raise ValueError(f"{dest} isn't a folder in _Staging (use fileops.stage_dir)")
    opts = download_options(dest, progress)
    url = f"https://www.youtube.com/watch?v={video_id}"

    def run() -> Any:
        with _make_ydl(opts) as ydl:
            return ydl.extract_info(url, download=True)

    try:
        info = limiter().call(run)
    except (YouTubePausedError, ReplayMissError):
        raise
    except Exception as exc:
        raise download_problem(video_id, exc) from exc
    if not isinstance(info, dict):
        raise DownloadError(f"The download of {video_id} gave an answer we can't read.")
    return _downloaded_file(info, dest, video_id), info


def download_options(dest: Path, progress: ProgressHook | None = None) -> dict[str, Any]:
    """yt-dlp's options for one download (names checked against yt-dlp 2026.08.19)."""

    def hook(status: dict[str, Any]) -> None:
        if progress is not None and status.get("status") in ("downloading", "finished"):
            done = status.get("downloaded_bytes") or 0
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            progress(int(done), int(total) if total else None)

    return {
        "format": DOWNLOAD_FORMAT,
        "paths": {"home": str(dest), "temp": str(dest)},
        "outtmpl": {"default": "%(id)s.%(ext)s"},
        "ffmpeg_location": str(tools.require("ffmpeg")),
        "js_runtimes": {"deno": {"path": str(tools.require("deno"))}},
        "cachedir": str(ensure_app_dir(app_dirs().cache) / "yt-dlp"),
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "logger": _YtDlpLog(),
        "progress_hooks": [hook],
        "postprocessors": [],  # none; yt-dlp's own M4A container fix-up still runs
        "overwrites": False,
        # The file's time is when it was downloaded, not YouTube's Last-Modified date, so
        # a download kept in _Staging for 24 hours isn't cleaned up at once (step 09b).
        # Already off when yt-dlp is used from Python; said here so it stays off.
        "updatetime": False,
        "socket_timeout": SOCKET_TIMEOUT_S,
    }


def download_problem(video_id: str, exc: BaseException) -> Exception:
    """The engine's error for what yt-dlp raised, by its message."""
    text = str(exc)
    low = text.lower()
    detail = _short_detail(text)
    if any(s in low for s in _REFUSED):
        return YouTubeRefusedError(
            "YouTube asked us to slow down (it wants to check we're not a bot), so downloads "
            "are paused. They carry on by themselves later; nothing is lost."
        )
    if any(s in low for s in _UNAVAILABLE):
        return VideoUnavailableError(f"YouTube won't let {video_id} be downloaded: {detail}")
    if any(s in low for s in _FORMAT):
        return FormatUnavailableError(
            f"YouTube didn't offer the usual audio format (140, AAC) for {video_id}. No "
            "other format is used."
        )
    if any(s in low for s in _EMPTY):
        return DownloadError(f"The download of {video_id} came back empty.")
    if any(s in low for s in _NETWORK):
        return DownloadError(
            f"The download of {video_id} failed on the network: {detail}", network=True
        )
    return DownloadError(f"The download of {video_id} didn't work: {detail}")


def _short_detail(text: str) -> str:
    """yt-dlp's message without "ERROR: [youtube] <id>: ", first line only."""
    first = (text.strip().splitlines() or [""])[0]
    first = re.sub(r"^(?:ERROR:\s*)?(?:\[[^\]]+\]\s*)?(?:[A-Za-z0-9_-]{11}:\s*)?", "", first)
    return first.strip() or "no details"


def _downloaded_file(info: dict[str, Any], dest: Path, video_id: str) -> Path:
    found: list[Path] = []
    for entry in info.get("requested_downloads") or []:
        if isinstance(entry, dict) and entry.get("filepath"):
            found.append(Path(entry["filepath"]))
    if not found:
        found = [
            p
            for p in dest.glob(f"{glob_escape(video_id)}.*")
            if not p.name.endswith((".part", ".ytdl", ".temp"))
        ]
    root = dest.resolve()
    for path in found:
        real = path.resolve()
        if real.parent != root:
            raise DownloadError(f"The download of {video_id} ended up outside its folder.")
        if real.is_file() and real.stat().st_size > 0:
            return real
    raise DownloadError(f"The download of {video_id} came back empty.")


def glob_escape(text: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", text)


class _YtDlpLog:
    """yt-dlp's messages go to the engine's log, never to stdout."""

    def debug(self, message: str) -> None:
        log.debug("yt-dlp: %s", message)

    def info(self, message: str) -> None:
        log.debug("yt-dlp: %s", message)

    def warning(self, message: str) -> None:
        log.info("yt-dlp warning: %s", message)

    def error(self, message: str) -> None:
        log.info("yt-dlp error: %s", message)  # raised to the caller as well


def _make_ydl(opts: dict[str, Any]) -> Any:
    """A yt-dlp downloader. Tests replace this; in replay mode it refuses, so a test that
    forgot to never reaches the network."""
    if os.environ.get(REPLAY_ENV):
        raise ReplayMissError("Replay mode: downloads aren't recorded; stub youtube._make_ydl.")
    import yt_dlp

    return yt_dlp.YoutubeDL(opts)


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
