# Step 06: YouTube Music matcher

## Context
Official YouTube Music "song" uploads (the auto-generated `Artist - Topic` releases) carry clean artist, album and track metadata. The matcher finds, for each scanned rip, the official upload of *the same recording*: same version, same remix, same length. **Wrong version is worse than no match.**

## Goal
Build:
- `musicorg.youtube`: search and metadata only in this step, plus the rate limiter and replay mode
- `musicorg.match`: scoring and classification

Implement `musicorg match`.

## Build
1. **`youtube.py`** wraps `ytmusicapi.YTMusic()`, unauthenticated:
   - `search_songs(query, limit=10) -> list[Candidate]`: the `songs` filter.
   - `get_track(video_id) -> Candidate | None`: via `get_watch_playlist(videoId=…, limit=1)['tracks'][0]`. Check the returned videoId matches; duration comes as a `length` string like `3:07`, so parse it. Used for pasted links in step 07.
   - `get_album(browse_id)`: album artist, year, and `tracks` with `trackNumber` and `trackCount`.
     - **There's no disc number.** Leave disc empty for downloads.
     - Find the track in the album by videoId. Failing that, by normalised title and duration ±2 s. Failing that, leave track number and total empty and log it. **Never guess.**
   - **Record real responses from the installed ytmusicapi before relying on any field:** 15–20 searches, 5 `get_watch_playlist` and 5 `get_album`, into `tests/fixtures/ytm/`. Fields expected (verify each): `videoId`, `title`, `artists[].name`, `album.name`, `album.id`, `duration_seconds` (or parse `duration`), `isExplicit`, `videoType` (official audio tracks are expected to be `MUSIC_VIDEO_TYPE_ATV`), `thumbnails`.
   - **Rate limiter:** one per process, shared by every call (including thumbnails later).
     - At most 1 request per 1.5 s, with ±0.5 s jitter.
     - Exponential backoff on HTTP 429 and connection errors.
     - After 3 consecutive network-level failures, raise `YouTubePausedError(resume_at)`.
   - **Replay mode:** when `MUSICORG_REPLAY_DIR` is set, answer only from fixtures and raise on a miss.
   - **Cache** raw search responses in `search_cache`, keyed by normalised query, for 30 days.
2. **Query building:** first `"<main artist> <title>"`, then the same plus the rendered version tokens (`"… Adventure Club Remix"`). For low-confidence parses, also try `"<title>"` alone and the reversed order. At most 3 queries per item; stop early on an AUTO-grade hit.
3. **Scoring** (`match.score(parsed, file_duration, candidate) -> (score, reasons)`):
   - Artist similarity, weight 0.35: `rapidfuzz.fuzz.token_sort_ratio` on normalised artist names, best pairing. **Not `token_set_ratio`**, which scores subsets as 100: "Closer" vs "Closer (Tribute to …)".
   - Title similarity, weight 0.35: the same, on normalised titles with version tokens removed.
   - Version agreement, weight 0.20: 1.0 if the non-soft token sets are equal, else 0.
   - Duration, weight 0.10: 1.0 at |Δ| ≤ 2 s, linear down to 0 at |Δ| ≥ 15 s.
   - `reasons`: short human strings plus the review-reason enum codes.
   - **Soft tokens** (`clean`, `explicit`, `remaster`) are excluded from the version check. Clean/explicit rule:
     - If the rip carries `clean` or `explicit`, only candidates whose `isExplicit` agrees can be AUTO.
     - If the rip says neither, and results contain both an explicit and a clean official version, prefer explicit (config `prefer_explicit: true`) and add the reason "explicit/clean pair: chose explicit".
4. **Classification per item** (item-state and reason enums in `ENGINE_API.md`):
   - **`matched_auto`** requires all of:
     - an official audio track (`videoType` ATV, or what the fixtures prove equivalent)
     - main artist normalises equal to one candidate artist
     - title normalises exactly equal
     - non-soft version sets equal
     - the clean/explicit rule satisfied
     - |Δduration| ≤ 2 s
     
     This means "safe to *queue*". Replacing still needs the fingerprint gate.
   - **`review`**: best score ≥ 0.60 but not AUTO. Keep the top 3 candidates, and the reasons for the best.
   - **`not_found`**: nothing ≥ 0.60, or no results.
   - Candidates previously rejected in `state.json` are never proposed again.
5. **`musicorg match [--limit N] [--rescan]`:**
   - Processes `new` items. `--rescan` also re-matches `review` and `not_found` items, bypassing the search cache.
   - Resumes after interruption. Prints progress with an ETA from the rate limiter.
   - **At the end, writes `Reports/auto-sample.csv`** via `fileops.write_export`: 20 random `matched_auto` items with parsed artist, title and version, candidate title and version, and a `https://music.youtube.com/watch?v=<id>` link.
6. **Evaluation harness:** `tests/data/match_cases.json`, 30+ hand-labelled cases. Each case is a parsed rip plus its recorded fixture, the expected state and the expected videoId. `pytest` fails if:
   - **any false `matched_auto`** occurs (zero allowed), or
   - overall top-1 correctness falls below 85%.
   
   Build the cases from real results for tracks you know. Include remixes, live versions, clean/explicit pairs, covers with the same title, and the "tribute" trap.

## Rules
- The matcher never downloads anything.
- Tests replay fixtures, and CI uses no network. `@pytest.mark.live` tests may hit YouTube Music on manual runs.

## Acceptance
- Harness: zero false AUTOs, and ≥ 85% top-1.
- `musicorg match --limit 100` on the real index runs without errors.
- Open `Reports/auto-sample.csv`, listen to all 20 links, and confirm each is the same recording as the rip. **Any wrong one becomes a harness case and a rule fix before continuing.**
