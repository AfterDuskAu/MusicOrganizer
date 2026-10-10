"""sharing (2026-10-04): the library, shared with a phone player at home.

A phone player on the same home network copies the owner's songs, videos, covers, lyrics
and playlists from here, and then plays its own copies. docs/ENGINE_API.md section 3 is
the agreement between the two ("format 1"). This module is the computer's half of it:
the list of everything, the files the list names, pairing, and (since 2026-10-10) taking
what was done on the phone.

What the owner was promised, and where each promise is kept:

- **Off until they switch it on.** Nothing here starts by itself. The app starts it
  (`sharing.set`) each time it opens with its Settings switch on; the engine keeps no
  "on" of its own, so an engine nobody asked never listens.
- **Read-only for the music.** A phone can ask for the list and for files, and can
  pair. A file is only ever opened for reading, the index is opened read-only, and
  nothing in the library's music folders is written: not a file, not a tag, not a
  cover, not a sidecar.
- **Three things come back from a phone, and nothing else** (the owner asked for them
  on 2026-10-10; until then nothing did): a playlist made on it, a song put in a
  playlist, and time spent listening to a song (`take_changes`). They change what
  `listening` keeps in state.json, written by `state` as every change to it is, and
  nothing more: a phone can't rename or delete a playlist, take a song out of one,
  or touch a favourite or a play count. What it sends is checked and bounded before
  any of it is kept, and none of its own words but a playlist's name is kept at all.
- **Paired once, by a six-digit code** the computer shows when asked (`sharing.pair`).
  A code is good once and for five minutes; five wrong codes and every code is refused
  for a minute. The device is given a long random key, which it sends with every
  request after that. What's kept of the key is its SHA-256, in `devices.json` in the
  app's settings folder (through `config`), for this profile.
- **Home network only, and only while the app is open.** A caller whose address isn't
  private (RFC 1918), link-local or this computer's own is dropped before it's
  answered, and so is a request sent to any name that isn't this computer at home.
  Nothing is ever sent to the internet, and no port is opened on the router. The
  listener lives inside `musicorg serve`, which stops when the app does.

**Privacy.** No address, computer name, pairing code or key is ever logged or put in an
error, and no key is given out over RPC. Every id in the list is a hash: a phone never
sees the library's own ids, a path, or where a song came from. The one thing it's told
is `discovered`: that a song is a download the owner hasn't moved into their main
library, which a phone lists apart from the owner's own songs.

Announcing the share on the network (Bonjour, `SERVICE_TYPE`) is the app's job: it has
the means without a new dependency, and an announcement made by the app ends with it.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from musicorg import browse, config, listening, naming
from musicorg import state as state_file
from musicorg.errors import MusicOrgError, NotFoundError, UserError
from musicorg.index import open_index
from musicorg.library import Library
from musicorg.normalize import compare_key

log = logging.getLogger(__name__)

FORMAT = 1
PREFIX = "/sync/v1/"
SERVICE_TYPE = "_homemusicsync._tcp"  # how the app announces a share (Bonjour)
# The port a share listens on, so that an address typed into a phone by hand keeps
# working. If another program has it, any free port is used instead (the app shows it).
PORT = 38521
PORT_TRIES = 8  # the engine before this one may still be letting go of the port
PORT_WAIT_S = 0.25
# Every address of this computer (IPv4). Callers are then checked one by one (`at_home`).
HOST = ""
CODE_LIFE_S = 300  # a pairing code is good for five minutes
WRONG_TRIES = 5  # this many wrong codes…
LOCK_S = 60  # …and every code is refused for this long
REBUILD_LEAST_S = 5.0  # a file the list doesn't name: the list is made again, this often at most
QUIET_S = 60.0  # between two log lines about callers turned away
REQUEST_WAIT_S = 20  # a caller that goes silent is let go
CHUNK = 64 * 1024
MAX_BODY = 4096  # a pairing request is a few dozen bytes
# What a device may tell the computer was done on it (2026-10-10), and how much at once.
ACCEPTS = ("changes",)  # said in `hello`: a phone sends changes only to a computer that says so
CHANGE_KINDS = frozenset({"playlist_new", "playlist_add", "listened"})  # Sync change kind
MAX_CHANGES_BODY = 1024 * 1024  # a megabyte: thousands of changes
MAX_CHANGES = 2000  # taken from one request; any after them wait for the device's next sync
MAX_DISCARD = 8 * MAX_CHANGES_BODY  # a body too long to take is read and thrown away, up to this
# The library's formats that format 1 has a name for. A song in another (Ogg, Opus) is
# left out of the list: a phone can't play it.
SOUND_TYPES = {".m4a": "m4a", ".mp3": "mp3", ".flac": "flac"}
VIDEO_TYPE = "mp4"
# Kept films and videos (outside the library) that a phone can play, by their ending.
# Anything else in those folders (an MKV, an AVI) isn't listed.
MOVIE_TYPES = {".mp4": "mp4", ".m4v": "m4v", ".mov": "mov"}
# Folders inside the Movies folder that are another program's own, never looked into.
NOT_OURS = frozenset({".fcpbundle", ".imovielibrary", ".tvlibrary", ".photoslibrary",
                      ".theater", ".localized", ".app"})  # fmt: skip
KEY_FIELD = "key_sha256"
_WHEN = "%Y-%m-%dT%H:%M:%SZ"
_RANGE = re.compile(r"bytes=(?P<first>\d{1,18})-(?P<last>\d{1,18})?")
_DEVICE_KIND = re.compile(r"[A-Za-z0-9 ]{1,20}")


# ---- who may ask -------------------------------------------------------------------------


def at_home(address: str) -> bool:
    """Whether an address can only be on a home network, or this computer itself:
    private (RFC 1918: 10.x, 172.16 to 172.31, 192.168.x), link-local (169.254.x) or
    loopback (127.x). IPv4 only: a share doesn't listen on anything else."""
    try:
        found = ipaddress.ip_address(address)
    except ValueError:
        return False
    if found.version != 4:
        return False
    first, second = found.packed[0], found.packed[1]
    return (
        first == 10
        or (first == 172 and 16 <= second <= 31)
        or (first == 192 and second == 168)
        or (first == 169 and second == 254)
        or first == 127
    )


def host_at_home(header: str | None) -> bool:
    """Whether the name a request was sent to can only be this computer at home: an
    address there, `localhost`, or a `.local` name. A web page elsewhere that points a
    name of its own at this computer is turned away by this."""
    if not header:
        return False
    host = header.strip().lower()
    if host.startswith("["):
        return False  # an IPv6 address: a share doesn't listen on one
    host = host.rsplit(":", 1)[0].rstrip(".")
    return host == "localhost" or host.endswith(".local") or at_home(host)


def own_address() -> str | None:
    """This computer's address on the home network, for the app's Settings to show (a
    phone that can't find the computer by itself is given it by hand). None when the
    computer isn't on a home network.

    Nothing is sent: pointing a UDP socket somewhere only makes the system say which of
    its addresses it would send from. Where it's pointed is an address set aside for
    examples (192.0.2.x), which no computer has."""
    far = str(ipaddress.IPv4Address(bytes((192, 0, 2, 1))))
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((far, 9))
            found = str(probe.getsockname()[0])
    except OSError:
        return None
    if not at_home(found) or ipaddress.ip_address(found).is_loopback:
        return None
    return found


# ---- the paired devices ------------------------------------------------------------------

_devices_lock = threading.Lock()


def devices() -> list[dict[str, Any]]:
    """The devices paired with this profile's library, as the app lists them. Nothing
    of a device's key is in it."""
    return [
        {"id": d["id"], "device": d["device"], "paired_at": d.get("paired_at"),
         "last_synced": d.get("last_synced")}
        for d in _saved()
    ]  # fmt: skip


def forget(device_id: str) -> None:
    """Unpair a device: its key stops working at once. The copies already on it stay."""
    with _devices_lock:
        saved = _saved()
        kept = [d for d in saved if d["id"] != device_id]
        if len(kept) == len(saved):
            raise NotFoundError("That device isn't paired any more.")
        config.save_devices(kept)
    log.info("sharing: a device was unpaired")


def _saved() -> list[dict[str, Any]]:
    return [
        d
        for d in config.load_devices()
        if isinstance(d.get("id"), str)
        and isinstance(d.get("device"), str)
        and isinstance(d.get(KEY_FIELD), str)
    ]


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()


def _device_with(header: str | None) -> dict[str, Any] | None:
    """The paired device whose key an `Authorization` header carries, if any."""
    if not header or not header.startswith("Bearer "):
        return None
    given = _digest(header[len("Bearer ") :].strip())
    found = None
    for device in _saved():  # every one is compared, however early a match comes
        if hmac.compare_digest(given, device[KEY_FIELD]):
            found = device
    return found


def _add_device(kind: str) -> str:
    """Pair a device, and return its key: the only time the key exists in full here."""
    key = secrets.token_urlsafe(32)
    with _devices_lock:
        saved = _saved()
        saved.append({"id": "d_" + secrets.token_hex(4), "device": kind, "paired_at": _now(),
                      "last_synced": None, KEY_FIELD: _digest(key)})  # fmt: skip
        config.save_devices(saved)
    return key


def _mark_synced(device_id: str) -> None:
    with _devices_lock:
        saved = _saved()
        for device in saved:
            if device["id"] == device_id:
                device["last_synced"] = _now()
        config.save_devices(saved)


def _device_kind(given: object) -> str:
    """What kind of device is asking ("iPhone", "iPad"), kept short and plain. It's the
    device's own word, shown in Settings: never trusted with anything."""
    return given if isinstance(given, str) and _DEVICE_KIND.fullmatch(given) else "Device"


def _now() -> str:
    return datetime.now(UTC).strftime(_WHEN)


# ---- the list ----------------------------------------------------------------------------


@dataclass(frozen=True)
class SharedFile:
    """A file the list names: what the list says of it, and where its bytes are."""

    id: str
    size: int
    version: str
    type: str
    path: Path  # the file itself, or the song or video a part is inside
    inside: str | None = None  # "cover" or "lyrics": taken out of that file's tags

    def listed(self) -> dict[str, Any]:
        return {"id": self.id, "size": self.size, "version": self.version, "type": self.type}


def library_info(lib: Library) -> dict[str, str]:
    """The library's name (its folder's), and an id that never changes for it: made from
    when the library was created, which its records have kept since `init` and which
    moving or renaming the folder doesn't change. A phone remembers the id, so it knows
    its library again when the computer's address or name changes."""
    born = lib.load_state().data.get("created_at")
    key = born if isinstance(born, str) and born else state_file.normalise_path(lib.root)
    return {"id": _name("lib", key), "name": lib.root.name}


def kept_media(folders: dict[str, Path]) -> list[tuple[dict[str, Any], SharedFile]]:
    """The films and videos the owner keeps outside the library (`config.media_folders`),
    as the list names them: `video` for what's in the videos folder, and for what's in
    the Movies folder `series` or `anime` inside its `Series` or `Anime` folder and
    `movie` anywhere else. Only looked at, never changed.

    An episode also says what it's part of and where it comes in it (`show`, `season`,
    `episode`), read from its folder and its name (`naming.kept_details`): nothing
    else records what a kept file is. Nothing more of where a file is kept is sent.

    Only real files of a kind a phone plays are listed. A link to somewhere else isn't
    followed, so nothing outside these two folders can be given out; hidden files and
    other programs' own folders (a Final Cut or TV library) are left alone."""
    found: list[tuple[dict[str, Any], SharedFile]] = []
    tops = {kind: folders.get(name) for kind, name in (("movie", "movies"), ("video", "media"))}
    for kind, top in tops.items():
        if top is None:
            continue
        # One kind's folder may be inside the other's (Videos, in Movies): what's in it
        # is listed once, as its own kind.
        others = {_folder_key(t) for k, t in tops.items() if k != kind and t is not None}
        if _folder_key(top) in others and kind == "video":
            continue  # the same folder for both: everything in it is a movie
        for folder, inside, names in os.walk(top, followlinks=False):
            inside[:] = sorted(
                d for d in inside
                if not d.startswith(".") and Path(d).suffix.lower() not in NOT_OURS
                and not (Path(folder) / d).is_symlink()
                and _folder_key(Path(folder) / d) not in others
            )  # fmt: skip
            for file_name in sorted(names):
                path = Path(folder) / file_name
                ending = MOVIE_TYPES.get(path.suffix.lower())
                if ending is None or file_name.startswith("."):
                    continue
                try:
                    about = path.lstat()
                except OSError:
                    continue
                if not stat.S_ISREG(about.st_mode) or about.st_size == 0:
                    continue
                inside_top = path.relative_to(top).as_posix()
                # Ids go by the folder the file was found under, as they always have: a
                # film listed before episodes were told apart keeps the id it had.
                key = f"{kind}:{inside_top}"
                main = SharedFile(_name("f", key), about.st_size, _stamp(about), ending, path)
                added = datetime.fromtimestamp(about.st_mtime, UTC).strftime(_WHEN)
                entry = {"id": _name("m", key), "title": path.stem, "kind": kind,
                         "added": added, "video": main.listed()}  # fmt: skip
                if kind == "movie":  # a film, or an episode of a series or an anime
                    entry.update(naming.kept_details(inside_top))
                found.append((entry, main))
    return found


def _folder_key(folder: Path) -> str:
    return os.path.normcase(os.path.abspath(folder))


def the_list(
    lib: Library,
    info: dict[str, str],
    remembered: dict[tuple[str, int, int], dict[str, Any]],
    *,
    films: dict[str, Path] | None = None,
) -> tuple[dict[str, Any], dict[str, SharedFile]]:
    """The list of everything (format 1), and every file it names, by id. `films`: the
    folders of kept films and videos to list too, when the owner has switched that on.

    Songs and videos are the library's files as the index has them; favourites, play
    counts, listening time, playlists and which downloads the owner moved into their
    main library come from `listening`. A file's details come from the index when the
    file hasn't changed since they were read; otherwise the file's tags are read now
    and kept in `remembered`, so that's done once."""
    heard = listening.get(lib)
    favourites = set(heard["favourites"])
    moved = set(heard["library"])
    with open_index(lib.paths, write=False) as index:
        rows = index.library_tracks()

    files: dict[str, SharedFile] = {}

    def listed(shared: SharedFile | None) -> dict[str, Any] | None:
        if shared is None:
            return None
        files[shared.id] = shared
        return shared.listed()

    tracks: list[dict[str, Any]] = []
    videos: list[tuple[dict[str, Any], tuple[str, str]]] = []
    ids: dict[str, str] = {}  # a song's id in the library → its id in the list
    named: dict[tuple[str, str], list[str]] = {}  # (title, artist) as compared → songs
    covers: dict[Path, SharedFile | None] = {}  # an album folder's cover.jpg, found once
    taken: set[str] = set()
    for row in rows:
        rel = row["rel_path"]
        path = lib.root.joinpath(*rel.split("/"))
        try:
            about = path.stat()
        except OSError:
            continue  # the index is a cache: a file that has gone just isn't listed
        video = naming.is_video_path(rel)
        kind = VIDEO_TYPE if video else SOUND_TYPES.get(path.suffix.lower())
        if kind is None or about.st_size == 0:
            continue
        # What the ids are made from: the library's own id for the file, which stays
        # the same when it's renamed or moved. A file without one (or a second file
        # with the same one) goes by its path.
        own = row.get("musicorg_id")
        key = own if isinstance(own, str) and own and own not in taken else f"path:{rel}"
        taken.add(key)

        when = (rel, about.st_size, about.st_mtime_ns)
        found = remembered.get(when)
        if found is None:
            found = remembered[when] = browse.details(row, path, about, video=video)

        title = found.get("title") or path.stem
        artist = found.get("artist") or found.get("album_artist") or naming.UNKNOWN_ARTIST
        length = row.get("duration_s")
        length = round(float(length), 3) if isinstance(length, int | float) else 0.0
        name_key = (compare_key(title), compare_key(artist))
        main = SharedFile(_name("f", key), about.st_size, _stamp(about), kind, path)
        own_cover = _from_tags(_name("c", key), path, found.get("cover_file"), "cover")

        if video:
            entry: dict[str, Any] = {"id": _name("v", key), "title": title, "artist": artist,
                                     "duration": length, "video": listed(main)}  # fmt: skip
            if own_cover is not None:  # a video's cover is its own picture, inside the file
                entry["cover"] = listed(own_cover)
            videos.append((entry, name_key))
            continue

        entry = {"id": _name("t", key), "title": title, "artist": artist}
        for ours, theirs in (("album", "album"), ("album_artist", "albumArtist"),
                             ("track", "trackNumber"), ("disc", "discNumber"),
                             ("year", "year"), ("genre", "genre")):  # fmt: skip
            if found.get(ours):
                entry[theirs] = found[ours]
        plays = heard["plays"].get(own) if isinstance(own, str) else None
        entry.update(
            duration=length,
            explicit=found.get("explicit") is True,
            favourite=own in favourites,
            playCount=plays["count"] if plays else 0,
        )
        if own == key:
            # The computer's own total, with what devices have told it added in. A file
            # that goes by its path has none: there's no id to keep its time by.
            entry["listened"] = heard["listened"].get(own, 0.0)
        if _is_download(row, found) and own not in moved:
            entry["discovered"] = True
        added = _when(found.get("acquired"))
        if added:
            entry["added"] = added
        entry["audio"] = listed(main)

        folder = path.parent
        if folder not in covers:  # the album folder's cover.jpg: one file for the album
            folder_rel = rel.rsplit("/", 1)[0]
            covers[folder] = _on_disk(
                _name("c", f"folder:{folder_rel}"), folder / naming.COVER_NAME, "jpg"
            )
        cover = covers[folder] or own_cover
        if cover is not None:
            entry["cover"] = listed(cover)
        words = _on_disk(_name("l", key), path.with_suffix(".lrc"), "lrc") or _from_tags(
            _name("l", key), path, found.get("lyrics_file"), "lyrics"
        )
        if words is not None:
            entry["lyrics"] = listed(words)

        tracks.append(entry)
        if isinstance(own, str) and own == key:
            ids[own] = entry["id"]
        named.setdefault(name_key, []).append(entry["id"])

    shown_videos = []
    for entry, name_key in videos:
        songs = named.get(name_key) or []
        if len(songs) == 1:  # the song a video belongs to, when it's clear which
            entry["track"] = songs[0]
        shown_videos.append(entry)

    playlists = [
        {"id": _name("p", p["id"]), "name": p["name"],
         "tracks": [ids[t] for t in p["track_ids"] if t in ids]}
        for p in heard["playlists"]
    ]  # fmt: skip
    movies: list[dict[str, Any]] = []
    for entry, main in kept_media(films) if films else []:
        files[main.id] = main
        movies.append(entry)
    body = json.dumps([tracks, shown_videos, playlists, movies], sort_keys=True, ensure_ascii=False)
    return {
        "format": FORMAT,
        "library": dict(info),
        "revision": hashlib.sha256(body.encode("utf-8", "surrogatepass")).hexdigest()[:16],
        "tracks": tracks,
        "videos": shown_videos,
        "playlists": playlists,
        "movies": movies,
    }, files


def _is_download(row: dict[str, Any], found: dict[str, Any]) -> bool:
    """Whether a song is one the computer found for its owner: downloaded from YouTube
    Music with no file of the owner's behind it (`MUSICORG_SOURCE` is `youtube_music`
    and there's no `MUSICORG_MATCH`). It's the rule the app lists Discover → Downloads
    by, and the one a download may be deleted by (`pipeline.plan_remove`). A rip that
    was replaced by a download, or kept with official details, has a match, so it's the
    owner's own. Read from what the library already records: nothing is written."""
    return row.get("source") == "youtube_music" and found.get("match") is None


def _name(kind: str, key: str) -> str:
    """An id for the list: a letter for what it names, and a hash of what it was made
    from. A phone is only ever given the hash."""
    digest = hashlib.sha256(f"{kind}\n{key}".encode("utf-8", "surrogatepass")).hexdigest()
    return f"{kind}-{digest[:24]}"


def _stamp(about: os.stat_result) -> str:
    """A file's version: it changes when the file is written again."""
    return f"{about.st_mtime_ns:x}-{about.st_size:x}"


def _on_disk(file_id: str, path: Path, kind: str) -> SharedFile | None:
    try:
        about = path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(about.st_mode) or about.st_size == 0:
        return None
    return SharedFile(file_id, about.st_size, _stamp(about), kind, path)


def _from_tags(file_id: str, path: Path, measured: object, part: str) -> SharedFile | None:
    """A cover or lyrics kept inside a file's tags, as a file of its own (`browse`
    measured it when it read the tags)."""
    if not isinstance(measured, dict):
        return None
    size, version, kind = measured.get("size"), measured.get("version"), measured.get("type")
    if not (isinstance(size, int) and size > 0 and isinstance(version, str) and version):
        return None
    if not isinstance(kind, str):
        return None
    return SharedFile(file_id, size, version, kind, path, inside=part)


def _when(value: object) -> str | None:
    """A date and time in the form the list uses, or None if it can't be read as one."""
    if not isinstance(value, str) or not value:
        return None
    try:
        found = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if found.tzinfo is None:
        found = found.replace(tzinfo=UTC)
    return found.astimezone(UTC).strftime(_WHEN)


# ---- what was done on a phone (2026-10-10) -------------------------------------------------


def take_changes(lib: Library, sent: list[Any]) -> tuple[list[str], dict[str, Any]]:
    """What a paired device says was done on it since it last said: a playlist made, a
    song put in a playlist, time spent listening to a song (section 3, "Changes").
    Returns the ids of the changes dealt with, for the answer, and what came of them
    (`listening.from_devices`).

    Nothing a device sends is kept as it came, bar a playlist's name. Each change is
    checked here and put in the library's own terms first: a song by its own id, found
    from the list's id for it (a hash); a playlist the same way; and the ids the device
    made up, for a change and for a playlist of its own, as short hashes.

    - **Dealt with** means done, or never going to be: a song or a playlist that isn't
      here any more, a change that came before, or one that makes no sense (a length
      of time that isn't one). The device forgets those.
    - **Left with the device**, to send again: a kind this engine doesn't know (a
      newer phone's), a change with no id to answer it by, and any past the first
      `MAX_CHANGES` of a request.
    """
    with open_index(lib.paths, write=False) as index:
        rows = index.library_tracks()
    # The way back from the list's ids to the library's own.
    songs = {_name("t", row["musicorg_id"]): row["musicorg_id"] for row in rows
             if isinstance(row.get("musicorg_id"), str) and row["musicorg_id"]}  # fmt: skip
    playlists = {_name("p", p["id"]): p["id"] for p in listening.get(lib)["playlists"]}

    dealt_with: list[str] = []
    understood: list[listening.DeviceChange] = []
    for change in sent[:MAX_CHANGES]:
        if not isinstance(change, dict):
            continue
        given, kind = change.get("id"), change.get("kind")
        if not isinstance(given, str) or not given:
            continue
        if not isinstance(kind, str) or kind not in CHANGE_KINDS:
            continue
        dealt_with.append(given)
        mark = _mark("change", given)
        track, where = change.get("track"), change.get("playlist")
        song = songs.get(track, "") if isinstance(track, str) else ""
        if not isinstance(where, str) or not where:
            where = None
        if kind == "playlist_new":
            name = change.get("name")
            if where and isinstance(name, str):
                understood.append(
                    listening.DeviceChange(mark, kind, theirs=_mark("playlist", where), name=name)
                )
        elif kind == "playlist_add":
            if song and where:
                ours = playlists.get(where, "")  # else one the device made up, or one that's gone
                theirs = "" if ours else _mark("playlist", where)
                understood.append(
                    listening.DeviceChange(mark, kind, song, playlist_id=ours, theirs=theirs)
                )
        else:  # listened
            seconds = change.get("seconds")
            if song and isinstance(seconds, int | float) and not isinstance(seconds, bool):
                understood.append(listening.DeviceChange(mark, kind, song, seconds=seconds))
    return dealt_with, listening.from_devices(lib, understood)


def _mark(kind: str, key: str) -> str:
    """What's kept of an id a device made up: a short hash of it."""
    return _name(kind, key)[-16:]


# ---- sharing, switched on ----------------------------------------------------------------


def refusal(error: str, message: str) -> dict[str, str]:
    return {"error": error, "message": message}


def status_off(lib: Library) -> dict[str, Any]:
    """What the app's Settings shows while sharing is off."""
    return {"on": False, "port": None, "address": None, "name": lib.root.name,
            "service": SERVICE_TYPE, "devices": devices(), "pairing": False,
            "pairing_seconds_left": 0, "films": False}  # fmt: skip


class Share:
    """One library being shared: the listener, the code on the screen, and the list."""

    def __init__(
        self,
        lib: Library,
        *,
        changed: Callable[[], None] | None = None,
        took: Callable[[], None] | None = None,
        films: bool = False,
        host: str | None = None,
        port: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.lib = lib
        self.info = library_info(lib)
        self._changed = changed
        self._took = took  # called when a device's changes changed what `listening` keeps
        # Whether kept films and videos (outside the library) are in the list too. Off
        # until the owner switches it on, apart from sharing itself.
        self.films = films
        self._host = HOST if host is None else host
        self._port = PORT if port is None else port
        self._clock = clock
        self._lock = threading.Lock()
        self._http: _Listener | None = None
        self._code: str | None = None
        self._code_until = 0.0
        self._wrong = 0
        self._locked_until = 0.0
        self._files: dict[str, SharedFile] = {}
        self._built_at: float | None = None
        self._remembered: dict[tuple[str, int, int], dict[str, Any]] = {}
        self._turned_away_at: float | None = None

    # -- on and off --

    @property
    def listening(self) -> bool:
        return self._http is not None

    @property
    def port(self) -> int | None:
        return self._http.server_port if self._http is not None else None

    def start(self) -> None:
        if self._http is not None:
            return
        self._http = _listen(self, self._host, self._port)
        threading.Thread(
            target=self._http.serve_forever,
            kwargs={"poll_interval": 0.2},
            name="sharing",
            daemon=True,
        ).start()
        log.info("sharing: switched on")

    def stop(self) -> None:
        """Stop listening, at once: a file on its way to a phone is cut off too."""
        with self._lock:
            listener, self._http = self._http, None
            self._code = None
        if listener is None:
            return
        listener.shutdown()
        listener.server_close()
        log.info("sharing: switched off")

    def status(self) -> dict[str, Any]:
        with self._lock:
            left = self._code_until - self._clock() if self._code else 0.0
        on = self.listening
        return {
            "on": on,
            "port": self.port,
            "address": own_address() if on else None,
            "name": self.info["name"],
            "service": SERVICE_TYPE,
            "devices": devices(),
            "pairing": left > 0,
            "pairing_seconds_left": max(0, round(left)),
            "films": self.films,
        }

    # -- pairing --

    def new_code(self) -> dict[str, Any]:
        """A fresh six-digit code for the computer's screen. Any code before it stops
        working."""
        if not self.listening:
            raise UserError("Switch sharing on first, then pair a device.")
        with self._lock:
            self._code = f"{secrets.randbelow(1_000_000):06d}"
            self._code_until = self._clock() + CODE_LIFE_S
            return {"code": self._code, "seconds": CODE_LIFE_S}

    def stop_pairing(self) -> None:
        """The code is taken off the screen: it no longer works."""
        with self._lock:
            self._code = None

    def pair(self, code: str, device: object) -> tuple[int, dict[str, Any]]:
        """A phone has sent a code: (the HTTP status, the answer's body)."""
        with self._lock:
            now = self._clock()
            if now < self._locked_until:
                return 429, refusal(
                    "too_many_tries", "Too many wrong codes. Wait a minute, then try again."
                )
            showing = self._code if self._code and now < self._code_until else None
            if showing is None or not hmac.compare_digest(code.encode(), showing.encode()):
                self._wrong += 1
                if self._wrong >= WRONG_TRIES:
                    self._wrong = 0
                    self._locked_until = now + LOCK_S
                if showing is None:
                    return 403, refusal(
                        "wrong_code",
                        "The computer isn't showing a pairing code now. Ask it to pair a "
                        "device, then type the code it shows.",
                    )
                return 403, refusal(
                    "wrong_code", "That code isn't the one on the computer's screen."
                )
            self._wrong = 0
            self._code = None  # a code works once
        kind = _device_kind(device)
        key = _add_device(kind)
        log.info("sharing: a device was paired (%s)", kind)
        self._tell()
        return 200, {"key": key}

    # -- the list and its files --

    def make_list(self) -> dict[str, Any]:
        """The list of everything, made now. The files it names are remembered, to be
        given out when they're asked for."""
        found, files = the_list(
            self.lib, self.info, self._remembered,
            films=config.media_folders() if self.films else None,
        )  # fmt: skip
        with self._lock:
            self._files = files
            self._built_at = self._clock()
        return found

    def file(self, file_id: str) -> SharedFile | None:
        """The file a list named by this id, or None. Only a file the list names is
        ever given out: no path comes from a phone."""
        with self._lock:
            found = self._files.get(file_id)
            stale = self._built_at is None or self._clock() - self._built_at > REBUILD_LEAST_S
        if found is None and stale:
            # The phone's list may be newer than the one remembered here (the engine
            # has started again since), so the list is made again, but not every time.
            self.make_list()
            with self._lock:
                found = self._files.get(file_id)
        return found

    def synced(self, device: dict[str, Any]) -> None:
        _mark_synced(device["id"])
        self._tell()

    # -- what was done on a device --

    @property
    def accepts(self) -> list[str]:
        """What this share takes from a device besides its requests, as `hello` says it.
        Changes are taken only by an engine that holds the library's lock, as the one
        the app starts always does: nothing else may write the library's records."""
        return list(ACCEPTS) if self.lib.writable else []

    def take(self, sent: list[Any]) -> list[str]:
        """A paired device's changes (`take_changes`): the ids of the ones dealt with.
        The app is told when any of them changed a playlist or a song's listening time."""
        dealt_with, done = take_changes(self.lib, sent)
        if done["playlists"] or done["songs"] or done["seconds"]:
            log.info(
                "sharing: a device's changes were taken (playlists made: %d, songs put in "
                "playlists: %d, minutes of listening: %d)",
                done["playlists"], done["songs"], round(done["seconds"] / 60),
            )  # fmt: skip
            if self._took is not None:
                self._took()
        return dealt_with

    def turned_away(self) -> None:
        """A caller from outside the home network was dropped. Said in the log, without
        its address, and not for every one of a run of them."""
        with self._lock:
            now = self._clock()
            recent = self._turned_away_at is not None and now - self._turned_away_at < QUIET_S
            if not recent:
                self._turned_away_at = now
        if not recent:
            log.warning("sharing: turned away a caller that isn't on the home network")

    def _tell(self) -> None:
        if self._changed is not None:
            self._changed()


def _listen(share: Share, host: str, port: int) -> _Listener:
    for _ in range(PORT_TRIES if port else 1):
        try:
            return _Listener((host, port), share)
        except OSError:
            time.sleep(PORT_WAIT_S)
    try:
        return _Listener((host, 0), share)  # any free port
    except OSError as exc:
        raise UserError(
            "Sharing couldn't be switched on: this computer wouldn't open a port for it "
            f"({exc.strerror or 'no reason given'})."
        ) from None


class _Listener(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows "reuse" would let a second program listen on the same port.
    allow_reuse_address = os.name != "nt"

    def __init__(self, address: tuple[str, int], share: Share) -> None:
        super().__init__(address, _Handler)
        self.share = share

    def verify_request(self, request: Any, client_address: Any) -> bool:
        """Only a caller at home is answered. Anyone else is dropped without a word."""
        if at_home(str(client_address[0])):
            return True
        self.share.turned_away()
        return False

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Into the log, without the caller's address. A phone dropping a connection
        (it went to sleep, or left the Wi-Fi) is normal."""
        import sys

        exc = sys.exc_info()[1]
        if isinstance(exc, ConnectionError | TimeoutError):
            log.debug("sharing: a device dropped the connection")
        else:
            log.warning("sharing: a request failed", exc_info=True, extra={"console": False})


class _Handler(BaseHTTPRequestHandler):
    server: _Listener
    server_version = "HomeMusicSync/1"
    sys_version = ""  # nothing about this computer's software
    timeout = REQUEST_WAIT_S

    def log_message(self, format: str, *args: Any) -> None:
        # The path and the status only: never the caller's address or a header.
        log.debug("sharing: %s %s", self.command, args[1] if len(args) > 1 else "")

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Every answer is JSON, a request that couldn't be read included."""
        self.close_connection = True
        self._answer(code, refusal("bad_request", "The computer didn't understand that request."))

    # -- routing --

    def do_GET(self) -> None:
        share = self.server.share
        path = urlsplit(self.path).path
        if not self._welcome():
            return
        if path == PREFIX + "hello":
            paired = _device_with(self.headers.get("Authorization")) is not None
            self._answer(200, {"format": FORMAT, "library": dict(share.info), "paired": paired,
                               "accepts": share.accepts})  # fmt: skip
            return
        if not path.startswith(PREFIX):
            self._answer(404, refusal("not_found", "There's nothing at that address."))
            return
        device = _device_with(self.headers.get("Authorization"))
        if device is None:
            self._answer(401, refusal("not_paired", "This device isn't paired with the computer."))
            return
        if path == PREFIX + "library":
            try:
                found = share.make_list()
            except (MusicOrgError, OSError) as exc:
                log.warning("sharing: the list couldn't be made: %s", exc)
                self._answer(500, refusal(
                    "unavailable", "The computer couldn't read its library just now. Try again."
                ))  # fmt: skip
                return
            share.synced(device)
            log.info("sharing: a device was given the list (%d songs, %d videos, %d films)",
                     len(found["tracks"]), len(found["videos"]), len(found["movies"]))  # fmt: skip
            self._answer(200, found)
            return
        if path.startswith(PREFIX + "files/"):
            try:
                shared = share.file(unquote(path[len(PREFIX + "files/") :]))
            except (MusicOrgError, OSError):
                shared = None
            if shared is None or not self._send(shared):
                self._answer(
                    404, refusal("not_found", "The computer doesn't have that file any more.")
                )
            return
        self._answer(404, refusal("not_found", "There's nothing at that address."))

    def do_POST(self) -> None:
        if not self._welcome():
            return
        path = urlsplit(self.path).path
        if path == PREFIX + "changes":
            self._changes()
            return
        if path != PREFIX + "pair":
            self._answer(404, refusal("not_found", "There's nothing at that address."))
            return
        wrong = refusal("wrong_code", "That code isn't the one on the computer's screen.")
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            self._answer(403, wrong)
            return
        try:
            length = max(0, min(int(self.headers.get("Content-Length") or 0), MAX_BODY))
            asked = json.loads(self.rfile.read(length) or b"{}")
            code, device = str(asked.get("code", "")), asked.get("device")
        except (ValueError, AttributeError):
            self._answer(403, wrong)
            return
        status, body = self.server.share.pair(code, device)
        self._answer(status, body)

    def _changes(self) -> None:
        """A paired device says what was done on it (2026-10-10). The body is read before
        anything is answered, so that the answer isn't lost behind what's left of it."""
        share = self.server.share
        body = self._body(MAX_CHANGES_BODY)
        if _device_with(self.headers.get("Authorization")) is None:
            self._answer(401, refusal("not_paired", "This device isn't paired with the computer."))
            return
        if "changes" not in share.accepts:  # as `hello` said: nothing is taken here
            self._answer(404, refusal("not_found", "There's nothing at that address."))
            return
        if body is None:
            self._answer(413, refusal(
                "bad_request", "That's more than the computer takes at once."
            ))  # fmt: skip
            return
        try:
            sent = json.loads(body or b"{}").get("changes", [])
        except (ValueError, AttributeError, RecursionError):
            self._answer(400, refusal("bad_request", "That isn't a list of changes."))
            return
        try:
            taken = share.take(sent if isinstance(sent, list) else [])
        except (MusicOrgError, OSError) as exc:
            log.warning("sharing: a device's changes couldn't be taken: %s", exc)
            self._answer(500, refusal(
                "unavailable", "The computer couldn't keep those changes just now. Try again."
            ))  # fmt: skip
            return
        self._answer(200, {"taken": taken})

    def _body(self, most: int) -> bytes | None:
        """The request's body, or None if it's longer than `most`: that one is read and
        thrown away instead (up to a point), and nothing of it is looked at."""
        try:
            length = max(0, int(self.headers.get("Content-Length") or 0))
        except ValueError:
            length = 0
        if length <= most:
            return self.rfile.read(length)
        self.close_connection = True
        left = min(length, MAX_DISCARD)
        while left > 0:
            chunk = self.rfile.read(min(CHUNK, left))
            if not chunk:
                break
            left -= len(chunk)
        return None

    # -- the checks --

    def _welcome(self) -> bool:
        """Whether this request is one to answer: sharing is still on, it was sent to
        this computer by its home name or address, and no web page is behind it."""
        if not self.server.share.listening:
            self.close_connection = True
            return False
        if not host_at_home(self.headers.get("Host")) or self.headers.get("Origin"):
            self._answer(
                403, refusal("not_home", "This computer only shares music on its home network.")
            )
            return False
        return True

    # -- answers --

    def _answer(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send(self, shared: SharedFile) -> bool:
        """Send a file's bytes. False (and nothing sent) if it isn't what the list said
        any more: gone, or written again since. The phone's next sync brings the new one."""
        if shared.inside is not None:
            try:
                data = browse.inside(shared.path, shared.inside)
            except (MusicOrgError, OSError):
                return False
            if data is None or len(data) != shared.size:
                return False
            if hashlib.sha256(data).hexdigest()[:16] != shared.version:
                return False
            part = self._part(shared)
            if part is None:
                return True  # a part that isn't in the file: refused, and answered
            self._begin(shared, part)
            self.wfile.write(data[part[0] : part[1] + 1])
            return True
        try:
            f = open(shared.path, "rb")
        except OSError:
            return False
        with f:
            about = os.fstat(f.fileno())
            if about.st_size != shared.size or _stamp(about) != shared.version:
                return False
            part = self._part(shared)
            if part is None:
                return True
            self._begin(shared, part)
            f.seek(part[0])
            left = part[1] - part[0] + 1
            while left > 0 and self.server.share.listening:
                chunk = f.read(min(CHUNK, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)
        return True

    def _part(self, shared: SharedFile) -> tuple[int, int] | None:
        """Which bytes to send, first and last: all of the file, or the part a phone
        asked for to carry on a file that was cut off (`Range: bytes=<from>-`, with
        `If-Range` naming the version it has the start of; a different version gets
        the whole file, never new bytes joined to old). None: it asked for a part that
        isn't in the file, and has been told so (416)."""
        whole = (0, shared.size - 1)
        asked = (self.headers.get("Range") or "").strip()
        if not asked:
            return whole
        has = (self.headers.get("If-Range") or "").strip().strip('"')
        if has and has != shared.version:
            return whole
        found = _RANGE.fullmatch(asked)
        if found is None:
            return whole  # several parts, or the last so-many bytes: the whole file instead
        first = int(found["first"])
        last = min(int(found["last"]), shared.size - 1) if found["last"] else shared.size - 1
        if first >= shared.size or first > last:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{shared.size}")
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return None
        return first, last

    def _begin(self, shared: SharedFile, part: tuple[int, int]) -> None:
        first, last = part
        whole = (first, last) == (0, shared.size - 1)
        self.send_response(200 if whole else 206)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(last - first + 1))
        if not whole:
            self.send_header("Content-Range", f"bytes {first}-{last}/{shared.size}")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", f'"{shared.version}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
