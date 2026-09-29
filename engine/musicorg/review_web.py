"""The local review page: `musicorg review serve` (added at the step 07 checkpoint, at the
owner's request; the Mac app stays v0.2).

It shows the items that need a decision in the browser, plays each rip and its
candidates side by side, and records every click as a decision through
`review.decide_one`, with the same checks and effects as a row of `review import`.

Safety:
- It listens on 127.0.0.1 only, and every request must carry the session's secret key
  (from the page's address) and a Host of 127.0.0.1 or localhost with its port. Other
  web pages open in the same browser can't use it (cross-site requests, DNS
  rebinding).
- A rip is played by its item id only, read-only; no path ever comes from the browser.
- The command holds the library's lock while it runs, since it writes decisions.
- A candidate is heard by opening it in YouTube Music, in one browser tab the page
  reuses (YouTube's embedded player won't play many official tracks on other pages).
  The engine itself only calls YouTube for a pasted link, through `youtube` as always.
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from musicorg import review, scan, state
from musicorg.errors import MusicOrgError
from musicorg.index import open_index
from musicorg.library import Library
from musicorg.normalize import render_versions
from musicorg.report import REASON_MEANING, minutes
from musicorg.youtube import Candidate

log = logging.getLogger(__name__)

PAGE = Path(__file__).with_name("review_page.html")
GROUPS = ("doubts", "length", "not_found", "auto", "decided")
DECIDED = frozenset(scan.DECISION_STATES.values())
AUDIO_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
               ".flac": "audio/flac", ".wav": "audio/wav", ".ogg": "audio/ogg",
               ".opus": "audio/ogg", ".webm": "audio/webm"}  # fmt: skip
CHUNK = 64 * 1024


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, lib: Library, port: int = 0) -> None:
        super().__init__(("127.0.0.1", port), _Handler)
        self.lib = lib
        self.key = secrets.token_urlsafe(24)
        self.deciding = threading.Lock()  # one decision at a time

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}/?key={self.key}"

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Into the log, not the terminal. A browser dropping a connection is normal."""
        import sys

        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError)):
            log.debug("review page: %s dropped the connection", client_address)
        else:
            log.warning("review page: a request failed", exc_info=True, extra={"console": False})


def serve(
    lib: Library,
    *,
    port: int = 0,
    open_browser: bool = True,
    ready: Callable[[str], None] | None = None,
) -> None:
    """Run the review page until Ctrl-C."""
    import webbrowser

    server = ReviewServer(lib, port)
    try:
        if ready is not None:
            ready(server.url)
        if open_browser:
            webbrowser.open(server.url)
        server.serve_forever()
    finally:
        server.server_close()


# ---- what the page shows ---------------------------------------------------------------


def group_of(item: dict[str, Any]) -> str | None:
    if item["state"] == "review":
        return "length" if item.get("reasons_json") == ["duration_mismatch"] else "doubts"
    if item["state"] == "not_found":
        return "not_found"
    if item["state"] == "matched_auto":
        return "auto"
    if item["state"] in DECIDED:
        return "decided"
    return None


def item_view(
    item: dict[str, Any],
    options: list[dict[str, Any]],
    decision: dict[str, Any] | None,
    folders: dict[str, Path],
) -> dict[str, Any]:
    seconds = item.get("duration_s")
    shown = []
    for option in options[: review.CANDIDATES]:
        payload = option["payload"]
        candidate = Candidate.from_dict(payload)
        delta = (
            round(candidate.duration_s - seconds)
            if seconds is not None and candidate.duration_s is not None
            else None
        )
        shown.append({
            "video_id": candidate.video_id,
            "title": candidate.title,
            "artists": list(candidate.artists),
            "album": candidate.album,
            "duration": minutes(candidate.duration_s),
            "delta_s": delta,
            "score": round(option["score"], 3),
            "versions": render_versions(payload.get("version_tokens") or []),
            "explicit": candidate.is_explicit,
            "official": candidate.is_official_audio,
            "thumbnail": candidate.thumbnail,
            "link": candidate.link,
            "notes": [r for r in option["reasons"] if r not in REASON_MEANING],
        })  # fmt: skip
    rel = item["rel_path"]
    return {
        "id": item["id"],
        "file": rel.rsplit("/", 1)[-1],
        "path": scan.item_path(folders, item),
        "artist": item.get("parsed_artist") or "",
        "title": item.get("parsed_title") or "",
        "versions": render_versions(item.get("parsed_version_json") or []),
        "duration": minutes(seconds),
        "format": (item.get("ext") or "").lstrip(".").upper(),
        "kbps": item.get("bitrate_kbps"),
        "state": item["state"],
        "group": group_of(item),
        "reasons": [
            {"code": c, "text": REASON_MEANING.get(c, c)} for c in item.get("reasons_json") or []
        ],  # fmt: skip
        "decision": decision,
        "candidates": shown,
    }


def _sort_key(group: str, view: dict[str, Any]) -> tuple[Any, ...]:
    best = view["candidates"][0] if view["candidates"] else None
    if group == "length" and best and best["delta_s"] is not None:
        return (abs(best["delta_s"]), view["path"])  # the smallest differences first
    if group in ("doubts", "not_found"):
        return (-(best["score"] if best else 0), view["path"])  # most likely first
    return (view["artist"].casefold(), view["title"].casefold())


# ---- requests --------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    server: ReviewServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("review page: " + format, *args)

    # ---- routing ----

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if not self._allowed(url):
            return
        if url.path == "/":
            self._send(HTTPStatus.OK, PAGE.read_bytes(), "text/html; charset=utf-8", page=True)
        elif url.path == "/api/summary":
            self._json(self._summary())
        elif url.path == "/api/items":
            group = parse_qs(url.query).get("group", ["doubts"])[0]
            if group not in GROUPS:
                self._error(HTTPStatus.BAD_REQUEST, f"Unknown group {group!r}.")
            else:
                self._json({"items": self._items(group)})
        elif url.path.startswith("/api/audio/"):
            self._audio(url.path.removeprefix("/api/audio/"))
        else:
            self._error(HTTPStatus.NOT_FOUND, "Not found.")

    def do_POST(self) -> None:
        url = urlsplit(self.path)
        if not self._allowed(url, post=True):
            return
        if url.path != "/api/decide":
            self._error(HTTPStatus.NOT_FOUND, "Not found.")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(length, 1_000_000)) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "The request wasn't valid JSON.")
            return
        try:
            self._json(self._decide(body))
        except MusicOrgError as exc:
            self._error(HTTPStatus.BAD_REQUEST, exc.message)

    # ---- the checks ----

    def _allowed(self, url: Any, *, post: bool = False) -> bool:
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in hosts:
            self._error(HTTPStatus.FORBIDDEN, "Wrong host.")
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{h}" for h in hosts}:
            self._error(HTTPStatus.FORBIDDEN, "Wrong origin.")
            return False
        key = self.headers.get("X-Review-Key") or parse_qs(url.query).get("key", [""])[0]
        if not hmac.compare_digest(key.encode(), self.server.key.encode()):
            self._error(HTTPStatus.FORBIDDEN, "This page needs the address the command printed.")
            return False
        if post and not (self.headers.get("Content-Type") or "").startswith("application/json"):
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Send JSON.")
            return False
        return True

    # ---- the API ----

    def _summary(self) -> dict[str, Any]:
        lib = self.server.lib
        with open_index(lib.paths, write=False) as index:
            counts = dict.fromkeys(GROUPS, 0)
            for item in index.items():
                group = group_of(item)
                if group:
                    counts[group] += 1
        return {"library": str(lib.root), "groups": counts}

    def _items(self, group: str) -> list[dict[str, Any]]:
        lib = self.server.lib
        decisions = state.decisions(lib.load_state().data)
        with open_index(lib.paths, write=False) as index:
            folders = scan.source_folders(lib, index)
            candidates = index.all_candidates()
            views = [item_view(i, candidates.get(i["id"], []), decisions.get(i["id"]), folders)
                     for i in index.items() if group_of(i) == group]  # fmt: skip
        return sorted(views, key=lambda v: _sort_key(group, v))

    def _decide(self, body: dict[str, Any]) -> dict[str, Any]:
        lib = self.server.lib
        item_id = str(body.get("item_id") or "")
        decision = str(body.get("decision") or "")
        ids = body.get("video_ids") or ([body["video_id"]] if body.get("video_id") else [None])
        warnings: list[str] = []
        changed = False
        with self.server.deciding, open_index(lib.paths, write=True) as index:
            for video_id in ids:
                result = review.decide_one(
                    lib, index, item_id, decision,
                    video_id=video_id, link=body.get("link"), fixes=body.get("fixes"),
                )  # fmt: skip
                changed = changed or result.changed
                if result.warning:
                    warnings.append(result.warning)
            item = index.item(item_id)
            assert item is not None
            decisions = state.decisions(lib.load_state().data)
            view = item_view(item, index.candidates(item_id), decisions.get(item_id),
                             scan.source_folders(lib, index))  # fmt: skip
        return {"ok": True, "changed": changed, "warnings": warnings, "item": view}

    def _audio(self, item_id: str) -> None:
        lib = self.server.lib
        with open_index(lib.paths, write=False) as index:
            item = index.item(item_id)
            folders = scan.source_folders(lib, index)
        if item is None or item["source_id"] not in folders:
            self._error(HTTPStatus.NOT_FOUND, "No such item.")
            return
        path = folders[item["source_id"]] / item["rel_path"]
        try:
            size = path.stat().st_size
            f = open(path, "rb")
        except OSError:
            self._error(HTTPStatus.NOT_FOUND, "The file isn't there (is its drive connected?).")
            return
        with f:
            start, end = 0, size - 1
            status = HTTPStatus.OK
            wanted = self.headers.get("Range", "")
            if wanted.startswith("bytes=") and "," not in wanted:
                first, _, last = wanted[6:].partition("-")
                try:
                    if first:
                        start = int(first)
                        end = min(int(last), size - 1) if last else size - 1
                    elif last:
                        start = max(0, size - int(last))
                except ValueError:
                    start, end = 0, size - 1
                if start > end or start >= size:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = HTTPStatus.PARTIAL_CONTENT
            self.send_response(status)
            self.send_header("Content-Type", AUDIO_TYPES.get(path.suffix.lower(), "audio/mpeg"))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(CHUNK, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the browser stopped listening (skipped ahead, or moved on)

    # ---- responses ----

    def _json(self, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")

    def _error(self, status: HTTPStatus, message: str) -> None:
        body = json.dumps({"ok": False, "error": message}).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send(self, status: HTTPStatus, body: bytes, kind: str, *, page: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        if page:
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; media-src 'self'; "
                "img-src 'self' data: https://*.googleusercontent.com https://*.ytimg.com",
            )
        self.end_headers()
        self.wfile.write(body)
