# Step 09a: Download queue and downloader

## Context
Bulk downloading is what gets you blocked by YouTube. This step builds **one** throttled queue that survives quitting, and the downloader it drives. Step 09b builds the pipeline that uses them.

## Goal
Build `musicorg.queue`, `youtube.download_audio()`, and `doctor --update-ytdlp` / `--rollback-ytdlp`. Implement `queue run|status|pause|resume`.

## Build
1. **Queue (`queue.py`, in `.musicorg/queue.sqlite`):**
   - Table `jobs(id, batch_id, plan_id, kind, item_id, payload_json, state, attempts, next_attempt_at, last_error, reason, created_at, updated_at)`.
   - Table `queue_meta(key, value)` holds: the pause flag, `paused_by_youtube` + `resume_at`, and the rolling-24 h download count. These survive restarts.
   - Job states use the enum in `ENGINE_API.md`.
   - One worker for network jobs, sequential in v0.1.
   - On startup, `running` jobs return to `queued` and their staging folders are discarded.
   - `queue pause` / `resume` only flip the flag, **without the library lock**. `queue run` checks the flag between jobs.
2. **Throttle defaults** (`config.json`):
   - one download at a time
   - a random 8–25 s pause between downloads
   - daily cap: 300 per rolling 24 h
   - "quiet start": the first 20 downloads of a session use 20–40 s pauses
3. **Retry:** backoff 1 min, 5 min, 30 min, 2 h. After 4 attempts the job ends `failed` with the last error.
4. **YouTube block detection** (whole queue):
   - Triggers: HTTP 429; an error containing "not a bot"; or **3 consecutive network-level failures** (HTTP 403/429, timeouts, connection errors).
   - The queue enters `paused_by_youtube` for 6 h (configurable), shown in `status` and `queue status`.
   - Never retry in a tight loop, and **never try to get around the check.**
5. **Per-item failures don't pause the queue.** Age-restricted, private, unavailable or region-blocked videos end that job `needs_review` with reason `video_unavailable`, and the queue continues.
6. **`youtube.download_audio(video_id, dest_dir) -> (Path, info)`** through the yt-dlp Python API. Check every option name against the installed yt-dlp:
   - `format='140'` only, **no fallback**. If 140 isn't offered: raise `FormatUnavailable`, and the job ends `needs_review` with `format_140_unavailable`.
   - `paths={'home': dest_dir, 'temp': dest_dir}`, `outtmpl={'default': '%(id)s.%(ext)s'}` (keeps a `%` in your folder name from breaking the template)
   - `ffmpeg_location=<tools ffmpeg>`
   - the JavaScript runtime pointed at `tools` deno (`js_runtimes` or whatever the installed yt-dlp calls it)
   - `cachedir=<engine cache>/yt-dlp`, `noplaylist=True`, `quiet=True`, `noprogress=True`, `logger=<logging adapter>`, and a progress hook for byte progress
   - **No postprocessors.** yt-dlp's automatic M4A container fix-up (a remux) is allowed.
   - Return `info['format_id']` and the other metadata to the caller. Never assume 140.
   - `dest_dir` must come from `fileops.stage_path`. yt-dlp writes nowhere else, including its `.part` files.
7. **`doctor --update-ytdlp`:**
   - Refuses while the library lock is held.
   - Records the current `yt-dlp` and `yt-dlp-ejs` versions in `config.json`, then runs `pip install -U "yt-dlp[default]"` in the engine's venv.
   - `doctor --rollback-ytdlp` reinstalls the recorded versions.
   - In-app auto-update comes with packaging (v0.5).
8. **`queue run`** (lock) processes jobs in the foreground until empty, paused or Ctrl-C. Ctrl-C is a graceful stop: the current job finishes or rolls back.

## Tests
- Crash mid-job → requeued, and staging discarded.
- Throttle timing and the daily cap, checked with a fake clock. The cap survives a restart.
- A simulated "not a bot" error → the whole queue pauses until `resume_at`, and the pause survives a restart.
- A simulated age-restricted error → only that job goes to `needs_review`, and the queue continues.
- Format 140 unavailable (stubbed) → `needs_review` with `format_140_unavailable`, and nothing else downloaded.
- `queue pause` from a second process while `queue run` holds the lock → the run stops after the current job.
- The downloader writes only inside `dest_dir` (stubbed yt-dlp; assert paths).

## Acceptance
- CI green on all three runners.
- Live check (`@pytest.mark.live`, run by hand once): download one known song into a scratch library's staging. Confirm `format_id == '140'`, AAC codec, ~128 kbps, and no files outside staging.
