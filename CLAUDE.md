# CLAUDE.md: Music Organizer engine

Standing rules for every Claude Code session in this project. Read this file, `docs/LIBRARY_CONTRACT.md` and `docs/ENGINE_API.md` before writing code. If a prompt conflicts with this file, **this file wins**: stop and say so instead of guessing.

## What this project is

A personal music app that replaces Spotify, Apple Music and YouTube Music for a home library. Music comes from YouTube Music, and the app turns it into a clean, permanent, tagged library of files. This repo's `engine/` is the part with no UI. It scans, matches, downloads, tags and protects the library. From v0.2 a Mac app (SwiftUI) sits on top, and later a Windows app. Both talk to the engine over JSON-RPC.

**v0.2 (from 2026-10-01): the Mac app** lives in `app/`, a Swift package: `MusicOrganizerKit` (the engine connection, the library's shape, the play queue, the lyrics parser, all tested without a window) and `MusicOrganizer` (the SwiftUI screens). `scripts/build_app.sh` builds `app/build/Music Organizer.app`. **The app never writes inside the library:** it reads audio and cover files to play and show them, and every change goes through the engine over JSON-RPC. What the app needs from the library, it asks the engine for (`library.tracks`, `library.lyrics`, `lyrics.for_video`, `listening.*`, `playlist.*`, `youtube.stream`, `youtube.video`, `discover.suggest`, `import.*`, `artist.*`, `account.*`, `sharing.*`); it doesn't parse tags or the index itself.

**Looks (2026-10-03):** Settings → App Layout chooses how the app is dressed: "Apple Native Build" (macOS's own colours and type) or "Warm Look" (`AppLook` and `WarmPalette` in the Kit, `Theme` in the app). A screen takes its surfaces, the colour of its words and its headings' type from `Theme` (`dressed()`, `heading()`, `Theme.current.panel`), never a colour of its own, so it works in every look; and in the native look every one of those is macOS's own, so that look stays exactly as macOS draws it. A look is put on when the app opens.

The owner builds with Claude Code and is not a professional programmer. Prefer boring, obvious code with good error messages over clever code. The development machine is an **Intel iMac**.

## Non-negotiable rules (the library contract)

1. **Files are the source of truth.** Everything about a recording lives in tags inside the file, including provenance tags (`MUSICORG_*`, see the contract). The SQLite index is a cache, rebuildable as described in the contract, section 5.
2. **The engine writes user data only inside the library root.**
   - External folders (the owner's existing rips, friends' iTunes folders) are **read-only sources**: never renamed, retagged, moved or deleted. Only-copy tracks are *copied* into the library, and only the copy is tagged.
   - A playlist file the owner chooses to import (`playlistfile`) is read-only in the same way, and nothing of where it was is kept.
   - **Engine-owned exceptions:**
     - the app's own config, log and cache folders (platformdirs): `config.json`, `accounts.json`, `downloads.json`, `devices.json`, logs, and yt-dlp's cache via its `cachedir` option
     - exports the user asked for (`report`, `review export`, `auto-sample`), written only through `fileops.write_export()`, which never overwrites and refuses any path inside the library's managed folders or a registered source
3. **All filesystem writes go through `musicorg.fileops`.** Other modules may not create, write, move, copy, rename, replace or delete files or folders. The only exceptions, enforced by an AST-based test (step 03a):
   - `state.py`: `state.json`, written atomically
   - `index.py`: owns the SQLite files (`index.sqlite`, `queue.sqlite`)
   - `config.py`: `config.json`, and beside it `accounts.json` (sign-ins), `downloads.json` (the computer's count of the day's downloads) and `devices.json` (the devices paired for sharing), written atomically
   - `tags.py`: its single mutagen save call, which only `fileops` ever calls, on staged copies
   - yt-dlp itself, writing **only** into the `_Staging/<batch_id>/` folder `fileops` hands it
   - a single line marked `# fileops-ok: in-memory`, for writes to in-memory buffers (e.g. Pillow saving into `BytesIO`)
4. **Never overwrite.** Name collisions get a ` (2)`, ` (3)` suffix, using the reserve-then-replace helper in `fileops`. Deletes go to the system Trash (`send2trash`). Superseded library files go to `_Replaced/` and are never purged automatically. A verified, journaled retag that swaps a file for its own re-tagged copy is not an overwrite.
5. **Every batch change is journaled and undoable.** Batch commands produce a plan first (dry run), then `apply` runs it.
6. **Never transcode into the library.** Keep the downloaded audio stream exactly as delivered (YouTube format 140, ~128 kbps AAC in M4A). Container fix-ups (remux) are allowed, re-encoding is not. **No format fallback:** if format 140 isn't offered, the job goes to review. MP3 rips copied as only-copy stay MP3.
   - **A saved video is the one other thing a download may be** (owner, 2026-10-01). It's one H.264 picture stream, at the size the owner chose (144p to 1080p), joined by ffmpeg to the same format-140 sound without converting either, as an MP4 in `Music/Videos/`. No fallback here either: a size YouTube doesn't offer that way goes to review, and nothing else is fetched in its place.
7. **No file is replaced without an audio fingerprint match** (step 08). Name + duration agreement alone is never enough to supersede a track.
8. **One gate to YouTube.** All yt-dlp and ytmusicapi calls, and thumbnail fetches, live in `musicorg.youtube` and share its rate limiter. **Downloads happen only through the throttled queue** (step 09a). Searches and metadata lookups (matching, review links, the app's search box) may call `youtube` directly, but only through that limiter.
9. **Tag writes are verified.** After every tag write, the decoded audio hash must equal the fresh hash from before the write, or the change is rolled back. For a saved video the picture stream's data is hashed too.
10. **Every modified path is guarded.** Sources *and* destinations of every file operation must resolve inside the library's managed folders. The only outside paths ever touched are `copy_in` sources and scan inputs, and those are opened read-only.

## Architecture

- `engine/`: Python 3.12 package `musicorg`, CLI entry point `musicorg`. Virtual environment at the repo root: `.venv`.
- Modules (created across the steps):
  - `cli`: argument parsing only, no logic
  - `config`: platformdirs paths and settings
  - `tools`: finding ffmpeg, ffprobe, fpcalc and deno
  - `errors`: exception types with plain-English messages
  - `library`: init and open
  - `state`: `state.json`
  - `naming`: path rules
  - `fileops`: the only writer, with journal, recovery, undo, plans and Trash
  - `tags`: read and write tags (mutagen), probe, audio hash
  - `index`: SQLite
  - `normalize`: title, artist and version-token parsing
  - `scan`: read-only indexing of external sources
  - `youtube`: ytmusicapi, yt-dlp and thumbnails, plus the rate limiter and replay mode
  - `match`: candidate scoring
  - `fingerprint`: Chromaprint comparison
  - `queue`: persistent job queue
  - `pipeline`: download → verify → tag → commit, and adopt
  - `lyrics` and `artwork`
  - `videolyrics`: a song's lyrics timed to its video, by lining up the two recordings' sound and by the video's captions (read-only)
  - `browse`: what the app shows: the library's tracks with their details, and a track's lyrics (read-only)
  - `listening`: the owner's favourites, play counts and playlists, kept in `state.json` by `MUSICORG_ID`
  - `discover`: songs the owner doesn't have, found from the ones they do (read-only; lookups through `youtube`)
  - `imports`: a playlist from elsewhere, each song found on YouTube Music (read-only; lookups through `youtube`)
  - `artist`: the Artist page: an artist's YouTube Music page, with which of their songs the owner has (read-only; lookups through `youtube`)
  - `spotify`: signing in to Spotify in the browser (PKCE) and reading the owner's playlists; the only module that talks to Spotify, and it only reads
  - `deezer`: a public Deezer playlist or album, read by its link with no sign-in; the only module that talks to Deezer, and it only reads
  - `lastfm`: the owner's most played and loved songs on Last.fm, with their own API key; the only module that talks to Last.fm, and it only reads
  - `playlistfile`: a playlist saved as a file (CSV, text, M3U), the way in for Amazon Music; the file is the owner's, opened read-only
  - `sharing`: the library shared with a phone player on the home network: the list of everything, the files it names, and pairing, over HTTP (read-only; the only module that listens beyond this computer, and only when the app's Settings switch is on)
  - `report`
  - `review` and `review_web`: the review spreadsheet, and the local review page
  - `rpc`: the JSON-RPC server
- External binaries, located by `tools`:
  - `ffmpeg` and `ffprobe`
  - `fpcalc` (Chromaprint)
  - `deno` ≥ 2.3, which yt-dlp needs for YouTube
- Dependencies: `yt-dlp[default]`, `ytmusicapi`, `mutagen`, `rapidfuzz`, `send2trash`, `platformdirs`, `requests`, `Pillow`. Dev tools: `pytest`, `ruff`. Ask before adding anything else.

## Enums

All states, decisions and tag values are defined **once**, in `docs/ENGINE_API.md` → "Enums". Use those exact strings everywhere: code, CSV, RPC and tags. Never invent a new value without adding it there first.

## Conventions

- Type hints everywhere. Format and lint with `ruff`.
- Use `pathlib.Path`, never string paths. Build library paths only through `naming` and `fileops`.
- **Windows-safe from day one:** no POSIX-only calls outside a guarded helper, and no assumptions about `/`, case sensitivity or path length. Tests run on Windows in CI.
- Ignore `.DS_Store`, `._*`, `Thumbs.db` and `desktop.ini` everywhere: scan, plan checks, collision checks.
- Log to a rotating file in the log directory and to stderr. **Never print to stdout in `rpc` mode**, because stdout carries the protocol.
- Errors shown to a user are plain English, e.g. "YouTube is slowing us down; resuming at 3:10am", not a raw traceback.
- **External library APIs change.** Before relying on any ytmusicapi, yt-dlp or LRCLIB field or option named in these docs, check it against the installed version and record a fixture. The docs describe intent, and the installed library is the truth. Note any differences in `docs/CHANGELOG.md`.

## Secrets (this repository is public)

- Never commit keys, passwords, tokens, login cookies or personal email addresses, and never put them in tests, fixtures, docs or commit messages. Anything committed stays public even after a later commit deletes it.
- Commits use the owner's private GitHub noreply address (`git config user.email`), never a personal email.
- Commits and pushes go through the `.githooks/` secret check (`scripts/check_secrets.py`: files, commit messages and commit author details). Never bypass it with `--no-verify`. If it flags something harmless, fix the line or end it with a `secrets-ok` comment, and say so.
- Tests that need a fake secret build it at runtime (e.g. `"ghp_" + "a1B2" * 10`), so the file never contains one.
- YouTube logins (yt-dlp cookie files, ytmusicapi `browser.json` / `oauth.json`) live outside the repo, in the app's config folder.
- A Spotify sign-in (`accounts.json`: the owner's app's Client ID and a refresh token that can only read playlists) lives there too. No token, one-time code or Client ID is ever logged, shown in an error, or written anywhere else.
- So does Last.fm's set-up (the owner's username and their own API key), under the same rule: neither is logged, shown in an error, given out over RPC, or written anywhere else.
- **Sharing (the owner's one hard rule, 2026-10-04): nothing private on GitHub or the web.** No network address, computer name, pairing code or key in this repository: not in code, tests, fixtures, docs, logs, the changelog or commit messages. A test that needs one builds it while it runs. The keys given to paired devices are kept only as their SHA-256, in `devices.json` in the app's config folder, for each profile; a key, a pairing code and a caller's address are never logged or shown in an error, and no key is given out over RPC.

## Testing

- `pytest` must pass before a step is declared done. Run it, don't assume.
- **Tests never touch the network.** Record real responses once into `engine/tests/fixtures/` and replay them. Replay mode: setting `MUSICORG_REPLAY_DIR=<dir>` makes `youtube` and `lyrics` answer only from recorded fixtures and raise on a miss. Live tests carry `@pytest.mark.live` and are skipped by default.
- Tests never touch real music folders or the real Trash (monkeypatch `send2trash` except in one marked integration test).
- Audio fixtures are generated at test time with ffmpeg (step 02). CI sets `MUSICORG_REQUIRE_TOOLS=1`, so missing tools **fail** the run instead of silently skipping tests.
- Every `fileops` operation needs tests for: success, crash between intent and act, crash between act and done, collision, guard refusal, and undo.

## Definition of done for any step

1. The step's **Acceptance** checks pass, run for real.
2. `pytest` is green locally and in CI on all three runners, and `ruff check` is clean.
3. `docs/CHANGELOG.md` has a short entry: what was added, and any deviation from the docs and why.
4. Nothing from the "Not yet" list was built.
5. For app changes: `swift build` and `swift test` pass in `app/`, and the change was looked at in the running app.

## Not yet (see `docs/ROADMAP.md` for the version each belongs to)

Weekly mix, a Subsonic-compatible server (but see sharing, below), packaging, signing, notarization, Windows app shell, accounts and cloud anything (but see imports, below).

**Discover was started early, on 2026-10-01, at the owner's request** (it was on this list). Built: `discover.suggest`, the app's What's New and Find pages, the guided "What music would you like today?" mode, and Find's Download Automatically (find and queue a batch in one click). Last.fm was added on 2026-10-03, also at the owner's request: the owner's most played songs there are a starting point (seed `lastfm`). The Artist page was added the same day (`artist.*`: who an artist is, their songs, albums and similar artists, from YouTube Music's own page). Still not yet, from its plan (`docs/roadmap/0.4-discover.md`): the `Discovered/` folder and its tag (a contract change), Last.fm's "similar tracks" as a source of picks, and concerts on the Artist page.

**Imports were started early, on 2026-10-02, at the owner's request** (Spotify/Apple Music import was on this list, and so were accounts). Built: Discover → Import Playlists for a YouTube or YouTube Music playlist by its link, with no sign-in; for Spotify, after a sign-in on Spotify's own page (2026-10-03); and, the same day, for a public Deezer playlist or album by its link, for a playlist saved as a file (the way in for Amazon Music), and for the owner's lists on Last.fm. Still not yet: Apple Music, and signing in to YouTube (`docs/ROADMAP.md`, v0.3). A sign-in is built only for reading playlists: logins stay on the Mac, never in the repo or the library.

**Profiles were built on 2026-10-03, at the owner's request** ("accounts" was on this list; these are local, with nothing online). A profile is a name and a library folder of its own, chosen in the app's Settings → Profiles; the app starts the engine for one profile at a time (`MUSICORG_PROFILE`), and the engine still serves one library. Sign-ins are kept per profile; the daily download limit is counted once for the whole computer. A new profile's library is made in the Mac's Music folder, named after it ("Music Kids"); and a profile can be marked as a child's (both 2026-10-03). The mark does nothing yet. Still not yet, from `docs/roadmap/1.1-family-mode.md`: a parent PIN, and filtering a child's profile by the explicit tag.

**Sharing with a phone player was started early, on 2026-10-04, at the owner's request** (the phone server was on this list). The owner's one condition is the privacy rule under Secrets. Built: `sharing`, a read-only server inside `musicorg serve` that gives a phone player on the home network the library's songs, videos, covers, lyrics and playlists (format 1: `docs/ENGINE_API.md`, section 3); Settings → Sharing in the app (the switch, this Mac's address, Pair a Device, the paired devices); and the app announces the share with Bonjour. **The limits the owner was promised, which every later change keeps:**

- Off until they switch it on in the app's Settings. The engine never starts sharing by itself.
- Read-only for the music files: the server never writes inside the library.
- A device is paired once, with a six-digit code shown on the Mac.
- The home network only, and only while Music Organizer is open: only callers with a private (RFC 1918), link-local or loopback address are answered, no port is ever opened on the router (no UPnP), and nothing is sent to the internet.

**The phone player is a separate project. It is never named, described or linked to here:** in code, docs, the changelog and commit messages it is "a phone player". Still not yet: favourites and play counts coming back from the phone, carrying on a file that was cut off, lyrics timed to a video, and a Subsonic-compatible server.
