# Step 10: Lyrics and artwork

## Context
The owner's library has no lyrics today. Synced lyrics power the karaoke view in the v0.2 Mac app. LRCLIB is a free lyrics database with line-synced lyrics. Square album art comes from YouTube Music album thumbnails, not the 16:9 video thumbnail.

## Goal
Build `musicorg.lyrics` and `musicorg.artwork`, fill in step 09b's hook, and implement `musicorg lyrics [--missing]` and `musicorg artwork [--missing]` for tracks already in the library, as plans run with `apply`.

## Build
1. **`lyrics.py`:**
   - **Before coding, read LRCLIB's current API docs** and check the parameters and response fields. Expected:
     - `GET https://lrclib.net/api/get` with `track_name`, `artist_name`, `album_name`, `duration` (seconds); returns 404 when not found
     - `GET /api/search`
     - response fields `syncedLyrics`, `plainLyrics`, `instrumental`, `trackName`
   - Send a descriptive `User-Agent` (app name, version, project URL or contact), as LRCLIB asks.
   - **Omit `album_name`** when the album is unknown or `Unsorted`.
   - Fallback: `/api/search`. Accept a result only if its duration is within ±2 s and artist and title normalise equal.
   - **Version guard:** if the track has a `remix`, `live`, `sped up`, `slowed`, `nightcore` or `extended` token and the LRCLIB record's `trackName` lacks the same token, **don't** write synced lyrics. Embed plain lyrics only and mark `lyrics_version_uncertain` in the index.
   - Limits: 1 request/s. Cache responses, including "not found", for 30 days. Replay mode applies.
   - **Output:**
     - Synced lyrics → the `.lrc` sidecar via `fileops.write_sidecar`. Validate first: every line's timestamp parses, timestamps are non-decreasing, and `[ar:]`, `[ti:]` and `[offset:]` headers are allowed.
     - Plain lyrics → embedded in the tags.
     - `instrumental: true` → no lyrics, and the index records `instrumental`.
2. **`artwork.py`** (thumbnail fetches go through `youtube`'s rate limiter):
   - `album_art(album_browse_id) -> bytes | None`: take the largest album thumbnail from `get_album()`.
   - Googleusercontent thumbnail URLs usually accept a size suffix like `=w1200-h1200`. **Test first:** compare against the unsized URL's real dimensions, and never upscale a smaller original.
   - Check it's square (±2%) and at least 500 px. Otherwise keep the largest available and log it.
   - Convert with Pillow into an in-memory buffer (`# fileops-ok: in-memory`). JPEG, quality 90, at most 1200×1200. **Don't re-encode** an image that's already a JPEG of 1200 px or less.
   - Embed it in the file (verified tag write) and write `cover.jpg` once per album folder via `write_sidecar`. An existing *different* `cover.jpg` is left alone and logged.
   - Only-copy tracks get no automatic art, unless the owner supplied `art_url` in the review CSV (a YouTube thumbnail or any https image). Fetch it, check it, embed it.
3. **CLI:** `lyrics --missing` and `artwork --missing` create a plan (kind `lyrics`/`artwork`) and print its id. `musicorg apply <plan_id>` runs it through the queue: the lyrics and artwork jobs are network jobs.

## Tests
Replay recorded LRCLIB fixtures for:
- synced found
- plain only
- instrumental
- not found
- a search-fallback hit
- a remix whose record lacks the remix token (synced rejected, plain embedded)

Plus:
- A malformed `.lrc` is rejected. A non-decreasing one with `[offset:]` is accepted.
- The sidecar write and undo round-trip. An existing different `.lrc` is superseded, not overwritten.
- Artwork: a non-square image is flagged. A large image is resized to ≤ 1200 px. A small JPEG is left un-re-encoded. An existing different `cover.jpg` is kept.

## Acceptance
- `musicorg lyrics --missing` → `apply` → `queue run` on the tracks replaced in step 09b. Report how many got synced, plain only, or none.
- Open 3 `.lrc` files alongside playback in a player that reads sidecar LRC files, and check the timing.
- Record in `docs/CHANGELOG.md` the **count** of files whose verified tag writes passed.

## Owner's notes (2026-09-30)
- **The owner wants every song to get its cover automatically**, the same way names, artists and lyrics are fixed. For replaced songs the plan above already does that. A single's "album" on YouTube Music is the single release, so a single gets its own song cover, not a random album's.
- **Only-copy songs:** the plan above gives them no automatic art (a wrong cover is worse than none), only an `art_url` the owner supplies. The owner's wish goes further, so decide with the owner at this step:
  - keep the rule;
  - use the album art of a YouTube Music match the owner confirmed in review;
  - or add a new source such as MusicBrainz's Cover Art Archive. That's a new network service: ask first, as for any dependency.
- **The owner's decisions (later that day):**
  - Lyrics: use every source that allows it. That's LRCLIB, then YouTube Music's own lyrics (Musixmatch or LyricFind, often timed). Genius and Musixmatch's own API are left out: paid keys, or terms against automatic copying.
  - Covers for songs with no official match: none, unless a picture is found on another source. The owner's `art_url` can now be a picture or a page with one (SoundCloud, Bandcamp, YouTube); its picture is read with yt-dlp, and nothing is downloaded.
