"""Tags, probe and the audio hash (docs/LIBRARY_CONTRACT.md sections 3 and 4).

- `TrackTags`: one field per row of the tag schema. None means "absent" (or, in a
  change, "leave as it is"); REMOVE means "delete this field".
- `read_tags(path)`: M4A, MP3, FLAC, Ogg Vorbis and Opus, via mutagen. Other files (WebM,
  raw AAC, WAV, ...) give empty tags with a warning. Reading never raises for a messy
  or broken file: problems become warnings.
- `write_tags(path, changes)`: changes the managed fields and keeps everything else.
  Only fileops calls it, on a staged copy, and checks the audio afterwards (contract
  6.7). MP3 is saved as ID3v2.3.
- `probe(path)` and `audio_hash(path)`, through ffprobe and ffmpeg.
- `to_record()` / `from_record()`: the JSON form the journal keeps, with the cover as its
  SHA-256.

Under rule 3 of CLAUDE.md this module may make exactly one mutagen `.save()` call.
"""

from __future__ import annotations

import base64
import dataclasses
import functools
import hashlib
import json
import logging
import re
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (
    APIC,
    ID3,
    TALB,
    TCON,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TPOS,
    TRCK,
    TXXX,
    USLT,
    PictureType,
)
from mutagen.id3 import Encoding as ID3Encoding
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, AtomDataType, MP4Cover, MP4FreeForm
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from musicorg import tools
from musicorg.errors import AudioError, NotFoundError, UserError
from musicorg.naming import VIDEO_SUFFIX

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Contract section 3: tags are read and written for these...
# .mp4 is a saved video (v0.2): the same tags as an M4A, beside a picture stream.
WRITABLE_SUFFIXES = frozenset({".m4a", ".mp4", ".mp3", ".flac", ".ogg", ".opus"})
# ...and these are indexed but not adopted in v0.1, so their tags aren't read.
NOT_ADOPTED_SUFFIXES = frozenset({".webm", ".aac", ".wav"})

# docs/ENGINE_API.md → Enums.
SOURCES = frozenset({"youtube_music", "youtube", "rip_copy", "bandcamp", "cd", "itunes", "other"})
MATCHES = frozenset(
    {"auto_exact", "user_confirmed", "manual", "auto_details", "user_details", "unconfirmed"}
)

VERSION_SEPARATOR = "; "
MULTI_VALUE_SEPARATOR = "; "  # how several values of one field are shown when read
JPEG, PNG = "image/jpeg", "image/png"
FFMPEG_TIMEOUT_S = 600


class Removal:
    """The type of REMOVE."""

    def __repr__(self) -> str:
        return "REMOVE"


# In a change, a field set to REMOVE is deleted from the file.
REMOVE = Removal()

Text = str | Removal | None
Number = int | Removal | None


@dataclass
class TrackTags:
    """Every tag the engine manages (contract section 4). `warnings` isn't a tag: it
    says what went wrong while reading."""

    # Standard tags
    title: Text = None
    artist: Text = None
    album_artist: Text = None
    album: Text = None
    year: Number = None
    track: Number = None
    track_total: Number = None
    disc: Number = None
    disc_total: Number = None
    genre: Text = None
    lyrics: Text = None  # plain lyrics; synced ones go in the .lrc sidecar
    cover: bytes | Removal | None = None  # the front cover
    cover_mime: Text = None  # image/jpeg or image/png
    explicit: bool | Removal | None = None
    # Provenance tags (MUSICORG_*)
    schema: Number = None
    musicorg_id: Text = None
    source: Text = None
    source_id: Text = None
    source_format: Text = None
    source_bitrate: Number = None
    acquired: Text = None
    match: Text = None
    match_score: float | Removal | None = None
    only_copy: bool | Removal | None = None  # False is written as "absent"
    origin_path: Text = None
    version: list[str] | Removal | None = None

    warnings: list[str] = field(default_factory=list, compare=False, repr=False)

    def fields(self) -> dict[str, Any]:
        """The tag fields and their values, `warnings` left out."""
        return {name: getattr(self, name) for name in TAG_FIELDS}


TAG_FIELDS = tuple(f.name for f in dataclasses.fields(TrackTags) if f.name != "warnings")

# Provenance fields, their tag names, and how their values are written.
PROVENANCE = {
    "schema": "MUSICORG_SCHEMA",
    "musicorg_id": "MUSICORG_ID",
    "source": "MUSICORG_SOURCE",
    "source_id": "MUSICORG_SOURCE_ID",
    "source_format": "MUSICORG_SOURCE_FORMAT",
    "source_bitrate": "MUSICORG_SOURCE_BITRATE",
    "acquired": "MUSICORG_ACQUIRED",
    "match": "MUSICORG_MATCH",
    "match_score": "MUSICORG_MATCH_SCORE",
    "only_copy": "MUSICORG_ONLY_COPY",
    "origin_path": "MUSICORG_ORIGIN_PATH",
    "version": "MUSICORG_VERSION",
}
_INT_FIELDS = frozenset(
    {"year", "track", "track_total", "disc", "disc_total", "schema", "source_bitrate"}
)


def new_track_id() -> str:
    """A new MUSICORG_ID: a random UUID, set once and never changed."""
    return str(uuid.uuid4())


# ---- reading ---------------------------------------------------------------------------


def read_tags(path: PurePath | str) -> TrackTags:
    """A file's tags. Never raises for a messy or unsupported file: what couldn't be read
    is in `warnings`. Raises NotFoundError if there's no file."""
    path = Path(path)
    if not path.is_file():
        raise NotFoundError(f"There's no file at {path}.")
    suffix = path.suffix.lower()
    if suffix not in WRITABLE_SUFFIXES:
        return TrackTags(warnings=[f"{path.name}: tags aren't read from {suffix or 'these'} files"])
    try:
        audio = mutagen.File(path)
    except Exception as exc:  # mutagen raises many kinds of error for broken files
        return TrackTags(warnings=[f"{path.name}: couldn't read its tags ({exc})"])
    backend = _backend(audio)
    if backend is None:
        kind = type(audio).__name__ if audio is not None else "unknown"
        return TrackTags(warnings=[f"{path.name}: tags aren't read from {kind} files"])
    tags = TrackTags()
    for name in TAG_FIELDS:
        if name == "cover_mime":
            continue
        try:
            if name == "cover":
                picture = backend.get_cover()
                if picture is not None:
                    tags.cover, tags.cover_mime = picture
            else:
                setattr(tags, name, _parse(name, backend.get(name)))
        except Exception as exc:
            tags.warnings.append(f"{path.name}: couldn't read {name} ({exc})")
    tags.warnings.extend(f"{path.name}: {w}" for w in backend.warnings)
    for warning in tags.warnings:
        log.debug("%s", warning)
    return tags


def _parse(name: str, raw: str | None) -> Any:
    """A field's value from the text in the file."""
    if raw is None or not raw.strip():
        return None
    raw = raw.strip()
    if name == "year":
        match = re.match(r"(\d{4})", raw)
        return int(match.group(1)) if match and int(match.group(1)) > 0 else None
    if name in _INT_FIELDS:
        match = re.match(r"\d+", raw)
        if not match:
            raise ValueError(f"not a number: {raw!r}")
        value = int(match.group(0))
        return value if value > 0 or name == "source_bitrate" else None
    if name == "match_score":
        return float(raw)
    if name in ("explicit", "only_copy"):
        return raw in ("1", "4")  # iTunes: 1 or 4 explicit, 0 none, 2 clean
    if name == "version":
        return [token for token in raw.split(VERSION_SEPARATOR) if token]
    return raw


# Unmanaged fields worth knowing about a rip: which program encoded it, and its comment.
_EXTRA = {
    "encoder": {"id3": ("TSSE", "TENC"), "mp4": ("©too",), "vorbis": ("ENCODER", "ENCODED-BY")},
    "comment": {"id3": ("COMM",), "mp4": ("©cmt",), "vorbis": ("COMMENT", "DESCRIPTION")},
}
_EXTRA_LIMIT = 300


def read_extra(path: PurePath | str) -> dict[str, str]:
    """A rip's encoder and comment, if it has them. Best effort: never raises for a
    broken file, and long values are cut."""
    path = Path(path)
    if path.suffix.lower() not in WRITABLE_SUFFIXES:
        return {}
    try:
        audio = mutagen.File(path)
    except Exception:
        return {}
    if audio is None or audio.tags is None:
        return {}
    kind = "mp4" if isinstance(audio, MP4) else "id3" if isinstance(audio, MP3) else "vorbis"
    extra = {}
    for name, keys in _EXTRA.items():
        for key in keys[kind]:
            try:
                if kind == "id3":
                    frames = audio.tags.getall(key)
                    values = [str(t) for f in frames for t in f.text] if frames else []
                else:
                    values = [str(v) for v in audio.tags.get(key, [])]
            except Exception:
                continue
            text = " ".join(v.strip() for v in values if v.strip())
            if text:
                extra[name] = text[:_EXTRA_LIMIT]
                break
    return extra


# ---- writing ---------------------------------------------------------------------------


def check_writable(path: PurePath | str) -> None:
    """UserError unless tags can be written to this kind of file."""
    suffix = Path(path).suffix.lower()
    if suffix not in WRITABLE_SUFFIXES:
        raise UserError(
            f"Music Organizer doesn't write tags to {suffix or 'files without an extension'} "
            f"files ({Path(path).name}). It writes M4A, MP4, MP3, FLAC, Ogg and Opus."
        )


def write_tags(path: PurePath | str, changes: TrackTags) -> None:
    """Change the fields set in `changes` (None: leave it; REMOVE: delete it), keeping every
    other tag, atom and frame. Only fileops calls this, on a staged copy, and checks the
    result. Raises ValueError for a value the schema doesn't allow."""
    path = Path(path)
    check_writable(path)
    changes = prepare(changes)
    try:
        audio = mutagen.File(path)
    except Exception as exc:
        raise UserError(f"Couldn't open {path.name} to write its tags: {exc}") from exc
    backend = _backend(audio)
    if audio is None or backend is None:
        raise UserError(f"{path.name} isn't a kind of file Music Organizer can write tags to.")
    if audio.tags is None:
        audio.add_tags()
        backend = _backend(audio)
        assert backend is not None

    values = changes.fields()
    for pair in (("track", "track_total"), ("disc", "disc_total")):
        if values[pair[0]] is None and values[pair[1]] is None:
            continue
        number, total = (_merge_value(backend.get(n), values[n], n) for n in pair)
        if number is None:
            total = None  # a total alone can't be stored everywhere
        backend.set_pair(pair[0], number, total)
    if values["cover"] is REMOVE:
        backend.remove_cover()
    elif values["cover"] is not None:
        backend.set_cover(values["cover"], str(values["cover_mime"]))
    skip = {"track", "track_total", "disc", "disc_total", "cover", "cover_mime"}
    for name, value in values.items():
        if name in skip or value is None:
            continue
        if value is REMOVE:
            backend.remove(name)
        else:
            backend.set(name, _render(name, value))
    try:
        _save(audio)
    except Exception as exc:
        raise UserError(f"Couldn't write the tags of {path.name}: {exc}") from exc


def _merge_value(raw: str | None, change: Any, name: str) -> int | None:
    if change is REMOVE:
        return None
    if change is not None:
        return int(change)
    try:
        return _parse(name, raw)
    except ValueError:
        return None


def _render(name: str, value: Any) -> str:
    """How a field's value is written as text."""
    if name == "year":
        return f"{value:04d}"
    if name == "match_score":
        return f"{value:.3f}"
    if name in ("explicit", "only_copy"):
        return "1" if value else "0"
    if name == "version":
        return VERSION_SEPARATOR.join(value)
    return str(value)


def _save(audio: mutagen.FileType) -> None:
    """The one mutagen save (CLAUDE.md rule 3). MP3 is written as ID3v2.3, with the year in
    TYER, because many car stereos and older players don't read v2.4."""
    options: dict[str, Any] = {}
    if isinstance(audio, MP3):
        id3 = audio.tags
        if id3.unknown_frames and getattr(id3, "_unknown_v2_version", 3) != 3:
            log.warning(
                "%s has ID3v2.4 frames that mutagen doesn't know; they can't be kept in "
                "ID3v2.3, so they're left out of the new tags.",
                audio.filename,
            )
        # v2.4-only frames that players read in v2.3 too (iTunes writes the sort names
        # there): kept, as mutagen suggests, by taking them out around the conversion.
        kept = [frame for key in _KEEP_IN_V23 for frame in id3.getall(key)]
        id3.update_to_v23()  # TDRC becomes TYER, and so on
        for frame in kept:
            id3.add(frame)
        options["v2_version"] = 3
    audio.save(**options)


# Sort names, mood, producer notice and ReplayGain: unmanaged, so kept (contract section 4).
_KEEP_IN_V23 = ("TSOA", "TSOP", "TSOT", "TSST", "TMOO", "TPRO", "RVA2")


def prepare(changes: TrackTags) -> TrackTags:
    """`changes` checked against the schema and put in the form they'll be read back in:
    empty text is never written (so it leaves the field as it is), the match score keeps 3
    decimals, and only_copy=False means the field is absent."""
    values = changes.fields()
    for name, value in values.items():
        if value is None or value is REMOVE:
            continue
        values[name] = _check(name, value)
    if values["only_copy"] is False:
        values["only_copy"] = REMOVE
    cover = values["cover"]
    if cover is REMOVE:
        values["cover_mime"] = REMOVE
    elif cover is not None:
        values["cover_mime"] = _image_mime(cover, values["cover_mime"])
    elif values["cover_mime"] is not None:
        raise ValueError("cover_mime can only change together with the cover")
    return TrackTags(**values)


def _check(name: str, value: Any) -> Any:
    if name == "cover":
        if not isinstance(value, bytes) or not value:
            raise ValueError("cover must be the image's bytes")
        return value
    if name in _INT_FIELDS:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name} must be a whole number, not {value!r}")
        low = 0 if name == "source_bitrate" else 1
        if value < low or (name == "year" and value > 9999):
            raise ValueError(f"{name} is out of range: {value}")
        return value
    if name in ("explicit", "only_copy"):
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be True or False, not {value!r}")
        return value
    if name == "match_score":
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
            raise ValueError(f"match_score must be between 0 and 1, not {value!r}")
        return round(float(value), 3)
    if name == "version":
        tokens = [t for t in value if isinstance(t, str) and t.strip()]
        if len(tokens) != len(value) or any(VERSION_SEPARATOR in t for t in tokens):
            raise ValueError(f"version must be a list of tokens, not {value!r}")
        return tokens or None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text, not {value!r}")
    if not value.strip():
        return None  # empty strings are never written
    if name == "source" and value not in SOURCES:
        raise ValueError(f"unknown MUSICORG_SOURCE {value!r}; see docs/ENGINE_API.md → Enums")
    if name == "match" and value not in MATCHES:
        raise ValueError(f"unknown MUSICORG_MATCH {value!r}; see docs/ENGINE_API.md → Enums")
    return value


def merge(current: TrackTags, changes: TrackTags) -> TrackTags:
    """The tags a file will have after `write_tags(changes)`."""
    changes = prepare(changes)
    merged = {}
    for name, now in current.fields().items():
        change = getattr(changes, name)
        merged[name] = now if change is None else (None if change is REMOVE else change)
    for number, total in (("track", "track_total"), ("disc", "disc_total")):
        pair_changed = getattr(changes, number) is not None or getattr(changes, total) is not None
        if pair_changed and merged[number] is None:
            merged[total] = None
    return TrackTags(**merged)


def _image_mime(data: bytes, given: Any) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        detected = JPEG
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = PNG
    else:
        raise ValueError("the cover must be a JPEG or PNG image")
    if given not in (None, REMOVE) and _normal_mime(str(given)) != detected:
        raise ValueError(f"the cover is {detected}, not {given}")
    return detected


def _normal_mime(mime: str) -> str | None:
    mime = mime.strip().lower()
    if mime in ("image/jpeg", "image/jpg", "jpeg", "jpg", "image/pjpeg"):
        return JPEG
    if mime in ("image/png", "png"):
        return PNG
    return None


# ---- the journal's form ----------------------------------------------------------------


def to_record(tags: TrackTags) -> dict[str, Any]:
    """The fields present, as JSON: the cover becomes its SHA-256 (the image itself is kept
    in `.musicorg/undo-art/` when a write replaces it)."""
    record: dict[str, Any] = {}
    for name, value in tags.fields().items():
        if value is None or value is REMOVE:
            continue
        record[name] = hashlib.sha256(value).hexdigest() if name == "cover" else value
    return json.loads(json.dumps(record))


def from_record(changes: dict[str, Any], cover: bytes | None = None) -> TrackTags:
    """A change from its journal form (field → value, or REMOVE). A cover in it needs its
    image bytes, `cover`."""
    values: dict[str, Any] = {}
    for name, value in changes.items():
        if name not in TAG_FIELDS:
            raise ValueError(f"not a tag field: {name}")
        if name == "cover" and value is not REMOVE:
            if cover is None or hashlib.sha256(cover).hexdigest() != value:
                raise ValueError("the cover's image is needed to restore it")
            value = cover
        values[name] = value
    if values.get("cover") is not None and values.get("cover") is not REMOVE:
        values.pop("cover_mime", None)  # worked out from the image
    return TrackTags(**values)


# ---- probe and the audio hash ----------------------------------------------------------


@dataclass(frozen=True)
class Probe:
    """What ffprobe says about a file's first audio stream, and about its picture if
    it's a video (a cover embedded in a song isn't a picture stream)."""

    codec: str | None
    duration_s: float | None
    bitrate_kbps: int | None
    sample_rate: int | None
    channels: int | None
    video_codec: str | None = None
    height: int | None = None


def probe(path: PurePath | str) -> Probe:
    """Codec, duration, bitrate, sample rate and channels, from ffprobe. Use this rather
    than durations in tags, which rips often get wrong."""
    path = Path(path)
    output = _run(
        [
            str(_tool("ffprobe")),
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        path,
        "read",
    )
    try:
        info = json.loads(output)
    except ValueError as exc:
        raise AudioError(f"Couldn't read the audio in {path.name}: unclear reply.") from exc
    stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if stream is None:
        raise AudioError(f"{path.name} has no audio in it.")
    fmt = info.get("format", {})
    picture = next((s for s in info.get("streams", []) if _is_picture_stream(s)), None)
    # In a video the file's overall bitrate is mostly the picture's, so it can't stand
    # in for the audio's.
    bit_rate = _number(stream.get("bit_rate")) or (
        None if picture else _number(fmt.get("bit_rate"))
    )
    return Probe(
        codec=stream.get("codec_name"),
        duration_s=_number(stream.get("duration")) or _number(fmt.get("duration")),
        bitrate_kbps=round(bit_rate / 1000) if bit_rate else None,
        sample_rate=int(_number(stream.get("sample_rate")) or 0) or None,
        channels=stream.get("channels"),
        video_codec=picture.get("codec_name") if picture else None,
        height=int(_number(picture.get("height")) or 0) or None if picture else None,
    )


def _is_picture_stream(stream: Any) -> bool:
    """A moving picture, not a song's embedded cover (which ffprobe also lists as a
    video stream, marked `attached_pic`)."""
    return (
        isinstance(stream, dict)
        and stream.get("codec_type") == "video"
        and not (stream.get("disposition") or {}).get("attached_pic")
    )


def audio_hash(path: PurePath | str) -> str:
    """MD5 of the decoded audio of the first audio stream, as hex. Always computed fresh:
    it's how a tag write proves the audio didn't change (contract 6.7).

    For a saved video (.mp4 with a picture stream) the picture counts too: the hash is
    the audio's, then ":" and the MD5 of the picture stream's data as stored (not
    decoded, which would take minutes)."""
    path = Path(path)
    sound = _stream_hash(path, ["-map", "0:a:0"])
    if path.suffix.lower() != VIDEO_SUFFIX or probe(path).video_codec is None:
        return sound
    # 0:V:0 is the first video stream that isn't an attached picture.
    return f"{sound}:{_stream_hash(path, ['-map', '0:V:0', '-c', 'copy'])}"


def _stream_hash(path: Path, select: list[str]) -> str:
    output = _run(
        [
            str(_tool("ffmpeg")),
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(path),
            *select,
            "-f",
            "md5",
            "-",
        ],
        path,
        "decode",
    )
    match = re.search(r"MD5=([0-9a-f]{32})", output)
    if not match:
        raise AudioError(f"Couldn't decode the audio in {path.name}: ffmpeg gave no hash.")
    return match.group(1)


@functools.cache
def _tool(name: str) -> Path:
    return tools.require(name)


def _run(command: list[str], path: Path, verb: str) -> str:
    if not path.is_file():
        raise NotFoundError(f"There's no file at {path}.")
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=FFMPEG_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioError(f"Couldn't {verb} the audio in {path.name}: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1:] or ["no details"]
        raise AudioError(f"Couldn't {verb} the audio in {path.name}: {detail[0]}")
    return result.stdout


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        return None


# ---- the three tag systems -------------------------------------------------------------


class _Backend:
    """Text in and out of one kind of tag. Fields are the TrackTags names."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def get(self, name: str) -> str | None:
        raise NotImplementedError

    def set(self, name: str, text: str) -> None:
        raise NotImplementedError

    def remove(self, name: str) -> None:
        raise NotImplementedError

    def set_pair(self, name: str, number: int | None, total: int | None) -> None:
        raise NotImplementedError

    def get_cover(self) -> tuple[bytes, str] | None:
        raise NotImplementedError

    def set_cover(self, data: bytes, mime: str) -> None:
        raise NotImplementedError

    def remove_cover(self) -> None:
        raise NotImplementedError


def _backend(audio: mutagen.FileType | None) -> _Backend | None:
    if isinstance(audio, MP4):
        return _MP4(audio)
    if isinstance(audio, MP3):
        return _ID3(audio)
    if isinstance(audio, FLAC | OggVorbis | OggOpus):
        return _Vorbis(audio)
    return None


def _joined(values: list[str]) -> str | None:
    texts = [v for v in values if v.strip()]
    return MULTI_VALUE_SEPARATOR.join(texts) if texts else None


class _MP4(_Backend):
    ATOMS = {
        "title": "©nam",
        "artist": "©ART",
        "album_artist": "aART",
        "album": "©alb",
        "year": "©day",
        "genre": "©gen",
        "lyrics": "©lyr",
        "explicit": "rtng",
        "track": "trkn",
        "disc": "disk",
    }
    FREEFORM = "----:com.apple.iTunes:"

    def __init__(self, audio: MP4) -> None:
        super().__init__()
        self.audio = audio

    @property
    def tags(self) -> Any:
        return self.audio.tags if self.audio.tags is not None else {}

    def _key(self, name: str) -> str:
        return self.ATOMS.get(name) or self.FREEFORM + PROVENANCE[name]

    def get(self, name: str) -> str | None:
        if name in ("track_total", "disc_total"):
            pair = self.tags.get(self.ATOMS[name.removesuffix("_total")])
            return str(pair[0][1]) if pair and pair[0][1] else None
        values = self.tags.get(self._key(name))
        if not values:
            return None
        if name in ("track", "disc"):
            return str(values[0][0]) if values[0][0] else None
        if name == "explicit":
            return str(values[0])
        if name in PROVENANCE:
            return _joined([bytes(v).decode("utf-8", "replace") for v in values])
        return _joined([str(v) for v in values])

    def set(self, name: str, text: str) -> None:
        key = self._key(name)
        if name == "explicit":
            self.audio.tags[key] = [int(text)]
        elif name in PROVENANCE:
            self.audio.tags[key] = [MP4FreeForm(text.encode("utf-8"), AtomDataType.UTF8)]
        else:
            self.audio.tags[key] = [text]

    def remove(self, name: str) -> None:
        self.audio.tags.pop(self._key(name), None)

    def set_pair(self, name: str, number: int | None, total: int | None) -> None:
        if number is None:
            self.audio.tags.pop(self.ATOMS[name], None)
        else:
            self.audio.tags[self.ATOMS[name]] = [(number, total or 0)]

    def get_cover(self) -> tuple[bytes, str] | None:
        covers = self.tags.get("covr")
        if not covers:
            return None
        mime = PNG if covers[0].imageformat == MP4Cover.FORMAT_PNG else JPEG
        return bytes(covers[0]), mime

    def set_cover(self, data: bytes, mime: str) -> None:
        kind = MP4Cover.FORMAT_PNG if mime == PNG else MP4Cover.FORMAT_JPEG
        self.audio.tags["covr"] = [MP4Cover(data, imageformat=kind)]

    def remove_cover(self) -> None:
        self.audio.tags.pop("covr", None)


class _ID3(_Backend):
    FRAMES: dict[str, Callable[..., Any]] = {
        "title": TIT2,
        "artist": TPE1,
        "album_artist": TPE2,
        "album": TALB,
        "genre": TCON,
        "year": TDRC,
    }
    TXXX_NAMES = {**PROVENANCE, "explicit": "ITUNESADVISORY"}

    def __init__(self, audio: MP3) -> None:
        super().__init__()
        self.audio = audio

    @property
    def tags(self) -> ID3:
        return self.audio.tags if self.audio.tags is not None else ID3()

    def _text(self, frames: list[Any]) -> str | None:
        values = []
        for frame in frames:
            if isinstance(frame, TCON):
                texts = list(frame.genres)
            elif isinstance(frame.text, str):  # USLT holds one string
                texts = [frame.text]
            else:
                texts = [str(t) for t in frame.text]
            if getattr(frame, "encoding", None) == ID3Encoding.LATIN1:
                texts = [self._repair(t) for t in texts]
            values.extend(texts)
        return _joined(values)

    def _repair(self, text: str) -> str:
        """UTF-8 text in a frame that says it's Latin-1, common in rips: "CafÃ©" → "Café"."""
        try:
            repaired = text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return text
        if repaired != text:
            self.warnings.append(f"repaired mis-encoded text {text!r} → {repaired!r}")
        return repaired

    def get(self, name: str) -> str | None:
        tags = self.tags
        if name in ("track", "track_total", "disc", "disc_total"):
            frame = "TRCK" if name.startswith("track") else "TPOS"
            text = self._text(tags.getall(frame))
            if text is None:
                return None
            number, _, total = text.partition("/")
            return total or None if name.endswith("_total") else number
        if name == "lyrics":
            return self._text(tags.getall("USLT")[:1])
        if name in self.TXXX_NAMES:
            return self._text(tags.getall(f"TXXX:{self.TXXX_NAMES[name]}"))
        return self._text(tags.getall(self.FRAMES[name].__name__))

    def set(self, name: str, text: str) -> None:
        self.remove(name)
        if name == "lyrics":
            self.tags.add(USLT(encoding=ID3Encoding.UTF8, lang="eng", desc="", text=text))
        elif name in self.TXXX_NAMES:
            desc = self.TXXX_NAMES[name]
            self.tags.add(TXXX(encoding=ID3Encoding.UTF8, desc=desc, text=[text]))
        else:
            self.tags.add(self.FRAMES[name](encoding=ID3Encoding.UTF8, text=[text]))

    def remove(self, name: str) -> None:
        if name == "lyrics":
            self.tags.delall("USLT")
        elif name in self.TXXX_NAMES:
            self.tags.delall(f"TXXX:{self.TXXX_NAMES[name]}")
        elif name == "year":
            for frame in ("TDRC", "TYER", "TDAT", "TIME", "TRDA"):
                self.tags.delall(frame)
        else:
            self.tags.delall(self.FRAMES[name].__name__)

    def set_pair(self, name: str, number: int | None, total: int | None) -> None:
        frame = TRCK if name == "track" else TPOS
        self.tags.delall(frame.__name__)
        if number is not None:
            text = f"{number}/{total}" if total else str(number)
            self.tags.add(frame(encoding=ID3Encoding.LATIN1, text=[text]))

    def _fronts(self) -> list[str]:
        return [k for k, f in self.tags.items() if k.startswith("APIC") and f.type == 3]

    def get_cover(self) -> tuple[bytes, str] | None:
        for key in self._fronts():
            frame = self.tags[key]
            return frame.data, _normal_mime(frame.mime) or _image_mime(frame.data, None)
        return None

    def set_cover(self, data: bytes, mime: str) -> None:
        self.remove_cover()
        desc = "" if "APIC:" not in self.tags else "Front cover"  # don't replace other art
        self.tags.add(
            APIC(
                encoding=ID3Encoding.LATIN1,
                mime=mime,
                type=PictureType.COVER_FRONT,
                desc=desc,
                data=data,
            )
        )

    def remove_cover(self) -> None:
        for key in self._fronts():
            del self.tags[key]


class _Vorbis(_Backend):
    """FLAC, Ogg Vorbis and Opus: plain comments, plus a front-cover picture block."""

    NAMES = {
        "title": "TITLE",
        "artist": "ARTIST",
        "album_artist": "ALBUMARTIST",
        "album": "ALBUM",
        "year": "DATE",
        "track": "TRACKNUMBER",
        "track_total": "TRACKTOTAL",
        "disc": "DISCNUMBER",
        "disc_total": "DISCTOTAL",
        "genre": "GENRE",
        "lyrics": "LYRICS",
        "explicit": "ITUNESADVISORY",
        **PROVENANCE,
    }
    # Other names rips use for the same fields; read when the usual one is missing.
    ALIASES = {"track_total": "TOTALTRACKS", "disc_total": "TOTALDISCS", "lyrics": "UNSYNCEDLYRICS"}

    def __init__(self, audio: FLAC | OggVorbis | OggOpus) -> None:
        super().__init__()
        self.audio = audio

    def _values(self, key: str | None) -> list[str]:
        tags = self.audio.tags
        return list(tags.get(key, [])) if key and tags is not None else []

    def get(self, name: str) -> str | None:
        values = self._values(self.NAMES[name]) or self._values(self.ALIASES.get(name))
        if name in ("track", "disc", "track_total", "disc_total") and values:
            number, _, total = values[0].partition("/")
            if name.endswith("_total"):
                return values[0] if not total else None
            return number
        if name in ("track_total", "disc_total") and not values:
            number_values = self._values(self.NAMES[name.removesuffix("_total")])
            if number_values and "/" in number_values[0]:
                return number_values[0].partition("/")[2] or None
        return _joined(values)

    def set(self, name: str, text: str) -> None:
        self.remove(name)
        self.audio.tags[self.NAMES[name]] = [text]

    def remove(self, name: str) -> None:
        for key in (self.NAMES[name], self.ALIASES.get(name)):
            if key and key in self.audio.tags:
                del self.audio.tags[key]

    def set_pair(self, name: str, number: int | None, total: int | None) -> None:
        self.remove(name)
        self.remove(f"{name}_total")
        if number is not None:
            self.audio.tags[self.NAMES[name]] = [str(number)]
            if total:
                self.audio.tags[self.NAMES[f"{name}_total"]] = [str(total)]

    def _pictures(self) -> list[Picture]:
        if isinstance(self.audio, FLAC):
            return list(self.audio.pictures)
        pictures = []
        for value in self._values("METADATA_BLOCK_PICTURE"):
            try:
                pictures.append(Picture(base64.b64decode(value)))
            except Exception as exc:
                self.warnings.append(f"skipped a damaged picture ({exc})")
        return pictures

    def _store(self, pictures: list[Picture]) -> None:
        if isinstance(self.audio, FLAC):
            self.audio.clear_pictures()
            for picture in pictures:
                self.audio.add_picture(picture)
            return
        if "METADATA_BLOCK_PICTURE" in self.audio.tags:
            del self.audio.tags["METADATA_BLOCK_PICTURE"]
        if pictures:
            self.audio.tags["METADATA_BLOCK_PICTURE"] = [
                base64.b64encode(p.write()).decode("ascii") for p in pictures
            ]

    def get_cover(self) -> tuple[bytes, str] | None:
        for picture in self._pictures():
            if picture.type == PictureType.COVER_FRONT:
                return picture.data, _normal_mime(picture.mime) or _image_mime(picture.data, None)
        return None

    def set_cover(self, data: bytes, mime: str) -> None:
        picture = Picture()
        picture.type = PictureType.COVER_FRONT
        picture.mime = mime
        picture.data = data
        self._store([p for p in self._pictures() if p.type != PictureType.COVER_FRONT] + [picture])

    def remove_cover(self) -> None:
        self._store([p for p in self._pictures() if p.type != PictureType.COVER_FRONT])
