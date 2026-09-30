"""A small test client for `musicorg serve` (step 11): plays the Mac app's part.

    .venv/bin/python scripts/rpc_client.py

It makes a scratch library and a folder of five made-up rips (short silent MP3s named
like songs the matcher's recordings cover), starts `musicorg serve` in replay mode
(MUSICORG_REPLAY_DIR = the test fixtures, so nothing reaches the network), then:

1. sends engine.hello, library.open and library.status
2. adds the rips as a source and scans them
3. starts match.run with limit 5
4. prints every notification as it arrives
5. closes stdin and checks the engine exits cleanly (code 0)

Everything is made in a temporary folder and removed afterwards. Exit code 0 means it
all worked.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "engine" / "tests" / "fixtures" / "ytm"
CASES = REPO / "engine" / "tests" / "data" / "match_cases.json"


class Client:
    def __init__(self, env: dict[str, str]) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-m", "musicorg", "serve"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
        )  # fmt: skip
        self.next_id = 0
        self.replies: dict[int, dict[str, Any]] = {}
        self.finished: dict[str, dict[str, Any]] = {}
        self.cond = threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for raw in self.process.stdout:
            message = json.loads(raw)  # any non-JSON line would fail here
            with self.cond:
                if "id" in message:
                    self.replies[message["id"]] = message
                else:
                    show(message)
                    if message["method"] == "job.finished":
                        self.finished[message["params"]["job_id"]] = message["params"]
                self.cond.notify_all()

    def call(self, method: str, **params: Any) -> Any:
        assert self.process.stdin is not None
        self.next_id += 1
        request = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}
        print(f"→ {method} {json.dumps(params) if params else ''}")
        self.process.stdin.write(json.dumps(request).encode() + b"\n")
        self.process.stdin.flush()
        with self.cond:
            self.cond.wait_for(lambda: self.next_id in self.replies, timeout=120)
        reply = self.replies.pop(self.next_id)
        if "error" in reply:
            raise SystemExit(f"✗ {method}: {reply['error']['message']}")
        print(f"← {short(reply['result'])}")
        return reply["result"]

    def wait_job(self, job_id: str) -> dict[str, Any]:
        with self.cond:
            self.cond.wait_for(lambda: job_id in self.finished, timeout=300)
        return self.finished[job_id]

    def close(self) -> int:
        assert self.process.stdin is not None
        self.process.stdin.close()
        return self.process.wait(30)


def show(message: dict[str, Any]) -> None:
    print(f"  ⋯ {message['method']} {short(message['params'])}")


def short(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) < 160 else text[:157] + "…"


def make_rips(folder: Path, count: int = 5) -> None:
    """Short silent MP3s named "<artist> - <title>.mp3" after the recorded cases."""
    import shutil

    ffmpeg = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    simple = [c for c in cases if not c["parsed"]["version_tokens"]][:count]
    for case in simple:
        name = f"{case['parsed']['artist']} - {case['parsed']['title']}.mp3".replace("/", "_")
        subprocess.run(
            [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "2",
             "-c:a", "libmp3lame", "-b:a", "64k", str(folder / name)],
            check=True,
        )  # fmt: skip


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # ✓ and → through a Windows pipe
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    with tempfile.TemporaryDirectory(prefix="musicorg-rpc-") as scratch:
        base = Path(scratch)
        rips, root, home = base / "rips", base / "Library", base / "home"
        rips.mkdir()
        make_rips(rips)
        env = dict(os.environ, MUSICORG_REPLAY_DIR=str(FIXTURES), MUSICORG_HOME=str(home))
        subprocess.run([sys.executable, "-m", "musicorg", "init", str(root)], env=env,
                       check=True, capture_output=True)  # fmt: skip

        client = Client(env)
        started = time.monotonic()
        client.call("engine.hello", client="rpc_client", client_version="0.1")
        client.call("library.open", root=str(root))
        client.call("library.status")
        client.call("sources.add", path=str(rips))
        scanned = client.wait_job(client.call("sources.scan")["job_id"])
        if not scanned["ok"]:
            raise SystemExit(f"✗ the scan failed: {scanned['error']}")
        matched = client.wait_job(client.call("match.run", limit=5)["job_id"])
        if not matched["ok"]:
            raise SystemExit(f"✗ match.run failed: {matched['error']}")
        status = client.call("library.status")
        code = client.close()
        took = time.monotonic() - started
        print(f"Items now: {status['items_by_state']}")
        if code != 0:
            print(f"✗ the engine exited with code {code}")
            return 1
        print(f"✓ Everything worked, and the engine exited cleanly ({took:.1f} s).")
        return 0


if __name__ == "__main__":
    sys.exit(main())
