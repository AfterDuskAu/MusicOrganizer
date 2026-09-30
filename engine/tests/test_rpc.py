"""rpc (step 11): the JSON-RPC server for the app. In-process tests check every method's
shape and every error code; subprocess tests drive `musicorg serve` itself: the
handshake, shutdown on EOF with the lock released, and a stdout that nothing else can
write to."""

from __future__ import annotations

import ast
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import require_tool
from index_support import add_candidates, add_item, add_source, candidate

from musicorg import library, rpc
from musicorg.index import open_index

ENGINE = Path(__file__).resolve().parents[1]
MODULES = ENGINE / "musicorg"


class Capture(io.RawIOBase):
    """A thread-safe byte sink that remembers every line written."""

    def __init__(self) -> None:
        self.data = bytearray()
        self.lock = threading.Lock()

    def writable(self) -> bool:
        return True

    def write(self, b: Any) -> int:
        with self.lock:
            self.data += bytes(b)
        return len(b)

    def lines(self) -> list[dict[str, Any]]:
        with self.lock:
            text = self.data.decode("utf-8")
        return [json.loads(line) for line in text.splitlines()]

    def notes(self, method: str) -> list[dict[str, Any]]:
        return [m["params"] for m in self.lines() if m.get("method") == method]


@pytest.fixture
def out() -> Capture:
    return Capture()


@pytest.fixture
def server(out: Capture) -> Iterator[rpc.Server]:
    srv = rpc.Server(io.BytesIO(), rpc.Writer(out))  # type: ignore[arg-type]
    yield srv
    srv.shutdown()


def call(srv: rpc.Server, method: str, **params: Any) -> dict[str, Any]:
    reply = srv.handle({"jsonrpc": "2.0", "id": 7, "method": method, "params": params})
    assert reply is not None and reply["id"] == 7
    return reply


def result(srv: rpc.Server, method: str, **params: Any) -> Any:
    reply = call(srv, method, **params)
    assert "error" not in reply, reply
    return reply["result"]


def code(srv: rpc.Server, method: str, **params: Any) -> int:
    reply = call(srv, method, **params)
    assert "error" in reply, reply
    assert isinstance(reply["error"]["message"], str) and reply["error"]["message"]
    return reply["error"]["code"]


def wait_job(srv: rpc.Server) -> None:
    assert srv._job is not None
    srv._job.thread.join(60)
    assert not srv._job.thread.is_alive()


def wait_queue(srv: rpc.Server) -> None:
    if srv._queue_thread is not None:
        srv._queue_thread.join(60)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    folder = tmp_path / "Library"
    library.init(folder)
    return folder


@pytest.fixture
def opened(server: rpc.Server, root: Path) -> rpc.Server:
    result(server, "engine.hello", client="pytest", client_version="1")
    assert result(server, "library.open", root=str(root)) == {"status": "open"}
    wait_queue(server)
    return server


# ---- the handshake and errors -----------------------------------------------------------


def test_hello_comes_first(server: rpc.Server) -> None:
    assert code(server, "library.status") == rpc.INVALID_REQUEST
    hello = result(server, "engine.hello", client="mac-app", client_version="0.2.0")
    assert set(hello) == {"engine_version", "schema_version", "ytdlp_version",
                          "ytmusicapi_version", "capabilities"}  # fmt: skip
    assert "review.decide" in hello["capabilities"]
    assert hello["ytdlp_version"] and hello["ytmusicapi_version"]


def test_protocol_errors(server: rpc.Server, out: Capture) -> None:
    server.handle_line(b"not json\n")
    server.handle_line(b'[{"jsonrpc": "2.0", "id": 1, "method": "engine.hello"}]\n')
    server.handle_line(b'{"id": 2, "method": "engine.hello"}\n')  # no "jsonrpc": "2.0"
    assert [m["error"]["code"] for m in out.lines()] == [
        rpc.PARSE_ERROR, rpc.INVALID_REQUEST, rpc.INVALID_REQUEST,
    ]  # fmt: skip
    result(server, "engine.hello", client="x")
    assert code(server, "no.such.method") == rpc.METHOD_NOT_FOUND
    assert code(server, "library.open", root=5) == rpc.INVALID_PARAMS
    assert code(server, "library.open") == rpc.INVALID_PARAMS
    assert code(server, "library.status") == rpc.USER_ERROR  # no library yet
    # A notification from the app (no id) gets no reply, even an error.
    assert server.handle({"jsonrpc": "2.0", "method": "no.such.method"}) is None


def test_engine_errors(opened: rpc.Server, tmp_path: Path) -> None:
    assert code(opened, "plan.get", plan_id="p_nope") == rpc.NOT_FOUND
    assert code(opened, "journal.undo", batch_id="b_nope", dry_run=True) == rpc.NOT_FOUND
    assert code(opened, "sources.add", path=str(tmp_path / "missing")) == rpc.USER_ERROR
    assert code(opened, "review.list", state="nonsense") == rpc.INVALID_PARAMS


def test_error_mapping() -> None:
    from datetime import UTC, datetime

    from musicorg.errors import (
        LibraryLockedError,
        OutsideLibraryError,
        PlanOutOfDateError,
        ToolMissingError,
        YouTubePausedError,
    )

    when = datetime(2026, 10, 1, 3, 10, tzinfo=UTC)
    assert rpc.error_for(OutsideLibraryError("x")).code == rpc.OUTSIDE
    assert rpc.error_for(PlanOutOfDateError("x")).code == rpc.PLAN_OUT_OF_DATE
    tool = rpc.error_for(ToolMissingError("fpcalc", "fpcalc is missing"))
    assert (tool.code, tool.data) == (rpc.TOOL_MISSING, {"tool": "fpcalc"})
    paused = rpc.error_for(YouTubePausedError("slow down", when))
    assert (paused.code, paused.data) == (rpc.YOUTUBE_PAUSED, {"resume_at": when.isoformat()})
    locked = LibraryLockedError.__new__(LibraryLockedError)
    Exception.__init__(locked, "locked")
    locked.message = "locked"  # type: ignore[attr-defined]
    assert rpc.error_for(locked).code == rpc.LOCKED
    internal = rpc.error_for(ZeroDivisionError("boom"))
    assert internal.code == rpc.INTERNAL and "log" in (internal.data or {})


def test_busy(opened: rpc.Server) -> None:
    release = threading.Event()
    started = opened._start_job("a test job", lambda progress: (release.wait(30), {})[1])
    assert set(started) == {"job_id"}
    assert code(opened, "match.run", limit=5) == rpc.BUSY
    assert code(opened, "sources.scan") == rpc.BUSY
    release.set()
    wait_job(opened)


def test_progress_is_throttled_and_the_job_finishes(opened: rpc.Server, out: Capture) -> None:
    def work(progress: Any) -> dict[str, Any]:
        for n in range(1, 1001):
            progress(n, 1000, "working")
        return {"did": 1000}

    job = opened._start_job("a test job", work)
    wait_job(opened)
    progress = out.notes("job.progress")
    assert 1 <= len(progress) <= 10  # 4 a second at most, and the last one always
    assert progress[-1]["done"] == 1000
    (finished,) = out.notes("job.finished")
    assert finished == {"job_id": job["job_id"], "ok": True, "summary": {"did": 1000}}


def test_a_failing_job_says_so(opened: rpc.Server, out: Capture) -> None:
    from musicorg.errors import UserError

    def work(progress: Any) -> dict[str, Any]:
        raise UserError("It didn't work, in plain English.")

    opened._start_job("a test job", work)
    wait_job(opened)
    (finished,) = out.notes("job.finished")
    assert (finished["ok"], finished["error"]) == (False, "It didn't work, in plain English.")


def test_a_thousand_notifications_from_threads_never_break_a_line(out: Capture) -> None:
    writer = rpc.Writer(out)  # type: ignore[arg-type]

    def burst(n: int) -> None:
        for k in range(250):
            writer.notify("job.progress", {"job_id": f"j{n}", "done": k, "total": 250,
                                           "message": "é ✓ " * 50})  # fmt: skip

    threads = [threading.Thread(target=burst, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = out.lines()  # every line parses
    assert len(lines) == 1000


# ---- every method's shape ---------------------------------------------------------------


def test_library_and_sources(opened: rpc.Server, root: Path, tmp_path: Path,
                             samples: dict[str, Path], out: Capture) -> None:  # fmt: skip
    rips = tmp_path / "rips"
    rips.mkdir()
    shutil.copyfile(samples["mp3"], rips / "Band - Song.mp3")
    status = result(opened, "library.status")
    assert set(status) == {"items_by_state", "tracks", "only_copy", "queue", "warnings"}
    source = result(opened, "sources.add", path=str(rips))["source"]
    listed = result(opened, "sources.list")["sources"]
    assert [s["id"] for s in listed] == [source["id"]]
    assert set(listed[0]) == {"id", "path", "added_at", "scanned_at"}
    job = result(opened, "sources.scan")
    wait_job(opened)
    (finished,) = out.notes("job.finished")
    assert finished["job_id"] == job["job_id"] and finished["ok"], finished
    assert finished["summary"]["new"] == 1
    assert out.notes("review.changed")
    assert result(opened, "library.status")["items_by_state"] == {"new": 1}
    # A second library.open of the same folder is fine; the same engine keeps it.
    assert result(opened, "library.open", root=str(root)) == {"status": "open"}


def test_library_init(server: rpc.Server, tmp_path: Path) -> None:
    result(server, "engine.hello", client="x")
    made = result(server, "library.init", root=str(tmp_path / "New Library"))
    assert made["status"] == "created" and isinstance(made["warnings"], list)
    assert result(server, "library.init", root=str(tmp_path / "New Library"))["status"] == "exists"


def test_review_list_and_decide(opened: rpc.Server, tmp_path: Path, out: Capture) -> None:
    lib = opened._library()
    with open_index(lib.paths, write=True) as index:
        add_source(index, tmp_path / "rips")
        iid = add_item(index, "Band - Song", state="review", reasons=["duration_mismatch"])
        options = [candidate("vid00000001", "Song", ("Band",), 200),
                   candidate("vid00000002", "Song (Live)", ("Band",), 260)]  # fmt: skip
        add_candidates(index, iid, options)
    page = result(opened, "review.list", state="review", offset=0, limit=10)
    assert page["total"] == 1
    (item,) = page["items"]
    assert set(item) == {"item_id", "source_path", "parsed", "duration_s", "state", "reasons",
                         "candidates", "fingerprint"}  # fmt: skip
    assert set(item["parsed"]) == {"artist", "title", "version_tokens", "confidence"}
    shape = {"candidate_id", "video_id", "title", "artists", "album", "album_browse_id",
             "duration_s", "is_official_audio", "is_explicit", "version_tokens", "score",
             "reasons"}  # fmt: skip
    assert shape <= set(item["candidates"][0])
    second = item["candidates"][1]["candidate_id"]
    decided = result(opened, "review.decide", item_id=iid, decision="candidate",
                     candidate_id=second)["item"]  # fmt: skip
    assert decided["state"] == "matched_user"
    from musicorg import state

    chosen = state.decisions(opened._library().load_state().data)[iid]
    assert (chosen["decision"], chosen["video_id"]) == ("candidate", "vid00000002")
    assert out.notes("review.changed")[-1] == {"review": 0, "not_found": 0}
    assert code(opened, "review.decide", item_id=iid, decision="maybe") == rpc.INVALID_PARAMS
    assert code(opened, "review.decide", item_id=iid, decision="candidate",
                candidate_id="c_nope") == rpc.NOT_FOUND  # fmt: skip


def test_plans_queue_and_journal(opened: rpc.Server, tmp_path: Path, samples: dict[str, Path],
                                 out: Capture) -> None:  # fmt: skip
    lib = opened._library()
    rips = tmp_path / "rips"
    rips.mkdir()
    shutil.copyfile(samples["mp3"], rips / "Band - Rare.mp3")
    with open_index(lib.paths, write=True) as index:
        add_source(index, rips)
        iid = add_item(index, "Band - Rare", state="only_copy", seconds=3)
        item = index.item(iid)
        assert item is not None
        index.put_items([{**item, "parse_confidence": 0.95}])

    created = result(opened, "plan.create", kind="adopt")
    assert {"operations", "downloads", "est_minutes", "low_confidence_adopts"} <= set(
        created["summary"]
    )
    assert created["summary"]["operations"] == 1
    plan = result(opened, "plan.get", plan_id=created["plan_id"])["plan"]
    assert plan["kind"] == "adopt" and len(plan["operations"]) == 1
    assert code(opened, "plan.create", kind="nonsense") == rpc.INVALID_PARAMS

    batch = result(opened, "plan.apply", plan_id=created["plan_id"])["batch_id"]
    wait_queue(opened)  # the queue runs by itself after plan.apply
    assert (lib.paths.music / "Band" / "Unsorted" / "Rare.mp3").is_file()
    assert out.notes("queue.state")
    assert out.notes("library.changed")[-1]["tracks_changed"] == 1
    assert code(opened, "plan.apply", plan_id=created["plan_id"]) == rpc.PLAN_OUT_OF_DATE

    status = result(opened, "queue.status")
    assert {"state", "queued", "running", "done", "failed", "needs_review", "daily_count",
            "daily_cap"} <= set(status)  # fmt: skip
    assert result(opened, "queue.pause") == {"state": "paused"}
    assert result(opened, "queue.resume")["state"] in ("idle", "running")
    wait_queue(opened)

    batches = result(opened, "journal.batches", limit=5)["batches"]
    assert batch in [b["batch_id"] for b in batches]
    preview = result(opened, "journal.undo", batch_id=batch, dry_run=True)["operations"]
    assert preview and preview[0]["status"] == "planned"
    job = result(opened, "journal.undo", batch_id=batch, dry_run=False)
    wait_job(opened)
    finished = out.notes("job.finished")[-1]
    assert finished["job_id"] == job["job_id"] and finished["ok"]
    assert not (lib.paths.music / "Band" / "Unsorted" / "Rare.mp3").exists()


def test_search(opened: rpc.Server) -> None:
    found = result(opened, "search.ytmusic", query="alessia cara here", limit=3)["results"]
    assert 1 <= len(found) <= 3
    assert {"candidate_id", "video_id", "title", "artists", "duration_s"} <= set(found[0])
    assert code(opened, "search.ytmusic", query="  ") == rpc.INVALID_PARAMS


# ---- `musicorg serve` as a real process -------------------------------------------------


def serve_process() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-m", "musicorg", "serve"], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ),
    )  # fmt: skip


def send(process: subprocess.Popen[bytes], method: str, **params: Any) -> dict[str, Any]:
    assert process.stdin is not None and process.stdout is not None
    request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    process.stdin.write(json.dumps(request).encode() + b"\n")
    process.stdin.flush()
    while True:
        message = json.loads(process.stdout.readline())
        if "id" in message:
            return message


def test_serve_opens_and_releases_the_library_on_eof(root: Path) -> None:
    process = serve_process()
    try:
        assert "result" in send(process, "engine.hello", client="pytest")
        assert send(process, "library.open", root=str(root))["result"] == {"status": "open"}
        # Another engine can't have the library meanwhile.
        second = serve_process()
        send(second, "engine.hello", client="pytest")
        refused = send(second, "library.open", root=str(root))
        assert refused["error"]["code"] == rpc.LOCKED
        assert second.stdin is not None
        second.stdin.close()
        assert second.wait(30) == 0
        assert process.stdin is not None
        process.stdin.close()
        assert process.wait(30) == 0
    finally:
        for p in (process,):
            if p.poll() is None:
                p.kill()
    with library.open(root, write=True, command="pytest"):
        pass  # the lock was released


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no SIGTERM from the parent")
def test_sigterm_shuts_down_cleanly(root: Path) -> None:
    process = serve_process()
    try:
        send(process, "engine.hello", client="pytest")
        send(process, "library.open", root=str(root))
        process.send_signal(signal.SIGTERM)
        assert process.wait(30) == 0
    finally:
        if process.poll() is None:
            process.kill()
    with library.open(root, write=True, command="pytest"):
        pass


def test_a_child_process_cant_write_into_the_stream() -> None:
    ffmpeg = require_tool("ffmpeg")
    script = (
        "import subprocess, sys\n"
        "from musicorg import rpc\n"
        "proto = rpc.protect_stdout()\n"
        "print('a stray print')\n"
        f"subprocess.run([{str(ffmpeg)!r}, '-version'])\n"
        "rpc.Writer(proto).notify('job.progress', {'done': 1})\n"
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr
    lines = done.stdout.decode("utf-8").splitlines()
    assert [json.loads(line)["method"] for line in lines] == ["job.progress"]
    assert b"ffmpeg version" in done.stderr and b"a stray print" in done.stderr


# ---- the no-print rule ------------------------------------------------------------------


def test_only_cli_prints() -> None:
    """In `serve`, stdout is the protocol: no module but cli.py may call print()."""
    offenders = []
    for path in sorted(MODULES.glob("*.py")):
        if path.name == "cli.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "print":
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def test_the_api_doc_lists_every_method() -> None:
    doc = (ENGINE.parent / "docs" / "ENGINE_API.md").read_text(encoding="utf-8")
    srv = rpc.Server(io.BytesIO(), rpc.Writer(io.BytesIO()))  # type: ignore[arg-type]
    for method in srv.methods:
        assert f"`{method}`" in doc or method in ("queue.pause", "queue.resume"), method
    assert "`queue.pause` / `queue.resume`" in doc


def test_the_test_client_runs_end_to_end(tmp_path: Path) -> None:
    """scripts/rpc_client.py: hello, open, status, scan, match.run, notifications, EOF."""
    require_tool("ffmpeg")
    script = ENGINE.parent / "scripts" / "rpc_client.py"
    done = subprocess.run([sys.executable, str(script)], capture_output=True, timeout=300,
                          env=dict(os.environ, TMPDIR=str(tmp_path)))  # fmt: skip
    text = done.stdout.decode("utf-8", errors="replace")
    assert done.returncode == 0, text + done.stderr.decode("utf-8", errors="replace")
    assert "job.finished" in text and "Everything worked" in text
