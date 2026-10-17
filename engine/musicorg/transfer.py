"""A profile taken to a drive, and brought back (2026-10-12).

The owner's yes to a rule change (CLAUDE.md, rule 2): "Export / Import under Profile",
for moving to another computer, or keeping a copy. An **export** is one new folder, in a
place the owner chose, holding what they ticked:

    export.json                 what's inside (written last: an export without it
                                didn't finish)
    Music/...                   the library's songs, covers and lyrics, as they lie
    Music/Videos/...            the saved music videos
    Videos/...                  the videos kept outside the library
    Movies/...                  the films kept (with Series/ and Anime/)
    records/state.json          the library's own records
    settings/addons.json        the owner's add-ons that need no set-up of their own
    settings/app.json           what the app handed over of its own settings

**No sign-in is ever in an export:** not `accounts.json`, not the paired devices' keys,
and not an add-on whose address carries a set-up of its own (which can hold a key): those
are named in `export.json` as left out, to be set up again by hand.

An **import** reads an export (it's a source: only ever opened for reading) into the
library that's open. Songs come in through `fileops.copy_in`, in one journaled batch
that undo takes back; a song the library has already (the same `MUSICORG_ID`) is left
out, so importing twice adds nothing. Films and videos go where kept ones go
(`fileops.keep_media`). The records are taken only by a library that has none of its
own, so nobody's playlists or play counts are ever written over.

Everything written goes through `fileops` (the export by `make_export_folder` and
`export_file`), `state` and `config`: this module writes nothing itself.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from musicorg import config, fileops, listening, naming, scan, state, tags
from musicorg.errors import MusicOrgError, UserError
from musicorg.index import open_index
from musicorg.library import Library

log = logging.getLogger(__name__)

FORMAT = 1
PARTS = ("music", "videos", "movies", "records", "settings")
# What's left out of every export, said in its own words in `export.json`.
LEFT_OUT = ("sign-ins", "paired devices")
VIDEO_ENDINGS = frozenset(
    {".mkv", ".mp4", ".m4v", ".mov", ".avi", ".webm", ".wmv", ".flv", ".mpg", ".mpeg", ".ts",
     ".m2ts"}
)  # fmt: skip
AUDIO_ENDINGS = frozenset({".m4a", ".mp3", ".flac", ".ogg", ".opus"})

# (how many are done, of how many, the one in hand)
Tick = Callable[[int, int, str], None]
File = tuple[Path, PurePosixPath]


def _files(folder: Path, *, leaving_out: Path | None = None) -> list[File]:
    """Every file under a folder that isn't junk, with its path inside it."""
    if not folder.is_dir():
        return []
    skip = leaving_out.resolve() if leaving_out is not None else None
    found: list[File] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.is_symlink() or naming.is_junk(path.name):
            continue
        if any(part.startswith(".") for part in path.relative_to(folder).parts):
            continue  # a hidden file or folder (a temp file of a copy under way)
        if skip is not None and naming.is_within(path.resolve(), skip):
            continue
        found.append((path, PurePosixPath(path.relative_to(folder).as_posix())))
    return found


def _is_saved_video(rel: PurePosixPath) -> bool:
    return bool(rel.parts) and rel.parts[0] == naming.VIDEOS_DIR


def _videos_only(files: Iterable[File]) -> list[File]:
    return [(path, rel) for path, rel in files if path.suffix.lower() in VIDEO_ENDINGS]


def gather(lib: Library) -> dict[str, list[File]]:
    """The files each part of an export would hold, with their paths in the export."""
    in_music = _files(lib.paths.music)
    folders = config.media_folders()
    movies, media = folders["movies"], folders["media"]
    return {
        "music": [
            (path, PurePosixPath(naming.MUSIC_DIR) / rel)
            for path, rel in in_music
            if not _is_saved_video(rel)
        ],
        "videos": [
            (path, PurePosixPath(naming.MUSIC_DIR) / rel)
            for path, rel in in_music
            if _is_saved_video(rel)
        ]
        + [(path, PurePosixPath("Videos") / rel) for path, rel in _videos_only(_files(media))],
        "movies": [
            (path, PurePosixPath("Movies") / rel)
            for path, rel in _videos_only(_files(movies, leaving_out=media))
        ],
    }


def contents(lib: Library) -> dict[str, Any]:
    """What a profile has to export, part by part: how many files and how big, for the
    app's tick-boxes. Nothing is written."""
    found = gather(lib)
    parts: dict[str, Any] = {
        name: {"files": len(files), "bytes": sum(path.stat().st_size for path, _ in files)}
        for name, files in found.items()
    }
    shown = listening.get(lib)
    parts["records"] = {
        "favourites": len(shown.get("favourites") or []),
        "playlists": len(shown.get("playlists") or []),
    }
    plain, own = _addons_to_export()
    parts["settings"] = {
        "addons": len(plain),
        "addons_left_out": [str(one["name"]) for one in own],
    }
    return {"parts": parts}


def _addons_to_export() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The owner's add-ons, in two lots: the ones whose address is the add-on's plain one
    (exported), and the ones set up their own way, whose address carries that set-up and
    may hold a key (left out, and named)."""
    plain: list[dict[str, Any]] = []
    own: list[dict[str, Any]] = []
    for one in config.load_addons() or []:
        address = one.get("address")
        if not isinstance(address, str):
            continue
        parts = urlsplit(address)
        is_plain = parts.path.strip("/") == "manifest.json" and not parts.query
        # A plain one goes as the engine keeps it (what its manifest said: nothing in
        # it is the owner's); of the other kind only the name is ever written down.
        (plain if is_plain else own).append(
            dict(one) if is_plain else {"name": str(one.get("name") or "an add-on")}
        )
    return plain, own


def _forbidden(lib: Library) -> list[Path]:
    """Where an export may never be made: the library itself, and every source folder."""
    banned = [lib.root]
    for source in state.sources(lib.load_state().data).values():
        path = source.get("path")
        if isinstance(path, str) and path:
            banned.append(Path(path))
    return banned


def export_profile(
    lib: Library,
    parent: Path,
    *,
    parts: Iterable[str],
    profile: str,
    app: dict[str, Any] | None = None,
    tick: Tick | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """Write an export of the open library into a new folder inside `parent`, and say
    what went into it. `parts` are the ones ticked (`PARTS`); `app` is what the app
    hands over of its own settings and records, kept as it's given."""
    wanted = _parts(parts)
    forbidden = _forbidden(lib)
    day = today or datetime.now(UTC).strftime("%Y-%m-%d")
    name = f"Bear Media Export {' '.join(profile.split()) or 'Profile'} {day}"
    found = gather(lib)
    todo: list[tuple[str, Path, PurePosixPath]] = [
        (part, path, rel) for part in ("music", "videos", "movies") if part in wanted
        for path, rel in found[part]
    ]  # fmt: skip
    folder = fileops.make_export_folder(Path(parent), name, forbidden=forbidden)
    summary: dict[str, Any] = {part: {"files": 0, "bytes": 0} for part in wanted}
    for done, (part, path, rel) in enumerate(todo):
        if tick is not None:
            tick(done, len(todo), path.name)
        fileops.export_file(path, folder, rel, forbidden=forbidden)
        summary[part]["files"] += 1
        summary[part]["bytes"] += path.stat().st_size
    left_out: list[str] = list(LEFT_OUT)
    if "records" in wanted:
        records = lib.paths.state_file
        if records.is_file():
            fileops.export_file(records, folder, "records/state.json", forbidden=forbidden)
            summary["records"] = {"files": 1, "bytes": records.stat().st_size}
    if "settings" in wanted:
        plain, own = _addons_to_export()
        fileops.export_file(
            _json({"addons": plain}), folder, "settings/addons.json", forbidden=forbidden
        )
        if app is not None:
            fileops.export_file(_json(app), folder, "settings/app.json", forbidden=forbidden)
        summary["settings"] = {"addons": len(plain), "app": app is not None}
        left_out += [f"the add-on {one['name']} (set up again by its own address)" for one in own]
    manifest = {
        "format": FORMAT,
        "made": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "profile": profile,
        "parts": summary,
        "left_out": left_out,
    }
    # Last: an export with this in it is a whole one.
    fileops.export_file(_json(manifest), folder, fileops.EXPORT_MARK, forbidden=forbidden)
    if tick is not None:
        tick(len(todo), len(todo), "")
    log.info("Exported %s to a folder named %s", ", ".join(sorted(wanted)), folder.name)
    return {"folder": str(folder), **manifest}


def _json(data: Any) -> bytes:
    return (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def _parts(parts: Iterable[str]) -> set[str]:
    wanted = set(parts)
    if not wanted or not wanted <= set(PARTS):
        raise UserError(f"Choose what to take: any of {', '.join(PARTS)}.")
    return wanted


def read_export(folder: Path) -> dict[str, Any]:
    """An export's own account of itself (`export.json`), or UserError when the folder
    isn't an export, didn't finish, or is of a kind this version can't read."""
    mark = Path(folder) / fileops.EXPORT_MARK
    try:
        found = json.loads(mark.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise UserError(
            f"{Path(folder).name} isn't a Bear Media export (or one that didn't "
            "finish): it has no export.json. Choose the folder an export made."
        ) from None
    except (OSError, ValueError) as exc:
        raise UserError(f"The export's export.json can't be read: {exc}.") from exc
    if not isinstance(found, dict) or found.get("format") != FORMAT:
        raise UserError(
            "That export was made by a newer Bear Media than this one, and can't be "
            "read here. Update the app first."
        )
    return found


def import_profile(
    lib: Library,
    folder: Path,
    *,
    parts: Iterable[str],
    dry_run: bool = False,
    tick: Tick | None = None,
) -> dict[str, Any]:
    """Bring an export into the open library, or (`dry_run`) only say what would come.
    The export is read and never changed. See the module's own words for the rules."""
    wanted = _parts(parts)
    folder = Path(folder).expanduser().absolute()
    manifest = read_export(folder)
    if naming.is_within(folder.resolve(), lib.root.resolve()):
        raise UserError("That export is inside this library's own folder, and can't be imported.")
    result: dict[str, Any] = {
        "profile": manifest.get("profile"),
        "made": manifest.get("made"),
        "dry_run": dry_run,
        "left_out": manifest.get("left_out") or [],
    }
    in_music = _files(folder / naming.MUSIC_DIR)
    library_files = [
        (path, rel)
        for path, rel in in_music
        if ("videos" if _is_saved_video(rel) else "music") in wanted
    ]
    result.update(_bring_in(lib, library_files, dry_run=dry_run, tick=tick))
    folders = config.media_folders()
    kept: list[tuple[Path, PurePosixPath, Path]] = []
    if "movies" in wanted:
        kept += [(p, rel, folders["movies"]) for p, rel in _videos_only(_files(folder / "Movies"))]
    if "videos" in wanted:
        kept += [(p, rel, folders["media"]) for p, rel in _videos_only(_files(folder / "Videos"))]
    result["films"] = _keep(lib, folder, kept, folders, dry_run=dry_run)
    if "records" in wanted:
        result["records"] = _take_records(lib, folder, dry_run=dry_run)
    if "settings" in wanted:
        result["addons"] = _take_addons(folder, dry_run=dry_run)
        result["app"] = _read_json(folder / "settings" / "app.json")
    return result


def _bring_in(
    lib: Library, files: list[File], *, dry_run: bool, tick: Tick | None
) -> dict[str, Any]:
    """Songs and saved videos into `Music/`, with the covers and lyrics beside them. One
    the library has already, by its `MUSICORG_ID`, is left out."""
    with open_index(lib.paths, write=False) as index:
        have = {row.get("musicorg_id") for row in index.library_tracks()}
    tracks: list[File] = []
    beside: list[File] = []
    already = 0
    for path, rel in files:
        if path.suffix.lower() not in AUDIO_ENDINGS | VIDEO_ENDINGS:
            beside.append((path, rel))
            continue
        try:
            track_id = tags.read_tags(path).musicorg_id
        except MusicOrgError:
            track_id = None
        if track_id is not None and track_id in have:
            already += 1
        else:
            tracks.append((path, rel))
    # A cover or lyrics file comes only where nothing of that name is there yet.
    beside = [(path, rel) for path, rel in beside if not (lib.paths.music / rel).exists()]
    summary = {"songs": {"brought": len(tracks), "already": already, "beside": len(beside)}}
    if dry_run or not (tracks or beside):
        return summary
    with fileops.batch(lib, "import") as batch:
        todo = tracks + beside
        for done, (path, rel) in enumerate(todo):
            if tick is not None:
                tick(done, len(todo), path.name)
            fileops.copy_in(batch, path, rel)
        summary["batch_id"] = batch.batch_id
    with open_index(lib.paths, write=True) as index:
        scan.scan_library(lib, index)
    return summary


def _keep(
    lib: Library,
    export: Path,
    files: list[tuple[Path, PurePosixPath, Path]],
    folders: dict[str, Path],
    *,
    dry_run: bool,
) -> dict[str, int]:
    """Films and videos back into the folders kept ones go in. One that's there already
    (the same name and size) is left."""
    brought = already = 0
    for path, rel, root in files:
        target = root / Path(*rel.parts)
        if target.is_file() and target.stat().st_size == path.stat().st_size:
            already += 1
            continue
        brought += 1
        if not dry_run:
            fileops.keep_media(
                path, target.parent, target.name, cache=export,
                allowed=[folders["movies"], folders["media"]], forbidden=[lib.root],
            )  # fmt: skip
    return {"brought": brought, "already": already}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _has_records(shown: dict[str, Any]) -> bool:
    return any(shown.get(key) for key in ("favourites", "plays", "listened", "playlists"))


def _take_records(lib: Library, folder: Path, *, dry_run: bool) -> str:
    """The export's favourites, play counts, listening time, playlists and followed
    channels: taken whole by a library that has none of its own (`taken`), and left
    alone by one that has (`kept_own`), so nothing is ever written over. `none`: the
    export has no records."""
    found = _read_json(folder / "records" / "state.json")
    theirs = found.get(listening.KEY) if isinstance(found, dict) else None
    if not isinstance(theirs, dict):
        return "none"
    if _has_records(listening.get(lib)):
        return "kept_own"
    if not dry_run:
        with state.edit(lib.paths.state_file) as st:
            st.data[listening.KEY] = theirs
    return "taken"


def _take_addons(folder: Path, *, dry_run: bool) -> int:
    """The export's add-ons the owner doesn't have yet, added to the end of their list
    as the export recorded them (what each one's manifest said). How many. A list never
    kept on this computer yet (the app hasn't put its own few in it) is left to the app."""
    found = _read_json(folder / "settings" / "addons.json")
    listed = found.get("addons") if isinstance(found, dict) else None
    current = config.load_addons()
    have = {one.get("address") for one in current or []}
    new = [
        dict(one)
        for one in (listed if isinstance(listed, list) else [])
        if isinstance(one, dict)
        and all(isinstance(one.get(key), str) for key in ("address", "id", "name"))
        and one["address"].startswith(("https://", "http://"))
        and one["address"] not in have
    ]
    if new and not dry_run and current is not None:
        config.save_addons(current + new)
    return len(new) if current is not None else 0


class Runner:
    """One export or import at a time, under way on a thread of its own, with how far
    it has got for the app to show. (A library of a few thousand songs is minutes of
    copying: the app can't wait on one answer for it.)"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._working: dict[str, Any] | None = None
        self._last: dict[str, Any] | None = None

    def start(
        self,
        kind: str,
        work: Callable[[Tick], dict[str, Any]],
        finished: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """Begin `work` (an export or an import, given what to call with its progress).
        UserError if one is under way already."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise UserError("An export or an import is under way already. Wait for it to end.")
            self._working = {"kind": kind, "done": 0, "total": 0, "name": ""}

            def tick(done: int, total: int, name: str) -> None:
                with self._lock:
                    self._working = {"kind": kind, "done": done, "total": total, "name": name}

            def run() -> None:
                try:
                    ended: dict[str, Any] = {"kind": kind, "ok": True, "summary": work(tick)}
                except MusicOrgError as exc:
                    ended = {"kind": kind, "ok": False, "message": str(exc)}
                except Exception as exc:  # a fault of ours: said, not swallowed
                    log.exception("The %s stopped", kind)
                    ended = {"kind": kind, "ok": False, "message": f"The {kind} stopped: {exc}"}
                with self._lock:
                    self._working, self._last = None, ended
                if finished is not None:
                    finished(ended)

            self._thread = threading.Thread(target=run, name=f"profile-{kind}", daemon=True)
            self._thread.start()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {"working": self._working, "last": self._last}

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
