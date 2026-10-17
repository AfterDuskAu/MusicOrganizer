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
- **What's done on a phone comes back, and nothing else** (`take_changes`). The owner
  asked for the first three things on 2026-10-10 (until then nothing came back): a
  playlist made on it, a song put in a playlist, and time spent listening to a song.
  For two more on 2026-10-11: a heart put on a song or taken off, and a song played
  to its end. And the same day for the rest of what can be done to a playlist: a new
  name, a song taken out, a new order, and the playlist deleted. They change what
  `listening` keeps in state.json (playlists, favourites, play counts, listening
  time), written by `state` as every change to it is, and nothing more: a song is
  never deleted or changed, only the lists it's in. What a phone sends is checked and
  bounded before any of it is kept, and none of its own words but a playlist's name
  is kept at all.
- **Paired once, by a six-digit code** the computer shows when asked (`sharing.pair`).
  A code is good once and for five minutes; five wrong codes and every code is refused
  for a minute. The device is given a long random key, which it sends with every
  request after that. What's kept of the key is its SHA-256, in `devices.json` in the
  app's settings folder (through `config`), for this profile. A device that pairs
  again takes its old place in that list instead of a second one (2026-10-11): it's
  known by the key it still has, or by an id of its own making that it sends with the
  code, of which only the SHA-256 is kept too. **A device may unpair itself**
  (2026-10-11, the owner's choice): a phone that forgets the library says so, its own
  key stops working and its line goes from the app's list. It can do nothing to
  another device's pairing.
- **Home network only, and only while the app is open.** A caller whose address isn't
  private (RFC 1918), link-local or this computer's own is dropped before it's
  answered, and so is a request sent to any name that isn't this computer at home.
  Nothing is ever sent to the internet, and no port is opened on the router. The
  listener lives inside `musicorg serve`, which stops when the app does.
- **Awake while a device syncs** (2026-10-10). A paired device asking for the list or
  a file, or sending its changes, keeps the computer from going to sleep until it has
  asked for nothing for two minutes (`Share.busy`, as the queue does while it
  downloads). Nobody without a key can, and switching sharing off ends it at once.
- **What's watched, and films, for a video app** (2026-10-16, the owner's words in
  docs/ENGINE_API.md section 3). A paired device of **any** profile on this computer
  may hand over its own watching (its Watchlist, what it watched, where it got to)
  and ask for its profile's (`watching.json`, through `config`: never the library or
  `state.json`; the app adds what came to its own records, and nothing is ever taken
  away); list the films in the Movies folder and fetch one; and send a film of its own,
  which arrives a part at a time in the app's cache (`fileops.write_upload_part`) and
  is then copied into the Movies folder by `fileops.keep_media`, never over a file
  that's there. Films go either way only while the app's films switch is on. The music
  requests still answer only the devices of the profile whose library is shared.
- **Films for adults, for a phone player that asks** (2026-10-17, the owner: "can be
  synced to [the phone player] ... it requires clicking download on the phone for it to
  download, just like how movies work"). The Movies folder's `18+` folder is listed, as
  kind `adult`, only to a phone player whose request for the list says `?adult=1`, only
  while the films switch is on and the profile open in the app isn't a child's; and its
  files are given only to the device whose list named them. Never to a video app (the
  films list and its files leave them out), and a device can't send one.

**Privacy.** No address, computer name, pairing code or key is ever logged or put in an
error, and no key is given out over RPC. Every id in the list is a hash: a phone never
sees the library's own ids, a path, or where a song came from. The one thing it's told
is `discovered`: that a song is a download the owner hasn't moved into their main
library, which a phone lists apart from the owner's own songs.

Announcing the share on the network (Bonjour, `SERVICE_TYPE`) is the app's job: it has
the means without a new dependency, and an announcement made by the app ends with it.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import socket
import stat
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from musicorg import browse, config, fileops, listening, naming, tags, tools
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
# While a paired device syncs, the computer is kept from going to sleep (2026-10-10): a
# film takes minutes to copy, and a computer that slept half-way cut the sync off.
AWAKE_QUIET_S = 120.0  # it may sleep again once the device has asked for nothing this long
AWAKE_CHECK_S = 1.0  # how often that's looked at
REQUEST_WAIT_S = 20  # a caller that goes silent is let go
CHUNK = 64 * 1024
MAX_BODY = 4096  # a pairing request is a few dozen bytes
# What a device may tell the computer was done on it (2026-10-10), and how much at once.
# Said in `hello`: a phone sends its changes only to a computer that says `changes`, a
# heart or a play only to one that says so too, and the rest of a playlist's changes (a
# new name, a song taken out, a new order, the playlist deleted) only to one that says
# `playlists` (all 2026-10-11).
ACCEPTS = ("changes", "favourites", "plays", "playlists")
# Also said in `hello`, by any share: a device may say it has forgotten this library.
FORGET = "forget"
PLAYLIST_EDITS = frozenset(
    {"playlist_remove", "playlist_rename", "playlist_order", "playlist_delete"}
)
CHANGE_KINDS = PLAYLIST_EDITS | {  # Sync change kind
    "playlist_new", "playlist_add", "listened", "favourite", "played"
}  # fmt: skip
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
# The films kept from the Finder for adults (2026-10-17): the Movies folder's `18+` folder,
# kind `adult`. A phone player is told of them only when it asks (`?adult=1` on the list),
# and only while the profile open on the computer isn't a child's; a video app never is.
# One made before this would have put them with its movies, since a kind it doesn't know
# is a movie to it.
ADULT_QUERY = "adult"
# Folders inside the Movies folder that are another program's own, never looked into.
NOT_OURS = frozenset({".fcpbundle", ".imovielibrary", ".tvlibrary", ".photoslibrary",
                      ".theater", ".localized", ".app"})  # fmt: skip
KEY_FIELD = "key_sha256"
ITS_ID_FIELD = "its_id_sha256"  # of the id a device made up for itself, if it sent one
MAX_ITS_ID = 100  # characters of such an id; a longer one isn't taken for one
_WHEN = "%Y-%m-%dT%H:%M:%SZ"
_RANGE = re.compile(r"bytes=(?P<first>\d{1,18})-(?P<last>\d{1,18})?")
_DEVICE_KIND = re.compile(r"[A-Za-z0-9 ]{1,20}")

# What's watched, and films, for a video app (2026-10-16). Said in `hello`: `watching` by
# every share, and `films` while the films switch is on. Both before `forget`.
WATCHING = "watching"
FILMS = "films"
MAX_WATCHING_BODY = 8 * 1024 * 1024  # 8 MB: a device's whole watching
MOST_TITLES = 5000  # on a Watchlist
MOST_WATCHED = 50_000  # films and episodes watched
MOST_PROGRESS = 5000  # places got to
MOST_ID = 100  # characters of a plugin's id for a title or an episode
MOST_NAME = 300  # characters of a title's name (and of an episode's `detail`)
MOST_POSTER = 1000  # characters of a poster's address
MOST_GENRES = 20
MOST_GENRE = 50
MOST_YEAR = 20  # "2019", or "2019–2021" for a series
MOST_SECONDS = 7 * 24 * 3600  # a place in a title, or its length: a week at most
EARLIEST_YEAR = 1970  # a time before this can't be when anything was watched on a device
TITLE_TYPES = ("movie", "series")
_POSTER_SHAPE = re.compile(r"[A-Za-z]{1,20}")
# A film a device sends to be copied into the Movies folder: never into its 18+ folder.
UPLOAD_KINDS = ("movie", "series", "anime")
UPLOAD_TYPES = ("mp4", "m4v", "mov", "mkv", "avi", "webm")
MOST_UPLOAD = 200 * 1024**3  # 200 GB
MOST_PART = 256 * 1024 * 1024  # 256 MB in one request
# A part refused unread is read and thrown away first (so the device gets the answer, not
# a broken connection), up to this much.
MAX_PART_DISCARD = 257 * 1024 * 1024
UPLOAD_KEEP_S = 2 * 24 * 3600  # what has arrived waits this long after its last part
# A film from a device never fills a disk to its last gigabyte: the library's own records
# (state.json, the index) still need room to be saved (`tools.room_for`).
ROOM_TO_SPARE = tools.ROOM_TO_SPARE
KEEP_CHECK_S = 30.0  # while a film is copied into the Movies folder: still syncing, this often
_UPLOAD_ID = re.compile(r"u-[0-9a-f]{24}")
_CONTENT_RANGE = re.compile(r"bytes (?P<first>\d{1,15})-(?P<last>\d{1,15})/(?P<size>\d{1,15})")
NOT_SHARED = "This computer isn't sharing its films just now: that's switched on in its Settings."


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


def forget(device_id: str, *, profile: str | None = None) -> None:
    """Unpair a device of this profile's (or of `profile`'s): its key stops working at
    once. The copies already on it stay."""
    with _devices_lock:
        saved = _saved(profile)
        kept = [d for d in saved if d["id"] != device_id]
        if len(kept) == len(saved):
            raise NotFoundError("That device isn't paired any more.")
        config.save_devices(kept, profile=profile)
    log.info("sharing: a device was unpaired")


def _saved(profile: str | None = None) -> list[dict[str, Any]]:
    """The devices paired with this profile (or with `profile`)."""
    return [d for d in config.load_devices(profile) if _whole(d)]


def _whole(device: dict[str, Any]) -> bool:
    return (
        isinstance(device.get("id"), str)
        and isinstance(device.get("device"), str)
        and isinstance(device.get(KEY_FIELD), str)
    )


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()


def _device_with(header: str | None) -> dict[str, Any] | None:
    """The device paired with this profile whose key an `Authorization` header carries,
    if any. The music's requests answer only these."""
    if not header or not header.startswith("Bearer "):
        return None
    given = _digest(header[len("Bearer ") :].strip())
    found = None
    for device in _saved():  # every one is compared, however early a match comes
        if hmac.compare_digest(given, device[KEY_FIELD]):
            found = device
    return found


def _any_device_with(header: str | None) -> tuple[str, dict[str, Any]] | None:
    """The device paired with **any** profile on this computer whose key an
    `Authorization` header carries, with the id of the profile it belongs to (the one
    that was open when it was paired). Only a video app's requests answer these
    (2026-10-16), each for its own profile's records and the computer's films."""
    if not header or not header.startswith("Bearer "):
        return None
    given = _digest(header[len("Bearer ") :].strip())
    found = None
    for profile, listed in config.load_all_devices().items():
        for device in listed:  # every one is compared, however early a match comes
            if _whole(device) and hmac.compare_digest(given, device[KEY_FIELD]):
                found = (profile, device)
    return found


def _add_device(kind: str, *, header: str | None = None, its_id: object = None) -> tuple[str, bool]:
    """Pair a device, and return its key (the only time the key exists in full here)
    and whether it was paired before.

    **A device that was paired before takes its old place, not a second one**
    (2026-10-11: a phone that forgot the library and paired again was listed twice).
    It's known in either of two ways, both optional and both the device's to send with
    the code:

    - the key it was given before, if it still has it (`header`, an `Authorization`
      header as on every other request);
    - an id of its own making (`its_id`: random, made once by the device, the same
      each time it pairs). Only its SHA-256 is kept, beside the key's.

    Its old key stops working: the new one takes its place. A device that sends
    neither is a new device, as before."""
    key = secrets.token_urlsafe(32)
    mark = _digest(its_id) if isinstance(its_id, str) and 0 < len(its_id) <= MAX_ITS_ID else None
    with _devices_lock:
        saved = _saved()
        holder = _device_with(header)
        before = [
            d for d in saved
            if (holder is not None and d["id"] == holder["id"])
            or (mark is not None and d.get(ITS_ID_FIELD) == mark)
        ]  # fmt: skip
        entry = {
            "id": before[0]["id"] if before else "d_" + secrets.token_hex(4),
            "device": kind,
            "paired_at": _now(),
            "last_synced": before[0].get("last_synced") if before else None,
            KEY_FIELD: _digest(key),
        }
        its = mark or (before[0].get(ITS_ID_FIELD) if before else None)
        if isinstance(its, str):
            entry[ITS_ID_FIELD] = its
        if before:
            # Its old place in the list; and were it there twice already, once now.
            twice = {d["id"] for d in before[1:]}
            saved = [entry if d["id"] == entry["id"] else d for d in saved if d["id"] not in twice]
        else:
            saved.append(entry)
        config.save_devices(saved)
    return key, bool(before)


def _mark_synced(device_id: str, profile: str | None = None) -> None:
    with _devices_lock:
        saved = _saved(profile)
        for device in saved:
            if device["id"] == device_id:
                device["last_synced"] = _now()
        config.save_devices(saved, profile=profile)


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
    adult: bool = False  # a film in the Movies folder's 18+ folder (2026-10-17)

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
    the Movies folder `series` or `anime` inside its `Series` or `Anime` folder, `adult`
    inside its `18+` folder (2026-10-17; marked `adult`, for the callers to leave out
    unless they may give them) and `movie` anywhere else. Only looked at, never changed.

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
                # A film, or an episode of a series or an anime, or a film for adults.
                about_it = naming.kept_details(inside_top) if kind == "movie" else {}
                adult = about_it.get("kind") == naming.KEPT_ADULT
                main = SharedFile(
                    _name("f", key), about.st_size, _stamp(about), ending, path, adult=adult
                )
                added = datetime.fromtimestamp(about.st_mtime, UTC).strftime(_WHEN)
                entry = {"id": _name("m", key), "title": path.stem, "kind": kind,
                         "added": added, "video": main.listed()}  # fmt: skip
                entry.update(about_it)
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
    adult: bool = False,
) -> tuple[dict[str, Any], dict[str, SharedFile]]:
    """The list of everything (format 1), and every file it names, by id. `films`: the
    folders of kept films and videos to list too, when the owner has switched that on.
    `adult`: the films in the Movies folder's 18+ folder too (kind `adult`, 2026-10-17),
    when the phone asked for them and may have them (`Share.make_list` decides); without
    it none of them is named, and the revision is what it would be without them.

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
        if main.adult and not adult:
            continue
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
    Music or SoundCloud with no file of the owner's behind it (`MUSICORG_SOURCE` is
    `youtube_music` or `soundcloud`
    and there's no `MUSICORG_MATCH`). It's the rule the app lists Discover → Downloads
    by, and the one a download may be deleted by (`pipeline.plan_remove`). A rip that
    was replaced by a download, or kept with official details, has a match, so it's the
    owner's own. Read from what the library already records: nothing is written."""
    return row.get("source") in tags.DOWNLOAD_SOURCES and found.get("match") is None


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
    """What a paired device says was done on it since it last said: a playlist made,
    changed or deleted, a song put in one or taken out, time spent listening to a song,
    a heart, a play (section 3, "Changes").
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
        track, where, at = change.get("track"), change.get("playlist"), change.get("at")
        song = songs.get(track, "") if isinstance(track, str) else ""
        at = at if isinstance(at, str) else ""
        if not isinstance(where, str) or not where:
            where = None
        # A playlist is one of the library's, by the list's id for it; else it's one the
        # device made up an id for, or one that's gone.
        ours = playlists.get(where, "") if where else ""
        theirs = _mark("playlist", where) if where and not ours else ""
        if kind == "playlist_new":
            name = change.get("name")
            if where and isinstance(name, str):
                understood.append(
                    listening.DeviceChange(mark, kind, theirs=_mark("playlist", where), name=name)
                )
        elif kind in ("playlist_add", "playlist_remove"):
            if song and where:
                understood.append(
                    listening.DeviceChange(mark, kind, song, playlist_id=ours, theirs=theirs, at=at)
                )
        elif kind == "playlist_rename":
            name = change.get("name")
            if where and isinstance(name, str):
                understood.append(
                    listening.DeviceChange(
                        mark, kind, playlist_id=ours, theirs=theirs, name=name, at=at
                    )
                )
        elif kind == "playlist_order":
            order = change.get("tracks")
            if where and isinstance(order, list):
                tracks = tuple(
                    songs[given]
                    for given in order[: listening.MAX_PLAYLIST_SONGS]
                    if isinstance(given, str) and given in songs
                )
                understood.append(
                    listening.DeviceChange(
                        mark, kind, playlist_id=ours, theirs=theirs, tracks=tracks, at=at
                    )
                )
        elif kind == "playlist_delete":
            if where:
                understood.append(
                    listening.DeviceChange(mark, kind, playlist_id=ours, theirs=theirs, at=at)
                )
        elif kind == "listened":
            seconds = change.get("seconds")
            if song and isinstance(seconds, int | float) and not isinstance(seconds, bool):
                understood.append(listening.DeviceChange(mark, kind, song, seconds=seconds))
        else:  # favourite, played
            on = change.get("on")
            if song and (kind == "played" or isinstance(on, bool)):
                understood.append(listening.DeviceChange(mark, kind, song, on=on is True, at=at))
    return dealt_with, listening.from_devices(lib, understood)


def _mark(kind: str, key: str) -> str:
    """What's kept of an id a device made up: a short hash of it."""
    return _name(kind, key)[-16:]


# ---- what's watched, for a video app (2026-10-16) -------------------------------------------
#
# A profile's watching is three lists (docs/ENGINE_API.md section 3): the titles on its
# Watchlist, what has been watched, and where each title was got to. The app gives the
# engine each profile's (`watching.set`) for a paired device to have; a device sends its
# own, which waits here until the app takes it (`watching.sent`, `watching.done`) and adds
# it to the profile's records. Both are kept in watching.json, by `config`. The engine
# never merges them itself, and never touches the library or state.json for them.

WATCHING_LISTS = ("watchlist", "watched", "progress")
_watching_lock = threading.Lock()


class WatchingError(UserError):
    """What a device (or the app) sent isn't a watching the computer takes: `status` is
    the answer's (400: it can't be read; 413: there's more than is taken at once)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def check_watching(sent: object, *, now: datetime | None = None) -> dict[str, list[dict[str, Any]]]:
    """A watching, as the computer keeps it: only the fields the agreement names, each
    checked and cut to its length. An entry that can't be read is left out (a title with
    no id or name, a time that isn't one); a field that can't be is left off its entry
    (a poster that isn't an `https` address). A title is on the Watchlist, and an id
    watched, once (the first); of two places for one title the later is kept. A time
    later than `now` counts as now, so a device whose clock is wrong can't win forever.

    WatchingError when it isn't a watching at all (400), or has more in a list than is
    taken at once (413): then nothing of it is kept."""
    if not isinstance(sent, dict):
        raise WatchingError(400, "That isn't a list of what's been watched.")
    most = {"watchlist": (MOST_TITLES, "titles on a Watchlist"),
            "watched": (MOST_WATCHED, "films and episodes watched"),
            "progress": (MOST_PROGRESS, "places got to")}  # fmt: skip
    lists: dict[str, list[Any]] = {}
    for field in WATCHING_LISTS:
        given = sent.get(field)
        if given is None:
            given = []
        if not isinstance(given, list):
            raise WatchingError(400, "That isn't a list of what's been watched.")
        count, what = most[field]
        if len(given) > count:
            raise WatchingError(
                413, f"That's more than the computer takes at once: {count:,} {what} at most."
            )
        lists[field] = given
    moment = (now or datetime.now(UTC)).astimezone(UTC).strftime(_WHEN)

    watchlist: dict[str, dict[str, Any]] = {}
    for one in lists["watchlist"]:
        title = _title(one)
        if title is not None and title["id"] not in watchlist:
            watchlist[title["id"]] = title
    watched: dict[str, dict[str, Any]] = {}
    for one in lists["watched"]:
        if isinstance(one, dict):
            seen, at = _an_id(one.get("id")), _moment(one.get("at"), moment)
            if seen is not None and at is not None and seen not in watched:
                watched[seen] = {"id": seen, "at": at}
    progress: dict[str, dict[str, Any]] = {}
    for one in lists["progress"]:
        line = _progress(one, moment)
        if line is not None and (
            line["id"] not in progress or line["at"] > progress[line["id"]]["at"]
        ):
            progress[line["id"]] = line
    return {"watchlist": list(watchlist.values()), "watched": list(watched.values()),
            "progress": list(progress.values())}  # fmt: skip


def _title(one: object) -> dict[str, Any] | None:
    """A film or series as the app's lists have it (a Title), or None."""
    if not isinstance(one, dict):
        return None
    title_id, name, kind = _an_id(one.get("id")), _text(one.get("name"), MOST_NAME), _kind(one)
    if title_id is None or name is None or kind is None:
        return None
    title: dict[str, Any] = {"id": title_id, "type": kind[0], "name": name}
    poster = _poster(one.get("poster"))
    if poster is not None:
        title["poster"] = poster
    shape = one.get("posterShape")
    if isinstance(shape, str) and _POSTER_SHAPE.fullmatch(shape):
        title["posterShape"] = shape
    year = one.get("year")
    if isinstance(year, int) and not isinstance(year, bool):
        year = str(year)
    made = _text(year, MOST_YEAR)
    if made is not None:
        title["year"] = made
    genres = one.get("genres")
    if isinstance(genres, list):
        kept: list[str] = []
        for genre in genres:
            named = _text(genre, MOST_GENRE)
            if named is not None and named not in kept:
                kept.append(named)
                if len(kept) == MOST_GENRES:
                    break
        if kept:
            title["genres"] = kept
    if kind[1]:
        title["anime"] = True
    return title


def _progress(one: object, moment: str) -> dict[str, Any] | None:
    """Where a title was got to (a Progress line), or None."""
    if not isinstance(one, dict):
        return None
    line_id, name, kind = _an_id(one.get("id")), _text(one.get("name"), MOST_NAME), _kind(one)
    seconds, length = _seconds(one.get("seconds")), _seconds(one.get("length"))
    at = _moment(one.get("at"), moment)
    if line_id is None or name is None or kind is None or at is None:
        return None
    if seconds is None or length is None:
        return None
    line: dict[str, Any] = {"id": line_id, "type": kind[0], "name": name, "seconds": seconds,
                            "length": length, "at": at}  # fmt: skip
    poster, episode = _poster(one.get("poster")), _an_id(one.get("episode"))
    detail, title = _text(one.get("detail"), MOST_NAME), _title(one.get("title"))
    for field, value in (("poster", poster), ("episode", episode), ("detail", detail),
                         ("title", title)):  # fmt: skip
        if value is not None:
            line[field] = value
    return line


def _kind(one: dict[str, Any]) -> tuple[str, bool] | None:
    """A title's `type`, and whether it's an anime. `movie` and `series` are as sent. An
    add-on's list of anime calls its titles `anime`, which the agreement has no type
    for: one is kept as a `series` marked `anime`, as the app reads it. Anything else
    (a channel) isn't a title the agreement carries."""
    kind = one.get("type")
    if kind in TITLE_TYPES:
        return str(kind), one.get("anime") is True
    if kind == "anime":
        return "series", True
    return None


def _an_id(value: object) -> str | None:
    """A plugin's id (`tt…`, `kitsu:…`, an episode's `tt…:1:2`), exactly as sent: at
    most MOST_ID characters, with nothing unseen in it. Never cut: a cut id is another."""
    if not isinstance(value, str) or not 0 < len(value) <= MOST_ID or value != value.strip():
        return None
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Zl", "Zp") for ch in value):
        return None
    return value


def _text(value: object, most: int) -> str | None:
    """Words, with anything unseen taken out, spaces at the ends trimmed, and cut to
    `most` characters. None when nothing is left."""
    if not isinstance(value, str):
        return None
    kept = "".join(ch for ch in value if unicodedata.category(ch) not in ("Cc", "Cs")).strip()
    return kept[:most].rstrip() or None


def _poster(value: object) -> str | None:
    """A picture's address: `https`, with a host, at most MOST_POSTER characters."""
    if not isinstance(value, str) or not 0 < len(value) <= MOST_POSTER:
        return None
    if any(ch.isspace() or unicodedata.category(ch) in ("Cc", "Cf", "Cs") for ch in value):
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        return None
    return value if parts.scheme.lower() == "https" and host else None


def _seconds(value: object) -> float | None:
    """A number of seconds from 0 to MOST_SECONDS, or None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or not 0 <= value <= MOST_SECONDS:
        return None
    return float(value)


def _moment(value: object, moment: str) -> str | None:
    """A time, in the form the agreement uses, and never later than `moment` (now)."""
    if not isinstance(value, str) or not value or len(value) > 40:
        return None
    try:
        found = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if found.tzinfo is None:
        found = found.replace(tzinfo=UTC)
    try:
        found = found.astimezone(UTC)
    except (OverflowError, ValueError):
        return None
    if found.year < EARLIEST_YEAR:
        return None
    return min(found.strftime(_WHEN), moment)


def _revision(watching: dict[str, Any]) -> str:
    body = json.dumps(watching, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def set_watching(profile: str, sent: object) -> str:
    """The app gives the engine a profile's watching (`watching.set`), for that profile's
    paired devices to have. It's checked as a device's is. Returns its revision."""
    checked = check_watching(sent)
    revision = _revision(checked)
    with _watching_lock:
        kept = config.load_watching()
        kept["given"][profile] = {"revision": revision, **checked}
        config.save_watching(kept)
    return revision


def given_watching(profile: str) -> dict[str, Any] | None:
    """A profile's watching as the app last gave it, as a device is given it; None if the
    app never has."""
    with _watching_lock:
        found = config.load_watching()["given"].get(profile)
    if found is None:
        return None
    return {"format": FORMAT, "revision": str(found.get("revision") or ""), **_lists(found)}


def keep_sent(profile: str, device: dict[str, Any], checked: dict[str, Any]) -> str:
    """Keep what a device sent of its watching (checked already) until the app takes it.
    A newer sending from the device replaces its one that the app hasn't taken: each is
    all of the device's watching, so nothing is lost. Returns the sending's id, which is
    new each time (so the app letting go of the older one can't let go of this)."""
    entry = {"id": "w_" + secrets.token_hex(8), "profile": profile, "device": device["id"],
             "kind": device["device"], "at": _now(), "watching": checked}  # fmt: skip
    with _watching_lock:
        kept = config.load_watching()
        kept["sent"] = [
            one for one in kept["sent"]
            if not (one.get("profile") == profile and one.get("device") == device["id"])
        ]  # fmt: skip
        kept["sent"].append(entry)
        config.save_watching(kept)
    return entry["id"]


def sent_watching() -> list[dict[str, Any]]:
    """What devices have sent of their watching that the app hasn't taken yet, oldest
    first (`watching.sent`). A device is named by its id in the app's list of paired
    devices and the kind it says it is: nothing of its key."""
    with _watching_lock:
        sent = config.load_watching()["sent"]
    return [
        {
            "id": one["id"],
            "profile": one["profile"],
            "device": one.get("device"),
            "kind": one.get("kind"),
            "at": one.get("at"),
            "watching": _lists(one.get("watching")),
        }
        for one in sent
        if isinstance(one.get("id"), str) and isinstance(one.get("profile"), str)
    ]


def done_watching(ids: Iterable[str]) -> int:
    """The app has taken these sendings (`watching.done`): they're let go. Returns how
    many are still waiting."""
    gone = set(ids)
    with _watching_lock:
        kept = config.load_watching()
        left = [one for one in kept["sent"] if one.get("id") not in gone]
        if len(left) != len(kept["sent"]):
            kept["sent"] = left
            config.save_watching(kept)
    return len(left)


def _lists(found: object) -> dict[str, list[dict[str, Any]]]:
    """The three lists of a kept watching, each always a list."""
    found = found if isinstance(found, dict) else {}
    return {
        field: [one for one in found.get(field) or [] if isinstance(one, dict)]
        if isinstance(found.get(field), list) else []
        for field in WATCHING_LISTS
    }  # fmt: skip


# ---- a film sent from a device (2026-10-16) -------------------------------------------------
#
# A device's own film arrives a part at a time into `<cache>/uploads/<upload>.part`, with a
# note beside it (`<upload>.json`: what it is, where it will be kept, how much has
# arrived). `fileops` writes both. Once it has all arrived it's copied into the Movies
# folder (`fileops.keep_media`, never over a file that's there) and both are deleted.


def sweep_uploads(cache: Path, skip: Iterable[str] = ()) -> int:
    """Delete what has arrived of films that devices were sending and that have had no
    part for two days (`fileops.sweep_cached`): when the engine starts, when sharing is
    switched on, and when a device starts sending a film. `skip`: the files in use."""
    folder = cache / fileops.UPLOADS_DIR
    if not folder.is_dir():
        return 0
    try:
        return fileops.sweep_cached(folder, cache=cache, older_than_s=UPLOAD_KEEP_S, skip=skip)
    except MusicOrgError as exc:
        log.warning("sharing: films that stopped arriving couldn't be cleared: %s", exc.message)
        return 0


def _upload_details(asked: object) -> dict[str, Any]:
    """What a device says of a film it will send, checked as `torrent.keep` checks a
    film's name. WatchingError (400) when it isn't one."""

    def refuse(message: str) -> WatchingError:
        return WatchingError(400, message)

    if not isinstance(asked, dict):
        raise refuse("That isn't a film to send.")
    kind = asked.get("kind", naming.KEPT_MOVIE)
    if kind not in UPLOAD_KINDS:  # never `adult`: a device's own film isn't one of those
        raise refuse("kind should be movie, series or anime.")
    title, show, year = asked.get("title", ""), asked.get("show") or "", asked.get("year")
    if not isinstance(title, str) or not isinstance(show, str):
        raise refuse("title and show should be words.")
    title, show = title.strip(), show.strip()
    if isinstance(year, int) and not isinstance(year, bool):
        year = str(year)
    year = year.strip() if isinstance(year, str) else ""
    season, episode = asked.get("season"), asked.get("episode")
    for number in (season, episode):
        if number is not None and (
            isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= 9999
        ):
            raise refuse("season and episode should be whole numbers from 0.")
    if show and kind == naming.KEPT_MOVIE:
        raise refuse("show is for a series or an anime: say which in kind.")
    if not show and (season is not None or episode is not None):
        raise refuse("season and episode need the show they're of.")
    if not title and not show:
        raise refuse("A film needs a title.")
    ending = asked.get("type")
    ending = ending.strip().lstrip(".").lower() if isinstance(ending, str) else ""
    if ending not in UPLOAD_TYPES:
        raise refuse("type should be one of: " + ", ".join(UPLOAD_TYPES) + ".")
    size = asked.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MOST_UPLOAD:
        raise refuse("size should be the film's length in bytes, up to 200 GB.")
    return {"kind": kind, "title": title, "show": show,
            "year": year if year.isdigit() and len(year) == 4 else None,
            "season": season, "episode": episode, "type": ending, "size": size}  # fmt: skip


def _read_note(uploads: Path, upload_id: str) -> dict[str, Any] | None:
    """The note beside an upload, if it's there and whole."""
    try:
        note = json.loads((uploads / f"{upload_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(note, dict) or note.get("upload") != upload_id:
        return None
    folder, size, have = note.get("folder"), note.get("size"), note.get("have")
    if not (isinstance(folder, list) and all(isinstance(part, str) for part in folder)):
        return None
    if not all(isinstance(note.get(k), str) for k in ("profile", "device", "name", "type")):
        return None
    if note["type"] not in UPLOAD_TYPES or not note["name"]:
        return None
    if not all(isinstance(n, int) and not isinstance(n, bool) for n in (size, have)):
        return None
    if not 0 < size <= MOST_UPLOAD or not 0 <= have <= size:
        return None
    return note


def _arrived(uploads: Path, upload_id: str, note: dict[str, Any] | None) -> int:
    """How many of an upload's bytes the computer has: what its note counts, as far as
    its file has them."""
    if note is None:
        return 0
    try:
        about = (uploads / f"{upload_id}.part").lstat()
    except OSError:
        return 0
    if not stat.S_ISREG(about.st_mode):
        return 0
    return min(int(note["have"]), about.st_size)


def _named_already(folder: Path, name: str) -> bool:
    """Whether `folder` has a film called `name` already, with any of a film's endings
    (and in any capitals). Only looked at."""
    try:
        with os.scandir(folder) as entries:
            names = [entry.name for entry in entries]
    except OSError:
        return False
    wanted = unicodedata.normalize("NFC", naming.safe_component(name)).casefold()
    for found in names:
        stem, dot, ending = found.rpartition(".")
        if dot and ending.lower() in UPLOAD_TYPES:
            if unicodedata.normalize("NFC", stem).casefold() == wanted:
                return True
    return False


def _why_not_kept(exc: BaseException) -> str:
    """Why a film couldn't be put in the Movies folder, as a device is told it: plain
    words, and nothing of where the folder is."""
    cause = exc if isinstance(exc, OSError) else exc.__cause__
    if isinstance(cause, OSError) and cause.errno == errno.ENOSPC:
        return (
            "There isn't room for that film in the computer's Movies folder. Make some "
            "room, then try again."
        )
    return "The computer couldn't put that film in its Movies folder just now. Try again later."


# ---- sharing, switched on ----------------------------------------------------------------


def refusal(error: str, message: str) -> dict[str, str]:
    return {"error": error, "message": message}


def status_off(lib: Library) -> dict[str, Any]:
    """What the app's Settings shows while sharing is off."""
    return {"on": False, "port": None, "address": None, "name": lib.root.name,
            "service": SERVICE_TYPE, "devices": devices(), "pairing": False,
            "pairing_seconds_left": 0, "films": False, "awake": True}  # fmt: skip


class Share:
    """One library being shared: the listener, the code on the screen, and the list."""

    def __init__(
        self,
        lib: Library,
        *,
        changed: Callable[[], None] | None = None,
        took: Callable[[], None] | None = None,
        watched: Callable[[str], None] | None = None,
        films: bool = False,
        awake: bool = True,
        child: Callable[[], bool] | None = None,
        host: str | None = None,
        port: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.lib = lib
        self.info = library_info(lib)
        self._changed = changed
        self._took = took  # called when a device's changes changed what `listening` keeps
        # Called with a profile's id when one of its devices has sent its watching.
        self._watched = watched
        # Whether kept films and videos (outside the library) are in the list too. Off
        # until the owner switches it on, apart from sharing itself.
        self.films = films
        # Whether the computer is kept awake while a paired device syncs (`busy`). On
        # unless the owner switches it off in the app's Settings (2026-10-11).
        self.awake = awake
        # Whether the profile open in the app is a child's, or might be (the app hasn't
        # said yet): then no film for adults is ever named to a device (2026-10-17).
        # Without anyone to ask, it's taken as a child's.
        self._child = child if child is not None else (lambda: True)
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
        # The films for adults the list named, for each device whose request asked for
        # them (2026-10-17): by the device's id in the list of paired devices. Only these
        # are given out, and only to that device.
        self._adult_files: dict[str, dict[str, SharedFile]] = {}
        self._film_files: dict[str, SharedFile] = {}  # the films list's, by id
        self._films_built_at: float | None = None
        self._uploading: set[str] = set()  # uploads a request is working on right now
        self._remembered: dict[tuple[str, int, int], dict[str, Any]] = {}
        self._turned_away_at: float | None = None
        self._awake_until = 0.0  # a paired device asked for something: awake until then
        self._awake: threading.Thread | None = None
        self._off = threading.Event()  # sharing was switched off: awake no longer

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
        self._off.clear()
        self._http = _listen(self, self._host, self._port)
        sweep_uploads(config.app_dirs().cache)  # films that stopped arriving two days ago
        threading.Thread(
            target=self._http.serve_forever,
            kwargs={"poll_interval": 0.2},
            name="sharing",
            daemon=True,
        ).start()
        log.info("sharing: switched on")

    def stop(self) -> None:
        """Stop listening, at once: a file on its way to a phone is cut off too, and a
        computer kept awake for a syncing device may sleep again."""
        with self._lock:
            listener, self._http = self._http, None
            self._code = None
            awake = self._awake
        self._off.set()
        if awake is not None:
            awake.join(timeout=10)
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
            "awake": self.awake,
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

    def pair(
        self, code: str, device: object, *, header: str | None = None, its_id: object = None
    ) -> tuple[int, dict[str, Any]]:
        """A phone has sent a code: (the HTTP status, the answer's body). `header` and
        `its_id` are how a device paired before is known again (`_add_device`): they're
        looked at only once the code is right."""
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
        key, again = _add_device(kind, header=header, its_id=its_id)
        log.info("sharing: a device was paired%s (%s)", " again" if again else "", kind)
        self._tell()
        return 200, {"key": key}

    # -- the list and its files --

    def adult_allowed(self) -> bool:
        """Whether films for adults may be named to a phone that asks for them: while
        the films switch is on and the profile open in the app isn't a child's."""
        return self.films and not self._child()

    def make_list(self, *, adult: bool = False, device: str | None = None) -> dict[str, Any]:
        """The list of everything, made now. The files it names are remembered, to be
        given out when they're asked for.

        `adult`: the phone asked for the films in the Movies folder's 18+ folder too
        (`?adult=1`, 2026-10-17). They're named only when `adult_allowed`, and their
        files are remembered for `device` alone; a list made without them forgets that
        device's, so it can fetch none of them until it asks again."""
        adult = adult and self.adult_allowed()
        found, files = the_list(
            self.lib, self.info, self._remembered,
            films=config.media_folders() if self.films else None, adult=adult,
        )  # fmt: skip
        theirs = {file_id: one for file_id, one in files.items() if one.adult}
        with self._lock:
            self._files = {file_id: one for file_id, one in files.items() if not one.adult}
            self._built_at = self._clock()
            if device is not None:
                if theirs:
                    self._adult_files[device] = theirs
                else:
                    self._adult_files.pop(device, None)
        return found

    def file(self, file_id: str, device: str | None = None) -> SharedFile | None:
        """The file a list named by this id, or None. Only a file the list names is
        ever given out: no path comes from a phone. A film for adults only to `device`,
        when the last list it was given named it, and while it may still have them."""
        with self._lock:
            found = self._files.get(file_id)
            if found is None and device is not None:
                found = self._adult_files.get(device, {}).get(file_id)
            stale = self._built_at is None or self._clock() - self._built_at > REBUILD_LEAST_S
        if found is not None and found.adult and not self.adult_allowed():
            return None  # the films switch went off, or a child's profile is open now
        if found is None and stale:
            # The phone's list may be newer than the one remembered here (the engine
            # has started again since), so the list is made again, but not every time.
            # (Without the films for adults: those are named only when a phone asks.)
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
        the app starts always does: nothing else may write the library's records. A
        device's watching (`watching`) is taken by any share, and films (`films`) while
        the films switch is on: neither is the library's (2026-10-16). A device saying it
        has forgotten the library is taken by any share: that changes the list of paired
        devices, not the library."""
        films = (FILMS,) if self.films else ()
        return [*(ACCEPTS if self.lib.writable else ()), WATCHING, *films, FORGET]

    def forgotten_by(self, device: dict[str, Any], profile: str | None = None) -> None:
        """A paired device says it has forgotten this library: it's unpaired, as the
        owner's Remove in the app does, and the app is told so its list is right. A
        device of another profile's is unpaired from that profile (2026-10-16)."""
        try:
            forget(device["id"], profile=profile)
        except NotFoundError:
            pass  # the owner removed it this very moment: it's gone either way
        log.info("sharing: that device said it had forgotten this library")
        self._tell()

    def take(self, sent: list[Any]) -> list[str]:
        """A paired device's changes (`take_changes`): the ids of the ones dealt with.
        The app is told when any of them changed something: a playlist, a heart, a play
        count or a song's listening time. The log says how many of each, and no more."""
        dealt_with, done = take_changes(self.lib, sent)
        if any(done.values()):
            listened = round(done["seconds"])
            log.info(
                "sharing: a device's changes were taken (playlists made: %d, songs put in "
                "playlists: %d, listening: %d min %d s, hearts changed: %d, plays counted: %d, "
                "songs taken out of playlists: %d, playlists renamed: %d, put in a new "
                "order: %d, deleted: %d)",
                done["playlists"], done["songs"], listened // 60, listened % 60,
                done["hearts"], done["plays"], done["removed"], done["renamed"],
                done["ordered"], done["deleted"],
            )  # fmt: skip
            if self._took is not None:
                self._took()
        return dealt_with

    # -- what's watched, and films, for a video app (2026-10-16) --

    def watching_for(self, owner: tuple[str, dict[str, Any]]) -> dict[str, Any] | None:
        """The watching of a device's profile, as the app last gave it, or None. Asking
        for it is a sync: the device's line in the app's list says when it last did."""
        profile, device = owner
        found = given_watching(profile)
        if found is not None:
            _mark_synced(device["id"], profile)
            self._tell()
        return found

    def take_watching(self, owner: tuple[str, dict[str, Any]], sent: object) -> None:
        """A device's own watching (all of it): checked, kept for the app until it takes
        it, and the app told. WatchingError when it can't be taken (nothing is kept)."""
        profile, device = owner
        checked = check_watching(sent)
        keep_sent(profile, device, checked)
        log.info("sharing: a device sent what was watched on it (%d on its Watchlist, "
                 "%d watched, %d places got to)", len(checked["watchlist"]),
                 len(checked["watched"]), len(checked["progress"]))  # fmt: skip
        if self._watched is not None:
            self._watched(profile)

    def films_list(self) -> dict[str, Any]:
        """The films and episodes in the Movies folder, as the list's `movies` names them,
        without the videos folder's. The files it names are remembered, to be given out
        to a device of any profile when they're asked for."""
        # Never the videos folder's, and never a film for adults (2026-10-17): the 18+
        # folder is for a phone player that asks for it, not for a video app.
        found = [
            (entry, main) for entry, main in kept_media(config.media_folders())
            if entry["kind"] != "video" and not main.adult
        ]  # fmt: skip
        movies = [entry for entry, _ in found]
        with self._lock:
            self._film_files = {main.id: main for _, main in found}
            self._films_built_at = self._clock()
        body = json.dumps(movies, sort_keys=True, ensure_ascii=False)
        revision = hashlib.sha256(body.encode("utf-8", "surrogatepass")).hexdigest()[:16]
        return {"format": FORMAT, "revision": revision, "movies": movies}

    def film_file(self, file_id: str, *, rebuild: bool = True) -> SharedFile | None:
        """The film the films list named by this id, or None. As with `file`, only a file
        a list names is ever given out, and a list newer than the one remembered here is
        made again, but not every time."""
        with self._lock:
            found = self._film_files.get(file_id)
            built = self._films_built_at
        stale = built is None or self._clock() - built > REBUILD_LEAST_S
        if found is None and rebuild and stale:
            self.films_list()
            with self._lock:
                found = self._film_files.get(file_id)
        return found

    def start_upload(
        self, owner: tuple[str, dict[str, Any]], asked: object
    ) -> tuple[int, dict[str, Any]]:
        """A device will send a film of its own (`POST /sync/v1/uploads`): (the HTTP
        status, the answer's body). WatchingError (400) when what it says of the film
        can't be taken."""
        if not self.films:
            return 403, refusal("not_shared", NOT_SHARED)
        about = _upload_details(asked)
        movies = config.media_folders()["movies"]
        folder, name = naming.kept_place(
            about["kind"], about["title"], year=about["year"], show=about["show"],
            season=about["season"], episode=about["episode"], root=movies,
        )  # fmt: skip
        if not name:
            raise WatchingError(400, "That title can't be made into a file's name.")
        profile, device = owner
        # The same film from the same device is the same upload, so a retry carries on.
        made_of = "\n".join(
            (profile, device["id"], *folder, name, about["type"], str(about["size"]))
        )
        upload_id = _name("u", made_of)
        cache = config.app_dirs().cache
        uploads = cache / fileops.UPLOADS_DIR
        with self._lock:
            working = upload_id in self._uploading
            if not working:
                self._uploading.add(upload_id)
            others = self._in_use()
        if working:  # a part of it is arriving this moment: say how much is here
            have = _arrived(uploads, upload_id, _read_note(uploads, upload_id))
            return 200, {"upload": upload_id, "have": have, "already": False}
        try:
            sweep_uploads(cache, skip=others)
            note = _read_note(uploads, upload_id)
            if _named_already(movies.joinpath(*folder), name):
                # Nothing more is sent; what had arrived of it isn't needed.
                if note is not None:
                    self._forget_upload(upload_id)
                return 200, {"upload": upload_id, "have": 0, "already": True}
            have = _arrived(uploads, upload_id, note)
            if note is not None and have == note["size"]:
                # All of it arrived before, and it couldn't be kept then: try again now.
                why = self._keep_upload(note)
                if why is not None:
                    return 500, refusal("unavailable", why)
                return 200, {"upload": upload_id, "have": 0, "already": True}
            if not tools.room_for(about["size"] - have, about["size"], cache, movies):
                return 500, refusal(
                    "unavailable",
                    "There isn't room on the computer for that film just now. Make some "
                    "room on it, then try again.",
                )
            note = {"upload": upload_id, "profile": profile, "device": device["id"],
                    "kind": about["kind"], "folder": list(folder), "name": name,
                    "type": about["type"], "size": about["size"], "have": have,
                    "at": _now()}  # fmt: skip
            config.ensure_app_dir(cache)
            fileops.make_cached_folder(uploads, cache=cache)
            fileops.write_upload_note(uploads / f"{upload_id}.json", json.dumps(note), cache=cache)
        finally:
            with self._lock:
                self._uploading.discard(upload_id)
        if have == 0:
            log.info("sharing: a device is sending a film (%d MB)", about["size"] // 1_000_000)
        return 200, {"upload": upload_id, "have": have, "already": False}

    def upload_part(
        self,
        owner: tuple[str, dict[str, Any]],
        upload_id: str,
        content_range: str | None,
        length: int,
        source: Any,
    ) -> tuple[int, dict[str, Any], bool]:
        """The next part of a film a device is sending (`PUT /sync/v1/uploads/<id>`):
        (the HTTP status, the answer's body, whether the part's bytes were read). With
        the last part the film is kept in the Movies folder before the answer."""
        if not self.films:
            return 403, refusal("not_shared", NOT_SHARED), False
        gone = refusal(
            "not_found", "The computer has no such film arriving. Start sending it again."
        )
        if not _UPLOAD_ID.fullmatch(upload_id):
            return 404, gone, False
        cache = config.app_dirs().cache
        uploads = cache / fileops.UPLOADS_DIR
        with self._lock:
            working = upload_id in self._uploading
            if not working:
                self._uploading.add(upload_id)
        if working:  # another part of it is arriving this moment
            have = _arrived(uploads, upload_id, _read_note(uploads, upload_id))
            return 409, {**self._elsewhere(), "have": have}, False
        try:
            profile, device = owner
            note = _read_note(uploads, upload_id)
            if note is None or (note["profile"], note["device"]) != (profile, device["id"]):
                return 404, gone, False  # another device's is as good as none
            found = _CONTENT_RANGE.fullmatch((content_range or "").strip())
            if found is None:
                return 400, refusal("bad_request", "A part needs its Content-Range."), False
            first, last, size = int(found["first"]), int(found["last"]), int(found["size"])
            if size != note["size"] or not first <= last < size or length != last - first + 1:
                return 400, refusal(
                    "bad_request", "That part doesn't fit the film the computer was told of."
                ), False  # fmt: skip
            if length > MOST_PART:
                return 413, refusal(
                    "bad_request", "That's more than the computer takes at once: 256 MB at most."
                ), False  # fmt: skip
            have = _arrived(uploads, upload_id, note)
            if first != have:
                return 409, {**self._elsewhere(), "have": have}, False
            written = fileops.write_upload_part(
                uploads / f"{upload_id}.part", first, source, length, cache=cache,
                going_on=self._still_on,
            )  # fmt: skip
            note["have"], note["at"] = first + written, _now()
            fileops.write_upload_note(uploads / f"{upload_id}.json", json.dumps(note), cache=cache)
            if written < length:  # cut off: what arrived is kept, for the device to carry on
                return 400, {**refusal(
                    "bad_request", "Only part of that part arrived. Carry on from what has."
                ), "have": note["have"]}, True  # fmt: skip
            if note["have"] < note["size"]:
                return 200, {"have": note["have"]}, True
            why = self._keep_upload(note)
            if why is not None:
                return 500, {**refusal("unavailable", why), "have": note["have"]}, True
            return 200, {"have": note["have"], "kept": True}, True
        finally:
            with self._lock:
                self._uploading.discard(upload_id)

    def _keep_upload(self, note: dict[str, Any]) -> str | None:
        """Copy a film that has all arrived into the Movies folder, under the name it was
        sent with (as `torrent.keep` names a film), never over a file that's there, and
        let go of what arrived. None when it's kept; otherwise why not, in words a
        device can show (what arrived is kept, for another try)."""
        cache = config.app_dirs().cache
        part = cache / fileops.UPLOADS_DIR / f"{note['upload']}.part"
        movies = config.media_folders()["movies"]
        outcome: list[str | None] = []

        def keep() -> None:
            try:
                fileops.keep_media(
                    part, movies.joinpath(*note["folder"]), f"{note['name']}.{note['type']}",
                    cache=cache, allowed=[movies], forbidden=[self.lib.root],
                )  # fmt: skip
            except (MusicOrgError, ValueError, OSError) as exc:
                log.warning("sharing: a film sent from a device couldn't be kept: %s",
                            getattr(exc, "message", None) or exc)  # fmt: skip
                outcome.append(_why_not_kept(exc))
                return
            outcome.append(None)

        # Copying a big film takes a while: the device is syncing all that time.
        worker = threading.Thread(target=keep, name="sharing-keep", daemon=True)
        worker.start()
        while worker.is_alive():
            worker.join(KEEP_CHECK_S)
            self.busy()
        why = outcome[0] if outcome else _why_not_kept(RuntimeError())
        if why is None:
            self._forget_upload(note["upload"])
            log.info("sharing: a film sent from a device was kept in the Movies folder")
        return why

    def _forget_upload(self, upload_id: str) -> None:
        cache = config.app_dirs().cache
        for ending in (".part", ".json"):
            fileops.forget_cached(cache / fileops.UPLOADS_DIR / f"{upload_id}{ending}", cache=cache)

    def _in_use(self) -> list[str]:
        """The files of the uploads being worked on (called holding `_lock`)."""
        return [f"{one}{ending}" for one in self._uploading for ending in (".part", ".json")]

    def _still_on(self, written: int) -> bool:
        """While a part arrives: the device is syncing, and carries on unless sharing has
        been switched off."""
        self.busy()
        return self.listening

    @staticmethod
    def _elsewhere() -> dict[str, str]:
        return refusal("wrong_place", "That part doesn't start where what has arrived ends.")

    # -- awake while a device syncs --

    def busy(self) -> None:
        """A paired device is syncing: it asked for the list or a file, or is being sent
        one, or sent its changes. The computer is kept from going to sleep until the
        device has asked for nothing for `AWAKE_QUIET_S` (the screen may still turn
        off), as it is while the queue downloads (`tools.keep_awake`). Only a paired
        device's requests count: nobody else can keep the computer awake. Switching
        sharing off lets it sleep at once, and so does the owner's switch for this
        (`awake`), which also stops it being kept awake at all."""
        with self._lock:
            if self._http is None or not self.awake:
                return
            self._awake_until = self._clock() + AWAKE_QUIET_S
            if self._awake is None:
                self._awake = threading.Thread(
                    target=self._stay_awake, name="sharing-awake", daemon=True
                )
                self._awake.start()

    def _stay_awake(self) -> None:
        """Holds the computer awake, from one thread from start to end (Windows ties
        it to the thread that asked), until the device has gone quiet or sharing is
        switched off."""
        log.info("sharing: a device is syncing, so the computer is kept awake")
        with tools.keep_awake():
            while True:
                with self._lock:
                    left = self._awake_until - self._clock()
                    if left <= 0 or self._http is None or not self.awake:
                        self._awake = None
                        break
                self._off.wait(min(left, AWAKE_CHECK_S))
        log.info("sharing: the computer is no longer kept awake for a device")

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


def _for_any_profile(path: str) -> bool:
    """Whether a device paired with another profile may ask for this (2026-10-16): its
    profile's watching, the films, and a film's file (`do_GET` checks it's a film's)."""
    return path in (PREFIX + "watching", PREFIX + "films") or path.startswith(PREFIX + "files/")


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
        asked = urlsplit(self.path)
        path = asked.path
        if not self._welcome():
            return
        header = self.headers.get("Authorization")
        if path == PREFIX + "hello":
            paired = _device_with(header) is not None
            known = paired or _any_device_with(header) is not None
            self._answer(200, {"format": FORMAT, "library": dict(share.info), "paired": paired,
                               "known": known, "accepts": share.accepts})  # fmt: skip
            return
        if not path.startswith(PREFIX):
            self._answer(404, refusal("not_found", "There's nothing at that address."))
            return
        # A device of the profile whose library is shared may ask for anything; one of
        # another profile's only for what a video app asks for (2026-10-16).
        device = _device_with(header)
        owner = (
            (config.current_profile(), device) if device is not None else _any_device_with(header)
        )
        if owner is None or (device is None and not _for_any_profile(path)):
            self._answer(401, refusal("not_paired", "This device isn't paired with the computer."))
            return
        share.busy()  # a paired device is syncing: the computer stays awake for it
        if path == PREFIX + "watching":
            self._watching_given(owner)
            return
        if path == PREFIX + "films":
            self._films()
            return
        if path == PREFIX + "library" and device is not None:  # this profile's devices only
            # The films for adults only when asked for, exactly so (2026-10-17).
            adult = parse_qs(asked.query).get(ADULT_QUERY) == ["1"]
            try:
                found = share.make_list(adult=adult, device=device["id"])
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
            file_id = unquote(path[len(PREFIX + "files/") :])
            try:
                # A film the films list named, for a device of any profile; anything the
                # list of everything named, for one of this profile's only.
                shared = share.film_file(file_id, rebuild=False) if share.films else None
                if shared is None and device is not None:
                    shared = share.file(file_id, device["id"])
                if shared is None and share.films:
                    shared = share.film_file(file_id)
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
        if path == PREFIX + "forget":
            self._forget()
            return
        if path == PREFIX + "watching":
            self._watching_sent()
            return
        if path == PREFIX + "uploads":
            self._upload_start()
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
            code, device, its_id = str(asked.get("code", "")), asked.get("device"), asked.get("id")
        except (ValueError, AttributeError):
            self._answer(403, wrong)
            return
        status, body = self.server.share.pair(
            code, device, header=self.headers.get("Authorization"), its_id=its_id
        )
        self._answer(status, body)

    def do_PUT(self) -> None:
        """Only the parts of a film a device is sending are put (2026-10-16)."""
        if not self._welcome():
            return
        path = urlsplit(self.path).path
        if not path.startswith(PREFIX + "uploads/"):
            self.send_error(501)  # nothing else is ever put
            return
        self._upload_part(unquote(path[len(PREFIX + "uploads/") :]))

    def _forget(self) -> None:
        """A paired device says it has forgotten this library (2026-10-11): it's
        unpaired. Only by its own key, so only ever itself; whatever else it sent is
        read and not looked at. A device of another profile's is unpaired from it
        (2026-10-16). This isn't syncing: it keeps no computer awake."""
        self._body(MAX_BODY)
        owner = _any_device_with(self.headers.get("Authorization"))
        if owner is None:
            self._answer(401, refusal("not_paired", "This device isn't paired with the computer."))
            return
        profile, device = owner
        try:
            self.server.share.forgotten_by(device, profile)
        except (MusicOrgError, OSError) as exc:
            log.warning("sharing: a device couldn't be unpaired: %s", exc)
            self._answer(500, refusal(
                "unavailable", "The computer couldn't do that just now. Try again."
            ))  # fmt: skip
            return
        self._answer(200, {"forgotten": True})

    def _changes(self) -> None:
        """A paired device says what was done on it (2026-10-10). The body is read before
        anything is answered, so that the answer isn't lost behind what's left of it."""
        share = self.server.share
        body = self._body(MAX_CHANGES_BODY)
        if _device_with(self.headers.get("Authorization")) is None:
            self._answer(401, refusal("not_paired", "This device isn't paired with the computer."))
            return
        share.busy()
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

    # -- what's watched, and films, for a video app (2026-10-16) --

    def _watching_given(self, owner: tuple[str, dict[str, Any]]) -> None:
        try:
            found = self.server.share.watching_for(owner)
        except (MusicOrgError, OSError) as exc:
            log.warning("sharing: a profile's watching couldn't be read: %s", exc)
            found = None
        if found is None:
            self._answer(500, refusal(
                "unavailable", "The computer doesn't have that yet. Try again at the next sync."
            ))  # fmt: skip
            return
        self._answer(200, found)

    def _watching_sent(self) -> None:
        """A device's own watching. The body is read before anything is answered."""
        share = self.server.share
        body = self._body(MAX_WATCHING_BODY)
        owner = _any_device_with(self.headers.get("Authorization"))
        if owner is None:
            self._answer(401, refusal("not_paired", "This device isn't paired with the computer."))
            return
        share.busy()
        if body is None:
            self._answer(413, refusal(
                "bad_request", "That's more than the computer takes at once."
            ))  # fmt: skip
            return
        try:
            sent = json.loads(body or b"{}")
        except (ValueError, RecursionError):
            self._answer(400, refusal("bad_request", "That isn't a list of what's been watched."))
            return
        try:
            share.take_watching(owner, sent)
        except WatchingError as exc:
            self._answer(exc.status, refusal("bad_request", exc.message))
            return
        except (MusicOrgError, OSError) as exc:
            log.warning("sharing: a device's watching couldn't be kept: %s", exc)
            self._answer(500, refusal(
                "unavailable", "The computer couldn't keep that just now. Try again."
            ))  # fmt: skip
            return
        self._answer(200, {"taken": True})

    def _films(self) -> None:
        share = self.server.share
        if not share.films:
            self._answer(403, refusal("not_shared", NOT_SHARED))
            return
        try:
            found = share.films_list()
        except (MusicOrgError, OSError) as exc:
            log.warning("sharing: the list of films couldn't be made: %s", exc)
            self._answer(500, refusal(
                "unavailable", "The computer couldn't read its films just now. Try again."
            ))  # fmt: skip
            return
        log.info("sharing: a device was given the list of films (%d)", len(found["movies"]))
        self._answer(200, found)

    def _upload_start(self) -> None:
        share = self.server.share
        body = self._body(MAX_BODY)
        owner = _any_device_with(self.headers.get("Authorization"))
        if owner is None:
            self._answer(401, refusal("not_paired", "This device isn't paired with the computer."))
            return
        share.busy()
        if body is None:
            self._answer(
                413, refusal("bad_request", "That's more than the computer takes at once.")
            )
            return
        try:
            asked = json.loads(body or b"{}")
        except (ValueError, RecursionError):
            self._answer(400, refusal("bad_request", "That isn't a film to send."))
            return
        try:
            status, answer = share.start_upload(owner, asked)
        except WatchingError as exc:
            self._answer(exc.status, refusal("bad_request", exc.message))
            return
        except (MusicOrgError, OSError) as exc:
            log.warning("sharing: a film couldn't be made ready to arrive: %s", exc)
            self._answer(500, refusal(
                "unavailable", "The computer couldn't get ready for that film just now. Try again."
            ))  # fmt: skip
            return
        self._answer(status, answer)

    def _upload_part(self, upload_id: str) -> None:
        """The next part of a film a device is sending. Its bytes go straight from the
        connection to the file (`fileops.write_upload_part`), never all in memory. A part
        that's refused unread is read and thrown away first, so that the device gets the
        answer rather than a broken connection."""
        share = self.server.share
        self.close_connection = True
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            length = -1
        owner = _any_device_with(self.headers.get("Authorization"))
        if owner is None:
            self._discard(min(max(length, 0), MAX_DISCARD))
            self._answer(401, refusal("not_paired", "This device isn't paired with the computer."))
            return
        share.busy()
        try:
            status, answer, read = share.upload_part(
                owner, upload_id, self.headers.get("Content-Range"), length, self.rfile
            )
        except (MusicOrgError, OSError) as exc:
            log.warning("sharing: part of a film from a device couldn't be kept: %s", exc)
            status, answer, read = 500, refusal(
                "unavailable", "The computer couldn't keep that part just now. Try again."
            ), True  # fmt: skip
        if not read:
            self._discard(min(max(length, 0), MAX_PART_DISCARD))
        self._answer(status, answer)

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
        self._discard(min(length, max(MAX_DISCARD, 2 * most)))
        return None

    def _discard(self, left: int) -> None:
        """Read `left` bytes of a request's body and throw them away."""
        while left > 0:
            try:
                chunk = self.rfile.read(min(CHUNK, left))
            except OSError:
                break
            if not chunk:
                break
            left -= len(chunk)

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
                self.server.share.busy()  # a film is minutes of this: still syncing
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
