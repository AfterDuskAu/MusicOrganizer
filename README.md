# Music Organizer

A personal music app that turns YouTube Music into a clean, permanent, tagged library of files on your own disk.

This repo holds the **engine** (`engine/`, Python, no UI). It scans, matches, downloads, tags and protects the library. The **Mac app** (`app/`, Swift, v0.2 in progress) plays the library and talks to the engine over JSON-RPC: build and open it with `scripts/build_app.sh --open` (needs Xcode).

**Status:** v0.1 in progress, built one step at a time from `prompts/`. See `prompts/README.md` for the order and the roadmap.

## Tools you need

Python 3.12, ffmpeg (with ffprobe), fpcalc (Chromaprint) and deno 2.3 or newer.

**macOS:** try `brew install python@3.12 ffmpeg chromaprint deno` first. If Homebrew isn't installed, or it starts compiling for ages on an Intel Mac, use the direct installers instead:

- **Python 3.12:** the macOS installer from python.org (universal2).
- **deno:** `curl -fsSL https://deno.land/install.sh | sh` (installs to `~/.deno/bin`).
- **fpcalc:** the macOS universal download from the Chromaprint releases page on GitHub.
- **ffmpeg and ffprobe:** a static Intel macOS build (evermeet.cx, which ffmpeg.org links to), or MacPorts (`sudo port install ffmpeg`). Put `ffmpeg`, `ffprobe` and `fpcalc` in `/usr/local/bin`.

**Windows:** `winget install ffmpeg`, `winget install DenoLand.Deno`, and fpcalc from the Chromaprint releases page (it isn't on winget).

## Set up and run

```bash
git config core.hooksPath .githooks   # once per clone: switches on the secret check
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e "engine[dev]"
musicorg doctor                  # checks tools and folders
pytest engine                    # runs the tests
```

Create the library (not inside the folder that holds your rips):

```bash
musicorg init ~/"Music Organizer Library"
```

## Keeping secrets out

This repository is public. Anything committed is exposed for good, even if a later commit deletes it, and bots scan GitHub for keys within minutes. So:

- `git config core.hooksPath .githooks` switches on the hooks, once per clone. They refuse any commit or push containing something that looks like a key, password, login cookie or personal email address, in files, commit messages or the commit's author details (`scripts/check_secrets.py`). CI runs the same check over every file and the whole history.
- Commit with your private GitHub noreply address: `git config user.email <id>+<username>@users.noreply.github.com`.
- YouTube login files (yt-dlp's `cookies.txt`, ytmusicapi's `browser.json` / `oauth.json`), `.env` files and key files are in `.gitignore`. Keep them outside the repo anyway.
- Never skip the hooks with `--no-verify`. If the check flags something that's genuinely harmless, end that line with a `secrets-ok` comment.

## Docs

- `CLAUDE.md`: standing rules for every Claude Code session.
- `docs/LIBRARY_CONTRACT.md`: folder layout, naming, formats, tag schema, safety guarantees.
- `docs/ENGINE_API.md`: enums, CLI commands and the JSON-RPC interface.
- `docs/CHANGELOG.md`: what each step added.
