# Known issues

Open gaps found along the way, not fixed yet. Each has a "decide" line when it needs the owner. Delete an entry when it's fixed, and say so in `CHANGELOG.md`.

Most of these came from comparing the engine with the Photonizer project's lessons (2026-09-30).

## Needs the owner's decision

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

- **Duplicate songs in the rips come in twice.** Two rips of one song (e.g. from two converter sites) both match the same official track, so both are copied in, the second as ` (2)`. Four such pairs after step 09c.
  - Proposed: `plan adopt --matched` keeps one copy per matched video, the higher-quality rip; the rest wait as duplicates.
  - The 4 existing pairs: a small plan that moves the extra copy out of the library.
  - Decide: which copy to keep. Bitrate doesn't help much when both came from YouTube, since 320 kbps is only a re-encode of 128 kbps.
- **Artist names exactly as YouTube Music spells them**, e.g. "JAŸ-Z" instead of "JAY-Z".
  - Proposed: a "preferred artist names" setting in state.json, applied to tags and folder names across the library.
  - Decide: which names.

## For later steps

- **Step 11 (RPC):** treat the app stopping the engine (SIGTERM) like Ctrl-C: finish or requeue the current job cleanly. Today only Ctrl-C is handled, so a stop counts as a crash. The crash-loop guard sets a job aside after 5 of those.
- **v0.2 Mac app:** carry over Photonizer's responsiveness rules that fit an app talking JSON-RPC:
  - stable row identity in lists
  - sorting and grouping off the main thread
  - a memory cache for album-art thumbnails
  - "a stall is the main thread blocked 100 ms or more"
  - "done means launched and responsive for 60 s"
- **After v0.1.0:** a static type checker (mypy or pyright). It's a new dev tool, so it needs the owner's OK.
