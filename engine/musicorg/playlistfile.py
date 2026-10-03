"""A playlist saved as a file, for imports: the way in for Amazon Music.

Amazon Music has no way to read someone's playlists from outside and no export of its
own. What its listeners can do is have a service such as TuneMyMusic or Soundiiz save
a playlist as a file. This module reads such a file, and any other playlist file of
these kinds, whichever service it came from:

- **CSV** (or tab- or semicolon-separated): a header row, then a song a row. The columns
  are found by name, in any order: the song's (`Track name`, `Title`, `Song`, …), the
  artist's (`Artist name`, `Artist`, …), and if they're there the album's, the length
  (`Duration`: seconds, milliseconds or 3:45) and the playlist's (`Playlist name`).
  A file holding several playlists gives their names, and the songs of one of them.
- **Text**: a song a line, `Artist - Title`. A line with no " - " is a title alone.
- **M3U / M3U8**: `#EXTINF:215,Artist - Title` lines. Where a song has none, its file's
  name stands in ("Artist - Title.mp3").

The file is the owner's own, anywhere on their computer: it's opened read-only, never
changed, and nothing of where it was is kept (only its name, as the playlist's name
when the file doesn't give one). No header names were checked against a real export
from Amazon Music: the columns are looked for by every name such exports are known to
use, and a file with none of them says which it needs.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from musicorg.errors import UserError

KINDS = (".csv", ".tsv", ".txt", ".m3u", ".m3u8")
MAX_BYTES = 5_000_000
MAX_TRACKS = 3000
# Column names, as `_column` spells them, the likeliest first.
TITLE_COLUMNS = ("track name", "track title", "song name", "song title", "title", "song",
                 "track", "name")  # fmt: skip
ARTIST_COLUMNS = ("artist name s", "artist name", "artist names", "artists", "artist",
                  "track artist", "album artist")  # fmt: skip
ALBUM_COLUMNS = ("album name", "album title", "album")
LENGTH_COLUMNS = ("duration ms", "duration", "length", "time")
PLAYLIST_COLUMNS = ("playlist name", "playlist")
EXTINF = re.compile(r"#EXTINF:\s*(-?\d+(?:\.\d+)?)?[^,]*,(.*)")
CLOCK = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.\d+)?")


def read(path: Path, playlist: str | None = None) -> dict[str, Any]:
    """The playlist in a file: `{name, tracks, more, playlists}`. `tracks` are import
    tracks (a title, artists, and the album and length if the file has them);
    `playlists` names every playlist in a file that holds several, else it's empty, and
    `playlist` chooses which of them (the first, if not given)."""
    path = Path(path)
    kind = path.suffix.lower()
    if kind not in KINDS:
        raise UserError(
            "That isn't a playlist file Music Organizer reads. Choose a CSV, a text file "
            "(.txt) or an M3U playlist."
        )
    try:
        if path.stat().st_size > MAX_BYTES:
            raise UserError("That file is too big to be a playlist (over 5 MB).")
        with path.open("rb") as handle:  # read-only: the file is the owner's, outside the library
            text = _decoded(handle.read(MAX_BYTES + 1))
    except OSError:
        raise UserError("That file couldn't be opened. Is it still where it was?") from None

    if kind in (".m3u", ".m3u8"):
        lists = {"": _from_m3u(text)}
    elif kind == ".txt" and not _has_header(text):
        lists = {"": _from_lines(text)}
    else:
        lists = _from_table(text, tab=kind == ".tsv")
    named = [name for name in lists if name]
    if playlist is not None and playlist not in lists:
        raise UserError("That file has no playlist of that name.")
    chosen = playlist if playlist is not None else next(iter(lists), "")
    tracks = lists.get(chosen, [])
    if not tracks:
        raise UserError("No songs could be read from that file.")
    return {
        "name": chosen or path.stem.strip() or "Imported Playlist",
        "tracks": tracks[:MAX_TRACKS],
        "more": len(tracks) > MAX_TRACKS,
        "playlists": named if len(named) > 1 else [],
    }


def _decoded(data: bytes) -> str:
    """The file's text. Exports are UTF-8 (some with a mark at the start), UTF-16 when a
    spreadsheet saved them as "Unicode text", or an older Windows encoding."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _column(name: str) -> str:
    """A header as it's compared: "Artist Name(s)" and "artist_name_s" are the same."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name.lower()).split())


def _pick(header: list[str], wanted: tuple[str, ...]) -> int | None:
    for name in wanted:
        if name in header:
            return header.index(name)
    return None


def _dialect(first_line: str, tab: bool) -> str:
    if tab:
        return "\t"
    counts = {mark: first_line.count(mark) for mark in ("\t", ";", ",")}
    best = max(counts, key=lambda mark: counts[mark])
    return best if counts[best] else ","


def _has_header(text: str) -> bool:
    """Whether a .txt file is really a table: its first line names a title column and
    is separated like one."""
    first = text.lstrip().split("\n", 1)[0]
    mark = _dialect(first, tab=False)
    header = [_column(cell) for cell in next(csv.reader([first], delimiter=mark), [])]
    return len(header) > 1 and _pick(header, TITLE_COLUMNS) is not None


def _from_table(text: str, *, tab: bool) -> dict[str, list[dict[str, Any]]]:
    """Songs by playlist name ("" for a file that names none), in the file's order."""
    body = text.lstrip()
    mark = _dialect(body.split("\n", 1)[0], tab)
    rows = csv.reader(io.StringIO(body, newline=""), delimiter=mark)
    try:
        header = [_column(cell) for cell in next(rows, [])]
        title_at = _pick(header, TITLE_COLUMNS)
        if title_at is None:
            raise UserError(
                "That file has no column for the songs' names. Its first row should name "
                "the columns, with one called Title or Track name, and one called Artist."
            )
        artist_at = _pick(header, ARTIST_COLUMNS)
        album_at = _pick(header, ALBUM_COLUMNS)
        length_at = _pick(header, LENGTH_COLUMNS)
        in_ms = length_at is not None and header[length_at].endswith("ms")
        playlist_at = _pick(header, PLAYLIST_COLUMNS)
        lists: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            title = _cell(row, title_at)
            if not title:
                continue
            lists.setdefault(_cell(row, playlist_at), []).append(
                _track(
                    title,
                    _cell(row, artist_at),
                    _cell(row, album_at),
                    _seconds(_cell(row, length_at), in_ms),
                )
            )
    except csv.Error:
        raise UserError("That file couldn't be read as a table of songs.") from None
    return lists


def _cell(row: list[str], at: int | None) -> str:
    return row[at].strip() if at is not None and at < len(row) else ""


def _from_lines(text: str) -> list[dict[str, Any]]:
    """A song a line: "Artist - Title"."""
    tracks = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            tracks.append(_named(line, None))
    return tracks


def _from_m3u(text: str) -> list[dict[str, Any]]:
    tracks = []
    waiting: tuple[str, int | None] | None = None  # an #EXTINF line's song, until its file's line
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        info = EXTINF.fullmatch(line)
        if info is not None:
            length = round(float(info.group(1))) if info.group(1) else None
            waiting = (info.group(2).strip(), length if length and length > 0 else None)
            continue
        if line.startswith("#"):
            continue
        if waiting is not None and waiting[0]:
            tracks.append(_named(*waiting))
        else:
            # The file's own name, without its folders: "Artist - Title.mp3".
            pure = PureWindowsPath(line) if "\\" in line else PurePosixPath(line)
            if pure.stem:
                tracks.append(_named(pure.stem, waiting[1] if waiting else None))
        waiting = None
    return tracks


def _named(words: str, length: int | None) -> dict[str, Any]:
    """A song from "Artist - Title"; words with no " - " are a title alone."""
    artist, dash, title = words.partition(" - ")
    if not dash or not title.strip():
        return _track(words.strip(), "", "", length)
    return _track(title.strip(), artist.strip(), "", length)


def _track(title: str, artist: str, album: str, length: int | None) -> dict[str, Any]:
    # Several artists are separated by ";" in these exports. A comma isn't split on:
    # it's inside some artists' names.
    artists = [name.strip() for name in artist.split(";") if name.strip()]
    return {
        "title": title,
        "artists": artists,
        "album": album or None,
        "duration_s": length,
        "is_explicit": None,
    }


def _seconds(text: str, in_ms: bool) -> int | None:
    """A length as these files write it: 215, 215000 (milliseconds) or 3:35."""
    if not text:
        return None
    clock = CLOCK.fullmatch(text)
    if clock is not None:
        hours, minutes, seconds = (int(part or 0) for part in clock.groups())
        return hours * 3600 + minutes * 60 + seconds or None
    try:
        number = float(text)
    except ValueError:
        return None
    if in_ms or number > 20_000:  # nothing is 20,000 seconds long: it's milliseconds
        number /= 1000
    return round(number) if number > 0 else None
