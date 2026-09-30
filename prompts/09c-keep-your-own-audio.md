# Step 09c: Keep your own audio (added by the owner, 2026-09-30)

## Context
After step 09b's first real batch, the owner decided against downloading songs they already have. They own about 1,800 rips, many ripped from CDs at 192–320 kbps, better than YouTube's 128 kbps. Of the 25 downloaded, about 10 were CD rips as good as or better than the download, so the batch was undone.

Downloading stays for people moving off Spotify or Apple Music (within the 300-a-day cap), and for a single song when the owner asks.

## Goal
Copy matched rips into the library with their own audio untouched, tagged with the official details of their YouTube Music match. Step 10 then adds covers and lyrics to these too.

## Build
- `musicorg plan adopt --matched`: `matched_auto` and `matched_user` rips, as well as `only_copy` ones.
  - Songs matched or confirmed later are picked up by the next plan.
  - Left out: a rip whose match the fingerprint gate turned down, and a WebM, raw AAC or WAV rip (only a download would fix it).
- The job:
  1. copy the rip into staging, SHA-256 verified
  2. look up the album (once per album, cached in the index)
  3. make one verified tag write
  4. commit to `naming.library_path`, and mark the item `adopted`
- Tags:
  - the match's title, artist, album artist, album, year, track number and total, and explicit
  - a track number, total or disc that YouTube Music doesn't give is removed, not kept from the rip
  - everything else the rip has, e.g. genre, is kept
  - provenance: `SOURCE=rip_copy`, `SOURCE_ID` = the match's videoId, `MATCH=auto_details` or `user_details`, `MATCH_SCORE`, `ORIGIN_PATH`, and no `ONLY_COPY`
- No fingerprint check is possible without a download. So there are no guesses: never a song still in review.
- Undo returns the items to `matched_auto` or `matched_user`.

## Acceptance
- `plan adopt --matched`, then `plan show`, `apply` and `queue run` on the owner's library, with no downloads.
- Spot-check a few files, including a CD rip and a YouTube-converter rip.
