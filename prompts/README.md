# Music Organizer: v0.1 engine brief

This folder is a step-by-step build brief for Claude Code. It builds version 0.1: the **engine**, which has no UI and scans, matches, downloads, tags and protects the library. The Mac app is v0.2 and talks to this engine.

## What v0.1 has to prove

One number decides the rest of the project:

> Of your existing YouTube-to-MP3 library, what fraction can be **replaced** with a clean official YouTube Music download, what fraction needs **review**, and what fraction is **only-copy** (not on YouTube Music at all)?

v0.1 is done when the engine can:

1. Index your existing rip folders **without touching them**.
2. Match every rip against YouTube Music and produce that report.
3. Replace the safe matches with official downloads. Files are checked by audio fingerprint, tagged, given lyrics and artwork, and filed into a new managed library.
4. Copy only-copy rips into the library with fixed tags, leaving the originals untouched.
5. Undo any batch of changes.

## How to use this brief

1. Run `prompts/01-project-setup.md` first. It creates the project in `~/Developer/` and copies `CLAUDE.md`, `docs/` and `prompts/` into it.
2. Run the rest **in order**, one per Claude Code session. Open Claude Code in the project and say e.g. "do prompts/03a-library-core.md".
3. Don't start the next step until the current one's **Acceptance** section passes. Every step ends with tests.
4. **After step 07, stop and bring the report back to Claude chat.** If most of your library turns out to be only-copy, the *adopt* half of step 09b matters more than the *replace* half, and the order may change.

| Step | File | What you get |
|---|---|---|
| 01 | `prompts/01-project-setup.md` | Repo, folder layout, GitHub remote |
| 02 | `prompts/02-engine-skeleton.md` | Python project, CLI, `doctor`, generated test audio, CI on Intel Mac, Apple Silicon Mac and Windows |
| 03a | `prompts/03a-library-core.md` | Naming rules, library init/open, the single-writer lock, `state.json`, the "no writes outside fileops" test |
| 03b | `prompts/03b-fileops.md` | The only module allowed to write files: guarded operations, no-overwrite moves, journal, crash recovery, undo, plans |
| 04 | `prompts/04-tags-and-integrity.md` | Tag schema with provenance, and the audio-integrity check on every tag write |
| 05 | `prompts/05-scan-existing-library.md` | Read-only index of your rips, and the messy-filename parser |
| 06 | `prompts/06-matcher.md` | YouTube Music search and scoring, zero false AUTOs |
| 07 | `prompts/07-measurement-report.md` | **The v0.1 decision report, plus the review CSV.** Stop here. |
| 08 | `prompts/08-fingerprint-gate.md` | Audio fingerprint comparison (Chromaprint) |
| 09a | `prompts/09a-queue-and-downloader.md` | Throttled download queue, YouTube block handling, downloader (format 140 only) |
| 09b | `prompts/09b-replace-and-adopt.md` | Replace and adopt pipeline, fingerprint calibration, first real batch |
| 09c | `prompts/09c-keep-your-own-audio.md` | The owner's own audio with official details, no downloads (added 2026-09-30) |
| 10 | `prompts/10-lyrics-and-artwork.md` | LRCLIB synced lyrics, square cover art |
| 11 | `prompts/11-rpc-server.md` | The interface the Mac app uses from v0.2, then tag `v0.1.0` |

Reference docs Claude Code must follow:

- `CLAUDE.md`: standing rules, loaded automatically by Claude Code.
- `docs/LIBRARY_CONTRACT.md`: folder layout, naming, formats, tag schema, safety guarantees.
- `docs/ENGINE_API.md`: every allowed state and value ("Enums"), the CLI commands and the JSON-RPC interface.

## Before you start: tools on an Intel iMac

The engine needs **Python 3.12, ffmpeg (with ffprobe), fpcalc (Chromaprint) and deno 2.3+**.

**Homebrew recently moved Intel Macs to its lowest support tier** (Homebrew 7.0.0, September 2026). Existing packages still install, but updated ones may compile from source, which is slow and can fail, and active support ends around September 2027. So:

1. Try `brew install python@3.12 ffmpeg chromaprint deno`. If it installs pre-built packages quickly, you're done.
2. If it starts compiling for ages or fails, stop it and use direct installers instead:
   - **Python 3.12:** the macOS installer from python.org (universal2).
   - **deno:** `curl -fsSL https://deno.land/install.sh | sh` (installs to `~/.deno/bin`).
   - **fpcalc:** the macOS universal download from the Chromaprint releases page on GitHub.
   - **ffmpeg and ffprobe:** a static Intel macOS build, or MacPorts (`sudo port install ffmpeg`), which Homebrew itself suggests for Intel Macs.
3. Run `musicorg doctor` (step 02) to confirm everything is found. Tool paths can be set in the config if they're somewhere unusual.

## Your hardware, and what it means

- **Windows:** an Intel iMac runs Windows 11 properly, in VMware Fusion (free) or Boot Camp. So you *can* test the Windows side yourself. CI also runs every test on Windows from step 02.
- **Apple Silicon friends:** they need an arm64 build. Release builds will come from CI (arm64 and x86_64) in v0.5, not from your iMac alone. Apple is winding down Rosetta, so an Intel-only build won't keep working on newer Macs.
- **Library location:** put the new library somewhere like `~/Music Organizer Library` or an external drive, **not inside the folder holding your rips**. The engine refuses that setup anyway. Reports and CSVs go to `<Library>/Reports/`.

## Roadmap after v0.1

| Version | What |
|---|---|
| **0.1** | Engine on your own library (this brief) |
| 0.2 | Mac app (SwiftUI): library view, review queue with A/B listen and "Other → paste link", mini and full player, synced lyrics and karaoke view, search box that plays anything |
| 0.3 | Imports for friends: Spotify data export, Exportify CSV, Apple Music library XML, YouTube Music playlist links, most-played first |
| 0.4 | Discover: YouTube Music radio minus what you own, Last.fm similar tracks as backup, a one-line "why" on each pick. Owner's plan: `docs/roadmap/0.4-discover.md` |
| 0.5 | Packaging: signed and notarized DMG, bundled Python/ffmpeg/fpcalc/deno, Sparkle updates, yt-dlp updates without an app release, arm64 and x86_64 builds |
| **1.0** | Hand it to 1–2 Mac friends |
| 1.1 | Phone server (Subsonic-compatible, home Wi-Fi), weekly mix in its own `Mix/` folder, shareable playlist links, Inbox for Bandcamp, CD rips and iTunes purchases, family mode (kids profile with clean music and a parent PIN: `docs/roadmap/1.1-family-mode.md`) |
| 2.0 | Windows app on the same engine |

## Not in v0.1 (on purpose)

UI, Spotify/Apple Music import, Discover and recommendations, weekly mix, phone server, packaging and signing, Windows app shell, accounts and cloud anything. They're all in the roadmap above. Don't let Claude Code sneak them in early.
