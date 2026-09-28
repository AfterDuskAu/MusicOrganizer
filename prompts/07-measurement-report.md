# Step 07: The decision report (v0.1 checkpoint)

## Context
This is the number the whole project turns on: how much of the owner's library can be replaced cleanly, how much needs a human, and how much exists nowhere else. **After this step, stop and bring the report back to the owner's Claude chat before building steps 08–11.**

## Goal
Build `musicorg.report` and the CSV review round-trip (`review export` / `review import`), so decisions can be made in Numbers or Excel before the Mac app exists.

## Build
1. **`musicorg report [--out <dir>]`** (no lock). Writes `report-YYYY-MM-DD.md` and `.csv` via `fileops.write_export` (default folder: `<LibraryRoot>/Reports/`), then prints the full path. The markdown includes:
   - **Headline table:** total items and each item state (`matched_auto`, `review`, `not_found`, `unsupported_format`, and so on), as counts **and** percentages.
   - **Why review:** a breakdown by review-reason code.
   - **Why not found:** no results vs low score, plus the top 30 examples with their parsed artist and title.
   - **Version profile:** counts of remix, bootleg, live, sped up/slowed and so on, library-wide and per state. This shows how remix-heavy the library is.
   - **Quality:** bitrate distribution of existing files, and the `suspect_upscale` count (heuristic). Note that YouTube Music official audio is ~128 kbps AAC: the replacement is *cleaner and correctly tagged*, not *higher bitrate*.
   - **Cost estimate:** downloads needed if every `matched_auto` is replaced, and the time at the step 09a throttle defaults (one at a time, 8–25 s pauses, 300/day cap), as "about N days".
   - **Recommendation paragraph** from thresholds; the owner decides:
     - `not_found` ≥ 30%: "Adopting only-copy tracks matters as much as replacing. Prioritise `plan adopt`."
     - `matched_auto` ≥ 60%: "Replace-first will clean most of the library quickly."
2. **`review export <csv> [--include-auto]`** (no lock), via `write_export`, so it never overwrites. UTF-8 **with BOM**. One row per `review` or `not_found` item (plus `matched_auto` with the flag). Columns:
   - `item_id`, `source_path`, `duration`, `parsed_artist`, `parsed_title`, `parsed_version`, `reasons`
   - `cand1_title`, `cand1_artists`, `cand1_version`, `cand1_duration`, `cand1_score`, `cand1_url`, and the same for cand2 and cand3 (`candN_url` = `https://music.youtube.com/watch?v=<id>`)
   - `fingerprint` (filled only after step 09b for items whose replace job ended in review, e.g. "different audio ✗ (BER 0.31)")
   - `decision`, `url`, `artist_fix`, `title_fix`, `album_fix`, `art_url` (all empty; `art_url` is used in step 10)
3. **`review import <csv>`** (lock):
   - **Encoding:** decode strictly as `utf-8-sig`. If that fails, refuse the whole file with "Save it as 'CSV UTF-8' in Numbers or Excel and import again."
   - **Validate every row first** and report all errors together, with row numbers. Refuse:
     - an unknown `item_id`
     - a `source_path` that differs from the indexed path for that id (protects against rows moving)
     - an invalid decision
     - a `url` that isn't `music.youtube.com/watch?v=`, `youtube.com/watch?v=` or `youtu.be/`
   - **Decisions** (the CSV enum in `ENGINE_API.md`):
     - `accept` → cand1
     - `cand:2` / `cand:3`
     - `url` → `youtube.get_track()`, then scored like a candidate. If the score is below 0.6, the item stays `review` with reason `url_low_score` and a warning. **It's evidence, not blind acceptance.**
     - `only_copy` (the `*_fix` columns are stored)
     - `skip`
     - `reject:<n>` → that candidate is never proposed again
   - Accepted items become `matched_user`. Decisions go to `state.json` (atomic) and the index.
   - **Idempotent:** importing the same CSV twice changes nothing the second time.

## Tests
- A report on a fixture index gives the exact expected counts and percentages.
- CSV round-trip: export → edit → import applies the decisions, and all invalid rows are reported with line numbers.
- Commas, quotes, newlines and non-Latin text survive. A cp1252-encoded file is refused with the message.
- A mismatched `source_path` is refused. A double import changes nothing.
- An export CSV exported **before** `index rebuild` still imports correctly after it (stable ids).

## Acceptance (the checkpoint)
1. Run `musicorg match` over the **whole** real library. It's rate-limited, so it may take hours: leave it running.
2. Run `musicorg report`.
3. **Stop.** Paste the headline table and the recommendation paragraph into the chat with Claude and decide together what comes next. Don't start step 08 automatically.
