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
- 185 tests: CLI and entry points, config round trip, tool discovery with fake programs, doctor output, audio fixtures, and the secret check below. `live` marker registered; live tests run only with `MUSICORG_LIVE=1`.
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
