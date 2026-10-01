"""What the app shows (v0.2): the library's tracks with the details a screen needs, and
one track's lyrics. Read-only as far as the library goes: nothing here changes a file.

The details come from each file's tags (rule 1: files are the source of truth) and are
kept in the index beside the size and modified time they were read at, so the list is
instant after the first time and corrects itself when a file changes. Covers and `.lrc`
files are looked for on disk every time, which is cheap.

Paths in and out are relative to the library root with `/` separators, as in the index.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from musicorg import naming, tags
from musicorg.errors import NotFoundError, OutsideLibraryError
from musicorg.index import Index
from musicorg.library import Library

log = logging.getLogger(__name__)

DETAILS_VERSION = 2  # raise it when `_details` gains a field, so old rows are read again


def tracks(lib: Library, index: Index) -> list[dict[str, Any]]:
    """Every track in the index whose file is there, as the `Track` shape in
    docs/ENGINE_API.md. `index` must be writable: details read from a file are stored."""
    found, fresh = [], []
    covers: dict[Path, bool] = {}
    for row in index.library_tracks():
        rel = row["rel_path"]
        path = lib.root.joinpath(*rel.split("/"))
        try:
            info = path.stat()
        except OSError:
            continue  # the index is a cache: a file that has gone just isn't shown
        details = _stored(row, info.st_size, info.st_mtime_ns)
        if details is None:
            details = _details(path)
            fresh.append({"rel_path": rel, "size": info.st_size, "mtime_ns": info.st_mtime_ns,
                          "title": details["title"], "artist": details["artist"],
                          "album": details["album"],
                          "details_json": json.dumps(details, ensure_ascii=False)})  # fmt: skip
        folder = path.parent
        if folder not in covers:
            covers[folder] = (folder / naming.COVER_NAME).is_file()
        synced = path.with_suffix(".lrc").is_file()
        shown = {k: v for k, v in details.items() if k not in ("v", "plain_lyrics")}
        found.append({
            "track_id": row["musicorg_id"],
            "path": rel,
            **shown,
            "title": details["title"] or path.stem,
            "duration_s": row["duration_s"],
            "only_copy": bool(row["only_copy"]),
            "cover": f"{rel.rsplit('/', 1)[0]}/{naming.COVER_NAME}" if covers[folder] else None,
            "lyrics": "synced" if synced else "plain" if details["plain_lyrics"] else "none",
        })  # fmt: skip
    index.set_track_details(fresh)
    if fresh:
        log.info("browse: read the details of %d tracks", len(fresh))
    return found


def lyrics(lib: Library, rel_path: str) -> dict[str, str | None]:
    """A track's lyrics: `synced` is the text of its `.lrc`, `plain` the lyrics in its
    tags. Either may be None."""
    path = track_path(lib, rel_path)
    lrc = path.with_suffix(".lrc")
    synced = lrc.read_text(encoding="utf-8", errors="replace") if lrc.is_file() else None
    plain = tags.read_tags(path).lyrics
    return {"synced": synced, "plain": plain if isinstance(plain, str) else None}


def track_path(lib: Library, rel_path: str) -> Path:
    """The audio file a library-relative path names. It must be inside Music/."""
    path = lib.root.joinpath(*rel_path.replace("\\", "/").split("/")).resolve()
    if not naming.is_within(path, lib.paths.music.resolve()):
        raise OutsideLibraryError("That file isn't in the library's Music folder.", rel_path)
    if not path.is_file() or not naming.is_audio_name(path.name):
        raise NotFoundError("That song isn't in the library any more.")
    return path


def _stored(row: dict[str, Any], size: int, mtime_ns: int) -> dict[str, Any] | None:
    if (row["size"], row["mtime_ns"]) != (size, mtime_ns) or not row.get("details_json"):
        return None
    try:
        details = json.loads(row["details_json"])
    except ValueError:
        return None
    return details if isinstance(details, dict) and details.get("v") == DETAILS_VERSION else None


def _details(path: Path) -> dict[str, Any]:
    found = tags.read_tags(path)

    def text(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    def number(value: object) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return {
        "v": DETAILS_VERSION,
        "title": text(found.title),
        "artist": text(found.artist),
        "album_artist": text(found.album_artist),
        "album": text(found.album),
        "year": number(found.year),
        "track": number(found.track),
        "disc": number(found.disc),
        "genre": text(found.genre),
        "explicit": found.explicit is True,
        "match": text(found.match),
        "acquired": text(found.acquired),
        "format": text(found.source_format),
        "bitrate_kbps": number(found.source_bitrate),
        "embedded_cover": isinstance(found.cover, bytes),
        "plain_lyrics": text(found.lyrics) is not None,
    }
