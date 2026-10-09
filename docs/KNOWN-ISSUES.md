# Known issues

Open gaps found along the way, not fixed yet. Each has a "decide" line when it needs the owner. Delete an entry when it's fixed, and say so in `CHANGELOG.md`.

Most of these came from comparing the engine with the Photonizer project's lessons (2026-09-30).

## Needs the owner's decision

- **Downloads by genre: a page, or folders too?** (2026-10-02) Discover → Downloads now groups downloads by genre as the owner drew it. The files themselves are still in `Music/<Artist>/<Album>/`. The plan's `Discovered/<Genre>/` folder would be a change to the library contract. Decide whether it's still wanted now that the page does the grouping.
- **A download's genre is partly a guess** (2026-10-02): the genre it was found under in Discover, or the genre of the owner's other songs by that artist. It's written into the file's genre tag, where Edit Details… can change it. Decide whether guessed genres should be marked as such.
- **Reading a video's sound to time its lyrics (rule 8)** (built 2026-10-02). To line a song's lyrics up with its video, the engine fetches the video's audio once (about 4 MB, kept only as a fingerprint in the index's cache), through `musicorg.youtube` and its rate limiter. Rule 8 says downloads happen only through the throttled queue. This was built on the reading that a download is a file kept in the library, and that this is what playing the video already fetches. Decide: is that reading right? If not, the sound line-up is taken out and only the captions time the lyrics (about half to 85 % of official videos). *The owner put this decision off on 2026-10-02. Since then the fetch happens only when the Karaoke button is clicked, never by itself.*
- **Delete is offered only for downloads** (built 2026-10-02): a song that came from the owner's own rips can't be deleted from the app. Decide whether that should ever be possible, and with what safeguard.
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

## Built on 2026-10-08 and not yet seen working in the app

Each of these is covered by tests and builds, but nobody has watched it do its job in the running app (the owner was using the app, or it needs a right-click or a real film). Take an entry off when it has been seen, or mend it and say so in `CHANGELOG.md`.

- **The Finder for adults-only add-ons**: the page itself, with a real add-on in it. Its row is switched off in the owner's sidebar. No such add-on is built in or tested against.
- **Movies being kept, listed on Downloads**: the rows, their bars, ✕ to stop one, and a keep carrying on after the app is reopened. (The engine's side of stopping and carrying on was run against a real public-domain torrent.)
- **Convert for Phones and Tablets**, in a video file's right-click menu, and the progress it shows.
- **A found playlist opened in place** (Music Finder → Playlists): its songs, Play All, Queue and Download on a song.
- **The question before a song is put in a playlist twice**, and **Remove Duplicates** in a playlist's menu.
- **Copy to Profile using the songs the other profile already has**, and the line that says how many.
- **Playlists moving on as the owner listens**: seen bringing a different page after a restart, not over a listening session.
- **The torrent upload limit**: libtorrent takes each setting; the speed itself hasn't been measured on a film others are asking for.
- **No gap under the search bar** when a search finds nothing (Downloads, the song lists, Albums).
- **The pause rule counting different songs**: tested with the owner's own evening replayed; it takes effect when the engine next starts.

Also open from that day, waiting on the owner (see `docs/ROADMAP.md`): megabytes or megabits for the upload limit; listeners' uploads in Covers & Remixes; whether Copy to Profile should ask before leaving out songs already there; and whether Music Finder's Playlists and Covers & Remixes should wait for a button instead of looking things up when opened (the owner: "come back to it later"; the service challenged this computer that evening, cause unknown).

## The speed audit (2026-10-08): what's still slow

The app stopped getting slower with use that day (each page is a view of its own, parked out of the window while it isn't showing: `PageHost`; the numbers are in `CHANGELOG.md`). What's left was measured the same evening on the owner's library (1,888 songs), and none of it is in the app's own code:

- **The song table was the slowest thing in the app, and has been rebuilt** (2026-10-09, `SongTable`: AppKit's own table, which makes only the rows on screen; SwiftUI's is taken out). On the owner's library in the window in front, old then new: list to list 0.40 s then 0.16 s, a fresh screenful 0.22 to 0.33 s then 0.07 to 0.15 s, a letter typed into the search 0.69 s then 0.14 to 0.31 s (`CHANGELOG.md` has the rest). What's left of it:
  - **Not seen in the app itself:** a download dragged from a list onto the sidebar, and the right-click menu opened by hand. Both need a hand, or the whole screen taken over: the screen-control tools working behind the owner's other windows can't carry a drag from one part of the window to another (tried on 9 October, from a song list and from the Downloads page's own list: nothing moves) and aren't let open a right-click menu. (Seen: the list, selecting, a double click playing, the playing row; and on 9 October a heart switched on and off without its row being selected, and a column dragged to a new place and back, remembered each time.)
  - **The first opening of a long list is 0.4 to 0.6 s** (it's 0.15 s after that), now that nothing is built ahead at launch. A profile of two such openings (9 October, the unseen copy): four tenths is macOS laying the page and its new rows out, a quarter is drawing them (the words of the cells are nearly half of that), a seventh is SwiftUI's own work for the page around the table, and making the cells is under a tenth.
    - **The heart beside each song was a tenth by itself**, as one of macOS's buttons, and is a plain picture now (`HeartMark`, 9 October): a list opens a tenth to a seventh quicker, the first time or not (`CHANGELOG.md`). Timed on the made-up library in the unseen copy, not again in the owner's own window: the Mac was busy with other work that afternoon, and two runs there came out twice as slow as the two before them for every page alike.
    - **Making a row's badges only when a song has one was tried and put back** (it was this list's idea). With that, and the cover's placeholder note drawn without a view of its own, a row has six views fewer of twenty-eight, and nothing changed that could be told from the noise. It's what's done with a row once it's made that costs, not making it.
    - What's left of it is macOS's own: a label for each cell's words, laid out and drawn. Drawing the words without the labels would be the next tenth or two, and would give up macOS's own drawing of them (and what reads the screen out). Not done.
- **A page of cards** (What's New's fifty, a Finder's posters) takes 0.4 to 1 s to build the first time: each card's buttons are AppKit controls. Once built it comes back in 0.13 to 0.3 s. No one part of a card is the cost: without its pop-up menu the page builds a tenth faster, without its tooltips a twentieth (tried and put back).
- **The first click on a button, when the app isn't the one in front, only brings the app forward** (found 9 October; it may be the owner's "sometimes it takes several clicks"). It's macOS's rule for everything but a few controls, asked of each view (`acceptsFirstMouse`): a song list and the sidebar answer the first click, and so does a row's heart, but a SwiftUI page doesn't, so a card, a tab, Play, a Finder's buttons all need a second click after the one that brought the window forward. Not changed: it's the owner's choice, since a click meant only to bring the window forward would then also press whatever it lands on (a queued download's ✕, a Remove). macOS 15 has a way to let chosen views take that click (`allowsWindowActivationEvents`), so it could be given to the harmless ones only. Whichever is built has to be tried by hand: a real click on a window that isn't in front is one thing the checks can't make.
- **Opening the app takes about 4 to 5 s on the owner's library when the Mac is calm** (9 October, from the engine's log and the unseen copy): roughly 1.5 s until the engine is up (the app's own start, then Python's), about 1 s of the engine answering (the list of every song is two thirds of it), and the rest the app sorting the songs and building its first page (0.8 s of work). No one part stands out. **With the Mac busy it's far longer:** 6 to 7 s during this session's own timing runs, and over 20 s at 1:48pm with another project's build running. That afternoon every timing in the owner's own window came out at twice its usual figure for the same reason; it isn't the app.
- **A click by hand wasn't timed.** The timings are the app choosing its pages itself (`MUSICORG_BENCH`). Clicks made through the screen-control tools read 0.1 to 0.25 s higher, but that's the tools asking the app about its window before each click; whether a click by hand costs more than the app's own choice isn't known.
- **Not measured:** Home with every row filled over a long session, the Artists page with an artist open, dragging a playlist in the sidebar, and resizing the window.
- **Not seen working after the change** (each needs a right-click or a drag the checks couldn't make): a song's right-click menu, dragging a download onto the sidebar, and sheets opened from a page (Edit Details, the guide). The pages, the album page and back, the library search, Settings' sections and the Downloads page were looked at.
- **A video's start.** One look-up instead of two is built but wasn't timed against YouTube (it had this Mac paused). Still to try when it answers again: asking yt-dlp for fewer of YouTube's "clients" (each is a request), and letting a click to play go ahead of a batch of Discover look-ups in the rate limiter's queue. Looking a video up before it's clicked would be faster still, but it's more requests to a service that had just bot-checked this Mac: not without the owner's word.

## Versions in a copy's name (2026-10-04): what's left

On 1 Oct, `plan adopt --unconfirmed` copied 1,062 rips in under their parsed titles, which leave the version out. 139 copies lost theirs: the rip "Come As You Are R" became "Come As You Are", and the real original became "Come As You Are (2)". The rule and the repair are in `CHANGELOG.md` (2026-10-04). These are still open.

**To do first, by the owner:** the repair has not been run on the real library. With the app closed, in this order, running the queue after each `apply`:

1. `musicorg plan adopt --matched`, `plan show`, `apply`, **`queue run`**. The waiting decisions go straight to their official titles.
2. `musicorg plan tidy`, `plan show`, `apply`, `queue run`. The remixes get their names back.
3. `musicorg plan tidy`, `apply`, `queue run` once more. The " (2)" originals get their plain names back.

How many songs step 2 lists depends on how many have been decided by then. Run alone, the first `plan tidy` would retitle 140 copies, tag 1 more and leave 2 alone, and the second would give 18 originals their plain names back. After step 1 with the 183 decisions waiting on 4 Oct, it is about 111 retitles and 1 retag, with the plain names split between the two tidy runs; with more songs decided first, fewer still, since a decided song takes its found name in step 1.

Read each dry run before applying it: it is the first real check. If step 2 is planned while step 1's jobs are still waiting, `plan tidy` leaves those copies out and says so ("a job for their rip is waiting in the queue"); run the queue and plan again.

Needs the owner's decision:

- **Lyrics on about 42 of these copies were looked up under the original's name.** The 1 Oct lyrics batch saw "Come As You Are", not the remix. About 42 have a timed `.lrc` and about 49 have words in their tags. Nobody has checked whether they are wrong: each passed the 2-second length check when it was written, so many may be right. The repair moves each `.lrc` along with its song and changes no lyrics. Decide: leave them, set the timed files aside in `_Replaced/` (undoable; karaoke and the lyrics pane then show plain words for those songs), or remove both. Either way a later `lyrics --missing` now asks as the remix, and will mostly find nothing for a bare "R".
  - **The same goes for a copy that is found** (`plan adopt --matched`): its `.lrc` now moves with it to the found song's name. Before, it was left behind under the old name. For the copies among the waiting decisions that have such a `.lrc`, the original's timed lyrics become the found remix's, unless the lookup for the found song finds timed lyrics of its own, which then take their place. Say so with this decision if they should be set aside instead.
  - Related: the changelog for 2026-10-01 says no lyrics are looked up for unconfirmed copies, but `plan lyrics` doesn't leave them out. That is how these copies got lyrics. Decide which is meant.
- **Shortened artist names and dropped featured artists, from the same batch.** The same code path cut about 18 artists short ("Ashford & Simpson" became "Ashford", "Down With Webster" became "Down") and dropped about 12 "(Featuring …)" credits. Not fixed here. Decide how a featured artist is written (in the artist tag as "A, B", or in the title as "(feat. B)") before it is built. Fixing it later renames some of the same files a second time.
- **The R isn't always a remix.** Of the 88 bare-R rips the research confirmed by listening, 70 are remixes; the others are covers (both "Crazy R"), a live session, mashups, edits, and six whose real title names no version (Tuesday, 1998, Memories, Who Gon Stop Me, Cooler Than Me, Road To Zion). The copy keeps the owner's "R" either way, and its version tag says `remix`. It's put right when the song is decided (a match, or only-copy with a title), or in Edit Details: a new title takes the version tag with it, and a title typed there stays (see the limits below for the one case where it doesn't).
- **The research's exact names** come in through the owner's review decisions, not through this repair: official ones as accepted matches, and the ones found only on SoundCloud, YouTube or Bandcamp as only-copy with a title and artist fix. A song decided that way takes its found name in step 1 and is never touched by `plan tidy`.
- **"(Explicit)" in a title.** A rip named "Run This Town (Explicit)" keeps "(Explicit)" in its title, like any version the rip names. Decide whether that one, "(Clean)" and remasters belong in a title.

Limits that stay:

- **`plan tidy` needs two runs.** The first gives the remix its name; the " (2)" original gets its plain name from the next one, once the name is free.
- **Undo goes newest first, and is strict about it.** An undo is refused, with nothing changed, when a later batch moved or renamed one of the batch's files, or when a file it would move back (a song, its `.lrc`, an album's cover) finds its old name taken. The message names the batch to undo first.
  - After the repair, the two tidy batches must be undone (the second, then the first) before any older batch that brought those songs in or wrote their lyrics. Without the refusal, undoing the 1 Oct batch would have set 18 originals aside in their remixes' places and left the 140 renamed copies in the library.
  - This is new for every kind of batch: a title changed in Edit Details also has to be undone before the batch that brought the song in. Before, such a song was skipped ("no longer there") and stayed in the library while its rip went back to waiting.
  - A song deleted to the Trash and then downloaded again under the same name: the first download's batch can no longer be undone as a whole, and the message says so.
- **A title typed in Edit Details is known from the journal.** That is how the engine tells "Cooler Than Me", typed by the owner over "Cooler Than Me R", from the same words written by the old rule: `plan tidy` leaves the first alone, and nothing reads the rip's name past it. If the journal's files were ever lost, the two would look the same again and the next `plan tidy` would put the R back. An edit that was undone doesn't count.
- **An only-copy decision with an artist or album fix but no title fix** still writes the title from the rip's names, and one with no artist fix writes the artist from them, so a hand edit to that field is lost (seen: "Come As You Are (Cover)" typed by hand went back to "Come As You Are R"). A decision with no fixes at all leaves the copy's names alone. To keep a typed title through such a decision, give it as the title fix.
- **A rip whose names weren't trusted (confidence under 0.8) gets no version tag**, now or from the repair: a guess isn't written into a file. `plan tidy` counts them and `plan show` lists them (2 on this library). The engine reads such a copy's version from its title and, failing that, its rip's name.
- **Parser limits.** Version words the parser has no token for stay in the title but give no version tag: an unbracketed "Old School Remix", "[G-Mix]", "Mashup", "Dub", "(Long Version)". A file named "Solo Dolo R 1" with no title tag isn't read as the owner's mark. The version always goes after everything else the parser keeps as the title, so two of the 140 come out with their words in another order than the rip has them: "E.T. (AIZZO REMIX) CAR VIDEO LIMMA" becomes "E.T. CAR VIDEO LIMMA (AIZZO REMIX)", and "Polozhenie [Extended] (Night Drive)" becomes "Polozhenie (Night Drive) (Extended)". Both still read back as the same song and versions.
- **A long title loses its version from the file name first**, since names are cut from the end. The title tag and the version tag still carry it. Two versions of a long-titled song can then meet again as " (2)".
- **An Edit Details rename that is killed after its move** ends `needs_review` when run again, and the index points at the old path until `musicorg index rebuild` (which also throws away the matcher's candidates, so it is a last resort). Nothing is lost or copied twice. An upgrade of an unconfirmed copy and a `plan tidy` rename are both carried on from wherever they stopped.
- **An upgrade given up for good after it moved the copy** (five tries, each stopped after the move) leaves the copy's `.lrc` under the old name. The copy itself is in the index at its new place, so no later plan copies the rip in again. Undoing that batch puts everything back.
- **`plan replace` doesn't look for an unconfirmed copy** of the rip it replaces, so the download would land beside the copy instead of taking its place. Read from the code, not tried.
- **Two adopts in one plan that want one name, with a file outside the plan already there:** the first lands on " (2)" and the second job ends `needs_review`. Nothing is overwritten.
- **New for Discover and imports:** owning a remix no longer counts as owning the original, so the original of a song the owner has only as a remix can now be suggested.
- **A copy still titled with the owner's R doesn't start a Discover radio.** "Come As You Are R" is a remix by nobody in particular, so it isn't the same song as the official "Come As You Are" and no radio is seeded from it (a named remix, "Here (Lucian Remix)", still finds its own upload). The roughly 120 unfound R copies stop seeding radios until they are decided. Say so if they should seed from the original instead.

## A pasted link in review (2026-10-04): what's left

- **`index rebuild` forgets a pasted link that scored low.** The link is kept with the candidate in the index (a cache), not in `state.json`: it isn't a decision. After a rebuild and the next `match`, the item's candidates are the best three by score, and the link has to be pasted again unless its track is one of them. Keeping it through a rebuild would mean recording it in `state.json`, a change to what that file holds (contract section 5). Decide whether it's worth that; accepting the link soon after pasting it avoids the question. (`match --rescan`, `match --recheck` and confirming an artist's other name no longer forget it: they leave the item alone while the link waits.)
- **An item with a pasted link waiting isn't matched again**, not even by `match --rescan`, until the link is accepted or rejected. The command says how many it left.
- **A low link pasted on a song that is already decided another way** puts the song in `review` in the index, with the link first, while `state.json` keeps the earlier decision. Until the owner decides the song again (accept, a candidate, skip…), it is out of `plan adopt --matched` and `plan replace`. Rejecting the link takes the link out but leaves the song in `review`; an `index rebuild` brings the earlier decision back and forgets the link. The engine did this before today too. Pasting the link of the track that is already the decision is not this case: that changes nothing. Say whether a low link on a decided song should instead leave the song as decided.
- **After a rejection, the item is classified again without the artist names the owner confirmed** (`review._update_index`), unlike the matcher and, since today, a pasted link. A `match --recheck` puts it right. Left alone here because it touches items with no pasted link.
- **`&amp;` in two names** (`Pharrell &amp; Rosco P. Coldchain`, `Macklemore &amp; Ryan Lewis`) was reported in the low-score messages and couldn't be traced to the engine: every recorded YouTube Music answer carries a plain "&". Not checked against those two songs themselves, which needs the network. If a recording of one of them shows the entity, decode it at the gate (`youtube._from_track` and `_names`) before the name can reach a song's tags; a test fails the moment a recording carries one.
- **A row with a pasted link moves to the end of the review rows** in the export (and of "Real doubts" on the page), because rows are sorted by their candidate 1's score.
- **Not run on the owner's library.** The twelve songs need their links pasted once more (the same sheet can be imported again, before or after the accept), then `accept` on the next export.

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
- **macOS asks to allow folders at every start, which stalls the start-up** (owner, 2026-10-01; this was also the unexplained slow first launch, when the engine sat for 53 seconds inside a plain "open this file" call). macOS remembers the answer per app and tells apps apart by signature; an ad hoc build is a new app every time it changes. Fix: a self-signed certificate the owner makes once (`docs/SIGNING.md`); the build script uses it when it exists. Until then, start the app without rebuilding and macOS doesn't ask. Still worth doing: have the engine touch fewer protected folders at start-up (it asks about Music and Downloads as well as the library's own folder).
- **"Reading your library…" takes a minute or more after a big import.** The first `library.tracks` after new songs arrive reads each new file's tags from disk, and on the iMac's disk that is slow for files not read recently (about 90 seconds for 1,062 new songs on 2026-10-01; this was also the unexplained slow first launch). After that it's instant. Fix: the adopt and replace jobs already hold each song's tags, so they should store the app's details in the index as they go; and the app should show progress instead of a bare spinner.
- **Unconfirmed copies keep junk from the rip's own tags**, such as an album called after a download site. They're fixed when the song is identified; a clean-up of obvious junk could come sooner.

### The video player (built 2026-10-01): what's still rough

- **Lyrics timed to the video (2026-10-02): what it can't do.** A video with its own mix or a live take of the song, and no captions, stays untimed. So does one that plays the song 2 % or more fast or slow (a slower search that finds those was tested and left out: no real video needed it). A cut is found to about ±0.3 s, so a line that starts within a second of one can land on the wrong side. *Breezeblocks*' video is the album track to within 0.14 s: if its lyrics still look out of step, the cause is somewhere else (the lyrics themselves, or the player's clock) and hasn't been looked for.
- **A song's lyrics can be badly timed in themselves.** 20 of LRCLIB's 21 records for *21 Questions* are one file whose last forty lines are crammed into fourteen seconds. The engine's check (`lyrics.check_lrc`) only refuses times that go backwards. On a video the label's captions now correct it; on the song alone nothing does.

- **A video's picture can stop on one frame while the song goes on** (owner, 2026-10-01). Not explained: see the changelog for what was ruled out. The player now notices (no new frames for 3 s), nudges the picture, and notes it in `~/Library/Caches/org.musicorganizer.app/player.log`. Next time it happens: read that file. No line there means frames were arriving and the layer wasn't drawing them (then: re-attach the layer when the window comes back into view). A line there means the stream stopped (then: fetch the picture in 10 MB pieces, which YouTube serves 50 times faster than one long request).

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
