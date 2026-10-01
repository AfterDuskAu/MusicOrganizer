# Known issues

Open gaps found along the way, not fixed yet. Each has a "decide" line when it needs the owner. Delete an entry when it's fixed, and say so in `CHANGELOG.md`.

Most of these came from comparing the engine with the Photonizer project's lessons (2026-09-30).

## Needs the owner's decision

- **Saved videos live in `Music/Videos/`, not in a folder beside `Music/`** (built 2026-10-01). The owner asked for "a separate folder of videos". Inside `Music/` every safety rule already covers them; a top-level `Videos/` means changing `fileops` itself (its guard, commit, supersede, restore and undo all assume `Music/`) with the full set of crash tests. Decide: is `Music/Videos/` fine, or should the top-level folder be done as its own step?
- **Videos in the YouTube Music search page** (suggested, not answered): a Songs / Videos switch there, so a video can be found and saved without first playing its song. Today a video is saved from the Local Visualizer.
- **Sizes of saved videos**, from one 4-minute official video (2026-10-01): 1080p about 100 MB, 720p about 35 MB, 480p about 22 MB, 360p about 15 MB, 144p 7 MB; the song alone is about 4 MB. A video that is a still picture is far smaller.
- **The "E" on a rip is copied from the match, not heard** (owner, 2026-10-01: a song marked explicit whose audio is the censored edit). When a rip gets its official details, it gets YouTube Music's explicit mark too, and the matcher prefers the explicit listing. A censored and an uncensored copy of a song sound the same to the fingerprint check. The owner can untick Explicit in Edit Details. For the song-identification research: can the two edits be told apart at all (the lyrics' muted words, the clean listing's length)? And should a rip's mark be left unset rather than guessed?
- **Moving or renaming the rips folder loses review work.**
  - A source's id comes from its path (`state.source_id`), and every item id comes from the source id plus the file's path (`index.item_id`).
  - So after a move, or a drive mounting under a new name, rescanning makes new items: the old decisions, rejections and fingerprint results no longer attach. "Superseded" and "adopted" are keyed by the old absolute path too.
  - Replaced rips are only re-linked (the video is already in the library). But adopted rips come back as `new` and, once reviewed again, could be adopted a second time as ` (2)` copies.
  - Removing a source and adding it back **at the same path** is safe.
  - Proposed fix: `musicorg sources relocate <source_id> <new folder>`, which keeps the id and maps the old path to the new one. `sources add` would also suggest it when a new folder's files match a missing source.
  - Decide: build it now, or before friends use the app (1.0)?
- **state.json has no history.**
  - It holds the owner's review decisions, rejections, artist names, superseded links and fingerprint results. It's written safely (a temp file, then a rename), but a wrong review import overwrites decisions in place, and there's no undo for that.
  - Proposed fix: before each save, keep a copy of the day's first version in `.musicorg/backups/` (the newest 7).
  - That's a new place the engine writes. CLAUDE.md rule 3 and the write-rules test need a named exception, so the owner decides.
- **A focused safety audit before the first real batch.** Photonizer's audit found its worst defects at exactly these edges: file identity, crash recovery and undo.
  - Proposed: one read-only review session of fileops' guard, reserve-then-replace, recovery and undo; the pipeline's commit and undo; queue recovery; and state.json writes.
  - Run it after 09b's calibration run and before `plan replace` without `--stage-only`. Decide: yes or no.

## Parked fixes for the Mac app (owner, 2026-10-01)

The owner named these and parked them: nothing here is built until the owner says so.

### Fix A-1: the app feels rough

*Built 2026-10-01 (the first three points below). Measured with the app's own stall detector (`MUSICORG_STALLS=1`; a stall is the main thread not answering for 100 ms or more), on the owner's 1,874-song library:*

- *Before: nearly every click and every page-sized scroll froze the app for about 200 ms, and launch froze it for about a second.*
- *After: clicks no longer stall at all. A jump of a whole page still takes about 135 ms (every visible row is new); ordinary scrolling brings in a row or two at a time. Launch stalls for about 0.4 s.*
- *What it was: a profile showed the app's own code was not the cost. macOS was measuring the height of each table row, one by one, by laying out all its cells. The table is now told one fixed row height. The first attempt (not rebuilding the list on every click) was worth doing but changed little by itself.*
- *Still slow, measured 2026-10-01: the first visit to a page (0.3 to 0.5 s, building its table), launch (two stalls of 0.2 to 0.6 s), and a page switch while the lyrics panel comes or goes (0.12 to 0.19 s, because the table changes width). A table built directly on AppKit, instead of SwiftUI's, is the likely cure for all three. **Owner, 2026-10-01: a maybe, down the road. The delay is now hard to notice; only do this if the app gets slower as it grows.***
- *Still open under A-1: the last three points below (the rip with no file extension, the slow first read, junk album names), and remembering scroll positions across a restart (the section is remembered; positions are kept while the app is open).*

- **It feels glitchy and slow:** slow to scroll, slow to follow clicks.
  - Likely causes to measure first (Photonizer's rule: measure before fixing): the song table is filtered and sorted again every time anything on screen changes; every visible row starts its own cover load; the player bar and the rows all redraw when the playing song changes.
  - Fixes to try: work the list out once per change and off the main thread; keep rows' identity stable; load covers through a small queue; check the app in a 60-second scroll with nothing else running (the first impressions were formed while a 1,063-song copy and the test suite were running on the same Mac).
- **The lyrics jump about ("spaz") as they move.** The view scrolls with an animation on every new line, inside a lazily built list whose row heights aren't known ahead, and the current line changes weight (bold), which changes its height mid-scroll. Fix: fixed layout for every line (no weight change, or a scale/colour change only), and one smooth scroll.
- **Remember where the owner was.** Moving between Songs, Albums, a playlist and so on always starts at the top. Each list should come back at the scroll position, selection and sort order it was left with, and the app should reopen on the section it was closed on.
- **A rip whose file name has no extension can't be copied in.** One of 1,063 unconfirmed copies failed this way on 2026-10-01: the file's name ends "(320 kbps (2)" with no ".mp3", so the staged copy's "extension" is everything after the first dot in "Y2meta.app". The scan knows it's an MP3; the adopt should name the staged copy by the detected format, not the file name.
- **macOS asks to allow folders at every start, which stalls the start-up** (owner, 2026-10-01; this was also the unexplained slow first launch, when the engine sat for 53 seconds inside a plain "open this file" call). macOS remembers the answer per app and tells apps apart by signature; an ad hoc build is a new app every time it changes. Fix: a self-signed certificate the owner makes once (`docs/SIGNING.md`); the build script uses it when it exists. Until then, start the app without rebuilding and macOS doesn't ask. Still worth doing: have the engine touch fewer protected folders at start-up (it asks about Music and Downloads as well as the library's own folder).
- **"Reading your library…" takes a minute or more after a big import.** The first `library.tracks` after new songs arrive reads each new file's tags from disk, and on the iMac's disk that is slow for files not read recently (about 90 seconds for 1,062 new songs on 2026-10-01; this was also the unexplained slow first launch). After that it's instant. Fix: the adopt and replace jobs already hold each song's tags, so they should store the app's details in the index as they go; and the app should show progress instead of a bare spinner.
- **Unconfirmed copies keep junk from the rip's own tags**, such as an album called after a download site. They're fixed when the song is identified; a clean-up of obvious junk could come sooner.

### The video player (built 2026-10-01): what's still rough

- **Full Screen, the Downloads strip, dragging a download onto Library, and View → Columns have never been tried by Claude in the running app** (the owner was using it, and had said to stop driving it). The owner's own tries are the test so far.
- **Song tables are now built row by row** so that downloaded rows can be dragged. Fix A-1's smoothness was measured before this; it hasn't been measured since. If scrolling feels worse, taking dragging out again (the right-click menu does the same job) is the first thing to try.

- **Finding a video can take 20 seconds**, not only the 5 to 12 first measured (seen 2026-10-01 while the research workers were also using YouTube from this Mac).
- **Switching between Cover and Video hitches for about 0.2 s** (measured: the player's own work takes 0 ms; the screen laying itself out again, lyrics included, is the rest). The same size as the lyrics panel's hitch above.
- **The first look-up of a video takes 5 to 12 seconds:** one search and one yt-dlp look-up, each behind the 1.5-second rate limiter. The next song's video is looked up ahead of time; a song jumped to by hand waits.
- **The video isn't large** in a small window: it shares the page with the lyrics. The player tab's layout is the owner's to decide (Fix A-3).
- **Nobody has listened to it yet**, and it's been tried on four songs.
- YouTube Music marks some still-picture "official audio" uploads as official videos, so a few "videos" are a picture that doesn't move.

### Fix A-2: automatic downloads stay out of the main library

*Partly built 2026-10-01: Settings → General chooses "Discover Downloads" (the default) or "All Library" for downloaded songs, and Discover → Downloads lists them. Still to do when Discover exists: its own folder on disk, and choosing song by song.*

For when the app can download songs from YouTube by itself (Discover, v0.4).

- A song downloaded automatically must **not** show up in the library's Recently Added, or anywhere in the main library.
- It goes to **Discovery → Recently Added → Songs** instead.
- It stays separate until the owner chooses. The owner is offered two options: bring the songs into the main music library, or keep them separate to sort later.
- For the engine this means a discovered song needs its own marker (and its own folder, as `docs/roadmap/0.4-discover.md` already plans: `Discovered/<Genre>/<YYYY-MM Month>/`), and the main library's lists (Songs, Albums, Artists, Recently Added, Most Played) must leave marked songs out until they're accepted.

### Fix A-3: a real media player tab

The full-window "now playing" screen exists, but the owner wants a proper **tab** for it, to open later and lay out the way they want.

- **A lyrics section** with the album cover (or the song's picture) as the background. Nice, but simple.
- **A way to put lyrics in by hand**, for a song with no lyrics or the wrong ones. *Built 2026-10-01 as Edit Details… (right-click a song); it still needs its place in the player tab.*
- **The sidebar's entries (Songs, Artists, Albums…) can be added or removed** by the owner. *Built 2026-10-01.*
- **The columns of information beside each song can be added or removed** by the owner. *Built 2026-10-01: the Columns menu on each list.*
- The layout is the owner's to decide when they sit down with it: build the pieces so they can be arranged, don't fix a design.
- Engine work this needs: a way to save lyrics the owner typed (a new write, through `fileops`, journaled and undoable like any other), and somewhere to keep the owner's layout choices (the app's own settings, not the library).

## For later steps

- **Step 11 (RPC):** treat the app stopping the engine (SIGTERM) like Ctrl-C: finish or requeue the current job cleanly. Today only Ctrl-C is handled, so a stop counts as a crash. The crash-loop guard sets a job aside after 5 of those.
- **v0.2 Mac app:** carry over Photonizer's responsiveness rules that fit an app talking JSON-RPC:
  - stable row identity in lists
  - sorting and grouping off the main thread
  - a memory cache for album-art thumbnails
  - "a stall is the main thread blocked 100 ms or more"
  - "done means launched and responsive for 60 s"
- **After v0.1.0:** a static type checker (mypy or pyright). It's a new dev tool, so it needs the owner's OK.
