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
