# Library contract

The engine guarantees everything in this document. It's written so that if the app is deleted tomorrow, the library still works in any player (Apple Music, foobar2000, Navidrome, VLC, a car stereo), and any future tool can pick it up.

## 1. Layout

```
<LibraryRoot>/                  chosen by the user; must NOT be inside a folder registered as a source
  Music/                        the library: organised, tagged audio files
    <Album Artist>/
      <Album> (<Year>)/
        01 <Title>.m4a
        01 <Title>.lrc          synced lyrics sidecar, same base name
        cover.jpg               album front cover
    Videos/                     saved videos (v0.2), apart from the songs
      <Artist>/
        <Title>.mp4             picture and sound in one file; its cover is inside it
  _Replaced/                    library files superseded by an upgrade or undo; mirrors Music/ paths; never auto-purged
  _Staging/                     engine scratch space: downloads and tag writes in progress
    calibration/                fingerprint calibration downloads (step 09b); never auto-cleaned
  Reports/                      reports and CSV exports, visible in Finder (written via fileops.write_export)
  .musicorg/
    index.sqlite                cache (see section 5)
    queue.sqlite                the job queue; NOT a cache; never deleted by rebuild
    state.json                  everything files can't carry (section 5)
    journal/YYYY-MM-DD.jsonl    append-only operation log
    plans/<plan_id>.json        dry-run plans
    undo-art/<sha256>.jpg       cover art saved for undo (.png for a PNG cover)
    lock, lock.info             single-writer lock and holder details
```

- **Managed folders:** `Music/`, `_Replaced/`, `_Staging/` and `.musicorg/`. The engine writes user data only there.
- **Other places the engine writes:** the app's own config, log and cache folders, and exports the user explicitly asked for (via `fileops.write_export`, which never overwrites).
- **Recommended root:** `~/Music Organizer Library` or a folder on an external drive, **not** inside `~/Music` if `~/Music` holds the rips you'll register as a source. The engine refuses a root inside a source, and a source that contains the root.
- `Mix/` (the weekly mix, v1.1) will sit beside `Music/` and is not part of the library.

## 2. Naming rules

Default template:

```
Music/<Album Artist>/<Album> (<Year>)/<Track> <Title>.<ext>
```

- **Fallbacks:**
  - The album artist falls back to the track artist, then `Unknown Artist`.
  - Unknown year: drop ` (<Year>)`.
  - Unknown album: `Music/<Artist>/Unsorted/<Title>.<ext>`.
  - Unknown title: the source file's name without its extension.
  - Compilations: album artist `Various Artists`.
- **The title is the song's whole name, its version included** (the owner's rule, 2026-10-04): *a song that has been found takes the found title; a song that hasn't been found keeps the title I had on it.*
  - **Found** (a download, a replacement, or a rip kept with its match's official details): the title is YouTube Music's own, e.g. `Lost Boy (Radio Remix)`.
  - **Not found yet, or kept as an only copy** (a copy named after its rip): the rip's clean title, followed by every version the rip names, in the rip's own words and order.
    - A named version goes in round brackets, whatever brackets the rip used: `Here (Lucian Remix)`, `Still Here (Acoustic Version)`. Spelling, capitals and accents are kept.
    - **The owner's own mark for a remix, a final `R`, stays exactly as typed:** `Come As You Are R`, `Done Wrong (R)`. It is never written out as "(Remix)" and never dropped. (Where the rip names the remix as well, as in `High Hopes (Filous Remix) R`, the R says nothing more, and the title is `High Hopes (Filous Remix)`.)
    - Junk such as `(320 kbps)` or `[Official Audio]` stays out.
    - A rip whose names couldn't be read with confidence (under 0.8) keeps its own title tag, untouched.
  - **A title the owner typed** (a title fix in review, or Edit Details) is used exactly as typed, and stays: `plan tidy`, and a later decision that gives no fixes, don't change it back, even when it is word for word what an older engine wrote. (The journal records that it was typed.)
  - The file name follows from the title by the rules below. So a remix and its original never compete for one name: `Come As You Are R.mp3` and `Come As You Are.mp3`.
- **Track numbers:** two digits, `01`. Multi-disc albums use `<Disc>-<Track>`, e.g. `2-07`. No track number: no prefix.
- **Sanitising, applied to every path component:**
  - NFC Unicode normalisation.
  - Replace `\ / : * ? " < > |` and control characters with `_`.
  - Trim leading and trailing spaces, and trailing dots. A leading `.` becomes `_`.
  - Windows reserved names (`CON PRN AUX NUL COM1–COM9 LPT1–LPT9`, any case, with or without an extension) get a `_` suffix.
- **Length:**
  - Each component: at most **120 characters and at most 200 bytes in UTF-8**. Truncate on a character boundary, title first, and keep the extension.
  - The full absolute path under `_Replaced/`, which is the longest variant, must fit in **259 characters on Windows**. `naming` computes the budget from the actual root and truncates the title to fit. `init` warns on Windows if the root is longer than 60 characters.
- **Saved videos** (v0.2): `Music/Videos/<Artist>/<Title>.mp4`. The artist is the album artist tag (the video's first artist), falling back as above. A video has no album, year or track number in its name, no `.lrc` and no `cover.jpg` (its cover is embedded). The same sanitising, length limits and collision rule apply. A video is recognised by where it is: an `.mp4` inside `Music/Videos/`. So that an artist called "Videos" doesn't land there, that one artist's folder is named `Videos (artist)`.
- **Collisions:** if the target exists and is not the same file, use ` (2)`, ` (3)` and so on before the extension. Compare case-insensitively (macOS and Windows defaults are case-insensitive). The reservation is atomic (section 6.4).

## 3. Formats

| Source | Stored as | Notes |
|---|---|---|
| YouTube Music download | `.m4a` (AAC, **format 140 only**) | Exactly as downloaded. Container fix-up allowed, no re-encode. No fallback to other formats. |
| YouTube Music video, saved whole (v0.2) | `.mp4`: one H.264 picture stream (144p to 1080p, the size the owner chose) and the **format 140** sound | Each stream exactly as downloaded, joined by ffmpeg without re-encoding. No fallback to another codec or size. Tagged like an M4A. Counts as one download. |
| Only-copy rip: MP3, M4A, FLAC, OGG, Opus | copied as is | Tags fixed on the copy only. |
| Matched rip kept as the owner's own audio (step 09c): MP3, M4A, FLAC, OGG, Opus | copied as is | The copy gets the match's official details. No download, so no fingerprint check: only AUTO matches and the owner's own choices. |
| Only-copy rip: WebM, raw AAC, WAV | **not adopted in v0.1** | Still matched and replaceable; counted in the report as `unsupported_format`. |
| Future lossless (Bandcamp, CD) | `.flac` | v1.1 Inbox; listed so the schema covers it. |

Copies for devices that need another format are a separate **export** feature, written to another folder and never into the library (post-v1).

## 4. Tag schema (schema version 1)

### Standard tags

| Meaning | M4A atom | MP3 (ID3**v2.3**) | FLAC / Ogg / Opus (Vorbis) |
|---|---|---|---|
| Title | `©nam` | `TIT2` | `TITLE` |
| Artist | `©ART` | `TPE1` | `ARTIST` |
| Album artist | `aART` | `TPE2` | `ALBUMARTIST` |
| Album | `©alb` | `TALB` | `ALBUM` |
| Year | `©day` | `TYER` | `DATE` |
| Track n/total | `trkn` | `TRCK` | `TRACKNUMBER` + `TRACKTOTAL` |
| Disc n/total | `disk` | `TPOS` | `DISCNUMBER` + `DISCTOTAL` |
| Genre | `©gen` | `TCON` | `GENRE` |
| Plain lyrics | `©lyr` | `USLT` | `LYRICS` |
| Front cover | `covr` (JPEG) | `APIC` type 3 | `METADATA_BLOCK_PICTURE` |
| Explicit | `rtng`: `1` explicit, `0` not (iTunes' `4` also reads as explicit, `2` "clean" as not) | `TXXX:ITUNESADVISORY` `1`/`0` | `ITUNESADVISORY` `1`/`0` |

- MP3 is written as **ID3v2.3** (`save(v2_version=3)`), because many car stereos and older players don't read v2.4.
- Existing tags and atoms the engine doesn't manage are **kept**, e.g. `iTunSMPB` gapless info in M4A, and unknown ID3 frames.

### Provenance tags (always written)

In M4A these are freeform atoms `----:com.apple.iTunes:<NAME>`. The `com.apple.iTunes` "mean" is used so that Picard, Kid3 and foobar2000 can display them. In MP3 they're `TXXX:<NAME>`, and in Vorbis formats plain comments.

| Name | Value | Example |
|---|---|---|
| `MUSICORG_SCHEMA` | Schema version | `1` |
| `MUSICORG_ID` | UUIDv4, assigned once, never changed. The track's identity across moves, renames and upgrades. | `3f0c…` |
| `MUSICORG_SOURCE` | See Enums in `ENGINE_API.md` | `youtube_music` |
| `MUSICORG_SOURCE_ID` | YouTube videoId, or a URL or other ID. For a rip kept with official details (step 09c), the videoId of its match | `dQw4w9WgXcQ` |
| `MUSICORG_SOURCE_FORMAT` | The actual format id yt-dlp delivered (`info_dict['format_id']`), or the codec for copies | `140` |
| `MUSICORG_SOURCE_BITRATE` | kbps from `probe()`, never a constant | `129` |
| `MUSICORG_ACQUIRED` | ISO-8601 UTC | `2026-10-02T09:14:00Z` |
| `MUSICORG_MATCH` | See Enums. Absent for adopts without owner fixes. | `auto_exact` |
| `MUSICORG_MATCH_SCORE` | 0.000–1.000 | `0.987` |
| `MUSICORG_ONLY_COPY` | `1` if no official source exists. Protect it. Absent for a rip kept with official details: an official source exists. | `1` |
| `MUSICORG_ORIGIN_PATH` | Copies and replacements: the original external rip path | `/Users/…/rips/x.mp3` |
| `MUSICORG_VERSION` | The versions the title names, as normalised tokens **separated by `; `**. The owner's `R` is `remix`. Absent when the title names no version. | `remix:adventure club` |

`MUSICORG_VERSION` says what the title says, for every file the engine brings in:

- a found song (a download, a saved video, a replacement, a rip kept with official details): read from the official title
- a copy named after its rip, and a title the owner typed: read from that title, with the owner's `R` understood as a remix
- Edit Details keeps it in step with an edited title: a new title that names no version removes it
- not written for a copy whose rip's names couldn't be read with confidence (under 0.8). A guess at a version isn't put into a file. A version tag the rip already carried is left as it is.

MusicBrainz IDs use Picard's standard names and are optional in v0.1. Leave room for them without writing them.

### Lyrics

- Plain lyrics are embedded in the file.
- Synced lyrics go in a `.lrc` sidecar with the same base name, UTF-8, `[mm:ss.xx]` timestamps (non-decreasing; `[ar:]`, `[ti:]` and `[offset:]` header tags are allowed).
- No lyrics found means no sidecar. Never write an empty or fake one.
- Synced lyrics are written only when the file is within 2 s of the length they were timed for (step 10). A file with a longer intro gets plain lyrics only, never lines at the wrong moment.

### Cover art

- The album's official cover from YouTube Music, 1200 × 1200 JPEG (never enlarged from a smaller original), embedded and saved once per album folder as `cover.jpg`. An existing different `cover.jpg` is left alone.
- A song with no official match gets no automatic cover. The owner's `art_url` (a picture, or a page with one) is the exception.

## 5. What lives outside the files, and rebuilding

`.musicorg/state.json` holds only what tags can't:

- favourites, play counts and playlists (v0.2, `listening`), by `MUSICORG_ID`
- review decisions, including rejected candidates, so they're never suggested again
- registered sources with their stable ids
- superseded-rip links (rip path → `MUSICORG_ID`)
- the fingerprint gate's results (rip item, video → verdict), so a download the gate turned down is never fetched again (step 09b)
- the owner's preferred spellings of names ("JAŸ-Z" → "Jay Z"), used in tags and folder names (step 09d)
- settings

It's written atomically (temp file, fsync, rename) after every batch and every review import.

**`musicorg index rebuild`** recreates `index.sqlite` as follows:

| Data | Rebuilt from |
|---|---|
| Library tracks | `Music/` tags |
| External items | a rescan of registered sources. Item ids are stable hashes, so decisions reattach correctly. |
| Decisions and superseded links | `state.json` |
| `adopted` state | `MUSICORG_ORIGIN_PATH` tags in `Music/` |
| Candidates, search cache, fingerprints, lyrics cache | recomputed by the next `match` and pipeline runs |

`queue.sqlite` is **not** a cache: rebuild never deletes it.

## 6. Safety guarantees (enforced by `fileops`)

1. **Single writer.**
   - Commands that change the library take an exclusive, non-blocking lock on `.musicorg/lock`: `fcntl.flock(LOCK_EX|LOCK_NB)` on macOS, `msvcrt.locking(LK_NBLCK)` on byte 0 on Windows.
   - The holder's PID and start time go in `.musicorg/lock.info`, so a second process can report who holds the lock.
   - Read-only commands don't lock (see `ENGINE_API.md`).
2. **Path guard.**
   - Every path an operation *modifies* must resolve (following symlinks) inside a managed folder, or the operation raises `OutsideLibraryError`. That covers the source of `move`, `supersede`, `trash`, `write_tags` and `write_sidecar`, and every destination.
   - The only outside paths ever touched are `copy_in` sources and scan inputs, and both are opened read-only.
   - The library root may not be inside a registered source, and a source may not contain the root.
3. **Journal first.**
   - Each operation writes an `intent` line (fsynced; `F_FULLFSYNC` on macOS) before acting and a `done` line after.
   - On open, any `intent` without `done` is recovered: completed, or rolled back.
   - A batch opened by `apply` stays open until its last queued job ends, and an open batch is not treated as a crash.
4. **Atomic, no-overwrite moves.** One helper does every move:
   1. Reserve the target with `open(target, 'xb')`. If it exists, try the next ` (n)`.
   2. Record the placeholder in the journal intent.
   3. `os.replace(src, placeholder)`.

   Never use `shutil.move`. Cross-volume moves (EXDEV) fail with a clear message and are never copy-then-delete. `_Staging/` must be on the same volume as `Music/`. On Windows, `PermissionError` (file open in another app) retries 5 times over ~2 s, then fails with "That file is open in another app; close it and try again", leaving the original untouched.
5. **Supersede, don't destroy.** Upgrades and undos move the old library file to `_Replaced/<same relative path>` first.
6. **Trash, not delete.** Removal from `Music/` goes to the system Trash via `send2trash`. The only outright deletes are regular files and empty folders inside the resolved `_Staging/` (`clean_staging`, `discard_staged`). Symlinks there are removed as links and never followed, and both functions refuse if `_Staging/` itself resolves outside the root.
   - Two clean-ups that hold no data are also removed: an empty name a move reserved (6.4) but didn't fill, and, during undo, folders the undone batch created that are empty again apart from junk (`.DS_Store`, `._*`, `Thumbs.db`, `desktop.ini`).
7. **Verified copies and tag writes.**
   - `copy_in` verifies that the copy's SHA-256 equals the source's before committing.
   - Tag writes hash the decoded audio **fresh** before and after: `ffmpeg -v error -i <file> -map 0:a:0 -f md5 -`. A mismatch rolls back and fails loudly.
   - For a saved video the picture counts too: the picture stream's data is hashed as stored (`-map 0:V:0 -c copy -f md5 -`; decoding it would take minutes), and both hashes must match.
8. **Undo.**
   - Every batch has a `batch_id`. `musicorg undo <batch_id>` first cancels the batch's queued jobs (it refuses while one is running), then reverses its done operations in reverse order, using the journal's before-states.
   - A tag write restores the before-state **exactly**: fields that were absent before are removed.
   - Undo is itself a journaled batch.
   - **Undo refuses, changing nothing, when it couldn't put things back** (2026-10-04), and says which later batch to undo first. Batches that build on each other are undone newest first.
     - A later batch moved or renamed one of the batch's files. Undo finds a file by where the batch left it; without this it would miss the file, or act on another song that has the name now.
     - A file the batch renamed can't go back to its own name because something else has that name now. Without this the file would come back as ` (2)` with its newer tags still on it.
9. **Plans with preconditions.**
   - Each planned operation stores its preconditions: the source's size, mtime and `sha1_head`, the item's state, and the target folder.
   - `apply` **and each job right before it acts** re-check them. A failed check ends the job `needs_review` with "the file changed since the plan was made".
10. **Environment warnings, not blocks.** On `init` and `status`:
    - warn if `<LibraryRoot>` is inside an iCloud Drive folder, because "Optimize Mac Storage" can turn files into placeholders
    - warn if Time Machine has no destination (macOS)
    - warn if free space is under 5 GB
    - warn about long roots on Windows
