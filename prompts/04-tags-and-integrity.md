# Step 04: Tags, probe and the audio-integrity check

## Context
Read `docs/LIBRARY_CONTRACT.md` sections 3 and 4. `fileops.write_tags` got its journal and undo plumbing in step 03b. This step makes the actual write real and verified.

## Goal
Build `musicorg.tags`:
- read and write the full tag schema for M4A, MP3 (ID3v2.3), FLAC and Ogg/Opus
- read-only handling for everything else
- `probe()` and a fresh audio hash
- the verified-write guarantee (contract 6.7)

## Build
1. **`TrackTags` dataclass:** one field per row in contract section 4 (standard and provenance), plus `cover: bytes | None` and `cover_mime`. `None` means "absent". A sentinel `REMOVE` means "delete this field". Empty strings are never written.
2. **`read_tags(path) -> TrackTags`:**
   - `.m4a`, `.mp3`, `.flac`, `.ogg` and `.opus` via mutagen.
   - `.webm`, `.aac` (raw ADTS) and `.wav` return an empty `TrackTags` plus a warning and **never raise**.
   - MP3 rips are messy: tolerate ID3v1, v2.2–2.4, duplicate frames and broken encodings. Record warnings and carry on.
3. **`write_tags(path, tags)`:**
   - Writes managed fields and the cover.
   - **Keeps every field and atom it doesn't manage**, e.g. `iTunSMPB` gapless data and unknown ID3 frames.
   - Freeform M4A atoms go under `----:com.apple.iTunes:<NAME>` as UTF-8.
   - MP3 is saved as **ID3v2.3** (`v2_version=3`, year in `TYER`), with `TXXX:<NAME>` provenance.
   - Vorbis formats use plain comments plus a picture block.
   - `REMOVE` deletes a field. `None` leaves it as is.
   - Multi-value `MUSICORG_VERSION` is joined with `; `.
4. **`probe(path)`** via `ffprobe -v error -show_format -show_streams -of json`: codec, duration (float seconds), bitrate (kbps), sample rate, channels. Use it everywhere instead of tag durations.
5. **`audio_hash(path) -> str`:** `ffmpeg -v error -i <path> -map 0:a:0 -f md5 -`, parsed to hex. Always computed fresh for writes. A (path, size, mtime) cache may be used only for scan-time reporting.
6. **Make the verified write real** (inside `fileops.write_tags`, calling `tags.write_tags` on a staged copy):
   1. Hash the audio.
   2. Copy the file to `_Staging/`.
   3. Write the tags on the copy.
   4. Hash the copy.
   5. If they match: save the old cover to `.musicorg/undo-art/<sha256>.jpg`, then swap the copy in with `os.replace` (with the Windows `PermissionError` retry from step 03b).
   6. If they don't: `discard_staged`, raise `IntegrityError`, and leave the original untouched.
7. **Undo exactness:** the undo of a write computes the diff from the journaled before-state. Fields that were absent before are written as `REMOVE`, including a `MUSICORG_ID` added by the same batch.
8. **`new_track_id()`:** UUIDv4. `MUSICORG_ID` is set once and **never** changed afterwards. `write_tags` refuses to change an existing different `MUSICORG_ID`, except during undo of the batch that added it.
9. **Round-trip tests** on the generated fixtures (M4A, MP3; build FLAC and Opus fixtures with ffmpeg here too):
   - every field written → read back identically
   - the cover survives
   - unmanaged frames and atoms survive
   - Unicode (Japanese, emoji, accents) survives
   - the audio hash is unchanged after a write
   - add lyrics and a cover to a file that had neither → undo → both gone, hash unchanged
   - a monkeypatched corrupting write raises `IntegrityError` and leaves the original untouched
   - `read_tags` on a `.webm` fixture returns empty with a warning
   - Windows CI: `write_tags` while another handle holds the file open → `FileInUseError`, original intact
10. **Compatibility check** (a manual script, not CI): `scripts/check_compat.py` writes a sample M4A and MP3 into a temp folder and prints instructions:
    - Drag them into **Apple Music** and check title, artist, album, artwork and lyrics.
    - Open them in **Kid3** or **MusicBrainz Picard** (both free) and check the `MUSICORG_*` fields.
    
    Record the results in `docs/CHANGELOG.md`.

## Rules
- Only `fileops` calls `tags.write_tags`. The step 03a write-rule test allows `tags.py` only its mutagen `.save(`.

## Acceptance
- All tests pass on the three CI runners.
- `scripts/check_compat.py` has been run once, with the results recorded.
