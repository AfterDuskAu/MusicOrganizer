# Engine API

The engine has two front doors onto the **same functions**. The CLI is for the owner, for testing and for v0.1. JSON-RPC is for the Mac app from v0.2 on. Neither may contain business logic of its own: both call into `musicorg` modules.

## 0. Enums (the only allowed values)

| Name | Values |
|---|---|
| **Item state** (external rips) | `new` · `matched_auto` · `matched_user` · `review` · `not_found` · `only_copy` · `skipped` · `unsupported_format` · `superseded` · `adopted` |
| **Item flag** (set by `scan`) | `not_adoptable` (WebM, raw AAC or WAV: replaceable, not adoptable in v0.1) · `suspect_upscale` (an MP3 of 256 kbps or more with signs of a YouTube source; a heuristic) · `unreadable` (ffprobe couldn't read the audio) |
| **Review reasons** (stored with `review` items) | `version_mismatch` · `duration_mismatch` · `artist_mismatch` · `title_fuzzy` · `not_official_audio` · `low_parse_confidence` · `fingerprint_mismatch` · `fingerprint_uncertain` · `format_140_unavailable` · `video_unavailable` · `file_changed` · `url_low_score` |
| **CSV decision** (`review import`) | `accept` · `cand:<n>` · `url` · `only_copy` · `skip` · `reject:<n>` |
| **RPC decision** (`review.decide`) | `accept` · `candidate` (+`candidate_id`) · `url` (+`url`) · `only_copy` · `skip` · `reject` (+`candidate_id`) |
| **Fingerprint verdict** (step 08, `fingerprint.compare`) | `match` · `uncertain` (→ review reason `fingerprint_uncertain`) · `different` (→ `fingerprint_mismatch`) |
| **Job state** | `queued` · `running` · `done` · `failed` · `needs_review` · `cancelled` |
| **Queue state** | `running` · `idle` · `paused` · `paused_by_youtube` |
| **Plan kind** | `replace` · `adopt` · `lyrics` · `artwork` |
| **Batch kind** | a plan kind (`replace` · `adopt` · `lyrics` · `artwork`) · `undo` · `demo` (the manual-check scripts in `scripts/`) |
| **Batch status** | `open` (running, or an open batch from `apply`) · `closed` · `interrupted` (closed by recovery after a crash) |
| **Journal operation** | `commit` · `copy_in` · `supersede` · `restore` (back from `_Replaced/`, by undo) · `move` · `trash` · `write_tags` · `write_sidecar` |
| **Undo step status** | `planned` (dry run) · `done` · `skipped` (already undone, or the file is gone) · `manual` (restore from the Trash by hand) |
| **`MUSICORG_SOURCE`** | `youtube_music` · `youtube` · `rip_copy` · `bandcamp` · `cd` · `itunes` · `other` |
| **`MUSICORG_MATCH`** | `auto_exact` (AUTO match and fingerprint pass) · `user_confirmed` (owner's decision and fingerprint pass) · `manual` (adopt using the owner's `*_fix` values) · `auto_details` (step 09c: the owner's own audio with an AUTO match's official details; no fingerprint check) · `user_details` (step 09c: the same, from the owner's review choice). Absent for adopts without fixes. |

A fingerprint mismatch puts the item back in `review` with reason `fingerprint_mismatch`. The gate's result is kept in `state.json` (`gate`): a `different` video is never proposed for that rip again, and an `uncertain` one never goes AUTO again.

A download that isn't what was asked for also goes to review: not format 140, not AAC, or under 100 kbps → `format_140_unavailable`; more than 2 s longer or shorter than YouTube Music said → `duration_mismatch`.

## 1. CLI (`musicorg`)

Global options:
- `--library <root>`: defaults to the last opened library in config.
- `--json`: machine-readable output.
- `--verbose`

"Lock" means the command takes the library's single-writer lock. Commands without the lock open SQLite read-only and can run while `queue run` is working.

| Command | Does | Lock | Built in |
|---|---|---|---|
| `musicorg init <root>` | Create layout; refuse a folder already holding audio, or staging on another volume; environment warnings | yes | 03a |
| `musicorg status` | Counts per item state, queue state, warnings | no | 02 (stub) → grows |
| `musicorg doctor` | Check tools (deno ≥ 2.3), yt-dlp, yt-dlp-ejs and ytmusicapi versions, config and log folders | no | 02 |
| `musicorg doctor --update-ytdlp` | `pip install -U "yt-dlp[default]"`, recording the old versions. Refuses while the lock is held. | refuses if held, then holds it during pip | 09a |
| `musicorg doctor --rollback-ytdlp` | Reinstall the recorded previous versions | refuses if held, then holds it during pip | 09a |
| `musicorg sources add <path>` | Register a read-only source. Refuse if it's inside the root or contains it. | yes | 05 |
| `musicorg sources list` | List sources | no | 05 |
| `musicorg sources remove <id>` | Forget a source; never touches its files | yes | 05 |
| `musicorg scan [<source_id>…]` | Read-only index of sources | yes | 05 |
| `musicorg index rebuild` | Rebuild per contract section 5 | yes | 05 |
| `musicorg match [--limit N] [--rescan] [--recheck]` | Search and score `new` items. `--rescan` re-matches `review` and `not_found` items and bypasses the search cache. Ends by writing `Reports/auto-sample.csv`. `--recheck` (07b) searches nothing: it classifies `review` and `not_found` items again from the candidates already found. | yes | 06 |
| `musicorg report [--out <dir>]` | Decision report, markdown and CSV | no | 07 |
| `musicorg review export <csv> [--include-auto]` | Review CSV. Never overwrites: a ` (2)` suffix if the file exists. | no | 07 |
| `musicorg review import <csv>` | Apply decisions (CSV decision enum) | yes | 07 |
| `musicorg review serve [--port N] [--no-open]` | The local review page in the browser: play each rip and its candidates, click to decide (same checks as `review import`). Only on 127.0.0.1. | yes | 07b |
| `musicorg journal list [--limit N]` | Recent batches (default 20) with counts and open/closed status | no | 03b |
| `musicorg undo <batch_id> [--dry-run]` | Reverse a batch | yes | 03b, extended 09b |
| `musicorg plan replace [--only auto\|accepted\|all-eligible] [--limit N] [--stage-only]` | Dry-run plan. `--only` defaults to `all-eligible` (AUTO matches and the owner's choices). `--limit` counts videos: one download serves every rip that matched it. `--stage-only` stops after the fingerprint step for calibration. | yes | 09b |
| `musicorg plan adopt [--include-not-found] [--matched]` | Dry-run plan: copy `only_copy` items (and optionally all `not_found`) into `Music/`. `--matched` (09c) also copies in `matched_auto` and `matched_user` rips, keeping the owner's own audio, with their match's official details; nothing is downloaded. | yes | 09b, 09c |
| `musicorg plan show <plan_id>` | Print operations and summary | no | 09b |
| `musicorg plan calibration [--out <dir>]` | Write `calibration-pairs.csv` (default `Reports/`) from the `--stage-only` downloads, `same` left for the owner to fill in | no | 09b |
| `musicorg apply <plan_id>` | Validate and enqueue; prints the `batch_id` | yes | 09b |
| `musicorg queue run` | Process the queue in the foreground until empty, paused or Ctrl-C | yes | 09a |
| `musicorg queue status` | Queue state and counts | no | 09a |
| `musicorg queue pause` / `resume` | Set a flag in `queue.sqlite` that `queue run` checks between jobs | no | 09a |
| `musicorg lyrics [--missing]` | Create a `lyrics` plan; run it with `apply` | yes | 10 |
| `musicorg artwork [--missing]` | Create an `artwork` plan; run it with `apply` | yes | 10 |
| `musicorg serve` | JSON-RPC server on stdio | yes | 11 |

Exit codes:
- `0` ok
- `1` user error, with a plain-English message
- `2` library locked (the message names the holder from `lock.info`)
- `3` external tool missing
- `4` YouTube blocked or paused
- `10` internal error, with the log path printed

## 2. JSON-RPC over stdio (`musicorg serve`)

- JSON-RPC 2.0, **one JSON object per line** (`\n`), UTF-8, on stdin/stdout. Nothing else is ever written to stdout.
- The app starts the engine as a child process and owns its lifetime. The engine exits cleanly when stdin closes.
- In `serve`, a background worker runs the queue exactly like `queue run`, from `library.open` until `queue.pause`, a YouTube pause, or EOF.
- Long operations return `{ "job_id" }` immediately, then report progress through notifications. Only one long operation runs at a time besides the queue worker; another returns error -32007.
- JSON-RPC batch requests (arrays) are rejected with -32600.

### Methods (v0.1)

| Method | Params | Result |
|---|---|---|
| `engine.hello` | `{ "client", "client_version" }` e.g. `"mac-app"`, `"0.2.0"` | `{ "engine_version", "schema_version", "ytdlp_version", "ytmusicapi_version", "capabilities": [..] }` |
| `library.init` | `{ "root" }` | `{ "status", "warnings": [..] }` |
| `library.open` | `{ "root" }` | `{ "status" }` |
| `library.status` | — | `{ "items_by_state": {..}, "tracks", "only_copy", "queue": {..}, "warnings": [..] }` |
| `sources.add` | `{ "path" }` | `{ "source" }` |
| `sources.list` | — | `{ "sources": [..] }` |
| `sources.scan` | `{ "source_ids"?: [..] }` | `{ "job_id" }` |
| `match.run` | `{ "limit"?, "rescan"? }` | `{ "job_id" }` |
| `review.list` | `{ "state"?: "review"\|"not_found"\|"matched_auto", "offset", "limit" }` | `{ "items": [ReviewItem], "total" }` |
| `review.decide` | `{ "item_id", "decision", "candidate_id"?, "url"?, "metadata"? }` | `{ "item" }` |
| `plan.create` | `{ "kind", "options"? }` | `{ "plan_id", "summary": { "operations", "downloads", "est_minutes", "low_confidence_adopts" } }` |
| `plan.get` | `{ "plan_id" }` | `{ "plan" }` |
| `plan.apply` | `{ "plan_id" }` | `{ "batch_id" }` (jobs go to the queue) |
| `queue.status` | — | `{ "state", "reason"?, "resume_at"?, "queued", "running", "done", "failed", "needs_review", "daily_count", "daily_cap" }` |
| `queue.pause` / `queue.resume` | — | `{ "state" }` |
| `journal.batches` | `{ "limit"? }` | `{ "batches": [..] }` |
| `journal.undo` | `{ "batch_id", "dry_run"?: true }` | `{ "operations": [..] }` or `{ "job_id" }` |
| `search.ytmusic` | `{ "query", "limit"? }` | `{ "results": [Candidate] }` |

**CLI-only in v0.1** (RPC comes with the v0.2 app when needed): `sources.remove`, `index.rebuild`, `report`, `review export/import`, `lyrics`, `artwork`, `doctor`.

`review.decide` with `"url"` is the **Other → paste a link** path. The URL is fetched through `youtube.get_track()`, scored like any candidate, and kept in `review` with reason `url_low_score` if it scores below 0.6. It's never accepted blindly.

### Notifications (engine → app, no `id`)

| Method | Params |
|---|---|
| `job.progress` | `{ "job_id", "done", "total", "message" }`, at most 4/s per job |
| `job.finished` | `{ "job_id", "ok", "summary", "error"? }` |
| `queue.state` | `{ "state", "reason"?, "resume_at"? }` |
| `review.changed` | `{ "review", "not_found" }` |
| `library.changed` | `{ "batch_id"?, "tracks_added", "tracks_changed" }` |

### Errors

Standard JSON-RPC codes, plus:

| Code | Meaning |
|---|---|
| -32001 | Library locked by another engine |
| -32002 | Path outside library |
| -32003 | External tool missing (`data.tool`) |
| -32004 | YouTube paused us (`data.resume_at`) |
| -32005 | Plan out of date; re-plan |
| -32006 | Not found (item, plan, batch) |
| -32007 | Busy: another long operation is running |

`error.message` is always plain English, suitable to show the user directly.

### Shapes

```jsonc
// Candidate
{ "candidate_id": "c_…", "video_id": "…", "title": "…", "artists": ["…"], "album": "…",
  "album_browse_id": "MPRE…", "duration_s": 228, "is_official_audio": true, "is_explicit": false,
  "version_tokens": ["remix:adventure club"], "score": 0.917,
  "reasons": ["artist exact", "title exact", "version match", "duration Δ1s"] }

// ReviewItem
{ "item_id": "i_3fa2…", "source_path": "…", "parsed": { "artist": "…", "title": "…", "version_tokens": [..], "confidence": 0.9 },
  "duration_s": 229, "state": "review", "reasons": ["version_mismatch"], "candidates": [Candidate],
  "fingerprint": null }
```
