# Step 02: Engine skeleton, CLI, tests and CI

## Context
Read `CLAUDE.md` and `docs/ENGINE_API.md` first. This step creates the empty but working engine that every later step builds on.

## Goal
Create a Python 3.12 package `musicorg` in `engine/` with:
- a working CLI, config, logging and a `doctor` command
- test audio fixtures generated at test time
- CI on an **Intel Mac, an Apple Silicon Mac and Windows**

## Build
1. **Environment:**
   - `python3.12 -m venv .venv` at the repo root. Homebrew's Python refuses global pip installs.
   - Document activating it in `README.md`.
   - `engine/pyproject.toml` (hatchling or setuptools), console script `musicorg = musicorg.cli:main`.
   - Dependencies exactly as in `CLAUDE.md`. Dev extras: `pytest`, `ruff`.
2. **Package:** `musicorg/__init__.py` (`__version__ = "0.1.0.dev0"`), plus `cli.py`, `config.py`, `logging_setup.py`, `tools.py`, `errors.py`.
3. **CLI** (`argparse`), following `docs/ENGINE_API.md`:
   - Implement `--version`, `doctor` and `status` for real.
   - Every other command prints "not implemented yet (step NN)" and exits 1.
   - Global options `--library`, `--json`, `--verbose`, and the documented exit codes.
4. **`config.py`:**
   - platformdirs config, log and cache folders.
   - `config.json` holds the last library root, tool path overrides, and throttle settings (defaults added in step 09a).
   - Written atomically. Unknown keys are preserved.
5. **`tools.py`:**
   - Locate `ffmpeg`, `ffprobe`, `fpcalc` and `deno`. Order:
     1. a config override path
     2. `PATH`
     3. the common install folders: `/usr/local/bin`, `/opt/homebrew/bin`, `/opt/local/bin`, `~/.deno/bin`, and on Windows `%LOCALAPPDATA%\Microsoft\WinGet\Links` and `C:\ProgramData\chocolatey\bin`
     
     A GUI app won't inherit your shell's PATH later, so the fallback folders matter.
   - Report versions. `deno` must be ≥ 2.3; older counts as missing.
   - `require(tool)` raises `ToolMissingError` with a plain-English message and install hints:
     - **macOS:** try Homebrew, else the direct downloads listed in `README.md`
     - **Windows:** `winget install ffmpeg`, `winget install DenoLand.Deno`, and fpcalc from the Chromaprint releases page (not on winget)
6. **`doctor`:** checks tools (with versions), `yt-dlp` / `yt-dlp-ejs` / `ytmusicapi` versions, Python version, and writable config and log folders. Prints a ✓/✗ checklist with a fix for every ✗.
7. **Logging:** rotating file (5 × 2 MB) in the log folder, plus stderr. Nothing goes to stdout except CLI output.
8. **Test fixtures** (`engine/tests/conftest.py`). Session-scoped, generated with ffmpeg into a temp folder:
   - **Melody A:** 20 s, a fixed sequence of tones changing every 0.5 s (ffmpeg `aevalsrc` with a piecewise expression), as M4A (AAC) and MP3.
   - **Melody B:** 20 s, a different sequence using *different pitch classes* (not octaves of A), as M4A.
   - **Noise:** 20 s pink noise, `anoisesrc=color=pink:seed=42`, as M4A.
   - **Noise + silence:** the *same* noise file with 2 s of silence prepended, made by **concatenating** (not regenerating).
   
   If `MUSICORG_REQUIRE_TOOLS=1` and ffmpeg is missing, **fail**. Otherwise skip audio tests with a clear message.
9. **Tests:** CLI entry point, config round-trip, tool discovery (fake PATH and fallback folders, deno version check), doctor output. Register the `live` marker, skipped by default.
10. **CI (`.github/workflows/engine.yml`):**
    - Matrix: `macos-15-intel` (your iMac's architecture), `macos-latest` (Apple Silicon) and `windows-latest`. Check the Intel runner label is still current when you write this.
    - Trigger on pushes to `main` and on pull requests only, with `concurrency: { group: ci-${{ github.ref }}, cancel-in-progress: true }`.
    - Set `MUSICORG_REQUIRE_TOOLS=1`.
    - macOS: `brew install ffmpeg chromaprint deno`.
    - Windows: `choco install ffmpeg -y --no-progress`, Deno via its official installer or winget, and fpcalc from the official `chromaprint-fpcalc-<version>-windows-x86_64.zip` release, added to `$GITHUB_PATH`.
    - Steps: `ruff check`, then `pytest`.
11. **`docs/CHANGELOG.md`:** the first entry.

## Rules
- No business logic yet. Stubs only.
- Windows-safe paths from the start (`pathlib`, `tempfile`, no hard-coded `/tmp`).

## Acceptance
- `pip install -e "engine[dev]"` works in the fresh `.venv`.
- `musicorg --version`, `musicorg doctor` and `musicorg status` run, and doctor correctly reports each tool as present or missing on the iMac.
- `pytest` passes locally, and a push to `main` passes CI on all three runners, **with audio tests actually running** (check that they're not skipped).
- `ruff check` is clean.
