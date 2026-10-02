"""The one gate to YouTube (CLAUDE.md rule 8).

- `search_songs(query)`: YouTube Music's "songs" search, as `Candidate`s.
- `get_track(video_id)`: one track, for a link the owner pastes (step 07).
- `get_album(browse_id)` and `find_track(album, ...)`: album artist, year and track
  numbers for downloads (step 09a).
- `download_audio(video_id, dest_dir)`: format 140 through yt-dlp (step 09a). Only the
  queue calls it (rule 8: downloads happen only through the throttled queue).
- `stream(video_id)`, `find_video(title, artist)` and `video(video_id)`: where the app can
  play a song, or its official video, from right now (v0.2). Nothing is downloaded.
- `download_video(video_id, dest_dir, height=...)`: a video as one MP4, its picture
  joined to the format-140 sound without converting either (v0.2). Queue only, like
  `download_audio`.
- `radio(video_id)`, `artist_radio(name)` and `genre_playlist(name)`: songs like a song,
  like an artist, and of a genre, for Discover (v0.4). Lookups only.

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
- A song's radio (`get_watch_playlist(videoId, radio=True)`) is about 50 tracks, the
  song itself first, nearly all official audio. An artist's radio (the `radioId` an
  "artists" search result carries, as a `playlistId`) is about 100, a quarter of them the
  artist's own. `get_mood_playlists` fails on every genre page (a KeyError inside
  ytmusicapi), so a genre is found by searching "featured_playlists"; those playlists
  are mostly music videos, not official audio.
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
from musicorg.normalize import (
    SOFT_VERSION_KINDS,
    Parsed,
    compare_key,
    parse_title,
    render_versions,
)

log = logging.getLogger(__name__)

REPLAY_ENV = "MUSICORG_REPLAY_DIR"
OFFICIAL_AUDIO = "MUSIC_VIDEO_TYPE_ATV"
OFFICIAL_VIDEO = "MUSIC_VIDEO_TYPE_OMV"
SEARCH_CACHE_DAYS = 30
RADIO_CACHE_DAYS = 7  # a radio changes from day to day; a week keeps the picks steady
RADIO_SONGS = 50
ARTIST_RADIO_SONGS = 100
GENRE_PLAYLIST_SONGS = 100

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
    thumbnails: tuple[tuple[str, int, int], ...] = ()  # (url, width, height), smallest first


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
        thumbnails=_thumbnails(raw.get("thumbnails")),
    )


def _thumbnails(raw: Any) -> tuple[tuple[str, int, int], ...]:
    found = [
        (str(t["url"]), _int(t.get("width")) or 0, _int(t.get("height")) or 0)
        for t in raw or []
        if isinstance(t, dict) and t.get("url")
    ]
    return tuple(sorted(found, key=lambda t: t[1] * t[2]))


# ---- lyrics and pictures (step 10) ------------------------------------------------------


def find_video(
    title: str,
    artist: str,
    *,
    versions: tuple[str, ...] = (),
    cache: SearchCache | None = None,
) -> Candidate | None:
    """The official music video YouTube Music has for a song, or None (v0.2). One
    "videos" search, cached for 30 days like a song search.

    Only a video YouTube Music itself marks as the artist's official one
    (`MUSIC_VIDEO_TYPE_OMV`) is taken, and only if it's the same song: the same title
    once "(Official Video)" and the like are set aside, the same version (a remix isn't
    the original), and an artist in common. Uploads by other people (lyric videos,
    fan edits: `MUSIC_VIDEO_TYPE_UGC`) are never taken, so plenty of songs have none.

    `versions` are version tokens known from somewhere other than the title ("remix",
    "remix:somebody", "slowed"): a rip named "Song R" can carry the plain title "Song"
    in its tags, and must not get the original's video. A remix by nobody in particular
    matches no video at all, since which remix it is can't be told.

    Checked against ytmusicapi 1.12.3: a "videos" result carries `resultType` "video",
    `videoId`, `title`, `artists[].name`, `videoType` and `duration_seconds`, and no
    album."""
    wanted = parse_title(title)
    hard = _hard_versions(wanted) | {
        v for v in versions if v.partition(":")[0] not in SOFT_VERSION_KINDS
    }
    extra = render_versions(sorted(hard - _hard_versions(wanted)))
    query = " ".join(part for part in (artist, title, extra) if part).strip()
    key = query_key(query)
    cache_key = f"videos {key}"
    raw = None if cache is None else cache.cached_search(cache_key, max_age_days=SEARCH_CACHE_DAYS)
    if raw is None:
        raw = _fetch("videos", key, lambda client: client.search(query, filter="videos", limit=10))
        if cache is not None:
            cache.put_search(cache_key, raw)
    if not isinstance(raw, list):
        raise YouTubeError(f"YouTube Music's search for {query!r} gave an answer we can't read.")
    for result in raw:
        if not isinstance(result, dict) or result.get("videoType") != OFFICIAL_VIDEO:
            continue
        duration = _int(result.get("duration_seconds")) or parse_length(result.get("duration"))
        found = _from_track(result, duration=duration)
        if found is not None and _same_song(wanted, artist, found, hard):
            return found
    return None


def _same_song(
    wanted: Parsed, artist: str, found: Candidate, versions: set[str] | None = None
) -> bool:
    """`versions`: the song's hard version tokens, when more is known than its title says."""
    theirs = parse_title(found.title)
    if compare_key(wanted.title) != compare_key(theirs.title) or not compare_key(wanted.title):
        return False
    if (_hard_versions(wanted) if versions is None else versions) != _hard_versions(theirs):
        return False
    credit = f" {artist_key(artist)} "
    return any(f" {artist_key(name)} " in credit for name in found.artists if artist_key(name))


def artist_key(name: str) -> str:
    """An artist's name for comparing, without "the": the library may say "Notorious
    B.I.G." where YouTube Music says "The Notorious B.I.G."."""
    key = compare_key(name)
    return " ".join(word for word in key.split() if word != "the") or key


def _hard_versions(parsed: Parsed) -> set[str]:
    """The version words that make it a different recording ("remix", "live"), without
    the ones that don't ("remaster", "explicit")."""
    return {t for t in parsed.version_tokens if t.partition(":")[0] not in SOFT_VERSION_KINDS}


# ---- songs like a song, an artist or a genre (Discover, v0.4) ----------------------------

RADIO_TRACK_KEEP = ("videoId", "title", "artists", "album", "length", "duration",
                    "duration_seconds", "isExplicit", "videoType", "year", "thumbnail",
                    "thumbnails")  # fmt: skip


def trim_tracks(raw: Any) -> dict[str, Any]:
    """A watch playlist's or a playlist's answer, cut down to its tracks and the fields
    the engine reads: what's cached and what's recorded for tests."""
    tracks = raw.get("tracks") if isinstance(raw, dict) else None
    kept = [
        {key: track[key] for key in RADIO_TRACK_KEEP if track.get(key) is not None}
        for track in (tracks if isinstance(tracks, list) else [])
        if isinstance(track, dict)
    ]
    for track in kept:  # one picture each, the largest: a radio is 50 tracks
        for key in ("thumbnail", "thumbnails"):
            pictures = [t for t in track.get(key) or [] if isinstance(t, dict) and t.get("url")]
            if pictures:
                track[key] = [max(pictures, key=lambda t: t.get("width") or 0)]
    title = raw.get("title") if isinstance(raw, dict) else None
    return {"tracks": kept, **({"title": title} if isinstance(title, str) else {})}


def _tracks(raw: Any) -> list[Candidate]:
    found = []
    for track in trim_tracks(raw)["tracks"]:
        duration = _int(track.get("duration_seconds")) or parse_length(
            track.get("length") or track.get("duration")
        )
        candidate = _from_track(track, duration=duration)
        if candidate is not None:
            found.append(candidate)
    return found


def radio(video_id: str, *, cache: SearchCache | None = None) -> list[Candidate]:
    """YouTube Music's radio for a song: about 50 songs like it, in its order, the song
    itself first. One request; the answer is kept for a week."""
    key = f"radio {video_id}"
    raw = None if cache is None else cache.cached_search(key, max_age_days=RADIO_CACHE_DAYS)
    if raw is None:
        raw = trim_tracks(
            _fetch(
                "radio",
                video_id,
                lambda client: client.get_watch_playlist(
                    videoId=video_id, radio=True, limit=RADIO_SONGS
                ),
            )
        )
        if cache is not None:
            cache.put_search(key, raw)
    return _tracks(raw)


@dataclass(frozen=True)
class ArtistRadio:
    """An artist as YouTube Music names them, and the songs on their radio: their own
    and other artists' that their listeners play."""

    name: str
    tracks: tuple[Candidate, ...]


def artist_radio(name: str, *, cache: SearchCache | None = None) -> ArtistRadio | None:
    """The radio of the artist YouTube Music finds for `name`, or None if it finds no
    artist by that name. Two requests (the artist, then the radio); the artist is kept
    for 30 days and the radio for a week.

    Only an artist whose name is the one asked for is taken ("the" aside): a search for
    a name YouTube Music doesn't know returns other artists, and their radio would be a
    surprise."""
    key = query_key(name)
    if not key:
        return None
    cache_key = f"artists {key}"
    raw = None if cache is None else cache.cached_search(cache_key, max_age_days=SEARCH_CACHE_DAYS)
    if raw is None:
        raw = _fetch("artists", key, lambda client: client.search(name, filter="artists", limit=5))
        raw = [
            {k: r.get(k) for k in ("artist", "browseId", "radioId")}
            for r in (raw if isinstance(raw, list) else [])
            if isinstance(r, dict)
        ]
        if cache is not None:
            cache.put_search(cache_key, raw)
    wanted = artist_key(name)
    found = next(
        (
            r
            for r in (raw if isinstance(raw, list) else [])
            if isinstance(r, dict)
            and isinstance(r.get("artist"), str)
            and isinstance(r.get("radioId"), str)
            and artist_key(r["artist"]) == wanted
        ),
        None,
    )
    if found is None:
        return None
    radio_id = found["radioId"]
    radio_key = f"artist radio {radio_id}"
    tracks = (
        None if cache is None else cache.cached_search(radio_key, max_age_days=RADIO_CACHE_DAYS)
    )
    if tracks is None:
        tracks = trim_tracks(
            _fetch(
                "artist-radio",
                radio_id,
                lambda client: client.get_watch_playlist(
                    playlistId=radio_id, limit=ARTIST_RADIO_SONGS
                ),
            )
        )
        if cache is not None:
            cache.put_search(radio_key, tracks)
    return ArtistRadio(name=found["artist"], tracks=tuple(_tracks(tracks)))


@dataclass(frozen=True)
class GenrePlaylist:
    """A playlist YouTube Music itself made for a genre. Its tracks are mostly music
    videos (`MUSIC_VIDEO_TYPE_OMV`), a few official audio."""

    title: str
    tracks: tuple[Candidate, ...]


def genre_playlist(name: str, *, cache: SearchCache | None = None) -> GenrePlaylist | None:
    """YouTube Music's own playlist for a genre ("hip hop", "indie"), or None if it has
    none. Two requests (the search, then the playlist), both kept for 30 days.

    Only a playlist by YouTube Music is taken, never a listener's (`_genre_choice`)."""
    key = query_key(name)
    if not key:
        return None
    cache_key = f"genre {key}"
    raw = None if cache is None else cache.cached_search(cache_key, max_age_days=SEARCH_CACHE_DAYS)
    if raw is None:
        raw = _fetch(
            "genre",
            key,
            lambda client: client.search(name, filter="featured_playlists", limit=20),
        )
        raw = [
            {k: r.get(k) for k in ("title", "browseId", "author")}
            for r in (raw if isinstance(raw, list) else [])
            if isinstance(r, dict)
        ]
        if cache is not None:
            cache.put_search(cache_key, raw)
    found = _genre_choice(raw, key)
    if found is None:
        return None
    playlist_id = found["browseId"].removeprefix("VL")
    playlist_key = f"playlist {playlist_id}"
    tracks = (
        None if cache is None else cache.cached_search(playlist_key, max_age_days=SEARCH_CACHE_DAYS)
    )
    if tracks is None:
        tracks = trim_tracks(
            _fetch(
                "playlist",
                playlist_id,
                lambda client: client.get_playlist(playlist_id, limit=GENRE_PLAYLIST_SONGS),
            )
        )
        if cache is not None:
            cache.put_search(playlist_key, tracks)
    title = tracks.get("title") if isinstance(tracks, dict) else None
    return GenrePlaylist(
        title=title if isinstance(title, str) and title else str(found.get("title") or name),
        tracks=tuple(_tracks(tracks)),
    )


PLAYLIST_ID = re.compile(r"[A-Za-z0-9_-]{2,80}")
PLAYLIST_MOST = 1000  # songs read from one playlist (ten requests' worth)


@dataclass(frozen=True)
class Playlist:
    """A playlist as YouTube Music gives it to someone who isn't signed in: a public or
    an unlisted one. Its entries are songs (official audio), music videos, or uploads."""

    playlist_id: str
    title: str
    tracks: tuple[Candidate, ...]


def playlist(playlist_id: str, *, limit: int = PLAYLIST_MOST) -> Playlist:
    """Read a playlist by its id (v0.3 imports). Never cached: the owner's own playlist
    changes, and is asked for when they ask. A private one, or one that doesn't exist,
    is a plain error."""
    from ytmusicapi.exceptions import YTMusicError

    if not PLAYLIST_ID.fullmatch(playlist_id):
        raise YouTubeError("That isn't a playlist's id.")
    try:
        raw = _fetch(
            "playlist", playlist_id, lambda client: client.get_playlist(playlist_id, limit=limit)
        )
    except (YTMusicError, KeyError, IndexError, TypeError) as exc:
        if is_slow_down(exc):
            raise
        # ytmusicapi reads a page that isn't a playlist's and trips over what's missing.
        log.info("playlist(%s): %s: %s", playlist_id, type(exc).__name__, exc)
        raise YouTubeError(
            "YouTube Music wouldn't show that playlist. If it's private, set it to "
            "Unlisted or Public (the playlist's Edit menu) and try again."
        ) from None
    kept = trim_tracks(raw)
    title = kept.get("title")
    return Playlist(
        playlist_id=playlist_id,
        title=title if isinstance(title, str) and title.strip() else "Playlist",
        tracks=tuple(_tracks(kept)),
    )


def _genre_choice(raw: Any, genre_key: str) -> dict[str, Any] | None:
    """Which of the playlists found is the genre's. The search returns all sorts ("Aussie
    Hip-Hop Golds", "Hip-Hop Christmas", "00s German Rap Essentials", in a different
    order each time), so: one named for the genre and its hits ("Hip Hop Hits 2024")
    first, then the shortest name with the genre in it, then whatever came first."""
    listed = [
        r
        for r in (raw if isinstance(raw, list) else [])
        if isinstance(r, dict)
        and isinstance(r.get("browseId"), str)
        and r.get("author") == "YouTube Music"
    ]
    named = [r for r in listed if f" {genre_key} " in f" {compare_key(r.get('title'))} "]
    hits = [r for r in named if " hits " in f" {compare_key(r.get('title'))} "]
    if hits:
        return hits[0]
    if named:
        return min(named, key=lambda r: len(str(r.get("title"))))
    return listed[0] if listed else None


def same_song(title: str, artist: str, found: Candidate) -> bool:
    """Whether a YouTube Music track is this song: the same title and version, and an
    artist in common."""
    return _same_song(parse_title(title), artist, found)


@dataclass(frozen=True)
class TrackLyrics:
    """YouTube Music's lyrics for one track: timed lines (milliseconds, text) when it has
    them, plain text otherwise. `source` is who supplied them, e.g. "Musixmatch"."""

    plain: str | None
    lines: tuple[tuple[int, str], ...] = ()
    source: str | None = None


def get_lyrics(video_id: str, *, cache: SearchCache | None = None) -> TrackLyrics | None:
    """The lyrics YouTube Music shows for a track, timed if it has them. None if it has
    none. Two requests (the watch playlist, then the lyrics), both cached for 30 days.
    Checked against ytmusicapi 1.12.3: `get_watch_playlist()["lyrics"]` is a browseId
    starting "MPLYt", and `get_lyrics(browseId, timestamps=True)` gives `hasTimestamps`,
    `source` and `lyrics` (text, or LyricLine objects with `text`, `start_time`,
    `end_time` in milliseconds)."""
    key = f"lyrics {video_id}"
    raw = None if cache is None else cache.cached_search(key, max_age_days=SEARCH_CACHE_DAYS)
    if raw is None:
        watch = _fetch(
            "watch", video_id, lambda client: client.get_watch_playlist(videoId=video_id, limit=1)
        )
        browse_id = watch.get("lyrics") if isinstance(watch, dict) else None
        raw = {"found": False}
        if isinstance(browse_id, str) and browse_id:
            found = _fetch(
                "lyrics",
                browse_id,
                lambda client: _plain_lyrics(client.get_lyrics(browse_id, timestamps=True)),
            )
            if isinstance(found, dict):
                raw = {"found": True, **found}
        if cache is not None:
            cache.put_search(key, raw)
    if not isinstance(raw, dict) or not raw.get("found"):
        return None
    source = str(raw.get("source") or "").removeprefix("Source: ").strip() or None
    body = raw.get("lyrics")
    if raw.get("hasTimestamps") and isinstance(body, list):
        lines = tuple(
            (int(line["start_time"]), str(line.get("text") or ""))
            for line in body
            if isinstance(line, dict) and isinstance(line.get("start_time"), int)
        )
        plain = "\n".join(text for _, text in lines).strip() or None
        return TrackLyrics(plain, lines, source) if lines else None
    if isinstance(body, str) and body.strip():
        return TrackLyrics(body.strip(), (), source)
    return None


def _plain_lyrics(found: Any) -> Any:
    """ytmusicapi's answer as plain JSON (its LyricLine objects become dicts), so it can
    be cached and recorded."""
    if not isinstance(found, dict):
        return None
    body = found.get("lyrics")
    if isinstance(body, list):
        body = [
            {"text": getattr(line, "text", None), "start_time": getattr(line, "start_time", None),
             "end_time": getattr(line, "end_time", None)}
            for line in body
        ]  # fmt: skip
    return {"lyrics": body, "source": found.get("source"),
            "hasTimestamps": bool(found.get("hasTimestamps"))}  # fmt: skip


IMAGE_MAX_BYTES = 15_000_000
IMAGE_TIMEOUT_S = 30
USER_AGENT = "MusicOrganizer (https://github.com/AfterDuskAu/MusicOrganizer)"
_SIZED = re.compile(r"=(?:w\d+-h\d+|s\d+)[^/]*$")


def sized_thumbnail(url: str, px: int) -> str:
    """A Google image URL asking for `px` × `px`. Checked live (2026-09-30): an album
    listed at 544 px comes at 1200 px when asked, and asking for more than the original
    (1425 px) returns the original, never an enlarged copy. Other URLs are unchanged."""
    if "googleusercontent.com" in url or "ggpht.com" in url:
        return _SIZED.sub(f"=w{px}-h{px}", url) if _SIZED.search(url) else f"{url}=w{px}-h{px}"
    return url


def fetch_image(url: str) -> bytes:
    """A picture from the web (album art, a page's thumbnail), through the rate limiter.
    Raises YouTubeError if it isn't an image or is implausibly large."""
    if not url.lower().startswith("https://"):
        raise YouTubeError(f"Only https:// pictures are fetched, not {url}.")
    replay = os.environ.get(REPLAY_ENV)
    if replay:
        import base64

        return base64.b64decode(read_recording(Path(replay), "image", url))

    def live() -> bytes:
        import requests

        response = requests.get(url, timeout=IMAGE_TIMEOUT_S, headers={"User-Agent": USER_AGENT})
        if response.status_code == 429 or response.status_code >= 500:
            response.raise_for_status()  # a slow-down: the limiter backs off
        if response.status_code >= 400:
            raise YouTubeError(f"{url} answered HTTP {response.status_code}.")
        kind = response.headers.get("content-type", "")
        if not kind.startswith("image/"):
            raise YouTubeError(f"{url} isn't a picture ({kind or 'unknown type'}).")
        if len(response.content) > IMAGE_MAX_BYTES:
            raise YouTubeError(f"The picture at {url} is too large.")
        return response.content

    return limiter().call(live)


def page_thumbnail(url: str) -> str | None:
    """The picture a web page offers for a track (SoundCloud, Bandcamp, YouTube and the
    other sites yt-dlp knows), without downloading anything. None if there isn't one."""
    replay = os.environ.get(REPLAY_ENV)
    if replay:
        return read_recording(Path(replay), "page", url)
    opts = {"quiet": True, "noprogress": True, "skip_download": True, "noplaylist": True,
            "logger": _YtDlpLog(), "socket_timeout": SOCKET_TIMEOUT_S,
            "cachedir": str(ensure_app_dir(app_dirs().cache) / "yt-dlp")}  # fmt: skip

    def live() -> Any:
        with _make_ydl(opts) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = limiter().call(live)
    except (YouTubePausedError, ReplayMissError):
        raise
    except Exception as exc:
        raise YouTubeError(f"Couldn't read {url}: {_short_detail(str(exc))}") from exc
    if not isinstance(info, dict):
        return None
    thumbs = [t for t in info.get("thumbnails") or [] if isinstance(t, dict) and t.get("url")]
    best = max(thumbs, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0), default=None)
    found = (best or {}).get("url") or info.get("thumbnail")
    return str(found) if found else None


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
    return _download(video_id, Path(dest_dir), download_options(Path(dest_dir), progress))


def download_video(
    video_id: str,
    dest_dir: Path,
    *,
    height: int,
    fps: int | None = None,
    progress: ProgressHook | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Download a video as one MP4 into `dest_dir` (a folder from `fileops.stage_dir`):
    its H.264 picture at exactly `height` (the faster frame rate if `fps` is above 30),
    joined to its format-140 sound. yt-dlp has ffmpeg join the two without converting
    either: a repackage, which rule 6 allows. There's no fallback: a size YouTube
    doesn't offer is FormatUnavailableError, and nothing else is fetched in its place.
    Returns the file and yt-dlp's info (`format_id` is like "137+140").

    The same errors, rate limiter and staging rule as `download_audio`."""
    if isinstance(height, bool) or not isinstance(height, int) or not 100 <= height <= 4320:
        raise YouTubeError(f"{height!r} isn't a picture height.")
    pace = "[fps>30]" if fps is not None and fps > 30 else "[fps<=?30]"
    picture = f"bestvideo[vcodec^=avc1][ext=mp4][protocol=https][height={height}]{pace}"
    opts = {
        **download_options(Path(dest_dir), progress),
        "format": f"{picture}+{DOWNLOAD_FORMAT}",
        "merge_output_format": "mp4",
    }
    try:
        return _download(video_id, Path(dest_dir), opts)
    except FormatUnavailableError:
        raise FormatUnavailableError(
            f"YouTube doesn't offer {video_id} as a {height}p picture the library keeps "
            "(H.264 in MP4, with the usual sound). No other format is used."
        ) from None


def _download(video_id: str, dest: Path, opts: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    if not VIDEO_ID.fullmatch(video_id):
        raise YouTubeError(f"{video_id!r} isn't a YouTube video id.")
    if not dest.is_dir() or STAGING_DIR not in dest.parts:
        raise ValueError(f"{dest} isn't a folder in _Staging (use fileops.stage_dir)")
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
        **_base_options(),
        "paths": {"home": str(dest), "temp": str(dest)},
        "outtmpl": {"default": "%(id)s.%(ext)s"},
        "ffmpeg_location": str(tools.require("ffmpeg")),
        "progress_hooks": [hook],
        "postprocessors": [],  # none; yt-dlp's own M4A container fix-up still runs
        "overwrites": False,
        # The file's time is when it was downloaded, not YouTube's Last-Modified date, so
        # a download kept in _Staging for 24 hours isn't cleaned up at once (step 09b).
        # Already off when yt-dlp is used from Python; said here so it stays off.
        "updatetime": False,
    }


def _base_options() -> dict[str, Any]:
    """What every yt-dlp call shares: format 140 only, deno, our cache folder, quiet."""
    return {
        "format": DOWNLOAD_FORMAT,
        "js_runtimes": {"deno": {"path": str(tools.require("deno"))}},
        "cachedir": str(ensure_app_dir(app_dirs().cache) / "yt-dlp"),
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "logger": _YtDlpLog(),
        "socket_timeout": SOCKET_TIMEOUT_S,
    }


@dataclass(frozen=True)
class Stream:
    """Where a video's audio can be played from right now, without saving it."""

    url: str
    headers: dict[str, str]  # what the player must send with its requests
    duration_s: float | None


@dataclass(frozen=True)
class VideoQuality:
    """One size of a video's picture, as an address to play it from (no sound in it)."""

    height: int  # 1080 for "1080p"
    fps: int
    url: str

    @property
    def label(self) -> str:
        return f"{self.height}p{self.fps}" if self.fps > 30 else f"{self.height}p"


@dataclass(frozen=True)
class Video:
    """A video the app can play: its sound, and its picture in each size on offer."""

    video_id: str
    audio: Stream
    qualities: tuple[VideoQuality, ...]  # the sharpest first


def stream(video_id: str) -> Stream:
    """The address of a video's format-140 audio, for the app to play (v0.2). Nothing is
    downloaded or written. The address is YouTube's and stops working after a few
    hours, so it's asked for each time a song is played, through the shared rate limiter.

    Raises the same errors as `download_audio`."""
    return _audio_of(video_id, _look_up(video_id))


def video(video_id: str) -> Video:
    """A video's sound and its picture sizes, for the app to play together (v0.2).
    Nothing is downloaded or written, and it's one request to YouTube, like `stream`.

    Only pictures Apple's player can show are listed: H.264 in MP4, served whole over
    https, which YouTube offers from 144p up to 1080p. Its 1440p and 4K pictures are
    VP9 or AV1 only, and aren't listed. Checked against yt-dlp 2026.08.19: each entry of
    `formats` carries `vcodec` ("avc1.640028"), `acodec` ("none" for picture only),
    `ext`, `protocol` ("https" or "m3u8_native"), `height`, `fps`, `tbr` and `url`."""
    info = _look_up(video_id)
    best: dict[tuple[int, int], tuple[float, str]] = {}
    for found in info.get("formats") or []:
        if not isinstance(found, dict) or not _is_playable_picture(found):
            continue
        key = (int(found["height"]), round(float(found.get("fps") or 0)))
        rate = float(found.get("tbr") or 0)
        if key not in best or rate > best[key][0]:
            best[key] = (rate, found["url"])
    qualities = tuple(
        VideoQuality(height, fps, best[height, fps][1])
        for height, fps in sorted(best, reverse=True)
    )
    return Video(video_id, _audio_of(video_id, info), qualities)


def _is_playable_picture(found: dict[str, Any]) -> bool:
    address = found.get("url")
    return (
        str(found.get("vcodec") or "").startswith("avc1")
        and str(found.get("acodec") or "none") == "none"
        and found.get("ext") == "mp4"
        and found.get("protocol") == "https"
        and isinstance(found.get("height"), int)
        and isinstance(address, str)
        and address.startswith("https://")
    )


def _look_up(video_id: str) -> dict[str, Any]:
    """What YouTube says about a video right now (yt-dlp, nothing downloaded)."""
    if not VIDEO_ID.fullmatch(video_id):
        raise YouTubeError(f"{video_id!r} isn't a YouTube video id.")
    url = f"https://www.youtube.com/watch?v={video_id}"

    def run() -> Any:
        with _make_ydl(_base_options()) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = limiter().call(run)
    except (YouTubePausedError, ReplayMissError):
        raise
    except Exception as exc:
        raise download_problem(video_id, exc) from exc
    if not isinstance(info, dict):
        raise DownloadError(f"YouTube gave no address to play {video_id} from.")
    with _recent_lock:
        _recent[video_id] = (time.monotonic(), info)
        for old_id in [k for k, (at, _) in _recent.items() if time.monotonic() - at > RECENT_S]:
            del _recent[old_id]
    return info


RECENT_S = 300.0
_recent: dict[str, tuple[float, dict[str, Any]]] = {}
_recent_lock = threading.Lock()


def _looked_up_lately(video_id: str) -> dict[str, Any] | None:
    """What YouTube said about a video in the last five minutes, if it was asked. The
    app asks about a video to play it and, a moment later, the engine wants the same
    video's sound and captions to time the lyrics: that needn't be a second request.
    Only `sources` uses it. `stream` and `video` always ask afresh: they're called again
    exactly when an address has stopped working."""
    with _recent_lock:
        found = _recent.get(video_id)
    return found[1] if found is not None and time.monotonic() - found[0] <= RECENT_S else None


def _audio_of(video_id: str, info: dict[str, Any]) -> Stream:
    address = info.get("url")
    if not isinstance(address, str) or not address.startswith("https://"):
        raise DownloadError(f"YouTube gave no address to play {video_id} from.")
    if str(info.get("format_id") or "") != DOWNLOAD_FORMAT:
        raise FormatUnavailableError(f"YouTube doesn't offer {video_id} in the format we play.")
    headers = info.get("http_headers")
    return Stream(
        url=address,
        headers={str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else {},
        duration_s=float(info["duration"])
        if isinstance(info.get("duration"), int | float)
        else None,
    )


# ---- a video's captions and sound, for timing lyrics to it (v0.2) ------------------------

CAPTION_MAX_BYTES = 2_000_000
MAX_MANUAL_TRACKS = 3


@dataclass(frozen=True)
class CaptionTrack:
    """One of a video's caption tracks: the uploader's own (`manual`) or YouTube's speech
    recognition (`automatic`). The address is YouTube's and expires."""

    kind: str
    language: str
    url: str


@dataclass(frozen=True)
class VideoSources:
    """What a video offers for timing lyrics to it: its sound and its captions."""

    video_id: str
    audio: Stream | None
    captions: tuple[CaptionTrack, ...]


def sources(video_id: str) -> VideoSources:
    """A video's sound (format 140) and its caption tracks, in one request (none, if the
    video was asked about in the last five minutes, as it is when it's playing). Nothing
    is downloaded.

    Checked against yt-dlp 2026.08.19 on 13 official videos (2026-10-02): `subtitles`
    and `automatic_captions` are always filled, each a dict of language key → formats,
    every real track offered as `json3` among others. `automatic_captions` also lists
    translations and entries with only a `vtt` format; the speech-recognition track
    itself has a key ending `-orig` ("en-orig"). A track's language label can't be
    trusted (an "English" track holding the Spanish words that are sung), so the
    caller tries the tracks against the words it has. Manual tracks come first, English
    ones before the rest, three at most."""
    info = _looked_up_lately(video_id) or _look_up(video_id)
    try:
        audio: Stream | None = _audio_of(video_id, info)
    except DownloadError:
        audio = None

    def tracks(listing: Any, kind: str, wanted: Callable[[str], bool]) -> list[CaptionTrack]:
        found = []
        for language, formats in (listing if isinstance(listing, dict) else {}).items():
            if not isinstance(language, str) or not wanted(language):
                continue
            for entry in formats if isinstance(formats, list) else []:
                address = entry.get("url") if isinstance(entry, dict) else None
                if entry.get("ext") == "json3" and isinstance(address, str):
                    if address.startswith("https://"):
                        found.append(CaptionTrack(kind, language, address))
                    break
        return found

    manual = tracks(info.get("subtitles"), "manual", lambda language: language != "live_chat")
    manual.sort(key=lambda track: not track.language.casefold().startswith("en"))
    automatic = tracks(
        info.get("automatic_captions"), "automatic", lambda language: language.endswith("-orig")
    )
    return VideoSources(video_id, audio, tuple(manual[:MAX_MANUAL_TRACKS] + automatic[:1]))


AUDIO_MAX_BYTES = 40_000_000  # about 40 minutes: longer isn't a song's video
AUDIO_PIECE_BYTES = 8 << 20
_CLEN = re.compile(r"[?&]clen=(\d+)")


def fetch_audio(stream: Stream) -> bytes:
    """The whole of a format-140 stream, in memory, to fingerprint it (lining a video's
    sound up with a song's). Nothing is written anywhere and nothing is kept: it's what
    playing the stream fetches, once. Through the rate limiter, as one request.

    Measured 2026-10-02: asked for plainly, YouTube serves a stream at about twice the
    speed it plays at (two minutes for a four-minute song). Asked for as a byte range
    in the address (`&range=0-<size>`; the size is the address's `clen`), a 4 MB song
    arrives in 0.15 to 0.4 s. One range of 13 MB was slowed again, so it's fetched in
    pieces of 8 MB."""
    found = _CLEN.search(stream.url)
    if found is None:
        raise DownloadError("YouTube didn't say how large that audio is, so it wasn't fetched.")
    size = int(found.group(1))
    if not 0 < size <= AUDIO_MAX_BYTES:
        raise DownloadError("That audio is too long to be a song's video.")

    def live() -> bytes:
        import requests

        data = bytearray()
        for low in range(0, size, AUDIO_PIECE_BYTES):
            high = min(size, low + AUDIO_PIECE_BYTES) - 1
            response = requests.get(
                f"{stream.url}&range={low}-{high}", headers=stream.headers,
                timeout=SOCKET_TIMEOUT_S,
            )  # fmt: skip
            if response.status_code == 429 or response.status_code >= 500:
                response.raise_for_status()  # a slow-down: the limiter backs off
            if response.status_code >= 400 or len(response.content) != high - low + 1:
                raise DownloadError(
                    f"YouTube didn't hand over that audio (HTTP {response.status_code})."
                )
            data += response.content
        return bytes(data)

    return limiter().call(live)


def fetch_captions(track: CaptionTrack) -> Any:
    """A caption track's contents (YouTube's "json3"), through the rate limiter."""

    def live() -> Any:
        import requests

        response = requests.get(
            track.url, timeout=IMAGE_TIMEOUT_S, headers={"User-Agent": USER_AGENT}
        )
        if response.status_code == 429 or response.status_code >= 500:
            response.raise_for_status()  # a slow-down: the limiter backs off
        if response.status_code >= 400:
            raise YouTubeError(f"YouTube answered HTTP {response.status_code} for the captions.")
        if len(response.content) > CAPTION_MAX_BYTES:
            raise YouTubeError("Those captions are too large to be a song's.")
        try:
            return response.json()
        except ValueError as exc:
            raise YouTubeError("YouTube's captions for that video couldn't be read.") from exc

    return limiter().call(live)


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
    # A search result says `thumbnails`; a watch playlist's track says `thumbnail`.
    pictures = track.get("thumbnails") or track.get("thumbnail") or []
    thumbnails = [t for t in pictures if isinstance(t, dict) and t.get("url")]
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
