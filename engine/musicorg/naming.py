"""Path rules: the library layout, file names built from tags, and names to skip.

Implements docs/LIBRARY_CONTRACT.md sections 1 (layout) and 2 (naming). Nothing here
touches the disk; fileops does the creating and moving.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path, PurePath, PurePosixPath

from musicorg.errors import PathTooLongError

# ---- layout (contract section 1) -------------------------------------------------------

MUSIC_DIR = "Music"
# Saved videos live in `Music/Videos/<Artist>/<Title>.mp4` (contract section 2). Inside
# Music/, so every fileops operation and guard covers them as it does a song.
VIDEOS_DIR = "Videos"
VIDEO_SUFFIX = ".mp4"
REPLACED_DIR = "_Replaced"
STAGING_DIR = "_Staging"
CALIBRATION_DIR = "calibration"
REPORTS_DIR = "Reports"
ENGINE_DIR = ".musicorg"


@dataclass(frozen=True)
class LibraryPaths:
    """Every fixed path in a library, worked out from its root folder."""

    root: Path

    @property
    def music(self) -> Path:
        return self.root / MUSIC_DIR

    @property
    def replaced(self) -> Path:
        return self.root / REPLACED_DIR

    @property
    def staging(self) -> Path:
        return self.root / STAGING_DIR

    @property
    def calibration(self) -> Path:
        return self.staging / CALIBRATION_DIR

    @property
    def reports(self) -> Path:
        return self.root / REPORTS_DIR

    @property
    def engine(self) -> Path:
        return self.root / ENGINE_DIR

    @property
    def journal(self) -> Path:
        return self.engine / "journal"

    @property
    def plans(self) -> Path:
        return self.engine / "plans"

    @property
    def undo_art(self) -> Path:
        return self.engine / "undo-art"

    @property
    def state_file(self) -> Path:
        return self.engine / "state.json"

    @property
    def index_file(self) -> Path:
        return self.engine / "index.sqlite"

    @property
    def queue_file(self) -> Path:
        return self.engine / "queue.sqlite"

    @property
    def lock_file(self) -> Path:
        return self.engine / "lock"

    @property
    def lock_info_file(self) -> Path:
        return self.engine / "lock.info"

    @property
    def managed(self) -> tuple[Path, ...]:
        """The folders the engine writes user data in."""
        return (self.music, self.replaced, self.staging, self.engine)

    @property
    def layout_folders(self) -> tuple[Path, ...]:
        """The folders `init` creates, parents before children."""
        return (
            self.music,
            self.replaced,
            self.staging,
            self.calibration,
            self.reports,
            self.engine,
            self.journal,
            self.plans,
            self.undo_art,
        )


# ---- names to skip, and audio files ----------------------------------------------------

_JUNK_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})

AUDIO_SUFFIXES = frozenset(
    {
        ".aac", ".aif", ".aifc", ".aiff", ".alac", ".ape", ".dff", ".dsf", ".flac",
        ".m4a", ".m4b", ".m4p", ".mka", ".mp2", ".mp3", ".mp4", ".oga", ".ogg",
        ".opus", ".wav", ".wave", ".webm", ".wma", ".wv",
    }
)  # fmt: skip


def is_junk(name: str) -> bool:
    """.DS_Store, ._* (macOS resource forks), Thumbs.db and desktop.ini: ignored everywhere."""
    return name.startswith("._") or name.lower() in _JUNK_NAMES


def is_within(path: PurePath, folder: PurePath) -> bool:
    """Whether `path` is `folder` or inside it, comparing as macOS and Windows do by
    default (ignoring case and Unicode form). Pure path logic: pass resolved paths."""

    def key(p: PurePath) -> str:
        text = unicodedata.normalize("NFC", str(p)).replace("\\", "/").rstrip("/")
        return text.casefold() if sys.platform in ("darwin", "win32") else text

    target = key(folder)
    return any(key(p) == target for p in (path, *path.parents))


def is_audio_name(name: str) -> bool:
    """Whether a file name looks like an audio file (by its extension)."""
    return not is_junk(name) and PurePath(name).suffix.lower() in AUDIO_SUFFIXES


# ---- naming (contract section 2) -------------------------------------------------------

MAX_CHARS = 120
MAX_BYTES = 200
WINDOWS_MAX_PATH = 259
# Room kept in the Windows budget for a collision suffix, up to " (99)".
COLLISION_ROOM = len(" (99)")
# When the Windows budget forces cuts, no field is cut shorter than this.
MIN_FIELD_CHARS = 10

UNKNOWN_ARTIST = "Unknown Artist"
VARIOUS_ARTISTS = "Various Artists"
UNSORTED = "Unsorted"
UNKNOWN_TITLE = "Unknown Title"
COVER_NAME = "cover.jpg"

_ILLEGAL = frozenset('\\/:*?"<>|')
# The contract lists CON PRN AUX NUL COM1-9 LPT1-9. Windows also refuses COM0, LPT0, the
# superscript-digit forms and CONIN$/CONOUT$, so those are covered too.
_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"{port}{n}" for port in ("COM", "LPT") for n in "0123456789¹²³"}
)
_EXTENSION = re.compile(r"(.+?)(\.[A-Za-z0-9]{1,5})")
_LEADING_NUMBER = re.compile(r"\s*(\d+)")
_YEAR = re.compile(r"\s*(\d{4})")
_ZWJ = "\u200d"


@dataclass(frozen=True)
class TrackMeta:
    """What a library file name is built from. Empty strings count as missing.

    Numbers may be ints or tag-style strings: "3", "3/12", "2019-05-03".
    """

    title: str | None = None
    artist: str | None = None
    album_artist: str | None = None
    album: str | None = None
    year: int | str | None = None
    track: int | str | None = None
    disc: int | str | None = None
    disc_total: int | str | None = None
    compilation: bool = False
    ext: str = "m4a"
    # The source file's name or path, for the title fallback.
    source_file: str | PurePath | None = None


def safe_component(s: str, max_chars: int = MAX_CHARS, max_bytes: int = MAX_BYTES) -> str:
    """Make `s` safe as one path component on macOS and Windows.

    NFC; `\\ / : * ? " < > |` and control characters become `_`; leading and trailing
    spaces and trailing dots are trimmed; a leading `.` becomes `_`; Windows reserved
    names get a `_` suffix; cut to `max_chars` characters and `max_bytes` UTF-8 bytes on a
    character boundary. Returns "" when nothing usable is left, so callers can fall back.
    """
    return _finish(_fit(_clean(s), max_chars, max_bytes))


def library_path(meta: TrackMeta, root: PurePath, *, platform: str | None = None) -> Path:
    """Where a track goes, relative to the library's Music/ folder.

    `Music/<Album Artist>/<Album> (<Year>)/<Track> <Title>.<ext>`, with the fallbacks,
    sanitising and length limits of contract section 2. On Windows (`platform` "win32",
    default: this computer) the whole path under `<root>/_Replaced/` must also fit in 259
    characters, with room left for a " (99)" collision suffix: the title is cut first,
    then the album, then the artist, none below 10 characters. If even that can't fit,
    PathTooLongError explains that the library folder's path is too long.
    """
    return _path(meta, root, platform, top=None)


def video_path(meta: TrackMeta, root: PurePath, *, platform: str | None = None) -> Path:
    """Where a saved video goes, relative to the library's Music/ folder:
    `Videos/<Artist>/<Title>.mp4`, with the same sanitising and length limits as a song.
    A video has no album, year or track number in its name."""
    artist = safe_component(meta.album_artist or "") or safe_component(meta.artist or "")
    plain = replace(
        meta, album=artist or UNKNOWN_ARTIST, year=None, track=None, disc=None,
        disc_total=None, compilation=False, ext=VIDEO_SUFFIX,
    )  # fmt: skip
    return _path(plain, root, platform, top=VIDEOS_DIR)


def is_video_path(rel: PurePath | str) -> bool:
    """Whether a path under Music/ (with or without the leading "Music") is a saved
    video: an .mp4 inside `Videos/`."""
    parts = PurePosixPath(str(rel).replace("\\", "/")).parts
    if parts and parts[0] == MUSIC_DIR:
        parts = parts[1:]
    return (
        len(parts) >= 2
        and parts[0].casefold() == VIDEOS_DIR.casefold()
        and parts[-1].lower().endswith(VIDEO_SUFFIX)
    )


def _path(meta: TrackMeta, root: PurePath, platform: str | None, *, top: str | None) -> Path:
    """`library_path`, or with `top` the same rules under that fixed first folder."""
    ext = _extension(meta.ext)
    album = _clean(meta.album or "")
    year = _year(meta.year)

    if top is not None:
        artist_dir = top
    elif album and meta.compilation:
        artist_dir = VARIOUS_ARTISTS
    else:
        artist_dir = (
            safe_component(meta.album_artist or "")
            or safe_component(meta.artist or "")
            or UNKNOWN_ARTIST
        )
        if artist_dir.casefold() == VIDEOS_DIR.casefold():
            artist_dir += " (artist)"  # `Videos/` is where saved videos live

    if album:
        year_part = f" ({year})" if year else ""
        album_text = _fit(album, MAX_CHARS, MAX_BYTES, fixed=year_part)
        prefix = _track_prefix(meta)
    else:
        year_part = ""
        album_text = UNSORTED
        prefix = ""

    title = _clean(meta.title or "") or _clean(_source_stem(meta.source_file)) or UNKNOWN_TITLE
    title_text = _fit(title, MAX_CHARS, MAX_BYTES, fixed=prefix + ext) or UNKNOWN_TITLE

    if (platform or sys.platform) == "win32":
        artist_dir, album_text, title_text = _fit_windows_budget(
            root, artist_dir, album_text, year_part, prefix, title_text, ext
        )
    return Path(artist_dir, _finish(album_text + year_part), _finish(prefix + title_text + ext))


def candidate_names(target: Path, limit: int = 1000) -> Iterator[Path]:
    """`target`, then `<stem> (2)<ext>`, `<stem> (3)<ext>` ... up to `(limit)`.

    Only the names; fileops reserves one atomically (contract 6.4).
    """
    yield target
    stem, ext = _split_extension(target.name)
    for n in range(2, limit + 1):
        yield target.with_name(f"{stem} ({n}){ext}")


# ---- kept films, outside the library (contract section 1) ------------------------------

# A film the owner keeps is loose in the Movies folder. A series' episodes and anime have
# a folder each inside it, and that folder is what says which a file is (the owner,
# 2026-10-08): nothing else records it, so it holds for a file the owner puts there too.
KEPT_MOVIE = "movie"
KEPT_FOLDERS = {"series": "Series", "anime": "Anime"}
KEPT_KINDS = (KEPT_MOVIE, *KEPT_FOLDERS)
SPECIALS_DIR = "Specials"  # season 0
_EPISODE_TITLE_GAP = " - "
_SEASON_DIR = re.compile(r"season\s*(\d{1,4})", re.IGNORECASE)
# "S01E02", wherever it is in a name: "Show S01E02 - Title", "show.s1e2.1080p".
_SEASON_EPISODE = re.compile(r"(?<![A-Za-z0-9])S(\d{1,4})\s?E(\d{1,4})(?!\d)", re.IGNORECASE)
# "E02": an episode whose season isn't known.
_EPISODE = re.compile(r"(?<![A-Za-z0-9])E(\d{1,4})(?![A-Za-z0-9])", re.IGNORECASE)
# The Windows budget: room kept for a kept file's ending and a collision suffix, and for
# what goes round a show's name twice over (its folder, and a file in it):
# \Series\<Show>\Season 9999\<Show> S9999E9999
_KEPT_TAIL_ROOM = len(".mpeg") + COLLISION_ROOM
_KEPT_SHOW_ROOM = len("\\Series\\") + len("\\Season 9999\\") + len(" S9999E9999")


def kept_place(
    kind: str,
    title: str,
    *,
    year: int | str | None = None,
    show: str | None = None,
    season: int | None = None,
    episode: int | None = None,
    root: PurePath | None = None,
    platform: str | None = None,
) -> tuple[tuple[str, ...], str]:
    """Where a film or an episode the owner keeps goes inside the Movies folder: the
    folders, from the Movies folder down, and the file's name without its ending.

        a film                         <Title> (<Year>)
        an episode of a series         Series/<Show>/Season 1/<Show> S01E02 - <Title>
        an episode of an anime         Anime/<Show>/Season 1/<Show> S01E02 - <Title>
        an anime that's a film         Anime/<Title> (<Year>)

    Only what's known is written: an episode with no title of its own is `<Show> S01E02`,
    one whose season isn't known is `<Show>/<Show> E02`, and season 0 is `Specials`.
    Every name is made safe and cut to the usual length (the title first, so the
    numbers stay). The name comes back empty when there's nothing to make one from.

    On Windows (`platform` "win32", default: this computer) the whole path under `root`,
    the Movies folder, must also fit in 259 characters, with room left for the file's
    ending and a " (99)" collision suffix. A show's name is cut by a rule that goes only
    by the Movies folder, so every episode of it lands in the same folder; an episode's
    own title gets what's left.
    """
    spare: int | None = None  # on Windows: what the path has left for names
    if root is not None and (platform or sys.platform) == "win32":
        spare = WINDOWS_MAX_PATH - _utf16_len(str(root).rstrip("\\/")) - _KEPT_TAIL_ROOM
    top = KEPT_FOLDERS.get(kind)
    own = _clean(title)
    part_of = ""
    if top is not None and show:
        most = MAX_CHARS if spare is None else (spare - _KEPT_SHOW_ROOM) // 2
        part_of = safe_component(show, _chars(most, show))
    if top is None or not part_of:
        folders: tuple[str, ...] = (top,) if top else ()
        made = _year(year)
        dated = f" ({made})" if made else ""
        most = _chars(_kept_name_room(spare, folders), own)
        return folders, _finish(_fit(own, most, MAX_BYTES, fixed=dated) + dated) if own else ""
    folders = (top, part_of)
    if season is not None:
        folders += (SPECIALS_DIR if season == 0 else f"Season {season}",)
    if season is not None and episode is not None:
        number = f" S{season:02d}E{episode:02d}"
    elif episode is not None:
        number = f" E{episode:02d}"
    else:
        number = ""
    most = _kept_name_room(spare, folders)
    head = _fit(part_of, _chars(most, part_of), MAX_BYTES, fixed=number) + number
    own = _fit(own, _chars(most, own), MAX_BYTES, fixed=head + _EPISODE_TITLE_GAP)
    return folders, _finish(head + (_EPISODE_TITLE_GAP + own if own else ""))


def _kept_name_room(spare: int | None, folders: tuple[str, ...]) -> int:
    """How long a kept file's name may be: the usual length, or on Windows what the
    path has left after its folders."""
    if spare is None:
        return MAX_CHARS
    return min(MAX_CHARS, spare - sum(1 + _utf16_len(folder) for folder in folders) - 1)


def _chars(most: int, text: str) -> int:
    """`most` as a count of `text`'s characters, at least a few. Windows counts an emoji
    as two, so a name that has one is given half."""
    if _utf16_len(text) > len(text):
        most //= 2
    return max(min(most, MAX_CHARS), MIN_FIELD_CHARS)


def kept_details(rel: PurePath | str) -> dict[str, str | int]:
    """What a file in the Movies folder is, read from where it is (`rel`: its path from
    the Movies folder down). Always `kind`; for an episode also `show`, and `season`,
    `episode` and `title` (the episode's own) where the path says.

    - `kind` is `series` or `anime` for a file inside `Series/` or `Anime/`, and
      `movie` for one anywhere else. Nothing is guessed from a film's name.
    - A file straight inside `Series/` or `Anime/` is part of no show (an anime film).
      Otherwise `show` is the name of the folder under it.
    - `season` and `episode` are the "S01E02" in the file's name. Without one, the
      season is a `Season 2` (or `Specials`: 0) folder it's in, and the episode an
      "E02" in its name.
    - `title` is what follows " - " after those numbers.
    """
    parts = PurePosixPath(unicodedata.normalize("NFC", str(rel)).replace("\\", "/")).parts
    kind = KEPT_MOVIE
    if len(parts) > 1:
        top = parts[0].casefold()
        kind = next((k for k, name in KEPT_FOLDERS.items() if name.casefold() == top), kind)
    found: dict[str, str | int] = {"kind": kind}
    if kind == KEPT_MOVIE or len(parts) < 3:
        return found
    show, stem = parts[1], PurePosixPath(parts[-1]).stem
    found["show"] = show
    numbered = _SEASON_EPISODE.search(stem)
    if numbered:
        found["season"], found["episode"] = int(numbered[1]), int(numbered[2])
    else:
        for folder in parts[2:-1]:
            if folder.casefold() == SPECIALS_DIR.casefold():
                found["season"] = 0
            elif in_season := _SEASON_DIR.fullmatch(folder.strip()):
                found["season"] = int(in_season[1])
        numbered = _EPISODE.search(stem)
        if numbered:
            found["episode"] = int(numbered[1])
    if numbered:
        rest = stem[numbered.end() :]
    elif stem.casefold().startswith(show.casefold() + _EPISODE_TITLE_GAP):
        rest = stem[len(show) :]
    else:
        rest = ""
    if rest.startswith(_EPISODE_TITLE_GAP) and (own := rest[len(_EPISODE_TITLE_GAP) :].strip()):
        found["title"] = own
    return found


# ---- helpers ---------------------------------------------------------------------------


def _clean(text: str) -> str:
    """NFC, illegal and control characters to `_`, outer spaces trimmed. No length limit."""
    text = unicodedata.normalize("NFC", text)
    chars = [
        "_" if ch in _ILLEGAL or unicodedata.category(ch) in ("Cc", "Cs") else ch for ch in text
    ]
    return "".join(chars).strip(" ")


def _finish(name: str) -> str:
    """The per-component rules that depend on the whole name: dots and reserved names."""
    name = name.strip(" ")
    if name.startswith("."):
        name = "_" + name[1:]
    name = name.rstrip(". ")
    base = name.split(".", 1)[0].rstrip(" ")
    if base.upper() in _RESERVED:
        name = base + "_" + name[len(base) :]
    return name


def _fit(text: str, max_chars: int, max_bytes: int, fixed: str = "") -> str:
    """Cut `text` so that `text + fixed` fits both limits. Trailing spaces are dropped."""
    room_chars = max_chars - len(fixed)
    room_bytes = max_bytes - len(fixed.encode("utf-8"))
    n = used = 0
    for ch in text[: max(room_chars, 0)]:
        size = len(ch.encode("utf-8"))
        if used + size > room_bytes:
            break
        used += size
        n += 1
    return _cut(text, n).rstrip(" ")


def _cut(text: str, n: int) -> str:
    """The first `n` characters, backing off so an accent, emoji sequence or flag isn't
    split. If backing off would leave nothing (e.g. a title of stacked accents), cut at `n`.
    """
    if len(text) <= n:
        return text
    cut = n
    while cut > 0 and _splits_cluster(text, cut):
        cut -= 1
    return text[:cut] if cut > 0 else text[:n]


def _splits_cluster(text: str, i: int) -> bool:
    """Whether cutting between text[i-1] and text[i] splits one visible character."""
    ch = text[i]
    if unicodedata.category(ch).startswith("M"):  # combining accents and marks
        return True
    if ch == _ZWJ or text[i - 1] == _ZWJ:  # emoji joined with zero-width joiners
        return True
    code = ord(ch)
    if 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF:  # variation selectors
        return True
    if 0x1F3FB <= code <= 0x1F3FF or 0xE0020 <= code <= 0xE007F:  # skin tones, tag flags
        return True
    if _is_regional_indicator(ch) and _is_regional_indicator(text[i - 1]):
        run = 0  # flags are pairs of regional indicators; don't cut inside a pair
        j = i - 1
        while j >= 0 and _is_regional_indicator(text[j]):
            run += 1
            j -= 1
        return run % 2 == 1
    return False


def _is_regional_indicator(ch: str) -> bool:
    return 0x1F1E6 <= ord(ch) <= 0x1F1FF


def _extension(ext: str) -> str:
    cleaned = _clean(ext).lstrip(".").lower()
    if not cleaned or not cleaned.isalnum():
        raise ValueError(f"not a file extension: {ext!r}")
    return "." + cleaned


def _split_extension(name: str) -> tuple[str, str]:
    """("01 Title", ".m4a"). A name without a plain extension is all stem."""
    match = _EXTENSION.fullmatch(name)
    return (match.group(1), match.group(2)) if match else (name, "")


def _source_stem(source: str | PurePath | None) -> str:
    if source is None:
        return ""
    name = source.name if isinstance(source, PurePath) else PurePath(source).name
    return _split_extension(name)[0]


def _number_and_total(value: int | str | None) -> tuple[int | None, int | None]:
    """7 → (7, None); "2/3" → (2, 3); 0, "", "x" → (None, None)."""
    if value is None or isinstance(value, bool):
        return None, None
    if isinstance(value, int):
        return (value if value > 0 else None), None
    number, _, total = str(value).partition("/")
    return _positive(number), _positive(total)


def _positive(text: str) -> int | None:
    match = _LEADING_NUMBER.match(text)
    if not match:
        return None
    value = int(match.group(1))
    return value if value > 0 else None


def _year(value: int | str | None) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return f"{value:04d}" if 0 < value <= 9999 else None
    match = _YEAR.match(value)
    if not match or int(match.group(1)) == 0:
        return None
    return match.group(1)


def _track_prefix(meta: TrackMeta) -> str:
    """The track number prefix: "07 ", "2-07 " on multi-disc albums, or "" without one."""
    track, _ = _number_and_total(meta.track)
    if track is None:
        return ""
    disc, disc_total_from_disc = _number_and_total(meta.disc)
    disc_total, _ = _number_and_total(meta.disc_total)
    disc_total = disc_total or disc_total_from_disc
    multi_disc = (disc_total or 0) > 1 or (disc or 0) > 1
    if multi_disc and disc is not None:
        return f"{disc}-{track:02d} "
    return f"{track:02d} "


def _utf16_len(text: str) -> int:
    """Windows counts path length in UTF-16 units: an emoji counts as 2."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _fit_windows_budget(
    root: PurePath,
    artist_dir: str,
    album_text: str,
    year_part: str,
    prefix: str,
    title_text: str,
    ext: str,
) -> tuple[str, str, str]:
    """Cut the title, then the album, then the artist until the longest path fits."""
    budget = WINDOWS_MAX_PATH - COLLISION_ROOM
    root_len = _utf16_len(str(root).rstrip("\\/"))

    def overflow() -> int:
        file_name = _finish(prefix + title_text + ext)
        parts = (REPLACED_DIR, artist_dir, _finish(album_text + year_part))
        longest_file = max(_utf16_len(file_name), len(COVER_NAME))
        return root_len + sum(1 + _utf16_len(p) for p in parts) + 1 + longest_file - budget

    for field in ("title", "album", "artist"):
        while (excess := overflow()) > 0:
            current = {"title": title_text, "album": album_text, "artist": artist_dir}[field]
            shorter = _shorten(current, excess)
            if shorter is None:
                break
            if field == "title":
                title_text = shorter
            elif field == "album":
                album_text = shorter
            else:
                artist_dir = _finish(shorter) or shorter
        if overflow() <= 0:
            return artist_dir, album_text, title_text

    raise PathTooLongError(
        f"The library folder's path is too long for Windows ({len(str(root))} characters), "
        f"so song paths inside it can't be kept under Windows' limit of {WINDOWS_MAX_PATH} "
        "characters. Move the library to a folder with a shorter path, such as "
        "C:\\Music Organizer Library."
    )


def _shorten(text: str, excess: int) -> str | None:
    """`text` made at least `excess` UTF-16 units shorter, but not below MIN_FIELD_CHARS
    characters. None if it can't get any shorter."""
    target = _utf16_len(text) - excess
    n = len(text)
    while n > MIN_FIELD_CHARS and _utf16_len(text[:n]) > target:
        n -= 1
    shorter = _cut(text, n).rstrip(" ")
    return shorter if shorter and len(shorter) < len(text) else None
