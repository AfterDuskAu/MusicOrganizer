# Step 09b: Replace and adopt pipeline

## Context
This is where files actually land in the library. Every file goes through staging → verify → fingerprint → tag → commit, through `fileops`, journaled and undoable. The owner's rips are **never** touched: replacing means adding a clean official copy to the library and linking the rip to it.

## Goal
Build `musicorg.pipeline`: replace jobs, adopt jobs, `plan replace`, `plan adopt`, `plan show`, `apply`, fingerprint calibration, and undo integration with item states.

## Build
1. **Replace job, one video** (a job may serve several rips; see point 3):
   1. `fileops.check_op()`: the preconditions still hold, or the job ends `needs_review` with reason `file_changed`.
   2. `download_audio()` into `fileops.stage_path(batch, …)`.
   3. `probe()`: AAC codec, bitrate ≥ 100 kbps, duration within ±2 s of the candidate. Otherwise `needs_review` with the reason.
   4. **Fingerprint gate** against each linked rip. `different` → that rip goes back to `review` with `fingerprint_mismatch`. `uncertain` → `review` with `fingerprint_uncertain`. The verdict is stored for the review CSV's `fingerprint` column. If no linked rip passes, the staged file is kept for 24 h and nothing is committed.
   5. Build `TrackTags`:
      - metadata from the candidate and `get_album()` (track number found by the step 06 rule; no disc number)
      - `SOURCE=youtube_music`, `SOURCE_ID`
      - `SOURCE_FORMAT` from `info['format_id']`, `SOURCE_BITRATE` from `probe()`
      - `ACQUIRED`, `MATCH` (`auto_exact` for `matched_auto`, `user_confirmed` for `matched_user`), `MATCH_SCORE`, `VERSION`
      - `ORIGIN_PATH` = the first passing rip, and a new `MUSICORG_ID`
   6. Lyrics and artwork: call step 10's functions if present (a no-op hook until then). Missing lyrics never fail a job.
   7. **One verified tag write** on the staged file with everything: tags, plain lyrics and cover.
   8. `fileops.commit()` to `naming.library_path(tags)`.
   9. Sidecars, using the **final committed name**: `.lrc` and `cover.jpg` via `write_sidecar`.
   10. Mark each passing rip `superseded`, and record rip path → `MUSICORG_ID` in `state.json`.
2. **Adopt job** (items in `only_copy`, or `not_found` with `--include-not-found`):
   - Allowed formats: mp3, m4a, flac, ogg, opus. Any other format: the job is skipped and the item's state becomes `unsupported_format`.
   - Steps:
     1. `check_op()`.
     2. `copy_in()` (SHA-256 verified).
     3. Build tags.
     4. Verified write.
     5. Commit.
     6. Mark the item `adopted`.
   - **Metadata rule:** use the parsed artist and title only if `parse_confidence ≥ 0.8` or the owner filled `artist_fix`/`title_fix`. Otherwise keep the rip's own title and artist tags and write provenance only. Missing artist → `Unknown Artist`; missing title → the file name.
   - Provenance: `SOURCE=rip_copy`, `ONLY_COPY=1`, `ORIGIN_PATH`, a new `MUSICORG_ID`. `MATCH=manual` only when the owner's fixes were used.
3. **Plans:**
   - `plan replace [--only auto|accepted|all-eligible] [--limit N] [--stage-only]`:
     - **Groups items by videoId:** one download per video, gated against each rip separately, all linked to one `MUSICORG_ID`.
     - If that videoId is already in the library (`MUSICORG_SOURCE_ID`): no download, just gate and link.
   - `plan adopt [--include-not-found]` includes a count of low-confidence adopts in the summary.
   - `plan show <plan_id>` (no lock): operations and summary (count, estimated time from the throttle, disk space ≈ duration × 16 KB/s).
   - `apply <plan_id>`: validate, open a batch (it stays open until its last job ends), enqueue, and print the `batch_id`.
4. **`--stage-only` (calibration mode):** runs through the queue but stops after the fingerprint step. Downloads are kept in `_Staging/calibration/`, which `clean_staging` skips. Nothing is committed and nothing is superseded.
5. **Undo integration:** `undo <batch_id>` (step 03b) now also:
   - cancels the batch's queued jobs, and refuses while one is running
   - returns item states: `superseded`/`adopted` → the previous state
   - removes the superseded links from `state.json`
   
   Because rips were never touched, undo leaves the library exactly as before the batch.

## Tests
- A full replace with a **stubbed downloader** returning the melody A fixture, against a rip that is the MP3 of melody A. The file lands at the expected path with complete provenance, lyrics hook called, sidecars named after the committed file.
- A fingerprint mismatch (melody B download) → the rip is back in `review` with `fingerprint_mismatch`, and nothing committed.
- Two rips of the same videoId → one download, both linked to one `MUSICORG_ID`. A videoId already in the library → no download.
- Adopt: a low-confidence parse keeps the original tags, `ONLY_COPY=1` is set, and the source is byte-identical. A `.webm` item → state `unsupported_format`.
- A source file changed between plan and job → `needs_review` with `file_changed`.
- Undo of a replace batch → `Music/` back to the pre-batch state, the item states restored, and `state.json` links removed.
- `--stage-only` commits nothing.

## Acceptance
1. **Calibrate first:** `musicorg plan replace --only auto --limit 25 --stage-only`, then `apply`, then `queue run`.
2. Build `pairs.csv`:
   - the 25 rip-vs-download pairs (`same=yes` after listening to any that look odd)
   - **10+ different-version pairs from your own library**, e.g. a remix rip vs the original rip, a live rip vs the studio rip (`same=no`)
3. Run `scripts/calibrate_fp.py pairs.csv`. There must be no `match` verdict on any `same=no` pair. Record the chosen thresholds in `config.json` and `docs/CHANGELOG.md`.
4. **First real batch:** `plan replace --only auto --limit 25` → `plan show` → `apply` → `queue run`. All 25 end `done` or `needs_review`, with no `failed` except genuine YouTube errors.
5. Open 5 new files in Apple Music: correct title, artist, album, art and track number.
6. `musicorg undo <batch_id>`. `musicorg status` shows the pre-batch counts, and `Music/` matches. Then make a **new** plan and apply it.
7. Only then run bigger batches, with the daily cap left on.
