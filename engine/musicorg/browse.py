"""What the app shows (v0.2): the library's tracks with the details a screen needs, and
one track's lyrics. Read-only as far as the library goes: nothing here changes a file.

The details come from each file's tags (rule 1: files are the source of truth) and are
kept in the index beside the size and modified time they were read at, so the list is
instant after the first time and corrects itself when a file changes. Covers and `.lrc`
files are looked for on disk every time, which is cheap.

Paths in and out are relative to the library root with `/` separators, as in the index.

It is also where a library file is read for which version of a song it is (`versions_of`):
its version tag and what its title names, the title read by who named it (`owner_named`,
`read_title`). The video lookup, Discover's "already have", the lyrics lookup and `plan
tidy` all read a library title this way, so they can't disagree. `typed_titles` says which
titles the owner typed themselves in Edit Details (the journal remembers): such a title
is theirs, and the engine neither changes it back nor reads past it to the rip's name.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

from musicorg import fileops, naming, tags
from musicorg.errors import AudioError, NotFoundError, OutsideLibraryError
from musicorg.index import Index
from musicorg.library import Library
from musicorg.normalize import Parsed, parse_filename, parse_owner_title, parse_title

log = logging.getLogger(__name__)

DETAILS_VERSION = 2  # raise it when `_details` gains a field, so old rows are read again
UNCONFIRMED = "unconfirmed"  # MUSICORG_MATCH of a rip copied in before it was identified
# MUSICORG_MATCH of a rip's copy whose names are the owner's (`owner_named`). No match
# tag at all says the same: an only copy the owner gave no fixes for.
OWNER_NAMED_MATCHES = frozenset({UNCONFIRMED, "manual"})


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
        video = naming.is_video_path(rel)
        details = _stored(row, info.st_size, info.st_mtime_ns)
        if details is None:
            details = _details(path, video=video)
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
            "source": row["source"],
            "source_id": row["source_id"],
            "video": video,
            "height": details.get("height") if video else None,
            # A video's cover is its own picture, inside the file; the folder has none.
            "cover": f"{rel.rsplit('/', 1)[0]}/{naming.COVER_NAME}"
            if covers[folder] and not video
            else None,
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


def owner_named(source: object, match: object) -> bool:
    """Whether a library file's title is one the owner named, not one YouTube Music gave:
    a copy of their own rip that is still unconfirmed, or kept as an only copy (with
    their fixes: `manual`; without: no match tag at all). `source` and `match` are the
    file's MUSICORG_SOURCE and MUSICORG_MATCH.

    It decides how the title is read (`read_title`). A copy with official details
    (`auto_details`, `user_details`), a replacement (`auto_exact`, `user_confirmed`)
    and a download all carry YouTube Music's own title."""
    return source == "rip_copy" and (match is None or match in OWNER_NAMED_MATCHES)


def read_title(title: str, *, owner: bool) -> Parsed:
    """A library title, parsed for its clean title and the versions it names. A title
    the owner named (`owner`) is read with the reader that knows their mark for a remix:
    "Lost Boy R" is the song "Lost Boy", a remix. An official title never is: YouTube
    Music doesn't write that mark, and a title that happens to end in " R" isn't one."""
    return parse_owner_title(title) if owner else parse_title(title)


def rip_stem(origin_path: str) -> str:
    """The file name, without its extension, of the rip a copy came from. The path may
    have been written on another system, so both kinds of slash count."""
    name = re.split(r"[\\/]", origin_path)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def typed_titles(lib: Library) -> dict[str, set[str]]:
    """The titles the owner typed themselves in Edit Details, by the song's MUSICORG_ID.

    Read from the journal: an `edit` batch's tag write that changed the title. An edit
    that was undone doesn't count. Needs no lock, and reads no library file.

    Why it's needed: a copy made before 2026-10-04 got its rip's title with the version
    left out ("Eyes On Fire" for the rip "Eyes On Fire R"), and `plan tidy` puts the
    version back. An owner who takes the R off by hand ends up with the very same title
    and tags. Only the journal can tell the two apart, and a title the owner typed
    stays as they typed it."""
    typed: dict[str, set[str]] = {}
    journal = fileops.read_journal(lib)
    for record in journal.values():
        if record.kind != "edit":
            continue
        undone = {
            op.intent.get("undoes")
            for other in record.undone_by
            for op in journal[other].ops
            if op.status == "done"
        }
        for op in record.ops:
            if op.op != "write_tags" or op.status != "done" or op.op_id in undone:
                continue
            before, after = op.intent.get("before") or {}, op.intent.get("after") or {}
            title, track_id = after.get("title"), after.get("musicorg_id")
            if (
                isinstance(title, str)
                and isinstance(track_id, str)
                and title != before.get("title")
            ):
                typed.setdefault(track_id, set()).add(title)
    return typed


def typed_by_owner(typed: dict[str, set[str]], track_id: object, title: object) -> bool:
    """Whether this song's title is one the owner typed in Edit Details (`typed` is
    `typed_titles`' answer)."""
    return isinstance(track_id, str) and isinstance(title, str) and title in typed.get(track_id, ())


def versions_of(found: tags.TrackTags, lib: Library | None = None) -> tuple[str, ...]:
    """What a library file's own tags say about which version of a song it is ("remix",
    "remix:somebody", "live"…): its version tag, and what its title names
    (`read_title`).

    A copy that is still unconfirmed, with no version tag and a title that names no
    version, is read by the name of the rip it came from instead. That covers a copy
    made before 2026-10-04, whose title lost the version its rip names until `plan tidy`
    puts it back, and a copy whose rip's name was too hard to read to trust (it keeps
    the rip's own title and gets no version tag). No other copy is read that way: once
    a song is found, or its file says which version it is, the rip's old name says
    nothing more about it.

    Nor does it once the owner has typed the title themselves (with `lib`, so the
    journal can be asked: `typed_titles`). Taking the R off "Cooler Than Me R" in Edit
    Details says it isn't a remix, and the rip's name mustn't say otherwise."""
    tokens = list(found.version) if isinstance(found.version, list) else []
    if isinstance(found.title, str) and found.title:
        owner = owner_named(found.source, found.match)
        tokens += read_title(found.title, owner=owner).version_tokens
    origin = found.origin_path
    if found.match == UNCONFIRMED and not tokens and isinstance(origin, str) and origin:
        named = parse_filename(rip_stem(origin)).version_tokens
        # The journal is read only when it matters: the rip names a version the file
        # doesn't.
        if named and not (
            lib is not None and typed_by_owner(typed_titles(lib), found.musicorg_id, found.title)
        ):
            tokens += named
    return tuple(dict.fromkeys(tokens))


def version_tokens(lib: Library, rel_path: str) -> tuple[str, ...]:
    """Which version of a song a library file is, as `versions_of` reads its tags."""
    return versions_of(tags.read_tags(track_path(lib, rel_path)), lib)


def row_match(row: dict[str, Any]) -> str | None:
    """A library track's MUSICORG_MATCH, as the index knows it: from the details read
    off the file's own tags when the row has them, else from its `match` column. The
    column was added on 2026-10-01 and is empty on rows written before that, whatever
    their tags say; the details hold what the tags said."""
    try:
        details = json.loads(row.get("details_json") or "null")
    except ValueError:
        details = None
    known = isinstance(details, dict) and "match" in details
    found = details["match"] if known else row.get("match")
    return found if isinstance(found, str) and found else None


def artist_genre(index: Index, artists: tuple[str, ...] | list[str]) -> str | None:
    """The genre the owner's own songs by these artists are tagged with: the commonest
    one, counting only songs that came from the owner's files (a downloaded song's
    genre may itself have been worked out this way). None if they have none. It's what
    a newly downloaded song by the artist is filed under."""
    from musicorg.youtube import artist_key

    wanted = {key for key in (artist_key(name) for name in artists) if key}
    if not wanted:
        return None
    found: Counter[str] = Counter()
    for row in index.library_tracks():
        if row["source"] == "youtube_music" and row.get("match") is None:
            continue  # a download
        credit = f" {artist_key(row.get('artist') or '')} "
        if not any(f" {key} " in credit for key in wanted):
            continue
        try:
            details = json.loads(row.get("details_json") or "{}")
        except ValueError:
            continue
        genre = details.get("genre") if isinstance(details, dict) else None
        if isinstance(genre, str) and genre.strip():
            found[genre.strip()] += 1
    return max(found, key=lambda genre: (found[genre], genre)) if found else None


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


def _details(path: Path, *, video: bool = False) -> dict[str, Any]:
    found = tags.read_tags(path)
    height = None
    if video:
        try:
            height = tags.probe(path).height
        except (AudioError, NotFoundError):
            height = None

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
        **({"height": height} if video else {}),  # only a video's row carries it
    }
