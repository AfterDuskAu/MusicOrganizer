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
  the engine stops. One further long operation at a time (`sources.scan`, `match.run`,
  `journal.undo`) returns `{job_id}` at once and reports `job.progress` (at most 4 a
  second) and `job.finished`. A second one while busy gets -32007.
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
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import IO, Any

from musicorg import (
    __version__,
    browse,
    fileops,
    library,
    logging_setup,
    match,
    pipeline,
    queue,
    review,
    scan,
    state,
    status,
    tags,
    youtube,
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
from musicorg.index import open_index

log = logging.getLogger(__name__)

PROTOCOL = "2.0"
SHUTDOWN_GRACE_S = 10.0
PROGRESS_INTERVAL_S = 0.25  # at most 4 job.progress a second per job
REVIEW_STATES = ("review", "not_found", "matched_auto")
RPC_DECISIONS = ("accept", "candidate", "url", "only_copy", "skip", "reject")

# Error codes (docs/ENGINE_API.md → Errors).
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL = (
    -32700, -32600, -32601, -32602, -32603,
)  # fmt: skip
USER_ERROR = -32000  # the request couldn't be done; `message` says why
LOCKED, OUTSIDE, TOOL_MISSING, YOUTUBE_PAUSED, PLAN_OUT_OF_DATE, NOT_FOUND, BUSY = (
    -32001, -32002, -32003, -32004, -32005, -32006, -32007,
)  # fmt: skip


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
        self.methods: dict[str, Callable[[dict[str, Any]], Any]] = {
            "engine.hello": self.engine_hello,
            "library.init": self.library_init,
            "library.open": self.library_open,
            "library.status": self.library_status,
            "library.tracks": self.library_tracks,
            "library.lyrics": self.library_lyrics,
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
        }

    # -- the loop --

    def run(self) -> int:
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
                self._queue_state()

            self._queue_thread = threading.Thread(target=target, name="queue", daemon=True)
            self._queue_thread.start()

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
                )  # fmt: skip
            elif kind == "lyrics":
                plan = pipeline.plan_lyrics(lib, index, missing=bool(options.get("missing")))
            elif kind == "artwork":
                plan = pipeline.plan_artwork(lib, index, missing=bool(options.get("missing")))
            elif kind == "tidy":
                plan = pipeline.plan_tidy(lib, index)
            else:
                raise RpcError(
                    INVALID_PARAMS, "kind should be replace, adopt, lyrics, artwork or tidy."
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

    def search_ytmusic(self, params: dict[str, Any]) -> dict[str, Any]:
        query = need(params, "query", str).strip()
        limit = want(params, "limit", int, 10)
        if not query:
            raise RpcError(INVALID_PARAMS, "query is empty.")
        if not 1 <= limit <= 50:
            raise RpcError(INVALID_PARAMS, "limit must be 1 to 50.")
        found = youtube.search_songs(query, limit)
        results = []
        for c in found:
            data = c.to_dict()
            data.update(candidate_id=match.candidate_id("search", c.video_id), version_tokens=[],
                        score=None, reasons=[])  # fmt: skip
            results.append(data)
        return {"results": results}


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
