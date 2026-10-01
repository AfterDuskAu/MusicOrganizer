# Roadmap

Where Music Organizer is and what comes next. The owner decides what goes in each version; the detailed plans live in `docs/roadmap/`. Updated 2026-10-01 (the Mac app's first slice).

## Where things stand (v0.1.0, released 2026-09-30)

- **The engine** has no screen of its own and does all the work:
  - scans the owner's rips without ever changing them
  - matches them on YouTube Music
  - copies them into a clean library with official details, covers and timed lyrics, on the owner's own audio
  - keeps one copy of each song, in the owner's spellings
  - can undo everything it does
  - speaks JSON-RPC, so the Mac app can drive it
- **The owner's library:**
  - 812 songs, with 787 covers and 712 timed lyrics
  - 1,012 rips waiting in review, and 61 not found
  - 9 review decisions saved on 2026-09-30 (4 matches chosen, 5 skipped), not yet brought into the library

## On hold: finish the owner's library (v0.1.x)

On hold since 2026-10-01 (owner): it waits for the song-identification research, so detection can be made more reliable with what it finds. The app comes first.

1. **Finish the song-identification research.**
   - It lives in `~/Developer/MusicOrganizer-research`, outside this public repo because it lists the owner's music.
   - It was paused on 2026-09-30; `resume.sh` starts it again.
   - For each rip still in doubt, it searches YouTube Music, YouTube, SoundCloud and Bandcamp, and compares the audio.
2. **Bring the research's answers into the engine, with the owner's OK.**
   - Its suggested decisions (`suggested_decisions.csv`) can go through `musicorg review import`, so every choice is still checked and saved the usual way.
   - Its rules can teach the matcher to decide more on its own, for example "length and one more thing disagree → a different recording". That leaves fewer songs for the owner to review by hand.
3. **Review what's left** on the review page (`musicorg review serve`), in short sessions.
4. **Bring the decided songs in**, the same routine as before:
   - `musicorg plan adopt --matched`
   - `musicorg lyrics --missing`
   - `musicorg artwork --missing`
   - apply each plan, then `musicorg queue run`
5. **The not-found songs:** copy them in with their own names (`plan adopt --include-not-found`), or give them a link or cover the research found.
6. **Small decisions waiting for the owner** (`docs/KNOWN-ISSUES.md`):
   - a `sources relocate` command, so moving the rips folder doesn't lose review work
   - daily backups of `state.json`, which holds the review decisions
   - a focused safety check of the code before any batch of hundreds of songs

## Now: v0.2, the Mac app

A SwiftUI app that starts the engine and talks to it (`musicorg serve`). Code in `app/`; build it with `scripts/build_app.sh --open`.

**Built (2026-10-01), the first usable slice:**

- Songs (sortable, with covers), Albums (a grid, then an album's page) and Artists, and a search box over all three.
- A player: play, pause, next, previous, a position slider, volume, shuffle, repeat, "up next", the space bar and the keyboard's media keys.
- Lyrics beside the library: timed lines light up as they're sung, and a click on a line jumps there.
- The sidebar shows how many songs there are and how many rips still wait for review.

**Still to build, roughly in this order** (the owner decides after using the first slice):

- **Review queue in the app:**
  - listen to the rip and a match side by side
  - use, reject, only copy, or paste a link
  - see the fingerprint result where there is one
- **Search box** that plays anything from YouTube Music, and a "download the official version" button on a song. **Downloads only happen when the owner asks** (2026-09-30).
- **A full-window "now playing" view:** the big cover with the karaoke lyrics. This is also where the timed lyrics get their listening check.
- **Settings:** preferred names (like "Jay Z"), the library folder, the queue.
- **What the engine is doing:** the queue, the journal's batches and Undo, in a window.
- Decisions to make first:
  - **Playlists:** how they're stored. The suggestion is `.m3u8` files in a `Playlists/` folder, so the files stay the source of truth.
  - **Play history:** needed for "most played" and for Discover.
- Photonizer's lessons on staying responsive, for when the library is much bigger (812 songs sort instantly today):
  - stable list rows
  - sorting off the main thread
  - "a stall is the main thread blocked 100 ms or more"

## v0.3: imports for friends

- Spotify data export, Exportify CSV, Apple Music library XML, YouTube Music playlist links, most-played first.
- Someone moving off a streaming service gets their songs as local files through the throttled download queue, at most 300 a day (owner, 2026-09-30).

## v0.4: Discover

Plan: [`docs/roadmap/0.4-discover.md`](roadmap/0.4-discover.md).

- Recommendations from a playlist, the whole library, most played, or an artist and similar bands.
- A grid of cards: play in the app first, with small Spotify, Apple Music and SoundCloud logos, and download on demand.
- A guided "What music would you like today?" mode.
- Discovered songs go to their own inbox: `Discovered/<Genre>/<YYYY-MM Month>/`, with Keep or Remove.

## v0.5: packaging

A signed and notarised DMG with Python, ffmpeg, fpcalc and deno bundled, automatic updates, yt-dlp updates without a new app release, and Intel and Apple Silicon builds. (yt-dlp updates itself, so this is a direct download, never a Mac App Store app.)

## 1.0: hand it to 1–2 Mac friends

## 1.1

- A phone server (Subsonic-compatible, on home Wi-Fi).
- A weekly mix in its own `Mix/` folder.
- Shareable playlist links.
- An Inbox for Bandcamp, CD rips and iTunes purchases.
- **Family mode:** [`docs/roadmap/1.1-family-mode.md`](roadmap/1.1-family-mode.md). A kids profile with clean music and a parent PIN.

## 2.0: the Windows app, on the same engine

The engine already runs its tests on Windows.

## Parked ideas (no version yet)

- Copies for devices that need another format, written outside the library (the contract's export feature).
- A static type checker (mypy or pyright) for the engine, after v0.1.
- Folder names and album years: "a wrong year is worse than none", for remastered albums whose year on YouTube Music is the reissue's.
