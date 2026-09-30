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
  6. lyrics and artwork: step 10's hook (`EXTRAS`), none until then
  7. **one verified tag write** on the staged file, then commit to `naming.library_path`
  8. sidecars under the committed name; the rips marked `superseded`, and linked to the
     new MUSICORG_ID in state.json
- **`--stage-only`** (calibration) stops after the gate: the download goes to
  `_Staging/calibration/<batch_id>/` with a note of what it was compared with, and no
  state changes. `calibration_pairs` turns the notes into a pairs.csv to fill in.
- **An adopt job** copies one rip into `_Staging/`, tags the copy, and commits it. Only
  MP3, M4A, FLAC, Ogg and Opus are adopted; any other format → `unsupported_format`.
- **`undo`** wraps `fileops.undo`: after the files are back, the batch's `superseded` and
  `adopted` rips return to the state the plan found them in, and their links go.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from musicorg import fileops, fingerprint, naming, queue, scan, state, tags, youtube
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
ADOPT_SUFFIXES = frozenset({".mp3", ".m4a", ".flac", ".ogg", ".opus"})
TRUSTED_PARSE = 0.8  # below this, an adopt keeps the rip's own title and artist
MIN_BITRATE_KBPS = 100
DURATION_TOLERANCE_S = 2
BYTES_PER_SECOND = 16_000  # format 140 is about 128 kbps
WORK_S_PER_DOWNLOAD = 10  # download, check, fingerprint, tag: a rough figure for estimates
WORK_S_PER_ADOPT = 2
DONE_STATES = ("superseded", "adopted")
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


ExtrasHook = Callable[[Candidate, "AlbumInfo"], Extras | None]
EXTRAS: list[ExtrasHook] = []  # step 10 appends its lyrics and artwork functions


def _extras(candidate: Candidate, album: AlbumInfo) -> Extras:
    """Everything the hooks give. A hook that fails only costs its extra: missing lyrics
    never fail a job."""
    found = Extras()
    for hook in EXTRAS:
        try:
            got = hook(candidate, album)
        except (UserError, OSError) as exc:
            log.warning("No extras for %s from %s: %s", candidate.video_id, hook, exc)
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
    config: Config | None = None,
) -> fileops.Plan:
    """A dry-run plan copying `only_copy` rips (and with `include_not_found`, every
    `not_found` one) into `Music/`. The summary counts adopts whose names are unsure
    (parsed with confidence under 0.8 and no fixes from the owner): those keep the rip's
    own title and artist tags."""
    states = ("only_copy", "not_found") if include_not_found else ("only_copy",)
    decisions = state.decisions(lib.load_state().data)
    folders = scan.source_folders(lib, index)
    busy = _items_in_open_jobs(lib)
    skipped: dict[str, int] = {}
    planned: set[str] = set()
    ops: list[fileops.PlanOp] = []
    low_confidence = unsupported = size = 0
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
        if not trusted:
            low_confidence += 1
        names = _adopt_names(item, fixes, trusted, rip)
        meta = naming.TrackMeta(
            title=names["title"], artist=names["artist"], album_artist=names.get("album_artist"),
            album=names.get("album"), year=names.get("year"), track=names.get("track"),
            ext=suffix, source_file=rip.name,
        )  # fmt: skip
        target = _free_target(lib, naming.library_path(meta, lib.root), planned)
        planned.add(_fold(target))
        rel_target = PurePosixPath(naming.MUSIC_DIR, *target.parts).as_posix()
        size += source.size
        ops.append(
            fileops.PlanOp(
                action="adopt",
                item_id=item["id"],
                item_state=item["state"],
                source=source,
                target=rel_target,
                target_folder=fileops.FolderCheck.of(lib, lib.paths.music / target.parent),
                params={"rip": str(rip), "names": names, "fixes": fixes, "trusted": trusted},
            )
        )
    adopts = len(ops) - unsupported
    summary = {
        "operations": len(ops),
        "downloads": 0,
        "adopts": adopts,
        "unsupported_format": unsupported,
        "est_minutes": estimate_minutes(0, adopts, config),
        "days": 0,
        "disk_mb": round(size / 1e6, 1),
        "low_confidence_adopts": low_confidence,
        "include_not_found": include_not_found,
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
    if plan.kind not in ("replace", "adopt"):
        raise UserError(f"Plan {plan_id} is a {plan.kind} plan; step 10 runs those.")
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
    """One job per video for a replace plan; one per rip for an adopt plan."""
    if plan.kind == "adopt":
        return [
            {"batch_id": batch_id, "plan_id": plan.plan_id, "kind": "adopt",
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

        album = _album(candidate)
        extras = _extras(candidate, album)
        probe = tags.probe(path)
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


def _album(candidate: Candidate) -> AlbumInfo:
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
        album = youtube.get_album(found.album_browse_id)
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

        rip = Path(op.params["rip"])
        staged = fileops.stage_copy(ctx.batch, rip)
        probe = tags.probe(staged)
        current = tags.read_tags(staged)
        new_tags = _adopt_tags(op, current, probe, rip)
        fileops.write_tags(ctx.batch, staged, new_tags)
        assert op.target is not None
        rel = PurePosixPath(op.target).relative_to(naming.MUSIC_DIR)
        final = fileops.commit(ctx.batch, staged, Path(*rel.parts))
        written = tags.merge(current, new_tags)
        index.put_library_tracks([_track_row(ctx.lib, final, written, probe.duration_s)])
        index.set_state(item_id, "adopted", [])
    return Outcome.done(f"Copied in as {final.name}.")


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
        match="manual" if fixes else None,
    )
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
    added = [op.result_path for op in (record.ops if record else []) if op.op == "commit"]
    with open_index(lib.paths, write=True) as index:
        restored: list[fileops.PlanOp] = []
        for op in ops:
            item = index.item(str(op.item_id))
            if item is not None and item["state"] in DONE_STATES and op.item_state:
                index.set_state(str(op.item_id), op.item_state, [])
                restored.append(op)
        gone = [p for p in added if p and not (lib.root / Path(*PurePosixPath(p).parts)).exists()]
        index.remove_library_tracks(gone)
    rips = {
        state.normalise_path(Path(op.params["rip"])) for op in restored if op.action == "replace"
    }
    if rips:
        with state.edit(lib.paths.state_file) as st:
            links = st.data.get("superseded")
            if isinstance(links, dict):
                for rip in rips:
                    links.pop(rip, None)
    log.info("Undo of %s: %d rips back to their earlier state", batch_id, len(restored))
    return result


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
        elif op.action == "adopt":
            unsure = "" if op.params.get("trusted") else "  [keeps the rip's own names]"
            lines.append(f"{op.op_id:>5}  adopt    {rip}  →  {op.target}{unsure}")
        else:
            lines.append(f"{op.op_id:>5}  skip     {rip}  (format not adopted in v0.1)")
    return lines


queue.register("replace", replace_job, network=True)
queue.register("adopt", adopt_job, network=False)
