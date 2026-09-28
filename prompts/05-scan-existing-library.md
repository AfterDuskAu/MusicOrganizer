# Step 05: Read-only scan of existing rip folders

## Context
The owner's current library is YouTube-to-MP3 rips: filenames like `Artist - Title (Official Video) [HQ].mp3`, tags that are junk or missing, bitrates that lie ("320kbps" files made from 128k sources). This step indexes those folders **without changing a single byte**.

## Goal
Build:
- `musicorg.index`: SQLite
- `musicorg.normalize`: artist, title and version parsing from messy names
- `musicorg.scan`: walks sources read-only

Implement the CLI commands `sources add|list|remove`, `scan` and `index rebuild`.

## Build
1. **`index.py`:** `index.sqlite` (cache) and `queue.sqlite` (not a cache; the tables are created in step 09a). Schema version in `PRAGMA user_version`, WAL mode, one connection per thread, read-only connections for no-lock commands. Tables in `index.sqlite`, at minimum:
   - `sources(id, path, added_at)`. Ids are the stable `s_…` ids from `state.py`.
   - `external_items(id, source_id, rel_path, size, mtime, sha1_head, ext, codec, duration_s, bitrate_kbps, raw_tags_json, parsed_artist, parsed_title, parsed_version_json, parse_confidence, flags_json, state, reasons_json)`
     - **`id` is stable:** `i_` + the first 16 hex characters of SHA-1(`<source_id>/<rel_path>`), so it survives a rebuild.
     - `state` uses the item-state enum in `ENGINE_API.md`.
   - `library_tracks(musicorg_id, rel_path, size, mtime, title, artist, album, duration_s, source, source_id, only_copy, origin_path)`
   - `search_cache(query_key, fetched_at, response_json)`
   - `candidates(id, item_id, video_id, payload_json, score, reasons_json)`
   - `fingerprints(path, size, mtime, duration_s, fp_blob)`
2. **`normalize.py`:** the heart of matching quality.
   - `parse_filename(stem) -> Parsed(artist, title, version_tokens, junk_removed, confidence)`
   - `parse_tags(TrackTags) -> Parsed`
   - `best_parse(file) -> Parsed`: prefer tags when they look real, otherwise the filename.
   - **Junk to strip** (case-insensitive, in brackets or free): official (music) video/audio, official lyric video, lyric(s) video, lyrics, visualizer/visualiser, audio, HQ, HD, 4K, 1080p, 320kbps and other bitrates, free download, out now, premiere, `[… release]` label tags, full song, emoji decorations.
   - **Version tokens** (**keep**, as `kind[:detail]`): `remix:<remixer>`, `edit`, `radio edit`, `extended`, `vip`, `bootleg:<who>`, `flip:<who>`, `live[:venue]`, `acoustic`, `unplugged`, `remaster[:year]`, `instrumental`, `sped up`, `slowed`, `nightcore`, `cover:<who>`, `demo`, `clean`, `explicit`. Handle `Rmx`, `Remix by X`, `X Remix`, `(X Remix)` and `[X Remix]`. "Slowed + reverb" gives `slowed`.
   - **Artists:** split on `feat.`, `ft.`, `featuring`, `with`, ` x `, ` & ` and `,` into a list, main artist first. Remixer-first filenames (`Remixer - Title`) are common: record the remixer as a version token and mark the artist uncertain.
   - **Compare key:** NFKC, casefold, strip punctuation and repeated spaces, `&` → `and`. Keep non-Latin scripts; no transliteration.
   - **`confidence`** 0–1: a clean `Artist - Title` is ≥ 0.8; a single token or likely-reversed order is < 0.5. **"Low confidence" means < 0.5** everywhere.
3. **`scan.py`:**
   - Walk each source recursively. **Don't follow folder symlinks**, and skip any folder that resolves to the library root.
   - Take audio extensions only (`.mp3 .m4a .aac .flac .wav .ogg .opus .webm`). Skip hidden files, `._*` and the other ignored names from `CLAUDE.md`.
   - Per file: `probe()`, `read_tags()`, `best_parse()`, and `sha1_head` (SHA-1 of the first 1 MB, plus the size).
   - Incremental: skip files with unchanged (size, mtime).
   - `.webm`, `.aac` and `.wav` are indexed, with the flag `not_adoptable` (they can still be replaced; see contract section 3).
   - **Fake-bitrate flag:** MP3s at ≥ 256 kbps with signs of a YouTube source (encoder tags, typical rip-channel names) get flagged `suspect_upscale`. It's a heuristic, and the report says so.
   - Files are opened **read-only**. Report progress through a callback.
4. **CLI:**
   - `sources add <path>`: the path must exist, and it's refused if it's inside the library root **or contains it**.
   - `sources list`
   - `sources remove <id>`: forgets the source only.
   - `scan [<id>…]`
   - `index rebuild`: follows contract section 5 exactly, and never touches `queue.sqlite`.
5. **`status`:** item counts by state, plus how many have low parse confidence.

## Tests
- **At least 60 filename cases** in a table (`tests/data/filenames.tsv`: input → artist, title, version tokens, confidence band). Include:
  - remixer-first filenames
  - feat. in the title vs in the artist field
  - brackets inside brackets
  - non-Latin titles
  - reversed "Title - Artist" (low confidence)
  - bootlegs
  - "slowed + reverb"
  - `[Monstercat Release]`
- A scan of a fixture folder:
  - indexes the items, and a rescan is incremental
  - leaves source files byte-identical
  - skips a folder symlink
  - skips a library root placed inside the source
- `sources add` refuses a path containing the root.
- **Rebuild keeps ids:** record a decision in `state.json`, delete `index.sqlite`, rebuild, and the decision is attached to the same file.

## Acceptance
1. `touch /tmp/scan-marker`
2. `musicorg sources add <your rips folder>`, then `musicorg scan`. Completes on the real library.
3. `find <rips folder> -newer /tmp/scan-marker ! -name .DS_Store` prints nothing.
4. `musicorg status` shows the item count and the low-confidence count.
