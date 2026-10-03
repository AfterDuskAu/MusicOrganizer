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
- **Every song playable (owner's choice A):** rips not identified yet are in the library under their own names, marked "not identified yet", and are upgraded in place when they're identified.
- **A daily player (owner's choice B):** favourites, play counts and Most Played, Recently Added, playlists, and a full-window "now playing" screen with large lyrics.
- **YouTube Music in the app:** search, play anything without saving it, and download a song only when asked.
- **Discover's first slice** (What's New and Find): see v0.4 below.
- **Fix a song by hand:** names, cover and lyrics, from a song's right-click menu. (Built but not yet tried in the running app: see the changelog.)
- **A song's official video** on the Local Visualizer, in the app's own player: Cover / Video, a menu of picture sizes up to 1080p, Full Screen, and Save Video (kept in `Music/Videos/`, listed under Downloads and Library → Videos).
- **A YouTube song that won't start** is noticed and tried again with a fresh address.

**The owner's layout for the sidebar and Settings** is in [`roadmap/0.2-app-layout.md`](roadmap/0.2-app-layout.md).

**The Visualizer** (2026-10-03, at the owner's request): visuals that move with the real sound, each made from a reference picture the owner sends. It's now a project of its own, **Particle Accelerator**, built separately first. When it reaches 1.0, the Local Visualizer page gets it as a Swift package. What that takes here: [`roadmap/0.2-visualizer.md`](roadmap/0.2-visualizer.md).

**Parked by the owner (2026-10-01):** Fix A-1 (the app feels rough), Fix A-2 (automatic downloads stay out of the main library) and Fix A-3 (a real media player tab). All three are written up in `docs/KNOWN-ISSUES.md`.

**Still to build, roughly in this order** (the owner decides after using the first slice):

- **Review queue in the app:**
  - listen to the rip and a match side by side
  - use, reject, only copy, or paste a link
  - see the fingerprint result where there is one
- **Settings:** preferred names (like "Jay Z"), the library folder, the queue.
  - **Profiles** (built 2026-10-03): Settings → Profiles. Each person has a name and a library folder of their own, with their own playlists, sign-ins and app settings; switching deletes nothing. The daily download limit is shared.
  - **Accounts, all in one place** (owner, 2026-10-01): YouTube, Spotify, Apple Music and any later service sign in from one Settings section.
    - **YouTube:** an opt-in sign-in using the browser's login, for age-restricted songs and, with YouTube Music Premium, the 256 kbps AAC audio. A spare account is suggested, since an account used for downloading can be restricted. To be tested before it's promised.
    - **Spotify and Apple Music:** for bringing playlists and libraries across (v0.3), never for their audio, which is locked. The export-file route needs no sign-in and stays the default.
    - Logins are kept in the Mac's Keychain or the app's config folder, never in the library or the repo.
- **Download quality, as a setting** (owner, 2026-10-01). Three choices:
  - **Standard, 128 kbps AAC** (the default; what the engine does today).
  - **Better, about 160 kbps Opus:** YouTube's Opus audio kept exactly as it is, repackaged without converting it. Converting is never done: it lowers quality.
  - **Best, 256 kbps AAC:** only with YouTube Music Premium, signed in under Settings → Accounts. To be tested before it's promised.
  - Checked on the iMac (macOS 15) on 2026-10-01: Apple's player opens and decodes Opus both as an `.opus` file and inside an MP4 file, so the app needs no second player. YouTube's own WebM packaging doesn't play, so a repackage (no re-encode) is needed. Only a 3-second test tone was tried, not a real download in the app, and not macOS 14.
  - Needs the owner's OK to change rule 6 in `CLAUDE.md` ("format 140 only, no fallback"), and each quality needs its own download checks.
- **What the engine is doing:** the queue, the journal's batches and Undo, in a window.
- **Reordering a playlist by dragging**, and exporting one as an `.m3u8` file.
- Photonizer's lessons on staying responsive, for when the library is much bigger (812 songs sort instantly today):
  - stable list rows
  - sorting off the main thread
  - "a stall is the main thread blocked 100 ms or more"

## v0.3: imports for friends (started early, 2026-10-02)

- Spotify data export, Exportify CSV, Apple Music library XML, YouTube Music playlist links, most-played first.
- Someone moving off a streaming service gets their songs as local files through the throttled download queue, 250 a day as standard, 500 at most (owner, 2026-10-01).

**Built (2026-10-02):** Discover → Import Playlists, for a YouTube or YouTube Music playlist by its link (public or unlisted, no sign-in). The songs are found, listed, and downloaded with one click into a playlist of the same name. The finding, the list and the button are the same for every service.

**Built (2026-10-03): Spotify sign-in.** Settings → Accounts walks through it: the owner registers a free app at developer.spotify.com (their account needs Spotify Premium; at most 5 people can sign in to one app), pastes its Client ID, and signs in on Spotify's own page in the browser. Their playlists and Liked Songs then appear under Import Playlists → Spotify. Only playlists the owner made or collaborates on give their songs. Not yet tried against Spotify itself.
  - Someone without Premium has no sign-in. The other way is a file: an Exportify CSV reads through Import Playlists → Amazon Music → Choose File (2026-10-03). Spotify's own "Download your data" export (JSON) isn't read.

**Built (2026-10-03, evening): Deezer, Amazon Music by file, Last.fm.** The owner asked which other big services were worth adding, and chose these three.
  - **Deezer:** a public playlist or album by its link, no sign-in. Tried against Deezer itself.
  - **Amazon Music:** it can't be read from outside and has no export, so the way in is a playlist saved as a CSV or text file by a service such as TuneMyMusic or Soundiiz. Any CSV, text (`Artist - Title` lines) or M3U playlist file reads, from any service. Not yet tried with a real Amazon export.
  - **Last.fm:** the owner's username and their own free API key (Settings → Profile → Last.fm). Their Loved Tracks and most played import as playlists, and "My most played on Last.fm" is a starting point in Discover → Find. Not yet tried against Last.fm itself.
  - **Looked at and left:** Tidal (would need a registered developer app, like Spotify's). SoundCloud is still a "Coming" row in Settings with nothing behind it.

**Still to build, and what each needs from the owner** (checked on 2026-10-02):

- **Apple Music.** Signing in to Apple Music from an app needs a paid Apple Developer membership (US$99 a year), which this project doesn't have. Two ways that need none: read the playlists straight from the Music app on this Mac (macOS asks once for permission), or a library file exported from the Music app (File → Library → Export Library…).
- **Signing in to YouTube**, for private playlists and Liked Music. Possible with the installed `ytmusicapi` using the browser's sign-in; a spare account is suggested, since an account used alongside downloading can be restricted (see Accounts, above). Until then: set a playlist to Unlisted and paste its link.

## v0.4: Discover (started early, 2026-10-01)

Plan: [`docs/roadmap/0.4-discover.md`](roadmap/0.4-discover.md).

**Built (2026-10-01):** picks from the whole library, most played, the top artist, a playlist, a named artist and similar bands, or a genre; the What's New and Find pages, with a grid of cards to play, download one by one, or download several together after seeing the plan. The guided "What music would you like today?" mode was added on 2026-10-02. Last.fm arrived on 2026-10-03 as a starting point: the owner's most played songs there. **Still to build:** the `Discovered/` inbox, and Last.fm's "similar tracks" as a source of picks (each would cost a YouTube search). The plan's own list follows.

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
