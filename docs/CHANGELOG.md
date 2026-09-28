# Changelog

## 0.1.0 — in progress

### Step 01: Project setup

- Created the repo: `CLAUDE.md`, `README.md`, `docs/`, `prompts/`, `.gitignore`.
- Copied `CLAUDE.md`, `docs/LIBRARY_CONTRACT.md`, `docs/ENGINE_API.md` and the prompt files from the v0.1 brief.
- Deviations from the step 01 prompt:
  - The brief's own README (step order, roadmap, tool install notes) is kept as `prompts/README.md`, so it's versioned with the prompts.
  - `.gitignore` also ignores `*.tsv` (so the `engine/tests/data/*.tsv` exception means something), SQLite side files (`*.sqlite-wal`, `-shm`, `-journal`), and `._*`, `Thumbs.db`, `desktop.ini`, which `CLAUDE.md` says to ignore everywhere.

### Step 02: Engine skeleton, CLI, tests and CI

- `engine/`: Python 3.12 package `musicorg` (hatchling), console script `musicorg`, dependencies exactly as in `CLAUDE.md`, dev extras `pytest` and `ruff`.
- Modules: `cli`, `config`, `tools`, `errors`, `logging_setup`, `doctor`, `status`, plus `__main__` for `python -m musicorg`.
- CLI: `--version`, `doctor` and `status` work. Every other command in `ENGINE_API.md` parses its real arguments, then prints "not implemented yet (step NN)" and exits 1. `--library`, `--json` and `--verbose` work before or after the command name. Exit codes follow `ENGINE_API.md`; unexpected errors exit 10 and print the log path.
- `config.py`: platformdirs folders, `config.json` with `last_library`, `tools` path overrides and an empty `throttle` section (filled in by step 09a). Atomic writes (temp file, fsync, rename); unknown keys are kept; a damaged file gives a plain-English error.
- `tools.py`: finds ffmpeg, ffprobe, fpcalc and deno via config override → `PATH` → common install folders. A copy that won't run, or a deno older than 2.3, counts as missing, and the search moves on to the next copy. `require()` raises `ToolMissingError` with install hints for macOS and Windows.
- `doctor`: ✓/✗ checklist for Python, the four tools, yt-dlp, yt-dlp-ejs and ytmusicapi, the settings file, and the config and log folders, with a fix under every ✗.
- Logging: rotating `musicorg.log` (the current file plus 4 old ones, 2 MB each) in the log folder, plus stderr. Nothing goes to stdout except command output. Tracebacks go to the log file only, unless the log file can't be written.
- Test fixtures generated with ffmpeg at test time: melody A (M4A and MP3), melody B (different pitch classes), pink noise, and the same noise with 2 s of silence prepended by concatenation. A manual check with fpcalc: A as M4A vs MP3 differ by 0% of fingerprint bits, A vs B by 41%, A vs noise by 57%.
- 186 tests: CLI and entry points, config round trip, tool discovery with fake programs, doctor output, audio fixtures, and the secret check below. `live` marker registered; live tests run only with `MUSICORG_LIVE=1`.
- CI (`.github/workflows/engine.yml`): `macos-15-intel`, `macos-latest` and `windows-latest`, on pushes to `main` and on pull requests, with `MUSICORG_REQUIRE_TOOLS=1`. Runs `ruff check`, `musicorg doctor`, then `pytest`. `macos-15-intel` is still current; GitHub keeps it until August 2027 as its last Intel image.
- Versions installed on the iMac: Python 3.12.10, ffmpeg/ffprobe 9.0.2 (evermeet.cx static build), fpcalc 1.6.1, deno 2.9.7, yt-dlp 2026.8.19, yt-dlp-ejs 0.8.0, ytmusicapi 1.12.3.
- Deviations and additions:
  - Homebrew's installer failed on the iMac, so the tools came from the README's direct installers. Python 3.12.10 is the last 3.12 release with a python.org Mac installer; later 3.12 releases are source only.
  - `doctor.py` and `status.py` hold those commands' logic, so `cli.py` stays argument parsing and printing only. Neither module is in `CLAUDE.md`'s module list.
  - argparse's own exit code 2 for bad arguments became 1, because 2 means "library locked".
  - With `--json`, errors are printed to stdout as `{"ok": false, "error": {"exit_code", "message"}}`, so a script reading stdout always gets JSON. Without `--json` they go to stderr.
  - `doctor` exits 0 when everything passes, 3 if a tool is missing or unusable, and otherwise 1 on any failed check.
  - `status --json` reports `library_exists` and `is_library` as booleans, rather than inventing a library-state enum.
  - `MUSICORG_HOME` puts the config, log and cache folders under one folder. Every test uses it, so tests never touch the real settings.
  - Besides `config.json`, `config.py` creates the app's own config, log and cache folders and runs doctor's write check there. Step 03a's write-rule test should allow that; it's rule 2's "engine-owned exceptions".
  - CLI output is switched to UTF-8 when it isn't already, so ✓/✗ and non-English names don't crash on a Windows pipe.
  - Windows CI installs deno with `denoland/setup-deno`, Deno's official GitHub Action, rather than winget. fpcalc comes from the official `chromaprint-fpcalc-1.6.1-windows-x86_64.zip`.
- Added at the owner's request (not in the step 02 prompt): a secret check, because the repo is public.
  - `scripts/check_secrets.py` (standard library only, Python 3.9+) flags keys, tokens, private keys, passwords in code or URLs, Google/YouTube login cookies, personal email addresses, and file names like `cookies.txt`, `browser.json`, `oauth.json` and `.env`. Findings are printed mostly hidden. Allowed emails: GitHub noreply addresses, no-reply senders and example/test domains.
  - `.githooks/pre-commit` checks staged changes and the email git will stamp on the commit. `.githooks/commit-msg` checks the message. `.githooks/pre-push` checks every commit being pushed (files, message, author and committer email), including secrets added and then deleted again. Switched on per clone with `git config core.hooksPath .githooks`.
  - CI job `secret check` scans every tracked file and the full history. `.gitignore` excludes the same secret file names.
  - A new "Secrets" section in `CLAUDE.md` and "Keeping secrets out" in `README.md`. Tests in `engine/tests/test_check_secrets.py`, so they run on all three CI machines.
  - A root `ruff.toml` lints `scripts/` with the engine's rules, targeting Python 3.9.
  - The check always writes UTF-8 and hides values with a plain `...`. The first Windows CI run failed because Windows pipes default to the old ANSI code page, which can't carry `…`.

### Step 03a: Library core (naming, init/open, lock, state)

- `naming.py`: the layout (`LibraryPaths`), `safe_component()`, `library_path()`, `candidate_names()`, plus `is_junk()` (`.DS_Store`, `._*`, `Thumbs.db`, `desktop.ini`) and `is_audio_name()`.
- `library.py`: `init()`, `open()`, `is_library()` and `environment_warnings()`.
- `fileops.py` starts here: creating the layout, the single-writer lock, and `recover_journal()`, an empty hook that step 03b fills in. Only fileops may write `lock` and `lock.info` (rule 3), and contract section 6 puts the lock under fileops.
- `state.py`: `.musicorg/state.json`, with a schema version, unknown keys kept, atomic saves (temp file, fsync (`F_FULLFSYNC` on macOS), rename), and `source_id()`.
- CLI: `musicorg init <root>` prints the layout and any warnings (`--json` too). `status` now shows warnings for a library. A locked library exits with code 2 and names the holder.
- `tests/test_write_rules.py` parses every module with `ast`. It passes on the engine and fails on a planted `Path.write_text` in a copy of the package.
- 430 tests (429 pass on the Mac; one Windows-only case test is skipped): a 45-case naming table plus limit, Unicode and Windows-budget tests; init and its refusals; the lock across two real processes (an in-process holder, and `musicorg init` as the second process, exit 2); a crash mid-save of `state.json` in a child process; warnings with a fake home folder.
- Acceptance, run on the iMac: `musicorg init ~/Music\ Organizer\ Library` created the layout and warned that Time Machine has no backup disk. Running it again changed nothing, and `musicorg status` found the library without `--library`. Also checked by hand: a folder holding an `.mp3` is refused (exit 1) and left untouched; a second writer gets exit 2 with "Another Music Organizer process (PID …, `queue run`, started 05:36) is using this library"; a folder inside a library is refused.
- Deviations and additions:
  - **Naming**
    - Reserved names also cover `COM0`, `LPT0`, `COM¹`–`COM³`, `LPT¹`–`LPT³`, `CONIN$` and `CONOUT$`, which Windows refuses too. The `_` goes after the base name, before any extension (`CON.m4a` → `CON_.m4a`), because Windows ignores what follows the first dot.
    - A leading `.` becomes `_` before trailing dots are trimmed, so `...` becomes `_` rather than nothing. Trailing dots are trimmed per component, so a title's own dots stay before the extension (`03 Wait for it....m4a`).
    - Lone surrogates (undecodable bytes in a rip's file name) become `_`, like control characters.
    - Cuts never split an accent, an emoji sequence or a flag. When that would leave nothing (e.g. one letter with hundreds of accents), the cut is made at the limit anyway.
    - Unknown album: `<Artist>/Unsorted/<Title>.<ext>` uses the same artist folder as the main template (album artist, then artist, then `Unknown Artist`). There's no track number or year, and the compilation flag is ignored without an album. No title and no source file: `Unknown Title`.
    - Tag-style numbers are accepted: track `"5/12"`, disc `"2/3"` (which also marks the album multi-disc), year `"2019-05-03"`.
    - The Windows path budget applies when the engine runs on Windows (`platform` parameter for tests). It is counted in UTF-16 units, as Windows counts (an emoji is 2). It keeps 5 characters free for a ` (99)` collision suffix, and it covers `cover.jpg` in the same folder. If cutting the title isn't enough, the album and then the artist are cut too, none below 10 characters. If even that can't fit, `PathTooLongError` asks for a shorter library folder.
    - `candidate_names()` stops after ` (1000)`, so a bug can't loop forever.
  - **init**
    - Also refuses the home folder or a drive's root folder, a folder inside another library, a root whose parent folder doesn't exist (probably a disconnected drive; creating it would put the library on the wrong disk), and a file sitting where a layout folder should go.
    - It searches for music breadth first, without following folder links, and gives up after 100,000 entries with "already holds a great many files".
    - On an existing library `init` is harmless: it takes the lock (exit 2 if another process holds it), recreates missing folders and never rewrites `state.json`. It also creates `_Staging/calibration/`, `.musicorg/journal/`, `plans/` and `undo-art/` from the contract's layout.
    - `init` saves the library as the default (`last_library` in `config.json`), so later commands don't need `--library`.
  - **Library, lock and state**
    - A folder is a library if it has `.musicorg/`. A missing `state.json` then loads as defaults, rather than making the library look like a new folder, which `init` would refuse because `Music/` holds audio.
    - `lock.info` is JSON (`pid`, `command`, `started_at` in UTC, `host`, `engine_version`). It's removed on a clean release, before unlocking. The message shows the start time in local time (with the date if it isn't today), and the computer's name only if it's a different computer. `LibraryLockedError.holder` carries the details for the RPC error in step 11.
    - `state.json` also gets `created_at`. Keys are saved sorted. A file from a newer engine (higher schema) is refused with a plain-English message. A damaged one raises `StateError` and points to a backup.
    - Other modules change state through `state.edit()` / `state.create_if_missing()`, and set the default library with `config.remember_library()`, because the write-rule test flags any other module's `.save()` call.
    - `source_id()` normalises the path: `~` expanded, absolute, symlinks resolved, NFC, and `os.path.normcase` (lower-case on Windows, unchanged on macOS).
  - **Environment warnings**
    - Also warns for `~/Library/CloudStorage` (Dropbox, OneDrive, Google Drive on macOS) and OneDrive on Windows, which make the same kind of placeholder files as iCloud.
    - iCloud Desktop & Documents counts as on when `~/Library/Mobile Documents/com~apple~CloudDocs/Desktop` (or `Documents`) exists. This is best effort.
    - Time Machine is reported missing only when `tmutil destinationinfo` says "No destinations configured". Anything else, including tmutil failing, gives no warning. Tests never run tmutil.
  - **Write-rule test**
    - It flags more than the prompt's list: `os.fdopen`/`codecs`/`gzip`/`bz2`/`lzma` opens with a write mode, `os.open` with write flags, an `open()` whose mode isn't a plain string, `os.renames`/`removedirs`/`link`/`symlink`/`truncate`, `tempfile` file and folder creators, `Path.symlink_to`/`hardlink_to`, and any `send2trash` import.
    - `tags.py` may make only one `.save()` call, per "its single mutagen save call". Calls into musicorg's own modules aren't flagged; the writing code is checked where it lives.
    - Logging's `RotatingFileHandler` isn't flagged: logs are one of rule 2's engine-owned exceptions, and `logging_setup.py` only creates the log folder through `config`.

### Step 03b: fileops (guarded operations, journal, recovery, undo, plans)

- `fileops.py` now holds the whole safety core of contract section 6:
  - `guard(paths, path, allow)`: resolves links, then the path must be strictly inside `Music/`, `_Replaced/`, `_Staging/` or `.musicorg/` (or the ones asked for). A managed folder itself doesn't count, so nothing can replace or remove `Music/`. Case matters except on Windows, so a differently-cased path is refused rather than guessed at.
  - `_move_no_overwrite`: every move. Reserve the first free name with `open(name, "xb")`, journal it, then `os.replace`. Names are compared ignoring case and Unicode form, so `Song.m4a` and `song.M4A` collide on any drive. EXDEV → `CrossVolumeError`; on Windows a `PermissionError` gets 5 more tries 0.4 s apart, then `FileInUseError`. On any error the reserved name and any new folders are removed and the file stays where it was.
  - Operations, each journaled as `intent` (fsynced) → act → `done`: `stage_path`, `commit`, `copy_in`, `supersede`, `move`, `trash`, `write_tags` (journal and undo side only) and `write_sidecar`. `copy_in` compares the SHA-256 of what it read with the copy read back, and checks the original's size and modification time before committing and again after.
  - Journal: `.musicorg/journal/YYYY-MM-DD.jsonl` (UTC date) with `batch_start`, `intent`, `reserved`, `done`, `failed`, `recovered` and `batch_end` lines. Library paths are stored relative to the root. F_FULLFSYNC on macOS for intents, reserved names, batch starts and ends, and copied files.
  - Batches: `with fileops.batch(lib, kind) as b`, or open batches for step 09b (`open_batch`, `resume_batch`, `close_batch`), which recovery never closes.
  - Recovery (the step 03a hook in `library.open`): each `intent` without `done` is completed or rolled back from what's on disk, journaled as `recovered` and logged. Batches that never ended are closed as `interrupted`.
  - `undo(lib, batch_id, dry_run, jobs)`, itself a batch of kind `undo`.
  - Plans: `Plan`, `PlanOp`, `FileCheck`, `FolderCheck`, `new_plan`, `save_plan`, `load_plan`, `validate`, `check_op`, `sha1_head`.
  - `discard_staged`, `clean_staging` and `write_export`.
- CLI: `musicorg journal list [--limit N]` (no lock) and `musicorg undo <batch_id> [--dry-run]` (lock), both with `--json`.
- `scripts/fileops_demo.py <root> <files…>`: copies files into a library in one batch and prints the batch id.
- 589 tests pass (2 skipped: the real-Trash test and an existing Windows-only one). Every operation is tested for success, a crash before acting, a crash between acting and `done`, a name collision, undo, and refusing a file outside the library. Also: links out of `Music/` refused as source and destination; a file that appears right after the name check is never overwritten; `clean_staging` and `discard_staged` never follow links, and refuse a `_Staging/` that leads elsewhere; a real process killed mid-move (`os._exit`) and recovered by the next one; the manual check below, run end to end.
- Manual check, run on the iMac with a scratch library and three generated files (`Melody A.m4a`, `Melody A.mp3`, `Pink Noise.m4a`), using a scratch settings folder so the default library didn't change:
  1. `musicorg init` created the scratch library (with the usual Time Machine warning).
  2. `python scripts/fileops_demo.py <root> <3 files>` printed `b_20260928-202402-e5df5b` and copied the files into `Music/Unknown Artist/Unsorted/`.
  3. `musicorg journal list` showed it: `demo  closed  3 copied in`.
  4. `musicorg undo b_20260928-202402-e5df5b --dry-run` listed three moves to `_Replaced/` and changed nothing; `musicorg undo b_20260928-202402-e5df5b` did them as batch `b_20260928-202404-742e94`.
  5. `Music/` was empty (the `Unknown Artist/Unsorted/` folders the batch made were removed too), `_Replaced/Unknown Artist/Unsorted/` held the three files with the originals' SHA-256, and the originals' SHA-256, sizes and modification times were unchanged. `journal list` then showed the batch as undone by the undo batch.
  - The real-Trash test (`MUSICORG_INTEGRATION=1 pytest -k real_trash`) passed once on the iMac. It leaves one file, `trash-test (safe to delete).txt`, in the Trash.
- Deviations and additions:
  - **Signatures:** the functions take the library (a `Library` or its `LibraryPaths`) first, which the prompt's signatures leave out: `guard(paths, path)`, `discard_staged(lib, path)`, `clean_staging(lib)`, `write_export(lib, path, data, sources=...)`, `validate(lib, plan)`, `check_op(lib, op)`. `write_export` takes the registered sources as a required `sources=` argument until step 05 stores them.
  - **Lock:** every operation, batch, plan save and staging clean-up refuses to run unless this process holds the library's lock. `write_export` and reading the journal or a plan need no lock.
  - **Reserved names:** the intent is journaled first (with the name expected to be free and the folders to be created), then the reserved name in its own fsynced `reserved` line, then the move. So a crash between reserving and journaling still leaves recovery something to find. Recovery only removes a reserved name that's empty and newer than its intent.
  - **Deletes outside `_Staging/`** (contract 6.6 now says so): an empty reserved name that was never filled, and, during undo, folders the undone batch created that are empty again apart from junk (which goes with them). Without the second, undo would leave empty `Artist/Album` folders behind, and `Music/` wouldn't be back as it was.
  - **`stage_path`** isn't journaled: it only creates `_Staging/<batch_id>/` and picks a free name there, and staging is scratch space.
  - **`write_tags`:** there's no tag reader until step 04, so `fileops.tag_access` (read and write a file's tags as a JSON-safe dict, the cover as its SHA-256) is the hook step 04 fills. Until then `write_tags` says "not implemented yet (step 04)", and recovery leaves an interrupted tag write for later. Undo restores the fields the batch changed that still have the batch's values; fields changed again later are left alone and named. With no later changes that's exactly the before-state, added fields removed. No collision test: a tag write doesn't create a name.
  - **`write_sidecar`:** `suffix` is `.lrc` (or another short extension) for `<track>.lrc`, or `cover.jpg` for the folder's cover. Returns the sidecar's path, or None when a different `cover.jpg` was left alone. Superseding a different `.lrc` is its own journaled `supersede`, so undo reverses the two in order.
  - **Undo:** it stops at the first problem and skips operations an earlier undo already reversed, so it can simply be run again. Undoing an undo re-applies the batch. A file that's no longer where the batch left it is skipped and reported. Undo closes an open batch. The queue hook is a `BatchJobs` object (`cancel_queued`, `running`) that step 09a provides: queued jobs are cancelled first, then undo refuses while one is running. A dry run cancels nothing.
  - **Plan preconditions:** `FileCheck` (size, modification time in nanoseconds, and `sha1_head`, the SHA-1 of the first 1 MiB), the item's state through a lookup function (no lookup means the check fails, not that it passes), and `FolderCheck` for the target folder: whether it existed, and its entries apart from junk. A plan fails if that folder has become a file, or if something new has the target's name (ignoring case). Other new files there are fine, e.g. an album's earlier tracks committed by the same batch.
  - **`copy_in`** refuses a source inside the library. If the original changes after its verified copy was committed, that's logged as an error instead of failing the operation.
  - **`move`** onto the file itself does nothing. Case-only renames aren't supported yet.
  - `journal list` has `--limit N` (default 20), since RPC `journal.batches` takes a limit.
  - `ENGINE_API.md` → Enums gained batch kind, batch status, journal operation and undo step status.
  - New errors: `OutsideLibraryError`, `CrossVolumeError`, `FileInUseError`, `FileOperationError`, `SourceChangedError`, `IntegrityError` (exit 1), `NotFoundError` and `UndoError`.
  - **Tests:** every test gets a fake Trash. The real-Trash test is marked `integration` and runs only with `MUSICORG_INTEGRATION=1`, never in CI, so a plain `pytest` never puts files in the owner's Trash. In tests, fsync only checks that its file is open and F_FULLFSYNC is skipped: on the iMac's spinning disk F_FULLFSYNC took 0.14–0.25 s and fsync about 10 ms, which made the suite several times slower. One test checks that macOS uses F_FULLFSYNC, and another that the intent is flushed before anything changes.
  - `fileops.py` is about 2,300 lines. It stays one module, as rule 3 and the write-rule test expect, with a section per topic.
- CI: the first push with step 03b (together with step 04) passed every step 03b test on all three runners. Only step 04's Ogg sample failed, on the Macs; see step 04.

### Step 04: Tags, probe and the audio-integrity check

- `tags.py` (mutagen 1.48.1):
  - `TrackTags`: every field of contract section 4, standard and `MUSICORG_*`, plus `cover` and `cover_mime`. None means absent (in a change: leave it); `REMOVE` deletes a field; `warnings` says what went wrong while reading.
  - `read_tags`: M4A, MP3, FLAC, Ogg Vorbis and Opus. WebM, raw AAC, WAV and anything else give empty tags and a warning, and never raise. Messy MP3s read without errors: ID3v1 only, ID3v2.2, duplicate frames, broken frames, UTF-8 text in a frame that says it's Latin-1 (repaired, with a warning), and files that aren't MP3 at all.
  - `write_tags`: changes only the fields it's given and keeps the rest (iTunSMPB and other freeform atoms, composer, comments, other TXXX frames, unknown frames in v2.3 files, back covers and other pictures). M4A provenance goes in `----:com.apple.iTunes:MUSICORG_*` atoms as UTF-8; MP3 is ID3v2.3 with the year in TYER and provenance in TXXX frames; FLAC, Ogg and Opus get plain comments and a front-cover picture block. `MUSICORG_VERSION` is joined with `; `. One mutagen save call.
  - `probe()` (ffprobe): codec, duration, kbps, sample rate and channels. `audio_hash()` (ffmpeg): MD5 of the decoded audio, always computed fresh. No cache yet; the prompt allows one only for scan-time reporting, so step 05 can add it.
  - `new_track_id()`: a UUIDv4.
- `fileops.write_tags` is now the verified write (contract 6.7): hash the audio → copy the file into `_Staging/` → write the tags on the copy → hash the copy and read its tags back → journal the intent (complete before- and after-state, the audio MD5, the staged copy) → keep the old cover in `.musicorg/undo-art/` → swap the copy in with `os.replace` (with the Windows retry) → `done`. If the audio or the tags don't match: `IntegrityError`, the copy is discarded and the file is untouched.
  - Recovery reads the file's tags: the new ones mean the swap happened (completed); the old ones mean it didn't (rolled back, the staged copy removed).
  - Undo works from the journaled before-state: fields that were absent are removed, including a `MUSICORG_ID` the batch added, and a replaced cover comes back from `undo-art/`.
  - A file's `MUSICORG_ID` can't be changed or removed, except by the undo of the batch that added it.
- `scripts/check_compat.py [<folder>]`: a scratch library with a 10-second tone as M4A and MP3, tagged with every field through the real verified write, and a checklist for Apple Music and Kid3 or Picard.
- 678 tests pass on the iMac; 3 are skipped (the real-Trash test, and two Windows-only ones). 97 are new, and step 03b's 7 tests with a fake tag store were replaced by real ones. Every field round-trips in all five formats with the audio hash unchanged; Unicode (Japanese, emoji, accents); covers in JPEG and PNG; unmanaged atoms, frames and comments survive; the messy MP3s above; files whose tags aren't read; values outside the schema refused; probe of all eight sample kinds; adding lyrics and a cover then undoing them leaves neither and the same audio; a write that changes the audio or doesn't read back is refused with the file untouched; a crash on either side of the swap is recovered; the track id rule; and, on the Windows runner only, a file held open by another handle gives `FileInUseError` with the original intact.
- Compatibility check (`scripts/check_compat.py`, run on the iMac):
  - ffprobe reads every field of both files, the `MUSICORG_*` ones included, and the 600 px cover.
  - macOS's own reader (`afinfo`, AudioToolbox) reads the title with its Japanese, the artist, album, year, genre and track number from both the M4A and the ID3v2.3 MP3. It doesn't report artwork or lyrics.
  - **Still to do by the owner:** Apple Music (artwork, lyrics, the explicit mark) and Kid3 or Picard (the `MUSICORG_*` fields), following the script's checklist. Neither app can be checked from the command line.
- Deviations and additions:
  - **Types:** the year, track and disc numbers are whole numbers, and the year is written as 4 digits (a full date in a rip reads as its year, and stays in the file unless the year is changed). `explicit` is True/False. `match_score` keeps 3 decimals. `only_copy=True` writes `1`; False removes the field, since the contract only defines `1`. `version` is a list of tokens.
  - **Empty text** counts as "not given", so it leaves a field as it is rather than erasing it; `REMOVE` erases.
  - **Checked values:** `MUSICORG_SOURCE` and `MUSICORG_MATCH` must be enum values, numbers must be in range, and the cover must be JPEG or PNG; anything else is a ValueError before a file is touched.
  - **Several values in one field** (two ARTIST comments, duplicate TIT2 frames) read joined with `; `. Writing that field stores one value.
  - **Covers:** the managed cover is the front cover: APIC type 3, picture type 3 in FLAC, Ogg and Opus, and M4A's `covr` (all of it: M4A has no picture types). Other pictures are kept.
  - **Lyrics in MP3:** all USLT frames count as the lyrics; a write replaces them with one (language `eng`).
  - **Totals:** a track or disc total isn't kept when its number is removed, because ID3 can't store a total alone.
  - **Vorbis aliases:** TOTALTRACKS, TOTALDISCS and UNSYNCEDLYRICS are read when the usual names are missing, and replaced by the usual names on write.
  - **Explicit:** `rtng` 1 is explicit and 0 not; iTunes' 4 also reads as explicit and 2 ("clean") as not. MP3 and Vorbis use `1`/`0`. The contract's table now says so.
  - **ID3v2.3:** mutagen converts only when `update_to_v23()` is called first; saving with `v2_version=3` alone kept the v2.4 TDRC frame. The conversion deletes v2.4-only frames, so the ones players also read in v2.3 (sort names TSOP, TSOA, TSOT, TSST; TMOO; TPRO; RVA2 ReplayGain) are carried across, as mutagen's documentation suggests. Other v2.4-only frames (e.g. TDRL, TDTG, SIGN) are dropped, and frames mutagen doesn't know can't be kept from a v2.4 file (logged). Only the library's copies are ever written, never the owner's rips.
  - **undo-art:** a PNG cover is kept as `<sha256>.png` (contract section 1 updated).
  - **The verified write also** reads the tags back from the copy and compares them with what was asked, and checks the file's size and modification time just before the swap (`SourceChangedError` if another app changed it).
  - **Journal order:** a tag write's intent is journaled once the staged copy has passed both checks, just before the swap; the library file isn't touched before then. A crash earlier leaves only the staged copy, which `clean_staging` removes.
  - **Undo** keeps step 03b's rule: a field changed again by a later batch is left as it is and named.
  - `REMOVE` moved from `fileops` to `tags`, and step 03b's `tag_access` placeholder is gone.
  - **Recovery of a move** now also compares the reserved file's size with the size in its intent, so an empty reservation whose source vanished isn't taken for a finished move.
  - New `samples` test fixture: 3 seconds of melody A as M4A, MP3, FLAC, Opus, Ogg Vorbis, WebM, WAV and raw AAC, plus an MP3 with no ID3 tag.
  - Homebrew's ffmpeg (the CI Macs) has no libvorbis, so the first CI run couldn't make the Ogg sample. The fixture now uses libvorbis when it's there and ffmpeg's own Vorbis encoder otherwise (marked experimental, stereo only), and the Ogg sample is stereo either way. Both paths were run on the iMac.
  - New error `AudioError`. `ENGINE_API.md`: batch kind `demo` now covers both manual-check scripts.
