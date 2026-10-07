# Changelog

## 0.2.0 (in progress): the Mac app

2026-10-01. The owner put the library clean-up on hold until the song-identification research is back, and asked for an app to use now.

- **The app** (`app/`, built by `scripts/build_app.sh`): songs, albums and artists with covers, search, a player with shuffle, repeat, "up next" and the media keys, and timed lyrics that follow the song. It starts `musicorg serve` itself and never writes inside the library.
- **Engine, for the app:** `library.tracks` (every song with the details a screen needs) and `library.lyrics` (a song's `.lrc` text and its plain lyrics), from a new read-only module, `browse`.
- **The index is now version 2:** `library_tracks` gained a `details_json` column, where `browse` keeps what it read from each file's tags, beside the size and modified time it was read at. A version 1 index is upgraded in place the next time the library is opened for writing. No rebuild, so the matcher's saved work is kept. The first `library.tracks` reads every file's tags once (about 8 seconds for 812 songs on the iMac); after that it takes under a tenth of a second.
- **CI** builds the app and runs its tests on both Macs.
- Deviations: `CLAUDE.md`'s "Not in v0.1" list became "Not yet", since the Mac app is no longer on it. SwiftUI table cells don't inherit the window's environment on macOS, so the app passes its model to them by hand.
- Not built yet: the review queue in the app, searching and downloading from YouTube Music, settings. Decisions for the owner are in `docs/ROADMAP.md`.

2026-10-01, later. After using the first slice the owner chose two things: every song playable now, then a daily player.

- **Unconfirmed copies** (`musicorg plan adopt --unconfirmed`): every rip still in `review` or `not_found` is copied into the library under its own names, tagged `MUSICORG_MATCH=unconfirmed` (a new enum value), so it can be played. Its state doesn't change, so the review queue is as it was.
  - When such a rip is decided later (a match chosen, or only-copy), the usual adopt **upgrades that copy where it is**: new tags, then a move to its new name. The rip isn't copied a second time, the track keeps its `MUSICORG_ID`, and undo puts the unconfirmed copy back.
  - If it turns out to be a duplicate of a better copy, it's set aside in `_Replaced/`.
  - A rescan or index rebuild doesn't mistake an unconfirmed copy for an adopted rip.
  - WebM, raw AAC and WAV rips are left out, as with any adopt.
  - No lyrics or covers are looked up for these: without a confirmed match there's nothing safe to look up. They keep whatever picture the rip had.
- **The index is now version 3:** `library_tracks.match`. Versions 1 and 2 are upgraded in place.
- **`listening`** (new module): favourites, play counts and playlists, in `state.json` under `"listening"`, by `MUSICORG_ID`, so they survive renames and upgrades. The contract already set `state.json` aside for play history. RPC: `listening.get`, `listening.favourite`, `listening.played`, `playlist.create`, `playlist.rename`, `playlist.delete`, `playlist.set_tracks`.
  - Deviation from the roadmap's suggestion (`.m3u8` files in a `Playlists/` folder): a new folder the engine writes needs a contract change and new `fileops` operations. `state.json` needed neither. Exporting a playlist as `.m3u8` can come later as an export.
  - A play is counted when a song plays to its end, not when it's skipped.
- `library.tracks` also gives `acquired` (when the song came into the library).
- **The app:** Favourites, Recently Added, Most Played and Not Identified Yet in the sidebar; playlists (new, rename, delete, add songs, remove, move up and down); a heart beside every song; a Plays column; a full-window "now playing" screen with the cover and large lyrics (click the cover in the player bar; Esc closes it).

2026-10-01, evening. The owner's next two choices: search YouTube Music from the app, and fix a song by hand (which covers the lyrics part of parked Fix A-3).

- **Play from YouTube Music without saving anything:** `youtube.stream(video_id)` asks yt-dlp where a song's format-140 audio can be played from (no download), through the shared rate limiter, and the app's player plays that address. RPC: `youtube.stream`. Checked live once against yt-dlp: the address answers range requests as `audio/mp4`. `youtube.stream` and `search.ytmusic` are answered from their own thread, so a slow YouTube lookup doesn't hold up the app's other requests.
- **Download when asked:** a new plan kind, `download` (`musicorg plan download <video_id>…`, and the app's Download button). No rip is behind it, so nothing is replaced and the fingerprint gate (rule 7) has nothing to compare; every other download check applies (format 140 only, AAC, bitrate, length), and a download that fails them isn't kept. The song gets its official details, lyrics and cover like any other, with `MUSICORG_SOURCE=youtube_music` and no `MUSICORG_MATCH`. These go to the main library because the owner clicked for each one; parked Fix A-2 is about *automatic* downloads, which don't exist yet.
- **Fix a song by hand:** a new plan kind, `edit` (RPC only). Title, artist, album, album artist, genre, year and track; a cover from a picture on the computer; lyrics typed or pasted.
  - Timed lyrics become the song's `.lrc` with their words in the tags. Plain lyrics go in the tags, and a `.lrc` that was there is set aside in `_Replaced/` as the wrong one. Empty text removes the lyrics.
  - A chosen cover is embedded and becomes the album folder's `cover.jpg`; a different one already there is set aside, because here the owner has chosen.
  - The file moves to the folder and name its new details give, keeping its `MUSICORG_ID`, so favourites and playlists follow it. Undo puts everything back.
  - Deviation: the owner's picture is a new kind of outside file the engine reads (rule 10 names only `copy_in` sources and scan inputs). It's read once, when the plan is made, never changed, and the prepared cover travels inside the plan.
- `queue.jobs` (RPC): how a batch's jobs ended, so the app can say why a download or an edit didn't work. `library.tracks` also gives `source_id`.
- **The app:** a YouTube Music page in the sidebar (search, play, Download, "In your library"), and Edit Details… on a song's right-click menu.
- 2026-10-01, late: the YouTube Music page has "Show 25 More" (the same search with a bigger limit; `search.ytmusic` now allows up to 100), and Recently Added is no longer capped at 200. The owner's design for the sidebar and Settings is written up in `docs/roadmap/0.2-app-layout.md`.
- **Not checked in the running app.** The owner asked for the app not to be opened and the Mac left idle (other work needed it), so these screens are compiled and the logic under them is tested, but nobody has clicked through them yet. To check: playing a search result (AVPlayer with YouTube's address), the Download button, and the Edit Details sheet.

2026-10-01, 5 pm. The owner's layout for the sidebar and Settings (`docs/roadmap/0.2-app-layout.md`), and a fix.

- **Fix: a song played from YouTube Music showed twice its length and went silent halfway.** Apple's player reads YouTube's format-140 stream as twice as long as the song. The app now trusts the length YouTube Music gives: the slider uses it, and the song ends there and the next one starts. Downloaded files aren't affected (yt-dlp repairs the container when it saves one).
- **Sidebar, to the owner's layout:** Library (with a "+" to add entries back, and "Remove from Sidebar" on each entry), Discover (What's New and Find as "coming" pages, Search YouTube Music, Downloads), Playlists (with a "+"), and a cog wheel for Settings. The owner's choice of Library entries is remembered.
- **Settings window** (the cog, or ⌘,):
  - *General:* downloaded songs go to **Discover Downloads** (the default: they stay out of the main library's lists) or **All Library**. This is parked Fix A-2 as a switch, done in the app: no file moves either way. The library folder is shown here too.
  - *Quality:* 128 kbps (256 is shown as coming), and **downloads per day**.
  - *Accounts:* YouTube, Apple Music and Spotify, each shown as coming.
  - *Lyrics:* **Find Missing Lyrics**, which runs the engine's `lyrics --missing` plan with a progress bar and a tally at the end.
- **Engine:** the daily download cap's default is now **250** (was 300), and it can't be set above 300, whatever `config.json` says. RPC: `settings.get` and `settings.set` (`daily_cap`). `library.tracks` also gives `source`, which is how the app knows a download.
- Fix, same day: the app opened only its Settings window, and quit when that was closed. The Settings scene had been put first, and the first scene is the one opened at launch. The main window is first again.
- Fix, same day: nothing under Library could be clicked. The rows were built by looping over their saved names, so the list took each row to be a piece of text, not a sidebar entry. They're built from the entries themselves now.
- The sidebar regrouped as the owner asked: Library, Media (Local Visualizer, YouTube Music), Discover (What's New, Find, Downloads), Playlists. Each group folds away and remembers it.
- Noted, not built: lyrics for a song being played from YouTube Music (see the layout plan).
- Not checked in the running app by Claude (the owner asked for the app not to be opened); the owner is trying each build themselves.

2026-10-01, 5:30 pm. **Fix A-1: the app feels right** (the owner's three notes). Measured before and after; the numbers are in `docs/KNOWN-ISSUES.md`.

- **Clicks and scrolling:** table rows have one fixed height (`FixedRows`), which removed the freeze on every click. A list's rows are worked out off the main thread, and only when its songs, the search, the sort or the play counts change, never because of a click. Covers load four at a time, give up their turn when their row scrolls away, and small ones are kept in the app's own cache folder so each is cut down from full size once.
- **Lyrics** no longer jump: every line keeps one size and weight, the line being sung is shown by brightness alone, and the view makes one smooth scroll per line.
- **Remembering where you were:** each page opened from the sidebar is kept, hidden, when another is chosen, so it comes back scrolled to the same place with the same selection and sort. What was opened inside a page (an album, an artist) is remembered page by page. The app reopens on the section it was closed on.
- Each list now has its own heading with Play and Shuffle, instead of buttons in the window's toolbar.
- `MUSICORG_STALLS=1` turns on a stall detector (`StallWatch`) that prints every freeze of 100 ms or more.
- The loading screen explains itself after 6 seconds (a possible macOS permission prompt, or new songs being read).

2026-10-01, 6 pm. The owner's notes on search and the lyrics panel, and signing.

- **Search is a button:** the search field is gone from every page's toolbar. A magnifying glass beside the sidebar button (or ⌘F) opens a search bar under the toolbar; Done or Esc closes it and clears the search. The YouTube Music page keeps its own search box.
- **The lyrics panel is no longer a fixture.** With lyrics switched on (the bubble button), the panel beside the library appears only while a song is on and its lyrics were found, and goes when there's nothing to show. It never appears beside Local Visualizer or the full-size now-playing screen, which show the lyrics themselves.
- **Lyrics for a song played from YouTube Music:** `lyrics.find` (RPC) looks them up the same way as for library songs (LRCLIB, then YouTube Music, with the length and version checks) and saves nothing. The panel appears when they arrive.
- **Signing:** `scripts/build_app.sh` signs with a certificate named "Music Organizer Dev" when the owner has made one (`docs/SIGNING.md`), so macOS remembers which folders the app may use instead of asking after every build. The owner made it on 2026-10-01; the next start was not held up.
- Checked in the running app: the magnifying glass and search bar, the panel staying away with nothing playing, a YouTube song bringing up timed lyrics, and Local Visualizer with no second lyrics panel.

2026-10-01, a little later. Two faults the owner found in the day's work, both from Fix A-1's own changes.

- **The YouTube results were squashed together.** `FixedRows` took the first table it came across, which could be another page's list, and gave it the song table's row height. It now only touches a table with several columns that fills exactly the space the helper fills.
- **Switching pages, and the lyrics panel opening, froze the app** (0.3 to 0.8 s per switch, measured). Three causes, found by profiling:
  - Pages kept alive were hidden with opacity 0 and reordered with zIndex. For an invisible view SwiftUI takes its AppKit views out of the window and puts them all back when it shows again, and a change of order does the same. Hidden pages are now moved far out of sight instead, and never reordered.
  - The lyrics panel was an inspector that slid in, making the song table lay itself out for every frame of the slide. It's now a plain column placed in one step.
  - Local Visualizer was rebuilt on every visit and blurred its backdrop at full window size. It's now kept like the other pages, and blurs a tiny picture before stretching it.
- Measured after: six switches between open pages gave one stall of 104 ms (before: a stall of 126 to 415 ms on every switch). With a song on, where the lyrics panel comes and goes and the table changes width, a switch still stalls for 120 to 185 ms. The first visit to a page still takes 0.3 to 0.5 s while its table is built.

2026-10-01, 6:20 pm. Lyrics tools in Edit Details, and columns the owner chooses (the rest of parked Fix A-3's list).

- **Find Timed Lyrics** (Edit Details): looks the song up by the title, artist and album typed in the sheet (`lyrics.find`: LRCLIB, then YouTube Music) and fills the lyrics box with what it finds. Nothing is saved until Save.
- **Sync by Tapping** (Edit Details): the song plays from the start, and a tap or the space bar as each line begins records its time (0.15 s is taken off each tap, for the ear-to-hand delay). "Back One Line" and "Start Again" correct mistakes. The timed text goes back to the lyrics box, to be saved like any other lyrics.
- **Columns:** a Columns menu on every song list shows or hides Artist, Album, Year, Genre, Quality, Added, Plays and Time (Genre, Quality and Added start hidden). Columns can also be dragged into another order. The choice is kept, and is the same for every list.
- Sync by Tapping has **−5 s** and **+5 s** buttons (owner, after trying it). Going back clears the lines tapped after the new position, so they're tapped again.
- Not checked in the running app beyond "the lists still draw": Claude's tools can't open menus or right-click, so the Columns menu, Find Timed Lyrics and Sync by Tapping are for the owner to try.

2026-10-01, 7:30 pm. **A song's video in the app's own player**, and a song that wouldn't start.

- **Fix: a YouTube song sat at 0:00, looking as if it was playing** (the owner's "Work Out"; other songs played, and so did this one a while later). YouTube now and then hands out an address it then refuses (HTTP 403; the engine's log shows the same on a download that worked at the second try). Apple's player doesn't announce an address it couldn't open, and the app wasn't watching for it.
  - The app now watches each item. When one fails, it asks for a fresh address and carries on from the same place, twice at most. After that it says so in the player bar ("can't be played from YouTube right now"), shows the play button again, and Play asks afresh.
  - The player bar shows its spinner while a song has stopped to wait for more of itself.
  - Checked by spoiling addresses on purpose (`MUSICORG_SPOIL=<n>` when starting the app: the first n addresses are made into ones YouTube refuses): one spoiled, the song starts by itself; three spoiled, the message; then Play, and it plays.
- **The song's official video, on the Local Visualizer** (and the full-window screen): a Cover / Video switch, and a menu of picture sizes like YouTube's (Best, then 1080p down to 144p; the choice is remembered).
  - Engine: `youtube.find_video(title, artist)` (one "videos" search on YouTube Music, kept for 30 days) and `youtube.video(video_id)` (the video's sound and its picture in each size the app can show, one yt-dlp look-up, nothing downloaded). RPC: `youtube.video`.
  - **Only the artist's own video counts:** one YouTube Music marks as official (`MUSIC_VIDEO_TYPE_OMV`), with the same title, the same version (a remix isn't the original; a remaster is) and an artist in common ("The" doesn't count: the library says "Notorious B.I.G."). Other people's uploads are never used, so many songs have no video, and the app says so and plays the song as usual.
  - YouTube serves picture and sound apart. The app joins one picture stream to the format-140 sound as a single item (off the main thread), so play, pause and seek act on both and they can't drift.
  - **While Video is on, the sound is the video's own**, not the library's file: a music video is often a different cut (an intro, a scene in the middle). It's off again each time the app starts.
  - A video as long as the song (within 2 seconds) keeps the song's place when switching, and its timed lyrics. Any other starts at its beginning, and the lyrics are shown without a lit line, with a note saying why.
  - Finding a video takes YouTube 5 to 12 seconds the first time. Turning Video on mid-song, the sound carries on until the picture is ready. With Video already on, the next song's video is looked for while the current one plays.
  - Not possible: above 1080p (YouTube's bigger pictures are VP9 or AV1, which Apple's player can't show here).
- `scripts/record_ytm.py` records "videos" searches too. ytmusicapi 1.12.3: a watch playlist's track has no `counterpart` (the song's video) without a sign-in, so the video is found by searching.
- **Looked at in the running app** (Claude, with the owner's library): a song from the YouTube page and three library songs; Video on mid-song and from the start; a song with no official video; 1080p and 360p; pause, a jump to 2:34, back to Cover; the retry and the give-up message. **Not checked: the sound itself.** Nobody has listened yet; the player reports it playing.
- Dates corrected: entries here and in the other notes that were dated 2 and 3 October were all written on 1 October.
- Fix (a test only): one queue test pretended it was 2 am on 1 October 2026 and paused its queue "until 8 am", then asked the real clock whether the pause still held. It began failing when that morning passed. Its pretend clock is now in the year 2100.
- Not built: saving a video (MP4). The owner has said where saved videos should show; it needs a change to rule 6 and the library contract first (`docs/KNOWN-ISSUES.md` → "Needs the owner's decision").

2026-10-01, 8:30 pm. Full screen for the video, and an "E" the owner can put right.

- **Full Screen** for a song's video: a button beside the picture-size menu, or a double click on the picture. The video takes the whole window and the window goes to macOS's full screen; play, pause, next, the position slider, volume and the size menu come up when the mouse moves and go away after three seconds; Esc or a double click brings the app back.
  - **Fit to Screen was built and taken out again the same evening**, at the owner's word: the video beside the lyrics was fine as it was.
  - Checked in the running app only as far as: the video filled the window and the controls hid themselves. Claude's test ran with the app in the background, where the window isn't sent to macOS's full screen, and then the owner said to stop. **Not checked: the real full screen, Esc, and the controls.**
- **Edit Details has an Explicit tick box** (`plan.create` kind `edit` takes `explicit`: true or false). The owner found a song marked "E" whose audio is the censored version. The mark on a rip is copied from the match on YouTube Music (which lists the explicit version first, the owner's own rule), and the engine can't hear which edit the owner's file is. Tested in the engine; not tried in the running app.
- **The owner's decisions on saving a video (MP4):** rule 6 may be changed to allow it, and the size saved is the one chosen in the player. Still to settle before it's built: `docs/KNOWN-ISSUES.md`.

2026-10-01, 9:15 pm. **Saving a video**, and a daily limit that can go to 500.

- **Rule 6 changed, with the owner's OK** (`CLAUDE.md`, and the contract's sections 1 to 3 and 6): a saved video is the one other thing a download may be. One H.264 picture stream, at the size chosen in the player (144p to 1080p), joined by ffmpeg to the format-140 sound without converting either, as an MP4. No fallback: a size YouTube doesn't offer that way goes to review.
- **Where a video lives: `Music/Videos/<Artist>/<Title>.mp4`** (the owner: "a separate folder of videos").
  - Deviation, for the owner to know: the folder is inside `Music/`, not beside it. Every file operation, guard, journal entry and undo in `fileops` works on `Music/`; a second top-level folder would mean changing that safety code and all its tests. Inside `Music/`, a video is covered by all of it from the first day. A top-level `Videos/` can still be done later as its own step.
  - A video is recognised by where it is. An artist who happens to be called "Videos" gets the folder `Videos (artist)`.
- **Engine:**
  - `youtube.download_video(video_id, dest, height=…, fps=…)`: yt-dlp fetches exactly that picture and format 140 and joins them (its own merger: a repackage). Queue only, like a song.
  - Plan kind `download` takes `videos` beside `video_ids` (`musicorg plan download --video ID:HEIGHT`). A video counts as **one download** for the pace and the daily limit (owner: songs and videos share the limit).
  - Checks before anything is kept: H.264 at the height asked for, AAC sound from format 140 at 100 kbps or more, the length YouTube Music gave within 2 s. Otherwise `video_format_unavailable` (a new review reason) or `duration_mismatch`, and nothing is kept.
  - Tags as for a song (title, artists, the first artist as album artist, source, id), with the video's own picture embedded as its cover. No album, no lyrics, no `cover.jpg`: those belong to the song, which is often a different cut.
  - `tags`: `.mp4` is read and written; `probe` reports a picture stream's codec and height (a song's embedded cover isn't one); the "nothing changed" hash of a video covers its picture data as well as its decoded sound.
  - The library scan (and so an index rebuild) finds `Music/Videos/`; an `.mp4` in a source folder is still never taken for a rip. Undo, tidy and Edit Details keep a video in `Videos/`; the lyrics and cover plans leave videos out.
  - `library.tracks`: `video` and `height`.
  - Fix found on the way: a track looked up by its id never had a picture, because a watch playlist calls it `thumbnail` and a search `thumbnails` (ytmusicapi 1.12.3).
- **The app:**
  - **Save Video** beside the picture-size menu on the Local Visualizer: saves the video that's playing at the size that's showing. It then says "Saved".
  - Saved videos are listed with the songs under **Discover → Downloads** (a film mark beside the title). With Settings → General on "All Library" they're listed under **Library → Videos** instead of among the songs. Videos never appear in Songs, Albums, Artists, Recently Added or Most Played.
  - A saved video plays with its picture on the Local Visualizer (Cover hides it; Full Screen works). Nothing comes from YouTube for it.
  - The sidebar shows the new Videos entry once without being asked; removed, it stays removed.
- **Settings → Quality, downloads per day:** a stepper in steps of 50, from 50 to **500** (it was a typed number, 300 at most). 250 stays the standard. Above 250 the window warns in orange that there's a high risk of YouTube refusing this Mac for some hours. Engine: `MAX_DAILY_CAP` is 500.
- **Checked for real:** one video (144p, 7 MB) saved into a scratch library from YouTube in 9 seconds, with the right streams, tags and cover; then undone. Apple's player, on its own, read that file's cover and played its picture, at the right length. **Not checked in the running app:** Save Video, the Videos list, playing a saved video there, and the stepper. The owner had the app open and had said to stop driving it earlier this evening, so these are for the owner to try.
- Not built: videos in the YouTube Music search page (a Songs / Videos switch was suggested; the owner hasn't said), and moving one download into the library by hand.

2026-10-01, 9:30 pm. The owner's notes after trying video and Save Video.

- **Fix: a remix got the original's video.** The owner's rip "Black Out Days R.mp3" ("R" is the owner's mark for a remix) is in the library as an unconfirmed copy whose title tag is the plain "Black Out Days", so the app asked for the plain song's video. The same for "Bones [Epic Remix]" and "Bad Habits (Leahy & Mack Remix)".
  - `youtube.video` takes the library song's `path`. The engine reads the song's version from its version tag and from the name of the rip it was copied from (`browse.version_tokens`), and `youtube.find_video(…, versions=…)` uses it: a remix only matches a video of that same remix, and a remix by nobody in particular matches none. The words of a known remix go into the search.
- **Fix: Save Video sat waiting for minutes.** It was queued behind a "Find Missing Lyrics" run of 1,098 songs started three minutes earlier: the queue ran jobs strictly oldest first. Now what the owner asks for one at a time and waits on (plan kinds `download` and `edit`) runs before the batches that work through the whole library. No change to `queue.sqlite`'s shape.
- **With Video on, a song starts at once** and its video takes over when it has loaded (owner: no waiting in silence). A video as long as the song takes over at the same place; any other starts from its beginning, as before.
- **"Cover" is now "Song"** on the Song / Video switch.
- **Leaving Full Screen:** Esc didn't work (it relied on an unseen button's shortcut). Esc is now caught by the app itself whenever the video has the screen, and the app, not the view, remembers that it put the window into macOS's full screen, so it reliably takes it out again. The Leave Full Screen button is bigger. **Not checked: Claude can't try full screen without taking over the owner's screen, and the owner was using the app.**
- **Download Song Too**, beside Save Video, for a song being played from YouTube Music: it downloads the song itself (sound only, with its album details), as the Download button on the YouTube Music page does. A song already in the library doesn't show it.
- Noted, not built (the owner's question): swapping a library song's audio for YouTube Music's (`docs/roadmap/0.2-app-layout.md`).

2026-10-01, 9:45 pm. Downloads you can see, move and try again; Columns in the menu bar.

- **A download shows at the top of Discover → Downloads from the moment it's asked for** (the owner's design): its name, song or video, and a bar that keeps moving while it waits its turn or downloads. When it arrives it becomes a row in the list below. One that ended without the song stays, in red, with **Try Again** and an **✕** to take it off the list; an ✕ on one still waiting cancels it before it starts.
  - The list is the engine's own (`queue.downloads`: the unfinished jobs of plan kind `download`), so it's still right after the app is closed and opened, and switching songs can't lose a download. `queue.dismiss` takes one off (it becomes `cancelled`); one that's downloading that moment is refused.
  - The bar doesn't show how far along a download is: the engine doesn't report that yet.
  - Download, Save Video and Download Song Too now return at once and read their state from this list.
- **Moving a download into the main library:** drag it from Downloads onto any Library entry in the sidebar, or right-click → **Move to Library** (and **Move Back to Downloads**, or drag it back onto Downloads). A moved song joins Songs, Albums, Artists and the rest; a moved video joins Library → Videos. No file moves: `listening.move` keeps the owner's choice in `state.json` (`"library"`), by `MUSICORG_ID`.
  - Only downloaded rows can be dragged; every other row is as it was.
- **Columns is in the menu bar: View → Columns.** It's off the top of each list.
- **Not checked in the running app** (the owner was using it): the Downloads strip, Try Again and ✕, dragging, and the View menu. The engine side of each is tested.

2026-10-01, 10:40 pm. Lyrics timed to the video, and a Swap Audio button with nothing behind it yet.

- **Lyrics that follow the video** (owner: most videos showed no timed lyrics). When a video takes over and the song's own timed lyrics don't fit it (the video has an intro or a scene), or the song has none, the app asks `lyrics.find` for lyrics by the **video's** length and id. LRCLIB keeps lyrics by length, and people time them to the music video's cut too; YouTube Music's own lyrics are asked second. Found, they replace the song's while the video plays, and the song's own come back with Song. Nothing is saved.
  - Checked against four real videos before building: all four had lyrics timed to the video's length (the first line at 0:09 for a video with a 9-second intro, at 0:36 for one with a long opening scene). YouTube Music alone had only untimed words for three of them. A fifth look-up got "try again later" from LRCLIB, which was busy with the owner's lyrics run.
  - The note "the lyrics aren't timed" now shows only when none were found for the video.
  - A saved video, which keeps no lyrics of its own, gets its lyrics the same way when it's played.
- **Swap Audio…** is on a song's right-click menu (owner: "build a swap button, its implementation comes later"). It explains what it will do and changes nothing. What must be true before a file is replaced is the owner's to decide; the questions are in `docs/roadmap/0.2-app-layout.md`.
- Not checked in the running app.

2026-10-01, 11 pm. A video's picture stopped while the song went on (the owner's report): not explained yet.

- **Seen:** in the owner's running app, a 1080p video sat on one frame for at least 12 seconds while the time and the lyrics moved on. The player reported nothing wrong. Minutes later another video in the same app was moving normally.
- **Ruled out:**
  - The video itself and its size: the same video at 1080p played outside the app three times without a gap (100 s, 75 s from the middle, 250 s straight), a new frame at every check.
  - YouTube's speed limit: a continuous request (how Apple's player asks) is served at about twice the stream's average rate (6.3 Mbit/s for a 3.2 Mbit/s stream), while 10 MB pieces arrive at over 300 Mbit/s. But this video's busiest 5 seconds need 1.9 times its average, so it never runs dry at that limit, from any starting point.
  - Two picture layers on one player (only the newest draws): the screen underneath was the Downloads list, which has none.
  - A busy Mac: the processor was 60% idle.
- **Not ruled out:** the picture layer losing hold after the window was covered or the app left and returned to; a hiccup in the picture stream's connection that Apple's player doesn't recover from when picture and sound are two joined streams.
- **Added, so it mends itself and says what happened:** the player counts new frames while a video plays. None for 3 seconds of playing, and it fetches the picture afresh from where the song is (a seek in place) and has the layer take hold of the player again; at most three times a video. Each time it writes a line to `~/Library/Caches/org.musicorganizer.app/player.log` (the app's own cache folder, not the library). If the picture freezes and that file has no line, frames were still arriving and the fault is in the layer.
  - The rule was tried against two minutes of real playback with a pause and three jumps: the longest gap between frames was 0.17 s, and it never fired.
- Also seen: YouTube Music's "official video" for one song is partly an upright phone clip. It's the artist's own upload, so the rule accepts it.

2026-10-01, midnight. **Discover, started** (the owner: "I'd like you to start working on Discover"). It was v0.4 and on `CLAUDE.md`'s "Not yet" list; the list now says it was started early, and what's still not built.

- **Engine: `discover` (new module) and `discover.suggest`.** Seeds in (the whole library, most played, the top artist, a playlist, a named artist, a genre; up to 8 together) and a count (1 to 500); ranked picks out, each with a one-line "why". Read-only: nothing is downloaded and no file changes.
  - Picks come from YouTube Music's **radios**: a song's radio (about 50 songs like it) for seeds made of the owner's songs, an artist's radio for a named artist (their own songs and similar bands'), and more radios started from that artist's songs when more is wanted.
  - Ranking: a song on several of the radios first, then artists the owner has a lot of (a small push, at most 16 places), then YouTube Music's own order. One artist gets a share of the page, not all of it, unless they were asked for by name.
  - Never suggested: a song already in the library (by YouTube id, or by title, version and artist, which also reads the name of the rip a copy came from), a candidate rejected in review, a song waiting in the download queue, the same song twice, and anything that isn't official audio.
  - It asks for 4 to 24 radios through the rate limiter. Measured on the owner's library, read-only: 7 to 10 s for 10 to 20 picks (4 radios), 14 s for 100 picks (9 radios). 500 picks wasn't timed; at 24 radios it should be under a minute. Answers are kept in the index's search cache (a radio for a week), so the same request again is immediate. `discover.progress` notes say how far along it is.
  - `shuffle` chooses which of the owner's songs the radios start from. The app sends the date, so a day's picks stay put until "Different Songs" is clicked.
- **`plan.create` kind `download` takes `candidates`**: tracks the engine gave out a moment ago. They aren't looked up on YouTube Music again, so a plan for 50 picks is made at once instead of in over a minute.
- **The app: Discover → What's New and Discover → Find** replace their "coming" pages.
  - What's New asks by itself the first time it's opened: 10, 50 or 100 songs picked from the whole library.
  - Find: start from an artist (or several, with commas), a genre, a playlist, the most played songs, the top artist or the whole library; 10, 50, 100, 500 or any number.
  - Both show a grid of cards: cover, title, artist, the "why" line, length. Click the cover to play it in the app (nothing is saved), Download for one song, a tick on each card and Download Selected for several. Several at once is a batch, so the plan is shown first (how many, about how long, how many days if it's over the daily limit) and nothing is queued until Download is clicked. Each card has an "open in" menu: a search for the song on Spotify, Apple Music or SoundCloud (plain links, no account or key).
  - Downloads land where the app's downloads already do: `Music/`, listed under Discover → Downloads.
- **Checked against the installed ytmusicapi (1.12.3), as `CLAUDE.md` asks:**
  - `get_watch_playlist(videoId, radio=True)`: 50 tracks, the song itself first, nearly all official audio, with `length` and no `isExplicit`.
  - An "artists" search result carries a `radioId`; `get_watch_playlist(playlistId=radioId)` gives about 100 tracks, a quarter of them the artist's own.
  - **`get_mood_playlists` fails on every genre page** (a KeyError inside ytmusicapi; the mood pages work). So the plan's "YouTube Music's genre lists" can't be used as written.
  - Genre playlists found by searching "featured_playlists" work, but the search returns all sorts in a different order each time ("Aussie Hip-Hop Golds", "00s German Rap Essentials"), and their tracks are mostly music videos, not official audio.
- **Deviations from `docs/roadmap/0.4-discover.md`:**
  - A genre starts from **the owner's own songs tagged with that genre** (the plan left "YouTube's lists, the library's tags, or both" open). Only with fewer than four of those does it use a YouTube Music playlist, choosing one named for the genre's hits where there is one. "Hip hop" and "rap" count as one genre, as do "R&B" and "rnb".
  - No `discover.enqueue`: picks are downloaded with the existing `plan.create` and `plan.apply`, so there's still one way in to the queue.
  - "Open in" is a menu of three names, not a row of logos, and there's no fourth service yet.
  - The time estimate comes with the download plan, not with the picks.
- **Not built:** the guided mode, the `Discovered/<Genre>/<Month>/` folder and its tag (a library-contract change, with the owner's open question on its layout), Last.fm, combining different kinds of seed in the app (the engine accepts it), and a way to say "not this one" to a pick.
- **A developer's check for the app:** `MUSICORG_SNAPSHOT=<folder>` runs the app unseen (no Dock icon, an invisible window) and saves pictures of its window, so a page can be looked at without touching the copy of the app in use. What's New and Find were looked at this way on a scratch library. **Not tried: any click** (Play, Download, the tick boxes, Download Selected and its confirmation, Find with results, the "open in" menu).
- `scripts/record_ytm.py` records the new requests (`radio`, `artist`, `genre`). `youtube._artist_key` is now `youtube.artist_key`.

2026-10-02, after midnight. **A download can be deleted** (the owner: "downloaded songs in discover should be able to be deleted").

- **Engine: a new plan kind, `remove`** (added to the enums). `plan.create` with `paths` plans it, and its job sends each file to the system Trash (`fileops.trash`, as the contract says deletes must), with its `.lrc`, and the album's `cover.jpg` when it was the album's last song; folders left empty are removed. The song also leaves the owner's favourites, play counts and playlists (`listening.forget`).
  - **Only a download can go this way:** a file tagged `MUSICORG_SOURCE=youtube_music` with no `MUSICORG_MATCH` (no rip behind it), which can be downloaded again. A song from the owner's own rips is refused, at the plan and again in the job.
  - Journaled like every change. Undo can't bring a file back from the Trash; it says so (`manual`), as it does for every trash.
  - It runs ahead of the bulk batches, like a download or an edit.
- **The app:** right-click a download (Discover → Downloads, or wherever it's listed) → **Delete…**, or "Delete N Downloads…" for several. It asks first. A download that's playing stops. The menu item isn't offered for the owner's own songs.
- Not tried in the running app (the owner asked for changes only, with the app left alone).

2026-10-02, 1 am. **Lyrics timed to the video** (the owner: "video/lyrics are completely off for 21 Questions… even if the video is 2-3 seconds longer. There has to be a method to make it effective"). Three lines of research ran side by side (the owner asked for two more agents): lining the recordings up by their sound, the video's own captions, and why today's way failed.

- **Why they were off.** A music video is rarely the album track second for second, and the app had two ways of coping, both wrong:
  - A video within 2 s of the song's length was taken to be the song, and the song's lyrics shown as they were. A video 2 s longer usually has 2 s of picture before the music, so every line was 2 s early.
  - For any other video, LRCLIB was asked for a record of the video's length. Measured on 13 official videos: it always has one, and of the 9 videos that really differ from their song it was wrong on 5 (by 2 s, 8 then 24 s, 9 then 29 s, 75 s). Most records "of the video's length" are the album's lyrics filed under another length.
- **What the videos really do** (measured from their sound): *21 Questions* starts the song 31.0 s in. *Work Out* 5.2 s in. *Lost Boy* leaves out 7.7 s and then 15.5 s of the song, so it needs three different shifts. *Breezeblocks* is the album track to within 0.14 s.
- **New: `videolyrics` (engine) and `lyrics.for_video` (RPC).** The song's lyrics, timed to the video that's playing. Tried in this order of trust:
  1. **The sound.** The video's audio and the song's are fingerprinted (Chromaprint) and lined up: for each moment of the video, which moment of the song is playing, or none. Each lyric line is moved to where it's sung in the video. A line the video leaves out is dropped; a part it plays twice has its lines twice; a scene with none of the song clears the line. Checked against the waveforms at 30 places on five real pairs: within 6 ms everywhere (lyrics need about 300). A cut point is found to about ±0.3 s. All 60 deliberately wrong pairings were refused.
  2. **The captions.** Most official videos carry the label's captions, one cue per sung line with the time it's sung in the video (10 of 13 tested; 1 more had usable automatic captions). The song's lyric lines are found in the captions and take their times. This times a video whose sound is another take of the song, and it checks way 1: where five or more lines in a row disagree with the label's captions, the song's own lyrics are badly timed there (one lyric file in circulation for *21 Questions* has its last forty lines crammed into fourteen seconds) and the captions place those lines.
  3. For a song with no timed lyrics at all: the label's captions themselves.
  4. A record on LRCLIB of the video's length, only if its times differ from the song's own.
  - When none works, the app shows the words with no line lit up, and says so. It never shows lines it knows are out of step: while the engine is working, and for a video it can't time, nothing is highlighted.
- **Cost.** For a library song: the video's sound is fetched once (about 4 MB, 0.2 s), fingerprinted (0.3 s) and lined up (0.05 s); with the captions check it took 2.4 s end to end the first time, measured through `musicorg serve` straight after the look-up the app makes to play the video. For a song played from YouTube Music the song's sound is fetched too: 8 to 14 s measured on its own; in the app it should be less, since the engine reuses the look-ups the app has just made, but that wasn't measured. The answer is kept in the index for 30 days, the fingerprints too, so the same video is instant afterwards.
- **YouTube now and then refuses an address it has just given out** (HTTP 403; seen twice in about thirty fetches). The fetch is tried once more with a fresh address, and a miss caused by YouTube not answering isn't remembered, so the next play tries again.
- **`youtube`** gained `sources` (a video's sound and caption tracks, in one look-up, or none if the video was asked about in the last five minutes, as it is when it's playing), `fetch_audio` and `fetch_captions`. **`fingerprint.fingerprint_bytes`** fingerprints audio held in memory (fpcalc reads it from its standard input), so nothing is written anywhere.
  - Found on the way: YouTube serves a stream asked for plainly at twice the speed it plays at (two minutes for a four-minute song). Asked for as a byte range in the address it takes 0.2 s; one range over about 10 MB is slowed again, so it's fetched in pieces of 8 MB.
- **The app:** the Local Visualizer asks `lyrics.for_video` when a video starts (and for a saved video played from the library). "Timing the lyrics to this video…" shows while it works.
- **To confirm with the owner (rule 8):** the engine now reads a video's whole audio from YouTube to fingerprint it. Nothing is saved, it goes through `musicorg.youtube` and the rate limiter, and it's once per video. It's the same bytes the app fetches to play the video. Rule 8 says "downloads happen only through the throttled queue"; this was built on the reading that a download is a file kept in the library. If the owner reads the rule the other way, way 1 has to go and the captions (way 2) remain.
- **What it can't do:** a video with its own mix or a live take and no captions; a video that plays the song 2 % or more fast or slow (none of the real ones did); a line that starts within about a second of a cut can land on the wrong side of it.
- Not tried in the running app (the owner asked for changes only). Tried for real through `musicorg serve` on a scratch library: the four songs above, as a library song and as songs played from YouTube Music.

2026-10-02, morning. **Karaoke: lyrics are lined up with a video only when asked** (the owner: timing every video that's played would spend requests on YouTube for videos nobody is reading the lyrics of; "next to download video… a Karaoke… when clicked it does anything possible").

- **By itself, the app now asks YouTube nothing for a video's lyrics.** When a video starts, `lyrics.for_video` is called without `full`: it gives lyrics already timed to that video (by Karaoke, on an earlier play), or a record on LRCLIB of the video's length that passes the check (its times differ from the song's own), or nothing. With nothing, it's as it was before last night: the song's lyrics stay, lit up if the video is as long as the song (within 2 s), unlit if not.
- **The Karaoke button** (Local Visualizer, beside Save Video, for a video from YouTube and for a saved one) asks with `full: true`: the sound line-up and the captions, as built last night. It shows "Lining up the lyrics…", then "Lyrics in time". Once done for a video the answer is kept for 30 days, so that video is in time by itself afterwards.
- One difference from before last night stays: the LRCLIB record "of the video's length" is no longer trusted on its length alone, since it was wrong on 5 of 9 videos.
- **Rule 8** (reading a video's audio to fingerprint it): the owner put the decision off. It now happens only when Karaoke is clicked.
- For the record: timing a video never counted against the daily download limit (that counts songs and videos saved through the queue). What it cost was 1 to 3 requests to YouTube the first time a video was played.
- Not tried in the running app: the owner's Mac was busy with another test, so only the engine's tests for this (34) and one build were run here, at low priority; the full suite ran in CI.

2026-10-02, 9 am. **Discover's guided mode** ("What music would you like today?": section 6 of `docs/roadmap/0.4-discover.md`).

- **The app:** Find has a **Guide Me…** button. It asks three things, one at a time, as a short conversation: what music (type a kind of music or an artist, or pick Surprise Me, What I Play Most, or one of six genres), just play it or download it, and how many (10, 25, 50, 100, 250, or any number to 500).
  - **Just play it** plays the picks from YouTube Music as a play queue and saves nothing.
  - **Download it** finds the picks, then says what it found and what it will take ("I found 237 hip hop songs… It takes about 2½ hours, paced so YouTube doesn't refuse this Mac"; and over how many days, if it's more than the daily limit). Nothing is queued until **Start** is clicked.
  - Either way the picks are also on the Find page afterwards, as if its own boxes had been filled in.
- **It adds no logic of its own**, as the plan says: the same `discover.suggest`, the same plan and queue. The one new thing is in the engine:
  - **A new seed kind, `typed`** (added to the enums): whatever was typed, without saying whether it's a genre or an artist. It's a genre if the owner has four or more songs tagged with it, or it's one of about sixty names genres go by. Otherwise it's an artist, if YouTube Music has one of exactly that name (one request, kept for 30 days). Failing both, YouTube Music's own playlists are searched for it. The answer's `seeds` say what it turned out to be, which is how the guide can say "hip hop songs" or "songs by Linkin Park and artists like them".
  - The names come first on purpose: there are artists called "Jazz" and "Pop".
- **Wording:** the plan's script says "weighted towards the artists you listen to most". The engine's ranking leans to the artists the owner *has* most of, so the guide says that.
- **Not tried in the running app** (the owner's Mac was busy with another test): no click, and nothing was asked of YouTube. Here: the engine's Discover tests (31), the app's own tests (32) and two low-priority builds. The full suite ran in CI.

2026-10-02, 9:30 am. **Downloads grouped by genre** (the owner's sketch: each genre named on the left, a box of its songs beside it, each line "Song Name - Artist - Date Added"), **and song lists whose columns stay put**.

- **Discover → Downloads** is now laid out as the sketch: a label for each genre (with how many songs), and beside it a box listing that genre's downloads, newest first, each with its name, artist and the day it was added. The genre with the newest download comes first; songs with no genre are last, under "No genre yet". Double-click plays; right-click is the same menu as every list (Edit Details…, Move to Library, Delete…); a row can still be dragged onto Library. The strip of downloads on their way stays at the top.
  - **This answers the plan's open question** ("genre then month, or month then genre?"): genre, with the date beside each song and no month level.
  - It's how the page is laid out, not a folder: the files stay where downloads already go (`Music/`). The `Discovered/` folder of `docs/roadmap/0.4-discover.md` is still not built; whether it's still wanted is the owner's to say.
  - Genres written differently are one group: "Hip-Hop/Rap", "hip hop" and "Hip Hop" are all Hip Hop (the first genre a tag names, whatever its capitals and hyphens), named the way most of the songs spell it. "Rap" stays its own group, as in the sketch.
- **A download now gets a genre tag.** YouTube Music gives none, so until now every download had none.
  - A song found by Discover takes the genre it was found under: the genre that was asked for (spelled as the owner's own files spell it), or the genre tag of the owner's song whose radio it came from. `discover.suggest` gives each pick a `genre`, and it travels with the pick through `plan.create` into the tag.
  - Any other download (and a saved video) takes the genre the owner's own songs by that artist are tagged with, the commonest one; downloads' own genres don't count towards that, so a guess can't feed on guesses.
  - With neither, it has no genre, and Edit Details… can give it one. Downloads made before today have none unless given one by hand.
- **Song lists: the columns no longer move about** (the owner: "it gets larger, and smaller depending on the input, and if it's accessing the library for the first time in that session").
  - The cause, seen in a picture of the list: a table starts every column at its "ideal" width whatever room it has (at the usual window size the Time column was cut off at the edge), and only shares the room out again when something next changes size. With three stretchy columns (Title, Artist, Album) the result differed from one opening to the next.
  - Now every column but Title has a set width, and Title's starting width is worked out from the room there is, so the columns fit exactly from the first moment and are in the same places in every list. As the window changes, only Title takes up or gives back the room.
  - Lost by this: Artist and Album can't be dragged wider or narrower any more.
  - A first try (changing the title's width as the window changed) moved the headings and not the rows; it was caught in a picture and replaced.
- **Checked:** both pages were looked at with the hidden copy of the app on a scratch library (three pictures: Downloads; Songs at the usual width; Songs after widening the window). Nothing was clicked, and the owner's copy wasn't touched. `MUSICORG_SNAPSHOT_WIDTH` was added to the developer's snapshot mode for the widening.

2026-10-02, 9:45 am. **How far along a download is.**

- **Engine:** the queue counts the bytes of the download that's running and `queue.downloads` gives each running one a `progress` (0 to 1). A video is two files (its picture, then its sound): they're counted as one, with the sound's size guessed from the video's length until it starts, so the share doesn't jump back. It's kept in memory by the process running the queue, so it's known when the app runs the queue (as it does) and not when `musicorg queue run` is used beside it.
- **The app:** the strip at the top of Discover → Downloads fills a real bar and says "Downloading… 42%", then "Checking and naming it…" once it has all arrived. The Download and Save Video buttons say the same percentage wherever they are (the YouTube Music page, Discover's cards, the Local Visualizer). It's read once a second, as before.
- A song is about 4 MB and arrives in a second or two, so this mostly shows on videos (a 1080p video is about 100 MB).
- **Not tried with a real download** (nothing was asked of YouTube: the owner's Mac was busy). The engine's counting and the app's wording are tested; the bar itself hasn't been seen moving.

2026-10-02, 10 am. **Find: several starting points together** (the plan's "seeds can be combined, e.g. Road Trip limited to rock, or two artists together").

- **The app:** Find's "Start from" row has an **Add Another** button. Up to four starting points, each its own row ("Start from… and from…"), each of any kind: a playlist and a genre, a genre and an artist, the most played songs and a playlist. A row is taken away with its minus. Find waits until every row has something in it. Only the first row is remembered from one day to the next.
- **How they combine:** the engine already took several seeds. The radios are shared between them in turn, and a song that turns up for more than one comes first. So "Road Trip and rock" leans to songs found from both, rather than strictly keeping only rock: nothing says what genre a song on a radio is, so a strict limit isn't possible.
- **Engine:** a pick found from two different starting points now says which: "On the radio for both Road Trip and rock songs" (it said "2 of your starting points").
- **Checked:** the three-row layout was looked at with the hidden copy of the app (started with three rows for the picture, then put back). The engine's test covers a playlist and a genre together with made-up radios. Not clicked, and not tried against YouTube.

2026-10-02, afternoon. **Download Automatically** (the owner: "a button of automatically download 10/25/50/100/250 songs from specific genre etc… you could fall asleep after asking for the download and wake up to 250 new songs").

- **Find → Download Automatically**, a menu of 10, 25, 50, 100 and 250 beside Find. It finds that many songs from the starting points in the boxes and queues every one, with nothing more to click. The picks land on the Find page, and a note under the boxes says how many are on the way, about how long it takes, and what the daily limit does to them.
  - It's the same `discover.suggest`, `plan.create` (kind `download`) and `plan.apply` as before; the app just doesn't stop between them. The click on the number is the owner's yes, and the batch is planned and journaled like any other (rule 5).
  - Asked for again from the same starting points, it starts from other songs of the owner's.
- **The queue starts again by itself.** `musicorg serve` used to run the queue until it stopped and leave it: at the daily limit, or after YouTube refused us, the songs still waiting sat there until the app was opened again or something else was downloaded. Now a queue that stopped until a known time is started again at that time. The clock is looked at every 30 seconds rather than slept through, so a Mac that was asleep then carries on when it wakes; and never sooner than a minute after it stopped.
- `queue.status` also gives `daily_resume_at`: when the next download may start, while the daily limit is reached. The app shows it above the waiting downloads ("That's 250 downloads in 24 hours, your daily limit. The rest carry on by themselves from 3:10 pm."), and the same for a pause by YouTube.
- `queue.dismiss` takes `{ "waiting": true }` in place of a job: every download that hasn't started is cancelled at once (`queue.dismiss_waiting`). The one downloading carries on.
- **Discover → Downloads with hundreds on the way** lists the one downloading, the next three in line and the first two that didn't arrive, and counts the rest ("and 243 more waiting their turn"), with how many are on the way and **Cancel Waiting…** (it asks first). A handful are all listed, as before.
- Nothing new keeps the Mac awake: the queue already does (`caffeinate -i`, since step 09a). The screen may still turn off.
- Checked: engine tests for the three engine changes; the app's words and the shortened list in its Kit tests (39 pass); the Find page, the note in each of its states, and the Downloads page with twelve made-up downloads waiting (a paused scratch library, so nothing was fetched) looked at in the unseen snapshot. **Not clicked in the running app, and no real batch was downloaded by it.**
- Found on the way: a long line of text with `fixedSize` beside a `Spacer` in that note left the whole page undrawn in the snapshot (sidebar and player bar included). The note now gives its text the room there is and lets it wrap.

2026-10-02, 4 pm. **Song lists keep their roomy rows, and the daily limit has a counter** (the owner, with two pictures: "the spacious grid… is how it starts, as soon as anything is clicked it goes super condensed. I like the spacious one"; and "a number readily available, keeping track of the YouTube daily limit… 1/250… green… orange… red").

- **The rows.** A song list opened with rows 34 points high and dropped to 24 at the first click, squashed together with the covers cut off. Fix A-1 (2026-10-01) set the table's row height once, to stop it measuring every row; SwiftUI puts its own height back whenever the selection changes, and nothing set ours again. `FixedRows` now watches the table's height and puts it straight back. This is what the owner meant on 2026-10-02 by "the grid… gets larger and smaller depending on the input": the fix that day (set column widths) answered a different thing.
  - Reproduced before the fix and checked after it in the unseen copy of the app, with a row selected by a temporary line (removed).
- **The counter.** The bottom of the sidebar shows downloads in the last 24 hours against the limit: "12/250 downloads today", with a dot. Green under three fifths of the limit, orange from there, red from 85%. Resting the pointer on it says what counts.
  - It's the engine's own count (`queue.status`: `daily_count` and `daily_cap`), the same one the queue stops at, so everything that uses the limit is in it: songs, saved videos, Download Automatically, downloads run from the command line. Playing and searching don't use the limit and aren't counted.
  - Kept right by asking again when a different download starts (a download is counted as it starts), when the queue's worker starts or stops, when the limit is changed in Settings, and once a minute, since a download leaves the count when it's a day old and nothing announces that. The engine reads it from the library's queue file; YouTube isn't asked.
  - No engine change.
- Not clicked in the running app.

2026-10-02, evening. **Import Playlists, first part: a YouTube or YouTube Music playlist by its link** (the owner: "let's start working on the Spotify/Apple Music login, and that gathers their songs on playlists, and then they can press automatic download to use the 250/500 limit a day… YouTube, as well"). Imports were on `CLAUDE.md`'s "Not yet" list (v0.3); the owner's request is the go-ahead, as it was for Discover, and the list says so now.

- **What all three services need is built**, with YouTube as the first source because it needs nothing from the owner: no sign-in, no key.
  - `imports` (new engine module, read-only): `import.playlist` reads a playlist from its link (public or unlisted, up to 1,000 songs); `import.find` says what each song is to the owner: `owned`, `queued`, `found`, `unsure` or `not_found` (new enums, in `ENGINE_API.md`).
  - Finding uses the rule the owner's rips are matched by (`match.match_item`): only its certain answer is `found`; a likely one is `unsure`, with one line on why, and is downloaded only if ticked. A song the owner has costs no search, and nor does a playlist entry that's already official audio. A music video in the playlist is swapped for the song itself (same artist, title and version; a video's length says nothing about the song's).
  - `plan.create` kind `download` takes `playlist_id`: each song joins that playlist of the owner's as it arrives (`listening.add_to_playlist`). A playlist deleted meanwhile is simply not joined.
  - `discover`'s owned-songs helper is shared now (`Owned`, `is_there`) and knows each song's id, so songs the owner already has go into the playlist at once.
- **The app:** Discover → Import Playlists. Paste a link, Read Playlist; the songs are listed with what was found for each (double-click plays what was found, saving nothing); **Download Automatically (N)** makes a playlist of the same name, puts the songs already owned in it, and queues the rest with nothing more to click. They count towards the daily limit like any download, and the sidebar's counter shows it.
- **Checked against YouTube Music for real**, on the scratch library: a 62-song playlist read in one go, a made-up playlist id answered with the plain "set it to Unlisted or Public" error, and the page looked at in the unseen copy (59 found, 2 not sure, 1 not found). **Not clicked in the running app:** Download Automatically on this page was only run in the engine's tests, with stand-in downloads.
- Known limits: songs join the playlist in the order they arrive, after the ones already owned, not in the original order. An account's own lists (Liked Music) can't be read without signing in.
- **Not built: Spotify, Apple Music, and signing in to YouTube.** What each needs from the owner is in `docs/ROADMAP.md` (v0.3).

2026-10-03. **Import Playlists: Spotify, after signing in** (the owner: "I have a Spotify Premium acc"; that's what Spotify requires of whoever registers the app).

- **`spotify`** (new engine module): the only place Spotify is talked to, and only to read.
  - **Signing in** is Spotify's Authorization Code with PKCE flow. `account.sign_in` starts a listener on 127.0.0.1 (port 36463, for five minutes) and gives the app an address at accounts.spotify.com to open in the browser; the owner signs in *there*, so their password never passes through the app or the engine. Spotify sends the browser back with a one-time code, which is exchanged for a refresh token. There is no client secret.
  - **What it may do** (the scopes asked for): read the owner's playlists, the ones they collaborate on, and Liked Songs. Nothing that changes anything.
  - **What's kept:** the owner's app's Client ID and the refresh token, in `accounts.json` beside `config.json` in the app's settings folder, written atomically by `config.py` and readable by this user only. Never the access token, never in the library or the repo. An answer that doesn't carry the sign-in's own `state` is turned away and the sign-in carries on waiting.
  - **Reading:** `import.playlists` lists the account's playlists, Liked Songs first; `import.playlist` (source `spotify`) reads one, fifty songs a request, up to 3,000. A playlist of someone else's that's only followed is listed but marked unreadable: since February 2026 Spotify gives its songs to nobody but its owner and collaborators. Podcast episodes and local files are left out.
  - Its songs go through the same `import.find` as a YouTube playlist's; here each song not already owned costs a YouTube Music search.
  - Errors are plain: not signed in, the sign-in ran out, Spotify refused (with the two usual reasons: no Premium on the app's account, or this account missing from the app's User Management), slow down, the port in use. No token, code or address is ever logged or put in an error.
- **The app:** Settings → Accounts has a working Spotify section with the three steps (open Spotify's developer page, create an app with the Redirect URI shown and a Copy button, paste the Client ID), Sign In with Spotify…, and Sign Out. Discover → Import Playlists has a YouTube link / Spotify switch; Spotify shows the account's playlists to choose from. The app only ever opens an address that is https at accounts.spotify.com. Settings now remembers its tab, and "Open Settings…" on the Import page opens Accounts.
- **Rules touched, at the owner's request for sign-ins:** `CLAUDE.md` rule 3's line for `config.py` now names `accounts.json` beside `config.json` (the Secrets section already put logins in the app's settings folder).
- **Deviation from "record a fixture":** nobody had signed in when this was written, so there is no recorded Spotify answer. The tests stand in for Spotify with answers in the shapes its documentation gives (read on 2026-10-03, after the February 2026 changes), and both the new and the old field names are read (`items`/`tracks`, `item`/`track`). **Not tried against Spotify itself:** the first real sign-in is the owner's.
- Checked: 13 engine tests (the whole sign-in through a real listener on this computer, renewing, every refusal, playlists, songs, RPC) and the Kit's; the Import page's Spotify side and the Settings section looked at in the unseen copy, signed out.

2026-10-03, later. **Profiles** (the owner: "Account Name, will be local, not sign in or anything. A new account removes songs and downloads and everything from the current library, allowing for a new person's music to be downloaded. Moving back to the other account will bring back all the music, settings they had… I don't want to mix our music… and we can also make a kids account").

- **A profile is a name and a library folder of its own.** Nothing online and no password. The app was already built around one library folder (its music, downloads, playlists, favourites and play counts all live in it), so a profile is simply another one: switching starts the engine on the other folder. Nothing is copied, moved or deleted by a switch, and two people's files can't mix because they're never in the same folder.
- **The app:** Settings → Profiles lists them, with Switch, Rename…, Remove from the List… (the folder stays where it is) and New Profile… (a name, and a folder suggested beside the library in use: "Library" → "Library (C)"; the engine makes the new, empty library with `library.init`). With more than one profile the sidebar shows whose music it is. What was there before profiles becomes the first profile, named after this Mac's user, and can be renamed.
  - **Settings per profile:** the app's own settings (sidebar, columns, the Find and Import boxes, lyrics options…) are put away when a profile is left and brought back when it's returned to. A new profile starts from the app's defaults. Window places and the list of profiles itself are the app's, not a profile's.
  - A folder can't be two profiles' library: choosing one that's another profile's is refused, with why.
- **Engine:** two small things, so that profiles are really separate and the limit really is the limit.
  - **Sign-ins belong to a profile.** The app starts the engine with `MUSICORG_PROFILE=<profile id>`, and `accounts.json` keeps each profile's sign-ins apart (`{"profiles": {"<id>": {"spotify": …}}}`). One person's Spotify is never the next person's. From the command line, with no profile named, it's the first profile.
  - **The daily limit is one count for the whole computer.** It used to be counted per library, so two profiles could each have had the whole limit. Now every download's start time is also kept in `downloads.json` in the app's settings folder, and each library counts its own and everyone else's (`queue.recent_downloads`). The sidebar's counter shows that one count.
- **Differs from the family-mode plan** (`docs/roadmap/1.1-family-mode.md`, 2026-09-30), which had profiles sharing one set of music files and only changing what's visible. The owner asked for separate music, and that's what this is. A kids profile today is just a profile: the plan's parent PIN, clean-first rule and explicit filter aren't built.
- **Rules touched:** `CLAUDE.md` rule 3's line for `config.py` and rule 2's list now name `downloads.json` too.
- Checked: engine tests (a second profile can't see the first's sign-in; two libraries share the count); the Kit's profile tests; and, in the unseen copy of the app on scratch folders, a real profile made and switched to (an empty library, made by the engine), then back (the first profile's 17 songs, playlist and favourite all there). **Not clicked in the running app:** the Profiles tab's buttons and the New Profile sheet were looked at, not pressed.

2026-10-03, after trying the app. The owner sent a batch: a playing error, a new layout for Downloads, Settings regrouped, Show More on Discover, a mark for songs heard to the end, an Up Next for YouTube Music, a new layout under a playing video, and copying a playlist to another profile. Done in that order, one commit each.

- **YouTube's refusals in plain words.** An age-restricted song showed yt-dlp's own message, written for people at a command line ("Use --cookies-from-browser or --cookies… See https://github.com/…"). The engine now says why in a sentence: "It's age-restricted: YouTube only plays it to someone signed in as an adult, and signing in to YouTube isn't built yet." Likewise a private video, members-only, not in this country, not out yet, taken down for copyright, a closed channel, or gone. The same words show when playing and in Downloads. (Getting past the age check needs a YouTube sign-in: later, at the owner's word.)
- **Discover → Downloads is two boxes: Videos and Songs** (the owner's sketch), each newest first, with a Videos First / Songs First switch that's remembered. The genre boxes are gone; a download's genre tag is still written.
- **Settings regrouped into three tabs**, as the owner laid out:
  - **Profile:** the profiles on this Mac (Switch, Rename, Remove from the List, Add New Profile…), then the profile's accounts, YouTube, Spotify, Apple Music and SoundCloud, each a row whose arrow folds open its instructions or sign-in. Only Spotify works so far; the others say "Coming". "Open Settings…" on the Import page opens this tab with Spotify folded open.
  - **Downloads:** where downloads show (Discover Downloads or All Library), the library folder (Select…), the quality (128 kbps; 256 says coming), and downloads per day (50 to 500).
  - **Lyrics:** Find Missing Lyrics, as before.
  - A tab remembered from before opens Profile. Messages that named the old tabs (the Spotify sign-in's, Downloads') name the new ones.
- **Show 25 More** under What's New's and Find's songs (the owner: "it shows 49 songs, not 50, because these radios didn't have more… should be able to click Show More… to continuously look for more"). Each press asks for 25 more from the same starting points, starting from other songs of the owner's each time, and none of the songs already on the page (`discover.suggest` takes `exclude`). If nothing new turns up, it says so.
- **A small red checkmark** beside a song from YouTube that's been played all the way through, on What's New, Find and YouTube Music (the owner's choice of mark). Remembered for good, per profile: `listening.heard` keeps the YouTube ids in the library's `state.json`, and `listening.get` gives them back as `heard`. A song only counts when it plays to its very end, as a play of the owner's own songs does.
- **Up Next and the YouTube Queue.** Each YouTube Music result has an **Up Next** button (beside its length and Download): the song plays after the one that's playing, after anything else put there the same way, or at once if nothing is playing. It also joins the **YouTube Queue**, which shows under YouTube Music in the sidebar while it has anything in it: the queued songs in order, each playable from there (the rest follow), downloadable, or taken out; Play and Clear at the top. Nothing in it is downloaded unless asked. It's kept with the profile's settings, so each person has their own. The play queue learned `queueNext` (tested in the Kit).
- **Under the cover or the video, as the owner drew it:** on the left, Song | Video and, one above the other, **Download Song**, **Download Video** and **Download Both**; in the middle, the title, artist, album and heart; on the right, for a video only, **Karaoke**, the picture size and **Full Screen**. Each download says "in your library", or how far along it is, instead of its button once it's done or on its way. Download Video with the song showing finds the song's official video first and saves it at its sharpest, up to 1080p; with the video showing, at the size showing. Download Song for one of the owner's own songs simply says it's theirs. (Save Video and Download Song Too, which were in that row, are these now.)
  - Checked in the unseen copy with a library song paused on the page. That copy's picture-taking stalls on the page's blurred backdrop, so the backdrop was left out for the picture only (a temporary line, removed).
- **Copy to Profile** (the owner: "if user C creates a playlist, it can be exported to user D, and it makes a copy of the songs"). Right-click a playlist → Copy to Profile ▸ D. Its songs are copied into D's library the next time D's profile is opened, by D's own engine, into a playlist of the same name there ("Road Trip (from C)" if D has a Road Trip already). C keeps its own; nothing is moved or deleted.
  - Engine: a new plan kind, **`share`** (`plan.create` with `source_root`, `paths`, `playlist_id`). The other library is only ever read: each song goes in with `fileops.copy_in` (checked against what was read), with its `.lrc`, and its album's `cover.jpg` if there's none here yet. It keeps its tags, its `MUSICORG_ID` and its place under `Music/`. A song already here, by `MUSICORG_ID` or YouTube id, isn't copied again; it just joins the playlist. Paths outside that library's `Music/` are refused. Journaled; Undo takes the copies out.
  - Why "next time D is opened" rather than straight away: the app runs one engine, for one library, at a time. A second engine for D would also start D's own waiting downloads.
  - Checked for real in the unseen copy on scratch folders: four songs of a playlist copied from one profile to another, with their lyrics files and covers, and the playlist there with all four. Songs that were downloads in the first profile show under the second's Discover → Downloads (where each profile keeps its downloads is its own setting).
- **The queue does local work first** (found while building Copy to Profile): an Edit Details, a Delete or a copy used to wait behind the owner's queued downloads, and at the daily limit, or while YouTube had paused us, it didn't run at all until the downloads could go on, which after a big Download Automatically could be hours. Now `edit`, `remove` and `share` jobs go first and run whatever YouTube's state.

2026-10-03, midday. The owner's second batch of the day, with pictures.

- **Leave Full Screen works.** It was at the top of the screen, where in full screen macOS keeps the top edge for its menu bar, which slid down and took the click; only Esc worked. It's in the bottom bar now, beside the picture size. Esc and a double-click still work. (Not tried in full screen here: the unseen copy can't go full screen.)
- **The Local Visualizer laid out as the owner drew it:** Song | Video above the picture, always in the same place; the song's name, artist, album and heart centred under it; beside the name on the right, Karaoke (and for a video, the picture size and Full Screen), or under it when there isn't room. The downloads are in a row when there's room and one above the other when there isn't. Before, the name was squeezed between the downloads and the video's buttons until it wrapped a letter at a time.
- **The sidebar keeps its width** (220 points); a page wanting more room can't squeeze it. Only the sidebar button hides it. Import Playlists moved below Downloads.
- **Karaoke on every song.** With the video showing, it lines the lyrics up with the video, as before. With the song showing, for a song with no lyrics or only plain ones, it looks for lyrics (LRCLIB, then YouTube Music; timed ones first) and keeps them: saved into a song of the owner's (an Edit, journaled), shown for a song played from YouTube. It says what it found under the name.
- **Queue on What's New, Find and YouTube Music.** A Q on each What's New and Find card, and "Queue" (was Up Next) on each YouTube Music row: a song joins the YouTube Queue and plays after the one playing; pressed again it comes back out, of the queue and of what's to play. Beside Play All on What's New and Find, **Queue (N)** shows the queue, where songs can be taken out.
- **The YouTube Queue page:** no Queue button on its own rows (just the time, Download and −). A song leaves the queue once it's been played, for **Played Already** beside Play and Clear: a list of them, newest first, any one played again with a click. Kept per profile.
- **YouTube Music rows show how often a song has been played** ("▶ 497M"), from YouTube Music's own search answer, at no extra cost (`plays` in the Candidate shape). The owner asked for the thumbs-up count: YouTube Music's search doesn't give it, and getting it would take another request to YouTube for every song listed.
- **Settings:** a **Play Options** tab: with Video chosen, play the song while its video loads (yes, as before) or wait for the video. And under Downloads, **Videos: always the highest quality available**: videos then play and save at their sharpest (up to 1080p), and the picture-size menu leaves the visualizer.
- Checked: the visualizer in the unseen copy, wide and narrow, song and video (the backdrop left out for the picture only, by a temporary line, removed); engine and Kit tests. Not clicked in the running app.

2026-10-03, about 1pm. The owner: "Add Deezer, Amazon music export file, Last.fm. Yes, show likes from YouTube." And the always-best-video setting split in two, playing and downloading.

- **Import Playlists → Deezer** (`deezer`, a new module): a public Deezer playlist or album by its link, with no sign-in and no key. The page's address, a share link (`link.deezer.com`, followed to the page; only Deezer's own addresses are accepted) or a playlist's number. Up to 3,000 songs, a hundred a request. Each song names its main artist only, which is all Deezer's lists give. Tried against Deezer itself: a 100-song playlist and a 14-song album, and four of the album's songs found on YouTube Music at once. A real share link wasn't at hand: if one doesn't lead straight to the playlist's page, the owner is told to paste the page's address instead.
- **Import Playlists → Amazon Music** (`playlistfile`, a new module): Amazon Music can't be read from outside and has no export of its own, so the way in is a playlist saved as a file by a service such as TuneMyMusic or Soundiiz. The reader takes any such file from any service: a CSV (or tab- or semicolon-separated) whose first row names the columns, found by name in any order (Title, Track name, Song; Artist, Artist name; Album; Duration in seconds, milliseconds or 3:45; Playlist name), a text file of `Artist - Title` lines, or an M3U playlist. A file holding several playlists lists them, and the app shows a menu to choose one. The file is opened read-only, at most 5 MB, and nothing of where it was is kept. No real Amazon export was at hand to check the column names against: every name such exports are known to use is looked for, and a file with none of them says what it needs. A Spotify export from Exportify reads too.
- **Last.fm** (`lastfm`, a new module): no sign-in and no password, but Last.fm answers only to an app with an API key, which the owner makes themselves (free, last.fm/api/account/create). Settings → Profile → Last.fm takes the username and the key (`account.connect`), asks Last.fm about the user first, and keeps both in `accounts.json` for this profile; `account.status` says only whether a key is saved, never the key. Two uses:
  - **Discover → Find → "My most played on Last.fm"** (seed kind `lastfm`): the radios start from the owner's most played songs there over the last six months (of all time, if that's fewer than four). One the owner has needs no search; up to 16 of the others are searched for. A much-played song the owner doesn't have is itself a pick ("One of your most played on Last.fm"). This is the "Last.fm as a second source" the Discover plan left to build, done as the owner's listening history. Last.fm's "similar tracks" lists aren't used: each pick from them would cost a YouTube search.
  - **Import Playlists → Last.fm:** Loved Tracks, or the most played over 7 days, a month, 3, 6 or 12 months, or all time (up to 1,000 songs), as a playlist.
  - Not tried against Last.fm itself: there was no key to ask with. The answers' shapes are from its documentation, with what its JSON is known to do allowed for (numbers as text, a list of one as the one thing).
- **A song whose playlist gives no length is found by its name:** a text file, a table without a length column and Last.fm give a title and an artist only. Before, the matcher's "length within 2 seconds" could never be met, so every such song would have been "not sure" and needed a tick. Now official audio of the same artist, title and version is enough for those, as it already was for a music video's entry; and "the length is different" is never given as the reason when there was no length. A song with a length (Spotify, Deezer, YouTube) is matched as before.
- **Thumbs up on the player page:** the count YouTube shows for the song that's playing from YouTube, or for its video while the video shows, beside the heart (a thumb and "900K"). It comes with the answer the app already asks for to play it (`likes` in `youtube.stream` and `youtube.video`, yt-dlp's `like_count`, checked against yt-dlp 2026.08.19 with one real song), so it costs no request. A song played from the library's own file has none: asking would cost a request a song.
- **Settings:** "Videos: always the highest quality available" moved to **Play Options**: it's about playing (the Local Visualizer's picture-size menu goes away). **Downloads** has its own "Always download the highest quality available": Download Video then saves the video at its sharpest (up to 1080p) whatever size is playing; off, it saves the size showing. Until it's set, it follows what the old single setting said. Songs still come in one quality (128 kbps); 256 waits for the YouTube sign-in.
- `account.sign_out` now answers with every service's state (as `account.status` does), and takes `lastfm`.
- **Deviations:** Last.fm was on `CLAUDE.md`'s Not yet list (under Discover); built at the owner's request, and that paragraph updated. A playlist file is a new kind of outside file the engine opens: read-only, like a scan's input (rule 2).
- Checked: engine tests (Deezer against recorded answers; Last.fm and the file reader against made-up ones), Kit tests; and in the unseen copy, the Import page's new tabs, the two Settings tabs, the Last.fm set-up steps, and the thumbs-up count beside the heart (a made-up count put in by a temporary line, removed). A Deezer album and a made-up CSV were read and their songs found on YouTube Music in a scratch library. Not clicked in the running app: choosing a file, setting up Last.fm, a real count on a playing song, the two settings.

2026-10-03, about 1:30pm. The owner, with a picture of the narrow player page: "I quite like this, it's all centred. Move full screen and karaoke under the heart. Same for songs." And options for the visualizer under Play Options.

- **The player page is centred, one thing under the next, at every width:** Song | Video, the picture, the name, the heart, then Karaoke (and for a video its picture size and Full Screen) in a row under the heart, then the downloads. Before, Karaoke and Full Screen sat beside the name when the window was wide, and under it only when narrow.
- **Lyrics make way.** A song with no lyrics no longer keeps half the page for "No lyrics for this song yet": the cover (now up to 560 points) or the video has the whole page. The page keeps its shape while the next song's lyrics are looked for, so the cover doesn't jump across and back at every song.
- **Settings → Play Options → Visualizer**, as the owner listed it:
  - "Videos: always the highest quality available" (as before).
  - **Always show lyrics**, yes or no. No: the cover or video always has the whole page. The lyrics button beside the volume slider switches the same thing while the Local Visualizer (or the big now-playing page) is showing; on any other page it still switches the lyrics beside the library.
  - **Show lyrics in full screen**, yes or no (no, as it was, until chosen). Yes: with a video on the whole screen, the song's lyrics take a column on the right and the video the rest; nothing is laid over the picture.
  - "Play the song while its video loads?" moved in here.
- **Settings → Play Options → Custom Visualizer**, marked Coming: "Which visualizer" and "Use the custom visualizer instead of the song or album cover". Both are switched off until the visualizer project (Particle Accelerator) is finished and added; nothing is behind them yet.
- Asked, not built: an **Artist page** (right-click a song → Artist Info, or an artist button on What's New, Find and YouTube Music: the artist's songs, background, coming concerts). The owner asked what I thought; the answer and the two decisions it needs are in `docs/ROADMAP.md`.
- Checked in the unseen copy, by temporary lines since removed: the page with lyrics, without, and with lyrics switched off; the full-screen layout with its lyrics column; the Play Options tab. Not clicked in the running app.

2026-10-03, about 2pm. **The Artist page** (the owner: "build the page, starting without concerts").

- **Discover → Artist**, under Find: type an artist's name, or get there from a song. It shows YouTube Music's own page for the artist: a picture, their background, subscribers, monthly audience and views; their best-known songs; **Show All Songs** (every song YouTube Music lists for them, up to 300, with lengths); their albums, and their singles and EPs, each opening to its songs; and **Fans Also Like**, each opening that artist's page, with a Back button.
- Every song there plays, queues and downloads like a YouTube Music row. The page says which songs the owner already has (by the song's name, version and artist, not only its YouTube id), how many of the artist's songs they have, and **Download Missing** fetches the rest of what's listed: the usual question first (how many, how long), and the daily limit applies.
- **Three ways in:** Artist Info on the right-click menu of any song (library songs, YouTube Music rows, What's New and Find cards; a song credited to several artists lists each); an artist button beside each YouTube Music row and beside the artist's name on each What's New and Find card; and the page's own box.
- **Engine:** `artist` (new module, read-only) and `artist.info`, `artist.songs`, `artist.album`; `youtube.find_artist` and `youtube.artist_page` (ytmusicapi's `get_artist`, checked against 1.12.3 and recorded for the tests). Two requests for an artist the first time (the name, kept 30 days; the page, kept a week), through the limiter. No key and no sign-in.
- **What YouTube Music calls things:** ytmusicapi names two of the counts `monthlyListeners` and (for related artists) `subscribers`; the page itself says "monthly audience" for both, so that's what the engine and the app call them.
- **Concerts aren't built.** The owner wants the next show, worldwide, and showed YouTube's "Event tickets" shelf under a video. Checked: a video's page does carry it, with no key. Whether to read that or use Ticketmaster's free API is written up in `docs/ROADMAP.md`.
- **Deviation:** the Artist page wasn't in the Discover plan; added at the owner's request, and `CLAUDE.md` says so.
- Checked: engine tests against recorded answers; Kit tests; and in the unseen copy with a real artist: the page, all songs, an album's songs, the lower half, and the cards' artist button. Not clicked in the running app: the right-click entries, Download Missing, Back.

2026-10-03, about 2:30pm. The owner: "Play options selected in settings should be permanent. They can be toggled off in individual pages, but reset after x amount of time to the settings preference. Toggling lyrics off from the base bar, near volume, doesn't automatically turn off always show lyrics in settings."

- **Settings are the standing choice; a page is switched only for now.** The lyrics button beside the volume slider no longer changes "Always show lyrics" in Settings (it did, since yesterday). On the Local Visualizer it switches the lyrics for now: the setting stays as chosen, and the page goes back to it by itself.
- **How long "for now" is** is a new first row in Settings → Play Options: "A change made on a page lasts" 5 minutes, 30 minutes (the standard), 1 hour, or until the app is next opened. Whichever is chosen, opening the app again always starts from the settings. Changing the setting itself in Settings takes effect at once and ends a page's switch.
- **Full screen has a lyrics button too** (in its bottom bar), which switches "Show lyrics in full screen" the same way: for now.
- The lyrics button's tip says which it is: as set in Settings, or switched for now and when it goes back.
- The lyrics beside the library pages (the same button, on any other page) are as they were: that isn't a Play Options setting, and stays how it's left.
- Checked in the unseen copy: the new row in Settings; a page switched for now (the lyrics went, the button dimmed, and the saved setting was untouched); and, with the time set to one minute, the lyrics coming back by themselves. Not clicked in the running app.

2026-10-03, about 3:30pm. Three changes from the owner, and a question answered.

- **Karaoke counts as a download now.** The owner asked whether lining lyrics up with a video "is counted as a YouTube download, correct?" It wasn't: Karaoke fetches the video's whole sound from YouTube (and the song's too, when the song is played from YouTube and isn't a file), which is as much as a download takes, but only the queue's downloads were counted. Now each whole sound Karaoke fetches counts as one against the daily limit (`queue.spend_download`), and shows on the sidebar's counter. A sound is fetched once and its fingerprint kept for 30 days, so the same video costs nothing the second time. With the limit used up, no sound is fetched: the video's captions are still tried (they cost nothing), and if they can't do it the page says the limit is why and when there's room again.
  - The caption under the player says so, in the owner's words: "…Karaoke lines them up. Uses 1 download from your daily limit." For a song played from YouTube it says 2 (the song's sound and the video's). The Karaoke button's tip says the same.
  - The worker and a request from the app can now both count a download at the same moment without one being lost (a lock around the count).
- **A new profile's library goes in the Mac's Music folder**, in a folder named after it: "Music Kids", "Music C". Before, it was put beside the library in use ("Main Music (C)"). A folder that's already there, or is another profile's, is never suggested: the next free name is ("Music Kids 2"). Choose… still picks any other folder. The libraries that exist already aren't moved by this: see below.
- **A child's profile:** a switch on the New Profile sheet, "This is a child's profile", and the same in a profile's ⋯ menu; a "Child" badge beside the name. It only marks the profile: what it will do (the owner: "most likely remove explicit songs, and not allow R18 songs") is still to be decided, and `docs/roadmap/1.1-family-mode.md` says so.
- **Asked, not done: moving the two existing libraries into the Music folder** ("All information about music will be moved… do you agree?"). Agreed for the libraries: each is one folder that holds everything about its music (songs, videos, lyrics, covers, playlists, play counts, its own records in `.musicorg`), so it moves whole, and on the same disk that's instant. The app's own small files (sign-ins, settings, logs) stay in the Mac's Library folder, where rule 2 puts them: they're the computer's, not the music's, and sign-ins shouldn't travel with a folder that may be copied. The move itself needs the app closed and the owner's word, so it waits for both.
- Checked: engine tests (the count, the limit, the captions still working at the limit), Kit tests, and the New Profile sheet and the badge in the unseen copy. Not clicked in the running app.

2026-10-03, about 4:30pm. **The Artists page, split down the middle** (the owner's drawing: "Mine" on the left, "Discover" on the right, one search box between them; "I'd give the Discover artist page about 70").

- **Library → Artists is now two lists side by side.** Mine: the library's artists, as before. Discover: the artists behind What's New's picks, the one with the most picks first (it asks for What's New's picks the first time, as that page would). The box above narrows Mine as it's typed; **Look Up** asks YouTube Music who goes by the name and lists them on the Discover side, with their pictures (`artist.search`, one request, kept 30 days).
- **Opening an artist gives both sides to them**, from either list:
  - **Mine, 30% of the page:** how many of their songs and albums the owner has, Play and Shuffle, and "Your Listening": times played, time listening, the most played song, where the artist comes among the owner's artists, last played, favourites, first added; then the songs with their play counts. For an artist the owner has nothing of, it says so. Plays are the ones this app has counted (a song played to its end, since 2026-10-01), so the numbers start small.
  - **Discover, 70%:** the artist's page on YouTube Music, laid out as it was. In a small window its header stacks and its song rows slim down (the Queue button is its symbol alone) so nothing is cut off.
  - The arrow goes back: to the artist before, then to the two lists. Esc does the same.
- **Discover → Artist is gone from the sidebar:** this page replaces it. Artist Info on a song, and the artist button on rows and cards, open it here. The library's old single-artist page (an album grid) went with it; the albums are under Library → Albums as before.
- An artist is the owner's "however it's written": "JAY-Z", "Jay Z" and "jay z" are one, as are "The Beatles" and "Beatles".
- **`scripts/move_library.py`:** moves one profile's library folder into the Mac's Music folder ("Music C") and tells the app and the engine where it went. The owner asked for C's library, and future ones, to live there; D's stays where it is while other work is being done on it. It refuses if the app is open, if the library is open in an engine, if the new folder exists, or if it would be a copy to another disk; and puts the folder back if the app's settings can't be updated. Tried on a made-up library and settings. Not yet run on C's library: the app had it open.
- A slip of mine, put right: the unseen test copy of the app shares the engine's settings folder with the real app, and making a scratch profile in it earlier today had left the engine's "last library" pointing at that scratch folder. It's back on C's library. The app always names the library it opens, so nothing went to the wrong place. Test copies now get a settings folder of their own (`MUSICORG_HOME`).
- Checked: engine and Kit tests; and in the unseen copy, the two lists, a search, an artist of the owner's and one they have nothing of, in a small window and a wide one. Not clicked in the running app.

2026-10-03, about 6pm. **Looks: Settings → App Layout** (the owner: the app is "a little too plain"; "Settings → App Layout: Apple Native Build, Warm Look … build a warm look idea you had"). The libraries stay where they are (the owner: "let's leave the music folders where they are"); `scripts/move_library.py` stays, unused.

- **Settings → App Layout** has the two looks, under the owner's names. **Apple Native Build** is the app as it was: macOS's own colours and type, light or dark as the Mac is set. **Warm Look** is new. The pages and everything on them are the same in both.
- **The Warm Look:** warm near-black surfaces (the sidebar a shade darker, the player bar a shade lighter), cream words, serif headings, macOS's orange as the highlight colour, and each page's main Play button filled with it. Always dark. A song table has plain lines between its rows: macOS's grey stripes don't sit on a warm page.
- **The glow.** In the Warm Look the top of the window, through the title bar and a page's heading, glows amber; while a song plays it takes the colours of that song's cover, left to right as they are on the cover. A dark cover still shows (its hue is kept and brought up to a set brightness), a white one doesn't glare, and a cover with no colour of its own (black, white, grey) gives the amber.
- **A look is put on when the app opens**, and a new choice shows the next time it's opened: Settings says so and has a **Reopen Now** button (it stops the music). The reason: macOS gives an app its highlight colour (a selected row, a ticked box, the ring round a text field) only as it opens; that was tried three ways. So the colours are fixed for as long as the app is open, and nothing has to redraw itself halfway.
- The look is the computer's, not a profile's (`appLook` isn't put away with a profile's settings), because switching profiles doesn't reopen the app.
- **How it's built:** the app had no colours of its own (about 190 uses of macOS's). There's now one place for them: `Theme` in the app, and `AppLook` and `WarmPalette` in the Kit, where a test checks the words can be read on every surface. Natively every value is macOS's own, so nothing changes there.
- The orange highlight is macOS's own setting for one app (`AppleAccentColor`, saved among the app's settings; nothing of the Mac's is touched). The Warm Look saves it and the native look takes it away.
- The unseen copy's pictures now show the whole window, title bar included, and a sheet that's up.
- Checked: Kit tests (60); in the unseen copy, the Warm Look on Songs, Albums, an album (also scrolled: its list stops below the title bar), Artists, What's New, Find, Downloads, Import Playlists, YouTube Music, Settings and a sheet; the glow at rest and from three covers. **The native look is unchanged:** eight pages pictured in the version before and in this one, with the same saved settings, came out the same to the pixel.
- Not checked: the unseen copy's window is never the front one, so the orange of a selected row, the filled Play button and the sliders weren't seen (only the orange lyrics button was); the Reopen Now button wasn't clicked (its wait-then-open step was tried on its own); the player page and full screen, which were dark with the cover's colours already. Not clicked in the running app.

2026-10-04, about 12:45pm. **Sharing the library with a phone player, at home.** The phone server was on the "Not yet" list. The owner gave the go-ahead on one condition: nothing private (no network address, computer name, pairing code, key or personal detail) on GitHub or the web. The phone player is a separate project; nothing here names it.

- **What the owner was promised**, and where it's kept:
  - **Off until they switch it on** (Settings → Sharing). The engine keeps no "on" of its own: the app asks each time it opens, and an engine nobody asked never listens.
  - **Read-only.** A phone can ask for the list and for files, and can pair. Nothing else. A whole sync leaves every file in the library with the size and time it had (a test checks it), and nothing is journaled, because nothing changes.
  - **Paired once, with a six-digit code** shown on the Mac. A code works once, for five minutes; five wrong codes and every code is refused for a minute.
  - **The home network only, and only while the app is open.** Only a caller with a private (RFC 1918), link-local or loopback address is answered; anyone else is dropped unanswered. No UPnP, and nothing is sent to the internet. The listener lives inside `musicorg serve` and goes when the app does.
- **`sharing`** (a new module): the computer's half of "format 1", which `docs/ENGINE_API.md` now describes in its own section 3. `GET /sync/v1/hello`, `POST /sync/v1/pair`, `GET /sync/v1/library`, `GET /sync/v1/files/<id>`, with the status codes and error words the phone expects.
  - **The list** is every song in `Music/` the index has, the saved videos, and the owner's playlists, with favourites and play counts from `listening`. Songs in Ogg or Opus are left out: the format has no name for them and a phone can't play them.
  - **Every id is a hash.** A phone never sees the library's own id for a song, a path, a YouTube id or a tag name. A song's ids are made from its `MUSICORG_ID`, so they survive a rename, a move and a retag; the library's id is made from when it was created, so it survives the folder being moved.
  - **A file needn't be a file.** A song's cover is the album folder's `cover.jpg` when there is one (one file for the album), else the picture inside the song; lyrics are the `.lrc`, else the plain lyrics in the tags; a video's cover is the picture inside it. Each has an id, an exact size and a version of its own.
  - **A file is never sent as something it isn't.** If it has gone, or been written again since the list was made, the answer is "not found", and the next list has the new version.
- **`browse`'s details are version 3:** the size and a version of the cover and the plain lyrics inside each file are measured when its tags are read, and kept in the index with the rest. So the list is made without opening a single song. **The first `library.tracks` after this update reads every file's tags once more** (as when the details last changed).
- **`devices.json`**, beside `config.json` in the app's settings folder, written by `config.py` the way `accounts.json` is (atomically, readable by this user only), for each profile. What's kept of a device's key is its SHA-256, so the file can't be used to pass for a device. A device says only what kind it is ("iPhone").
- **RPC:** `sharing.status`, `sharing.set`, `sharing.pair`, `sharing.stop_pairing`, `sharing.forget`, and the notification `sharing.changed`. No key is ever in an answer. A pairing code, a key and a caller's address are never logged (a test reads the log for them).
- **The app:** Settings → Sharing. The switch (off by default, and each profile's own), this Mac's address and port under it, Pair a Device (a sheet with the code and how long it still works), and the paired devices, each with Remove. The app announces the share with Bonjour (`_homemusicsync._tcp`, under the Mac's own name, `local.` only), through macOS's own `dnssd`: no new dependency, and macOS ends the announcement by itself when the app goes. The engine announces nothing, so it stays the same on Windows.
- **The secret check** (`scripts/check_secrets.py`) now also refuses a home-network address in a file or a commit message. The repository and its history have none.
- **Rules touched, at the owner's request:** `CLAUDE.md` records the go-ahead, the four promises and the privacy rule; rule 2's list and rule 3's line for `config.py` name `devices.json`; the "Not yet" list keeps only the Subsonic-compatible server.
- **Beyond the brief, for the privacy rule:**
  - A request must be sent to the computer by its home address, `localhost` or a `.local` name, must not carry an `Origin`, and a pairing request must be JSON. A web page somewhere else can't then use the share through a browser at home. It answers 403 `not_home`, an error word the format didn't have.
  - Two more error words: `unavailable` (the library couldn't be read just then) and `bad_request` (anything that isn't part of the format, such as an attempt to change something).
- **Checked:**
  - `pytest` and `ruff`, `swift build` and `swift test`. 35 new engine tests and 7 in the Kit. Every test's share listens on this computer's own loopback address only.
  - **End to end, in the iPhone simulator**, with the phone player built by its own command, against a scratch library of generated tones (six songs in three formats, one in Opus, a video, two album covers, a cover inside a song, timed and plain lyrics, two playlists, favourites, play counts):
    - The engine alone: nothing listening until asked; then answering on this Mac and at its home address; no key, a foreign name and an attempt to change something all refused.
    - An address typed into the phone: a wrong code refused in the server's own words, the right one paired, 15 files copied, six songs and the video on the phone with their covers, the lyrics' text as it is on the computer, both playlists, the favourites. A second sync after a new favourite and a new playlist: nothing copied again, the list brought up to date.
    - The app itself (the unseen test copy, on the scratch library): it announced the share, the phone found the Mac by itself, paired with the code on the app's sheet, and synced with nothing to copy (the engine had started again in between, so ids hold across that). Settings showed the newly paired device by itself. Quitting the app closed the port and ended the announcement. The page was looked at in both looks.
- **Not checked:** a real phone or tablet over Wi-Fi; a caller from outside the home network (tests only); the switch, Pair a Device, New Code, Done and Remove clicked by hand (the unseen copy can't be clicked: the switch came from a start-up argument, and the code from a temporary line that isn't in the commit); macOS asking about incoming connections (this Mac's firewall is off); the owner's own library, which wasn't touched, so how long the list takes for two thousand songs isn't measured; lyrics and the video on the phone's own player screens (not opened: they would have played sound); Windows, beyond CI.
- **Known:** a phone that forgets the computer and pairs again is listed a second time until the old entry is removed (a device says only what kind it is). A file's version is its size and time, so a song that's retagged is copied again at the next sync.

2026-10-04, about 3pm. **The custom visualizer: Visualizers 5, 7 and 8 on the Local Visualizer.** The owner asked for three of Particle Accelerator's visuals in the app now, with Visualizer 7 as the standard one. The roadmap had this waiting for Particle Accelerator's 1.0.

- **The app's first package:** Particle Accelerator, in `app/Package.swift`, pinned to one commit (it has no 1.0.0 to pin), used by the `MusicOrganizer` target only. `app/Package.resolved` is committed. CI fetches it from its public repo. It needs macOS 14 and Apple's own frameworks, and brings no packages of its own.
- **The page:** the Local Visualizer's switch is Song | Video | Visualizer. Visualizer plays the song itself and shows the visual where the cover would be, in the video's shape and place, with the lyrics in the narrow column a video gets. A video is still a video: the visualizer only ever takes the cover's place (so with Video chosen and no video to show, it's there if it's on). With nothing playing, the cover's place is as it was.
- **Settings → Play Options → Custom Visualizer:** the two rows that were marked "Coming" work. Which visualizer: 5, 7 or 8, and 7 until another is chosen. Use it instead of the song or album cover: No until it's turned on. The page's switch changes the second only for now (`switchForNow`), as the lyrics button does: Settings keeps the standing choice.
- **It only listens.** Particle Accelerator's listener adds a listening tap to what the app's one player plays and passes the sound on untouched. It writes nothing and uses no network. The app's rule that it never writes inside the library is untouched, and nothing in the engine changed.
- **The Kit:** `CustomVisualizer` (which are offered, the standard one, what's shown for a saved number) and `PagePicture` (what the switch says). Two new tests.
- **The owner's own looks came across.** They had tuned the three and pressed Set Standard, which keeps a standard in the Particle Accelerator app's settings only, so the library still had older ones. Asked, the owner chose to have theirs written into the library as its standards; that was done there the same afternoon, and this pin is that commit.
- **Deviations:**
  - **Earlier than the roadmap said,** at the owner's request. `docs/roadmap/0.2-visualizer.md` now says what was done and what's left for 1.0 (its settings panel in Settings, every finished visual, its version in place of a commit).
  - **The listener is given the player when a visualizer is first wanted,** not always when the app starts as Particle Accelerator's notes advise: when the app opens if the setting is Yes, otherwise when the page is first switched to Visualizer. An app whose owner never uses a visualizer then plays exactly as before. The cost, by Particle Accelerator's own measurement: switched to for the first time in the middle of a song, the song stops for about half a second, once.
  - **Drawn at Medium quality, not Auto.** Measured in Particle Accelerator that afternoon, off screen with a made-up loud reading, on the 2019 iMac: at High (which Auto means there), Visualizer 8 as the owner has it takes the graphics card about 18 ms a frame, and 60 frames a second allows 16.7; at Medium, 4.4 ms. Visualizers 5 and 7 take 5.9 and 9.4 ms at High. Medium is also the quality the owner tuned the three at.
- **Checked:**
  - `swift build` and `swift test` (69 tests), with the package fetched from GitHub at the pinned commit. `pytest` (1,497 passed) and `ruff`, though the engine wasn't touched.
  - In the unseen test copy, on an empty scratch library with settings of its own: Settings → Play Options shows the Custom Visualizer rows with Visualizer 7 chosen and "No"; the Local Visualizer shows the three-way switch; and with the setting on and Visualizer 8 chosen, the visual's stage was drawing in the page at Medium (its own frame-time line read 150,000 sparks at 1736×978 and 1.0 ms on the graphics card, in silence).
- **Not checked:**
  - **The picture itself in this app.** An unseen window's picture can't be saved off the graphics card, so what was seen is the stage's own report that it was drawing, not the sparks.
  - **Anything with a song playing:** the visual moving to music here, the switch clicked by hand, the half-second stop, a song played from YouTube or a video with the listener on, a saved video's sound with Visualizer chosen. The unseen copy never plays (it would take the media keys). The owner was told what to try.
  - Changing the setting in the Settings window by hand.
- **Known:**
  - No full screen for the visualizer, and which visualizer is chosen only in Settings.
  - Nothing about a visual can be changed here: its look is changed in Particle Accelerator, and arrives when the pin is moved.

2026-10-04, about 3:30pm. **CI's first run with sharing and the visualizer failed on two runners.** The two commits had been pushed together, and neither had been through CI before.

- **The Intel Mac couldn't build Particle Accelerator.** It has Xcode 16, where one of Apple's audio functions (`MTAudioProcessingTapCreate`) hands its result back differently than in the Xcode 26 on the owner's iMac. Put right in Particle Accelerator, which builds with either now, and the pin in `app/Package.swift` is moved to that commit. Nothing in this repo's own code changed for it.
- **Windows failed two sharing tests.** They wrote the song's `.lrc` as text, which on Windows turns each line ending into two, so the file was two bytes longer than the words it was compared with. The tests write it as bytes now. The engine was right: it sends a file as it is on the disk.
- **Checked here:** `swift build` and `swift test` with the new pin (69 tests), the sharing tests (35) and `ruff`. Neither failure can be reproduced on this Mac: the real check is the next CI run.

2026-10-04, about 4pm. **A copy keeps the version in its name** (the owner: "a song that has been found takes the found name; a song that has not been found keeps the name I had on it"; their example: "Lost Boy R" stays "Lost Boy R" until it's found, then becomes "Lost Boy (Radio Remix)").

- **What was wrong.** On 1 Oct, `plan adopt --unconfirmed` copied 1,062 rips into the library under their parsed titles. The parsed title has the version taken out on purpose, for matching, and nothing put it back or wrote `MUSICORG_VERSION`. 139 copies lost their version: the owner's rip "Come As You Are R" (R is their mark for a remix) became "Come As You Are", and the real original became "Come As You Are (2)".
- **The rule** (`LIBRARY_CONTRACT.md` section 2, a contract change the owner asked for):
  - A found song takes YouTube Music's title.
  - A copy named after its rip gets the clean title followed by every version the rip names, in the rip's own words: "Here (Lucian Remix)", "Still Here (Acoustic Version)". Brackets become round ones and junk such as "(320 kbps)" stays out.
  - **The owner's R stays as typed:** "Come As You Are R", "Done Wrong (R)". It is never written "(Remix)" and never dropped. (When the rip names the remix as well, "High Hopes (Filous Remix) R", the R isn't repeated.)
  - `MUSICORG_VERSION` says what the title says (`remix` for the R). It's now written for adopts, downloads and saved videos too, each from its own title.
- **The parser** keeps the words it takes out as a version (`Parsed.version_words`), and `normalize.full_title` puts them back. **Matching is unchanged:** the parser before and after gave the same artist, title, tokens and confidence on 4,256 parses, and every existing row of the test table is as it was. `normalize.parse_owner_title` reads a title the owner named, their R included; `parse_title` stays the reader for official titles, which never carry that mark. The owner's index was scanned before the words were kept, so a plan parses a rip's names again from what the scan stored (`scan.parse_again`): no rescan and no index rebuild, which would throw away the matcher's work.
- **Adopts** (`plan adopt`):
  - A copy named from its rip gets the full title and the version tag. A rip whose names weren't trusted (confidence under 0.8) keeps its own title and gets no version tag: a guess isn't written into a file.
  - A title fix is used as typed, and its version read from it ("Lost Boy R" gives `remix`; a plain title removes the tag).
  - A found song's version tag comes from the official title, not from the candidate's stored tokens. A candidate kept from a review row or a search has none, and that used to remove the tag from a song whose official title says remix.
  - An unconfirmed copy decided as only-copy with no fixes keeps the names it has: a title the owner corrected in Edit Details is no longer overwritten, and the file isn't moved. One exception: a title still exactly as the old rule wrote it becomes the full title, unless the owner typed those very words in Edit Details (the journal says so: see "A title the owner typed" below).
  - `plan show` marks a matched rip whose name says remix or live while the official title chosen for it names no version, and the summary counts them (`version_not_in_title`). The choice stands.
  - `apply` refuses an adopt plan made before today. **Make a new plan** rather than applying an old one.
- **The repair** (`plan tidy`): a copy still named after its rip, with no version tag, whose rip names a version.
  - Its title is still the one the old rule wrote: it becomes the full title, the file is renamed to match with its `.lrc`, and the tag is written. One journaled, undoable batch, through the same rename job as preferred names. The track keeps its `MUSICORG_ID`, so favourites, play counts and playlists follow it.
  - The owner changed the title by hand and it still shows the version ("Lost Boy R"): the tag only.
  - The owner changed it to one that names no version: left alone, and counted.
  - Copies with official details, copies the owner gave fixes for and downloads are never touched. This is decided from each file's own tags, never from the index's `match` column, which is empty for 811 older rows. The job checks it again right before it writes: a copy that was found between the plan and the job keeps its official title, even when that is the very title the plan saw.
  - A copy whose rip has a job waiting in the queue is left out (`rip_in_queue`): that job may be about to give it its official name. Run the queue, then plan again.
  - The summary counts each kind apart, each with its own line in the CLI: `versions` (a version put back), `renames` (a preferred name), `numbers_dropped` (a " (2)" no longer needed, which used to be counted under `renames` as a "preferred name"). What was left alone is counted by reason in `versions_skipped` and listed, file by file, in `versions_left`; `plan show` prints each as a `left` line with its reason.
  - A name already taken gets " (2)"; nothing is overwritten. The plan shows the " (2)" a file will get, whether the name is taken by a file already there or by an earlier line of the same plan, so no two lines of a dry run show one name. The originals pushed to " (2)" get their plain names back from a **second** `plan tidy`.
  - The rips aren't opened: the plan reads what the scan stored.
  - A rename job that stops part way is carried on when it runs again: after its tag write (it knows its own new title), and after its move (the journal says where the file went; the `.lrc` follows and the index is told). A title edited between the plan and the job ends `needs_review` and nothing is written.
  - A repair whose job was given up after its tag write (the title and tag are right, the file still has its old name) is finished by the next `plan tidy`.
- **One way to read a library title** (`browse.versions_of`): the version tag, plus what the title names, read by who named it (the owner's R counts in a title of theirs, never in an official one).
  - The lyrics lookup uses it, so a remix whose tag is missing is no longer given the original's timed lyrics.
  - The video lookup and Discover's "already have" use it. The rip's old name is now read only for a copy that is still unconfirmed and whose file names no version. Before, it was read for every copy for ever, which would have made a song the owner matched to the plain official track read as a remix inside the engine.
  - Owning "Melody R" no longer counts as owning "Melody".
- **Edit Details keeps the version tag in step with the title**: a new title sets it to what that title names, and removes it when it names none. That is the owner's way to put a wrong version right.
- **A title the owner typed stays** (`browse.typed_titles`). Taking the R off "Eyes On Fire R" in Edit Details leaves exactly what the old rule wrote: "Eyes On Fire", no version tag, a rip called "Eyes On Fire R". From the file alone the two can't be told apart, and the next `plan tidy` (or an only-copy decision with no fixes) would have put the R straight back. The journal can tell them apart: an `edit` batch wrote that title. So `plan tidy` leaves such a copy alone and counts it, the decision leaves its names alone, and the video lookup, the lyrics lookup and Discover no longer read the rip's name past it. An edit that was undone doesn't count. The journal is read once per plan; a lookup reads it only for a copy whose rip names a version its file doesn't.
- **Undo is refused, with nothing changed, when it couldn't put things back**, and says which batch to undo first:
  - **A renamed file's old name now belongs to something else.** After two tidy runs, undoing only the first would have moved the remix onto the original's name: it would have come back as " (2)" with its new title still in its tags, and gone missing from the app. A name counts as taken the way `fileops` counts it when it moves a file: anything of that name, a folder too, whatever its letter case.
  - **A later batch moved or renamed one of the batch's files.** Undo finds files by where the batch left them. Each file is now followed through the journal first. After the repair, undoing the 1 Oct batch would have found the *original* under the remix's old name, set that aside, and left the 140 renamed copies in the library. A file set aside or sent to the Trash since is simply "no longer there", as before, unless something else has its name now.
  - Also fixed: a song that undo brings back under another name (a restore onto a taken name) is now put in the index; and `undo --dry-run` no longer says of a file the batch retagged and then renamed that its tags "can't be restored" (it looked for the file under its old name before the move back): it says they will be, as the real undo then does.
- **An upgrade that stops part way never copies the rip in twice.** An unconfirmed copy being given its official details (or decided as an only copy) is retagged, moved, its `.lrc` moved after it, then the index is told. A job that stopped after the move and was run again looked where the old row pointed, found nothing, and copied the rip in a second time under a new id; moving the `.lrc` with the song (new in this change) added an ordinary way to stop there. Now the index is told the moment the copy has moved, the job run again reads its own batch's journal for where the copy was and finishes what goes with the move, and any copy of the owner's own audio that the index lists for the rip is carried on with rather than copied again.
- **A rip's name with nothing to read in it** ("(Official Video).mp3", a name of only symbols) no longer raises an error in the parser: it gives no title and no confidence, like any other name that can't be read. `plan tidy` and the video lookup read rips' names for copies whose rip isn't indexed, and would have stopped on one; so would a scan.
- **To run it** (not done: the app holds the library's lock, and this work never touched the real library): close the app, and run the queue after each `apply`. First `musicorg plan adopt --matched`, `apply`, `queue run`, so the waiting decisions go straight to their official titles; then `musicorg plan tidy`, `plan show`, `apply`, `queue run`; then `plan tidy`, `apply`, `queue run` once more. Checked read-only, with this code, against a copy of the index and of the files' tags: parsing the 1,890 rips' names again gives the stored title, artist, versions and confidence for every one; the first `plan tidy` retitles 140 copies and tags 1 more ("Lost Boy R"), and leaves 2 alone whose rips' names weren't trusted; two pairs want one new name ("Solo Dolo R", "I Wanna Go To Hell R") and the second of each is planned as " (2)"; the second run gives 18 originals their plain names back. The dry run on the real library is still the first real check.
- Deviations from the design (`docs/KNOWN-ISSUES.md` has what is left):
  - The owner's R is kept as typed, not written "(Remix)" (the owner's decision, 4 Oct).
  - `full_title` gives nothing, not the stripped title, for a parse that has tokens but no words (one stored by an older engine), so the mistake can't be repeated silently; plans parse the names again first.
  - The version tag is read back from the title being written, not copied from the parse. For a rip whose tags say "Here (Lucian Remix)" and whose file is "Here R", the tag is `remix:lucian`, as the title says, not that and a second nameless remix.
  - An upgrade is carried on after a stop between its tag write and its move (it used to copy the rip in a second time), and moves the `.lrc` with the song.
  - Timed lyrics are **not** set aside by the repair. The design proposed it; the owner hasn't decided, so a `.lrc` simply moves with its song.
  - The repair skips a rip whose names weren't trusted, and counts it, rather than writing its tokens.
  - A hand-changed title counts as showing the rip's versions when it names the same kinds of version (a remix, a live take), not the same tokens: a rip's tags and file name can name one remix twice.
  - The rip's name is read only when the file names no version in its tag **or its title**, which is a little narrower than "no version tag". Discover works from the index, which doesn't hold the version tag, and the tag is written from the title, so the two tests agree.
  - The lyrics lookup gets the same reading as the video lookup, the rip's name included for a copy not yet repaired.
  - Undo refuses rather than following the file to " (2)". Following it would need `fileops` to learn where a file landed so its tags could be restored there; refusing needed no change to `fileops`. It is strict: a `.lrc` or a cover whose old name is taken refuses the undo as well, and so does a later batch having moved any of the batch's files. That second refusal goes beyond the repair and holds for every kind of batch: batches that build on each other are undone newest first.
  - `renames` in the tidy summary no longer includes a file that only loses its " (2)": those are `numbers_dropped`.
  - `apply` and the jobs find a rip's copy through the index's rows for that rip and the batch's journal, not only through a row still marked unconfirmed. A rip that is decided again after its copy was upgraded has that copy retagged, never a second one made.
  - A file with a " (2)" that only needs a tag change keeps its name when the plain name belongs to another song. It used to try for the plain name and land on " (3)". A numbered name is also recognised when the index and the naming rule spell its folder in different letter case.
  - `plan show` says `retag` for a tidy operation that changes tags and moves nothing.
  - A tidy rename is also carried on after a stop that comes after its move, which the design left as a known weakness.
- No new enum value, plan kind or `fileops` operation, and `naming.py`, `tags.py` and `fileops.py` are unchanged. `parsed_json` gains `version_words` for rips scanned from now on; the index's version is the same. New in the tidy summary: `versions`, `numbers_dropped`, `versions_skipped`, `versions_left`. New in the adopt summary: `version_not_in_title`.
- Checked: 1,936 tests pass on the iMac with the day's other work merged in (1,448 before this change) and `ruff check` is clean. The Windows and Linux runners run it when it is pushed. New tests cover the parser's table with its full titles and the round trip back; the report itself (a remix and its original each keep their name); the repair with its `.lrc`, a favourite and a playlist, the second and third tidy; what the repair leaves alone; a stop between the tag write and the move; taken names; undo of each; the undo that is refused and the order that works; a repaired copy upgraded when it's found; the lyrics lookup's versions; Discover's "already have"; Edit Details both ways. After a review of the change, also: an upgrade stopped after its move, at its `.lrc`, and in its last steps (one copy each time, and undo); a copy found between a tidy plan and its job; a version taken off by hand staying off; the undo of an older batch after the repair, of a tag-only repair, and with a folder, another letter case, a `.lrc` or a cover in the way; the dry run of an undo; the lyrics lookup itself with recorded answers; names Windows can't hold, a title too long for its version, two names that differ only in letter case; a saved video's version tag. Each new behaviour was also broken on purpose in a scratch copy to see that a test notices (28 breaks, all noticed). **Not run on the owner's library**, and not looked at in the app (no app change was needed: it shows the title tag).

2026-10-04, about 5pm. **A pasted link that scores low is candidate 1, as its message says** (seen on the owner's library today: twelve songs that couldn't be accepted).

- **What was wrong.** A review row with the decision `url` has its link scored against the rip. Under 0.60 the item stays in review (`url_low_score`) and the owner is told "… it's candidate 1 on the next export." It wasn't. The pasted track was stored first, but the index gives an item's candidates out by score, so it came after everything that scored more, and with three better candidates it wasn't on the row at all (a row has room for three). It couldn't be accepted, and pasting the link again gave the same message, for ever. The owner's example: the rip "Kygo / Sexual Healing" and the link to Marvin Gaye's "Sexual Healing (Kygo Remix)", 0.50, behind candidates at 0.69, 0.60 and 0.60. Step 07 meant it to be candidate 1 from the start; its test had no other candidate beside the link.
- **Now the pasted track is first**, whatever its score, and shown with its real score: `cand1` of the next `review export`, first on the review page (at once, in the answer to the click), and first in `review.list` and in `review.decide`'s answer.
- **`accept` takes it** like any candidate 1 (the spreadsheet's `accept`, the page's "Use this", `review.decide` with `accept`): the item becomes `matched_user` with that video. It's the owner's own decision, so no score is checked, as for every other candidate 1. Replacing is as it was: a download still has to pass the fingerprint gate.
- **How long it stays first:**
  - until the owner decides the item, whatever the decision;
  - or rejects that candidate: `reject:1` takes it out like any other, it's never proposed again, and the item is classified again from what's left, in score order;
  - or pastes another link that's on YouTube Music: the new one is first, and the old one stays as an ordinary candidate, by its score.
  - Rejecting a different candidate leaves it first and the item as it is. Pasting the same link again changes nothing and adds nothing. A link that isn't on YouTube Music (`video_unavailable`) leaves the earlier link first.
  - The matcher doesn't end it either: `match --recheck` and `match --rescan` leave an item alone while its pasted link waits (below).
- **How it's marked.** `"pasted": true` in that stored candidate's payload (`index.PASTED`), put on and taken off only by `review`. `Index.candidates()` and `Index.all_candidates()` give a marked candidate out first and leave the rest as they were: the best score first, equal scores in the order they were stored. There's no new column, so the index's version is the same and nothing is upgraded; an engine from before this change reads the same index and simply orders by score.
- **Everything that reads the order** was looked at:
  - `review export`, the review page's lists and its answer to a decision, `review.list` and `review.decide`: the pasted track first, which is the point. "Candidate 1" means the same in all of them, so `accept` takes what the owner sees. The fingerprint column and `fingerprint` are about candidate 1, as before.
  - The export still sorts review rows by their candidate 1's score, and the page's "Real doubts" list too, so an item with a pasted link comes at the end of them.
  - The report's CSV shows the pasted track as that item's candidate.
  - `plan replace`, `plan adopt --matched`, the lyrics and cover plans and the AUTO sample read the candidates only of items that are matched, replaced or copied in. A mark is only ever on an item still in `review`: every decision takes it off, and nothing else moves a marked item out of `review`. So none of them can meet one.
  - `match --recheck` and `match --rescan` skip an item whose pasted link is waiting (below). For every other item `--recheck` reads the stored candidates and puts them in score order itself, as before.
- **Matching is unchanged for every item with no pasted link.** `pipeline.py` wasn't touched. `match.py` gained one check, which only an item with a waiting pasted link meets (below). Checked by running one library through the code from before and after, and again after the review's changes: twelve rips (AUTO, review, an explicit and a clean version, a remix, an artist under another name, not found, a link that scores well), every kind of decision with each sheet imported twice, a recheck, an artist's other name confirmed, an AUTO match rejected, and a rescan. Item states and reasons, every stored candidate, the export, the report, `state.json`, and the `replace` and `adopt --matched` plans came out the same to the byte, clock times aside. The one difference is the new `waiting: 0` in the summaries of the recheck and the confirmed name.
- **The matcher leaves an item alone while its pasted link waits** (`match.link_waiting`). `match --recheck`, `match --rescan` and confirming an artist's other name on the review page (which rechecks that artist's items) don't touch such an item: its state, reasons and candidates stay exactly as they are, nothing is searched for it, and the link is still first. Both commands say how many they left (`waiting` in their results; a line in the terminal; the page's message after a name is confirmed). When the owner has answered (a decision, a rejection of the link), the matcher takes the item again as it does any other. This is what keeps "first until the owner decides" true: the review page asks about the artist's name right after a pasted link is used, and a yes used to drop the pasted links of that artist's other songs without a word.
  - No mark can be left on a list the matcher makes: it stores only rows it made afresh, which never carry one, and it only makes a list for an item that has no waiting link.
  - One exception: a link the fingerprint gate already found `different` for the rip isn't waiting. It's never proposed for that rip again (step 09b), so the matcher takes the item and the link goes.
  - **`index rebuild` forgets a pasted link**, as it forgets every candidate: the index is a cache. After `match` the list is the best three by score with nothing put first, even when the search finds that very video again, and the link has to be pasted once more. A pasted link was never in `state.json` (step 07: it isn't a decision, and a recorded `url` would turn into `matched_user` on a rebuild), and that hasn't changed.
- **`&amp;` in a name** (the same messages were reported with `Pharrell &amp; Rosco P. Coldchain` and `Macklemore &amp; Ryan Lewis`). **Not found in the engine, and nothing was changed for it.**
  - The recorded answers say the engine never receives one. The recorded answer for a link (`get_watch_playlist`, ytmusicapi 1.12.3) names "Michael Franti & Spearhead" with a plain "&". Across all 95 recordings (searches, links, radios, albums, videos, playlists, artist pages) there are 200 names and titles with a plain "&" and not one web entity.
  - ytmusicapi passes YouTube Music's text on as it is, and so does the engine: the CLI prints the message, the page shows it as text, RPC sends it as JSON. So the `&amp;` was most likely added by whatever showed the message afterwards.
  - The two songs themselves weren't checked: that needs the network, which this work didn't use. To settle it, record one (`scripts/record_ytm.py watch -- <the link's id>`) and read the name in the file.
  - Two tests keep watch: one on the recorded link, and one that fails if any recording ever carries a web entity, and says where to decode it then (the gate: `youtube._from_track` and `_names`).
- **On the owner's library** (not done: this work never touched it): import the sheet with the twelve links again, or paste each on the review page. Export, and type `accept` on those rows (on the page: "Use this" on the first candidate).
- Choices made here, for the owner to change if they're wrong:
  - The mark is kept in the index, with the candidate, not in `state.json`. That is the smallest change, and it's why `index rebuild` forgets a pasted link (`docs/KNOWN-ISSUES.md`).
  - `match --rescan` doesn't search again for an item with a waiting link. Accept or reject the link first if the item should be matched afresh.
  - A link that isn't on YouTube Music doesn't push an earlier pasted link from the front: nothing came to take its place.
  - The message is word for word as it was. It's true now.
- **After a review of the change**, four more things were put right:
  - **The sheet with the links can be imported again after the accept.** A `url` row whose link scores low used to put the song back in `review` in the index even when that very track was already the owner's decision, so it dropped out of `plan adopt --matched` and `plan replace` until the accept was imported again (the engine did this before today's change too). Now such a row changes nothing: the song stays `matched_user`, counted as unchanged, with no message. A low link for a *different* track is still only a suggestion.
  - **Pasting a link the owner rejected before takes the rejection back.** The owner's newer word wins: the track comes off `rejected` in `state.json` and is an ordinary pasted link from then on. Before, it was shown first but dropped again by the next rejection of anything, and accepting it stored only the row's few details (no album) while it stayed on the rejected list.
  - **A pasted link is scored with the artist names the owner confirmed**, as the matcher scores a candidate. Before, a link could be called "below 0.60" when the matcher itself would score the same track above it (0.35 against 0.70 for a rip filed under a name the owner had confirmed as the track's artist).
  - **The matcher no longer forgets a waiting link** (above). As first built, a new match kept the pasted track only if it was among the best three by score.
- No new enum value, no new index version, no new dependency. New in the Candidate shape: `pasted`. New in the summaries of `match` and `match --recheck`: `waiting`. `ENGINE_API.md` says all of the above under "paste a link", and in the `match`, `review export` and `review import` rows.
- Checked: 1,966 tests pass on the iMac (1,936 before) and `ruff check` is clean. New tests: the stuck case (three candidates above the link; the next export has it as `cand1` with its low score; `accept` gives `matched_user` with that video, and a plan would fetch that video); the same link twice; another low link; a link that scores well after a low one; a dead link; `reject:1`; rejecting another candidate; deciding another way (a candidate, skip, only copy, an `accept` from a sheet exported before the paste); the sheet with the link imported again after the accept, twice, with `plan adopt --matched` and `plan replace` still taking the song; a rejected link pasted again, low and high, and what an older engine could have left on the rejected list; a link scored with a confirmed name; the review page and RPC, order and accept; a recheck, a rescan and a confirmed name leaving a waiting item alone, and what the terminal and the page say; the gate's exception; a rebuild; the order itself in the index; an export of items with no pasted link compared, cell for cell, with what the code wrote before; and the two for `&amp;`. Each new behaviour was also broken on purpose in a scratch copy to see that a test notices (14 breaks at first, 20 more after the review, all noticed). **Not run on the owner's library**, and not looked at in the app (it has no review screen yet).

2026-10-05, about midday. **Visualizer 8 as the owner set it last, and a choice of quality for the visualizer.**

- **Visualizer 8's newer standard.** Late on 2026-10-04 the owner changed four of its settings in Particle Accelerator (only the strongest pitches show, in a darker picture with hardly any glow and corners as dark as they go), pressed Set Standard and locked it, and asked for it here. As before, that was kept only in that app's settings, so it was written into Particle Accelerator's library, and the pin in `app/Package.swift` is moved to that commit. The lock is for Particle Accelerator's own controls panel; nothing here shows one.
- **Settings → Play Options → Custom Visualizer → Quality** (the owner: "It can stay at medium, but offer the choices"): Auto, Low, Medium, High and Ultra, which are Particle Accelerator's own five, in its menu's order. Medium until another is chosen. Like the other Play Options it's each profile's own.
  - A higher quality draws more sparks and a sharper picture. On the 2019 iMac, Visualizer 8 drops frames at High (and Auto means High there), which is why Medium stays the standard.
- **The Kit:** `CustomVisualizer` also says which qualities are offered, the standard one, and what's used for a saved name. One new test.
- **Checked:** `swift build` and `swift test` (70 tests) with the new pin, fetched from GitHub; Particle Accelerator's own CI passed on both Macs first. In the unseen test copy: the Quality row in Settings, showing Medium; and the stage's own report with Low chosen (75,000 sparks at 1280×720) and with High (300,000 sparks).
- **Not checked:** Ultra and Auto in the app (they go through the same line as Low and High; the unseen copy's window was covered at the time, and a covered window isn't drawn). Changing the quality while a visual is showing, and the row clicked by hand. Visualizer 8's new picture seen here by anyone but the owner.

2026-10-05, about 1pm. **The song skipped every few seconds on an Apple TV: the visualizer's listener, on an output with a long delay.** The owner heard the music drop for a moment every few seconds with the Mac's sound going to an Apple TV over AirPlay, on every page of the app.

- **The cause:** to hear the music, the visualizer puts a listening tap on what the player plays. Once a visualizer had been shown, the tap stayed on until the app was closed, whatever page was showing. On the Mac's speakers that costs nothing. An AirPlay device holds two seconds of sound, and there the system tops the player's sound queue up with almost nothing to spare; the tap takes the rest.
- **Measured,** with a small silent test player (zero volume, a made-up tone) and the system's own log, the Apple TV reporting a 2.012 s delay:
  - On the iMac's speakers, with or without a tap: each new piece of the song reaches the sound queue with 2.1 s of sound still in hand.
  - On the Apple TV, without a tap: the queue goes 1.2 to 1.3 s with nothing new to decode, and has about 0.1 s in hand when the next piece arrives.
  - On the Apple TV, with a tap: 1.5 to 1.6 s with nothing to decode, and the queue is empty every time, once every six seconds. The owner's own app showed the same (1.63 s, empty).
  - Nothing else was wrong: one program sending sound, a steady AirPlay stream with an exact clock, a strong Wi-Fi link, and no simulator running (the cause of the stutter found earlier the same day).
- **The fix:** the app doesn't listen while the sound goes to an output that holds a second of sound or more (`CustomVisualizer.longestOutputDelay`). `SoundOutput` (new, in the app) reads the output's delay from Core Audio and says when the Mac's output changes; `VisualizerSound` takes the tap off when the sound moves to such an output and puts it back when it returns. On the Local Visualizer the visual's place says why nothing moves ("The visualizer can't follow the music while the sound is going to Apple TV…").
- **So the visualizer doesn't move to the music over AirPlay.** That's the price of an unbroken song there. Hearing the music some other way on such an output (the Mac's own sound, with macOS's permission) would be a piece of work of its own, in Particle Accelerator.
- **Also measured, and left as it was at the time:** pausing and playing are about two seconds late on the Apple TV. The app pauses its sound queue within 0.04 s of being asked and has sound leaving the Mac within 0.3 s; the rest is the two seconds an AirPlay device holds. (The next entry puts Pause right, and corrects what was first said here: that every app has it.)
- **Checked:** `swift build` and `swift test` (71 tests, one new). The app's own `SoundOutput` code, run on this Mac with the sound on the Apple TV, read "Apple TV, 2.012 s". In the unseen test copy, with the setting on, the Local Visualizer showed the note in the visual's place.
- **Confirmed afterwards** (about 1:10pm): the owner reopened the rebuilt app, played to the Apple TV, and the skip was gone. The player's log for that minute showed no tap, and sound still in hand at every one of eleven top-ups (it went 1.2 to 1.3 s with nothing to decode each time, never the 1.5 s that empties the queue).
- **Not checked:** the tap coming off and going back on as the output is changed by hand (the app never changes the Mac's output itself, and neither did these checks); other long-delay outputs than an Apple TV; screen mirroring switched on.

2026-10-05, about 1:30pm. **Pause stops the sound at once on an AirPlay device.** The owner: "I would like the two second buffer to be removed. It doesn't happen for anything else, but for our app it does??" They were right, for Pause, and the entry above was wrong to say every app has it.

- **Measured on the Apple TV,** with two silent test players and the AirPlay sender's own log:
  - A player built as the app is (Apple's AVPlayer): on Pause its sound queue stops at once, but the stream to the Apple TV stays open, so the two seconds already sent play out. Earlier the same day the app's own pauses showed the stream shut 2.1 s after the queue stopped.
  - A player built on an audio engine, as many apps are: the stream is shut within 0.03 s of Pause, and the Apple TV goes quiet at once.
  - Taking the song out of the AVPlayer on Pause didn't help: the stream still ran on for 2.1 s.
  - Muting the Mac's output did: the mute left for the Apple TV in 0.001 s and was acknowledged in 0.006 s, and unmuting put the volume back as it was.
- **So on Pause, on an output with a long delay, the app mutes the Mac's output,** and unmutes it when what was already sent has run out (the output's delay and a fifth of a second: 2.2 s on the Apple TV). `PauseSilence` (new, in the app) does it; `LongDelayOutput` in the Kit says which outputs and for how long. It's what the Mac's own mute key does.
  - Only for a pause the owner asked for, only on such an output, and never to an output that's already muted. Only the output it muted is unmuted, and it's unmuted if the app is quit in those two seconds.
  - Play before the two seconds are up doesn't unmute early: what's left in the pipe is the old sound, and the new sound doesn't arrive before the unmute does.
  - **This is the one way the app ever changes anything about the Mac's sound output.** `SoundOutput` still never chooses the output.
- **Play is not quicker, and can't be made so on this output.** The stream runs the Apple TV 1.75 s behind what's sent (measured the same for both kinds of player), so any app's sound takes that long to come out after it starts.
- **Checked:** `swift build` and `swift test` (72 tests, one new).
- **Confirmed afterwards** (about 1:40pm): the owner, on the rebuilt app: "the pause happens immediately." The log for their two pauses shows the output muted 0.001 s after each, the volume command leaving for the Apple TV at once, and the unmute 2.2 s later (1.3 s after Play, in the one case where Play came inside the two seconds).
- **Still wanted by the owner:** Play. "The song countdown starts going down, but it still takes 2 seconds to hear something." In the log the song's clock starts about a second after Play and the sound is due a second after that, so the countdown does run ahead of the sound. Nothing here changes that yet.
- **Known:** for those two seconds the Mac shows its sound as muted; if the app crashed in those two seconds the output would stay muted until unmuted by hand; skipping to another song or jumping within one still lets two seconds of the old sound play on, since only Pause does this.

2026-10-05, about 2:15pm. **Why there was no delay the day before, and an AirPlay button in the player bar (first version).** The owner: "yesterday, about 24 hours ago, there was no delay… and now there's a delay? HAVE THE APP SEND it to the apple tv itself then." And then, their own guess: full screen mirroring has no delay, sound only has two seconds.

- **The owner's guess is right, and measured.** The Apple TV as the Mac's sound output holds 2.012 s of sound with sound only, and **0.082 s with the screen mirrored** (read from Core Audio a minute after the owner turned mirroring on). The system log for the day before, 12:30 to 3:30pm, has the screen-mirroring engine reporting once a second throughout. So nothing in the app got slower: the Apple TV was being used the other way.
  - With the screen mirrored, the two fixes above stand down by themselves, since both go by the output's delay: nothing is muted on Pause, and the visualizer listens.
- **The AirPlay button** (`AirPlayButton`, new: macOS's own button and list of devices, left of the volume slider). Choosing a device makes the app's player send what it plays to that device itself, not through the Mac's sound output. The device is then told to play and pause, and isn't two seconds behind.
  - While a device is being chosen, and while one is in use, the visualizer's tap is off: a player with a tap on it can't be sent to an AirPlay device. The page says so.
  - Pause doesn't mute the Mac's output then (the device pauses when told), and the picture watchdog stands down (the picture is drawn on the device).
  - The player notes when it starts and stops sending ("AirPlay from the app: on") in its log.
- **Seen in the owner's first try** (their app, a song playing, 2:09pm), in the system log: the player handed the song to the Apple TV; five pauses and plays in eight seconds each went to it as a command; the next song was handed over when the first ended, and was ready there 2.2 s later. No error was logged.
- **Not known yet:** how it sounded to the owner (how quickly Play and Pause act, and the gap between songs); a song played from YouTube; a song's video (two streams joined, which AirPlay may not take); the volume slider, the position slider and the lyrics' timing while sending; what the Apple TV's own remote does to the app.
- **Checked:** `swift build` and `swift test` (72 tests).

2026-10-05, about 2:45pm. **The visualizer on the whole screen.** The owner: "just like video. Fullscreen for visualizer, and lyrics to the side of it if selected on."

- **Full Screen for the visualizer, the same way as for a video.** On the Local Visualizer, with the custom visualizer showing, there's a Full Screen button under it (where the video's is), and a double click on the visualizer does the same. The visual then has the whole screen; the controls come up when the mouse moves and go away when it rests; Esc, a double click or Leave Full Screen brings the app back.
- **Lyrics to the side, by the setting that was already there.** Settings → Play Options → Show lyrics in full screen now covers both: with it on, the song's lyrics take a column on the right and the visualizer fills the rest (it isn't letterboxed as a video is: its own background is black). The lyrics button in full screen switches them for now, as it does for a video.
- **One screen for both.** `FullScreenVideo` became `FullScreenPicture`, and the model's `videoFullScreen` became `pictureFullScreen`. What stands where the cover would be is worked out in one place for the page and the whole screen (`PagePicture.showing` in the Kit, tested): a video once its picture is ready, the visualizer for a song that's playing, or else the cover. So a saved video that comes up next is still shown as a video, and a song after it gets the visualizer back.
- **It stays until the screen is given back.** A visualizer switched on with the page's Song | Video | Visualizer switch is switched on only for now, and the page goes back to the cover after the time chosen in Settings (30 minutes unless changed). On the whole screen that clock is ignored: the visualizer doesn't turn into a cover in the middle of a song. Back on the page, the setting rules again.
- **Drawn once.** While it has the whole screen the page underneath doesn't draw it as well, as with a video. Going in and coming out, the visual starts afresh.
- **Checked:** `swift build` and `swift test` (73 tests, one new). In the unseen test copy: the Full Screen button under the visualizer; the whole-screen layout with and without the lyrics column and the controls; and the visual drawing at the new shape (150,000 sparks at 2024×1024 with the screen to itself in a 1500-point window, 1668×1242 beside the lyrics; Medium's limit on pixels holds).
- **Not checked:** the real thing on the owner's screen, with a song playing: the double click, Esc, the mouse hiding, and how smoothly it runs at the iMac's full size. The owner's quality is High, where Visualizer 8 was already a little over what 60 frames a second allows on this iMac; the quality caps how many pixels are drawn, and the page's picture in a large window is at that cap already, so the whole screen shouldn't be much heavier; but that's by reasoning, not measured.

2026-10-07, about 2:15pm. **Picking several downloads at once.** The owner: "Discover downloads allow multiple selection of songs with cmd click, to move to library easier. Or mouse click and highlight several."

- **Discover → Downloads works like a Mac list now.** A click picks a line, ⌘-click adds a line or drops it, and Shift-click picks every line from the last one clicked to this one, across the Videos and Songs boxes. A click on the page beside the lines lets go of them. Double-click still plays.
- **What's picked moves together.** A right-click on a picked line is about all of them (Move to Library, Delete, Add to Playlist, Favourites: the menu every list of songs has), and dragging one onto Library in the sidebar takes the rest with it. A right-click or drag on a line that isn't picked is about that line alone, as before.
- **How:** which lines are picked is worked out in the Kit (`RowSelection`, tested). One drag carries one piece of text, so several songs' ids travel as its lines (`DraggedSongs`), and the sidebar's Library and Downloads rows read them back. No engine change: `listening.move` already took a list.
- **The one thing that looks new** is a picked line's tint (the app's highlight colour, lightly), and the words at the top of the page now say how to pick several. Nothing else on the page moved.
- **Checked:** `swift build` and `swift test` (79 tests, six new).
- **Not checked:** clicked in the running app. The unseen test copy can't click, so the tint, the Shift-click run and the drag of several are for the owner to try. Dragging the mouse across lines to pick them isn't built: a drag there carries the song to the sidebar.

2026-10-07, about 3:45pm. **A child's profile looks up only clean songs.** The owner: "If both a clean/explicit version show up, show only the clean. If there is no clean version, say that there is no clean version in the search. In settings if kids version is selected then offer a toggle, to allow explicit/original songs if a clean version is not found." Family mode was on the "Not yet" list; this part was started early at the owner's request.

- **What a child's profile finds.** In a profile marked as a child's, every lookup of songs gives only clean ones: the search, What's New and Find, an artist's page with their songs and albums, and imported playlists. A song found both clean and explicit shows only the clean one. A song with no clean version is left out, and the search says which: "No clean version was found for: …".
- **The switch.** Settings → Profiles → a child's profile's ⋯ menu → Allow Explicit Songs When No Clean Version Is Found. Off unless switched on; with it on, a song with no clean version is shown as it is. A song that does have a clean version still shows only that one.
- **Never assumed clean.** A song is clean only when YouTube Music marks it not explicit. One with no mark either way is treated like one with no clean version, and the note says "left out because it isn't marked clean or explicit", not that it's explicit.
- **Engine:** a new read-only module, `kids` (a filter over what a lookup found; it asks YouTube nothing), and `kids.set` (`docs/ENGINE_API.md`). The engine keeps no mark of its own: the app tells it when the library opens, before anything is looked up, and whenever the mark or the switch changes. Answers gain `kids_note` (in `discover.suggest` it joins `note`), and a song let through by the switch is marked `only_explicit`.
- **App:** the switch is saved with the profile (`allowsExplicit`), the search page shows the note above its results, and the words under "This is a child's profile" now say what it does.
- **Deviations and limits, for the owner:**
  - **Discover will be nearly empty in a child's profile with the switch off.** The radios Discover reads don't say whether a song is explicit (checked in the recorded answers: none carry the mark), so every pick is "not marked" and is left out, with the note saying so. Finding out would cost one more request to YouTube for each pick. To decide: pay that, or treat unmarked picks another way.
  - "Clean" means "not marked explicit". That's YouTube Music's own mark; nothing here reads lyrics.
  - Two results are the same song when the title (without its version words) and the first artist agree. Only what one lookup returned is compared: a clean version that the search didn't return isn't looked for.
  - For imports the matcher still prefers the explicit version first (the owner's own rule), and a song found only that way then counts as not found. Clean-first matching for a child's profile isn't built.
  - It's a filter, not a lock: there's no PIN, so the mark can be taken off, and explicit songs already in the child's library aren't hidden.
- **Checked:** `pytest` (1,974 passed, eight new), `ruff check`, `swift build`, `swift test` (three new).
- **Not checked:** in the running app with a real search: the ⋯ menu's switch, and the note on the search page. The artist page and import page don't show the note yet (the songs are left out there without a word); that waits for the owner's drawing of the new layout.

2026-10-07, about 4:50pm. **Videos and movies beside the music: add-ons in the engine, the owner's new sidebar, Explore and Movie Finder.** The owner: "from now on it won't be a music organizer, but a media organiser", with a drawing of the sidebar and a plan for movies. Their decisions: build it in Python, a Mac app today (a web page, Windows and Linux later), and test against the channels add-on and Public Domain Movies.

- **Engine: `addons`, a new read-only module** that speaks the Stremio add-on protocol: it reads an add-on's manifest (a word or an object for each resource; `stremio://` read as `https://`; only the last `/manifest.json` cut), builds every address one way, checks the manifest before asking so nothing pointless is sent, gives up after 8 seconds, and names an add-on that fails while the rest still answer. `addon.list`, `addon.add`, `addon.remove`, `addon.catalog`, `addon.details` and `addon.streams` (`docs/ENGINE_API.md`, with two new enums: stream kind and stream quality). The owner's list is kept in `addons.json` in the app's settings folder, written by `config`. It starts with three: Cinemeta (film details), the channels add-on, and Public Domain Movies.
- **A stream's quality** is read from its name: "cam" for one filmed off a screen (cam, telesync, telecine), said before any size, or else 4k, 1080p, 720p and so on.
- **`youtube.video` takes a `video_id`:** a channel's video or a trailer is shown as itself, with nothing looked for by name.
- **The sidebar, as drawn:** Music (the library's entries, with "+" as before); Videos (Channel, Movies, and Music Videos, which "+" brings back if removed); Media Discovery (Visualizer, Music Finder with Explore under it, Video Finder with Explore under it, Movie Finder, Downloads); Playlists, with Import Playlists at its foot. "YouTube Music" is Music Finder, "Local Visualizer" is Visualizer, and What's New and Find are one page (Music Finder → Explore) with a switch between them. The last page open is still remembered: an old "What's New" or "Find" opens Explore.
- **Video Finder → Explore:** channels in a grid, by section (Channels, Gaming, News, Sports, then the rest of the list's own genres), with Show More. A click opens a channel: its videos newest first; a double click plays one and carries on down the list, through the app's own player.
- **Movie Finder:** the film lists of every add-on (Popular, By Year, Best Rated, Public Domain Movies), a genre where the list has them, a search where it has one, Show More. A click opens the film over its own picture: length, year, rating, genres, cast, directors, summary, a Trailer button, and under them where it can be played from, add-on by add-on, each with its quality.
- **Deviations from the owner's plan, and why:**
  - Python inside the engine instead of Node and Express, by the owner's choice today; and no web page or proxy, since the Mac app asks the engine directly.
  - No torrent playing yet (the plan's 4c). It needs a new dependency, which `CLAUDE.md` says to ask about first; and the cross-platform player isn't chosen. Streams are listed, not played.
  - The plan's own test add-on (Phase 5) wasn't made: the owner named two real ones to test against instead.
- **Found while checking, for the owner:**
  - **The channels add-on is a snapshot from 2023.** It answers quickly and its list of channels suits the grid, but a channel's newest video there is from October 2023, and its search answers with an error. For today's videos, and for following a channel, the app should read the channel itself through `youtube`; the add-on is the catalogue of who to look at.
  - It has no Learning or Podcasts genre; the page says so. Those need another source.
  - Films still in cinemas appear in Cinemeta's lists with their details; none of the three add-ons has a stream for them, and the page says "None of your lists has this film to play."
- **Checked:** `pytest` (1,995 passed, 21 new for add-ons, replaying real answers recorded today), `ruff check`, `swift build`, `swift test` (86, four new). Live, once: the three manifests, channels by genre and a second page, a channel's videos, a film search, a film's details and streams. In the unseen test copy: the sidebar, Explore's grid, Movie Finder's grid, and a film's page.
- **Not checked:** clicked or played in the running app: opening a channel, playing one of its videos (it plays as sound until the player page is switched to Video), the Trailer button, and the search box. The pages that already say "YouTube" in their words (the search page, some messages) still do.
- **Not built, with a page that says so:** Video Finder (searching for any video), Videos → Channel (the channels you follow) and Videos → Movies (downloaded films).

2026-10-07, about 5:30pm. **A film player (libmpv), and films played from a torrent as they arrive (libtorrent).** The owner said yes to both dependencies, after asking which of FFmpeg and libmpv was better (mpv is a whole player built on FFmpeg; FFmpeg alone only decodes).

- **The film player** (`FilmPlayer`, in the app): libmpv 0.41 with FFmpeg 9, through the Swift package MPVKit, pinned to version 1.0.0. Its `MPVKit` product is the LGPL build; `MPVKit-GPL` is never used. It plays MKV, MP4, MOV, AVI and the rest, with the graphics chip decoding where it can (VideoToolbox), drawn with Metal. A film has the whole window: play and pause (also the space bar), a slider to move through it, back 10 and forward 30 seconds, volume, Full Screen (also a double click), Close. The controls go away three seconds after the mouse rests. The music pauses when a film starts.
- **Videos → Movies** is a real page now: the video files in the Mac's Movies folder (and folders inside it), by name, with their kind and size; double-click plays one; Open File… plays one from anywhere. Nothing there is changed.
- **Engine: `torrents`, with `torrent.play`, `torrent.status` and `torrent.stop`.** The engine joins the torrent, fetches only the film's file in the order a player reads it, and gives the app an address on this computer that the film player opens like a web video. It answers ranges of bytes as their pieces arrive, so moving through the film works. One new dependency, `libtorrent` (BSD), which has ready-made builds for this Mac, Apple silicon, Windows and Linux.
- **What it holds to** (also in `CLAUDE.md`): only ever from the owner's click; **no port is opened on the router** (UPnP and NAT-PMP off); the address listens on 127.0.0.1 only, with a key in its path made afresh each time the engine starts; what arrives is kept in the app's cache folder, never the library, and deleted when the film is closed, after ten minutes unasked-for, and when the engine stops.
- **The owner should know: a torrent shares as it fetches.** While a film plays, the parts already here are given to others in the same torrent. The film's page says so under its streams.
- **A film's page plays its streams:** a Play button on each. A web address or a torrent opens in the film player; a video by its id plays in the app's own player. A torrent's line in the player says how it's going ("6 sources · 4.7 MB/s · 9% here").
- **Rule 3 gained one exception:** libtorrent itself writes and deletes in `torrents/` in the app's cache folder. `torrents` opens those files only to read, which the write-rules test checks.
- **Checked:** `pytest` (2,013 passed, 18 new: the range a player asks for, which file is the film, the magnet link, and the address served from a stand-in film), `ruff check`, `swift build`, `swift test` (88, two new).
  - The player, in the unseen test copy with mpv's own log on: a test MKV (H.264 with AC3 sound) decoded by VideoToolbox on the iMac's Radeon Pro 570X, drawn at the window's full size, sound started.
  - Torrents, live, with The Kid (1921) from Public Domain Movies: the first megabyte in 13 seconds, a jump to the middle in 3, the last bytes in 3; everything deleted on stop.
  - End to end in the unseen copy, muted: the app asked the engine, the engine joined the torrent, and the film player showed its first frame from the engine's address. The cache folder was empty after the engine stopped.
- **Not checked:** by eye or ear. The unseen copy can't photograph a video surface, so nobody has yet looked at the picture, heard the sound, or used the controls, the slider or Full Screen. HEVC, 4K, HDR and files with several sound tracks or subtitles are untried.
- **Limits:** no subtitle or sound-track menu yet (mpv plays the first of each); a film can't be kept yet (nothing is saved to the Movies folder); if the engine is killed outright, what a torrent had fetched stays in the cache folder until something clears it; the package's maker says MPVKit's Metal drawing rests on a patch mpv hasn't taken in yet, and that they update it rarely.
- **CI:** the app's build now fetches MPVKit's ready-made libraries (about 1.7 GB with the GPL ones it also lists, which aren't linked), so the Mac runs will take longer.
- **Two test hooks, for checking unseen:** `MUSICORG_FILM=<file>` or `torrent:<info-hash>:<file number>` plays it once, silently, when Videos → Movies opens; `MUSICORG_FILM_LOG=1` has mpv say what it's doing on stderr.

2026-10-07, about 6pm. **A film's page was bigger than the window, and made every other page so.** The owner, with a picture of a film's page cut off under the sidebar: "there's no set frame… This has messed with the music library, and other libraries."

- **The fault:** a film's page laid its backdrop picture out as part of the page, filling the frame from edge to edge, so the page took the picture's size, not the window's. In a window narrower or shorter than the picture it spilled out on every side. And since the app keeps the pages already opened stacked together, each as it was left, the stack grew to the biggest of them: once a film had been opened, the song lists and the other pages were laid out to that size too.
- **The fix:** the page takes the room it's given, and the picture is laid behind it, cut to fit, where its size can't change the page's. Nothing else on the page moved.
- **Checked:** `swift build`, `swift test`, and in the unseen test copy a film's page in a 900-point window: title, chips, summary and the Back arrow all inside the page, beside the sidebar.
- **Not checked:** in the owner's app, with the sidebar open and shut and the window dragged to other sizes; and that the song lists are their right size again after a film has been opened (by reasoning they are, since nothing else was larger than its room).
- **Why it was missed:** the page was pictured once, in a 1,500-point window, where the picture happened to fit. A new page is to be pictured narrow as well as wide.

2026-10-07, about 6:45pm. **The film's picture fits the window and stays in the middle; no page's name in the top bar; search beside the sidebar button.** The owner, with three pictures of a film drawn enlarged or off to one side under a top bar that was still showing, and a fourth of the top bar as they want it: "I moved the search bar, and removed the title. This should be done on absolutely every page." They also found the sound in time with the picture.

- **The film was drawn at the size the window had when it started.** The first version drew through the MPVKit package's Metal patch, which has no way to hear that the window changed (its `control` is "not implemented", and the size is read only when a film loads). Make the window bigger, or go full screen, and the film stayed its old size in a corner; make it smaller and only a part of the film showed, enlarged.
- **Now mpv draws into the app's own view, and is told the view's size with every frame** (mpv's render API, over OpenGL: `FilmGLView`, in a small target of its own, `FilmDrawing`). The film is fitted and centred whatever the window does. OpenGL is marked deprecated by Apple, though still shipped; that one target is built with warnings off, since every line in it would raise one. The Metal patch isn't used any more.
- **Frames are drawn on a thread of their own.** Drawn on the main thread, the unseen test copy dropped about five frames a second; on their own thread, none. So a busy interface doesn't cost the film frames.
- **The graphics chip still decodes** (VideoToolbox), straight into the drawing, once the view asks for modern OpenGL (3.2 core; with the old kind mpv fell back to copying each frame through memory).
- **The top bar goes away for a film,** as it does for the big cover: the title and search no longer sit over it, in a window or full screen.
- **No page's name in the top bar, on every page** (an album's page too): each page says what it is itself. **Search is beside the sidebar button** at the top of the sidebar; with the sidebar shut, both are at the left of the top bar, still side by side. ⌘F as before.
- **Checked:** `swift build` (no warnings), `swift test` (88). In the unseen test copy, by mpv's own log: the test film decoded by VideoToolbox; with the window changed from 3,400 to 2,600 pixels wide while it played, mpv laid the film out again for the new size, centred (2702×1520 at 349,0, then 2600×1462 at 0,29); no frames dropped and sound and picture in time over 14 seconds. A picture of the Explore page with the new top bar.
- **Not checked:** by eye, in the owner's app: the film in a window, resized, full screen, with the sidebar open and shut; and the top bar with the sidebar shut. mpv logs one OpenGL error as the first frame is set up ("invalid framebuffer operation") and none after; the film plays, and it's left as found.

2026-10-07, about 7:15pm. **Two search buttons, and a strip across the top of a film in full screen.** The owner sent a picture of each: "errors".

- **Two search buttons with the sidebar shut.** The search button put at the top of the sidebar stays in the top bar when the sidebar is shut (macOS moves it there with the sidebar's own button), so the second one added for that case was one too many. It's gone; there is one button, in either state.
- **A strip across the top of a film.** With the app already in full screen, opening a film took the top bar's buttons away but left the bar's empty strip over the film. In a window the bar goes cleanly (pictured in the unseen copy: no strip). So a film opened in a window that's already full screen now leaves the top bar alone and asks macOS to keep it out of sight with the menu bar; it comes down only when the mouse goes to the top of the screen. A film opened in a window, and then made full screen, hides the bar as before. Leaving full screen in the middle of a film puts it back to the window's way.
- **Checked:** `swift build` (no warnings) and `swift test`.
- **Not checked, and a guess as to the cause:** the unseen copy can't go full screen, so the strip was never reproduced here; the fix follows from what the two pictures show (the strip only over the page's part of the bar, in full screen) and from the bar going cleanly in a window. The owner's eyes decide whether it's gone.

## 0.1.1 — in progress

### Step 09d: Duplicates and preferred names (the owner's requests, 2026-09-30)

- **Duplicates keep the best copy:**
  - The best copy is chosen by: lossless first; then a rip that isn't a YouTube conversion; then the higher bitrate; then the bigger file.
  - Any CD or iTunes rip beats a converter site's MP3, whatever the bitrates. The owner's library showed why: the first tidy plan would have kept a 128 kbps converter MP3 of a song over its 121 kbps iTunes CD rip.
  - A YouTube conversion is recognised by ffmpeg's "Lavf"/"Lavc" encoder tag, or a converter's name in the file name (`scan.youtube_converted`). The converter names y2meta, x2mate and yt5s were added.
  - When both copies come from one YouTube upload, the higher-bitrate one lost less in its re-encode.
  - `plan adopt --matched` copies in only the best rip of each song. The others (`adopt_duplicate`) are linked to it (`superseded`), and nothing is copied.
  - `plan tidy` does the same for songs already in the library twice. The lesser copy and its `.lrc` go to `_Replaced/`, and its rip is linked to the kept file. A kept file named ` (2)` gets its plain name back when that name is free.
- **Preferred names:**
  - `musicorg names set "JAŸ-Z" "Jay Z"` saves the owner's spelling in state.json (`names`).
  - New songs use it at once (replace, and adopt with official details).
  - `plan tidy` writes it into the tags (title, artist, album artist, album). It moves each file to the folder and name those give, taking the `.lrc` along, and the album's `cover.jpg` once its old folder has no songs left.
  - Only whole names are replaced, never inside the preferred spelling, so doing it twice changes nothing. A test showed "Band" → "The Band" could otherwise become "The The Band".
- **Fixed, `fileops.undo`:** undo planned every step before running any. So a batch that retagged a file and then moved it lost the tag restore: when it was planned, the file wasn't yet back where it had been tagged. Each step is now planned right before it runs. A regression test covers it.
- Undo now refreshes the index's library tracks for every file a batch touched (moves, set-asides), not only commits.
- **After the owner's tidy run** (5 duplicates set aside, 19 songs renamed; all 24 done), two gaps were found and fixed:
  - Two copies of one song, "04 Song.m4a" and "04 Song.mp3", shared one "04 Song.lrc", and setting the MP3 aside took the shared lyrics file with it. A lyrics file that both copies share now stays. `lyrics --missing` fetches the lost one again.
  - Moves left the old artist folders behind, empty. `fileops.remove_empty_folders` removes a `Music/` folder holding nothing but junk, and its emptied parents. No data is removed, so it isn't journaled, and undo recreates any folder a file moves back into. Renames call it for the folder they leave, and `plan tidy` also clears empty folders already in the library.
  - Both have tests.
- The plan and batch kind `tidy` is new (ENGINE_API.md → Enums), as are the `names` key in state.json (contract section 5) and `plan.create` kind `tidy` over RPC.
- 1,175 tests pass. New:
  - preferred names applied to the library and undone, with lyrics and cover moved along
  - new songs using them
  - only the best rip adopted, with the other linked, and undone
  - a library duplicate set aside with its " (2)" dropped, and undone
  - the `names` commands
  - the undo regression test

## 0.1.0 — 2026-09-30

The first version of the engine, run on the owner's own library.

**What it does:**
- Scans the owner's rip folders without ever changing them. It indexed 1,890 rips.
- Matches the rips on YouTube Music.
- Fixes each matched song's details, on the owner's own audio (step 09c).
- Adds covers and timed lyrics.
- Every change is journaled and can be undone.
- It can also replace a rip with the official YouTube Music download. The owner decided to keep their own audio, since many rips are CD rips, so that stays for later users moving off streaming services.

**On the owner's library:**
- 817 songs are in the library, with official details, 787 covers and 708 synced lyrics.
- 1,012 rips are waiting in review, and 61 weren't found.

**For the v0.2 Mac app:** `musicorg serve`, the JSON-RPC interface in `ENGINE_API.md` section 2.

**Tests:** 1,169 pass on macOS (Intel and Apple Silicon) and Windows, and CI runs the secret check.

**Left for later** (`docs/KNOWN-ISSUES.md`):
- relocating a source folder
- state.json backups
- keeping one copy of songs the rips have twice
- preferred artist spellings
- a check of synced lyrics against playback, in the Mac app's karaoke view

Steps, in order:

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

### Step 03b: fileops (guarded operations, journal, recovery, undo, plans)

- `fileops.py` now holds the whole safety core of contract section 6:
  - `guard(paths, path, allow)`: resolves links, then the path must be strictly inside `Music/`, `_Replaced/`, `_Staging/` or `.musicorg/` (or the ones asked for). A managed folder itself doesn't count, so nothing can replace or remove `Music/`. Case matters except on Windows, so a differently-cased path is refused rather than guessed at.
  - `_move_no_overwrite`: every move. Reserve the first free name with `open(name, "xb")`, journal it, then `os.replace`. Names are compared ignoring case and Unicode form, so `Song.m4a` and `song.M4A` collide on any drive. EXDEV → `CrossVolumeError`; on Windows a `PermissionError` gets 5 more tries 0.4 s apart, then `FileInUseError`. On any error the reserved name and any new folders are removed and the file stays where it was.
  - Operations, each journaled as `intent` (fsynced) → act → `done`: `stage_path`, `commit`, `copy_in`, `supersede`, `move`, `trash`, `write_tags` (journal and undo side only) and `write_sidecar`. `copy_in` compares the SHA-256 of what it read with the copy read back, and checks the original's size and modification time before committing and again after.
  - Journal: `.musicorg/journal/YYYY-MM-DD.jsonl` (UTC date) with `batch_start`, `intent`, `reserved`, `done`, `failed`, `recovered` and `batch_end` lines. Library paths are stored relative to the root. F_FULLFSYNC on macOS for intents, reserved names, batch starts and ends, and copied files.
  - Batches: `with fileops.batch(lib, kind) as b`, or open batches for step 09b (`open_batch`, `resume_batch`, `close_batch`), which recovery never closes.
  - Recovery (the step 03a hook in `library.open`): each `intent` without `done` is completed or rolled back from what's on disk, journaled as `recovered` and logged. Batches that never ended are closed as `interrupted`.
  - `undo(lib, batch_id, dry_run, jobs)`, itself a batch of kind `undo`.
  - Plans: `Plan`, `PlanOp`, `FileCheck`, `FolderCheck`, `new_plan`, `save_plan`, `load_plan`, `validate`, `check_op`, `sha1_head`.
  - `discard_staged`, `clean_staging` and `write_export`.
- CLI: `musicorg journal list [--limit N]` (no lock) and `musicorg undo <batch_id> [--dry-run]` (lock), both with `--json`.
- `scripts/fileops_demo.py <root> <files…>`: copies files into a library in one batch and prints the batch id.
- 589 tests pass (2 skipped: the real-Trash test and an existing Windows-only one). Every operation is tested for success, a crash before acting, a crash between acting and `done`, a name collision, undo, and refusing a file outside the library. Also: links out of `Music/` refused as source and destination; a file that appears right after the name check is never overwritten; `clean_staging` and `discard_staged` never follow links, and refuse a `_Staging/` that leads elsewhere; a real process killed mid-move (`os._exit`) and recovered by the next one; the manual check below, run end to end.
- Manual check, run on the iMac with a scratch library and three generated files (`Melody A.m4a`, `Melody A.mp3`, `Pink Noise.m4a`), using a scratch settings folder so the default library didn't change:
  1. `musicorg init` created the scratch library (with the usual Time Machine warning).
  2. `python scripts/fileops_demo.py <root> <3 files>` printed `b_20260928-202402-e5df5b` and copied the files into `Music/Unknown Artist/Unsorted/`.
  3. `musicorg journal list` showed it: `demo  closed  3 copied in`.
  4. `musicorg undo b_20260928-202402-e5df5b --dry-run` listed three moves to `_Replaced/` and changed nothing; `musicorg undo b_20260928-202402-e5df5b` did them as batch `b_20260928-202404-742e94`.
  5. `Music/` was empty (the `Unknown Artist/Unsorted/` folders the batch made were removed too), `_Replaced/Unknown Artist/Unsorted/` held the three files with the originals' SHA-256, and the originals' SHA-256, sizes and modification times were unchanged. `journal list` then showed the batch as undone by the undo batch.
  - The real-Trash test (`MUSICORG_INTEGRATION=1 pytest -k real_trash`) passed once on the iMac. It leaves one file, `trash-test (safe to delete).txt`, in the Trash.
- Deviations and additions:
  - **Signatures:** the functions take the library (a `Library` or its `LibraryPaths`) first, which the prompt's signatures leave out: `guard(paths, path)`, `discard_staged(lib, path)`, `clean_staging(lib)`, `write_export(lib, path, data, sources=...)`, `validate(lib, plan)`, `check_op(lib, op)`. `write_export` takes the registered sources as a required `sources=` argument until step 05 stores them.
  - **Lock:** every operation, batch, plan save and staging clean-up refuses to run unless this process holds the library's lock. `write_export` and reading the journal or a plan need no lock.
  - **Reserved names:** the intent is journaled first (with the name expected to be free and the folders to be created), then the reserved name in its own fsynced `reserved` line, then the move. So a crash between reserving and journaling still leaves recovery something to find. Recovery only removes a reserved name that's empty and newer than its intent.
  - **Deletes outside `_Staging/`** (contract 6.6 now says so): an empty reserved name that was never filled, and, during undo, folders the undone batch created that are empty again apart from junk (which goes with them). Without the second, undo would leave empty `Artist/Album` folders behind, and `Music/` wouldn't be back as it was.
  - **`stage_path`** isn't journaled: it only creates `_Staging/<batch_id>/` and picks a free name there, and staging is scratch space.
  - **`write_tags`:** there's no tag reader until step 04, so `fileops.tag_access` (read and write a file's tags as a JSON-safe dict, the cover as its SHA-256) is the hook step 04 fills. Until then `write_tags` says "not implemented yet (step 04)", and recovery leaves an interrupted tag write for later. Undo restores the fields the batch changed that still have the batch's values; fields changed again later are left alone and named. With no later changes that's exactly the before-state, added fields removed. No collision test: a tag write doesn't create a name.
  - **`write_sidecar`:** `suffix` is `.lrc` (or another short extension) for `<track>.lrc`, or `cover.jpg` for the folder's cover. Returns the sidecar's path, or None when a different `cover.jpg` was left alone. Superseding a different `.lrc` is its own journaled `supersede`, so undo reverses the two in order.
  - **Undo:** it stops at the first problem and skips operations an earlier undo already reversed, so it can simply be run again. Undoing an undo re-applies the batch. A file that's no longer where the batch left it is skipped and reported. Undo closes an open batch. The queue hook is a `BatchJobs` object (`cancel_queued`, `running`) that step 09a provides: queued jobs are cancelled first, then undo refuses while one is running. A dry run cancels nothing.
  - **Plan preconditions:** `FileCheck` (size, modification time in nanoseconds, and `sha1_head`, the SHA-1 of the first 1 MiB), the item's state through a lookup function (no lookup means the check fails, not that it passes), and `FolderCheck` for the target folder: whether it existed, and its entries apart from junk. A plan fails if that folder has become a file, or if something new has the target's name (ignoring case). Other new files there are fine, e.g. an album's earlier tracks committed by the same batch.
  - **`copy_in`** refuses a source inside the library. If the original changes after its verified copy was committed, that's logged as an error instead of failing the operation.
  - **`move`** onto the file itself does nothing. Case-only renames aren't supported yet.
  - `journal list` has `--limit N` (default 20), since RPC `journal.batches` takes a limit.
  - `ENGINE_API.md` → Enums gained batch kind, batch status, journal operation and undo step status.
  - New errors: `OutsideLibraryError`, `CrossVolumeError`, `FileInUseError`, `FileOperationError`, `SourceChangedError`, `IntegrityError` (exit 1), `NotFoundError` and `UndoError`.
  - **Tests:** every test gets a fake Trash. The real-Trash test is marked `integration` and runs only with `MUSICORG_INTEGRATION=1`, never in CI, so a plain `pytest` never puts files in the owner's Trash. In tests, fsync only checks that its file is open and F_FULLFSYNC is skipped: on the iMac's spinning disk F_FULLFSYNC took 0.14–0.25 s and fsync about 10 ms, which made the suite several times slower. One test checks that macOS uses F_FULLFSYNC, and another that the intent is flushed before anything changes.
  - `fileops.py` is about 2,300 lines. It stays one module, as rule 3 and the write-rule test expect, with a section per topic.
- CI: the first push with step 03b (together with step 04) passed every step 03b test on all three runners. Only step 04's Ogg sample failed, on the Macs; see step 04.

### Step 04: Tags, probe and the audio-integrity check

- `tags.py` (mutagen 1.48.1):
  - `TrackTags`: every field of contract section 4, standard and `MUSICORG_*`, plus `cover` and `cover_mime`. None means absent (in a change: leave it); `REMOVE` deletes a field; `warnings` says what went wrong while reading.
  - `read_tags`: M4A, MP3, FLAC, Ogg Vorbis and Opus. WebM, raw AAC, WAV and anything else give empty tags and a warning, and never raise. Messy MP3s read without errors: ID3v1 only, ID3v2.2, duplicate frames, broken frames, UTF-8 text in a frame that says it's Latin-1 (repaired, with a warning), and files that aren't MP3 at all.
  - `write_tags`: changes only the fields it's given and keeps the rest (iTunSMPB and other freeform atoms, composer, comments, other TXXX frames, unknown frames in v2.3 files, back covers and other pictures). M4A provenance goes in `----:com.apple.iTunes:MUSICORG_*` atoms as UTF-8; MP3 is ID3v2.3 with the year in TYER and provenance in TXXX frames; FLAC, Ogg and Opus get plain comments and a front-cover picture block. `MUSICORG_VERSION` is joined with `; `. One mutagen save call.
  - `probe()` (ffprobe): codec, duration, kbps, sample rate and channels. `audio_hash()` (ffmpeg): MD5 of the decoded audio, always computed fresh. No cache yet; the prompt allows one only for scan-time reporting, so step 05 can add it.
  - `new_track_id()`: a UUIDv4.
- `fileops.write_tags` is now the verified write (contract 6.7): hash the audio → copy the file into `_Staging/` → write the tags on the copy → hash the copy and read its tags back → journal the intent (complete before- and after-state, the audio MD5, the staged copy) → keep the old cover in `.musicorg/undo-art/` → swap the copy in with `os.replace` (with the Windows retry) → `done`. If the audio or the tags don't match: `IntegrityError`, the copy is discarded and the file is untouched.
  - Recovery reads the file's tags: the new ones mean the swap happened (completed); the old ones mean it didn't (rolled back, the staged copy removed).
  - Undo works from the journaled before-state: fields that were absent are removed, including a `MUSICORG_ID` the batch added, and a replaced cover comes back from `undo-art/`.
  - A file's `MUSICORG_ID` can't be changed or removed, except by the undo of the batch that added it.
- `scripts/check_compat.py [<folder>]`: a scratch library with a 10-second tone as M4A and MP3, tagged with every field through the real verified write, and a checklist for Apple Music and Kid3 or Picard.
- 678 tests pass on the iMac; 3 are skipped (the real-Trash test, and two Windows-only ones). 97 are new, and step 03b's 7 tests with a fake tag store were replaced by real ones. Every field round-trips in all five formats with the audio hash unchanged; Unicode (Japanese, emoji, accents); covers in JPEG and PNG; unmanaged atoms, frames and comments survive; the messy MP3s above; files whose tags aren't read; values outside the schema refused; probe of all eight sample kinds; adding lyrics and a cover then undoing them leaves neither and the same audio; a write that changes the audio or doesn't read back is refused with the file untouched; a crash on either side of the swap is recovered; the track id rule; and, on the Windows runner only, a file held open by another handle gives `FileInUseError` with the original intact.
- Compatibility check (`scripts/check_compat.py`, run on the iMac):
  - ffprobe reads every field of both files, the `MUSICORG_*` ones included, and the 600 px cover.
  - macOS's own reader (`afinfo`, AudioToolbox) reads the title with its Japanese, the artist, album, year, genre and track number from both the M4A and the ID3v2.3 MP3. It doesn't report artwork or lyrics.
  - Apple Music (Music app, checked by the owner in Get Info): both files show the right title with its Japanese, artist, album artist, album, genre, year, track and disc numbers, the artwork and the three lines of lyrics. The M4A shows the explicit (E) mark.
  - Kid3 3.10.1 (`kid3-cli`, installed on the iMac for this; Picard wasn't needed): both files show every `MUSICORG_*` field with its value, the front cover, the lyrics with their Japanese and emoji, and the explicit flag (M4A "Rating/Advisory 1", MP3 `ITUNESADVISORY 1`). Kid3 reports the MP3's tag as ID3v2.3.0.
- Deviations and additions:
  - **Types:** the year, track and disc numbers are whole numbers, and the year is written as 4 digits (a full date in a rip reads as its year, and stays in the file unless the year is changed). `explicit` is True/False. `match_score` keeps 3 decimals. `only_copy=True` writes `1`; False removes the field, since the contract only defines `1`. `version` is a list of tokens.
  - **Empty text** counts as "not given", so it leaves a field as it is rather than erasing it; `REMOVE` erases.
  - **Checked values:** `MUSICORG_SOURCE` and `MUSICORG_MATCH` must be enum values, numbers must be in range, and the cover must be JPEG or PNG; anything else is a ValueError before a file is touched.
  - **Several values in one field** (two ARTIST comments, duplicate TIT2 frames) read joined with `; `. Writing that field stores one value.
  - **Covers:** the managed cover is the front cover: APIC type 3, picture type 3 in FLAC, Ogg and Opus, and M4A's `covr` (all of it: M4A has no picture types). Other pictures are kept.
  - **Lyrics in MP3:** all USLT frames count as the lyrics; a write replaces them with one (language `eng`).
  - **Totals:** a track or disc total isn't kept when its number is removed, because ID3 can't store a total alone.
  - **Vorbis aliases:** TOTALTRACKS, TOTALDISCS and UNSYNCEDLYRICS are read when the usual names are missing, and replaced by the usual names on write.
  - **Explicit:** `rtng` 1 is explicit and 0 not; iTunes' 4 also reads as explicit and 2 ("clean") as not. MP3 and Vorbis use `1`/`0`. The contract's table now says so.
  - **ID3v2.3:** mutagen converts only when `update_to_v23()` is called first; saving with `v2_version=3` alone kept the v2.4 TDRC frame. The conversion deletes v2.4-only frames, so the ones players also read in v2.3 (sort names TSOP, TSOA, TSOT, TSST; TMOO; TPRO; RVA2 ReplayGain) are carried across, as mutagen's documentation suggests. Other v2.4-only frames (e.g. TDRL, TDTG, SIGN) are dropped, and frames mutagen doesn't know can't be kept from a v2.4 file (logged). Only the library's copies are ever written, never the owner's rips.
  - **undo-art:** a PNG cover is kept as `<sha256>.png` (contract section 1 updated).
  - **The verified write also** reads the tags back from the copy and compares them with what was asked, and checks the file's size and modification time just before the swap (`SourceChangedError` if another app changed it).
  - **Journal order:** a tag write's intent is journaled once the staged copy has passed both checks, just before the swap; the library file isn't touched before then. A crash earlier leaves only the staged copy, which `clean_staging` removes.
  - **Undo** keeps step 03b's rule: a field changed again by a later batch is left as it is and named.
  - `REMOVE` moved from `fileops` to `tags`, and step 03b's `tag_access` placeholder is gone.
  - **Recovery of a move** now also compares the reserved file's size with the size in its intent, so an empty reservation whose source vanished isn't taken for a finished move.
  - New `samples` test fixture: 3 seconds of melody A as M4A, MP3, FLAC, Opus, Ogg Vorbis, WebM, WAV and raw AAC, plus an MP3 with no ID3 tag.
  - Homebrew's ffmpeg (the CI Macs) has no libvorbis, so the first CI run couldn't make the Ogg sample. The fixture now uses libvorbis when it's there and ffmpeg's own Vorbis encoder otherwise (marked experimental, stereo only), and the Ogg sample is stereo either way. Both paths were run on the iMac.
  - New error `AudioError`. `ENGINE_API.md`: batch kind `demo` now covers both manual-check scripts.
- CI: green on all three runners (678 passed on each, 3 skipped), once the Ogg sample was fixed. Windows ran the file-in-use test and the link tests.

### Step 05: Read-only scan of existing rip folders

- `normalize.py`: `parse_filename`, `parse_tags`, `best_parse` and `compare_key`.
  - Takes out junk (official video/audio/lyric video, lyrics, visualizer, audio, HQ/HD/4K/1080p, bitrates, free download, out now, premiere, full song, `[… Release]` label tags, emoji and symbols) and keeps version tokens as `kind[:detail]` (remix, radio edit, extended, VIP, bootleg, flip, live, acoustic, unplugged, remaster, instrumental, sped up, slowed, nightcore, cover, demo, clean, explicit), inside brackets, nested brackets, and as dash-separated parts.
  - Artists split on feat./ft./featuring/with, ` x `, ` & ` and `,`, main artist first. A remixer named where the artist should be is recorded as a version token and marked uncertain.
  - Confidence: a clean "Artist - Title" is 0.9; names from real tags 0.95; an artist tag with a title-only file name 0.8; a dash spaced on one side only ("Artist- Title") 0.75; a single token 0.3; a detectably reversed order 0.4. Bands: high ≥ 0.8, mid, low < 0.5.
- `index.py`: `index.sqlite` with the prompt's tables, `PRAGMA user_version` 1, WAL, a connection per thread, read-only connections for no-lock commands (a missing index reads as empty). Stable item ids: `i_` + 16 hex characters of SHA-1 of `<source_id>/<rel_path>` (NFC).
- `scan.py`: sources (`add_source`, `remove_source`, `list_sources`), `scan`, `scan_library` and `rebuild`.
  - The scan walks each source without following links, skips hidden files and folders, junk names and any folder holding a library, and indexes the audio extensions from the prompt: probe, tags, best parse, `sha1_head`. Files are only ever opened for reading.
  - Incremental: a file with the same size and modification time isn't read again. Files that have gone are dropped from the index.
  - Flags: `not_adoptable` for WebM, raw AAC and WAV; `suspect_upscale`; `unreadable` when ffprobe can't read the audio.
- CLI: `sources add|list|remove`, `scan [<id>…]` (progress on stderr) and `index rebuild`. `status` now shows the sources, library tracks, item counts by state with percentages, and the low-confidence count.
- 859 tests pass (3 skipped, as before). New: 119 file-name cases in `tests/data/filenames.tsv`, plus tests of tags parsing, name comparison, the index, the scan (incremental rescans, sources left byte-identical, links, a library inside a source, a missing source, an unreadable folder), sources, the rebuild keeping ids and decisions, and the commands.
- **Acceptance** (2026-09-29), run on the owner's Apple Music media folder (1,890 audio files, 9.2 GB: 1,491 MP3, 398 M4A, 1 WAV) as the source of a new library on the Desktop, with the Music app closed:
  1. `touch` a marker; `sources add`, then `scan`: completed, all 1,890 files read in 92 s. A second scan took 1 s (1,890 unchanged).
  2. `find <source> -newer <marker> ! -name .DS_Store` printed nothing, for the source and for the whole Apple Music folder around it. The same after `index rebuild`.
  3. `status`: 1,890 items, all `new`; 90 flagged `suspect_upscale`, 1 `not_adoptable` (the WAV). Low parse confidence: 30 on the first scan, 22 (1.2%) after the fixes below, with 1,864 high and 4 mid.
- **Fixes from the real library.** Reading the first scan's parses turned up names the table didn't cover. Each is now a case in the table:
  - Download sites as the first part ("Y2meta.app - ", "onlymp3.to - ", "x2mate.com - ", "yt5s.io - ") were taken as the artist; any web address at the start or end of a name, or alone in brackets ("(mp3convert.org)"), is now junk. Only common endings count, so "Mr.Kitty" and "Will.i.am" stay names.
  - onlymp3.to's ending `-<video id>-192k-<timestamp>` is taken off; the video id is kept in `notes`.
  - A dash or tilde with a space on one side only ("Artist- Title", "Artist -Title", "Artist~ Title") separates, at 0.75. "Jay-Z" still doesn't split.
  - Figure dashes and other dash look-alikes count as dashes. Web entities are decoded ("can&#39t" → "can't"); a plain "S&M" is left alone.
  - Underscores standing in for an apostrophe ("Ain_t") or quotes ("(From _Film_)") in names that otherwise use spaces. Names joined only by hyphens ("john-newman-love-me-again") read as words.
  - Bracket groups made only of upload words are junk even in new combinations: "(Animated Video)", "[FULL-HD]", "[HD UPGRADE]", "(Video Oficial)", "(Lyrics Lyric Video)", "(Very High Audio Quality)". "[Supported by …]" is junk. A trailing unbracketed "lyric" is junk like "lyrics".
  - A stray bracket ("Here (Lucian Remix))" gave "Here )") is dropped.
  - **An artist tag with no title tag:** Apple Music shows such a file under its artist with the file name as its name. `best_parse` now takes the artist from the tag and the title from the file name (0.8), dropping the artist when the name repeats it ("Portugal The Man Do You" → "Do You"). Before, the tag artist was ignored: 8 of the 30 low-confidence files had one.
- **Known limit:** a scan doesn't re-read unchanged files, so parser changes reach the index only through `musicorg index rebuild` (38 s on this library with the files cached).
- Deviations and additions:
  - **Parsed** also carries `artists` (everyone credited), `credit` (the artist text as credited, before splitting: "Simon & Garfunkel", "Tyler, The Creator"), `artist_uncertain`, `source` (filename or tags) and `notes`. Splitting on `&` and `,` follows the prompt but breaks up duos and names with commas; `credit` keeps the whole name, so step 06 can match either.
  - **`compare_key`** also removes accents from Latin letters ("Beyoncé" = "Beyonce"), which rips often drop. Marks on other scripts are kept (Japanese が stays が).
  - **Artist splitting:** ` x ` splits only in lower case, so "Lil Nas X" stays whole. `vs.` also splits.
  - **More junk** than the prompt lists: producer and director credits ("Prod. by …"), a browser's duplicate counter "(1)" at the very end, download-site names ("y2mate.com - ", see above), genre tags in square brackets ("[Dubstep]") and junk tags at the start ("[MV] Artist - Title").
  - **Words that aren't junk or a known version** stay in the title: "(Part 2)", "(Bass Boosted)", "(8D Audio)", "(Piano Version)". The last three are different recordings, so they shouldn't match the original automatically; they'll go to review.
  - **Reversed order** is only detectable when the left side carries video junk or a version ("Blinding Lights (Official Video) - The Weeknd"). A plain "Title - Artist" can't be told apart and reads as "Artist - Title". A `|` separator gives low confidence either way.
  - **Channel names:** a VEVO or "- Topic" channel in the artist names the artist (mid confidence). "Nightcore - …" is a version, not an artist. Promo channels (Trap Nation and the like) and junk artists ("Unknown Artist", "YouTube") mean the title is a video title, parsed like a file name.
  - **`best_parse`:** when real tags and the file name agree on the title, version tokens from both are kept, so a remix named only in the file name is still a remix.
  - **Index columns:** `mtime_ns` (nanoseconds, as `fileops` plans use) instead of `mtime`; `parsed_json` (the whole parse); `scanned_at` on items and sources. `library_tracks` is keyed by `rel_path` (from the library root, "Music/…"), with `musicorg_id` indexed but not unique. `raw_tags_json` holds the managed tags (cover as its SHA-256), the encoder and comment (new `tags.read_extra`), and read warnings.
  - **The rebuild** empties and recreates the index's tables in place rather than deleting the file. `queue.sqlite` isn't created until step 09a and is never touched.
  - **The scan** also skips file links (not only folder links) and Windows hidden files, and never treats items as gone when their source isn't connected or a folder couldn't be read. It reads with 4 worker threads and writes to the index in batches of 200, so an interrupted scan keeps its progress.
  - **`suspect_upscale`** (a heuristic, as the report will say): an MP3 of 256 kbps or more with signs of a YouTube source — video junk in its name, an ffmpeg encoder tag (Lavf/Lavc, what online converters use) or a converter named in its name or comment.
  - **States from state.json and the library** outrank `new`: a superseded link → `superseded`; a library file with `MUSICORG_SOURCE` `rip_copy` whose `MUSICORG_ORIGIN_PATH` is the rip → `adopted`; the owner's decision → `matched_user`, `only_copy` or `skipped`. The state.json keys (`sources`, `decisions`, `superseded`) are documented in `state.py` for steps 07 and 09b.
  - **`sources add`** also refuses a folder overlapping another source. state.json is the record of sources; the index mirrors it.
  - New: `ENGINE_API.md` → Enums gained the item flags; `naming.is_within`; error `LibraryIndexError`.

### Step 06: YouTube Music matcher

- `youtube.py`, the one gate to YouTube, with search and metadata only:
  - `search_songs(query)`, the "songs" search as `Candidate`s; `get_track(video_id)` via `get_watch_playlist`; and `get_album(browse_id)` with `find_track`, which places a song by videoId, else by the one track with the same normalised title and a length within 2 s, else leaves the number empty and logs it.
  - **Rate limiter**, one per process: 1 request per 1.5 s ±0.5 s. It backs off 5 s then 10 s on HTTP 429, a 5xx or a connection failure, and after 3 in a row raises `YouTubePausedError` (exit code 4) and refuses every call for 30 minutes.
  - **Replay mode** (`MUSICORG_REPLAY_DIR`): answers only from recordings; a miss raises `ReplayMissError`. Every test runs in it (an autouse fixture), so no test can reach the network.
  - **Search cache:** raw responses in `search_cache`, keyed by the normalised query, for 30 days.
  - `scripts/record_ytm.py` records responses; `cases` records every search the harness cases need.
- `match.py`: `queries`, `score`, `assess`, `classify`, `match_item`, `run` and `write_sample`, with the prompt's weights (artist 0.35, title 0.35, version 0.20, duration 0.10), AUTO conditions, clean/explicit rule and thresholds.
  - Items are saved one at a time, so an interrupted run carries on where it stopped.
  - Candidates rejected in state.json are never proposed again.
- `musicorg match [--limit N] [--rescan]`, with progress and an estimate of the time left on stderr. It ends by writing `Reports/auto-sample.csv` through `fileops.write_export`.
- `normalize.parse_title` reads YouTube Music's own titles. Parts after " - " that are only versions come out as version tokens ("Yesterday - Remastered 2009" → `remaster:2009`); `parse_tags` now does the same for tag titles. Also new: `Parsed.from_dict`.
- **Evaluation harness** (`tests/data/match_cases.json`, `test_match_harness.py`): 47 hand-labelled cases, 42 of them rips from the owner's library and 5 made up. They cover remixes (including the owner's "R" mark), live versions, clean/explicit pairs, a cover, the tribute trap, wrong artists, video rips a few seconds long, title-only names and a non-song.
  - Result: **0 false AUTO matches, top-1 47/47 (100%)**; 19 AUTO, 23 review, 5 not found.
  - I labelled the cases from each candidate's artist, title, album, length and explicit flag. The matcher's own result was on screen while I did, so the labels aren't independent of it; the owner's listening check below is the independent test.
  - A third test pins each case's expected state, so a rule change that moves a case has to be deliberate.
- 61 searches, 6 track lookups (one of a video that doesn't exist) and 5 albums recorded in `tests/fixtures/ytm/` (1.3 MB). 951 tests pass (4 skipped: the three as before, and the live search).
- **What the recorded responses showed (ytmusicapi 1.12.3)**, and the code now relies on:
  - Search: every field the prompt names is there. Official audio is `MUSIC_VIDEO_TYPE_ATV`, music videos `…_OMV`. `limit` is a minimum: 20 results come back for `limit=10`, so the engine keeps the first 10.
  - `get_watch_playlist` tracks have `length` ("3:55") and `year`, but no `duration_seconds` and no `isExplicit`. A video that doesn't exist raises `YTMusicServerError` ("No content returned by the server"), and `get_track` returns None.
  - **Albums list other videoIds than search**, often the music video, for 3 of the 5 recorded albums. The title-and-length fallback placed every one.
  - **The album-level `isExplicit` is unreliable:** Eminem's *Recovery* says false while all 17 tracks say true. Only the per-track flag is used.
  - Artist names can carry odd spellings ("JAŸ-Z"); `compare_key`'s accent folding matches them.
  - ytmusicapi parses the body as JSON before checking the status code, so a 429 served as a web page arrives as a JSON error, which also counts as a slow-down.
- **Acceptance** (2026-09-29):
  1. Harness: 0 false AUTO matches, top-1 100% (47 cases).
  2. `musicorg match --limit 100` on the owner's library (the Apple Music folder scanned in step 05) ran without errors: 100 items in 2 min 37 s with 101 searches; 36 AUTO, 64 review, 0 not found.
     - Every AUTO match I checked by metadata is the same artist, the same title, an official album track and within 2 s.
     - Why items went to review: a length 3–60 s off (usually a YouTube rip of the right song), a different title ("Are You Mine?" vs "R U Mine?", typos), or a different artist ("Adventure Club - Crave You", which is their remix, tagged without the word "remix").
     - Nothing was `not_found`. An exact title with the same versions already scores 0.55, so almost any same-title song reaches review at 0.60. The report in step 07 should rank review items by score.
  3. `Reports/auto-sample.csv` written with 20 links. **Listening check: the owner listened to all 20; every one is the same recording as the rip.**
- **CI** (commit `deff4f8`): green on all three runners. macOS Apple Silicon and Intel: 951 passed, 4 skipped. Windows: 950 passed, 5 skipped (its platform-only skips, the real-Trash test and the live search).
- **The owner's rules** (asked after the first run):
  - **"R" means remix.** A final capital "R" or "(R)" in a rip's name or title ("Stressed Out R", "Done Wrong (R)", "Black Out Days R(slowed)"; 124 files in the library) becomes the version token `remix`. YouTube Music's own titles are left alone ("Vitamin R" stays). A bare `remix` never equals a named one (`remix:filous`), so these rips are AUTO only when YouTube Music's track is an unnamed "(Remix)" too, with the same length. Otherwise the right remix can rank first in review.
  - **Always the explicit version, unless the rip's name says clean.** "Edited", "censored" and "clean edit/mix/radio edit" now also mean clean ("uncensored" already meant explicit). A track whose own title says clean is never AUTO for a rip that doesn't say clean. That's on top of the pair rule, which already took the explicit one.
  - Re-checking the 36 AUTO matches from the real run with both rules changed none of them. The index keeps the old parses of the "R" files until `musicorg index rebuild` (the step 05 limit).
- Deviations and additions:
  - **Artist similarity** pairs everyone credited on both sides, including "feat." artists in a candidate's title, so a mislabelled "Eminem & Rihanna - Run This Town" finds JAY-Z's track (feat. Rihanna). AUTO still needs the rip's main artist or its whole credit to equal a candidate artist.
  - **Artist equality ignores spaces**, because tags say "Cold Play" and "Audio Slave". Titles must be exactly equal, as the prompt says.
  - **Queries** use the whole credited artist ("Michael Franti & Spearhead") where the prompt says main artist; YouTube Music finds both.
  - **Title-only rips** ("Mase-Feel So Good") match a candidate artist at the start or end of the title for scoring, but are never AUTO (no main artist).
  - **Clean/explicit:** a rip's own advisory tag counts as saying explicit. When the rip says neither and there's a pair, only the preferred version can be AUTO. If it isn't eligible (e.g. its length is 3 s out), the item goes to review; the other version is never taken instead. A disagreement is recorded as `version_mismatch`, since the enum has no separate code.
  - **Soundtrack notes** ("(From "Top Gun: Maverick")", "- From the Motion Picture …") are junk in brackets and after a title's " - ", but not elsewhere: "The Beatles - From Me To You" keeps its title.
  - **state.json** gains `rejected` ({item_id: [videoId, …]}), which step 07 writes. **config.json** gains `prefer_explicit` (default true).
  - **Stored candidates** carry the ENGINE_API Candidate shape plus `link`, `video_type`, `year` and `thumbnail`. Items store the review-reason codes; candidates store the human reasons followed by the codes.
  - **Recordings are trimmed:** opaque feedback tokens are removed everywhere, a watch playlist keeps only its first track, and an album drops its recommendations and description. A failed request is recorded as its error and replayed as the same error.
  - **The time-left estimate** uses the time items have actually taken so far (paced by the rate limiter), so cached searches and slow answers are counted too.
  - **auto-sample.csv** is UTF-8 with a byte-order mark (Excel and Numbers read the names correctly). It adds the album and the rip's full path, and draws its 20 from every `matched_auto` item in the index, not only this run's.

### Step 07: The decision report and the review spreadsheet

- `report.py`, for `musicorg report [--out <dir>]` (no lock). It writes `report-YYYY-MM-DD.md` and `.csv` through `fileops.write_export` (default `Reports/`) and prints the table, the recommendation and both paths. The markdown has:
  - the headline table: every item state, with counts and percentages;
  - the recommendation from the prompt's thresholds (not found ≥ 30%: adopt first; AUTO ≥ 60%: replace first), with a neutral paragraph when neither is met;
  - why review: each reason code, as a share of review items;
  - why not found: no results against a low score, and the 30 closest misses;
  - the version profile, library-wide and per state;
  - quality: the bitrate distribution, formats, `suspect_upscale` and the note on 128 kbps AAC;
  - the cost of replacing every `matched_auto` item at the step 09a pace.
- `review.py`, for `musicorg review export <csv> [--include-auto]` (no lock) and `musicorg review import <csv>` (lock), with the prompt's columns, checks and decisions. `accept`/`cand:n` → `matched_user`; `url` → fetched with `get_track` and scored; `only_copy` with its fixes; `skip`; `reject:n`.
- CLI: `report`, `review export`, `review import`. `config.THROTTLE_DEFAULTS` holds the step 09a pace (8–25 s pauses, 20–40 s for the first 20, 300 per 24 h); `Config.throttle()` merges the owner's changes. New helpers: `scan.source_folders`, `scan.item_path`, `index.all_candidates`, `match.candidate_id`.
- 979 tests pass (4 skipped). New: the report on a fixture index (exact counts, percentages, sections, files, never overwriting, the cost estimate, each recommendation) and the review round trip. The round-trip tests cover every decision; all bad rows reported with row numbers and nothing imported; commas, quotes, newlines and other scripts surviving; a cp1252 file refused; a moved row refused; a double import changing nothing; pasted links scored (high, low and unavailable); and a spreadsheet exported before `index rebuild` importing after it.
- **Acceptance** (2026-09-29, the v0.1 checkpoint):
  1. `index rebuild` (so the "R" files parse as remixes), then `musicorg match` over the whole library: 1,890 items in 52 minutes, 1,980 searches, no errors or slow-downs.
  2. `musicorg report`:

     | State | Items | Share |
     |---|---:|---:|
     | `matched_auto` | 704 | 37.2% |
     | `review` | 1,123 | 59.4% |
     | `not_found` | 62 | 3.3% |
     | `unsupported_format` | 1 | 0.1% |

     Recommendation: neither threshold is met (37% AUTO against 60%; 3% not found against 30%), so the review decides how much gets replaced.
     - Review reasons: length off 925 (82%), title 268, artist 262, version 126, a hard-to-read name 16.
     - **534 reviews differ only in length.** Same artist, title and version, official audio; off by up to 5 s: 178, 5–10 s: 91, 10–30 s: 131, over 30 s: 134. The report now shows this breakdown, which the prompt didn't ask for.
     - Replacing the 704 AUTO items: about 3 days at the queue's pace.
     - The 8 AUTO matches carrying a version (two 2003 edits, six remixes) all name the same remix or edit, at the same length.
  3. **Stop:** the table and recommendation go back to the owner's Claude chat before steps 08–11.
- **CI** (commit `0833b13`): green on all three runners. macOS Apple Silicon and Intel: 979 passed, 4 skipped. Windows: 978 passed, 5 skipped.
- Deviations and additions:
  - **Candidates are identified by the video id in the row's own `candN_url`**, never by their position in the index. That's what makes an export from before a rebuild import correctly (the rebuild drops candidates), and makes a second import of the same file change nothing, even after a `reject` has removed a candidate.
  - **`unsupported_format`** in the report: a WebM, raw AAC or WAV rip with no official match (`not_found` or `only_copy` with the `not_adoptable` flag), as contract section 3 says. It can't be adopted in v0.1, so it's counted apart. The index state is unchanged; step 09b sets the real state.
  - **The report's CSV** lists every item: its state, reasons, flags, format, bitrate, length, parse, and the chosen or best candidate with its link.
  - **The cost estimate** takes the rolling 300-a-day cap as the limit on days, and adds up the pauses plus an assumed 10 s per download for the hours.
  - **A pasted link below 0.6** (or one that isn't on YouTube Music: `video_unavailable`) is never recorded as a decision, because a rebuild would turn a recorded `url` into `matched_user`. The item stays `review` with the reason, and the link's track is shown as candidate 1 on the next export.
  - **`reject:n`** adds the video id to state.json's `rejected` list. An undecided item is then classified again from its remaining candidates, without searching (an AUTO match whose candidate is rejected goes back to review or not found). Rejecting the candidate the owner had chosen before also removes that choice.
  - **One decision per row**; a blank decision leaves the item alone. Decisions are case-insensitive and may have spaces (`Cand: 2`). Rows for items already `superseded` or `adopted` are refused.
  - **Accepted spreadsheets:** a file saved by Numbers ("CSV UTF-8", no BOM) or Excel (with a BOM) both import. The header must hold the columns; any extra ones are ignored.
  - **Order of the export:** review items with the most likely match first, then not found, then AUTO (with `--include-auto`).

### Step 07b: The local review page (the owner's request at the checkpoint)

At the step 07 checkpoint the owner chose a basic UI for settling the review items by hand, before step 08. `CLAUDE.md` now allows this one tool in v0.1 ("Not in v0.1" names it as the exception; the Mac app stays v0.2), and `ENGINE_API.md` lists the command.

- `musicorg review serve [--port N] [--no-open]` (lock) opens the review page in the browser. Ctrl-C closes it.
- **Five lists:**
  - Real doubts: review items with any reason besides length, most likely match first.
  - Length only: the smallest differences first.
  - Not found.
  - Automatic: for spot checks.
  - Decided.
- **For each rip:** your file, playable in the page (with seeking); the reasons in plain words; and up to 3 candidates. Each candidate shows its artist, album, length (and the difference from yours), score, version, and "Explicit" or "Clean" when both are listed.
- **Decisions:**
  - "Use this"; "✗" (not this one, stays on the song); "None of these"; "Keep my copy", with optional name fixes; "Skip"; or "Use link" for a pasted link, which is fetched and scored.
  - Keys: ← → 1–3 x k s p.
  - Each click goes through `review.decide_one`, the same checks and effects as a row of `review import`: state.json first, then the index.
  - A new choice replaces the old one.
- `review_web.py` (Python's own `http.server`, nothing added) and `review_page.html` (plain HTML and JavaScript, nothing loaded from elsewhere). `review.decide_one` is the new one-decision entry point.
- **Safety:**
  - It listens on 127.0.0.1 only.
  - Every request needs the session's random key (in the printed address) and a 127.0.0.1/localhost Host header. A POST needs JSON and a same-origin Origin. So other web pages in the browser can't drive it (cross-site requests, DNS rebinding).
  - A rip is played by item id only, opened read-only; no path comes from the browser.
  - A content security policy allows only the page's own scripts and YouTube thumbnails.
- **Listening to a candidate** opens it in YouTube Music, in one browser tab the page reuses. YouTube's embedded player showed "This video is unavailable" in testing, even for a video that normally allows embedding, so the page doesn't use it.
- Tried in the browser pane on a scratch copy of the library's records (the owner's decisions untouched). Worked: listing, playing a rip, use, ✗, skip, keep, a pasted link, and the Decided list. That run added three things: ✗ stays on the song, "Clean" is shown beside an explicit version, and dropped connections go to the log instead of the terminal.
- 990 tests pass (4 skipped). New: the page's safety checks (key, host, origin, content type, 127.0.0.1 only, no outside scripts), the lists and their order, every decision (repeating one changes nothing), a pasted link (replayed), bad decisions with their messages, playing with ranges (206, 416, missing file, unknown item, a path in the address), and the command holding the lock.
- **What the owner's first 51 decisions taught the matcher** (all but one took candidate 1, and none rejected anything):
  - **Spelling tolerance.** Rip and candidate names count as the same despite case, accents, punctuation, spaces, "&"/"and"/"n", a leading "The" ("XX" / "The xx", "Beatles" / "The Beatles"), and a typo: 1 letter in names of 5–10 letters, 2 in longer ones ("Huslin" / "Hustlin", "Snoop Dog" / "Snoop Dogg"). Shorter names must match exactly ("Air" isn't "Aer"), and numbers and roman numerals must agree ("Interlude" isn't "Interlude I"). This is a deviation from step 06, whose prompt says "title normalises exactly equal". The version, official audio and the 2 s length check stay exact.
  - **A featured artist credited as the main one** is the same artist ("Bruno Mars – Uptown Funk" is Mark Ronson's track feat. Bruno Mars). So is a title-only name that starts or ends with the candidate's artist ("Mase-Feel So Good").
  - **Names the rules call the same score as exact.** Otherwise a tribute act ("Re Beatles", 0.938) outranked the real track ("Beatles" / The Beatles, 0.922).
  - Checked on the owner's data before building. With these rules, 30 of the 51 decisions would have been made automatically, all 30 agreeing with the owner (0 disagreements). On a copy of the index, `match --recheck` then moved 22 review items to AUTO (the xx, the Beatles, typos) and 38 from "real doubts" to "length only". The harness still has 0 false AUTO; one case moved deliberately (Mase-Feel So Good → AUTO, the right track).
  - **Confirmed artist names.** When a choice on the review page picks a track by an artist the file doesn't name (even allowing for spelling, and not featured), the page asks: "Is 'Biggie Smalls' the same artist as 'Notorious B.I.G.'?" A single choice can't tell an alias from a mislabelled file ("Eminem & Rihanna – Run This Town" is JAY-Z's), so nothing is learnt without that yes.
    - A yes goes to state.json `aliases` and re-checks that artist's undecided items from the candidates already found. Tried on a copy: 20 Biggie Smalls songs, 3 became AUTO and 10 moved from not found to review.
    - Later searches use the confirmed name.
    - "No, just this song" isn't asked again that session.
  - `musicorg match --recheck` classifies review and not-found items again from the candidates already found, with the current rules, aliases and rejections, without searching (about 6 s for 1,135 items).
  - New: `match.same_name`, `match.alias_offer`, `match.recheck`, `match.candidate_rows`, `review.confirm_alias`, `state.aliases`, and `/api/alias` on the page. 1,015 tests pass (4 skipped).

### Step 08: The fingerprint gate

- `fingerprint.py`: `fingerprint(path, length_s=0, index=None) -> RawFP` runs `fpcalc -raw -json`, and `compare(a, b) -> FingerprintResult` gives `ber`, `offset_s`, `overlap_ratio` and a verdict: `match`, `uncertain` or `different`. The verdicts are now in `ENGINE_API.md` → Enums. Whole-file fingerprints are cached in the index's `fingerprints` table by (path, size, mtime); `Index.cached_fingerprint` / `put_fingerprint` are new.
- Thresholds live in `config.json` under `"fingerprint"` (`config.FINGERPRINT_DEFAULTS`). They stay at the prompt's conservative values (`match` BER ≤ 0.15 with overlap ≥ 0.6, `uncertain` ≤ 0.25) until step 09b calibrates them.
- `scripts/calibrate_fp.py <pairs.csv>` (`a_path,b_path,same`) prints the BER distribution of each group, the verdicts under the current thresholds, any different pair that passes as a match (marked DANGER), the same pairs that would go to review, and suggested thresholds with a quarter of the gap as a margin on each side. A path may also be a saved `fpcalc -raw -json` file, so step 09b can reuse fingerprints the research already made instead of downloading the audio again.
- The known limit is in the module docstring: the clean and explicit edits of a song fingerprint as a `match`, and step 06's clean/explicit rule decides between them.
- 1,037 tests pass (4 skipped). New: the prompt's four comparisons on the step 02 audio, fpcalc missing, the cache (reused, refreshed when the file changes, never written through a read-only index, never used for partial fingerprints), a file that isn't audio, thresholds from `config.json`, the calibration script on a small synthetic pairs file and its refusals, and the shapes below, built from generated audio.
- **Deviations, from the song-identification research (29–30 Sep, about 1,000 comparisons on the owner's rips):**
  - **Whole-file fingerprints**, not the first 120 s. A radio edit and the album version share their first two minutes, so only the whole file tells them apart. `length_s` still limits it when asked.
  - **Offset voting as well as the ±15 s slide.** Offsets where many identical fingerprint values agree are tried too, so a music video's 30 s or 69 s intro still lines up. Voting is Chromaprint's usual trick and is cheap in plain Python.
  - **Coverage in 2 s windows, and a shape check.** The prompt's single average BER lets an extended mix pass as a `match`: the whole of the shorter file lines up, and the extra minute at the end isn't in the average. Each file's 2 s windows are now checked against the other. A `match` also needs at most 15 s of unmatched audio at either end of either file, and no unmatched stretch of 10 s or more in the middle. Otherwise the result is `uncertain` and the file goes to review, never `match`. Research basis: a rip's extra audio was at most 7 s at the start and 11 s at the end in 90% of true matches, and no true match had a 10 s gap in the middle. Both limits are in `config.json`.
  - **Partly the same is `uncertain`, not `different`.** A high average BER with at least half of either file found (a cut, an inserted skit, a remix) goes to review with `fingerprint_uncertain`, which says more than `fingerprint_mismatch`.
  - `FingerprintResult` also carries `a_coverage`, `b_coverage`, the extra seconds at each end, `middle_gap_s` and a plain-English `why`, for the review page and the log.
- **Checked against the research data** (read-only, outside the repo): the research's 1,036 saved comparisons with both fingerprints, replayed through `compare_items` with the default thresholds.
  - All 692 pairs the research found different came out `different`. **No different recording passed as a match.**
  - Of the 301 same pairs, 290 were `match` and 11 `uncertain`: review, the safe side. Among these, the AUTO and owner-decided items gave 43 `match` and 2 `uncertain`.
  - Rips with a long music-video intro or outro (19), and partial or related pairs (19), were `uncertain` or `different`.
  - One rip that ends a few seconds before the official track was a `match`. That's inside the 15 s allowance.
  - About 75 ms per comparison of whole songs.

### Step 09a: The download queue and the downloader

- `queue.py` and `.musicorg/queue.sqlite`: the `jobs` table with the prompt's columns, and `queue_meta` holding the owner's pause flag, the YouTube pause (until when, and why), the downloads of the last 24 hours, and the count of network failures in a row. All of it survives a restart. Under rule 3 the SQL lives in `index.py` (`QueueStore`, `open_queue`); `queue.py` holds the logic.
  - One job at a time. The pace comes from `config.json` → `throttle`: 8–25 s between downloads, 20–40 s for the first 20 of a session, at most 300 in any 24 hours. Jobs that don't download (adopts) skip the pace.
  - **Retries** wait 1 min, 5 min, 30 min, then 2 h. A retry due within 30 minutes is waited for; a later one ends the run ("waiting", with the time to come back).
  - **YouTube refusing us** ("confirm you're not a bot", HTTP 429), or 3 network-level failures in a row (403, timeouts, dropped connections), pauses the whole queue for 6 hours (`youtube_pause_hours`). The job goes back to the queue with its attempt not counted, and `queue run` exits with code 4 and the time it can resume.
  - **One video's problem stays that job's.** Age-restricted, private, removed, region-blocked or members-only → `needs_review` with `video_unavailable`, and the queue carries on.
  - On start, jobs a crash left `running` go back to `queued`, and their staging folders are discarded. A job's batch is closed when its last job ends. A job whose batch has already ended (e.g. undone) is `cancelled`.
  - `queue pause` / `resume` flip the flag without the lock; `queue run` checks it between jobs, and during the waits between downloads. `resume` doesn't lift a YouTube pause.
  - Ctrl-C during `queue run` stops after the current job; a second Ctrl-C stops at once, and that job is queued again next time.
  - Handlers per job kind (`queue.register`) come in step 09b. A handler downloads only through `JobContext.download()`, which keeps to the pace and the cap.
- `youtube.download_audio(video_id, dest_dir)`: format `"140"` only, `paths` home and temp both set to the job's staging folder, `outtmpl` `%(id)s.%(ext)s`, the `tools` ffmpeg and deno (`js_runtimes`), `cachedir` in the app's cache folder, no postprocessors, a logging adapter and a progress hook. Every option name was checked against yt-dlp 2026.08.19. It goes through the shared rate limiter, returns yt-dlp's info (the caller reads `format_id`), and turns yt-dlp's error messages into `YouTubeRefusedError`, `VideoUnavailableError`, `FormatUnavailableError` or `DownloadError` (with `network`).
- `fileops.stage_dir(batch, name)`: an empty folder `_Staging/<batch_id>/<name>/` for one job, anything left there before discarded first. The prompt says "from `fileops.stage_path`", but that returns a file name, and yt-dlp needs a folder of its own. Tested for success, leftovers, a link in the way and the lock.
- `doctor --update-ytdlp` records the `yt-dlp` and `yt-dlp-ejs` versions in `config.json` (`ytdlp_previous`), then runs `pip install -U "yt-dlp[default]"` with the engine's own Python. `--rollback-ytdlp` reinstalls those versions. Both refuse while the library is locked, and then hold the lock themselves while pip runs, so a `queue run` can't start half way through (`ENGINE_API.md` updated).
- `status` shows the queue's state and counts, and `undo` now cancels the batch's queued jobs first, then says how many it cancelled.
- 1,078 tests pass (5 skipped). New tests:
  - the pace, with a fake clock
  - the daily cap across a restart
  - "not a bot" pausing everything, across a restart, and `resume` not lifting it
  - 3 network failures pausing the queue, and a success resetting the count
  - an age-restricted video going to review while the queue continues
  - format 140 missing twice going to review, and only format 140 ever requested
  - retries up to failed
  - `queue pause` from a second process while `queue run` holds the lock
  - a real crash: a child process exits in the middle of a job, leaving a `.part` file; the next run queues the job again and clears the file
  - undo cancelling queued jobs
  - the downloader writing only inside its folder, with the options checked
  - 12 real yt-dlp error messages and what each becomes
  - update and rollback with pip stubbed
- **Acceptance** (2026-09-30, with the owner's go-ahead): the live check `test_live_download` downloads one official track into a scratch library's staging.
  - The first run failed with a `DownloadError`. Its message was lost when the next run reused the temporary folder.
  - The next two runs passed: `format_id` 140, AAC, 128 kbps, 199.5 s, 3.2 MB, and nothing outside `_Staging/<batch>/job-live/`.
  - So a first attempt can fail and a retry succeed, as in the research. The queue's retries cover that.
  - The downloaded files went to the Trash afterwards.
- **Deviations, from the song-identification research:**
  - **Format 140 missing gets one retry** before `needs_review`. Research downloads saw "Requested format is not available" 28 times, and 9 of those videos worked on a later try. The retry asks for format 140 again; there's never a fallback format (rule 6).
  - **An empty download is retried** like any other failure, rather than ending the job (research: 5 empty files).
  - **"Sign in to confirm your age" is one video, not a block.** Research matched on "sign in to confirm" alone and wrongly paused everything for 6 hours. Only "not a bot", 429 and too many requests pause the queue, and a test holds the two apart.
  - **After a YouTube pause the pace starts gently again.** Each `queue run` is a new session, so the quiet start applies. Research found that slowing down after a refusal, not only pausing, is what keeps a home connection working.
- Other deviations:
  - **Four retries.** The prompt lists four waits (1 min, 5 min, 30 min, 2 h) and says "after 4 attempts". Read as four retries, so the 2 h wait is used: a job ends `failed` after its 5th try.
  - **Daily cap reached:** `queue run` stops and says when the next download can start, rather than holding the lock for hours.

### Step 09b: Replace and adopt

- `pipeline.py`: `plan replace`, `plan adopt`, `plan show`, `apply`, the replace and adopt jobs (registered with the queue), undo, and calibration.
  - **Plans** are saved in `.musicorg/plans/` with each rip's preconditions (size, time, first megabyte, item state). A replace plan groups rips by videoId: one download serves every rip that matched it, and a video already in the library (`MUSICORG_SOURCE_ID`) isn't downloaded again. `--only` defaults to `all-eligible`; `--limit` counts videos. The summary gives downloads, time (from the throttle settings), the days the daily cap spreads them over, and disk space (length × 16 KB/s).
  - **`apply`** re-checks every precondition, refuses a plan applied before or one whose items another batch has queued (`PlanOutOfDateError`, new, exit code 1), opens a batch and queues the jobs.
  - **A replace job:**
    1. re-checks the rips (`file_changed` → review)
    2. downloads format 140
    3. checks it: format 140, AAC, at least 100 kbps, length within 2 s
    4. runs the fingerprint gate against each rip
    5. reads the album from YouTube Music (the track number by the step 06 rule)
    6. calls step 10's hook (`pipeline.EXTRAS`, empty for now)
    7. makes one verified tag write in staging
    8. commits to `naming.library_path`, writes the sidecars under the committed name, and links each passing rip in `state.json`
  - **An adopt job** copies the rip into staging (SHA-256 verified), tags the copy with provenance (`rip_copy`, `ONLY_COPY=1`, `ORIGIN_PATH`, a new `MUSICORG_ID`), and commits it. The metadata rule is the prompt's: parsed names only with confidence ≥ 0.8 or the owner's fixes (`MATCH=manual` then), else the rip's own tags. WebM, raw AAC and WAV become `unsupported_format`. Two rips that would get the same name are planned as ` (2)`.
  - **`undo`** (`pipeline.undo`, used by `musicorg undo`): after `fileops.undo` puts the files back, the batch's `superseded` and `adopted` rips return to the state the plan found them in, their `state.json` links go, and their `library_tracks` rows go.
  - **`--stage-only`** keeps each download in `_Staging/calibration/<batch_id>/` with a `.json` note of every comparison, and changes no state. The new `musicorg plan calibration` writes `Reports/calibration-pairs.csv` (`a_path,b_path,same,verdict,ber,why`, `same` left empty) for `scripts/calibrate_fp.py`.
- `fileops`:
  - `stage_copy()` copies an outside file into `_Staging/`, SHA-256 verified. The source is opened read-only.
  - `set_aside()` keeps a staged file in another `_Staging/` folder, never overwriting.
  - Neither is journaled: staging is scratch space, and the commit that follows is journaled. Undo explains a tag write done in staging ("taking the file back covers it") instead of calling it missing.
- `queue run` now cleans `_Staging/` of files older than 24 hours when it starts. Nothing called `clean_staging` before.
- `youtube.download_options` sets `updatetime: False` (already yt-dlp's default from Python), so a kept download ages from when it was downloaded.
- `index.py`: `library_tracks_from(source, source_id)`, `remove_library_tracks()`, and `QueueStore.jobs(plan_id=…)`. No schema change, so no rebuild is needed.
- 1,097 tests pass (5 skipped). New tests, with a stand-in downloader that "downloads" the step 02 melodies:
  - a full replace: path, every provenance tag, the hook called, the sidecar named after a ` (2)` commit, the rip byte-identical
  - a mismatch: back to review, nothing committed, the download kept, never planned or proposed again
  - two rips of one video: one download and one `MUSICORG_ID`; then a third rip linked with no download
  - a download of the wrong length
  - a rip changed after apply
  - plans applied twice or out of date
  - undo of a replace and of an adopt
  - `--stage-only` and the pairs CSV
  - `--only` and `--limit`
  - adopts: trusted, low-confidence, the owner's fixes, WebM, two of one name
  - the matcher respecting an uncertain verdict, and the review CSV's `fingerprint` column
  - `stage_copy` and `set_aside`
  - the commands end to end: `plan adopt`, `plan show`, `apply`, `queue run`, `undo`
- **Deviations:**
  - **The gate's results are kept in `state.json`** (`gate`, added to the contract's section 5). Without that, `match --recheck` could make an `uncertain` rip AUTO again, and the next plan would download the same video again. Now a `different` video is turned down for that rip like an owner's rejection (`state.turned_down`), an `uncertain` one can't go AUTO, and plans leave both out ("fingerprint … before"). The review CSV's `fingerprint` column shows the result, e.g. `uncertain (BER 0.20)`.
  - **Adopt copies into staging, then commits** (`stage_copy` + `commit`) instead of `copy_in`, which commits straight into `Music/`. That keeps the prompt's order (copy, tag, verified write, commit): a crash can't leave an untagged file in the library to be copied again as ` (2)`.
  - **Download checks use existing review reasons:** a delivered format other than 140, a codec other than AAC, or under 100 kbps → `format_140_unavailable`; a length off by more than 2 s → `duration_mismatch`. No new enum values.
  - **An unreadable rip is `uncertain`**, not an error: it goes to review rather than being replaced unchecked, or retried forever.
  - **A matched item without an album id** (a review choice made from a spreadsheet row) gets its album from `get_track()` before `get_album()`.
- **Acceptance: not run yet.** It downloads from YouTube and needs the owner's listening. Steps 1–7 of the prompt are for the owner's next session:
  - Calibrate: `plan replace --only auto --limit 25 --stage-only`, then `apply`, `queue run` and `plan calibration`.
  - Add the different-version pairs, then run `calibrate_fp.py`.
  - Run the first real batch of 25.
  - Check 5 files in Apple Music. There's no cover art until step 10.
  - Undo, then a new plan.

### Ideas from the Photonizer project (2026-09-30)

The owner asked for ideas from the Photonizer project that would make the app run smoothly. One review agent checked about 20 candidates against the engine's code. Two small ones were real gaps and are fixed. The rest are in the new `docs/KNOWN-ISSUES.md`, or were already covered, or aren't worth it (listed there).
- **Crash-loop guard (`queue.py`):**
  - A job's try is counted before it starts, but a job left `running` by a crash was put back at the front of the queue however many times it had crashed the engine. One bad job could block the queue on every restart.
  - Now a job interrupted on each of its 5 tries ends `failed`, saying so in plain words. Making a new plan tries it again.
- **Keep the computer awake (`tools.keep_awake`, used by `queue run`):**
  - macOS: `/usr/bin/caffeinate -i -w <pid>`, which ends by itself if the engine crashes.
  - Windows: `SetThreadExecutionState`.
  - The screen can still turn off. If it can't be done, the queue runs anyway. There's no new dependency.
  - Tests never start either: `conftest.py` stubs it, and `test_tools.py` tests it with both stubbed.
- Already covered, so not added: tests kept away from the real config and Trash, fake clocks, the Time Machine and iCloud warnings, and the handling of disconnected drives.
- Not worth it here:
  - fingerprint caching by content (the gate's results are already remembered)
  - one read per file and per-drive read queues (scans are incremental, and the reads are small)
  - security-scoped bookmarks (yt-dlp's self-updates mean this is never a Mac App Store app)
- 1,103 tests pass.

### Step 09b: the calibration run (2026-09-30)

- The owner ran `plan replace --only auto --limit 25 --stage-only`, then `apply` and `queue run`:
  - All 25 downloads finished in about 15 minutes.
  - One HTTP 403 was retried a minute later and succeeded.
  - `plan calibration` wrote the pairs file.
- **Verdicts:**
  - 24 were `match`, with BER from 0.011 to 0.110. The limit for a match is 0.15.
  - 1 was `uncertain`: fpcalc couldn't read the rip.
- **Fixed, `fingerprint.py`:** that rip (an MP3) has a cut-off last frame.
  - fpcalc read it to the end but exited with "Invalid data found", and the engine threw the fingerprint away.
  - Now, when fpcalc fails but printed a fingerprint covering at least 90% of the file (less the ~2.7 s every whole-file fingerprint stops short of the end), that fingerprint is used and the damage is logged. The comparison is as strict as ever.
  - Damage earlier in a file still fails. fpcalc stops at the damage, and a test shows it.
  - With this fix, the rip compares with its download as `match` at BER 0.038: 25 of 25.
- **Different-version pairs, from the research's saved fingerprints** (read-only, outside the repo):
  - 32 of the owner's remix rips (an "R" at the end of the file name means remix, the owner's convention), each against the original of the same song. No audio was downloaded again.
  - `calibrate_fp.py` on the 57 pairs (25 same, 32 different):
    - Same: all 25 `match`. BER from 0.011 to 0.110 (median 0.046); at most 2 s of extra audio at the start and 3 s at the end.
    - Different: all 32 `different`. BER from 0.347 to 0.495.
    - **No different pair came near a match.**
  - The script suggests match ≤ 0.169 and uncertain ≤ 0.287. **Kept at 0.15 and 0.25** (config.json unchanged): every same pair already passes with room to spare, and the stricter values are the safer ones.
- **First real batch** (acceptance 4–6):
  - `plan replace --only auto --limit 25`, `apply`, `queue run`: 25 `done`; 2 network hiccups were retried and worked; 0 failed, 0 to review.
  - All 25 files have title, artist, album, year, track number and total, and full provenance.
  - Checked in Kid3, not Apple Music: the owner's rips live in Apple Music's own media folder, so adding the new files there would list every song twice. Cover art arrives with step 10.
  - `undo` put the library back exactly: `Music/` empty, the 25 files in `_Replaced/`, `matched_auto` back to 735 (710 + 25), and no superseded links left.
  - The undo printout no longer lists the tag writes done in staging (still in `--json`).
- A new plan after the undo (the end of acceptance 6): 25 `done`, 0 failed, 0 to review. Two HTTP 403s were retried and worked.
  - All 25 files are complete and linked (25 superseded), and they stay in the library.
  - Downloads in the last 24 hours: 80 of 300.
- **Step 09b's acceptance passes.** Bigger batches come next, with the daily cap left on (acceptance 7).

### Step 09c: Keep your own audio (the owner's decision, 2026-09-30)

- **Why:** the owner already has about 1,800 songs and doesn't want them downloaded again. Downloading stays for people moving off streaming services, and for a song the owner picks.
  - Step 09b's real batch showed the owner is right for many songs. Of the 25 rips, about 10 came from CDs, at 320 kbps MP3 (one made with Exact Audio Copy) or iTunes AAC, as good as or better than YouTube's 128 kbps AAC.
  - The YouTube-converter rips (ffmpeg's "Lavf" encoder) were worse than the download, since each is a re-encode of it.
  - That batch was undone; its files are in `_Replaced/`.
- `plan adopt --matched` copies in `matched_auto` and `matched_user` rips, keeping their audio, with their match's official details. Nothing is downloaded. See `prompts/09c-keep-your-own-audio.md`.
  - Tags: title, artist, album artist, album, year, track number and total, and explicit.
  - A track number, total or disc that YouTube Music doesn't give is removed, because the rip's may belong to another CD. Other tags, like genre, stay.
  - Provenance: `SOURCE=rip_copy`, `SOURCE_ID` = the match's videoId, and `MATCH=auto_details` or `user_details` (new values in `ENGINE_API.md` → Enums and `tags.MATCHES`). No `ONLY_COPY`.
  - Left out, and counted in the summary: matches the fingerprint gate turned down, and WebM, raw AAC or WAV rips, which only a download would fix.
  - The next plan picks up songs matched or confirmed later.
- `youtube.get_album(…, cache=index)`: album answers are kept in the index for 30 days, so songs from one album ask once. Only the fields the engine uses are kept, plus thumbnails for step 10. Replace jobs use the cache too.
- The contract's format table, `MUSICORG_SOURCE_ID` and `MUSICORG_ONLY_COPY` describe the new kind of file.
- New tests:
  - the official details applied and the audio untouched
  - the rip's disc and track numbers removed when they don't belong
  - its genre kept
  - one album lookup shared by two songs
  - no download ever
  - undo back to `matched_auto` and `matched_user`
  - turned-down and WebM matches left out
- **Acceptance run on the owner's library** (2026-09-30): `plan adopt --matched` → `apply` → `queue run`.
  - 817 of 817 done (735 `auto_details`, 82 `user_details`), 0 failed, no downloads. Faster than the 55 min estimate, because songs share album lookups.
  - 352 artist folders; about 4.2 GB copied; every rip untouched.
  - 6 files have no track number and 2 no album or year, because YouTube Music couldn't place them. They're left empty rather than guessed.
  - 4 songs are in the library twice (` (2)`). The owner's rips had them twice, from different YouTube-converter sites, so both copies were adopted. Noted in `KNOWN-ISSUES.md`.
  - Artist names are as YouTube Music spells them, e.g. "JAŸ-Z". A "preferred artist names" setting is noted in `KNOWN-ISSUES.md`.

### Step 10: Lyrics and covers

- `lyrics.py`: LRCLIB first, then YouTube Music. The owner asked for every source that allows it; Genius and Musixmatch's own API need paid keys or forbid automatic copying.
  - **LRCLIB:** `/api/get` (album left out when unknown or `Unsorted`), then `/api/search`. A search result counts only within 2 s and with the same artist and title. At most one request a second, with a User-Agent naming the project. Checked against the research's 1,357 saved answers, since LRCLIB's docs page renders only in a browser.
  - **YouTube Music:** `youtube.get_lyrics`, via `get_watch_playlist()["lyrics"]` then `get_lyrics(browseId, timestamps=True)`. Checked live with ytmusicapi 1.12.3: timed lines with `start_time` in ms, from Musixmatch or LyricFind.
  - **Guards:**
    - version: a remix or live track whose LRCLIB title lacks that word gets plain lyrics only (`version_uncertain`)
    - timing (new): synced lyrics only when the file is within 2 s of the length they were timed for. The owner's own audio (09c) can have a video intro.
    - every LRC text is checked
  - Answers, "not found" included, are cached in the index for 30 days.
- `artwork.py`: the album's cover at 1200 px, or the owner's `art_url`.
  - Checked live: an album listed at 544 px comes at 1200 px, and asking for more than the original returns the original (1425 px), never an enlarged copy.
  - Square within 2% and at least 500 px, else kept anyway and logged. JPEG quality 90 at most 1200 px, made in memory; a JPEG already 1200 px or less is never re-encoded.
  - An album's cover is fetched once per run. Songs with no official match get nothing automatic.
- `youtube.py`:
  - `get_lyrics`, `fetch_image` (https only; a 404 is an error, not a slow-down), `page_thumbnail` (yt-dlp, no download), `sized_thumbnail`
  - `Album.thumbnails`, which the album cache already keeps
  - all through the shared limiter
- `pipeline.py`:
  - **New replaces and adopts get lyrics and a cover in the same verified tag write** (`EXTRAS`: `lyrics_extras`, `cover_extras`).
  - `musicorg lyrics [--missing]` and `musicorg artwork [--missing]` plan the same for songs already in the library, one job per song, and `apply` queues them.
  - `artwork --missing` also takes songs whose cover isn't square: a converter's 16:9 video frame.
  - Undo takes both back: the tags return to how they were, and the `.lrc` and `cover.jpg` go to `_Replaced/`.
- `review`: `art_url` is read from the spreadsheet and the review page, for only-copy rows, https only.
- **Deviations:**
  - **Lyrics and cover jobs aren't "network jobs" in the queue's sense.** They download no audio, so they don't wait the 8–25 s download pace or count toward the 300-a-day cap. LRCLIB's one-a-second pace and YouTube's shared limiter set their speed. The brief called them network jobs; taken literally, 817 songs would take hours and use up the day's downloads.
  - **Lyrics and cover jobs check the file's identity, not its bytes.** It must still be there with the same MUSICORG_ID. These jobs only add their own fields in a verified write, so a lyrics batch before an artwork one doesn't make the artwork plan stale.
  - **No new index columns** for "instrumental" or "version uncertain" (a schema change would force a rebuild, losing the review candidates). The 30-day answer cache stops repeat lookups, and each job's result line says what was found.
- **Tests never contain real lyrics or covers:** lyrics are copyrighted and the repo is public. The recordings keep LRCLIB's and YouTube Music's real shapes, with made-up lines. Covers are made in memory.
- Also fixed: `queue.run(kinds={})` fell back to every registered kind.
- **Acceptance run on the owner's library** (2026-09-30): `lyrics --missing` (807 songs; 10 already had lyrics) and `artwork --missing` (787 songs; 28 had a square cover already, 2 have no official album). Both applied, then one `queue run`: 1,594 jobs done, 0 failed, 0 to review; 2 network hiccups were retried and worked.
  - Lyrics:
    - **708 synced** (88%): 634 from LRCLIB and 74 from YouTube Music (Musixmatch or LyricFind), so the second source added 74 songs
    - 55 plain only, 19 of them because of the timing guard (the file 2–3 s off the length the lyrics were timed for)
    - 12 instrumental
    - 32 not found
    - The version guard didn't trigger.
  - Covers: 787 embedded and 505 `cover.jpg` files; 0 failed. Most are 1200 × 1200. 15 albums only offer 512 px. 30 official covers aren't square within 2% (e.g. 1145 × 1200); they're kept and logged.
  - Checks: every "synced" song has its `.lrc` next to it. The lyrics' tag writes (plain lyrics, in 763 songs) and the covers' (787) all passed the verified-write check, where the decoded audio must be unchanged.
- **Step 10's acceptance:** the counts above are recorded. What's left is the owner's check of a few `.lrc` files against playback, in a player that reads them.

### Step 11: The JSON-RPC server (`musicorg serve`)

- `rpc.py` implements every method, notification and error in `ENGINE_API.md` section 2.
  - **Transport:**
    - Newline-delimited JSON on stdin and stdout, in binary mode.
    - `protect_stdout()` duplicates descriptor 1 for the protocol, then points 1 at stderr, so a child process (ffmpeg, fpcalc, deno) or a stray print can't corrupt the stream.
    - Deviation: the brief's `os.fdopen(os.dup(1), 'wb')` is written as `os.write` on the duplicated descriptor (`ProtocolOut`). The write-rules test rightly flags any `fdopen` for writing outside fileops, and the protocol stream isn't a file on disk. The effect is the same, and CLAUDE.md's rule 3 needed no new exception.
    - One writer lock for every message.
  - **Lifecycle:**
    - `engine.hello` comes first.
    - `library.open` holds the lock for the engine's lifetime.
    - On stdin EOF (and SIGTERM on macOS), no new work is taken, the queue's job gets 10 s, and the lock is released, exit 0. A job still running after 10 s is queued again at the next start; the crash-loop guard caps that.
  - **Workers:**
    - The queue worker runs `queue.run` from `library.open`, and again after `plan.apply` or `queue.resume`.
    - One long operation at a time (`sources.scan`, `match.run`, a non-dry-run `journal.undo`) returns `{job_id}`. Its progress goes out at most 4 times a second, the last always, and it ends with `job.finished`. A second one gets -32007.
  - **Errors:** every engine exception maps to a code, and an unexpected one to -32603 with the log's path.
- `scripts/rpc_client.py` plays the app's part:
  - makes a scratch library with five made-up rips named after recorded matcher cases
  - runs hello, open, status, `sources.add`, a scan and `match.run` (limit 5) in replay mode, printing every notification
  - closes stdin and checks for exit code 0
  - It runs in the test suite too, so the Windows runner exercises it.
- `state.edit()` holds a lock, so a queue job and an app request never interleave their state.json edits.
- 19 new tests:
  - the handshake
  - every protocol error, and the engine errors' codes
  - every method's shape, on a real scratch library
  - notifications during a job, and throttled progress
  - the busy error
  - 1,000 notifications from four threads, every line whole
  - `musicorg serve` as a real process: a second engine refused with -32001, a clean exit on EOF and on SIGTERM, the lock released
  - a child process writing to descriptor 1 without breaking the stream
  - the no-print rule: only `cli.py` calls `print`
  - the API doc naming every method
- **Doc changes** (`ENGINE_API.md`, to match the implementation):
  - -32000 for a request that can't be done, with a plain-English reason. The doc had no code for ordinary user errors.
  - The defaults: `review.list` limit 50, at most 500; `journal.undo` a dry run unless `dry_run` is false; `search.ytmusic` limit 10.
  - `plan.create`'s kinds and options, including 09c's `matched` and step 10's `missing`.
  - When `queue.state` and `library.changed` are sent.
- `musicorg serve` replaced the last "not implemented yet" stub, and `NotImplementedYetError` is gone.
