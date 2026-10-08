"""`musicorg serve`: the JSON-RPC 2.0 server the Mac app (v0.2) talks to (step 11).

docs/ENGINE_API.md section 2 is the contract. The app starts the engine as a child
process and owns its lifetime:

- **One JSON object per line** on stdin and stdout, UTF-8, `\\n` endings, flushed at once.
  Nothing else ever reaches stdout: at start, file descriptor 1 is duplicated for the
  protocol and then pointed at stderr, so even ffmpeg, fpcalc or deno, which write to
  the real descriptor 1, can't corrupt the stream (`protect_stdout`).
- **One writer lock**, so notifications from worker threads never interleave with
  responses.
- `engine.hello` must come first. `library.open` takes the library's lock and holds it
  until shutdown; a CLI `queue pause` still works through the flag.
- **Workers:** the queue runs on its own thread exactly like `queue run`, from
  `library.open` (and after `plan.apply` or `queue.resume`) until it's empty, paused, or
  the engine stops. A queue that stopped until a known time (the daily limit, a pause by
  YouTube) is started again at that time. One further long operation at a time
  (`sources.scan`, `match.run`, `journal.undo`) returns `{job_id}` at once and reports
  `job.progress` (at most 4 a second) and `job.finished`. A second one while busy gets
  -32007.
- **Shutdown:** stdin closing is the signal on both platforms (SIGTERM on macOS does the
  same). No new work is taken; the queue's current job gets 10 seconds to finish
  (otherwise it's queued again when the engine next starts); the lock is released; the
  process exits 0.

Errors carry plain English in `message`, suitable to show the user directly.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import IO, Any

from musicorg import (
    __version__,
    addons,
    artist,
    browse,
    discover,
    fileops,
    imports,
    kids,
    lastfm,
    library,
    listening,
    logging_setup,
    lyrics,
    match,
    pipeline,
    queue,
    relay,
    review,
    scan,
    sharing,
    spotify,
    state,
    status,
    tags,
    torrents,
    videolyrics,
    youtube,
)
from musicorg.config import (
    MAX_DAILY_CAP,
    THROTTLE_DEFAULTS,
    Config,
    app_dirs,
    media_folders,
    save_daily_cap,
)
from musicorg.errors import (
    LibraryLockedError,
    MusicOrgError,
    NotFoundError,
    OutsideLibraryError,
    PlanOutOfDateError,
    ToolMissingError,
    YouTubeBlockedError,
    YouTubePausedError,
)
from musicorg.index import open_index, open_queue

log = logging.getLogger(__name__)

PROTOCOL = "2.0"
SHUTDOWN_GRACE_S = 10.0
PROGRESS_INTERVAL_S = 0.25  # at most 4 job.progress a second per job
# A queue that stopped until a known time is started again then: the clock is looked at
# this often, and never sooner than the least wait after it stopped.
RESUME_CHECK_S = 30.0
RESUME_LEAST_S = 60.0
REVIEW_STATES = ("review", "not_found", "matched_auto")
SLOW_METHODS = frozenset(
    {"youtube.stream", "youtube.video", "search.ytmusic", "lyrics.find", "lyrics.for_video",
     "discover.suggest", "import.playlist", "import.playlists", "import.find",
     "account.connect", "artist.info", "artist.songs", "artist.album", "artist.search",
     # Add-ons and video lookups wait on the network too (an add-on for up to 8 seconds).
     # Answered on the main line they held up every other request the app made
     # meanwhile, a favourite or a list of songs included (found 2026-10-07).
     "addon.list", "addon.add", "addon.restore", "addon.catalog", "addon.details", "addon.streams",
     "video.search", "channel.videos", "channel.search", "torrent.stop"}
)  # fmt: skip
MAX_EXCLUDE = 5000  # songs already on screen that Show More leaves out
RPC_DECISIONS = ("accept", "candidate", "url", "only_copy", "skip", "reject")

# Error codes (docs/ENGINE_API.md → Errors).
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL = (
    -32700, -32600, -32601, -32602, -32603,
)  # fmt: skip
USER_ERROR = -32000  # the request couldn't be done; `message` says why
LOCKED, OUTSIDE, TOOL_MISSING, YOUTUBE_PAUSED, PLAN_OUT_OF_DATE, NOT_FOUND, BUSY = (
    -32001, -32002, -32003, -32004, -32005, -32006, -32007,
)  # fmt: skip


def _settings(config: Config) -> dict[str, Any]:
    """The settings the app shows (v0.2): the engine's own, kept in config.json."""
    return {
        "daily_cap": config.throttle()["daily_cap"],
        "daily_cap_default": THROTTLE_DEFAULTS["daily_cap"],
        "daily_cap_max": MAX_DAILY_CAP,
    }


class Stop(BaseException):
    """SIGTERM: leave the read loop and shut down. A BaseException, so no request
    handler's `except Exception` swallows it."""


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def error_for(exc: BaseException) -> RpcError:
    """The engine's exceptions as JSON-RPC errors."""
    if isinstance(exc, RpcError):
        return exc
    if isinstance(exc, LibraryLockedError):
        return RpcError(LOCKED, exc.message)
    if isinstance(exc, OutsideLibraryError):
        return RpcError(OUTSIDE, exc.message)
    if isinstance(exc, ToolMissingError):
        return RpcError(TOOL_MISSING, exc.message, {"tool": getattr(exc, "tool", None)})
    if isinstance(exc, YouTubePausedError | YouTubeBlockedError):
        resume = getattr(exc, "resume_at", None)
        return RpcError(YOUTUBE_PAUSED, exc.message,
                        {"resume_at": resume.isoformat() if resume else None})  # fmt: skip
    if isinstance(exc, PlanOutOfDateError):
        return RpcError(PLAN_OUT_OF_DATE, exc.message)
    if isinstance(exc, NotFoundError):
        return RpcError(NOT_FOUND, exc.message)
    if isinstance(exc, MusicOrgError):
        return RpcError(USER_ERROR, exc.message)
    log.error("Unexpected error in an RPC request", exc_info=exc)
    path = logging_setup.log_file()
    return RpcError(
        INTERNAL,
        f"Something unexpected went wrong ({type(exc).__name__}: {exc}).",
        {"log": str(path) if path else None},
    )


# ---- the transport ---------------------------------------------------------------------


class ProtocolOut:
    """The protocol's own copy of stdout: a duplicated file descriptor, written with
    os.write. (Not a file on disk, so it needs no fileops; it's opened with no `open()`.)"""

    def __init__(self, fd: int) -> None:
        self.fd = fd

    def write(self, data: bytes) -> int:
        view = memoryview(data)
        while view:
            view = view[os.write(self.fd, view) :]
        return len(data)

    def flush(self) -> None:
        pass  # os.write doesn't buffer


def protect_stdout() -> ProtocolOut:
    """The protocol's own copy of stdout. File descriptor 1 then points at stderr, so
    nothing else (not even a child process) can write into the protocol stream."""
    sys.stdout.flush()
    proto = ProtocolOut(os.dup(1))
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return proto


class Writer:
    """Writes whole JSON lines, one at a time."""

    def __init__(self, stream: IO[bytes] | ProtocolOut) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def send(self, message: dict[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=False, default=str).encode("utf-8") + b"\n"
        with self._lock:
            try:
                self._stream.write(line)
                self._stream.flush()
            except (BrokenPipeError, ValueError, OSError):
                pass  # the app has gone; shutdown follows from stdin closing

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.send({"jsonrpc": PROTOCOL, "method": method, "params": params})


# ---- params ----------------------------------------------------------------------------


def need(params: dict[str, Any], name: str, kind: type | tuple[type, ...]) -> Any:
    if name not in params:
        raise RpcError(INVALID_PARAMS, f"The parameter {name!r} is missing.")
    return want(params, name, kind)


def want(
    params: dict[str, Any], name: str, kind: type | tuple[type, ...], default: Any = None
) -> Any:
    value = params.get(name, default)
    if value is None:
        return default
    kinds = kind if isinstance(kind, tuple) else (kind,)
    if isinstance(value, bool) and bool not in kinds:
        raise RpcError(INVALID_PARAMS, f"The parameter {name!r} has the wrong type.")
    if not isinstance(value, kinds):
        names = " or ".join(k.__name__ for k in kinds)
        raise RpcError(INVALID_PARAMS, f"The parameter {name!r} should be a {names}.")
    return value


# ---- the server ------------------------------------------------------------------------


@dataclass
class _Job:
    job_id: str
    kind: str
    thread: threading.Thread


class Server:
    """Handles requests; `run()` reads them until stdin closes."""

    def __init__(self, reader: IO[bytes], writer: Writer) -> None:
        self.reader, self.writer = reader, writer
        self.lib: library.Library | None = None
        self.greeted = False
        self.stopping = threading.Event()
        self._busy_lock = threading.Lock()
        self._job: _Job | None = None
        self._job_counter = 0
        self._queue_thread: threading.Thread | None = None
        self._queue_lock = threading.Lock()
        self._resume_wait = 0  # counts the waits to start the queue again; the newest wins
        self._share: sharing.Share | None = None  # the library shared at home, while it's on
        # A child's profile: only clean songs are looked up. The app says so each time it
        # opens (`kids.set`); the engine keeps no "on" of its own.
        self._kids = {"on": False, "allow_explicit": False}
        self._films: torrents.Player | None = None  # films playing from torrents, if any
        self._relay: relay.Relay | None = None  # long videos' playlists, if any were asked for
        self.methods: dict[str, Callable[[dict[str, Any]], Any]] = {
            "engine.hello": self.engine_hello,
            "library.init": self.library_init,
            "library.open": self.library_open,
            "library.status": self.library_status,
            "library.tracks": self.library_tracks,
            "library.lyrics": self.library_lyrics,
            "listening.get": self.listening_get,
            "listening.favourite": self.listening_favourite,
            "listening.played": self.listening_played,
            "listening.heard": self.listening_heard,
            "listening.move": self.listening_move,
            "playlist.create": self.playlist_create,
            "playlist.rename": self.playlist_rename,
            "playlist.delete": self.playlist_delete,
            "playlist.set_tracks": self.playlist_set_tracks,
            "sources.add": self.sources_add,
            "sources.list": self.sources_list,
            "sources.scan": self.sources_scan,
            "match.run": self.match_run,
            "review.list": self.review_list,
            "review.decide": self.review_decide,
            "plan.create": self.plan_create,
            "plan.get": self.plan_get,
            "plan.apply": self.plan_apply,
            "queue.status": self.queue_status,
            "queue.pause": self.queue_pause,
            "queue.resume": self.queue_resume,
            "journal.batches": self.journal_batches,
            "journal.undo": self.journal_undo,
            "search.ytmusic": self.search_ytmusic,
            "youtube.stream": self.youtube_stream,
            "youtube.video": self.youtube_video,
            "lyrics.find": self.lyrics_find,
            "lyrics.for_video": self.lyrics_for_video,
            "discover.suggest": self.discover_suggest,
            "import.playlist": self.import_playlist,
            "import.playlists": self.import_playlists,
            "account.status": self.account_status,
            "account.sign_in": self.account_sign_in,
            "account.connect": self.account_connect,
            "account.sign_out": self.account_sign_out,
            "import.find": self.import_find,
            "artist.search": self.artist_search,
            "artist.info": self.artist_info,
            "artist.songs": self.artist_songs,
            "artist.album": self.artist_album,
            "queue.jobs": self.queue_jobs,
            "queue.downloads": self.queue_downloads,
            "queue.dismiss": self.queue_dismiss,
            "settings.get": self.settings_get,
            "settings.set": self.settings_set,
            "kids.set": self.kids_set,
            "addon.list": self.addon_list,
            "addon.add": self.addon_add,
            "addon.remove": self.addon_remove,
            "addon.order": self.addon_order,
            "addon.restore": self.addon_restore,
            "addon.catalog": self.addon_catalog,
            "addon.details": self.addon_details,
            "addon.streams": self.addon_streams,
            "video.search": self.video_search,
            "channel.videos": self.channel_videos,
            "channel.search": self.channel_search,
            "channel.follow": self.channel_follow,
            "channel.followed": self.channel_followed,
            "torrent.play": self.torrent_play,
            "torrent.status": self.torrent_status,
            "torrent.keep": self.torrent_keep,
            "torrent.stop": self.torrent_stop,
            "sharing.status": self.sharing_status,
            "sharing.set": self.sharing_set,
            "sharing.pair": self.sharing_pair,
            "sharing.stop_pairing": self.sharing_stop_pairing,
            "sharing.forget": self.sharing_forget,
        }

    # -- the loop --

    def run(self) -> int:
        # What arrived of a film last played over a day ago goes, each time the engine
        # starts (and every ten minutes while a film player is running).
        threading.Thread(
            target=torrents.sweep, args=(app_dirs().cache,), daemon=True, name="film-cache"
        ).start()
        try:
            for raw in iter(self.reader.readline, b""):
                if self.stopping.is_set():
                    break
                self.handle_line(raw)
        except Stop:
            log.info("serve: stopping (SIGTERM)")
        finally:
            self.shutdown()
        return 0

    def handle_line(self, raw: bytes) -> None:
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return
        try:
            message = json.loads(text)
        except ValueError:
            self._error(None, RpcError(PARSE_ERROR, "That line isn't JSON."))
            return
        if isinstance(message, list):
            self._error(None, RpcError(INVALID_REQUEST, "Batch requests (arrays) aren't accepted."))
            return
        if isinstance(message, dict) and message.get("method") in SLOW_METHODS and self.greeted:
            # A YouTube lookup takes seconds: answer it from its own thread, so the app's
            # other requests (lyrics, a favourite) aren't kept waiting behind it.
            threading.Thread(target=self._answer, args=(message,), daemon=True).start()
            return
        self._answer(message)

    def _answer(self, message: Any) -> None:
        reply = self.handle(message)
        if reply is not None:
            self.writer.send(reply)

    def handle(self, message: Any) -> dict[str, Any] | None:
        """One request → its response (None for a notification from the app)."""
        request_id = message.get("id") if isinstance(message, dict) else None
        try:
            if not isinstance(message, dict) or message.get("jsonrpc") != PROTOCOL or not (
                isinstance(message.get("method"), str)
            ):  # fmt: skip
                raise RpcError(INVALID_REQUEST, "That isn't a JSON-RPC 2.0 request.")
            method = message["method"]
            params = message.get("params", {})
            if params is None:
                params = {}
            if not isinstance(params, dict):
                raise RpcError(INVALID_PARAMS, "Params must be an object.")
            handler = self.methods.get(method)
            if handler is None:
                raise RpcError(METHOD_NOT_FOUND, f"There's no method called {method!r}.")
            if not self.greeted and method != "engine.hello":
                raise RpcError(INVALID_REQUEST, "Send engine.hello first.")
            if self.stopping.is_set():
                raise RpcError(USER_ERROR, "The engine is shutting down.")
            result = handler(params)
        except Exception as exc:  # every error becomes a reply, never a crash
            if "id" not in (message if isinstance(message, dict) else {}):
                return None
            return self._error_reply(request_id, error_for(exc))
        if isinstance(message, dict) and "id" not in message:
            return None
        return {"jsonrpc": PROTOCOL, "id": request_id, "result": result}

    def _error_reply(self, request_id: Any, err: RpcError) -> dict[str, Any]:
        body: dict[str, Any] = {"code": err.code, "message": err.message}
        if err.data is not None:
            body["data"] = err.data
        return {"jsonrpc": PROTOCOL, "id": request_id, "error": body}

    def _error(self, request_id: Any, err: RpcError) -> None:
        self.writer.send(self._error_reply(request_id, err))

    def shutdown(self) -> None:
        """Stop taking work, give the queue's job 10 s, release the lock."""
        if self.stopping.is_set() and self.lib is None:
            return
        self.stopping.set()
        spotify.cancel_sign_in()  # stop listening for a sign-in nobody finished
        self._stop_sharing()  # first of all: nothing is shared once the app has gone
        self._stop_films()  # and no torrent is left joined (what arrived stays its day)
        if self._relay is not None:
            self._relay.stop()  # nor any long video's playlist left to be read
            self._relay = None
        deadline = time.monotonic() + SHUTDOWN_GRACE_S
        for thread in (self._queue_thread, self._job.thread if self._job else None):
            if thread is not None and thread.is_alive():
                thread.join(max(0.0, deadline - time.monotonic()))
        if self.lib is not None:
            self.lib.close()
            self.lib = None
        log.info("serve: shut down")

    # -- helpers --

    def _library(self) -> library.Library:
        if self.lib is None:
            raise RpcError(USER_ERROR, "No library is open yet. Call library.open first.")
        return self.lib

    @contextmanager
    def _index(self, write: bool = False) -> Iterator[Any]:
        with open_index(self._library().paths, write=write) as index:
            yield index

    def _start_job(
        self, kind: str, work: Callable[[Callable[..., None]], dict[str, Any]]
    ) -> dict[str, Any]:
        """Run `work(progress)` on a thread; one at a time. Returns {job_id} at once."""
        with self._busy_lock:
            if self._job is not None and self._job.thread.is_alive():
                raise RpcError(BUSY, f"The engine is busy with {self._job.kind}; try again when "
                               "it has finished.")  # fmt: skip
            self._job_counter += 1
            job_id = f"j_{self._job_counter}"
            last = [0.0]

            def progress(done: int, total: int, message: str = "") -> None:
                now = time.monotonic()
                if done < total and now - last[0] < PROGRESS_INTERVAL_S:
                    return
                last[0] = now
                note = {"job_id": job_id, "done": done, "total": total, "message": message}
                self.writer.notify("job.progress", note)

            def target() -> None:
                try:
                    summary = work(progress)
                except Exception as exc:
                    err = error_for(exc)
                    note = {"job_id": job_id, "ok": False, "summary": None, "error": err.message}
                    self.writer.notify("job.finished", note)
                else:
                    self.writer.notify("job.finished", {"job_id": job_id, "ok": True,
                                                        "summary": summary})  # fmt: skip

            thread = threading.Thread(target=target, name=f"job-{kind}", daemon=True)
            self._job = _Job(job_id, kind, thread)
            thread.start()
        return {"job_id": job_id}

    def start_queue(self) -> None:
        """Run the queue on its thread, if it isn't running already."""
        lib = self._library()
        with self._queue_lock:
            if self._queue_thread is not None and self._queue_thread.is_alive():
                return
            if self.stopping.is_set():
                return

            def target() -> None:
                self._queue_state()
                try:
                    result = queue.run(lib, should_stop=self.stopping.is_set)
                except Exception as exc:
                    log.error("The queue worker stopped: %s", exc, exc_info=exc)
                else:
                    done = result.counts.get("done", 0)
                    if done:
                        self.writer.notify("library.changed", {"tracks_added": 0,
                                                               "tracks_changed": done})  # fmt: skip
                    if result.resume_at is not None:
                        self._resume_queue_at(result.resume_at)
                self._queue_state()

            self._queue_thread = threading.Thread(target=target, name="queue", daemon=True)
            self._queue_thread.start()

    def _resume_queue_at(self, when: datetime) -> None:
        """The queue stopped until a known time (the daily limit, a pause by YouTube, a
        retry that isn't due yet): start it again then, by itself, so downloads asked
        for at night carry on without anyone clicking. Only the newest wait counts. The
        clock is looked at every little while rather than slept through, so a computer
        that was asleep at the time starts the queue when it wakes."""
        with self._queue_lock:
            self._resume_wait += 1
            mine = self._resume_wait

        def wait() -> None:
            self.stopping.wait(RESUME_LEAST_S)  # never straight round again
            while not self.stopping.is_set() and self._resume_wait == mine:
                left = (when - datetime.now(UTC)).total_seconds()
                if left <= 0:
                    self.start_queue()
                    return
                self.stopping.wait(min(left, RESUME_CHECK_S))

        threading.Thread(target=wait, name="queue-resume", daemon=True).start()

    def _queue_state(self) -> None:
        if self.lib is None:
            return
        found = queue.status(self.lib.paths)
        self.writer.notify("queue.state", {"state": found["state"], "reason": found["reason"],
                                           "resume_at": found["resume_at"]})  # fmt: skip

    def _review_changed(self) -> None:
        with self._index() as index:
            counts = index.counts_by_state()
        self.writer.notify("review.changed", {"review": counts.get("review", 0),
                                              "not_found": counts.get("not_found", 0)})  # fmt: skip

    # -- methods: engine and library --

    def engine_hello(self, params: dict[str, Any]) -> dict[str, Any]:
        client = need(params, "client", str)
        client_version = want(params, "client_version", str, "")
        self.greeted = True
        log.info("serve: hello from %s %s", client, client_version)
        return {
            "engine_version": __version__,
            "schema_version": tags.SCHEMA_VERSION,
            "ytdlp_version": _version("yt-dlp"),
            "ytmusicapi_version": _version("ytmusicapi"),
            "capabilities": sorted(self.methods),
        }

    def library_init(self, params: dict[str, Any]) -> dict[str, Any]:
        root = Path(need(params, "root", str)).expanduser()
        if self.lib is not None:
            raise RpcError(USER_ERROR, "A library is already open; this engine serves one.")
        result = library.init(root, command="serve")
        return {"status": "exists" if result.already_library else "created",
                "warnings": result.warnings}  # fmt: skip

    def library_open(self, params: dict[str, Any]) -> dict[str, Any]:
        root = Path(need(params, "root", str)).expanduser()
        if self.lib is not None:
            if library.root_path(root) == self.lib.root:
                return {"status": "open"}
            raise RpcError(USER_ERROR, "Another library is already open; restart the engine to "
                           "change libraries.")  # fmt: skip
        self.lib = library.open(root, write=True, command="musicorg serve")
        self.start_queue()
        return {"status": "open"}

    def library_status(self, params: dict[str, Any]) -> dict[str, Any]:
        lib = self._library()
        found = status.get_status(lib.root)
        with self._index() as index:
            only_copy = sum(1 for t in index.library_tracks() if t.get("only_copy"))
        return {"items_by_state": found["items_by_state"], "tracks": found["tracks"],
                "only_copy": only_copy, "queue": found["queue"],
                "warnings": found["warnings"]}  # fmt: skip

    def library_tracks(self, params: dict[str, Any]) -> dict[str, Any]:
        lib = self._library()
        with self._index(write=True) as index:
            return {"root": str(lib.root), "tracks": browse.tracks(lib, index)}

    def library_lyrics(self, params: dict[str, Any]) -> dict[str, Any]:
        return browse.lyrics(self._library(), need(params, "path", str))

    # -- listening: favourites, play counts, playlists (v0.2) --

    def listening_get(self, params: dict[str, Any]) -> dict[str, Any]:
        return listening.get(self._library())

    def listening_favourite(self, params: dict[str, Any]) -> dict[str, Any]:
        track_id, on = need(params, "track_id", str), need(params, "on", bool)
        return {"favourites": listening.set_favourite(self._library(), track_id, on)}

    def listening_played(self, params: dict[str, Any]) -> dict[str, Any]:
        return listening.played(self._library(), need(params, "track_id", str))

    def listening_heard(self, params: dict[str, Any]) -> dict[str, Any]:
        return listening.heard(self._library(), need(params, "video_id", str))

    def listening_move(self, params: dict[str, Any]) -> dict[str, Any]:
        ids, to = need(params, "track_ids", list), need(params, "to", str)
        if to not in ("library", "downloads"):
            raise RpcError(INVALID_PARAMS, "to should be library or downloads.")
        moved = listening.move(self._library(), ids, to_library=to == "library")
        return {"library": moved}

    def playlist_create(self, params: dict[str, Any]) -> dict[str, Any]:
        name = need(params, "name", str)
        return {"playlists": listening.create_playlist(self._library(), name)}

    def playlist_rename(self, params: dict[str, Any]) -> dict[str, Any]:
        playlist_id, name = need(params, "playlist_id", str), need(params, "name", str)
        return {"playlists": listening.rename_playlist(self._library(), playlist_id, name)}

    def playlist_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        playlist_id = need(params, "playlist_id", str)
        return {"playlists": listening.delete_playlist(self._library(), playlist_id)}

    def playlist_set_tracks(self, params: dict[str, Any]) -> dict[str, Any]:
        playlist_id, ids = need(params, "playlist_id", str), need(params, "track_ids", list)
        if not all(isinstance(i, str) for i in ids):
            raise RpcError(INVALID_PARAMS, "track_ids should be a list of track ids.")
        return {"playlists": listening.set_playlist_tracks(self._library(), playlist_id, ids)}

    # -- sources, scan, match --

    def sources_add(self, params: dict[str, Any]) -> dict[str, Any]:
        path = Path(need(params, "path", str))
        with self._index(write=True) as index:
            return {"source": scan.add_source(self._library(), index, path)}

    def sources_list(self, params: dict[str, Any]) -> dict[str, Any]:
        lib = self._library()
        with self._index() as index:
            scanned = index.sources()
        found = []
        for source_id, source in sorted(state.sources(lib.load_state().data).items()):
            found.append({"id": source_id, "path": source.get("path"),
                          "added_at": source.get("added_at"),
                          "scanned_at": scanned.get(source_id, {}).get("scanned_at")})  # fmt: skip
        return {"sources": found}

    def sources_scan(self, params: dict[str, Any]) -> dict[str, Any]:
        ids = want(params, "source_ids", list)
        if ids is not None and not all(isinstance(i, str) for i in ids):
            raise RpcError(INVALID_PARAMS, "source_ids should be a list of source ids.")
        lib = self._library()

        def work(progress: Callable[..., None]) -> dict[str, Any]:
            with open_index(lib.paths, write=True) as index:
                result = scan.scan(lib, index, source_ids=ids, progress=progress)
            self._review_changed()
            return result.to_dict()

        return self._start_job("sources.scan", work)

    def match_run(self, params: dict[str, Any]) -> dict[str, Any]:
        limit = want(params, "limit", int)
        rescan = bool(want(params, "rescan", bool, False))
        if limit is not None and limit < 1:
            raise RpcError(INVALID_PARAMS, "limit must be 1 or more.")
        lib = self._library()

        def work(progress: Callable[..., None]) -> dict[str, Any]:
            def seen(done: int, total: int, seconds_left: float) -> None:
                left = f"about {max(1, round(seconds_left / 60))} min left" if done < total else ""
                progress(done, total, left)

            with open_index(lib.paths, write=True) as index:
                result = match.run(lib, index, limit=limit, rescan=rescan, progress=seen)
            self._review_changed()
            return result.to_dict()

        return self._start_job("match.run", work)

    # -- review --

    def review_list(self, params: dict[str, Any]) -> dict[str, Any]:
        wanted = want(params, "state", str, "review")
        if wanted not in REVIEW_STATES:
            raise RpcError(INVALID_PARAMS, f"state should be one of {', '.join(REVIEW_STATES)}.")
        offset = want(params, "offset", int, 0)
        limit = want(params, "limit", int, 50)
        if offset < 0 or not 1 <= limit <= 500:
            raise RpcError(INVALID_PARAMS, "offset must be 0 or more, and limit 1 to 500.")
        lib = self._library()
        gate = state.gate(lib.load_state().data)
        with self._index() as index:
            folders = scan.source_folders(lib, index)
            items = index.items_in_states([wanted])
            page = [review_item(index, folders, gate, item)
                    for item in items[offset : offset + limit]]  # fmt: skip
        return {"items": page, "total": len(items)}

    def review_decide(self, params: dict[str, Any]) -> dict[str, Any]:
        item_id = need(params, "item_id", str)
        decision = need(params, "decision", str)
        if decision not in RPC_DECISIONS:
            raise RpcError(INVALID_PARAMS, f"decision should be one of {', '.join(RPC_DECISIONS)}.")
        candidate_id = want(params, "candidate_id", str)
        url = want(params, "url", str)
        metadata = want(params, "metadata", dict, {})
        lib = self._library()
        with self._index(write=True) as index:
            video_id = None
            if decision in ("accept", "candidate", "reject"):
                options = index.candidates(item_id)
                if decision == "accept" and candidate_id is None:
                    if not options:
                        raise RpcError(USER_ERROR, "This item has no candidates to accept.")
                    video_id = options[0]["video_id"]
                else:
                    if candidate_id is None:
                        raise RpcError(INVALID_PARAMS, f"{decision} needs a candidate_id.")
                    chosen = next((c for c in options if c["id"] == candidate_id), None)
                    if chosen is None:
                        raise RpcError(NOT_FOUND, f"{candidate_id!r} isn't one of this item's "
                                       "candidates.")  # fmt: skip
                    video_id = chosen["video_id"]
            if decision == "url" and not url:
                raise RpcError(INVALID_PARAMS, "url needs the url parameter.")
            page = {"accept": "use", "candidate": "use"}.get(decision, decision)
            fixes = {k: str(v) for k, v in metadata.items() if isinstance(v, str)}
            review.decide_one(lib, index, item_id, page, video_id=video_id, link=url,
                              fixes=fixes)  # fmt: skip
            item = index.item(item_id)
            assert item is not None
            folders = scan.source_folders(lib, index)
            shown = review_item(index, folders, state.gate(lib.load_state().data), item)
        self._review_changed()
        return {"item": shown}

    # -- plans and the queue --

    def plan_create(self, params: dict[str, Any]) -> dict[str, Any]:
        kind = need(params, "kind", str)
        options = want(params, "options", dict, {})
        lib = self._library()
        with self._index() as index:
            if kind == "replace":
                plan = pipeline.plan_replace(
                    lib, index, only=str(options.get("only", "all-eligible")),
                    limit=want(options, "limit", int), stage_only=bool(options.get("stage_only")),
                )  # fmt: skip
            elif kind == "adopt":
                plan = pipeline.plan_adopt(
                    lib, index, include_not_found=bool(options.get("include_not_found")),
                    matched=bool(options.get("matched")),
                    unconfirmed=bool(options.get("unconfirmed")),
                )  # fmt: skip
            elif kind == "lyrics":
                plan = pipeline.plan_lyrics(lib, index, missing=bool(options.get("missing")))
            elif kind == "artwork":
                plan = pipeline.plan_artwork(lib, index, missing=bool(options.get("missing")))
            elif kind == "tidy":
                plan = pipeline.plan_tidy(lib, index)
            elif kind == "download":
                ids = want(options, "video_ids", list, [])
                videos = want(options, "videos", list, [])
                media = want(options, "media", list, [])
                if not ids and not videos and not media:
                    raise RpcError(INVALID_PARAMS, "Give video_ids, videos or media.")
                known = want(options, "candidates", list, [])
                plan = pipeline.plan_download(
                    lib, index, ids, videos, known=known,
                    playlist_id=want(options, "playlist_id", str), media=media,
                )  # fmt: skip
            elif kind == "edit":
                cover = want(options, "cover_file", str)
                plan = pipeline.plan_edit(
                    lib, index, need(options, "path", str),
                    changes=want(options, "changes", dict, {}),
                    lyrics_text=want(options, "lyrics", str),
                    cover_file=Path(cover).expanduser() if cover else None,
                )  # fmt: skip
            elif kind == "remove":
                plan = pipeline.plan_remove(lib, index, need(options, "paths", list))
            elif kind == "share":
                plan = pipeline.plan_share(
                    lib, index, Path(need(options, "source_root", str)),
                    need(options, "paths", list), playlist_id=want(options, "playlist_id", str),
                )  # fmt: skip
            else:
                raise RpcError(
                    INVALID_PARAMS,
                    "kind should be replace, adopt, lyrics, artwork, tidy, download, edit, "
                    "remove or share.",
                )
        summary = dict(plan.summary)
        for key in ("operations", "downloads", "est_minutes", "low_confidence_adopts"):
            summary.setdefault(key, 0)
        return {"plan_id": plan.plan_id, "summary": summary}

    def plan_get(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"plan": fileops.load_plan(self._library(), need(params, "plan_id", str)).to_dict()}

    def plan_apply(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._index() as index:
            applied = pipeline.apply(self._library(), index, need(params, "plan_id", str))
        self.start_queue()
        return {"batch_id": applied.batch_id}

    def queue_status(self, params: dict[str, Any]) -> dict[str, Any]:
        return queue.status(self._library().paths)

    def queue_pause(self, params: dict[str, Any]) -> dict[str, Any]:
        found = queue.pause(self._library().paths)
        self._queue_state()
        return {"state": found["state"]}

    def queue_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        found = queue.resume(self._library().paths)
        self.start_queue()
        return {"state": found["state"]}

    # -- the journal --

    def journal_batches(self, params: dict[str, Any]) -> dict[str, Any]:
        limit = want(params, "limit", int, 20)
        return {"batches": [b.to_dict() for b in fileops.list_batches(self._library(), limit)]}

    def journal_undo(self, params: dict[str, Any]) -> dict[str, Any]:
        batch_id = need(params, "batch_id", str)
        dry_run = bool(want(params, "dry_run", bool, True))
        lib = self._library()
        if dry_run:
            return {
                "operations": pipeline.undo(lib, batch_id, dry_run=True).to_dict()["operations"]
            }

        def work(progress: Callable[..., None]) -> dict[str, Any]:
            result = pipeline.undo(lib, batch_id).to_dict()
            changed = sum(op["status"] == "done" for op in result["operations"])
            note = {"batch_id": batch_id, "tracks_added": 0, "tracks_changed": changed}
            self.writer.notify("library.changed", note)
            return result

        return self._start_job("journal.undo", work)

    # -- search --

    def settings_get(self, params: dict[str, Any]) -> dict[str, Any]:
        return _settings(Config.load())

    def settings_set(self, params: dict[str, Any]) -> dict[str, Any]:
        cap = want(params, "daily_cap", int)
        if cap is not None:
            return _settings(save_daily_cap(cap))  # config.py does its own writing (rule 3)
        return _settings(Config.load())

    # -- methods: add-ons, where movies and channels are listed (2026-10-07) --
    # Lookups only: nothing is played, downloaded or written in the library.

    def addon_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"addons": addons.listed()}

    def addon_add(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"addons": addons.add(need(params, "address", str))}

    def addon_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"addons": addons.remove(need(params, "addon_id", str))}

    def addon_order(self, params: dict[str, Any]) -> dict[str, Any]:
        ids = need(params, "addon_ids", list)
        if not all(isinstance(one, str) for one in ids):
            raise RpcError(INVALID_PARAMS, "addon_ids should be a list of add-on ids.")
        return {"addons": addons.reorder(ids)}

    def addon_restore(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"addons": addons.restore()}

    def addon_catalog(self, params: dict[str, Any]) -> dict[str, Any]:
        skip = want(params, "skip", int, 0) or 0
        if skip < 0:
            raise RpcError(INVALID_PARAMS, "skip can't be less than 0.")
        return addons.catalog(
            addons.named(need(params, "addon_id", str)),
            need(params, "type", str),
            need(params, "id", str),
            search=want(params, "search", str),
            genre=want(params, "genre", str),
            skip=skip,
        )

    def addon_details(self, params: dict[str, Any]) -> dict[str, Any]:
        return addons.details(
            need(params, "type", str), need(params, "id", str), want(params, "addon_id", str)
        )

    def addon_streams(self, params: dict[str, Any]) -> dict[str, Any]:
        return addons.streams(addons.listed(), need(params, "type", str), need(params, "id", str))

    # -- methods: videos of any kind, and the channels the owner follows (2026-10-07) --

    def video_search(self, params: dict[str, Any]) -> dict[str, Any]:
        query = need(params, "query", str).strip()
        limit = want(params, "limit", int, 25) or 25
        if not query:
            raise RpcError(INVALID_PARAMS, "query is empty.")
        if not 1 <= limit <= youtube.VIDEOS_MOST:
            raise RpcError(INVALID_PARAMS, f"limit must be 1 to {youtube.VIDEOS_MOST}.")
        return {"videos": [v.to_dict() for v in youtube.search_videos(query, limit)]}

    def channel_videos(self, params: dict[str, Any]) -> dict[str, Any]:
        channel_id = need(params, "channel_id", str)
        limit = want(params, "limit", int, 50) or 50
        if not 1 <= limit <= youtube.VIDEOS_MOST:
            raise RpcError(INVALID_PARAMS, f"limit must be 1 to {youtube.VIDEOS_MOST}.")
        lib = self._library()
        found = youtube.channel_videos(channel_id, limit)
        return {
            "channel_id": found.channel_id,
            "name": found.name,
            "followers": found.followers,
            "description": found.description,
            "thumbnail": found.thumbnail,
            "followed": any(c["channel_id"] == channel_id for c in listening.followed(lib)),
            "videos": [v.to_dict() for v in found.videos],
        }

    def channel_search(self, params: dict[str, Any]) -> dict[str, Any]:
        query = need(params, "query", str).strip()
        limit = want(params, "limit", int, 24) or 24
        if not query:
            raise RpcError(INVALID_PARAMS, "query is empty.")
        if not 1 <= limit <= youtube.VIDEOS_MOST:
            raise RpcError(INVALID_PARAMS, f"limit must be 1 to {youtube.VIDEOS_MOST}.")
        return {"channels": youtube.search_channels(query, limit)}

    def channel_follow(self, params: dict[str, Any]) -> dict[str, Any]:
        channels = listening.follow(
            self._library(),
            need(params, "channel_id", str),
            need(params, "on", bool),
            name=want(params, "name", str, "") or "",
            thumbnail=want(params, "thumbnail", str),
        )
        return {"channels": channels}

    def channel_followed(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"channels": listening.followed(self._library())}

    # -- methods: a film played from a torrent while it arrives (2026-10-07) --
    # Only ever from the owner's click. What arrives is kept in the app's cache folder,
    # never in the library, and is deleted when the film is closed.

    def _stop_films(self) -> None:
        films, self._films = self._films, None
        if films is not None:
            films.stop()

    def torrent_play(self, params: dict[str, Any]) -> dict[str, Any]:
        info_hash = need(params, "info_hash", str)
        index = want(params, "file_index", int)
        trackers = want(params, "trackers", list, []) or []
        if not all(isinstance(t, str) for t in trackers):
            raise RpcError(INVALID_PARAMS, "trackers should be a list of addresses.")
        return self._film_player().play(info_hash, index, trackers)

    def _film_player(self) -> torrents.Player:
        if self._films is None:
            self._films = torrents.Player(app_dirs().cache, movies=media_folders()["movies"])
        return self._films

    def torrent_keep(self, params: dict[str, Any]) -> dict[str, Any]:
        """Keep a film in the Movies folder once all of it has arrived. Only ever from
        the owner's click."""
        title = need(params, "title", str).strip()
        year = (want(params, "year", str) or "").strip()
        trackers = want(params, "trackers", list, []) or []
        if not title:
            raise RpcError(INVALID_PARAMS, "title is empty.")
        if not all(isinstance(t, str) for t in trackers):
            raise RpcError(INVALID_PARAMS, "trackers should be a list of addresses.")
        name = f"{title} ({year})" if year.isdigit() and len(year) == 4 else title
        return self._film_player().keep(
            need(params, "info_hash", str), want(params, "file_index", int), trackers, name,
            convert=bool(want(params, "convert", bool, False)),
        )  # fmt: skip

    def torrent_status(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._films is None:
            raise RpcError(USER_ERROR, "No film is being played from a torrent.")
        return self._films.status(need(params, "info_hash", str))

    def torrent_stop(self, params: dict[str, Any]) -> dict[str, Any]:
        info_hash = want(params, "info_hash", str)
        if self._films is not None:
            if info_hash is None:
                self._stop_films()
            else:
                self._films.close(info_hash)
        return {}

    # -- methods: a child's profile (2026-10-07) --

    def kids_set(self, params: dict[str, Any]) -> dict[str, Any]:
        """Whether this engine is running for a child's profile, where only clean songs
        are looked up, and whether a song with no clean version may be shown as it is."""
        on = need(params, "on", bool)
        allow = on and bool(want(params, "allow_explicit", bool, False))
        self._kids = {"on": on, "allow_explicit": allow}
        return dict(self._kids)

    def _clean(self, answer: dict[str, Any], key: str, note: str = "kids_note") -> dict[str, Any]:
        """A lookup's answer as a child's profile gets it: `answer[key]` with only clean
        songs, and a sentence about what was left out. Anyone else's is untouched."""
        if not self._kids["on"] or not isinstance(answer.get(key), list):
            return answer
        shown, said = kids.clean_only(answer[key], allow_explicit=self._kids["allow_explicit"])
        notes = " ".join(part for part in (answer.get(note), said) if part) or None
        return {**answer, key: shown, note: notes}

    # -- methods: sharing the library with a phone player at home (2026-10-04) --

    def _stop_sharing(self) -> None:
        share, self._share = self._share, None
        if share is not None:
            share.stop()

    def sharing_status(self, params: dict[str, Any]) -> dict[str, Any]:
        lib = self._library()
        return self._share.status() if self._share is not None else sharing.status_off(lib)

    def sharing_set(self, params: dict[str, Any]) -> dict[str, Any]:
        """Switch sharing on or off. On, this engine listens on the home network until
        it's switched off or the engine stops; it never starts listening by itself."""
        on = need(params, "on", bool)
        lib = self._library()
        if on and self._share is None:
            share = sharing.Share(lib, changed=lambda: self.writer.notify("sharing.changed", {}))
            share.start()
            self._share = share
        elif not on:
            self._stop_sharing()
        return self.sharing_status({})

    def sharing_pair(self, params: dict[str, Any]) -> dict[str, Any]:
        """A six-digit code for the app to show, which a phone types to be paired."""
        self._library()
        if self._share is None:
            raise RpcError(USER_ERROR, "Switch sharing on first, then pair a device.")
        return self._share.new_code()

    def sharing_stop_pairing(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._share is not None:
            self._share.stop_pairing()
        return self.sharing_status({})

    def sharing_forget(self, params: dict[str, Any]) -> dict[str, Any]:
        self._library()
        sharing.forget(need(params, "device_id", str))
        return self.sharing_status({})

    def youtube_stream(self, params: dict[str, Any]) -> dict[str, Any]:
        found = youtube.stream(need(params, "video_id", str))
        return {"url": found.url, "http_headers": found.headers, "duration_s": found.duration_s,
                "likes": found.likes}  # fmt: skip

    def youtube_video(self, params: dict[str, Any]) -> dict[str, Any]:
        """A song's official music video, to play in the app: its sound, and its picture
        in each size on offer. Nothing is saved."""
        title, artist = need(params, "title", str), need(params, "artist", str)
        path = want(params, "path", str)
        video_id = want(params, "video_id", str)
        if video_id is not None:
            # A video played as itself (a channel's video, a trailer): that very one,
            # with nothing looked for by name.
            if not youtube.VIDEO_ID.fullmatch(video_id):
                raise RpcError(INVALID_PARAMS, "That isn't a video's id.")
            return self._video_answer(youtube.video(video_id), title, None)
        # A library song's file says which version it is: its version tag, and its title
        # as the owner writes a remix ("Song R"). Without it a remix would get the
        # original's video.
        versions = browse.version_tokens(self._library(), path) if path else ()
        with self._index(write=True) as index:  # the index keeps the search's answer
            match = youtube.find_video(title, artist, versions=versions, cache=index)
        if match is None:
            return {"found": False}
        return self._video_answer(youtube.video(match.video_id), match.title, match.duration_s)

    def _video_answer(self, found: Any, title: str, duration_s: float | None) -> dict[str, Any]:
        if not found.qualities:
            return {"found": False}

        def address(quality: Any) -> str:
            if not found.segmented:
                return quality.url
            # A long video: a list written here of this picture size with the sound,
            # read by the app's player from an address on this computer (`relay`).
            if self._relay is None:
                self._relay = relay.Relay()
            return self._relay.address_of(
                relay.master_playlist(
                    quality.url,
                    found.audio.url,
                    picture_codec=quality.codec,
                    sound_codec=found.sound_codec,
                    kbps=quality.kbps,
                    width=quality.width,
                    height=quality.height,
                    fps=quality.fps,
                )  # fmt: skip
            )

        return {
            "found": True,
            "video_id": found.video_id,
            "title": title,
            "duration_s": found.audio.duration_s or duration_s,
            "http_headers": found.audio.headers,
            "audio_url": found.audio.url,
            "likes": found.audio.likes,
            "segmented": found.segmented,
            "qualities": [{"label": q.label, "height": q.height, "fps": q.fps, "url": address(q)}
                          for q in found.qualities],
        }  # fmt: skip

    def lyrics_find(self, params: dict[str, Any]) -> dict[str, Any]:
        """Lyrics for a song being played from YouTube Music. Looked up, never saved."""
        length = want(params, "duration_s", (int, float))
        query = lyrics.Query(
            title=need(params, "title", str),
            artist=want(params, "artist", str, "") or "",
            album=want(params, "album", str),
            duration_s=float(length) if length is not None else None,
            video_id=want(params, "video_id", str),
            official_s=float(length) if length is not None else None,
        )
        with self._index(write=True) as index:  # the index keeps the answer for next time
            found = lyrics.find(query, cache=index)
        return {"synced": found.synced, "plain": found.plain, "source": found.source}

    def queue_jobs(self, params: dict[str, Any]) -> dict[str, Any]:
        batch_id = need(params, "batch_id", str)
        with open_queue(self._library().paths, write=False) as store:
            jobs = store.jobs(batch_id=batch_id)
        return {"jobs": [{"job_id": job["id"], "kind": job["kind"], "state": job["state"],
                          "reason": job["reason"], "message": job["last_error"]}
                         for job in jobs]}  # fmt: skip

    def queue_downloads(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"downloads": queue.downloads(self._library().paths)}

    def queue_dismiss(self, params: dict[str, Any]) -> dict[str, Any]:
        lib = self._library()
        if want(params, "waiting", bool, False):
            queue.dismiss_waiting(lib)  # every download that hasn't started
        else:
            queue.dismiss_download(lib, need(params, "job_id", int))
        return {"downloads": queue.downloads(lib.paths)}

    def lyrics_for_video(self, params: dict[str, Any]) -> dict[str, Any]:
        """A song's lyrics timed to its video, for the app to show while the video
        plays. Without `full` YouTube is asked nothing: only what's already known is
        given. With it (the app's Karaoke button) the video's sound is lined up with
        the song's and checked against the video's captions. Never saved in the
        library."""
        video_length = want(params, "video_duration_s", (int, float))
        song_length = want(params, "song_duration_s", (int, float))
        with self._index(write=True) as index:  # the index keeps what's worked out
            found = videolyrics.for_video(
                self._library(), index,
                title=need(params, "title", str),
                artist=want(params, "artist", str, "") or "",
                video_id=need(params, "video_id", str),
                video_duration_s=float(video_length) if video_length is not None else None,
                song_path=want(params, "song_path", str),
                song_video_id=want(params, "song_video_id", str),
                song_duration_s=float(song_length) if song_length is not None else None,
                video_path=want(params, "video_path", str),
                full=bool(want(params, "full", bool, False)),
            )  # fmt: skip
        return found.to_dict()

    def discover_suggest(self, params: dict[str, Any]) -> dict[str, Any]:
        """Songs the owner doesn't have, found from ones they do (Discover, v0.4).
        Lookups only: nothing is downloaded and the library isn't changed."""
        seeds = [discover.Seed.from_dict(seed) for seed in need(params, "seeds", list)]
        count = want(params, "count", int, 50)
        shuffle = want(params, "shuffle", str, "") or ""
        token = want(params, "token", str)
        exclude = want(params, "exclude", list, [])
        if len(exclude) > MAX_EXCLUDE or not all(isinstance(v, str) for v in exclude):
            raise RpcError(INVALID_PARAMS, f"exclude should be at most {MAX_EXCLUDE} video ids.")

        def progress(done: int, total: int) -> None:
            self.writer.notify("discover.progress", {"token": token, "done": done, "of": total})

        with self._index(write=True) as index:  # the index keeps YouTube Music's answers
            found = discover.suggest(
                self._library(), index, seeds, count, shuffle=shuffle, exclude=exclude,
                progress=progress,
            )  # fmt: skip
        return self._clean(found, "picks", note="note")

    def import_playlist(self, params: dict[str, Any]) -> dict[str, Any]:
        """A playlist from elsewhere, as its name and its songs (imports, v0.3). Only
        read: nothing is looked for or downloaded yet."""
        source = need(params, "source", str)
        self._library()
        if source == "youtube":
            return imports.from_youtube(need(params, "link", str))
        if source == "spotify":
            return imports.from_spotify(need(params, "playlist_id", str))
        if source == "deezer":
            return imports.from_deezer(need(params, "link", str))
        if source == "lastfm":
            return imports.from_lastfm(need(params, "list", str))
        if source == "file":
            return imports.from_file(need(params, "path", str), want(params, "playlist", str))
        raise RpcError(INVALID_PARAMS, f"source should be one of: {', '.join(imports.SOURCES)}.")

    def import_playlists(self, params: dict[str, Any]) -> dict[str, Any]:
        """The playlists a signed-in service has, to choose one from."""
        if need(params, "source", str) != "spotify":
            raise RpcError(INVALID_PARAMS, "source should be spotify.")
        return {"playlists": imports.spotify_playlists()}

    # -- methods: sign-ins (for reading playlists) --

    def account_status(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"spotify": spotify.status(), "lastfm": lastfm.status()}

    def account_connect(self, params: dict[str, Any]) -> dict[str, Any]:
        """Save the owner's Last.fm username and API key, once Last.fm has answered for
        them. There's no password and no sign-in page: Last.fm answers about any public
        profile, but only to an app with a key."""
        if need(params, "service", str) != "lastfm":
            raise RpcError(INVALID_PARAMS, "service should be lastfm.")
        lastfm.connect(need(params, "user", str), want(params, "api_key", str))
        return self.account_status({})

    def account_sign_in(self, params: dict[str, Any]) -> dict[str, Any]:
        """Start signing in to Spotify: the engine listens on this computer for
        Spotify's answer, and the app opens the address given in the browser. The
        owner's password goes to Spotify's own page and nowhere else."""
        if need(params, "service", str) != "spotify":
            raise RpcError(INVALID_PARAMS, "service should be spotify.")

        def done(problem: str | None) -> None:
            self.writer.notify("account.changed", {"service": "spotify",
                                                   "signed_in": problem is None,
                                                   "problem": problem})  # fmt: skip

        return {"authorize_url": spotify.begin_sign_in(want(params, "client_id", str), done)}

    def account_sign_out(self, params: dict[str, Any]) -> dict[str, Any]:
        service = need(params, "service", str)
        if service == "spotify":
            spotify.sign_out()
        elif service == "lastfm":
            lastfm.forget()
        else:
            raise RpcError(INVALID_PARAMS, "service should be spotify or lastfm.")
        return self.account_status({})

    def import_find(self, params: dict[str, Any]) -> dict[str, Any]:
        """Each imported song found on YouTube Music, or known to be the owner's
        already. Lookups only; up to a hundred songs a call."""
        tracks = need(params, "tracks", list)
        token = want(params, "token", str)

        def progress(done: int, total: int) -> None:
            self.writer.notify("import.progress", {"token": token, "done": done, "of": total})

        with self._index(write=True) as index:  # the index keeps YouTube Music's answers
            found = imports.find(self._library(), index, tracks, progress=progress)
        if self._kids["on"]:
            allow = self._kids["allow_explicit"]
            found = [kids.clean_found(one, allow_explicit=allow) for one in found]
        return {"found": found}

    # -- methods: the Artist page --

    def artist_search(self, params: dict[str, Any]) -> dict[str, Any]:
        """The artists YouTube Music finds for what was typed (the Artists page)."""
        query = need(params, "query", str)
        self._library()
        with self._index(write=True) as index:  # the index keeps YouTube Music's answers
            return artist.search(index, query)

    def artist_info(self, params: dict[str, Any]) -> dict[str, Any]:
        """An artist's page on YouTube Music, by their name or their id, with which of
        their songs the owner has. Lookups only."""
        name, artist_id = want(params, "name", str), want(params, "artist_id", str)
        if (name is None) == (artist_id is None):
            raise RpcError(INVALID_PARAMS, "Give a name or an artist_id (one of them).")
        with self._index(write=True) as index:  # the index keeps YouTube Music's answers
            found = artist.info(self._library(), index, name=name, artist_id=artist_id)
        return self._clean(found, "songs")

    def artist_songs(self, params: dict[str, Any]) -> dict[str, Any]:
        playlist_id = need(params, "playlist_id", str)
        with self._index(write=True) as index:
            return self._clean(artist.songs(self._library(), index, playlist_id), "songs")

    def artist_album(self, params: dict[str, Any]) -> dict[str, Any]:
        browse_id = need(params, "browse_id", str)
        with self._index(write=True) as index:
            return self._clean(artist.album(self._library(), index, browse_id), "songs")

    def search_ytmusic(self, params: dict[str, Any]) -> dict[str, Any]:
        query = need(params, "query", str).strip()
        limit = want(params, "limit", int, 10)
        if not query:
            raise RpcError(INVALID_PARAMS, "query is empty.")
        if not 1 <= limit <= 100:
            raise RpcError(INVALID_PARAMS, "limit must be 1 to 100.")
        found = youtube.search_songs(query, limit)
        results = []
        for c in found:
            data = c.to_dict()
            data.update(candidate_id=match.candidate_id("search", c.video_id), version_tokens=[],
                        score=None, reasons=[])  # fmt: skip
            results.append(data)
        return self._clean({"results": results}, "results")


def review_item(
    index: Any, folders: dict[str, Path], gate: dict[str, Any], item: dict[str, Any]
) -> dict[str, Any]:
    """An item in the ReviewItem shape (docs/ENGINE_API.md → Shapes)."""
    candidates = [c["payload"] for c in index.candidates(item["id"])]
    first = candidates[0]["video_id"] if candidates else None
    verdict = gate.get(item["id"], {}).get(first or "", None) if first else None
    return {
        "item_id": item["id"],
        "source_path": scan.item_path(folders, item),
        "parsed": {
            "artist": item.get("parsed_artist"),
            "title": item.get("parsed_title"),
            "version_tokens": item.get("parsed_version_json") or [],
            "confidence": item.get("parse_confidence"),
        },  # fmt: skip
        "duration_s": item.get("duration_s"),
        "state": item["state"],
        "reasons": item.get("reasons_json") or [],
        "candidates": candidates,
        "fingerprint": verdict,
    }


def _version(package: str) -> str | None:
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def serve() -> int:
    """`musicorg serve`: speak JSON-RPC on stdin and stdout until stdin closes."""
    proto = protect_stdout()
    reader = sys.stdin.buffer
    server = Server(reader, Writer(proto))
    if hasattr(signal, "SIGTERM") and threading.current_thread() is threading.main_thread():

        def on_term(signum: int, frame: Any) -> None:
            server.stopping.set()
            raise Stop  # interrupts the read loop, which then shuts down cleanly

        signal.signal(signal.SIGTERM, on_term)
    log.info("serve: started (engine %s)", __version__)
    return server.run()
