# Engine API

The engine has two front doors onto the **same functions**. The CLI is for the owner, for testing and for v0.1. JSON-RPC is for the Mac app from v0.2 on. Neither may contain business logic of its own: both call into `musicorg` modules.

## 0. Enums (the only allowed values)

| Name | Values |
|---|---|
| **Item state** (external rips) | `new` · `matched_auto` · `matched_user` · `review` · `not_found` · `only_copy` · `skipped` · `unsupported_format` · `superseded` · `adopted` |
| **Item flag** (set by `scan`) | `not_adoptable` (WebM, raw AAC or WAV: replaceable, not adoptable in v0.1) · `suspect_upscale` (an MP3 of 256 kbps or more with signs of a YouTube source; a heuristic) · `unreadable` (ffprobe couldn't read the audio) |
| **Review reasons** (stored with `review` items) | `version_mismatch` · `duration_mismatch` · `artist_mismatch` · `title_fuzzy` · `not_official_audio` · `low_parse_confidence` · `fingerprint_mismatch` · `fingerprint_uncertain` · `format_140_unavailable` · `video_format_unavailable` (v0.2: a saved video that isn't the picture size and streams asked for) · `video_unavailable` · `file_changed` · `url_low_score` |
| **CSV decision** (`review import`) | `accept` · `cand:<n>` · `url` · `only_copy` · `skip` · `reject:<n>` |
| **RPC decision** (`review.decide`) | `accept` · `candidate` (+`candidate_id`) · `url` (+`url`) · `only_copy` · `skip` · `reject` (+`candidate_id`) |
| **Fingerprint verdict** (step 08, `fingerprint.compare`) | `match` · `uncertain` (→ review reason `fingerprint_uncertain`) · `different` (→ `fingerprint_mismatch`) |
| **Job state** | `queued` · `running` · `done` · `failed` · `needs_review` · `cancelled` |
| **Queue state** | `running` · `idle` · `paused` · `paused_by_youtube` |
| **Plan kind** | `replace` · `adopt` · `lyrics` · `artwork` · `tidy` (step 09d: duplicates and preferred names) · `download` (v0.2: songs the owner asked for, with no rip behind them) · `edit` (v0.2: the owner's own corrections to one song) · `remove` (v0.2: downloads the owner doesn't want, sent to the Trash) |
| **Discover seed** (`discover.suggest`, v0.4) | `library` (the whole library; also "just recommend") · `most_played` · `top_artist` (the artist played most; with nothing played, the one with the most songs) · `playlist` (+`playlist_id`) · `artist` (+`name`: that artist and similar ones) · `genre` (+`name`) · `typed` (+`name`: whatever the owner typed when asked what music they'd like; the engine works out whether it's a genre or an artist, and the answer's `seeds` say which) |
| **Batch kind** | a plan kind · `undo` · `demo` (the manual-check scripts in `scripts/`) |
| **Batch status** | `open` (running, or an open batch from `apply`) · `closed` · `interrupted` (closed by recovery after a crash) |
| **Journal operation** | `commit` · `copy_in` · `supersede` · `restore` (back from `_Replaced/`, by undo) · `move` · `trash` · `write_tags` · `write_sidecar` |
| **Undo step status** | `planned` (dry run) · `done` · `skipped` (already undone, or the file is gone) · `manual` (restore from the Trash by hand) |
| **`MUSICORG_SOURCE`** | `youtube_music` · `youtube` · `rip_copy` · `bandcamp` · `cd` · `itunes` · `other` |
| **`MUSICORG_MATCH`** | `auto_exact` (AUTO match and fingerprint pass) · `user_confirmed` (owner's decision and fingerprint pass) · `manual` (adopt using the owner's `*_fix` values) · `auto_details` (step 09c: the owner's own audio with an AUTO match's official details; no fingerprint check) · `user_details` (step 09c: the same, from the owner's review choice) · `unconfirmed` (v0.2: a rip copied in under its own names before it was identified, so it can be played; its item stays in `review` or `not_found`, and the copy is upgraded in place when it's decided). Absent for adopts without fixes. |

A fingerprint mismatch puts the item back in `review` with reason `fingerprint_mismatch`. The gate's result is kept in `state.json` (`gate`): a `different` video is never proposed for that rip again, and an `uncertain` one never goes AUTO again.

A download that isn't what was asked for also goes to review: not format 140, not AAC, or under 100 kbps → `format_140_unavailable`; more than 2 s longer or shorter than YouTube Music said → `duration_mismatch`. A saved video is checked the same way: not H.264 at the height asked for, not joined to format-140 AAC sound → `video_format_unavailable`; the wrong length → `duration_mismatch`. Nothing that fails is kept.

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
| `musicorg plan adopt [--include-not-found] [--matched] [--unconfirmed]` | Dry-run plan: copy `only_copy` items (and optionally all `not_found`) into `Music/`. `--matched` (09c) also copies in `matched_auto` and `matched_user` rips, keeping the owner's own audio, with their match's official details; nothing is downloaded. `--unconfirmed` (v0.2) also copies in every `review` and `not_found` rip under its own names, tagged `unconfirmed`, without changing its state; a later adopt of the same rip upgrades that copy in place. | yes | 09b, 09c, v0.2 |
| `musicorg plan tidy` | Dry-run plan: songs the library has twice keep their best copy (lossless; then a CD or iTunes rip over a YouTube conversion; then bitrate; then size; the other goes to `_Replaced/`, its rip linked to the kept file), and the owner's preferred names go into tags and folder names (with the `.lrc` and `cover.jpg`) | yes | 09d |
| `musicorg names list` / `set <original> <preferred>` / `remove <original>` | The owner's preferred spellings, e.g. `JAŸ-Z` → `Jay Z`. New songs use them at once; `plan tidy` applies them to the library. | `set`/`remove`: yes | 09d |
| `musicorg plan download [<video_id>…] [--video ID:HEIGHT]…` | Dry-run plan: download these YouTube Music songs into the library (v0.2; the app's Download button). No rip is involved, so nothing is replaced. Songs already in the library are left out. `--video` saves a video whole into `Music/Videos/`, its picture at that height (the app's Save Video button). | yes | v0.2 |
| `musicorg plan show <plan_id>` | Print operations and summary | no | 09b |
| `musicorg plan calibration [--out <dir>]` | Write `calibration-pairs.csv` (default `Reports/`) from the `--stage-only` downloads, `same` left for the owner to fill in | no | 09b |
| `musicorg apply <plan_id>` | Validate and enqueue; prints the `batch_id` | yes | 09b |
| `musicorg queue run` | Process the queue in the foreground until empty, paused or Ctrl-C | yes | 09a |
| `musicorg queue status` | Queue state and counts | no | 09a |
| `musicorg queue pause` / `resume` | Set a flag in `queue.sqlite` that `queue run` checks between jobs | no | 09a |
| `musicorg lyrics [--missing]` | Create a `lyrics` plan; run it with `apply`. Lyrics come from LRCLIB, then YouTube Music. `--missing`: songs with neither embedded lyrics nor a `.lrc`. | yes | 10 |
| `musicorg artwork [--missing]` | Create an `artwork` plan; run it with `apply`. The album's official cover, or the owner's `art_url`. `--missing`: songs with no cover, or a cover that isn't square (a video frame). | yes | 10 |
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

### Methods (v0.1, plus `library.tracks` and `library.lyrics` from v0.2)

| Method | Params | Result |
|---|---|---|
| `engine.hello` | `{ "client", "client_version" }` e.g. `"mac-app"`, `"0.2.0"` | `{ "engine_version", "schema_version", "ytdlp_version", "ytmusicapi_version", "capabilities": [..] }` |
| `library.init` | `{ "root" }` | `{ "status", "warnings": [..] }` |
| `library.open` | `{ "root" }` | `{ "status" }` |
| `library.status` | — | `{ "items_by_state": {..}, "tracks", "only_copy", "queue": {..}, "warnings": [..] }` |
| `library.tracks` | — | `{ "root", "tracks": [Track] }`: every song in the library, for the app's screens (v0.2). Slow the first time (it reads each file's tags once), instant afterwards. |
| `library.lyrics` | `{ "path" }` (a Track's `path`) | `{ "synced", "plain" }`: the text of the song's `.lrc`, and the lyrics in its tags. Either may be null. |
| `listening.get` | — | `Listening`: the owner's favourites, play counts and playlists (v0.2), kept in `state.json` |
| `listening.favourite` | `{ "track_id", "on" }` | `{ "favourites": [track_id] }` (most recent first) |
| `listening.played` | `{ "track_id" }` (the app sends it when a song has played to its end) | `{ "count", "last_played" }` |
| `listening.move` | `{ "track_ids": [..], "to": "library" \| "downloads" }` | `{ "library": [track_id] }`: the downloads the owner has moved into the main library's lists (v0.2). No file moves; it's the owner's sorting, kept in `state.json`. |
| `playlist.create` | `{ "name" }` | `{ "playlists": [Playlist] }` |
| `playlist.rename` | `{ "playlist_id", "name" }` | `{ "playlists": [Playlist] }` |
| `playlist.delete` | `{ "playlist_id" }` (only the list goes; its songs are untouched) | `{ "playlists": [Playlist] }` |
| `playlist.set_tracks` | `{ "playlist_id", "track_ids": [..] }`: the playlist's songs, in order (add, remove and reorder are all this call) | `{ "playlists": [Playlist] }` |
| `sources.add` | `{ "path" }` | `{ "source" }` |
| `sources.list` | — | `{ "sources": [..] }` |
| `sources.scan` | `{ "source_ids"?: [..] }` | `{ "job_id" }` |
| `match.run` | `{ "limit"?, "rescan"? }` | `{ "job_id" }` |
| `review.list` | `{ "state"?: "review"\|"not_found"\|"matched_auto", "offset"?: 0, "limit"?: 50 }` (limit 1–500) | `{ "items": [ReviewItem], "total" }` |
| `review.decide` | `{ "item_id", "decision", "candidate_id"?, "url"?, "metadata"? }`. `accept` without `candidate_id` takes candidate 1; `metadata` holds `artist_fix`, `title_fix`, `album_fix`, `art_url` for `only_copy`. | `{ "item": ReviewItem }` |
| `plan.create` | `{ "kind", "options"? }`. Kinds and options: `replace` (`only`, `limit`, `stage_only`), `adopt` (`include_not_found`, `matched`, `unconfirmed`), `lyrics` and `artwork` (`missing`), `tidy` (none), as the CLI's flags; `download` (`video_ids`: a list of songs; and/or `videos`: a list of `{ "video_id", "height", "fps"? }`, each a video saved whole into `Music/Videos/` with its picture at that height: 144, 240, 360, 480, 720 or 1080; and optionally `candidates`: tracks as the engine gave them out a moment ago, such as Discover's picks, in the Candidate shape. One of those isn't looked up on YouTube Music again, so a plan for many picks is made at once; a candidate's `genre`, if it has one, becomes the downloaded song's genre tag); `edit` (`path`, plus any of `changes`: an object of `title`, `artist`, `album_artist`, `album`, `genre`, `year`, `track`, `explicit` (true or false), where null or "" clears a field; `lyrics`: text, timed or plain, "" removes them; `cover_file`: a picture on this computer); `remove` (`paths`: library songs or videos that were downloaded from YouTube Music with no rip behind them. Each goes to the system Trash with its `.lrc`, and the album's `cover.jpg` with the album's last song. A song from the owner's own files is refused. Undo can't bring a file back from the Trash). | `{ "plan_id", "summary": { "operations", "downloads", "est_minutes", "low_confidence_adopts", … } }` (the plan's whole summary) |
| `plan.get` | `{ "plan_id" }` | `{ "plan" }` |
| `plan.apply` | `{ "plan_id" }` | `{ "batch_id" }` (jobs go to the queue, and the queue worker starts) |
| `queue.status` | — | `{ "state", "reason"?, "resume_at"?, "queued", "running", "done", "failed", "needs_review", "daily_count", "daily_cap" }` |
| `queue.pause` / `queue.resume` | — | `{ "state" }` |
| `journal.batches` | `{ "limit"? }` | `{ "batches": [..] }` |
| `journal.undo` | `{ "batch_id", "dry_run"?: true }` (a dry run unless `dry_run` is `false`) | `{ "operations": [..] }` for a dry run, else `{ "job_id" }` |
| `search.ytmusic` | `{ "query", "limit"?: 10 }` (limit 1–100; the app's "show more" asks again with a bigger limit) | `{ "results": [Candidate] }` (`score` null; `candidate_id` made from the videoId) |
| `youtube.stream` | `{ "video_id" }` | `{ "url", "http_headers", "duration_s" }`: where the app can play the song's audio (format 140) from right now. Nothing is downloaded or saved. The address expires, so the app asks each time it plays. |
| `youtube.video` | `{ "title", "artist", "path"? }` (the song; `path` for a library song, so its version is read from its tags and the name of the rip it came from: a remix never gets the original's video) | `{ "found": false }` when YouTube Music has no official video for the song (only a video it marks as the artist's own counts, of the same version of the song; other people's uploads never do). Otherwise `{ "found": true, "video_id", "title", "duration_s", "http_headers", "audio_url", "qualities": [{ "label", "height", "fps", "url" }] }`: the video's sound (format 140) and its picture in each size the app can show (H.264, 144p to 1080p, the sharpest first), as separate addresses the app plays together. Nothing is downloaded or saved. The addresses expire, so the app asks again when one stops working; the search behind it is kept for 30 days. |
| `lyrics.find` | `{ "title", "artist"?, "album"?, "duration_s"?, "video_id"? }` | `{ "synced", "plain", "source" }`: lyrics for a song being played from YouTube Music (LRCLIB, then YouTube Music), with the usual length and version checks. Nothing is saved; either may be null. |
| `lyrics.for_video` | `{ "title", "artist"?, "video_id", "video_duration_s"?, "song_path"?, "song_video_id"?, "song_duration_s"?, "video_path"?, "full"?: false }`. **Without `full`, YouTube is asked nothing:** the answer is lyrics already timed to this video by an earlier `full` request, or a record on LRCLIB of the video's length whose times differ from the song's own, or `synced` null. `full: true` is the app's Karaoke button: everything described on the right is done. `video_id` is the video that's playing. `song_path` is the library song it's the video of (its own `.lrc` and its own sound are used); without it `song_video_id` is the song on YouTube Music, and without that the song is looked for by name. `video_path` is the video itself when it's a saved one in the library. | `{ "synced", "plain": null, "how", "source", "note" }`: the song's lyrics **timed to that video** (v0.2), or `synced` null when it can't be done (the app then shows the words with no line lit up). `how` says which way it was done: `audio` (the video's sound lined up with the song's by fingerprint, and each line moved to where it's sung in the video), `captions` (the lines pinned to the video's own captions), `caption_text` (the label's captions themselves, for a song with no timed lyrics) or `lrclib` (a record timed to the video's cut, used only if its times differ from the song's). Nothing is saved in the library. A `full` request takes about 2.5 s the first time for a library song and 8 to 14 s for a song played from YouTube Music; the answer is kept in the index for 30 days (a miss for one day), and is then given to requests without `full` as well. |
| `discover.suggest` | `{ "seeds": [{ "kind", "playlist_id"?, "name"? }], "count"?: 50, "shuffle"?: "", "token"? }`. 1 to 8 seeds (see **Discover seed**), `count` 1 to 500. `shuffle` is any word: the same word starts from the same songs of the owner's, a new one from others. `token` is echoed in `discover.progress`. | `{ "picks": [Candidate + { "why", "hits", "genre" }], "wanted", "radios", "seeds": [{ "kind", "label", "radios" }], "note" }`: songs the owner doesn't have, best first (v0.4). `why` is one plain line ("On the radio for 3 of your songs", "Similar to Linkin Park"); `hits` is how many of the radios asked had the song. `genre` (or null) is the genre the pick was found under: the genre asked for, spelled as the owner's own files spell it, or the genre tag of the owner's song whose radio it was on. `note` (or null) says in plain English when fewer than `count` were found, or when a seed gave nothing. Lookups only: nothing is downloaded and the library isn't changed. It asks YouTube Music for 4 to 24 radios through the rate limiter, so it takes about 8 seconds for 10 picks and 14 for 100 (500 should be under a minute); the answers are kept in the index for a week, so the same request again is immediate. Never among the picks: a song already in the library (by YouTube id, or by title, version and artist), a candidate rejected in review, a song waiting in the download queue, the same song twice, or anything that isn't official audio. A pick is downloaded the usual way: `plan.create` kind `download`. |
| `queue.jobs` | `{ "batch_id" }` | `{ "jobs": [{ "job_id", "kind", "state", "reason", "message" }] }`: how a batch's jobs ended, so the app can say what happened to a download or an edit |
| `queue.downloads` | — | `{ "downloads": [{ "job_id", "batch_id", "state", "reason", "message", "video_id", "title", "artists", "video", "height", "fps", "thumbnail", "progress" }] }`: the owner's own downloads (plan kind `download`) that haven't arrived, newest first: `queued`, `running`, or ended `failed` / `needs_review` (v0.2, for the app's Downloads page). `progress` is the share of a `running` download that has arrived, 0 to 1 (a video's picture and sound counted as one), or null: it isn't running, YouTube hasn't said how big it is, or another process is running the queue. At 1 the job is still checking and tagging what arrived. |
| `queue.dismiss` | `{ "job_id" }` | the same as `queue.downloads`. Takes one download off that list: one still `queued` is cancelled before it starts, one that ended without the song is no longer shown (both become `cancelled`). One that's `running` is refused. |
| `settings.get` | — | `{ "daily_cap", "daily_cap_default", "daily_cap_max" }`: the engine's settings the app shows (kept in `config.json`) |
| `settings.set` | `{ "daily_cap"? }` (1 to `daily_cap_max`, which is 500; the app offers steps of 50) | the same as `settings.get`. A new cap applies from the next queue run. |

**RPC-only:** `plan.create` with kind `edit` (the app's Edit Details sheet) or `remove` (the app's Delete on a download), `youtube.stream`, `youtube.video`, `lyrics.find`, `lyrics.for_video`, `discover.suggest`, `listening.*`, `playlist.*`, `queue.downloads`, `queue.dismiss`.

**CLI-only in v0.1** (RPC comes with the v0.2 app when needed): `sources.remove`, `index.rebuild`, `report`, `review export/import`, `lyrics`, `artwork`, `doctor`.

`review.decide` with `"url"` is the **Other → paste a link** path. The URL is fetched through `youtube.get_track()`, scored like any candidate, and kept in `review` with reason `url_low_score` if it scores below 0.6. It's never accepted blindly.

### Notifications (engine → app, no `id`)

| Method | Params |
|---|---|
| `job.progress` | `{ "job_id", "done", "total", "message" }`, at most 4/s per job |
| `job.finished` | `{ "job_id", "ok", "summary", "error"? }` (`summary` is null when `ok` is false; `error` is plain English) |
| `discover.progress` | `{ "token", "done", "of" }`: while `discover.suggest` works, one after each radio it has asked for (`of` is how many it expects to need) |
| `queue.state` | `{ "state", "reason"?, "resume_at"? }`, when the queue worker starts or stops, and after `queue.pause` |
| `review.changed` | `{ "review", "not_found" }` |
| `library.changed` | `{ "batch_id"?, "tracks_added", "tracks_changed" }`, after a queue run that finished jobs (`tracks_changed` = jobs done) and after an undo |

### Errors

Standard JSON-RPC codes, plus:

| Code | Meaning |
|---|---|
| -32000 | The request couldn't be done (a plain-English reason, e.g. "No library is open yet", a folder that doesn't exist) |
| -32001 | Library locked by another engine |
| -32002 | Path outside library |
| -32003 | External tool missing (`data.tool`) |
| -32004 | YouTube paused us (`data.resume_at`) |
| -32005 | Plan out of date; re-plan |
| -32006 | Not found (item, plan, batch) |
| -32007 | Busy: another long operation is running |

`error.message` is always plain English, suitable to show the user directly. Also standard: -32700 (not JSON), -32600 (not a request, a batch array, or anything before `engine.hello`), -32601 (no such method), -32602 (a missing or mistyped param), -32603 (unexpected; `data.log` is the log file).

### Shapes

```jsonc
// Candidate
{ "candidate_id": "c_…", "video_id": "…", "title": "…", "artists": ["…"], "album": "…",
  "album_browse_id": "MPRE…", "duration_s": 228, "is_official_audio": true, "is_explicit": false,
  "version_tokens": ["remix:adventure club"], "score": 0.917,
  "reasons": ["artist exact", "title exact", "version match", "duration Δ1s"] }

// Track (v0.2). `path` and `cover` are relative to the library root, with `/` separators.
// The app plays the file and shows the cover by reading them; it never writes to them.
{ "track_id": "t_…", "path": "Music/Artist/Album (2020)/01 Song.m4a", "title": "…", "artist": "…",
  "album_artist": "…", "album": "…", "year": 2020, "track": 1, "disc": 1, "genre": "…",
  "duration_s": 228.1, "explicit": false, "only_copy": false, "source": "rip_copy", "source_id": "videoId or null", "match": "auto_details", "acquired": "2026-09-30T10:00:00Z",
  "format": "mp3", "bitrate_kbps": 320, "cover": "Music/Artist/Album (2020)/cover.jpg",
  "embedded_cover": true, "lyrics": "synced",    // lyrics: "synced" | "plain" | "none"
  "video": false, "height": null }               // a saved video (in Music/Videos/): true, and its picture's height

// Listening and Playlist (v0.2). Songs are named by `track_id` (MUSICORG_ID), which survives renames.
{ "favourites": ["t_…"], "library": ["t_…"], "plays": { "t_…": { "count": 3, "last_played": "2026-10-01T03:00:00Z" } },
  "playlists": [ { "id": "pl_…", "name": "Road trip", "created_at": "…", "track_ids": ["t_…"] } ] }

// ReviewItem
{ "item_id": "i_3fa2…", "source_path": "…", "parsed": { "artist": "…", "title": "…", "version_tokens": [..], "confidence": 0.9 },
  "duration_s": 229, "state": "review", "reasons": ["version_mismatch"], "candidates": [Candidate],
  "fingerprint": null }
```
