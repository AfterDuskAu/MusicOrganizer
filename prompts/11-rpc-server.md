# Step 11: JSON-RPC server for the app

## Context
From v0.2, a Mac app (SwiftUI) starts the engine as a child process and talks to it over stdin/stdout, and later a Windows app does the same. This step builds that interface so the v0.2 app can be built against a stable contract. `docs/ENGINE_API.md` section 2 is the spec.

## Goal
Build `musicorg.rpc` and `musicorg serve`, covering every v0.1 method, notification and error code in `docs/ENGINE_API.md`, with a small test client.

## Build
1. **Transport:**
   - Newline-delimited JSON on stdin/stdout, UTF-8, `\n` line endings, flushed after every message. Binary mode on Windows.
   - **Protect stdout at the file-descriptor level,** because child processes (ffmpeg, fpcalc, deno) bypass Python's `sys.stdout`. At `serve` start:
     1. `proto = os.fdopen(os.dup(1), 'wb', buffering=0)`
     2. `os.dup2(2, 1)`
     3. `sys.stdout = sys.stderr`
     
     Only the protocol writer uses `proto`.
   - A writer lock, so notifications from worker threads never interleave with responses.
2. **Dispatcher:**
   - Method → handler. Params are validated with small dataclasses; a bad param gets `-32602` with a plain-English message.
   - Unknown method → `-32601`. JSON-RPC batch arrays → `-32600`.
   - Exceptions map to the engine error codes in the API doc. Anything unexpected → `-32603`, with the log file path in `data`.
3. **Workers:**
   - A queue worker thread runs the queue exactly like `queue run`, from `library.open` until `queue.pause`, a YouTube pause, or shutdown.
   - One further long operation at a time (`sources.scan`, `match.run`, `journal.undo`): it returns `{job_id}` at once. A second one while busy → `-32007`.
   - Progress arrives as `job.progress` (at most 4/s per job), then `job.finished`. Queue changes emit `queue.state`.
   - One SQLite connection per thread.
4. **Lifecycle:**
   - `engine.hello` must come first; anything before it gets an error.
   - **stdin EOF is the shutdown signal on both platforms** (Windows has no SIGTERM from the parent):
     1. stop accepting work
     2. let the current queue job finish or roll back safely within 10 s
     3. release the lock
     4. exit 0
     
     SIGTERM on macOS does the same.
   - `serve` holds the library lock for its lifetime. A CLI `queue pause` still works through the flag.
5. **`search.ytmusic`:** candidates for the app's search box, through the same rate limiter.
6. **Test client `scripts/rpc_client.py`:** starts `musicorg serve` with `MUSICORG_REPLAY_DIR` set to the fixtures, then:
   1. sends `engine.hello`, `library.open` and `library.status`
   2. starts `match.run` with `limit: 5` on a fixture library
   3. prints notifications as they arrive
   4. closes stdin and checks for a clean exit

## Tests
Protocol tests drive `serve` as a real subprocess:
- the hello handshake
- every method returns the documented shape (network stubbed via replay)
- each error code
- notifications during a fake job
- the busy error
- clean shutdown on EOF with the lock released

Plus:
- A handler that runs `subprocess.run(['ffmpeg', '-version'])` **without** capturing output doesn't corrupt the stream.
- 1,000 progress events never corrupt a line.
- The no-print test: fails if any `musicorg` module except `cli.py` calls `print`.
- All of the above pass on the Windows runner too.

## Acceptance
- `python scripts/rpc_client.py` runs end to end on the iMac and in your Windows 11 VM.
- `docs/ENGINE_API.md` matches the implementation exactly. Fix any difference in code or doc, and note it in `docs/CHANGELOG.md`.
- **Release:**
  1. Set `__version__ = "0.1.0"`.
  2. Add the `0.1.0` CHANGELOG entry.
  3. Commit, and tag `v0.1.0`.
