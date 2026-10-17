"""fetch (2026-10-17): a film fetched from a web address a plugin gave, to keep it.

The owner, 2026-10-17: "add a way to download from the porn finder. it only offers play."
A way to play from a plugin is most often a torrent, which `torrents` keeps. The owner's
plugins for adults give a plain web address instead (a Stream of kind `url`), and until
now nothing could keep one. This module does, from any Finder: the whole film is fetched
from its address into the app's own cache folder, checked, made into one phones and
tablets play if Settings → Downloads says so (`convert`), and copied into its folder in
the Movies folder by `fileops.keep_media`, never over a file that's there. A film from the
Finder for adults goes in the Movies folder's `18+` folder (`naming.kept_place`); one from
the Movie Finder loose in the Movies folder, as a torrent's does.

What this module holds to:

- **Only from the owner's click** (`stream.keep`, or the app carrying on a download it
  had asked for when it was last open), and only an `http` or `https` address. A YouTube
  or SoundCloud address is refused: those go only through `youtube` (CLAUDE.md rule 8).
- **What arrives is kept in the app's cache folder, `fetched/`**, never in a library:
  `fileops.write_fetched_part` writes the film's bytes and `fileops.write_fetched_note` a
  note of how many have come. A file is asked for from where it got to (`Range: bytes=
  <have>-`), so one that's cut off carries on from there: at once, a few times, and when
  the app is opened again and asks once more. An address that doesn't take ranges, or
  whose file has changed since (`If-Range`), starts again from the beginning. A playlist
  of parts (HLS, `.m3u8`) is joined by ffmpeg into one file there, copying the picture
  and sound and changing neither; one that's cut off is fetched again from its start.
  What's left in `fetched/` is deleted once the film is kept, or two days after it was
  last touched (`fileops.sweep_cached`, when the engine starts and while it fetches).
- **It's checked before it's kept:** there must be room for it on the disks, with a
  gigabyte to spare on each (`tools.room_for`); what arrived must be a video (ffprobe
  finds a picture in it); and a live stream, which never ends, is refused.
- Nothing else is fetched or told to anyone: the address is the plugin's own, asked as
  the film player would ask it.

In replay mode (`MUSICORG_REPLAY_DIR`, every test) only an address on this computer is
ever fetched: any other raises `ReplayMissError`.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from musicorg import convert, fileops, tools
from musicorg.errors import MusicOrgError, ReplayMissError, UserError

log = logging.getLogger(__name__)

REPLAY_ENV = "MUSICORG_REPLAY_DIR"
USER_AGENT = "musicorg"  # as the add-ons are asked (`addons`)
# Fetch state (docs/ENGINE_API.md → Enums).
FINDING, FETCHING, COMPLETE = "finding", "fetching", "complete"
KEEP_S = 2 * 24 * 3600  # what arrived waits this long after it was last touched
SWEEP_EVERY_S = 600
CONNECT_S = 15  # how long an address is given to answer at all...
READ_S = 30  # ...and to send the next of the film, before it counts as cut off
# What has arrived is counted in the note after each part, once it is on the disk: the app
# closed part way, or a cut, loses at most the part it was on.
PART_BYTES = 8 * 1024 * 1024
TRY_WAITS_S = (2, 5, 15, 30, 60)  # a cut-off fetch is asked again after these, then given up
PROBE_S = 60  # how long ffprobe is given to read a playlist's length
RATE_S = 5.0  # how fast it's arriving is reckoned over this long
FINISHED_KEPT = 50  # fetches that ended are remembered this many, for the app to ask about
# Addresses that go only through `youtube` and its one gate (rule 8): never fetched here.
GATED_DOMAINS = (
    "youtube.com", "youtu.be", "youtube-nocookie.com", "googlevideo.com", "ytimg.com",
    "soundcloud.com", "sndcdn.com",
)  # fmt: skip
PLAYLIST_TYPES = frozenset(
    {"application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl", "audio/x-mpegurl"}
)
PLAYLIST_MARK = b"#EXTM3U"  # how a playlist of parts begins, whatever it's called
# A web page, not a film. (Anything else is fetched, and what it is ffprobe says.)
PAGE_TYPES = frozenset({"text/html", "application/xhtml+xml"})
VIDEO_ENDINGS = frozenset(
    {"mkv", "mp4", "m4v", "mov", "avi", "webm", "wmv", "flv", "mpg", "mpeg", "ts", "m2ts"}
)
# What ffprobe calls a kind of file → the ending it's kept with.
FORMAT_ENDINGS = (
    ("matroska", ".mkv"), ("mp4", ".mp4"), ("mov", ".mp4"), ("avi", ".avi"),
    ("mpegts", ".ts"), ("flv", ".flv"), ("asf", ".wmv"), ("mpeg", ".mpg"),
)  # fmt: skip
PLAYLIST_PROTOCOLS = "http,https,tcp,tls,crypto"  # all ffmpeg may open for a playlist


class FetchError(UserError):
    """A film couldn't be fetched or kept. The message is for the owner."""


class _Stopped(Exception):
    """The owner pressed Stop, or the engine is stopping."""


def fetch_id(url: str) -> str:
    """The id a fetch goes by: made from its address, so asking for the same one again
    (after the app was closed) finds what has arrived of it."""
    return "s-" + hashlib.sha256(url.encode("utf-8", "surrogatepass")).hexdigest()[:24]


def check_address(url: str) -> str:
    """`url`, if it's one this module may fetch: a web address, not YouTube's or
    SoundCloud's. FetchError otherwise; ReplayMissError in replay mode for anything not
    on this computer."""
    found = urlsplit(url.strip())
    host = (found.hostname or "").lower().rstrip(".")
    if found.scheme not in ("http", "https") or not host:
        raise FetchError("Only a web address (http or https) can be downloaded.")
    if _gated(host):
        raise FetchError(
            "That's a YouTube or SoundCloud address: those are downloaded through Music "
            "Finder and Video Finder, not here."
        )
    if os.environ.get(REPLAY_ENV) and not _on_this_computer(host):
        raise ReplayMissError("Replay mode: only an address on this computer is fetched.")
    return url.strip()


def _gated(host: str) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in GATED_DOMAINS)


def _on_this_computer(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def sweep(cache: Path, skip: list[str] | tuple[str, ...] = ()) -> int:
    """Delete what has sat in `fetched/` unused for two days (`fileops` does it, and only
    inside the app's cache): when the engine starts, and while it fetches."""
    folder = cache / fileops.FETCHED_DIR
    if not folder.is_dir():
        return 0
    try:
        return fileops.sweep_cached(folder, cache=cache, older_than_s=KEEP_S, skip=skip)
    except (MusicOrgError, OSError) as exc:
        log.warning("fetch: films that stopped arriving couldn't be cleared: %s", exc)
        return 0


@dataclass
class _Fetch:
    """One film being fetched, and how it's going."""

    id: str
    url: str
    name: str  # its name in the Movies folder, without an ending
    folder: tuple[str, ...]  # its folders there (`naming.kept_place`)
    convert: bool
    size_hint: int | None  # the plugin's figure, if it gave one
    state: str = FINDING
    have: int = 0
    size: int | None = None
    length_s: float | None = None  # a playlist's: progress is time then
    done_s: float = 0.0
    kept_path: str | None = None
    keep_error: str | None = None
    keep_note: str | None = None
    converting: float | None = None
    ended: bool = False
    stop: threading.Event = field(default_factory=threading.Event)
    process: subprocess.Popen[str] | None = None
    samples: deque[tuple[float, int]] = field(default_factory=lambda: deque(maxlen=64))

    def status(self) -> dict[str, Any]:
        if self.length_s:
            progress = min(self.done_s / self.length_s, 1.0)
        elif self.size:
            progress = min(self.have / self.size, 1.0)
        else:
            progress = 0.0
        return {
            "id": self.id,
            "state": self.state,
            "bytes": self.have,
            "size": self.size,
            "bytes_per_second": self.rate(),
            "progress": round(progress, 4),
            "keeping": not self.ended,
            "kept_path": self.kept_path,
            "keep_error": self.keep_error,
            "keep_note": self.keep_note,
            "converting": self.converting,
        }

    def heard(self, have: int) -> None:
        self.have = have
        self.samples.append((time.monotonic(), have))

    def rate(self) -> int:
        """Bytes a second, over the last few seconds."""
        now = time.monotonic()
        # (Copied whole first: the fetch's own thread adds to it meanwhile.)
        recent = [(t, n) for t, n in list(self.samples) if now - t <= RATE_S]
        if self.state != FETCHING or len(recent) < 2 or recent[-1][0] <= recent[0][0]:
            return 0
        return max(int((recent[-1][1] - recent[0][1]) / (recent[-1][0] - recent[0][0])), 0)


class _Reading:
    """An address's answer as `fileops.write_fetched_part` reads it: `head` (its first
    bytes, already read to see what it is) and then the rest. Any way the connection
    fails (requests' and urllib3's own errors among them) is an OSError there, which
    ends the part with what did arrive."""

    def __init__(self, raw: Any, head: bytes = b"") -> None:
        self.raw = raw
        self.head = head
        self.failed = False

    def read(self, n: int) -> bytes:
        if self.head:
            given, self.head = self.head[:n], self.head[n:]
            return given
        try:
            return bytes(self.raw.read(n))
        except Exception as exc:  # urllib3 and requests have errors of their own
            self.failed = True
            raise OSError(f"the connection was cut off ({type(exc).__name__})") from None


@dataclass
class _Answer:
    """What one answer from the address turned out to be (`Fetcher._answered`)."""

    playlist: bool = False  # a playlist of parts: ffmpeg fetches it
    done: bool = False  # nothing more to come: all of the film is here
    reading: _Reading | None = None  # the film's bytes, from where it had got to
    validator: str | None = None  # what to ask with next time, to carry on
    # None of those: nothing worth reading, asked again after a moment.


class Fetcher:
    """The films being fetched from web addresses to keep. One for the engine, made the
    first time one is asked for."""

    def __init__(
        self,
        cache: Path,
        movies: Path | None,
        *,
        forbidden: Callable[[], list[Path]] = list,
    ) -> None:
        self.cache = cache  # the app's cache folder
        self.folder = cache / fileops.FETCHED_DIR
        self.movies = movies  # where a kept film goes; None: none can be kept
        self._forbidden = forbidden  # inside these nothing is ever kept: the open library
        self._jobs: dict[str, _Fetch] = {}
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._swept = 0.0

    # -- what the app asks for --

    def keep(
        self,
        url: str,
        name: str,
        *,
        folder: tuple[str, ...] = (),
        convert: bool = False,
        size: int | None = None,
    ) -> dict[str, Any]:
        """Fetch the film at `url` and keep it in the Movies folder as `name` (its own
        ending is added), in `folder` inside it. Returns at once, with its status; it
        carries on by itself for as long as the engine runs. Asked again for the same
        address while it's under way, it's the same fetch. One kept within the last two
        days, whose file is still there, is said to be kept and isn't fetched again."""
        url = check_address(url)
        if self.movies is None:
            raise FetchError("There's nowhere set to download films to on this computer.")
        if not name.strip():
            raise FetchError("A film needs a name to be saved under.")
        job_id = fetch_id(url)
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and not job.ended:
                return job.status()
            job = _Fetch(job_id, url, name.strip(), tuple(folder), convert, size)
            kept = self._kept_before(job)
            if kept is not None:
                job.kept_path, job.ended, job.state = kept, True, COMPLETE
                self._remember(job)
                return job.status()
            self._remember(job)
        threading.Thread(target=self._run, args=(job,), name="fetch", daemon=True).start()
        return job.status()

    def status(self, which: str) -> dict[str, Any]:
        job = self._find(which)
        if job is None:
            raise FetchError("That film isn't being downloaded any more.")
        return job.status()

    def stop(self, which: str) -> None:
        """The owner has changed their mind. What arrived stays in the cache for two
        days, so Download pressed again carries on from there; nothing in the Movies
        folder is touched. One that's being written there just now is finished."""
        job = self._find(which)
        if job is None or job.ended or job.state == COMPLETE:
            return
        job.stop.set()
        if job.process is not None and job.process.poll() is None:
            job.process.kill()
        with self._lock:
            self._jobs.pop(job.id, None)

    def stop_all(self) -> None:
        """The engine is stopping: every fetch stops at once, and what arrived of each is
        noted, so it carries on when the app asks again."""
        self._stopping.set()
        for job in list(self._jobs.values()):
            job.stop.set()
            if job.process is not None and job.process.poll() is None:
                job.process.kill()

    def sweep(self) -> int:
        """Delete what has sat unused in `fetched/` for two days; what's in use stays."""
        self._swept = time.monotonic()
        skip = [f"{job.id}{ending}" for job in list(self._jobs.values()) if not job.ended
                for ending in (".part", ".json", ".hls.mp4", ".converting.mp4")]  # fmt: skip
        return sweep(self.cache, skip)

    # -- the workings --

    def _find(self, which: str) -> _Fetch | None:
        with self._lock:
            return self._jobs.get(which) or self._jobs.get(fetch_id(which.strip()))

    def _remember(self, job: _Fetch) -> None:
        """(Holding `_lock`.) The newest fetches that ended are kept for the app to ask
        about; older ones are forgotten."""
        self._jobs[job.id] = job
        ended = [one for one in self._jobs.values() if one.ended]
        for old in ended[: max(len(ended) - FINISHED_KEPT, 0)]:
            self._jobs.pop(old.id, None)

    def _files(self, job: _Fetch) -> dict[str, Path]:
        return {ending: self.folder / f"{job.id}{ending}"
                for ending in (".part", ".json", ".hls.mp4", ".converting.mp4")}  # fmt: skip

    def _note(self, job: _Fetch) -> dict[str, Any] | None:
        """The note beside a fetch, if it's there, whole, and for this address."""
        try:
            note = json.loads(self._files(job)[".json"].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(note, dict) or note.get("url") != job.url:
            return None
        return note

    def _write_note(self, job: _Fetch, **more: Any) -> None:
        note = {"url": job.url, "have": job.have, "size": job.size, "at": time.time(), **more}
        fileops.write_fetched_note(self._files(job)[".json"], json.dumps(note), cache=self.cache)

    def _kept_before(self, job: _Fetch) -> str | None:
        """Where this address's film was kept within its two days, if it's still there."""
        note = self._note(job)
        kept = note.get("kept") if note else None
        if isinstance(kept, str) and Path(kept).is_file():
            return kept
        return None

    def _run(self, job: _Fetch) -> None:
        host = urlsplit(job.url).hostname or "the plugin"
        try:
            fileops.make_cached_folder(self.folder, cache=self.cache)
            self.sweep()
            log.info("fetch: downloading a film from a plugin's address")
            source = self._fetch(job, host)
            job.state = COMPLETE
            self._keep(job, source)
        except _Stopped:
            log.info("fetch: a film's download was stopped; what arrived stays its two days")
        except FetchError as exc:
            job.keep_error = exc.message
            log.warning("fetch: a film couldn't be downloaded: %s", exc.message)
        except MusicOrgError as exc:
            job.keep_error = exc.message
            log.warning("fetch: a film couldn't be kept: %s", exc.message)
        except Exception as exc:
            job.keep_error = "Something went wrong while downloading it. Try again later."
            # Its words without any address: a plugin's address may carry its own key.
            log.warning("fetch: a film's download failed (%s: %s)", type(exc).__name__,
                        _without_addresses(str(exc)))  # fmt: skip
        finally:
            job.ended = True
            job.process = None

    def _going_on(self, job: _Fetch) -> bool:
        return not (job.stop.is_set() or self._stopping.is_set())

    def _wait(self, job: _Fetch, seconds: float) -> None:
        if job.stop.wait(seconds) or self._stopping.is_set():
            raise _Stopped

    def _fetch(self, job: _Fetch, host: str) -> Path:
        """All of the film, into the cache: where it is there."""
        import requests

        part = self._files(job)[".part"]
        note = self._note(job)
        validator = None
        if note is not None and isinstance(note.get("have"), int) and part.is_file():
            # What arrived before (the app was closed, or the owner pressed Stop): carried
            # on from there, if the address's file is still the same one.
            job.heard(min(note["have"], part.stat().st_size))
            job.size = note.get("size") if isinstance(note.get("size"), int) else None
            validator = note.get("validator") if isinstance(note.get("validator"), str) else None
        tries, roomy = 0, False
        session = requests.Session()
        try:
            while True:
                if not self._going_on(job):
                    raise _Stopped
                headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
                if job.have > 0:
                    headers["Range"] = f"bytes={job.have}-"
                    if validator:
                        headers["If-Range"] = validator
                before = job.have
                try:
                    response = session.get(
                        job.url, headers=headers, stream=True, timeout=(CONNECT_S, READ_S)
                    )
                except requests.RequestException:
                    response = None
                answer = _Answer()
                if response is not None:
                    with response:
                        answer = self._answered(job, response, host, roomy)
                        if answer.reading is not None:
                            validator, roomy = answer.validator, True
                            answer.done = self._take(job, answer.reading, part, validator)
                if answer.playlist:  # (its answer closed first: ffmpeg asks for it itself)
                    return self._fetch_playlist(job, host)
                if answer.done:
                    return part
                if not self._going_on(job):
                    raise _Stopped
                if job.have > before:
                    tries = 0  # cut off after more came: carried on at once from there
                    continue
                tries += 1  # nothing came: asked again after a while, a few times
                if tries > len(TRY_WAITS_S):
                    raise FetchError(
                        f"{host} isn't sending the film. Press Download again to try once "
                        "more: it carries on from where it got to."
                    )
                self._wait(job, TRY_WAITS_S[tries - 1])
        finally:
            session.close()

    def _answered(self, job: _Fetch, response: Any, host: str, roomy: bool) -> _Answer:
        """What an answer from the address is (`_Answer`). FetchError when it's no film
        at all, or there's no room for it (`roomy`: that has been looked at already)."""
        final = (urlsplit(response.url).hostname or "").lower()
        if final and _gated(final):
            raise FetchError(
                "That address leads to YouTube or SoundCloud, which aren't downloaded here."
            )
        status = response.status_code
        if status == 416 and job.have > 0:
            # Nothing is left after where it got to: all of it is here, if the address's
            # file is that long. Otherwise it's started again.
            got = _content_range(response.headers.get("Content-Range"))
            if (got[1] if got is not None else job.size) == job.have:
                job.size = job.have
                return _Answer(done=True)
            job.heard(0)
            job.size = None
            return _Answer()
        if status == 429 or status >= 500:
            return _Answer()  # busy, or a fault at its end: asked again after a moment
        if status not in (200, 206):
            raise FetchError(f"{host} answered with an error (HTTP {status}).")
        kind = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if kind in PLAYLIST_TYPES or urlsplit(job.url).path.lower().endswith(".m3u8"):
            return _Answer(playlist=True)
        if kind in PAGE_TYPES:
            raise FetchError(f"{host} gave a web page, not a film: nothing was downloaded.")
        coded = (response.headers.get("Content-Encoding") or "identity").strip().lower()
        if coded != "identity":
            response.raw.decode_content = True  # it's the film's bytes that are kept
        if status == 206:
            got = _content_range(response.headers.get("Content-Range"))
            if got is None or got[0] != job.have:
                job.heard(0)  # an answer that doesn't follow on: started again
                job.size = None
                return _Answer()
            size = got[1]
        else:
            if job.have > 0:
                log.info("fetch: the address sent the whole film again; starting afresh")
            job.heard(0)
            length = response.headers.get("Content-Length") or ""
            size = int(length) if length.isdigit() and coded == "identity" else None
        head = b""
        if job.have == 0:
            # A playlist of parts that doesn't say so: its first line says it.
            try:
                head = bytes(response.raw.read(len(PLAYLIST_MARK)))
            except Exception:
                return _Answer()
            if head.startswith(PLAYLIST_MARK):
                return _Answer(playlist=True)
        job.size = size
        job.state = FETCHING
        whole = size if size is not None else job.size_hint
        if not roomy and whole is not None and self.movies is not None:
            if not tools.room_for(max(whole - job.have, 0), whole, self.cache, self.movies):
                raise FetchError(
                    "There isn't room on this Mac for that film. Make some room, then press "
                    "Download again."
                )
        tag = response.headers.get("ETag") or ""
        validator = (
            tag if tag and not tag.startswith("W/") else response.headers.get("Last-Modified")
        ) or None
        if coded != "identity":
            validator = None  # a film sent packed can't be carried on part way
        self._write_note(job, validator=validator)
        return _Answer(reading=_Reading(response.raw, head), validator=validator)

    def _take(self, job: _Fetch, reading: _Reading, part: Path, validator: str | None) -> bool:
        """Read the film's bytes from an answer into its file in the cache, a part at a
        time, noting after each how much has arrived. True when all of it has."""
        start = job.have
        while True:
            if job.size is not None and job.have >= job.size:
                return True
            wanted = PART_BYTES if job.size is None else min(PART_BYTES, job.size - job.have)
            at = job.have
            written = fileops.write_fetched_part(
                part, at, reading, wanted, cache=self.cache,
                going_on=lambda n, at=at: self._ticked(job, at + n),
            )  # fmt: skip
            job.heard(at + written)
            self._write_note(job, validator=validator)
            if not self._going_on(job):
                raise _Stopped
            if written < wanted:
                if reading.failed:
                    log.info("fetch: a film's download was cut off after %d MB; carrying on",
                             (job.have - start) // 1_000_000)  # fmt: skip
                    return False
                # The answer ended. With no length said, that's the whole film; with one,
                # the rest is asked for again.
                return job.size is None or job.have >= job.size

    def _ticked(self, job: _Fetch, have: int) -> bool:
        job.heard(have)
        if time.monotonic() - self._swept > SWEEP_EVERY_S:
            self.sweep()
        return self._going_on(job)

    def _fetch_playlist(self, job: _Fetch, host: str) -> Path:
        """A playlist of parts (HLS): its length read first (a live stream has none, and
        is refused), then ffmpeg joins its picture and sound into one file in the cache,
        copying them and changing neither. Where that file is."""
        job.state, job.size = FINDING, None
        length = _playlist_length(job.url)
        if length is None:
            raise FetchError(
                "That address is a live stream, which goes on without end: it can't be downloaded."
            )
        job.length_s = length
        if (
            job.size_hint
            and self.movies is not None
            and not tools.room_for(job.size_hint, job.size_hint, self.cache, self.movies)
        ):
            raise FetchError(
                "There isn't room on this Mac for that film. Make some room, then press "
                "Download again."
            )
        target = self._files(job)[".hls.mp4"]
        # ffmpeg writes this one file, in the cache's `fetched/` folder (CLAUDE.md rule 3).
        command = [
            str(tools.require("ffmpeg")), "-nostdin", "-y", "-v", "error",
            "-protocol_whitelist", PLAYLIST_PROTOCOLS, "-user_agent", USER_AGENT,
            "-f", "hls", "-i", job.url, "-c", "copy", "-movflags", "+faststart",
            "-progress", "pipe:1", "-nostats", "-f", "mp4", str(target),
        ]  # fmt: skip
        if not self._going_on(job):
            raise _Stopped
        try:
            process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            )  # fmt: skip
        except OSError as exc:
            raise FetchError(f"The downloader couldn't be started: {exc}") from exc
        job.process = process
        job.state = FETCHING
        assert process.stdout is not None and process.stderr is not None
        try:
            for line in process.stdout:
                key, _, value = line.strip().partition("=")
                if key == "out_time_us" and value.isdigit():
                    job.done_s = int(value) / 1_000_000
                if key == "total_size" and value.isdigit():
                    job.heard(int(value))
                if not self._going_on(job):
                    process.kill()
                    break
            said = process.stderr.read()
            code = process.wait()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        if not self._going_on(job):
            raise _Stopped
        if code != 0:
            detail = _without_addresses((said.strip().splitlines() or ["no details"])[-1])
            raise FetchError(f"{host}'s film couldn't be downloaded: {detail}")
        job.done_s = length
        job.heard(target.stat().st_size)
        return target

    def _keep(self, job: _Fetch, source: Path) -> None:
        """A film that has all arrived: checked, converted if that was asked for and it
        needs it, then copied into the Movies folder by `fileops.keep_media` (never over a
        file that's there), and what's left in the cache let go of."""
        assert self.movies is not None
        try:
            found = convert.streams_of(source)
        except convert.ConvertError:
            found = {}
        pictures = [
            s for s in found.get("streams") or []
            if isinstance(s, dict) and s.get("codec_type") == "video"
            and not (s.get("disposition") or {}).get("attached_pic")
        ]  # fmt: skip
        if not pictures:
            self._let_go(job)
            raise FetchError("What that address gave isn't a film: nothing was kept.")
        # A playlist's parts were joined into an MP4; a file keeps the kind it is.
        ending = ".mp4" if source == self._files(job)[".hls.mp4"] else _ending(found, job.url)
        made: Path | None = None
        kept_from = source
        if job.convert:
            try:
                wanted = convert.plan(f"{job.name}{ending}", found)
                if wanted is not None:
                    made = self._files(job)[".converting.mp4"]
                    job.converting = 0.0
                    log.info("fetch: converting a downloaded film (%s)", wanted.about)

                    def heard(done: float) -> None:
                        job.converting = done

                    # ffmpeg writes this one file, in the cache's `fetched/` folder.
                    convert.run(
                        source, made, wanted, progress=heard,
                        should_stop=lambda: not self._going_on(job),
                    )  # fmt: skip
                    kept_from, ending = made, convert.ENDING
            except MusicOrgError as exc:
                if not self._going_on(job):
                    raise _Stopped from None
                job.keep_note = f"It was left as it arrived: {exc.message}"
                log.warning("fetch: a downloaded film wasn't converted: %s", exc.message)
                kept_from = source
            job.converting = None
        saved = fileops.keep_media(
            kept_from,
            self.movies.joinpath(*job.folder),
            f"{job.name}{ending}",
            cache=self.folder,
            allowed=[self.movies],
            forbidden=self._forbidden(),
        )
        job.kept_path = str(saved)
        log.info("fetch: a downloaded film was kept in the Movies folder")
        self._let_go(job)
        try:
            # Remembered for its two days, so the same film asked for again (the app
            # carrying on a download it didn't hear the end of) isn't fetched twice.
            self._write_note(job, kept=str(saved))
        except MusicOrgError:
            pass

    def _let_go(self, job: _Fetch) -> None:
        """What arrived of a film, out of the cache: it's kept, or it's no film."""
        for ending, path in self._files(job).items():
            if ending != ".json":
                fileops.forget_cached(path, cache=self.cache)
        fileops.forget_cached(self._files(job)[".json"], cache=self.cache)


def _content_range(header: str | None) -> tuple[int, int | None] | None:
    """`bytes <first>-<last>/<size>` → (first, size); the size None when it's `*`."""
    if not header or not header.startswith("bytes "):
        return None
    span, _, whole = header[len("bytes ") :].partition("/")
    first, _, _ = span.partition("-")
    if not first.strip().isdigit():
        return None
    return int(first), int(whole) if whole.strip().isdigit() else None


def _ending(found: dict[str, Any], url: str) -> str:
    """The ending a fetched film is kept with: from what ffprobe says it is, else its
    address's, else `.mp4`."""
    named = urlsplit(url).path.rsplit(".", 1)
    asked = "." + named[1].lower() if len(named) == 2 and named[1].lower() in VIDEO_ENDINGS else ""
    kind = str((found.get("format") or {}).get("format_name") or "")
    for word, ending in FORMAT_ENDINGS:
        if word in kind.split(","):
            if ending == ".mkv" and asked == ".webm":
                return asked
            if ending == ".mp4" and asked in (".mov", ".m4v"):
                return asked
            return ending
    return asked or ".mp4"


def _playlist_length(url: str) -> float | None:
    """How long a playlist of parts is, in seconds, by ffprobe; None for a live one (or
    one whose length can't be read). FetchError when it can't be read at all."""
    command = [
        str(tools.require("ffprobe")), "-v", "error", "-protocol_whitelist", PLAYLIST_PROTOCOLS,
        # Said to be a playlist: ffmpeg reads one only by its ending or its kind otherwise.
        "-user_agent", USER_AGENT, "-f", "hls", "-show_format", "-of", "json", url,
    ]  # fmt: skip
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=PROBE_S, check=False,
        )  # fmt: skip
        found = json.loads(result.stdout) if result.returncode == 0 else None
    except subprocess.TimeoutExpired:
        raise FetchError("That address's playlist took too long to read.") from None
    except (OSError, ValueError):
        raise FetchError("That address's playlist couldn't be read.") from None
    if not isinstance(found, dict):
        raise FetchError("That address's playlist couldn't be read.")
    length = (found.get("format") or {}).get("duration")
    try:
        seconds = float(length) if length is not None else None
    except (TypeError, ValueError):
        seconds = None
    return seconds if seconds and seconds > 0 else None


_ADDRESS = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)


def _without_addresses(text: str) -> str:
    """ffmpeg's words, without any address in them: one may carry the plugin's own key,
    and errors are shown and logged."""
    return _ADDRESS.sub("the address", text).strip() or "no details"
