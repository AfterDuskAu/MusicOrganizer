"""Registered sources, and the read-only scan of them (step 05).

Sources are the owner's existing rip folders. They are read, never written: files are
only ever opened for reading, and nothing in a source is created, renamed, retagged,
moved or deleted (CLAUDE.md rule 2).

- `add_source`, `remove_source`, `list_sources`: kept in state.json, mirrored in the
  index.
- `scan(lib, index)`: walks each source (without following folder links, skipping hidden
  files, junk and any library inside it) and indexes every audio file: probe, tags, the
  best parse of its name and tags, and `sha1_head`. Incremental: a file whose size and
  modification time haven't changed isn't read again.
- `parse_again(item)`: an item's names parsed again from what the scan stored, for
  what a newer parser adds (the words a rip names its version in).
- `scan_library` and `rebuild`: `musicorg index rebuild` (contract section 5).
"""

from __future__ import annotations

import logging
import os
import re
import stat
import sys
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from musicorg import fileops, naming, normalize, state, tags
from musicorg.errors import AudioError, NotFoundError, UserError
from musicorg.index import Index, item_id
from musicorg.library import Library

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = frozenset({".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus", ".webm"})
# Indexed and replaceable, but not adopted in v0.1 (contract section 3).
NOT_ADOPTABLE_EXTENSIONS = frozenset({".webm", ".aac", ".wav"})

# docs/ENGINE_API.md → Enums: item flags.
FLAG_NOT_ADOPTABLE = "not_adoptable"
FLAG_SUSPECT_UPSCALE = "suspect_upscale"
FLAG_UNREADABLE = "unreadable"

# The state an owner's decision puts an item in (docs/ENGINE_API.md: RPC decision).
DECISION_STATES = {
    "accept": "matched_user",
    "candidate": "matched_user",
    "url": "matched_user",
    "only_copy": "only_copy",
    "skip": "skipped",
}

SUSPECT_UPSCALE_KBPS = 256
_YOUTUBE_SIGNS = re.compile(
    r"youtube|youtu\.be|y2mate|y2meta|x2mate|yt5s|ytmp3|savefrom|mp3juices|converter|"
    r"onlinevideo",
    re.IGNORECASE,
)
_BATCH = 200  # items written to the index at a time
_WORKERS = min(4, os.cpu_count() or 1)

Progress = Callable[[int, int, str], None]


# ---- sources ---------------------------------------------------------------------------


def add_source(lib: Library, index: Index, path: Path) -> dict[str, Any]:
    """Register a folder of rips, read-only. Refuses a folder inside the library, one
    that contains the library, and one overlapping another source."""
    given = Path(path).expanduser()
    if not given.exists():
        raise UserError(
            f"The folder {given} doesn't exist. If it's on an external drive, check that the "
            "drive is connected."
        )
    if not given.is_dir():
        raise UserError(f"{given} is a file, not a folder. Choose the folder that holds your rips.")
    real = given.resolve()
    root = lib.root
    if naming.is_within(real, root):
        raise UserError(
            f"{real} is inside the library ({root}). A source is a folder of your own rips, "
            "outside the library."
        )
    if naming.is_within(root, real):
        raise UserError(
            f"{real} contains the library ({root}). Music Organizer never changes a source, "
            "so the library can't be inside one. Choose the folder that holds only your rips, "
            "or move the library somewhere else."
        )
    source_id = state.source_id(real)
    added_at = _now()
    with state.edit(lib.paths.state_file) as st:
        sources = st.data.setdefault("sources", {})
        if source_id in sources:
            raise UserError(f"{real} is already a source ({source_id}).")
        for other_id, other in state.sources(st.data).items():
            other_path = Path(str(other.get("path", "")))
            if naming.is_within(real, other_path) or naming.is_within(other_path, real):
                raise UserError(
                    f"{real} overlaps the source {other_id} ({other_path}). Sources can't be "
                    "inside one another; register just one of them."
                )
        sources[source_id] = {"path": str(real), "added_at": added_at}
    index.put_source(source_id, str(real), added_at)
    log.info("Added source %s: %s", source_id, real)
    return {"id": source_id, "path": str(real), "added_at": added_at}


def remove_source(lib: Library, index: Index, source_id: str) -> dict[str, Any]:
    """Forget a source and its items in the index. Its files are never touched, and the
    owner's decisions stay in state.json, so adding it again brings them back."""
    with state.edit(lib.paths.state_file) as st:
        sources = st.data.get("sources")
        if not isinstance(sources, dict) or source_id not in sources:
            raise NotFoundError(
                f"There's no source {source_id}. `musicorg sources list` shows them."
            )
        removed = sources.pop(source_id)
    items = index.remove_source(source_id)
    log.info("Removed source %s (%d items forgotten)", source_id, items)
    return {"id": source_id, "path": removed.get("path"), "items_forgotten": items}


def list_sources(lib: Library, index: Index) -> list[dict[str, Any]]:
    """Sources from state.json, with item counts and the last scan from the index."""
    indexed = index.sources()
    result = []
    for source_id, source in state.sources(lib.load_state().data).items():
        row = indexed.get(source_id, {})
        path = str(source.get("path", ""))
        result.append(
            {
                "id": source_id,
                "path": path,
                "added_at": source.get("added_at"),
                "scanned_at": row.get("scanned_at"),
                "items": row.get("items", 0),
                "available": Path(path).is_dir(),
            }
        )
    return result


def source_folders(lib: Library, index: Index) -> dict[str, Path]:
    """Each source's folder by id: state.json's record, with the index's mirror for any
    it lacks (e.g. in tests that fill the index directly)."""
    found = {k: Path(v["path"]) for k, v in index.sources().items() if v.get("path")}
    for source_id, source in state.sources(lib.load_state().data).items():
        if isinstance(source.get("path"), str):
            found[source_id] = Path(source["path"])
    return found


def item_path(folders: dict[str, Path], item: dict[str, Any]) -> str:
    """An item's full path, as reports and the review spreadsheet show it."""
    folder = folders.get(item["source_id"])
    return str(folder / item["rel_path"]) if folder else item["rel_path"]


def _sync_sources(lib: Library, index: Index) -> dict[str, dict[str, Any]]:
    """Make the index's sources match state.json, which is the record."""
    wanted = state.sources(lib.load_state().data)
    for source_id in set(index.sources()) - set(wanted):
        index.remove_source(source_id)
    for source_id, source in wanted.items():
        index.put_source(source_id, str(source.get("path", "")), str(source.get("added_at", "")))
    return wanted


# ---- scanning --------------------------------------------------------------------------


@dataclass
class ScanResult:
    sources: list[str] = field(default_factory=list)
    files: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    gone: int = 0
    unreadable: int = 0  # indexed, but ffprobe couldn't read the audio
    skipped_links: int = 0
    skipped_libraries: list[str] = field(default_factory=list)
    unreadable_folders: list[str] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Walk:
    skip: frozenset[str]
    links: int = 0
    libraries: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


@dataclass
class _Todo:
    source_id: str
    root: Path
    rel: str
    path: Path
    info: os.stat_result
    known: bool


def scan(
    lib: Library,
    index: Index,
    *,
    source_ids: list[str] | None = None,
    progress: Progress | None = None,
) -> ScanResult:
    """Index the audio files in the sources (all of them, or those named). Only files
    that are new or changed are read. Items whose files have gone are dropped, unless
    their folder couldn't be read or their source isn't connected."""
    started = time.monotonic()
    sources = _sync_sources(lib, index)
    if not sources:
        raise UserError(
            "No sources yet. Add the folder that holds your rips with `musicorg sources add "
            "<folder>`."
        )
    chosen = source_ids or list(sources)
    unknown = [s for s in chosen if s not in sources]
    if unknown:
        raise NotFoundError(
            f"There's no source {', '.join(unknown)}. `musicorg sources list` shows them."
        )

    result = ScanResult(sources=chosen)
    skip = frozenset(_key(p) for p in (lib.root,))
    todo: list[_Todo] = []
    for source_id in chosen:
        root = Path(str(sources[source_id].get("path", "")))
        if not root.is_dir():
            log.warning("The source %s (%s) isn't there; is its drive connected?", source_id, root)
            result.missing_sources.append(source_id)
            continue
        known = index.known_files(source_id)
        walk = _Walk(skip=skip)
        seen: set[str] = set()
        for rel, path, info in _walk(root, walk):
            seen.add(rel)
            result.files += 1
            old = known.get(rel)
            if old is not None and (old[1], old[2]) == (info.st_size, info.st_mtime_ns):
                result.unchanged += 1
                continue
            todo.append(_Todo(source_id, root, rel, path, info, known=old is not None))
        result.skipped_links += walk.links
        result.skipped_libraries += walk.libraries
        result.unreadable_folders += walk.unreadable
        gone = [
            ids[0]
            for rel, ids in known.items()
            if rel not in seen and not _under_any(rel, walk.unreadable, root)
        ]
        result.gone += index.delete_items(gone)

    derive = _state_deriver(lib, index)
    batch: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        for done, (job, item) in enumerate(zip(todo, pool.map(_analyse, todo), strict=True), 1):
            item["state"] = derive(item["id"], job.root / job.rel) or "new"
            batch.append(item)
            result.new += not job.known
            result.changed += job.known
            result.unreadable += FLAG_UNREADABLE in item["flags_json"]
            if len(batch) >= _BATCH:
                index.put_items(batch)
                batch = []
            if progress is not None:
                progress(done, len(todo), job.rel)
    index.put_items(batch)
    when = _now()
    for source_id in chosen:
        if source_id not in result.missing_sources:
            index.mark_scanned(source_id, when)
    result.seconds = round(time.monotonic() - started, 1)
    log.info("Scan: %s", result.to_dict())
    return result


def _walk(
    root: Path, walk: _Walk, *, videos: bool = False
) -> Iterator[tuple[str, Path, os.stat_result]]:
    """Audio files under `root`: (path relative to root with `/`, path, stat). Folder and
    file links are skipped, never followed, and so are hidden files and folders, junk
    names, and any library (a folder holding `.musicorg`).

    With `videos` (the library's own Music/ folder), the saved videos in `Videos/` are
    found too. In a source folder an .mp4 is never taken for a rip."""
    folders = [root]
    while folders:
        folder = folders.pop()
        try:
            with os.scandir(folder) as found:
                entries = sorted(found, key=lambda e: e.name)
        except OSError as exc:
            rel = _rel(root, Path(folder))
            walk.unreadable.append(rel)
            log.warning("Couldn't read the folder %s: %s", folder, _why(exc))
            continue
        for entry in entries:
            name = entry.name
            if name.startswith(".") or naming.is_junk(name):
                continue
            try:
                if entry.is_symlink() or entry.is_junction():
                    walk.links += 1
                    continue
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                log.warning("Skipped %s: %s", entry.path, _why(exc))
                continue
            if _hidden(info):
                continue
            path = Path(entry.path)
            if stat.S_ISDIR(info.st_mode):
                if _key(path) in walk.skip or os.path.isdir(path / naming.ENGINE_DIR):
                    walk.libraries.append(str(path))
                    continue
                folders.append(path)
            elif stat.S_ISREG(info.st_mode) and (
                Path(name).suffix.lower() in AUDIO_EXTENSIONS
                or (videos and naming.is_video_path(_rel(root, path)))
            ):
                yield _rel(root, path), path, info


def _analyse(job: _Todo) -> dict[str, Any]:
    """Everything the index keeps about one file. Only reads it."""
    path, ext = job.path, job.path.suffix.lower()
    flags = []
    if ext in NOT_ADOPTABLE_EXTENSIONS:
        flags.append(FLAG_NOT_ADOPTABLE)
    try:
        probe: tags.Probe | None = tags.probe(path)
    except (AudioError, NotFoundError) as exc:  # not audio, or gone since the walk
        log.info("Couldn't read the audio of %s: %s", path, exc.message)
        probe = None
        flags.append(FLAG_UNREADABLE)
    try:
        file_tags = tags.read_tags(path)
        extra = tags.read_extra(path)
        head = fileops.sha1_head(path)
    except (OSError, NotFoundError) as exc:
        log.warning("Couldn't read %s: %s", path, exc)
        file_tags, extra, head = tags.TrackTags(), {}, None
        if FLAG_UNREADABLE not in flags:
            flags.append(FLAG_UNREADABLE)
    parsed = _parse_names(job.rel, file_tags)
    bitrate = probe.bitrate_kbps if probe else None
    if _suspect_upscale(ext, bitrate, parsed, extra, job.rel):
        flags.append(FLAG_SUSPECT_UPSCALE)
    record = tags.to_record(file_tags)
    return {
        "id": item_id(job.source_id, job.rel),
        "source_id": job.source_id,
        "rel_path": job.rel,
        "size": job.info.st_size,
        "mtime_ns": job.info.st_mtime_ns,
        "sha1_head": head,
        "ext": ext,
        "codec": probe.codec if probe else None,
        "duration_s": probe.duration_s if probe else None,
        "bitrate_kbps": bitrate,
        "raw_tags_json": {"tags": record, "extra": extra, "warnings": file_tags.warnings},
        "parsed_artist": parsed.artist,
        "parsed_title": parsed.title,
        "parsed_version_json": list(parsed.version_tokens),
        "parse_confidence": parsed.confidence,
        "parsed_json": parsed.to_dict(),
        "flags_json": flags,
        "reasons_json": [],
        "scanned_at": _now(),
    }


def _parse_names(rel: str, file_tags: tags.TrackTags) -> normalize.Parsed:
    """The best parse of a rip's file name and its own title and artist tags."""
    return normalize.best_parse(Path(rel).stem, file_tags)


def parse_again(item: dict[str, Any]) -> normalize.Parsed:
    """An item's names parsed again, from what the scan stored about it: its path, and
    its own title and artist tags. It is the parse the scan made, with whatever the
    parser has learned since. An index scanned by an older engine holds no version
    words (`Parsed.version_words`), and a scan doesn't read an unchanged file again,
    so a plan that names a copy after its rip parses the names again here.

    Nothing is read from the rip, and the index isn't rebuilt (a rebuild would throw
    away the matcher's candidates). If the parser has changed what it makes of a name,
    the result differs from the stored `parsed_title`: compare them before trusting
    that a title in the library is the one the engine wrote."""
    own = (item.get("raw_tags_json") or {}).get("tags") or {}
    file_tags = tags.TrackTags(title=_text(own.get("title")), artist=_text(own.get("artist")))
    return _parse_names(item["rel_path"], file_tags)


def youtube_converted(extra: dict[str, str], name: str) -> bool:
    """Signs a rip was made from a YouTube video by a converter site or tool: ffmpeg's
    encoder tag ("Lavf…", "Lavc…"), or a converter's name in the file name or comment.
    Such a file is a re-encode of YouTube's own audio, so a CD or iTunes rip of the same
    song is better whatever the bitrates say (step 09d)."""
    encoder = extra.get("encoder", "").casefold()
    clues = " ".join([name, extra.get("comment", ""), encoder])
    return encoder.startswith(("lavf", "lavc")) or bool(_YOUTUBE_SIGNS.search(clues))


def _suspect_upscale(
    ext: str, bitrate: int | None, parsed: normalize.Parsed, extra: dict[str, str], rel: str
) -> bool:
    """A heuristic, and the report says so: an MP3 of 256 kbps or more that shows signs of
    coming from YouTube (whose audio is ~128 kbps) was probably re-encoded up from it."""
    if ext != ".mp3" or not bitrate or bitrate < SUSPECT_UPSCALE_KBPS:
        return False
    video_junk = [j for j in parsed.junk_removed if j != "emoji" and not j.isdigit()]
    encoder = extra.get("encoder", "").casefold()
    clues = " ".join([rel, extra.get("comment", ""), encoder])
    return (
        bool(video_junk)
        or encoder.startswith(("lavf", "lavc"))
        or bool(_YOUTUBE_SIGNS.search(clues))
    )


def _state_deriver(lib: Library, index: Index) -> Callable[[str, Path], str | None]:
    """What state.json and the library say about an item, which outranks `new`: a rip
    already replaced (superseded) or copied in (adopted), or the owner's decision."""
    data = lib.load_state().data
    decisions = state.decisions(data)
    superseded = set(state.superseded(data))
    adopted = {
        state.normalise_path(Path(t["origin_path"]))
        for t in index.library_tracks()
        if t.get("origin_path")
        and t.get("source") == "rip_copy"
        and t.get("match") != "unconfirmed"  # still waiting for the owner's review
    }

    def derive(item: str, path: Path) -> str | None:
        key = state.normalise_path(path)
        if key in superseded:
            return "superseded"
        if key in adopted:
            return "adopted"
        decision = decisions.get(item, {}).get("decision")
        return DECISION_STATES.get(str(decision)) if decision else None

    return derive


# ---- the library, and rebuilding the index ---------------------------------------------


def scan_library(lib: Library, index: Index) -> int:
    """Index the tracks in Music/ from their tags (paths relative to the library root).
    Returns how many."""
    rows = []
    walk = _Walk(skip=frozenset())
    for rel, path, info in _walk(lib.paths.music, walk, videos=True):
        found = tags.read_tags(path)
        try:
            duration = tags.probe(path).duration_s
        except AudioError:
            duration = None
        rows.append(
            {
                "rel_path": f"{naming.MUSIC_DIR}/{rel}",
                "musicorg_id": _text(found.musicorg_id),
                "size": info.st_size,
                "mtime_ns": info.st_mtime_ns,
                "title": _text(found.title),
                "artist": _text(found.artist),
                "album": _text(found.album),
                "duration_s": duration,
                "source": _text(found.source),
                "source_id": _text(found.source_id),
                "only_copy": 1 if found.only_copy is True else 0,
                "origin_path": _text(found.origin_path),
                "match": _text(found.match),
            }
        )
    index.put_library_tracks(rows)
    return len(rows)


@dataclass
class RebuildResult:
    library_tracks: int
    scan: ScanResult

    def to_dict(self) -> dict[str, Any]:
        return {"library_tracks": self.library_tracks, "scan": self.scan.to_dict()}


def rebuild(lib: Library, index: Index, *, progress: Progress | None = None) -> RebuildResult:
    """Recreate index.sqlite (contract section 5): library tracks from Music/ tags,
    external items from a fresh scan of the sources (ids are stable, so decisions and
    superseded links from state.json reattach), and `adopted` from MUSICORG_ORIGIN_PATH.
    queue.sqlite is never touched."""
    index.reset()
    tracks = scan_library(lib, index)
    if not state.sources(lib.load_state().data):
        return RebuildResult(tracks, ScanResult())
    return RebuildResult(tracks, scan(lib, index, progress=progress))


# ---- helpers ---------------------------------------------------------------------------


def _rel(root: Path, path: Path) -> str:
    return PurePosixPath(*path.relative_to(root).parts).as_posix() if path != root else ""


def _under_any(rel: str, folders: list[str], root: Path) -> bool:
    return any(folder == "" or rel.startswith(folder + "/") for folder in folders)


def _key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _hidden(info: os.stat_result) -> bool:
    """Windows' hidden attribute (dot-names are handled separately)."""
    return bool(getattr(info, "st_file_attributes", 0) & 0x2)


def _why(exc: OSError) -> str:
    if isinstance(exc, PermissionError) and sys.platform == "darwin":
        return (
            f"{exc.strerror}. On a Mac, allow your terminal app to read this folder in System "
            "Settings → Privacy & Security → Files and Folders (or Full Disk Access)."
        )
    return exc.strerror or str(exc)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
