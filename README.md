# Music Organizer

A personal music app that turns YouTube Music into a clean, permanent, tagged library of files on your own disk.

This repo holds the **engine** (`engine/`, Python, no UI). It scans, matches, downloads, tags and protects the library. A Mac app arrives in v0.2 and talks to the engine over JSON-RPC.

**Status:** v0.1 in progress, built one step at a time from `prompts/`. See `prompts/README.md` for the order and the roadmap.

## Tools you need

Python 3.12, ffmpeg (with ffprobe), fpcalc (Chromaprint) and deno 2.3 or newer.

**macOS:** try `brew install python@3.12 ffmpeg chromaprint deno` first. If Homebrew isn't installed, or it starts compiling for ages on an Intel Mac, use the direct installers instead:

- **Python 3.12:** the macOS installer from python.org (universal2).
- **deno:** `curl -fsSL https://deno.land/install.sh | sh` (installs to `~/.deno/bin`).
- **fpcalc:** the macOS universal download from the Chromaprint releases page on GitHub.
- **ffmpeg and ffprobe:** a static Intel macOS build, or MacPorts (`sudo port install ffmpeg`).

**Windows:** `winget install ffmpeg`, `winget install DenoLand.Deno`, and fpcalc from the Chromaprint releases page (it isn't on winget).

## Set up and run

`engine/` is created in step 02. From then on:

```bash
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e "engine[dev]"
musicorg doctor                  # checks tools and folders
pytest engine                    # runs the tests
```

## Docs

- `CLAUDE.md`: standing rules for every Claude Code session.
- `docs/LIBRARY_CONTRACT.md`: folder layout, naming, formats, tag schema, safety guarantees.
- `docs/ENGINE_API.md`: enums, CLI commands and the JSON-RPC interface.
- `docs/CHANGELOG.md`: what each step added.
