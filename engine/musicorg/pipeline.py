"""Replace and adopt (step 09b): how files actually land in the library.

The owner's rips are never touched. Replacing a rip means adding a clean official copy to
the library and linking the rip to it; adopting one means copying it in. Every file goes
staging → check → tag → commit, through `fileops`, journaled and undoable.

- **Plans first** (`plan_replace`, `plan_adopt`): a dry run, saved in `.musicorg/plans/`.
  Each operation records its preconditions: the rip's size, time and first megabyte, and
  the item's state.
- **`apply`** checks the plan still holds, opens a batch and queues the jobs. The throttled
  queue (step 09a) runs them; the handlers are registered at the bottom of this module.
- **A replace job** is one video, serving every rip that matched it:
  1. each rip's preconditions again (`file_changed` → review)
  2. the video already in the library (MUSICORG_SOURCE_ID): no download, only steps 4 and 8
  3. download format 140, then check it: AAC, at least 100 kbps, the length within 2 s
  4. **the fingerprint gate** against each rip (contract rule 7). `different` →
     `fingerprint_mismatch`, `uncertain` → `fingerprint_uncertain`: that rip goes back to
     review. The result is kept in state.json ("gate"), so the same download is never
     tried again. If no rip passes, the download is kept in `_Staging/` for 24 hours.
  5. the album from YouTube Music (the track number by the step 06 rule, never a guess)
  6. lyrics and the album cover (step 10, through `EXTRAS`)
  7. **one verified tag write** on the staged file, then commit to `naming.library_path`
  8. sidecars under the committed name; the rips marked `superseded`, and linked to the
     new MUSICORG_ID in state.json
- **`--stage-only`** (calibration) stops after the gate: the download goes to
  `_Staging/calibration/<batch_id>/` with a note of what it was compared with, and no
  state changes. `calibration_pairs` turns the notes into a pairs.csv to fill in.
- **An adopt job** copies one rip into `_Staging/`, tags the copy, and commits it. Only
  MP3, M4A, FLAC, Ogg and Opus are adopted; any other format → `unsupported_format`.
- **Keep your own audio** (step 09c, `plan adopt --matched`): a matched rip is copied in
  the same way, with its match's official details (title, artist, album, year, track
  number) and no download. MUSICORG_MATCH says so: `auto_details` or `user_details`.
  There's no fingerprint check without a download, so only AUTO matches and the owner's
  own choices qualify, never a guess still in review. When several rips match the same
  track, only the best copy is copied in (lossless first, then a CD or iTunes rip over a
  YouTube conversion, then the higher bitrate, then the bigger file); the others are
  linked to it.
- **Tidy** (step 09d, `plan tidy`): songs already in the library twice keep their best
  copy (the other goes to `_Replaced/`), and the owner's preferred names ("JAŸ-Z" →
  "Jay Z") are written into the tags and folders. New songs use them from the start.
- **`undo`** wraps `fileops.undo`: after the files are back, the batch's `superseded` and
  `adopted` rips return to the state the plan found them in, and their links go.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import math
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from musicorg import (
    artwork,
    browse,
    fileops,
    fingerprint,
    listening,
    lyrics,
    naming,
    queue,
    scan,
    state,
    tags,
    youtube,
)
from musicorg.config import Config
from musicorg.errors import (
    AudioError,
    NotFoundError,
    PlanOutOfDateError,
    UserError,
    YouTubeError,
)
from musicorg.index import Index, open_index, open_queue
from musicorg.library import Library
from musicorg.queue import JobContext, Outcome
from musicorg.youtube import Candidate

log = logging.getLogger(__name__)

ONLY = {
    "auto": ("matched_auto",),
    "accepted": ("matched_user",),
    "all-eligible": ("matched_auto", "matched_user"),
}
MATCH_TAG = {"matched_auto": "auto_exact", "matched_user": "user_confirmed"}
DETAILS_TAG = {"matched_auto": "auto_details", "matched_user": "user_details"}
ADOPT_SUFFIXES = frozenset({".mp3", ".m4a", ".flac", ".ogg", ".opus"})
TRUSTED_PARSE = 0.8  # below this, an adopt keeps the rip's own title and artist
MIN_BITRATE_KBPS = 100
DURATION_TOLERANCE_S = 2
BYTES_PER_SECOND = 16_000  # format 140 is about 128 kbps
# Roughly what a video's picture takes, by height, for a plan's size estimate (measured on
# one official video, 2026-10-01; a video that's a still picture takes far less).
VIDEO_KBPS = {144: 100, 240: 190, 360: 370, 480: 580, 720: 1030, 1080: 3200}
WORK_S_PER_DOWNLOAD = 10  # download, check, fingerprint, tag: a rough figure for estimates
WORK_S_PER_ADOPT = 2
WORK_S_PER_DETAILS = 4  # an adopt with official details: a copy plus an album lookup
DONE_STATES = ("superseded", "adopted")
UNCONFIRMED = "unconfirmed"  # MUSICORG_MATCH of a rip copied in before it was identified
UNCONFIRMED_STATES = ("review", "not_found")
LOSSLESS = frozenset({"flac", "alac", "wavpack", "pcm_s16le", "pcm_s24le"})
KEPT_DIR = "kept"
FILE_CHANGED = "file_changed"


# ---- step 10's hook ----------------------------------------------------------------


@dataclass
class Extras:
    """What step 10 adds to a replace: plain lyrics and the cover go in the tag write;
    synced lyrics become the `.lrc` sidecar."""

    lyrics: str | None = None
    synced_lyrics: str | None = None
    cover: bytes | None = None
    cover_mime: str | None = None


@dataclass
class ExtrasQuery:
    """What the step 10 hooks get: the official track, its album, the file's own length
    and version tokens, and the index (as the answers' cache)."""

    candidate: Candidate
    album: AlbumInfo
    duration_s: float | None
    versions: tuple[str, ...]
    cache: Any


ExtrasHook = Callable[[ExtrasQuery], Extras | None]


def lyrics_extras(query: ExtrasQuery) -> Extras | None:
    c = query.candidate
    found = lyrics.find(
        lyrics.Query(
            title=c.title, artist=", ".join(c.artists), album=query.album.title,
            duration_s=query.duration_s, video_id=c.video_id,
            official_s=float(c.duration_s) if c.duration_s else None, versions=query.versions,
        ),
        cache=query.cache,
    )  # fmt: skip
    return Extras(lyrics=found.plain, synced_lyrics=found.synced)


def cover_extras(query: ExtrasQuery) -> Extras | None:
    if not query.album.browse_id:
        return None
    art = artwork.album_art(query.album.browse_id, cache=query.cache)
    return Extras(cover=art.data, cover_mime=art.mime) if art is not None else None


EXTRAS: list[ExtrasHook] = [lyrics_extras, cover_extras]


def _versions(op: fileops.PlanOp) -> tuple[str, ...]:
    return tuple(str(v) for v in (op.params["candidate"].get("version_tokens") or []))


def _extras(query: ExtrasQuery) -> Extras:
    """Everything the hooks give. A hook that fails only costs its extra: missing lyrics
    never fail a job."""
    found = Extras()
    for hook in EXTRAS:
        try:
            got = hook(query)
        except (UserError, OSError) as exc:
            log.warning("No extras for %s from %s: %s", query.candidate.video_id, hook, exc)
            continue
        if got is None:
            continue
        found.lyrics = found.lyrics or got.lyrics
        found.synced_lyrics = found.synced_lyrics or got.synced_lyrics
        if found.cover is None and got.cover:
            found.cover, found.cover_mime = got.cover, got.cover_mime
    return found


# ---- planning ------------------------------------------------------------------------


def plan_replace(
    lib: Library,
    index: Index,
    *,
    only: str = "all-eligible",
    limit: int | None = None,
    stage_only: bool = False,
    config: Config | None = None,
) -> fileops.Plan:
    """A dry-run plan replacing matched rips with official downloads, saved to
    `.musicorg/plans/`. One download per video however many rips matched it; `limit`
    counts videos. A video already in the library isn't downloaded again."""
    if only not in ONLY:
        raise UserError(f"--only takes auto, accepted or all-eligible, not {only!r}.")
    if limit is not None and limit < 1:
        raise UserError("--limit must be 1 or more.")
    data = lib.load_state().data
    decisions, gate = state.decisions(data), state.gate(data)
    folders = scan.source_folders(lib, index)
    busy = _items_in_open_jobs(lib)
    skipped: dict[str, int] = {}

    def skip(why: str) -> None:
        skipped[why] = skipped.get(why, 0) + 1

    groups: dict[str, list[fileops.PlanOp]] = {}
    for item in index.items_in_states(ONLY[only]):
        if item["id"] in busy:
            skip("already_queued")
            continue
        chosen = _chosen(index, item, decisions)
        if chosen is None:
            skip("no_candidate")
            continue
        verdict = gate.get(item["id"], {}).get(chosen["video_id"], {}).get("verdict")
        if verdict in ("different", "uncertain"):
            skip(f"fingerprint_{'mismatch' if verdict == 'different' else 'uncertain'}_before")
            continue
        rip = Path(scan.item_path(folders, item))
        try:
            source = fileops.FileCheck.of(lib, rip)
        except OSError:
            skip("rip_missing")
            continue
        video_id = chosen["video_id"]
        if video_id not in groups and limit is not None and len(groups) >= limit:
            skip("over_limit")
            continue
        groups.setdefault(video_id, []).append(
            fileops.PlanOp(
                action="replace",
                item_id=item["id"],
                item_state=item["state"],
                source=source,
                params={
                    "video_id": video_id,
                    "candidate": chosen["payload"],
                    "score": chosen["score"],
                    "rip": str(rip),
                    "stage_only": stage_only,
                },
            )
        )

    in_library = {v for v in groups if _library_copy(lib, index, v) is not None}
    downloads = [v for v in groups if v not in in_library]
    seconds = sum((groups[v][0].params["candidate"].get("duration_s") or 0) for v in downloads)
    ops = [op for video_ops in groups.values() for op in video_ops]
    summary = {
        "operations": len(ops),
        "downloads": len(downloads),
        "in_library": len(in_library),
        "est_minutes": estimate_minutes(len(downloads), 0, config),
        "days": download_days(len(downloads), config),
        "disk_mb": round(seconds * BYTES_PER_SECOND / 1e6, 1),
        "low_confidence_adopts": 0,
        "only": only,
        "stage_only": stage_only,
        "skipped": skipped,
    }
    plan = fileops.new_plan("replace", ops, summary)
    fileops.save_plan(lib, plan)
    return plan


def plan_adopt(
    lib: Library,
    index: Index,
    *,
    include_not_found: bool = False,
    matched: bool = False,
    unconfirmed: bool = False,
    config: Config | None = None,
) -> fileops.Plan:
    """A dry-run plan copying `only_copy` rips (and with `include_not_found`, every
    `not_found` one) into `Music/`. The summary counts adopts whose names are unsure
    (parsed with confidence under 0.8 and no fixes from the owner): those keep the rip's
    own title and artist tags.

    With `matched` (step 09c), matched rips come too, keeping their own audio, with their
    match's official details. A rip whose match the fingerprint gate turned down stays
    out, and so does a WebM, raw AAC or WAV rip (only a download would fix those).

    With `unconfirmed` (v0.2), every rip still in `review` or `not_found` is copied in
    under its own names, tagged `MUSICORG_MATCH=unconfirmed`, so it can be played now.
    Its state doesn't change: it stays in the review queue. When it's decided later, the
    usual adopt finds that copy and upgrades it where it is (new tags, new name) instead
    of copying the rip a second time."""
    states = ("only_copy", "not_found") if include_not_found else ("only_copy",)
    if unconfirmed:
        states = tuple(dict.fromkeys(states + UNCONFIRMED_STATES))
    copies = _unconfirmed_copies(index)
    data = lib.load_state().data
    decisions, gate = state.decisions(data), state.gate(data)
    folders = scan.source_folders(lib, index)
    busy = _items_in_open_jobs(lib)
    skipped: dict[str, int] = {}
    planned: set[str] = set()
    ops: list[fileops.PlanOp] = []
    low_confidence = unsupported = size = waiting_count = 0
    for item in index.items_in_states(states):
        if item["id"] in busy:
            skipped["already_queued"] = skipped.get("already_queued", 0) + 1
            continue
        rip = Path(scan.item_path(folders, item))
        try:
            source = fileops.FileCheck.of(lib, rip)
        except OSError:
            skipped["rip_missing"] = skipped.get("rip_missing", 0) + 1
            continue
        suffix = _suffix(item)
        # Not final: a rip still to be reviewed (a `not_found` one too, unless
        # --include-not-found says to settle those now).
        waiting = item["state"] == "review" or (
            item["state"] == "not_found" and not include_not_found
        )
        prior = copies.get(state.normalise_path(rip))
        if waiting and prior is not None:
            skipped["already_in_library"] = skipped.get("already_in_library", 0) + 1
            continue
        if waiting and suffix not in ADOPT_SUFFIXES:
            skipped["format_needs_a_download"] = skipped.get("format_needs_a_download", 0) + 1
            continue
        if suffix not in ADOPT_SUFFIXES:
            unsupported += 1
            ops.append(fileops.PlanOp(action="unsupported", item_id=item["id"],
                                      item_state=item["state"], source=source,
                                      params={"rip": str(rip)}))  # fmt: skip
            continue
        decision = decisions.get(item["id"], {})
        fixes = {
            k: decision[k]
            for k in ("artist_fix", "title_fix", "album_fix")
            if decision.get("decision") == "only_copy"
            and isinstance(decision.get(k), str)
            and decision[k].strip()
        }
        trusted = bool(fixes) or (item.get("parse_confidence") or 0) >= TRUSTED_PARSE
        if waiting:
            waiting_count += 1
        elif not trusted:
            low_confidence += 1
        names = _adopt_names(item, fixes, trusted, rip)
        meta = naming.TrackMeta(
            title=names["title"], artist=names["artist"], album_artist=names.get("album_artist"),
            album=names.get("album"), year=names.get("year"), track=names.get("track"),
            ext=suffix, source_file=rip.name,
        )  # fmt: skip
        ideal = naming.library_path(meta, lib.root)
        stays = prior is not None and _fold(PurePosixPath(naming.MUSIC_DIR, *ideal.parts)) == (
            _fold(PurePosixPath(prior))
        )
        target = ideal if stays else _free_target(lib, ideal, planned)
        planned.add(_fold(target))
        rel_target = PurePosixPath(naming.MUSIC_DIR, *target.parts).as_posix()
        if prior is None:
            size += source.size
        ops.append(
            fileops.PlanOp(
                action="adopt_unconfirmed" if waiting else "adopt",
                item_id=item["id"],
                item_state=item["state"],
                source=source,
                target=rel_target,
                # An upgrade that keeps its name has nothing new to check in the folder.
                target_folder=None
                if stays
                else fileops.FolderCheck.of(lib, lib.paths.music / target.parent),
                params={"rip": str(rip), "names": names, "fixes": fixes, "trusted": trusted},
            )
        )
    details = duplicates = 0
    if matched:
        wanted: dict[str, list[tuple[dict[str, Any], dict[str, Any], Path, fileops.FileCheck]]] = {}
        for item in index.items_in_states(ONLY["all-eligible"]):
            if item["id"] in busy:
                skipped["already_queued"] = skipped.get("already_queued", 0) + 1
                continue
            chosen = _chosen(index, item, decisions)
            if chosen is None:
                skipped["no_candidate"] = skipped.get("no_candidate", 0) + 1
                continue
            verdict = gate.get(item["id"], {}).get(chosen["video_id"], {}).get("verdict")
            if verdict in ("different", "uncertain"):
                skipped["fingerprint_turned_down"] = skipped.get("fingerprint_turned_down", 0) + 1
                continue
            if _suffix(item) not in ADOPT_SUFFIXES:
                skipped["format_needs_a_download"] = skipped.get("format_needs_a_download", 0) + 1
                continue
            rip = Path(scan.item_path(folders, item))
            try:
                source = fileops.FileCheck.of(lib, rip)
            except OSError:
                skipped["rip_missing"] = skipped.get("rip_missing", 0) + 1
                continue
            wanted.setdefault(chosen["video_id"], []).append((item, chosen, rip, source))
        linked: list[fileops.PlanOp] = []
        for video_id, group in wanted.items():
            group.sort(key=lambda g: _rip_quality(g[0], g[3]), reverse=True)
            in_library = bool(index.library_tracks_with_source_id(video_id))
            for n, (item, chosen, rip, source) in enumerate(group):
                if n == 0 and not in_library:
                    details += 1
                    size += source.size
                    action = "adopt_details"
                else:
                    duplicates += 1
                    action = "adopt_duplicate"
                op = fileops.PlanOp(
                    action=action,
                    item_id=item["id"],
                    item_state=item["state"],
                    source=source,
                    params={"rip": str(rip), "video_id": video_id,
                            "candidate": chosen["payload"], "score": chosen["score"]},
                )  # fmt: skip
                (ops if action == "adopt_details" else linked).append(op)
        ops.extend(linked)  # after the copies they link to
    adopts = len(ops) - unsupported - details - duplicates - waiting_count
    summary = {
        "operations": len(ops),
        "downloads": 0,
        "adopts": adopts,
        "unconfirmed": waiting_count,
        "with_details": details,
        "duplicates": duplicates,
        "unsupported_format": unsupported,
        "est_minutes": estimate_minutes(0, adopts + waiting_count, config)
        + math.ceil(details * WORK_S_PER_DETAILS / 60),
        "days": 0,
        "disk_mb": round(size / 1e6, 1),
        "low_confidence_adopts": low_confidence,
        "include_not_found": include_not_found,
        "matched": matched,
        "skipped": skipped,
    }
    plan = fileops.new_plan("adopt", ops, summary)
    fileops.save_plan(lib, plan)
    return plan


def estimate_minutes(downloads: int, adopts: int, config: Config | None = None) -> int:
    """Roughly how long the queue takes: the pace between downloads (the quiet start
    included) plus the work itself. Waits for the daily cap aren't counted: see
    `download_days`."""
    t = (config if config is not None else Config.load()).throttle()
    quiet = min(downloads, t["quiet_start_downloads"])
    pace = quiet * (t["quiet_start_min_s"] + t["quiet_start_max_s"]) / 2
    pace += (downloads - quiet) * (t["pause_min_s"] + t["pause_max_s"]) / 2
    seconds = pace + downloads * WORK_S_PER_DOWNLOAD + adopts * WORK_S_PER_ADOPT
    return math.ceil(seconds / 60) if seconds else 0


def download_days(downloads: int, config: Config | None = None) -> int:
    """How many 24-hour stretches the daily cap spreads the downloads over."""
    cap = (config if config is not None else Config.load()).throttle()["daily_cap"]
    return math.ceil(downloads / cap) if downloads and cap > 0 else (1 if downloads else 0)


def _chosen(
    index: Index, item: dict[str, Any], decisions: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """The candidate to download: the owner's choice for `matched_user`, else the best."""
    candidates = index.candidates(item["id"])
    if not candidates:
        return None
    wanted = decisions.get(item["id"], {}).get("video_id")
    if item["state"] == "matched_user" and wanted:
        for c in candidates:
            if c["video_id"] == wanted:
                return c
    return candidates[0]


def _unconfirmed_copies(index: Index) -> dict[str, str]:
    """Rips already in the library as an unconfirmed copy: the rip's path (normalised)
    → the copy's path, relative to the library root."""
    return {
        state.normalise_path(Path(t["origin_path"])): str(t["rel_path"])
        for t in index.library_tracks()
        if t.get("match") == UNCONFIRMED and t.get("origin_path")
    }


def _unconfirmed_copy(lib: Library, index: Index, rip: Path) -> Path | None:
    """The library file that is `rip`'s unconfirmed copy, checked against its own tags
    (the index is only a cache). None if there isn't one."""
    rel = _unconfirmed_copies(index).get(state.normalise_path(rip))
    if rel is None:
        return None
    path = lib.root / Path(*PurePosixPath(rel).parts)
    if not path.is_file():
        return None
    found = tags.read_tags(path)
    same_rip = isinstance(found.origin_path, str) and state.normalise_path(
        Path(found.origin_path)
    ) == state.normalise_path(rip)
    return path if found.match == UNCONFIRMED and same_rip else None


def _move_upgraded(ctx: JobContext, index: Index, path: Path, rel_target: Path) -> Path:
    """Move an upgraded copy to `Music/<rel_target>` if that isn't where it is, and
    forget its old index row. Returns where it is now."""
    index.remove_library_tracks([_rel_path(ctx.lib, path)])
    if _fold(_music_rel(ctx.lib, path)) == _fold(rel_target):
        return path
    old_folder = path.parent
    final = fileops.move(ctx.batch, path, rel_target)
    fileops.remove_empty_folders(ctx.lib, old_folder)
    return final


def _suffix(item: dict[str, Any]) -> str:
    ext = str(item.get("ext") or Path(item["rel_path"]).suffix).lower()
    return ext if ext.startswith(".") else f".{ext}"


def _adopt_names(
    item: dict[str, Any], fixes: dict[str, str], trusted: bool, rip: Path
) -> dict[str, Any]:
    """Title, artist and album for an adopted rip (step 09b's metadata rule): the parsed
    names (or the owner's fixes) when trusted, else the rip's own tags. A missing artist
    is Unknown Artist; a missing title, the file's name."""
    own = (item.get("raw_tags_json") or {}).get("tags") or {}
    names: dict[str, Any] = {
        k: own.get(k) for k in ("album_artist", "album", "year", "track") if own.get(k)
    }
    if trusted:
        names["artist"] = fixes.get("artist_fix") or item.get("parsed_artist") or own.get("artist")
        names["title"] = fixes.get("title_fix") or item.get("parsed_title") or own.get("title")
        if fixes.get("album_fix"):
            names["album"] = fixes["album_fix"]
    else:
        names["artist"], names["title"] = own.get("artist"), own.get("title")
    names["artist"] = names.get("artist") or naming.UNKNOWN_ARTIST
    names["title"] = names.get("title") or rip.stem
    return names


def _free_target(lib: Library, target: Path, planned: set[str]) -> Path:
    """`target`, or ` (2)` etc. when an earlier adopt in the same plan will take it."""
    for candidate in naming.candidate_names(target):
        if _fold(candidate) not in planned:
            return candidate
    raise UserError(f"Too many adopted files would be named {target.name}.")


def _fold(path: Path | PurePosixPath) -> str:
    return PurePosixPath(*path.parts).as_posix().casefold()


def _items_in_open_jobs(lib: Library) -> set[str]:
    """Items that a queued or running job will act on."""
    found: set[str] = set()
    with open_queue(lib.paths, write=False) as store:
        for job_state in ("queued", "running"):
            for job in store.jobs(state=job_state):
                found.update(
                    op["item_id"] for op in job["payload"].get("ops", []) if op.get("item_id")
                )
    return found


# ---- showing and applying ------------------------------------------------------------


def show(lib: Library, plan_id: str) -> fileops.Plan:
    """A saved plan (no lock needed)."""
    return fileops.load_plan(lib, plan_id)


@dataclass
class ApplyResult:
    plan_id: str
    batch_id: str
    jobs: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def apply(lib: Library, index: Index, plan_id: str) -> ApplyResult:
    """Check a plan still holds, open a batch, and queue its jobs. The batch stays open
    until its last job ends. Refuses a plan that was applied already, or whose rips or
    items changed since it was made."""
    plan = fileops.load_plan(lib, plan_id)
    with open_queue(lib.paths, write=False) as store:
        earlier = store.jobs(plan_id=plan_id)
    if earlier:
        raise PlanOutOfDateError(
            f"Plan {plan_id} was applied already (batch {earlier[0]['batch_id']}). Make a new "
            "plan to go further."
        )
    if not plan.operations:
        raise UserError(f"Plan {plan_id} has nothing to do.")

    problems = [p.message for p in fileops.validate(lib, plan, item_state=_state_lookup(index))]
    busy = _items_in_open_jobs(lib)
    problems += [
        f"item {op.item_id} is already queued by another batch"
        for op in plan.operations
        if op.item_id in busy
    ]
    if problems:
        shown = "\n".join(f"  - {p}" for p in problems[:5])
        more = f"\n  … and {len(problems) - 5} more" if len(problems) > 5 else ""
        raise PlanOutOfDateError(
            f"Plan {plan_id} is out of date, so nothing was queued:\n{shown}{more}\nMake a "
            f"new plan (`musicorg plan {plan.kind}`) and apply that."
        )

    b = fileops.open_batch(lib, plan.kind)
    jobs = _jobs(plan, b.batch_id)
    try:
        queue.add_jobs(lib, jobs)
    except BaseException:
        fileops.close_batch(lib, b.batch_id)
        raise
    log.info("Applied plan %s as batch %s: %d jobs", plan_id, b.batch_id, len(jobs))
    return ApplyResult(plan_id, b.batch_id, len(jobs))


def _jobs(plan: fileops.Plan, batch_id: str) -> list[dict[str, Any]]:
    """One job per video for a replace plan; one per operation for the others."""
    if plan.kind != "replace":
        return [
            {"batch_id": batch_id, "plan_id": plan.plan_id, "kind": plan.kind,
             "item_id": op.item_id, "payload": {"ops": [asdict(op)]}}
            for op in plan.operations
        ]  # fmt: skip
    groups: dict[str, list[fileops.PlanOp]] = {}
    for op in plan.operations:
        groups.setdefault(op.params["video_id"], []).append(op)
    return [
        {"batch_id": batch_id, "plan_id": plan.plan_id, "kind": "replace",
         "item_id": ops[0].item_id,
         "payload": {"video_id": video_id, "candidate": ops[0].params["candidate"],
                     "stage_only": bool(ops[0].params.get("stage_only")),
                     "ops": [asdict(op) for op in ops]}}
        for video_id, ops in groups.items()
    ]  # fmt: skip


def _state_lookup(index: Index) -> fileops.ItemStateLookup:
    def lookup(item_id: str) -> str | None:
        item = index.item(item_id)
        return item["state"] if item else None

    return lookup


def _ops(payload: dict[str, Any]) -> list[fileops.PlanOp]:
    return fileops.Plan.from_dict(
        {"plan_id": "p_job", "kind": "replace", "created_at": "", "operations": payload["ops"]}
    ).operations


# ---- the replace job -----------------------------------------------------------------


@dataclass(frozen=True)
class GateResult:
    verdict: str  # fingerprint.VERDICTS
    ber: float | None
    why: str

    @property
    def reason(self) -> str:
        return "fingerprint_mismatch" if self.verdict == "different" else "fingerprint_uncertain"


@dataclass
class AlbumInfo:
    title: str | None = None
    artist: str | None = None
    year: str | None = None
    track: int | None = None
    track_total: int | None = None
    browse_id: str | None = None


def replace_job(ctx: JobContext) -> Outcome:
    payload = ctx.payload
    video_id = str(payload["video_id"])
    candidate = Candidate.from_dict(payload["candidate"])
    with open_index(ctx.lib.paths, write=True) as index:
        live, changed = _still_valid(ctx, index, _ops(payload))
        if not live:
            if changed:
                return Outcome.needs_review(FILE_CHANGED, fileops.FILE_CHANGED)
            return Outcome.done("Every rip in this job was already replaced.")

        existing = _library_copy(ctx.lib, index, video_id)
        if existing is not None and not payload.get("stage_only"):
            return _link_to_existing(ctx, index, live, video_id, existing)

        path, info = ctx.download(video_id)
        problem = _check_download(path, info, candidate)
        if problem is not None:
            reason, message = problem
            for op in live:
                index.set_state(str(op.item_id), "review", [reason])
            return Outcome.needs_review(reason, message)

        download_fp = fingerprint.fingerprint(path)
        limits = Config.load().fingerprint()
        results = [(op, _gate(Path(op.params["rip"]), download_fp, limits, index)) for op in live]
        if payload.get("stage_only"):
            return _keep_for_calibration(ctx, path, video_id, results)
        _record_gate(ctx.lib, video_id, results)
        passed = [op for op, result in results if result.verdict == "match"]
        for op, result in results:
            if result.verdict != "match":
                index.set_state(str(op.item_id), "review", [result.reason])
                log.info("%s vs %s: %s (%s)", op.params["rip"], video_id, result.verdict,
                         result.why)  # fmt: skip
        if not passed:
            fileops.set_aside(ctx.lib, path, f"{ctx.batch.batch_id}/{KEPT_DIR}")
            first = results[0][1]
            return Outcome.needs_review(first.reason, first.why)

        names = state.names(ctx.lib.load_state().data)
        candidate = _preferred(candidate, names)
        album = _preferred_album(_album(candidate, index), names)
        probe = tags.probe(path)
        extras = _extras(ExtrasQuery(candidate, album, probe.duration_s,
                                     _versions(passed[0]), index))  # fmt: skip
        new_tags = _download_tags(
            candidate, album, extras, info, probe, passed[0], float(passed[0].params["score"])
        )
        fileops.write_tags(ctx.batch, path, new_tags)
        meta = naming.TrackMeta(
            title=candidate.title, artist=", ".join(candidate.artists) or None,
            album_artist=album.artist, album=album.title, year=album.year, track=album.track,
            compilation=album.artist == naming.VARIOUS_ARTISTS, ext=path.suffix,
        )  # fmt: skip
        final = fileops.commit(ctx.batch, path, naming.library_path(meta, ctx.lib.root))
        musicorg_id = str(new_tags.musicorg_id)
        index.put_library_tracks([_track_row(ctx.lib, final, new_tags, probe.duration_s)])
        _write_sidecars(ctx, final, extras)
        _mark_replaced(ctx.lib, index, passed, musicorg_id)
    kept = len(passed)
    return Outcome.done(
        f"{final.name}: replaces {kept} rip{'s' if kept != 1 else ''}"
        + (f"; {len(results) - kept} back to review" if kept < len(results) else "")
    )


def _still_valid(
    ctx: JobContext, index: Index, ops: list[fileops.PlanOp]
) -> tuple[list[fileops.PlanOp], bool]:
    """The job's operations whose preconditions still hold. A rip that changed goes to
    review with `file_changed`; one already replaced (by another batch) is left out."""
    live, changed = [], False
    lookup = _state_lookup(index)
    for op in ops:
        now = lookup(str(op.item_id))
        if now in DONE_STATES:
            continue
        problem = fileops.check_op(ctx.lib, op, item_state=lookup)
        if problem is not None:
            log.warning("Job %s: %s", ctx.job["id"], problem.message)
            if now is not None:
                index.set_state(str(op.item_id), "review", [FILE_CHANGED])
            changed = True
            continue
        live.append(op)
    return live, changed


def _library_copy(lib: Library, index: Index, video_id: str) -> Path | None:
    """The library file already downloaded from this video, if there is one."""
    for track in index.library_tracks_from("youtube_music", video_id):
        path = lib.root / Path(*PurePosixPath(track["rel_path"]).parts)
        if path.is_file() and track.get("musicorg_id"):
            return path
    return None


def _link_to_existing(
    ctx: JobContext, index: Index, live: list[fileops.PlanOp], video_id: str, existing: Path
) -> Outcome:
    """The video is in the library already: gate each rip against that file and link the
    ones that match. Nothing is downloaded or written in `Music/`."""
    musicorg_id = tags.read_tags(existing).musicorg_id
    assert isinstance(musicorg_id, str)
    limits = Config.load().fingerprint()
    existing_fp = fingerprint.fingerprint(existing, index=index)
    results = [(op, _gate(Path(op.params["rip"]), existing_fp, limits, index)) for op in live]
    _record_gate(ctx.lib, video_id, results)
    passed = [op for op, result in results if result.verdict == "match"]
    for op, result in results:
        if result.verdict != "match":
            index.set_state(str(op.item_id), "review", [result.reason])
    if not passed:
        first = results[0][1]
        return Outcome.needs_review(first.reason, first.why)
    _mark_replaced(ctx.lib, index, passed, musicorg_id)
    return Outcome.done(f"Linked {len(passed)} rip(s) to {existing.name}, already in the library.")


def _check_download(
    path: Path, info: dict[str, Any], candidate: Candidate
) -> tuple[str, str] | None:
    """Is this the audio we asked for? (review reason, message), or None if it is."""
    delivered = str(info.get("format_id") or "")
    if delivered != youtube.DOWNLOAD_FORMAT:
        return (
            "format_140_unavailable",
            f"YouTube delivered format {delivered or 'unknown'}, not 140; nothing is kept.",
        )
    probe = tags.probe(path)  # AudioError: the queue retries
    if (probe.codec or "").lower() != "aac":
        return ("format_140_unavailable",
                f"The download's audio is {probe.codec or 'unknown'}, not AAC.")  # fmt: skip
    if probe.bitrate_kbps is not None and probe.bitrate_kbps < MIN_BITRATE_KBPS:
        return ("format_140_unavailable",
                f"The download is only {probe.bitrate_kbps} kbps (at least "
                f"{MIN_BITRATE_KBPS} expected).")  # fmt: skip
    expected = candidate.duration_s
    if expected is not None and probe.duration_s is not None:
        if abs(probe.duration_s - expected) > DURATION_TOLERANCE_S:
            return ("duration_mismatch",
                    f"The download is {probe.duration_s:.0f} s long; YouTube Music said "
                    f"{expected} s.")  # fmt: skip
    return None


def _gate(
    rip: Path, other: fingerprint.RawFP, limits: dict[str, float], index: Index
) -> GateResult:
    """The fingerprint gate for one rip. A rip whose audio can't be fingerprinted is
    `uncertain`: it goes to review rather than being replaced unchecked."""
    try:
        rip_fp = fingerprint.fingerprint(rip, index=index)
    except (AudioError, NotFoundError) as exc:
        return GateResult("uncertain", None, f"The rip couldn't be fingerprinted: {exc.message}")
    result = fingerprint.compare_items(rip_fp.items, other.items, limits)
    return GateResult(result.verdict, round(result.ber, 4), result.why)


def _record_gate(
    lib: Library, video_id: str, results: list[tuple[fileops.PlanOp, GateResult]]
) -> None:
    now = _now()
    with state.edit(lib.paths.state_file) as st:
        found = st.data.get("gate")
        if not isinstance(found, dict):
            found = st.data["gate"] = {}
        for op, result in results:
            per_item = found.get(str(op.item_id))
            if not isinstance(per_item, dict):
                per_item = found[str(op.item_id)] = {}
            per_item[video_id] = {"verdict": result.verdict, "ber": result.ber,
                                  "why": result.why, "checked_at": now}  # fmt: skip


def _keep_for_calibration(
    ctx: JobContext, path: Path, video_id: str, results: list[tuple[fileops.PlanOp, GateResult]]
) -> Outcome:
    """--stage-only: keep the download and a note of each comparison; change nothing."""
    note = {
        "video_id": video_id,
        "comparisons": [
            {"item_id": op.item_id, "rip": op.params["rip"], "verdict": r.verdict,
             "ber": r.ber, "why": r.why}
            for op, r in results
        ],
    }  # fmt: skip
    kept = fileops.set_aside(
        ctx.lib, path, f"{naming.CALIBRATION_DIR}/{ctx.batch.batch_id}", note=note
    )
    verdicts = ", ".join(r.verdict for _, r in results)
    return Outcome.done(f"Calibration: kept {kept.name} ({verdicts}); nothing committed.")


def _album(candidate: Candidate, cache: youtube.SearchCache | None = None) -> AlbumInfo:
    """Album, album artist, year and track number from YouTube Music. A lookup that fails
    costs only those tags (a YouTube slow-down still stops the queue)."""
    found = candidate
    if not found.album_browse_id:
        try:
            fuller = youtube.get_track(candidate.video_id)
        except YouTubeError as exc:
            log.info("No album details for %s: %s", candidate.video_id, exc)
            fuller = None
        if fuller is not None:
            found = Candidate(
                video_id=candidate.video_id, title=candidate.title, artists=candidate.artists,
                album=fuller.album or candidate.album, album_browse_id=fuller.album_browse_id,
                duration_s=candidate.duration_s, is_explicit=candidate.is_explicit
                if candidate.is_explicit is not None else fuller.is_explicit,
                video_type=candidate.video_type, year=candidate.year or fuller.year,
                thumbnail=candidate.thumbnail or fuller.thumbnail,
            )  # fmt: skip
    info = AlbumInfo(title=found.album, year=found.year, browse_id=found.album_browse_id)
    if not found.album_browse_id:
        return info
    try:
        album = youtube.get_album(found.album_browse_id, cache=cache)
    except YouTubeError as exc:
        log.info("Couldn't read the album %s: %s", found.album_browse_id, exc)
        return info
    info.title = album.title or info.title
    info.artist = ", ".join(album.artists) or None
    info.year = album.year or info.year
    info.track, info.track_total = youtube.find_track(
        album, video_id=found.video_id, title=found.title, duration_s=found.duration_s
    )
    return info


def _download_tags(
    candidate: Candidate,
    album: AlbumInfo,
    extras: Extras,
    info: dict[str, Any],
    probe: tags.Probe,
    origin: fileops.PlanOp,
    score: float,
) -> tags.TrackTags:
    versions = [str(v) for v in (origin.params["candidate"].get("version_tokens") or [])]
    return tags.TrackTags(
        title=candidate.title,
        artist=", ".join(candidate.artists) or None,
        album_artist=album.artist,
        album=album.title,
        year=_year(album.year),
        track=album.track,
        track_total=album.track_total if album.track else None,
        explicit=candidate.is_explicit,
        lyrics=extras.lyrics,
        cover=extras.cover,
        cover_mime=extras.cover_mime if extras.cover else None,
        schema=tags.SCHEMA_VERSION,
        musicorg_id=tags.new_track_id(),
        source="youtube_music",
        source_id=candidate.video_id,
        source_format=str(info["format_id"]),
        source_bitrate=probe.bitrate_kbps,
        acquired=_now(),
        match=MATCH_TAG.get(str(origin.item_state)),
        match_score=round(min(max(score, 0.0), 1.0), 3),
        origin_path=str(origin.params["rip"]),
        version=versions or None,
    )


def _write_sidecars(ctx: JobContext, final: Path, extras: Extras) -> None:
    """The `.lrc` and `cover.jpg`, named after the file as committed."""
    if extras.synced_lyrics:
        fileops.write_sidecar(ctx.batch, final, ".lrc", extras.synced_lyrics.encode("utf-8"))
    if extras.cover and (extras.cover_mime or tags.JPEG) == tags.JPEG:
        fileops.write_sidecar(ctx.batch, final, naming.COVER_NAME, extras.cover)


def _mark_replaced(
    lib: Library, index: Index, passed: list[fileops.PlanOp], musicorg_id: str
) -> None:
    """Link each rip to the library file (state.json first: it's what a rebuild trusts),
    then mark it `superseded`."""
    with state.edit(lib.paths.state_file) as st:
        links = st.data.get("superseded")
        if not isinstance(links, dict):
            links = st.data["superseded"] = {}
        for op in passed:
            links[state.normalise_path(Path(op.params["rip"]))] = musicorg_id
    for op in passed:
        index.set_state(str(op.item_id), "superseded", [])


def _track_row(
    lib: Library, final: Path, written: tags.TrackTags, duration: float | None
) -> dict[str, Any]:
    info = final.stat()

    def text(value: object) -> str | None:
        return value if isinstance(value, str) else None

    return {
        "rel_path": PurePosixPath(*final.relative_to(lib.root).parts).as_posix(),
        "musicorg_id": text(written.musicorg_id),
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "title": text(written.title),
        "artist": text(written.artist),
        "album": text(written.album),
        "duration_s": duration,
        "source": text(written.source),
        "source_id": text(written.source_id),
        "only_copy": 1 if written.only_copy is True else 0,
        "origin_path": text(written.origin_path),
        "match": text(written.match),
    }


# ---- the adopt job -------------------------------------------------------------------


def adopt_job(ctx: JobContext) -> Outcome:
    (op,) = _ops(ctx.payload)
    item_id = str(op.item_id)
    with open_index(ctx.lib.paths, write=True) as index:
        item = index.item(item_id)
        if item is None:
            return Outcome.needs_review(FILE_CHANGED, f"Item {item_id} is no longer indexed.")
        if item["state"] in DONE_STATES:
            return Outcome.done("Already in the library.")
        problem = fileops.check_op(ctx.lib, op, item_state=_state_lookup(index))
        if problem is not None:
            index.set_state(item_id, "review", [FILE_CHANGED])
            return Outcome.needs_review(FILE_CHANGED, problem.message)
        if op.action == "unsupported":
            index.set_state(item_id, "unsupported_format", [])
            return Outcome.done(f"{Path(op.params['rip']).name}: this format isn't adopted.")
        if op.action == "adopt_details":
            return _adopt_with_details(ctx, index, op)
        if op.action == "adopt_duplicate":
            return _link_duplicate(ctx, index, op)

        rip = Path(op.params["rip"])
        assert op.target is not None
        rel = PurePosixPath(op.target).relative_to(naming.MUSIC_DIR)
        prior = _unconfirmed_copy(ctx.lib, index, rip)
        if op.action == "adopt_unconfirmed" and prior is not None:
            return Outcome.done(f"Already in the library as {prior.name}.")
        if prior is not None:  # decided since it was copied in: upgrade that copy
            probe = tags.probe(prior)
            current = tags.read_tags(prior)
            new_tags = _adopt_tags(op, current, probe, rip)
            fileops.write_tags(ctx.batch, prior, new_tags)
            final = _move_upgraded(ctx, index, prior, Path(*rel.parts))
        else:
            staged = fileops.stage_copy(ctx.batch, rip)
            probe = tags.probe(staged)
            current = tags.read_tags(staged)
            new_tags = _adopt_tags(op, current, probe, rip)
            fileops.write_tags(ctx.batch, staged, new_tags)
            final = fileops.commit(ctx.batch, staged, Path(*rel.parts))
        written = tags.merge(current, new_tags)
        index.put_library_tracks([_track_row(ctx.lib, final, written, probe.duration_s)])
        if op.action == "adopt_unconfirmed":
            return Outcome.done(f"Copied in, still to be reviewed, as {final.name}.")
        index.set_state(item_id, "adopted", [])
    return Outcome.done(f"Copied in as {final.name}.")


def _adopt_with_details(ctx: JobContext, index: Index, op: fileops.PlanOp) -> Outcome:
    """Step 09c: the rip's own audio, with its match's official details. Nothing is
    downloaded; YouTube Music is asked only for the album (once per album, cached)."""
    item_id, rip = str(op.item_id), Path(op.params["rip"])
    names = state.names(ctx.lib.load_state().data)
    candidate = _preferred(Candidate.from_dict(op.params["candidate"]), names)
    album = _preferred_album(_album(candidate, index), names)
    prior = _unconfirmed_copy(ctx.lib, index, rip)
    staged = prior if prior is not None else fileops.stage_copy(ctx.batch, rip)
    probe = tags.probe(staged)
    extras = _extras(ExtrasQuery(candidate, album, probe.duration_s, _versions(op), index))
    current = tags.read_tags(staged)
    change = _details_tags(op, candidate, album, extras, current, probe, rip)
    fileops.write_tags(ctx.batch, staged, change)
    meta = naming.TrackMeta(
        title=candidate.title, artist=", ".join(candidate.artists) or None,
        album_artist=album.artist, album=album.title, year=album.year, track=album.track,
        compilation=album.artist == naming.VARIOUS_ARTISTS, ext=rip.suffix.lower(),
        source_file=rip.name,
    )  # fmt: skip
    target = naming.library_path(meta, ctx.lib.root)
    if prior is not None:  # copied in unconfirmed earlier: upgrade that copy where it is
        final = _move_upgraded(ctx, index, prior, target)
    else:
        final = fileops.commit(ctx.batch, staged, target)
    written = tags.merge(current, change)
    index.put_library_tracks([_track_row(ctx.lib, final, written, probe.duration_s)])
    _write_sidecars(ctx, final, extras)
    index.set_state(item_id, "adopted", [])
    return Outcome.done(f"Copied in with its official details as {final.name}.")


def _details_tags(
    op: fileops.PlanOp,
    candidate: Candidate,
    album: AlbumInfo,
    extras: Extras,
    current: tags.TrackTags,
    probe: tags.Probe,
    rip: Path,
) -> tags.TrackTags:
    """The official details over the rip's own tags. A track number, total or disc that
    YouTube Music doesn't give is removed rather than kept from the rip: the rip's numbers
    may belong to another album (a compilation, a greatest-hits CD). Never a guess."""
    versions = [str(v) for v in (op.params["candidate"].get("version_tokens") or [])]
    change = tags.TrackTags(
        title=candidate.title,
        artist=", ".join(candidate.artists) or None,
        album_artist=album.artist or tags.REMOVE,
        album=album.title or tags.REMOVE,
        year=_year(album.year) or tags.REMOVE,
        track=album.track or tags.REMOVE,
        track_total=(album.track_total if album.track else None) or tags.REMOVE,
        disc=tags.REMOVE,
        disc_total=tags.REMOVE,
        explicit=candidate.is_explicit,
        lyrics=extras.lyrics,
        cover=extras.cover,
        cover_mime=extras.cover_mime if extras.cover else None,
        schema=tags.SCHEMA_VERSION,
        source="rip_copy",
        source_id=candidate.video_id,
        source_format=(probe.codec or rip.suffix.lstrip(".")).lower(),
        source_bitrate=probe.bitrate_kbps,
        acquired=_now(),
        match=DETAILS_TAG.get(str(op.item_state)),
        match_score=round(min(max(float(op.params["score"]), 0.0), 1.0), 3),
        only_copy=False,
        origin_path=str(rip),
        version=versions or tags.REMOVE,
    )
    if not isinstance(current.musicorg_id, str):
        change.musicorg_id = tags.new_track_id()
    return change


def _adopt_tags(
    op: fileops.PlanOp, current: tags.TrackTags, probe: tags.Probe, rip: Path
) -> tags.TrackTags:
    names = op.params["names"]
    fixes = op.params.get("fixes") or {}
    change = tags.TrackTags(
        schema=tags.SCHEMA_VERSION,
        source="rip_copy",
        source_format=(probe.codec or rip.suffix.lstrip(".")).lower(),
        source_bitrate=probe.bitrate_kbps,
        acquired=_now(),
        only_copy=True,
        origin_path=str(rip),
        match="manual" if fixes else tags.REMOVE,
    )
    if op.action == "adopt_unconfirmed":  # not decided yet: neither only-copy nor matched
        change.only_copy, change.match = None, UNCONFIRMED
    if not isinstance(current.musicorg_id, str):
        change.musicorg_id = tags.new_track_id()
    if op.params.get("trusted"):
        change.artist, change.title = names["artist"], names["title"]
        if fixes.get("album_fix"):
            change.album = fixes["album_fix"]
    else:
        if not current.artist:
            change.artist = names["artist"]
        if not current.title:
            change.title = names["title"]
    return change


def _rip_quality(item: dict[str, Any], source: fileops.FileCheck) -> tuple[int, int, int, int]:
    """The better of two rips of one song: lossless first; then one that isn't a YouTube
    conversion (a CD or iTunes rip beats a converter site's MP3 of any bitrate); then the
    higher bitrate; then the bigger file. (Two converter rips of one YouTube upload came
    from the same audio; the higher-bitrate one lost less in its re-encode.)"""
    lossless = 1 if str(item.get("codec") or "").lower() in LOSSLESS else 0
    extra = (item.get("raw_tags_json") or {}).get("extra") or {}
    original = 0 if scan.youtube_converted(extra, str(item.get("rel_path") or "")) else 1
    return lossless, original, int(item.get("bitrate_kbps") or 0), int(source.size)


def _link_duplicate(ctx: JobContext, index: Index, op: fileops.PlanOp) -> Outcome:
    """A rip of a song already in the library as a better copy: link it, copy nothing."""
    video_id = str(op.params["video_id"])
    for track in index.library_tracks_with_source_id(video_id):
        path = ctx.lib.root / Path(*PurePosixPath(track["rel_path"]).parts)
        if path.is_file() and track.get("musicorg_id"):
            prior = _unconfirmed_copy(ctx.lib, index, Path(op.params["rip"]))
            if prior is not None:  # its unconfirmed copy is now a known duplicate
                fileops.supersede(ctx.batch, prior)
                index.remove_library_tracks([_rel_path(ctx.lib, prior)])
                fileops.remove_empty_folders(ctx.lib, prior.parent)
            _mark_replaced(ctx.lib, index, [op], str(track["musicorg_id"]))
            return Outcome.done(f"A duplicate of {path.name}, which is kept; linked to it.")
    return Outcome.done("Its better copy isn't in the library, so nothing was linked.")


# ---- preferred names and duplicates (step 09d) ---------------------------------------


def prefer(text: str | None, names: dict[str, str]) -> str | None:
    """`text` with each spelling the owner renamed replaced, e.g. "JAŸ-Z, Kanye West" →
    "Jay Z, Kanye West"."""
    if not text:
        return text
    for original, preferred in names.items():
        # Whole names only ("Band" isn't changed inside "Bandit"), and never inside the
        # preferred spelling itself, so doing it twice changes nothing ("Band" → "The
        # Band" mustn't become "The The Band").
        pattern = re.compile(rf"(?<!\w){re.escape(original)}(?!\w)")
        pieces = text.split(preferred)
        literal = preferred.replace("\\", "\\\\")  # re.sub's replacement escapes
        text = preferred.join(pattern.sub(literal, piece) for piece in pieces)
    return text


def _preferred(candidate: Candidate, names: dict[str, str]) -> Candidate:
    if not names:
        return candidate
    return replace(
        candidate,
        title=prefer(candidate.title, names) or candidate.title,
        artists=tuple(prefer(a, names) or a for a in candidate.artists),
        album=prefer(candidate.album, names),
    )


def _preferred_album(album: AlbumInfo, names: dict[str, str]) -> AlbumInfo:
    album.title = prefer(album.title, names)
    album.artist = prefer(album.artist, names)
    return album


def set_name(lib: Library, original: str, preferred: str) -> dict[str, str]:
    """Remember the owner's preferred spelling of a name ("JAŸ-Z" → "Jay Z"). Takes
    effect for new songs at once, and for the library with `plan tidy`."""
    original, preferred = original.strip(), preferred.strip()
    if not original or not preferred:
        raise UserError("Give both the spelling to change and the one you prefer.")
    if original == preferred:
        raise UserError("Those are the same spelling.")
    with state.edit(lib.paths.state_file) as st:
        found = st.data.get("names")
        if not isinstance(found, dict):
            found = st.data["names"] = {}
        found[original] = {"name": preferred, "decided_at": _now()}
    return state.names(lib.load_state().data)


def remove_name(lib: Library, original: str) -> dict[str, str]:
    with state.edit(lib.paths.state_file) as st:
        found = st.data.get("names")
        if not isinstance(found, dict) or original not in found:
            raise NotFoundError(f"There's no preferred spelling for {original!r}.")
        del found[original]
    return state.names(lib.load_state().data)


TIDY_FIELDS = ("title", "artist", "album_artist", "album")


def plan_tidy(lib: Library, index: Index) -> fileops.Plan:
    """`musicorg plan tidy`: songs in the library twice keep their best copy, and every
    file gets the owner's preferred names, moving to the folder and name they now give.
    A file whose name has a ` (2)` it no longer needs is renamed too."""
    names = state.names(lib.load_state().data)
    files = _library_files(lib, index)
    by_video: dict[str, list[tuple[dict[str, Any], Path]]] = {}
    for track, path in files:
        if track.get("source_id"):
            by_video.setdefault(str(track["source_id"]), []).append((track, path))
    items_by_rip = _items_by_rip(lib, index)
    ops: list[fileops.PlanOp] = []
    dropped: set[str] = set()
    for group in by_video.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda g: _file_quality(g[1]), reverse=True)
        keep_track, _ = group[0]
        for track, path in group[1:]:
            item = items_by_rip.get(state.normalise_path(Path(track.get("origin_path") or "")))
            dropped.add(track["rel_path"])
            ops.append(
                fileops.PlanOp(
                    action="duplicate",
                    item_id=item["id"] if item else None,
                    item_state=item["state"] if item else None,
                    source=fileops.FileCheck.of(lib, path),
                    params={
                        "musicorg_id": track["musicorg_id"],
                        "rip": track.get("origin_path"),
                        "keep": keep_track["rel_path"],
                        "keep_id": keep_track["musicorg_id"],
                    },
                )  # fmt: skip
            )
    duplicates = len(ops)
    for track, path in files:
        if track["rel_path"] in dropped:
            continue
        current = tags.read_tags(path)
        changes = {
            field: prefer(getattr(current, field), names)
            for field in TIDY_FIELDS
            if isinstance(getattr(current, field), str)
            and prefer(getattr(current, field), names) != getattr(current, field)
        }
        after = {f: changes.get(f, getattr(current, f)) for f in TIDY_FIELDS}
        meta = naming.TrackMeta(
            title=after["title"], artist=after["artist"], album_artist=after["album_artist"],
            album=after["album"], year=current.year if isinstance(current.year, int) else None,
            track=current.track if isinstance(current.track, int) else None,
            compilation=after["album_artist"] == naming.VARIOUS_ARTISTS, ext=path.suffix,
            source_file=path.name,
        )  # fmt: skip
        here = PurePosixPath(track["rel_path"])
        target = _ideal_path(lib, meta, here)
        if not changes:
            if target == here or not _just_a_number_added(here, target):
                continue
            ideal = lib.root / Path(*target.parts)
            if (ideal.exists() or ideal.is_symlink()) and target.as_posix() not in dropped:
                continue  # the plain name belongs to another song: keep the number
        ops.append(
            fileops.PlanOp(
                action="rename",
                source=fileops.FileCheck.of(lib, path),
                target=target.as_posix(),
                params={"musicorg_id": track["musicorg_id"], "changes": changes},
            )
        )
    renames = len(ops) - duplicates
    empty = _empty_folders(lib)
    if empty:
        ops.append(fileops.PlanOp(action="empty_folders", params={"folders": empty}))
    summary = {"operations": len(ops), "duplicates": duplicates, "renames": renames,
               "empty_folders": len(empty), "downloads": 0,
               "est_minutes": math.ceil(len(ops) * 2 / 60), "days": 0, "disk_mb": 0,
               "low_confidence_adopts": 0}  # fmt: skip
    plan = fileops.new_plan("tidy", ops, summary)
    fileops.save_plan(lib, plan)
    return plan


def _just_a_number_added(here: PurePosixPath, ideal: PurePosixPath) -> bool:
    """`here` is the ideal name with a " (2)"-style number the file may no longer need."""
    return here.parent == ideal.parent and here.stem != ideal.stem and (
        here.stem.startswith(ideal.stem + " (") and here.stem.endswith(")")
    )  # fmt: skip


def _file_quality(path: Path) -> tuple[int, int, int, int]:
    """As `_rip_quality`, for a library file: the signs of a YouTube conversion are read
    from the rip it came from (read-only), or from the file itself if that's gone."""
    found = tags.probe(path)
    lossless = 1 if (found.codec or "").lower() in LOSSLESS else 0
    origin = tags.read_tags(path).origin_path
    source = Path(origin) if isinstance(origin, str) and Path(origin).is_file() else path
    original = 0 if scan.youtube_converted(tags.read_extra(source), source.name) else 1
    return lossless, original, int(found.bitrate_kbps or 0), path.stat().st_size


def _items_by_rip(lib: Library, index: Index) -> dict[str, dict[str, Any]]:
    folders = scan.source_folders(lib, index)
    return {
        state.normalise_path(Path(scan.item_path(folders, item))): item
        for item in index.items_in_states(DONE_STATES)
    }


def _empty_folders(lib: Library) -> list[str]:
    """Folders in `Music/` with nothing in them but junk (e.g. left by an earlier move)."""
    found = []
    for folder in sorted(lib.paths.music.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if folder.is_dir() and not folder.is_symlink() and all(
            naming.is_junk(p.name) and p.is_file() for p in folder.rglob("*")
        ):  # fmt: skip
            if not any(PurePosixPath(f) in PurePosixPath(_rel_path(lib, folder)).parents
                       for f in found):  # fmt: skip
                found.append(_rel_path(lib, folder))
    return sorted(found)


def tidy_job(ctx: JobContext) -> Outcome:
    (op,) = _ops(ctx.payload)
    if op.action == "empty_folders":
        removed = 0
        for rel in op.params["folders"]:
            folder = ctx.lib.root / Path(*PurePosixPath(rel).parts)
            if folder.is_dir():
                removed += len(fileops.remove_empty_folders(ctx.lib, folder))
        return Outcome.done(f"Removed {removed} empty folder(s).")
    path = _same_file(ctx.lib, op)
    if path is None:
        return Outcome.needs_review(FILE_CHANGED, "The file has moved or changed since the plan.")
    with open_index(ctx.lib.paths, write=True) as index:
        if op.action == "duplicate":
            return _set_aside_duplicate(ctx, index, op, path)
        return _rename(ctx, index, op, path)


def _set_aside_duplicate(ctx: JobContext, index: Index, op: fileops.PlanOp, path: Path) -> Outcome:
    keep = ctx.lib.root / Path(*PurePosixPath(str(op.params["keep"])).parts)
    if not keep.is_file() or tags.read_tags(keep).musicorg_id != op.params["keep_id"]:
        return Outcome.needs_review(FILE_CHANGED, "The better copy has moved or changed.")
    lrc = path.with_suffix(".lrc")
    if lrc == keep.with_suffix(".lrc"):
        pass  # the two copies share a name ("04 Song.mp3", "04 Song.m4a"), and the lyrics
    elif lrc.is_file() and not keep.with_suffix(".lrc").is_file():
        fileops.move(ctx.batch, lrc, _music_rel(ctx.lib, keep.with_suffix(".lrc")))
    elif lrc.is_file():
        fileops.supersede(ctx.batch, lrc)
    fileops.supersede(ctx.batch, path)
    index.remove_library_tracks([_rel_path(ctx.lib, path)])
    if op.item_id is not None and op.params.get("rip"):
        _mark_replaced(ctx.lib, index, [op], str(op.params["keep_id"]))
    return Outcome.done(f"{path.name} set aside; {keep.name} is the better copy.")


def _rename(ctx: JobContext, index: Index, op: fileops.PlanOp, path: Path) -> Outcome:
    changes = dict(op.params.get("changes") or {})
    if changes:
        fileops.write_tags(ctx.batch, path, tags.TrackTags(**changes))
    assert op.target is not None
    target = PurePosixPath(op.target).relative_to(naming.MUSIC_DIR)
    final = path
    if PurePosixPath(_rel_path(ctx.lib, path)) != PurePosixPath(op.target):
        old_folder = path.parent
        final = fileops.move(ctx.batch, path, Path(*target.parts))
        lrc = path.with_suffix(".lrc")
        if lrc.is_file():
            fileops.move(ctx.batch, lrc, _music_rel(ctx.lib, final.with_suffix(".lrc")))
        cover = old_folder / naming.COVER_NAME
        if (
            cover.is_file()
            and old_folder != final.parent
            and not (final.parent / naming.COVER_NAME).exists()
            and not any(p.suffix.lower() in ADOPT_SUFFIXES for p in old_folder.iterdir())
        ):
            fileops.move(ctx.batch, cover, _music_rel(ctx.lib, final.parent / naming.COVER_NAME))
        fileops.remove_empty_folders(ctx.lib, old_folder)
    index.remove_library_tracks([_rel_path(ctx.lib, path)])
    written = tags.read_tags(final)
    index.put_library_tracks([_track_row(ctx.lib, final, written, tags.probe(final).duration_s)])
    what = ", ".join(f"{k} → {v}" for k, v in changes.items())
    return Outcome.done(f"{final.name}" + (f" ({what})" if what else " (renamed)"))


def _rel_path(lib: Library, path: Path) -> str:
    return PurePosixPath(*path.relative_to(lib.root).parts).as_posix()


def _music_rel(lib: Library, path: Path) -> Path:
    """A library path as `fileops.move` takes it: relative to Music/."""
    return path.relative_to(lib.paths.music)


# ---- a download the owner asked for, and edits by hand (v0.2) -----------------------

EDIT_TEXT = ("title", "artist", "album_artist", "album", "genre")
EDIT_NUMBERS = {"year": (1000, 2100), "track": (1, 999)}
EDIT_FLAGS = ("explicit",)
MAX_LYRICS_CHARS = 100_000


def plan_download(
    lib: Library,
    index: Index,
    video_ids: list[str],
    videos: list[dict[str, Any]] | None = None,
    *,
    known: list[dict[str, Any]] | None = None,
    playlist_id: str | None = None,
) -> fileops.Plan:
    """A plan downloading these from YouTube Music into the library (v0.2): songs (the
    app's "download" button), and `videos`, each `{"video_id", "height", "fps"?}`: a video
    saved whole, its picture at that height (the app's "save video" button).

    `known` are tracks as the engine gave them out a moment ago (Discover's picks, in the
    Candidate shape). One of those isn't looked up on YouTube Music again, so a plan for
    fifty picks is made at once instead of in over a minute. A pick's `genre` (the genre
    it was found under) goes into the plan, and from there into the song's genre tag.

    `playlist_id` is one of the owner's playlists (an imported playlist, v0.3): each
    song joins it as it arrives. A playlist deleted meanwhile is simply not joined.

    There's no rip behind them, so nothing is replaced and the fingerprint gate has
    nothing to compare; every other check on a download applies. What's already in the
    library is left out."""
    if playlist_id is not None and not any(
        found["id"] == playlist_id for found in listening.get(lib)["playlists"]
    ):
        raise NotFoundError("That playlist doesn't exist any more.")
    ops: list[fileops.PlanOp] = []
    skipped: dict[str, int] = {}
    given: dict[str, Candidate] = {}
    genres: dict[str, str] = {}  # the genre a pick was found under (Discover)
    for data in known or []:
        try:
            candidate = Candidate.from_dict(data)
        except (KeyError, TypeError, AttributeError):
            raise UserError("A candidate should be a track as the engine gave it.") from None
        if (
            isinstance(candidate.video_id, str)
            and isinstance(candidate.title, str)
            and candidate.title
            and all(isinstance(name, str) for name in candidate.artists)
        ):
            given[candidate.video_id] = candidate
            genre = data.get("genre")
            if isinstance(genre, str) and 0 < len(genre.strip()) <= MAX_GENRE_CHARS:
                genres[candidate.video_id] = " ".join(genre.split())

    def look_up(video_id: object) -> Candidate | None:
        if not isinstance(video_id, str) or not youtube.VIDEO_ID.fullmatch(video_id):
            raise UserError(f"{video_id!r} isn't a YouTube video id.")
        if index.library_tracks_with_source_id(video_id):
            skipped["already_in_library"] = skipped.get("already_in_library", 0) + 1
            return None
        if video_id in given:
            return given[video_id]
        found = youtube.get_track(video_id)
        if found is None:
            skipped["not_on_youtube_music"] = skipped.get("not_on_youtube_music", 0) + 1
        return found

    for video_id in dict.fromkeys(video_ids):
        found = look_up(video_id)
        if found is not None:
            params = {"video_id": video_id, "candidate": found.to_dict()}
            if video_id in genres:
                params["genre"] = genres[video_id]
            if playlist_id is not None:
                params["playlist_id"] = playlist_id
            ops.append(fileops.PlanOp(action="download", params=params))
    size = sum((op.params["candidate"].get("duration_s") or 0) * BYTES_PER_SECOND for op in ops)
    seen: set[str] = set()
    for wanted in videos or []:
        video_id = wanted.get("video_id") if isinstance(wanted, dict) else None
        height, fps = (wanted.get("height"), wanted.get("fps")) if video_id else (None, None)
        if isinstance(height, bool) or not isinstance(height, int) or height not in VIDEO_KBPS:
            sizes = ", ".join(str(h) for h in VIDEO_KBPS)
            raise UserError(f"A video's height should be one of {sizes}.")
        if fps is not None and (isinstance(fps, bool) or not isinstance(fps, int)):
            raise UserError("A video's fps should be a whole number.")
        if video_id in seen:
            continue
        seen.add(str(video_id))
        found = look_up(video_id)
        if found is None:
            continue
        ops.append(
            fileops.PlanOp(
                action="download_video",
                params={
                    "video_id": video_id,
                    "candidate": found.to_dict(),
                    "height": height,
                    "fps": fps,
                },
            )  # fmt: skip
        )
        size += (found.duration_s or 0) * (VIDEO_KBPS[height] * 125 + BYTES_PER_SECOND)
    summary = {
        "operations": len(ops),
        "downloads": len(ops),
        "videos": sum(op.action == "download_video" for op in ops),
        "est_minutes": estimate_minutes(len(ops), 0),
        "days": download_days(len(ops)),
        "disk_mb": round(size / 1e6, 1),
        "low_confidence_adopts": 0,
        "skipped": skipped,
    }
    plan = fileops.new_plan("download", ops, summary)
    fileops.save_plan(lib, plan)
    return plan


def download_job(ctx: JobContext) -> Outcome:
    (op,) = _ops(ctx.payload)
    if op.action == "download_video":
        return _download_video(ctx, op)
    video_id = str(op.params["video_id"])
    candidate = Candidate.from_dict(op.params["candidate"])
    with open_index(ctx.lib.paths, write=True) as index:
        have = [
            row
            for row in index.library_tracks_with_source_id(video_id)
            if not naming.is_video_path(row["rel_path"])
        ]
        if index.library_tracks_with_source_id(video_id):
            if have:  # it arrived another way meanwhile: the playlist still gets it
                _join_playlist(ctx.lib, op, have[0].get("musicorg_id"))
            return Outcome.done("Already in the library.")
        path, info = ctx.download(video_id)
        problem = _check_download(path, info, candidate)
        if problem is not None:
            return Outcome.needs_review(*problem)
        names = state.names(ctx.lib.load_state().data)
        candidate = _preferred(candidate, names)
        album = _preferred_album(_album(candidate, index), names)
        probe = tags.probe(path)
        extras = _extras(ExtrasQuery(candidate, album, probe.duration_s, (), index))
        new_tags = tags.TrackTags(
            title=candidate.title,
            artist=", ".join(candidate.artists) or None,
            album_artist=album.artist,
            album=album.title,
            year=_year(album.year),
            track=album.track,
            track_total=album.track_total if album.track else None,
            # YouTube Music gives no genre. A song found by Discover takes the genre it
            # was found under; any other takes the one the owner's own songs by the
            # artist have. With neither it has none, and can be given one by hand.
            genre=op.params.get("genre") or browse.artist_genre(index, candidate.artists),
            explicit=candidate.is_explicit,
            lyrics=extras.lyrics,
            cover=extras.cover,
            cover_mime=extras.cover_mime if extras.cover else None,
            schema=tags.SCHEMA_VERSION,
            musicorg_id=tags.new_track_id(),
            source="youtube_music",
            source_id=candidate.video_id,
            source_format=str(info["format_id"]),
            source_bitrate=probe.bitrate_kbps,
            acquired=_now(),
        )
        fileops.write_tags(ctx.batch, path, new_tags)
        meta = naming.TrackMeta(
            title=candidate.title, artist=", ".join(candidate.artists) or None,
            album_artist=album.artist, album=album.title, year=album.year, track=album.track,
            compilation=album.artist == naming.VARIOUS_ARTISTS, ext=path.suffix,
        )  # fmt: skip
        final = fileops.commit(ctx.batch, path, naming.library_path(meta, ctx.lib.root))
        index.put_library_tracks([_track_row(ctx.lib, final, new_tags, probe.duration_s)])
        _write_sidecars(ctx, final, extras)
    _join_playlist(ctx.lib, op, new_tags.musicorg_id)
    return Outcome.done(f"Downloaded as {final.name}.")


def _join_playlist(lib: Library, op: fileops.PlanOp, track_id: object) -> None:
    """A song downloaded for a playlist (an import) goes into it now that it's here."""
    playlist_id = op.params.get("playlist_id")
    if isinstance(playlist_id, str) and isinstance(track_id, str) and track_id:
        if not listening.add_to_playlist(lib, playlist_id, track_id):
            log.info("The playlist %s is gone; the song wasn't added to it.", playlist_id)


def _download_video(ctx: JobContext, op: fileops.PlanOp) -> Outcome:
    """Save a video whole: `Music/Videos/<Artist>/<Title>.mp4`, tagged like a song, with
    the video's own picture as its cover. No lyrics and no folder cover: those belong
    to the song, which is often a different cut."""
    video_id = str(op.params["video_id"])
    candidate = Candidate.from_dict(op.params["candidate"])
    height = int(op.params["height"])
    with open_index(ctx.lib.paths, write=True) as index:
        if index.library_tracks_with_source_id(video_id):
            return Outcome.done("Already in the library.")
        path, info = ctx.download_video(video_id, height, op.params.get("fps"))
        problem = _check_video(path, info, candidate, height)
        if problem is not None:
            return Outcome.needs_review(*problem)
        candidate = _preferred(candidate, state.names(ctx.lib.load_state().data))
        probe = tags.probe(path)
        cover = _video_cover(candidate)
        artist = ", ".join(candidate.artists) or None
        first = candidate.artists[0] if candidate.artists else None
        new_tags = tags.TrackTags(
            title=candidate.title,
            artist=artist,
            album_artist=first,
            genre=browse.artist_genre(index, candidate.artists),
            explicit=candidate.is_explicit,
            cover=cover.data if cover else None,
            cover_mime=cover.mime if cover else None,
            schema=tags.SCHEMA_VERSION,
            musicorg_id=tags.new_track_id(),
            source="youtube_music",
            source_id=candidate.video_id,
            source_format=str(info["format_id"]),
            source_bitrate=probe.bitrate_kbps,
            acquired=_now(),
        )
        fileops.write_tags(ctx.batch, path, new_tags)
        meta = naming.TrackMeta(title=candidate.title, artist=artist, album_artist=first)
        final = fileops.commit(ctx.batch, path, naming.video_path(meta, ctx.lib.root))
        index.put_library_tracks([_track_row(ctx.lib, final, new_tags, probe.duration_s)])
    return Outcome.done(f"Saved the video as {final.name}.")


def _check_video(
    path: Path, info: dict[str, Any], candidate: Candidate, height: int
) -> tuple[str, str] | None:
    """Is this the video we asked for? (review reason, message), or None if it is."""
    delivered = str(info.get("format_id") or "")
    picture, _, sound = delivered.partition("+")
    if not picture or sound != youtube.DOWNLOAD_FORMAT:
        return (
            "video_format_unavailable",
            f"YouTube delivered format {delivered or 'unknown'}, not a picture joined to "
            "sound format 140; nothing is kept.",
        )
    probe = tags.probe(path)  # AudioError: the queue retries
    if (probe.video_codec or "").lower() != "h264" or probe.height != height:
        return ("video_format_unavailable",
                f"The download's picture is {probe.video_codec or 'unknown'} at "
                f"{probe.height or 'an unknown'} lines, not H.264 at {height}.")  # fmt: skip
    if (probe.codec or "").lower() != "aac":
        return ("video_format_unavailable",
                f"The download's audio is {probe.codec or 'unknown'}, not AAC.")  # fmt: skip
    if probe.bitrate_kbps is not None and probe.bitrate_kbps < MIN_BITRATE_KBPS:
        return ("video_format_unavailable",
                f"The download's audio is only {probe.bitrate_kbps} kbps (at least "
                f"{MIN_BITRATE_KBPS} expected).")  # fmt: skip
    expected = candidate.duration_s
    if expected is not None and probe.duration_s is not None:
        if abs(probe.duration_s - expected) > DURATION_TOLERANCE_S:
            return ("duration_mismatch",
                    f"The download is {probe.duration_s:.0f} s long; YouTube Music said "
                    f"{expected} s.")  # fmt: skip
    return None


def _video_cover(candidate: Candidate) -> artwork.Art | None:
    """The video's own picture, as its cover. A video without one is still saved."""
    if not candidate.thumbnail:
        return None
    try:
        return artwork.art_from_url(candidate.thumbnail)
    except (UserError, OSError) as exc:
        log.warning("No cover for the video %s: %s", candidate.video_id, exc)
        return None


def _ideal_path(lib: Library, meta: naming.TrackMeta, here: PurePosixPath) -> PurePosixPath:
    """Where a library file belongs, from the library root: a saved video stays in
    `Music/Videos/`, a song goes by its artist and album."""
    build = naming.video_path if naming.is_video_path(here) else naming.library_path
    return PurePosixPath(naming.MUSIC_DIR, *build(meta, lib.root).parts)


def plan_edit(
    lib: Library,
    index: Index,
    rel_path: str,
    *,
    changes: dict[str, Any] | None = None,
    lyrics_text: str | None = None,
    cover_file: Path | None = None,
) -> fileops.Plan:
    """A plan for the owner's own corrections to one song (v0.2):

    - `changes`: title, artist, album_artist, album, genre (text), year, track (numbers),
      explicit (true or false). None or "" clears a field; a song always keeps a title.
      `explicit` is the owner's to correct: for a rip it was copied from the match on
      YouTube Music, and nothing checks that the owner's own audio isn't the clean edit.
    - `lyrics_text`: the song's lyrics. Timed lyrics (`[mm:ss.xx]words` lines) become its
      `.lrc`, with their words in the tags; anything else is plain lyrics, and a `.lrc`
      that was there is set aside as wrong. "" removes the lyrics. None leaves them.
    - `cover_file`: a picture on this computer. It's read here, once, and never changed;
      the plan carries the prepared cover.

    The file moves to the folder and name its new details give. Like every change, it
    runs through the queue as a journaled batch, so `undo` puts it all back."""
    path = browse.track_path(lib, rel_path)
    current = tags.read_tags(path)
    if not isinstance(current.musicorg_id, str):
        raise UserError("That file isn't one the library manages, so it can't be edited here.")
    cleaned: dict[str, Any] = {}
    for name, value in (changes or {}).items():
        if name in EDIT_TEXT:
            if value is not None and not isinstance(value, str):
                raise UserError(f"{name} should be text.")
            value = " ".join(value.split()) if value else None
            if name == "title" and value is None:
                raise UserError("A song needs a title.")
        elif name in EDIT_NUMBERS:
            low, high = EDIT_NUMBERS[name]
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high
            ):
                raise UserError(f"{name} should be a number from {low} to {high}, or empty.")
        elif name in EDIT_FLAGS:
            if value is not None and not isinstance(value, bool):
                raise UserError(f"{name} should be true or false.")
        else:
            raise UserError(f"{name!r} can't be edited.")
        if value != getattr(current, name):
            cleaned[name] = value
    params: dict[str, Any] = {"musicorg_id": current.musicorg_id, "changes": cleaned}
    if lyrics_text is not None:
        if len(lyrics_text) > MAX_LYRICS_CHARS:
            raise UserError("Those lyrics are too long to save.")
        params["lyrics"] = lyrics_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if cover_file is not None:
        try:
            if cover_file.stat().st_size > youtube.IMAGE_MAX_BYTES:
                raise UserError("That picture is too big to use as a cover.")
            picture = cover_file.read_bytes()
        except OSError as exc:
            raise UserError(f"That picture couldn't be read: {exc.strerror or exc}.") from exc
        try:
            art = artwork.prepare(picture)
        except YouTubeError as exc:
            raise UserError("That file isn't a picture that can be used as a cover.") from exc
        params["cover_b64"] = base64.b64encode(art.data).decode("ascii")
    if not cleaned and "lyrics" not in params and "cover_b64" not in params:
        raise UserError("Nothing would change.")

    here = PurePosixPath(_rel_path(lib, path))
    target = here
    if any(name in cleaned for name in (*TIDY_FIELDS, "year", "track")):
        after = {name: cleaned.get(name, getattr(current, name)) for name in TIDY_FIELDS}
        after.update({n: cleaned[n] if n in cleaned else getattr(current, n)
                      for n in ("year", "track")})  # fmt: skip
        meta = naming.TrackMeta(
            title=_str(after["title"]), artist=_str(after["artist"]),
            album_artist=_str(after["album_artist"]), album=_str(after["album"]),
            year=after["year"] if isinstance(after["year"], int) else None,
            track=after["track"] if isinstance(after["track"], int) else None,
            compilation=after["album_artist"] == naming.VARIOUS_ARTISTS, ext=path.suffix,
            source_file=path.name,
        )  # fmt: skip
        target = _ideal_path(lib, meta, here)
    op = fileops.PlanOp(
        action="edit", source=fileops.FileCheck.of(lib, path), target=target.as_posix(),
        params=params,
    )  # fmt: skip
    summary = {"operations": 1, "downloads": 0, "est_minutes": 1, "days": 0, "disk_mb": 0,
               "low_confidence_adopts": 0, "fields": sorted(cleaned),
               "lyrics": "lyrics" in params, "cover": "cover_b64" in params}  # fmt: skip
    plan = fileops.new_plan("edit", [op], summary)
    fileops.save_plan(lib, plan)
    return plan


def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def edit_job(ctx: JobContext) -> Outcome:
    (op,) = _ops(ctx.payload)
    path = _same_file(ctx.lib, op)
    if path is None:
        return Outcome.needs_review(FILE_CHANGED, "The file has moved or changed since the plan.")
    change = tags.TrackTags(
        **{k: tags.REMOVE if v is None else v for k, v in op.params["changes"].items()}
    )
    text = op.params.get("lyrics")
    synced = lyrics.check_lrc(text) if text else None
    if text is not None:
        change.lyrics = (lyrics.plain_from(synced) if synced else text) or tags.REMOVE
    cover = base64.b64decode(op.params["cover_b64"]) if op.params.get("cover_b64") else None
    if cover is not None:
        change.cover, change.cover_mime = cover, tags.JPEG
    fileops.write_tags(ctx.batch, path, change)

    lrc = path.with_suffix(".lrc")
    if synced:
        fileops.write_sidecar(ctx.batch, path, ".lrc", synced.encode("utf-8"))
    elif text is not None and lrc.is_file():
        fileops.supersede(ctx.batch, lrc)  # the timed lyrics were the wrong ones
    if cover is not None:
        old = path.parent / naming.COVER_NAME
        if old.is_file() and old.read_bytes() != cover:
            fileops.supersede(ctx.batch, old)  # the owner chose this album's cover
        fileops.write_sidecar(ctx.batch, path, naming.COVER_NAME, cover)
    with open_index(ctx.lib.paths, write=True) as index:
        moved = _rename(ctx, index, replace(op, params={**op.params, "changes": {}}), path)
    return Outcome.done(f"Edited: {moved.message}")


# ---- taking a download out of the library (v0.2) ---------------------------------------

MAX_REMOVE = 500
MAX_GENRE_CHARS = 60


def plan_remove(lib: Library, index: Index, rel_paths: list[str]) -> fileops.Plan:
    """A plan sending downloads to the system Trash (the app's "Delete" on Discover →
    Downloads): songs and videos the owner downloaded from YouTube Music and doesn't want.

    Only a download can go this way: a file whose `MUSICORG_SOURCE` is `youtube_music`
    with no rip behind it (no `MUSICORG_MATCH`), which can be downloaded again. A song
    that came from the owner's own rips is refused: it may be the only good copy.

    Each file goes to the Trash with its `.lrc`; the album's `cover.jpg` goes too when no
    other song is left in its folder. The song also leaves the owner's favourites, play
    counts and playlists. Journaled like every change. Undo can't bring a
    file back from the Trash (it says so: restore it by hand), which is why the app asks
    before it does this."""
    if not rel_paths:
        raise UserError("Choose the downloads to delete.")
    if len(rel_paths) > MAX_REMOVE:
        raise UserError(f"That's too many to delete at once (the limit is {MAX_REMOVE}).")
    ops = []
    for rel in dict.fromkeys(rel_paths):
        if not isinstance(rel, str):
            raise UserError("A path should be text.")
        path = browse.track_path(lib, rel)
        found = tags.read_tags(path)
        if not isinstance(found.musicorg_id, str):
            raise UserError(f"{path.name} isn't a file the library manages.")
        if found.source != "youtube_music" or found.match is not None:
            raise UserError(
                f"“{found.title or path.stem}” came from your own files, not from a download, "
                "so it isn't deleted here."
            )
        ops.append(
            fileops.PlanOp(
                action="remove",
                source=fileops.FileCheck.of(lib, path),
                params={
                    "musicorg_id": found.musicorg_id,
                    "title": found.title if isinstance(found.title, str) else path.stem,
                    "artist": found.artist if isinstance(found.artist, str) else None,
                },
            )
        )
    summary = {"operations": len(ops), "downloads": 0, "est_minutes": 1, "days": 0,
               "disk_mb": 0, "low_confidence_adopts": 0}  # fmt: skip
    plan = fileops.new_plan("remove", ops, summary)
    fileops.save_plan(lib, plan)
    return plan


def remove_job(ctx: JobContext) -> Outcome:
    (op,) = _ops(ctx.payload)
    path = _same_file(ctx.lib, op)
    if path is None:
        return Outcome.needs_review(FILE_CHANGED, "The file has moved or changed since the plan.")
    found = tags.read_tags(path)
    if found.source != "youtube_music" or found.match is not None:
        return Outcome.needs_review(FILE_CHANGED, "It isn't a download any more.")
    rel = _rel_path(ctx.lib, path)
    folder = path.parent
    lrc = path.with_suffix(".lrc")
    fileops.trash(ctx.batch, path)
    if lrc.is_file():
        fileops.trash(ctx.batch, lrc)
    # The album's cover goes with its last song; a folder with nothing left goes too.
    cover = folder / naming.COVER_NAME
    others = [
        entry for entry in folder.iterdir() if entry.is_file() and naming.is_audio_name(entry.name)
    ]
    if not others and cover.is_file():
        fileops.trash(ctx.batch, cover)
    fileops.remove_empty_folders(ctx.lib, folder)
    with open_index(ctx.lib.paths, write=True) as index:
        index.remove_library_tracks([rel])
    listening.forget(ctx.lib, [str(op.params["musicorg_id"])])
    return Outcome.done(f"{path.name} is in the Trash.")


# ---- lyrics and covers for the library (step 10) ------------------------------------

WORK_S_PER_LYRICS = 3  # LRCLIB's pace (one request a second), sometimes YouTube Music too
WORK_S_PER_COVER = 1  # most songs share their album's cover, fetched once


@dataclass
class _Official:
    """What's known about a library file's official track, from its rip's match."""

    candidate: dict[str, Any] | None = None
    art_url: str | None = None


def _officials(lib: Library, index: Index) -> dict[str, _Official]:
    """Rip path (normalised) → its match's candidate, and the owner's `art_url`, for
    every rip that's in the library."""
    decisions = state.decisions(lib.load_state().data)
    folders = scan.source_folders(lib, index)
    found: dict[str, _Official] = {}
    for item in index.items_in_states(DONE_STATES):
        chosen = _chosen(index, {**item, "state": "matched_user"}, decisions)
        art_url = decisions.get(item["id"], {}).get("art_url")
        found[state.normalise_path(Path(scan.item_path(folders, item)))] = _Official(
            chosen["payload"] if chosen else None,
            art_url if isinstance(art_url, str) and art_url else None,
        )
    return found


def _library_files(lib: Library, index: Index) -> list[tuple[dict[str, Any], Path]]:
    found = []
    for track in index.library_tracks():
        path = lib.root / Path(*PurePosixPath(track["rel_path"]).parts)
        if path.is_file() and track.get("musicorg_id"):
            found.append((track, path))
    return found


def _official_for(
    officials: dict[str, _Official], track: dict[str, Any]
) -> tuple[_Official, dict[str, Any] | None]:
    """The track's official match, if it has one and it's the track's own videoId."""
    origin = track.get("origin_path")
    official = officials.get(state.normalise_path(Path(origin))) if origin else None
    official = official or _Official()
    candidate = official.candidate
    if candidate is not None and candidate.get("video_id") != track.get("source_id"):
        candidate = None  # the tags say another track; don't mix them up
    return official, candidate


def plan_lyrics(lib: Library, index: Index, *, missing: bool = False) -> fileops.Plan:
    """`musicorg lyrics [--missing]`: a plan fetching lyrics for library files (with
    `missing`, only files with neither embedded lyrics nor a `.lrc`)."""
    officials = _officials(lib, index)
    ops, skipped = [], {}
    for track, path in _library_files(lib, index):
        if naming.is_video_path(track["rel_path"]):
            # A song's lyrics are timed to the song; its video is often a different cut.
            skipped["video"] = skipped.get("video", 0) + 1
            continue
        current = tags.read_tags(path)
        if missing and (current.lyrics or path.with_suffix(".lrc").exists()):
            skipped["has_lyrics"] = skipped.get("has_lyrics", 0) + 1
            continue
        _, candidate = _official_for(officials, track)
        video_id = track.get("source_id") if candidate is not None or (
            track.get("source") == "youtube_music") else None  # fmt: skip
        ops.append(
            fileops.PlanOp(
                action="lyrics",
                source=fileops.FileCheck.of(lib, path),
                params={
                    "musicorg_id": track["musicorg_id"],
                    "video_id": video_id,
                    "official_s": (candidate or {}).get("duration_s"),
                },
            )  # fmt: skip
        )
    summary = {"operations": len(ops), "downloads": 0, "missing": missing, "skipped": skipped,
               "est_minutes": math.ceil(len(ops) * WORK_S_PER_LYRICS / 60), "days": 0,
               "disk_mb": 0, "low_confidence_adopts": 0}  # fmt: skip
    plan = fileops.new_plan("lyrics", ops, summary)
    fileops.save_plan(lib, plan)
    return plan


def plan_artwork(lib: Library, index: Index, *, missing: bool = False) -> fileops.Plan:
    """`musicorg artwork [--missing]`: a plan giving library files their cover: the
    album's official cover, or the owner's `art_url`. With `missing`, only files with no
    cover or a cover that isn't square (a converter's video frame). A file with neither an
    official match nor an `art_url` gets no automatic cover."""
    officials = _officials(lib, index)
    ops, skipped = [], {}
    albums: set[str] = set()
    for track, path in _library_files(lib, index):
        if naming.is_video_path(track["rel_path"]):
            skipped["video"] = skipped.get("video", 0) + 1  # it keeps its own picture
            continue
        official, candidate = _official_for(officials, track)
        browse_id = (candidate or {}).get("album_browse_id")
        if not browse_id and not official.art_url:
            skipped["no_official_cover"] = skipped.get("no_official_cover", 0) + 1
            continue
        if missing and artwork.is_square(tags.read_tags(path).cover):
            skipped["has_cover"] = skipped.get("has_cover", 0) + 1
            continue
        albums.add(browse_id or official.art_url or "")
        ops.append(
            fileops.PlanOp(
                action="artwork",
                source=fileops.FileCheck.of(lib, path),
                params={
                    "musicorg_id": track["musicorg_id"],
                    "browse_id": browse_id,
                    "art_url": None if browse_id else official.art_url,
                },
            )  # fmt: skip
        )
    summary = {"operations": len(ops), "albums": len(albums), "downloads": 0,
               "missing": missing, "skipped": skipped,
               "est_minutes": math.ceil((len(albums) * 3 + len(ops) * WORK_S_PER_COVER) / 60),
               "days": 0, "disk_mb": 0, "low_confidence_adopts": 0}  # fmt: skip
    plan = fileops.new_plan("artwork", ops, summary)
    fileops.save_plan(lib, plan)
    return plan


def _same_file(lib: Library, op: fileops.PlanOp) -> Path | None:
    """The library file the operation is about, if it's still there with the same
    MUSICORG_ID. Its bytes may have changed since the plan (a lyrics batch before an
    artwork one): these jobs only add their own fields, in a verified write, so the
    file's identity is what's checked, not its size or time."""
    assert op.source is not None
    path = lib.root / Path(*PurePosixPath(op.source.path).parts)
    if not path.is_file() or tags.read_tags(path).musicorg_id != op.params["musicorg_id"]:
        return None
    return path


def lyrics_job(ctx: JobContext) -> Outcome:
    (op,) = _ops(ctx.payload)
    path = _same_file(ctx.lib, op)
    if path is None:
        return Outcome.needs_review(FILE_CHANGED, "The file has moved or changed since the plan.")
    current = tags.read_tags(path)
    duration = tags.probe(path).duration_s
    query = lyrics.Query(
        title=str(current.title or path.stem), artist=str(current.artist or ""),
        album=current.album if isinstance(current.album, str) else None,
        duration_s=duration, video_id=op.params.get("video_id"),
        official_s=float(op.params["official_s"]) if op.params.get("official_s") else None,
        versions=tuple(current.version) if isinstance(current.version, list) else (),
    )  # fmt: skip
    with open_index(ctx.lib.paths, write=True) as index:
        found = lyrics.find(query, cache=index)
    if found.plain and found.plain != current.lyrics:
        fileops.write_tags(ctx.batch, path, tags.TrackTags(lyrics=found.plain))
    if found.synced:
        fileops.write_sidecar(ctx.batch, path, ".lrc", found.synced.encode("utf-8"))
    detail = f" from {found.source}" if found.source else ""
    note = f" ({found.note})" if found.note else ""
    return Outcome.done(f"{found.status}{detail}{note}")


def artwork_job(ctx: JobContext) -> Outcome:
    (op,) = _ops(ctx.payload)
    path = _same_file(ctx.lib, op)
    if path is None:
        return Outcome.needs_review(FILE_CHANGED, "The file has moved or changed since the plan.")
    with open_index(ctx.lib.paths, write=True) as index:
        if op.params.get("browse_id"):
            art = artwork.album_art(str(op.params["browse_id"]), cache=index)
        else:
            art = artwork.art_from_url(str(op.params["art_url"]))
    if art is None:
        return Outcome.done("none: no cover could be fetched")
    fileops.write_tags(ctx.batch, path, tags.TrackTags(cover=art.data, cover_mime=art.mime))
    fileops.write_sidecar(ctx.batch, path, naming.COVER_NAME, art.data)
    problems = f" ({'; '.join(art.problems)})" if art.problems else ""
    return Outcome.done(f"cover {art.width}×{art.height}{problems}")


# ---- undo ----------------------------------------------------------------------------


def undo(lib: Library, batch_id: str, *, dry_run: bool = False) -> fileops.UndoResult:
    """`musicorg undo`: the files first (`fileops.undo`, which cancels the batch's queued
    jobs and refuses while one runs), then the rips the batch replaced or adopted go back
    to the state the plan found them in, and their links in state.json are removed."""
    result = fileops.undo(lib, batch_id, dry_run=dry_run, jobs=queue.BatchJobs(lib))
    if dry_run:
        return result
    with open_queue(lib.paths, write=False) as store:
        jobs = store.jobs(batch_id=batch_id)
    ops = [op for job in jobs if job["payload"].get("ops") for op in _ops(job["payload"])]
    record = fileops.read_journal(lib).get(batch_id)
    touched = _touched_paths(record)
    with open_index(lib.paths, write=True) as index:
        restored: list[fileops.PlanOp] = []
        for op in ops:
            item = index.item(str(op.item_id)) if op.item_id else None
            if item is not None and item["state"] in DONE_STATES and op.item_state:
                index.set_state(str(op.item_id), op.item_state, [])
                restored.append(op)
        _refresh_tracks(lib, index, touched)
    rips = {
        state.normalise_path(Path(op.params["rip"]))
        for op in restored
        if op.action in LINKING_ACTIONS and op.params.get("rip")
    }
    if rips:
        with state.edit(lib.paths.state_file) as st:
            links = st.data.get("superseded")
            if isinstance(links, dict):
                for rip in rips:
                    links.pop(rip, None)
    log.info("Undo of %s: %d rips back to their earlier state", batch_id, len(restored))
    return result


LINKING_ACTIONS = frozenset({"replace", "duplicate", "adopt_duplicate"})


def _touched_paths(record: fileops.BatchRecord | None) -> set[str]:
    """Every audio path in `Music/` an undone batch's operations named."""
    found: set[str] = set()
    for op in record.ops if record else []:
        for value in (op.result_path, op.intent.get("src"), op.intent.get("dst"),
                      op.intent.get("path")):  # fmt: skip
            if isinstance(value, str) and value.startswith(naming.MUSIC_DIR + "/"):
                if Path(value).suffix.lower() in ADOPT_SUFFIXES or naming.is_video_path(value):
                    found.add(value)
    return found


def _refresh_tracks(lib: Library, index: Index, rel_paths: set[str]) -> None:
    """Make the index's library tracks match these files again after an undo: rows for
    files that are back, none for files that went."""
    gone, back = [], []
    for rel in rel_paths:
        path = lib.root / Path(*PurePosixPath(rel).parts)
        if path.is_file():
            written = tags.read_tags(path)
            if isinstance(written.musicorg_id, str):
                back.append(_track_row(lib, path, written, tags.probe(path).duration_s))
                continue
        gone.append(rel)
    index.remove_library_tracks(gone)
    index.put_library_tracks(back)


# ---- calibration ---------------------------------------------------------------------


def calibration_pairs(lib: Library, index: Index, out: Path | None = None) -> Path:
    """`Reports/calibration-pairs.csv` from the notes `--stage-only` left in
    `_Staging/calibration/`: one row per rip and download, with `same` left empty for the
    owner to fill in after listening (scripts/calibrate_fp.py reads it)."""
    rows: list[list[str]] = []
    for note_path in sorted(lib.paths.calibration.glob("*/*.json")):
        try:
            note = json.loads(note_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Skipped %s: %s", note_path, exc)
            continue
        download = note_path.with_name(note_path.name.removesuffix(".json"))
        for c in note.get("comparisons", []):
            ber = c.get("ber")
            shown_ber = f"{ber:.4f}" if isinstance(ber, float) else ""
            rows.append([str(c.get("rip", "")), str(download), "", str(c.get("verdict", "")),
                         shown_ber, str(c.get("why", ""))])  # fmt: skip
    if not rows:
        raise UserError(
            "There are no calibration downloads yet. Run `musicorg plan replace --only auto "
            "--limit 25 --stage-only`, apply it, and `musicorg queue run` first."
        )
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["a_path", "b_path", "same", "verdict", "ber", "why"])
    writer.writerows(rows)
    folder = out if out is not None else lib.paths.reports
    return fileops.write_export(
        lib,
        folder / "calibration-pairs.csv",
        buffer.getvalue().encode("utf-8"),
        sources=scan.source_folders(lib, index).values(),
    )


# ---- helpers -------------------------------------------------------------------------


def _year(value: str | None) -> int | None:
    if value and value[:4].isdigit():
        return int(value[:4])
    return None


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def describe(plan: fileops.Plan) -> list[str]:
    """One line per operation, for `plan show`."""
    lines = []
    for op in plan.operations:
        rip = Path(str(op.params.get("rip", ""))).name
        if op.action == "replace":
            c = op.params["candidate"]
            artists = ", ".join(c.get("artists") or [])
            score = float(op.params["score"])
            lines.append(f"{op.op_id:>5}  replace  {rip}  ←  {artists} – {c.get('title', '')} "
                         f"({op.params['video_id']}, score {score:.2f})")  # fmt: skip
        elif op.action == "adopt_details":
            c = op.params["candidate"]
            artists = ", ".join(c.get("artists") or [])
            lines.append(f"{op.op_id:>5}  adopt    {rip}  +  official details: {artists} – "
                         f"{c.get('title', '')} ({op.params['video_id']})")  # fmt: skip
        elif op.action == "adopt":
            unsure = "" if op.params.get("trusted") else "  [keeps the rip's own names]"
            lines.append(f"{op.op_id:>5}  adopt    {rip}  →  {op.target}{unsure}")
        elif op.action == "download":
            c = op.params["candidate"]
            artists = ", ".join(c.get("artists") or [])
            lines.append(f"{op.op_id:>5}  download {artists} – {c.get('title', '')} "
                         f"({op.params['video_id']})")  # fmt: skip
        elif op.action == "download_video":
            c = op.params["candidate"]
            artists = ", ".join(c.get("artists") or [])
            lines.append(f"{op.op_id:>5}  video    {artists} – {c.get('title', '')} "
                         f"({op.params['video_id']}, {op.params['height']}p)")  # fmt: skip
        elif op.action == "edit":
            assert op.source is not None
            what = [f"{k}: {v if v is not None else '(cleared)'}"
                    for k, v in op.params["changes"].items()]  # fmt: skip
            what += ["lyrics"] if "lyrics" in op.params else []
            what += ["cover"] if "cover_b64" in op.params else []
            lines.append(f"{op.op_id:>5}  edit     {op.source.path}  ({', '.join(what)})")
        elif op.action == "remove":
            assert op.source is not None
            lines.append(f"{op.op_id:>5}  delete   {op.source.path}  (to the Trash)")
        elif op.action == "adopt_unconfirmed":
            lines.append(f"{op.op_id:>5}  adopt    {rip}  →  {op.target}  [unconfirmed: stays "
                         "in review]")  # fmt: skip
        elif op.action == "duplicate":
            assert op.source is not None
            lines.append(f"{op.op_id:>5}  set aside {op.source.path}  (a duplicate of "
                         f"{op.params['keep']})")  # fmt: skip
        elif op.action == "rename":
            assert op.source is not None
            changes = ", ".join(f"{k}: {v}" for k, v in (op.params.get("changes") or {}).items())
            lines.append(f"{op.op_id:>5}  rename   {op.source.path}  →  {op.target}"
                         + (f"  ({changes})" if changes else ""))  # fmt: skip
        elif op.action == "empty_folders":
            for rel in op.params["folders"]:
                lines.append(f"{op.op_id:>5}  remove   {rel}/  (an empty folder)")
        elif op.action == "adopt_duplicate":
            lines.append(f"{op.op_id:>5}  link     {rip}  (a duplicate; the best copy is kept)")
        elif op.action in ("lyrics", "artwork"):
            assert op.source is not None
            where = op.params.get("browse_id") or op.params.get("art_url") or ""
            what = f"  (cover from {where})" if op.action == "artwork" else ""
            lines.append(f"{op.op_id:>5}  {op.action:<8} {op.source.path}{what}")
        else:
            lines.append(f"{op.op_id:>5}  skip     {rip}  (format not adopted in v0.1)")
    return lines


queue.register("replace", replace_job, network=True)
queue.register("adopt", adopt_job, network=False)
# Lyrics and covers download no audio: they're paced by their own limiters (LRCLIB's one
# request a second, YouTube's shared limiter), not the download pace or the daily cap.
queue.register("lyrics", lyrics_job, network=False)
queue.register("artwork", artwork_job, network=False)
queue.register("tidy", tidy_job, network=False)
queue.register("download", download_job, network=True)
queue.register("edit", edit_job, network=False)
queue.register("remove", remove_job, network=False)
