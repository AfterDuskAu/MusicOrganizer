"""Playing a film from a torrent, while it arrives (the owner's yes to libtorrent, 2026-10-07).

A stream from an add-on can be a torrent: an info-hash, and sometimes which file in it
and some trackers. To play one, the engine joins the torrent, asks for the film's
pieces in the order a player needs them, and hands the app an address on this
computer (`http://127.0.0.1:<port>/…`) that the film player opens like any web video.
The player asks for ranges of bytes, as players do; each range is answered as soon as
its pieces are here, which is what makes seeking work.

What the owner should know, and what this module holds to:

- **A torrent shares as it fetches.** While a film plays, the pieces already here are
  given to others in the same torrent. That is how BitTorrent works.
- **While a film is still arriving it's sent on freely; the owner's limit is for
  afterwards** (the owner, 2026-10-12: "while the item still isn't at 100%, it uses
  max"; the choice of 5, 3, 1 MB/s, no limit or nothing is how much to give back once
  it's all here). A film gets most from others while it gives to them, so the limit
  used to slow its arriving. Until that day the limit held the whole time, and
  "nothing" meant nothing was ever sent: now it means nothing once the film is here.
- **No port is opened on the router** (no UPnP, no NAT-PMP), the same promise as
  sharing with a phone.
- **The address is for this computer only:** it listens on 127.0.0.1, and its path
  carries a key made afresh each time the engine starts, so nothing else can ask.
- **What arrives is kept in the app's cache folder**, never in the library, **for a day
  after the film was last played** (the owner, 2026-10-08; until then it was deleted
  the moment the film was closed). Played again within the day, the torrent is joined
  again from the owner's click and finds what's already there, so the film starts
  without being fetched twice. After a day unused it's deleted (`fileops.sweep_cached`,
  when the engine starts and while it runs). libtorrent itself writes those files, in
  the one folder it is given; this module opens them only to read.
- **A torrent is joined only while its film is open** (or being kept): closing the film,
  ten minutes unused, or the engine stopping leaves the torrent. Nothing is fetched or
  shared for a film that's merely remembered.
- **Or for a few seconds, to count who has it** (`count`, the owner's choice of
  2026-10-11: a Count button beside each way to play, so a torrent nobody has can be
  told from one many have before it's opened). Then the torrent is told to fetch
  nothing: no piece of the film is asked for, nothing of it is written, and it's left
  when the count is in. Like everyone in a torrent, this computer can be seen by the
  others in it for those seconds.
- Nothing starts by itself: only `torrent.play`, `torrent.keep` and `torrent.count`,
  each from the owner's click.

In replay mode (`MUSICORG_REPLAY_DIR`, every test) no torrent is ever joined:
`_new_session` refuses.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol

from musicorg.errors import ReplayMissError, UserError

log = logging.getLogger(__name__)

REPLAY_ENV = "MUSICORG_REPLAY_DIR"
INFO_HASH = re.compile(r"[0-9a-f]{40}")
VIDEO_ENDINGS = frozenset(
    {"mkv", "mp4", "m4v", "mov", "avi", "webm", "wmv", "flv", "mpg", "mpeg", "ts", "m2ts"}
)
CONTENT_TYPES = {
    "mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",
    "mkv": "video/x-matroska", "avi": "video/x-msvideo",
}  # fmt: skip
METADATA_WAIT_S = 120  # how long a player's first question waits for the torrent's details
PIECE_WAIT_S = 120  # how long a range waits for its next piece before giving up
AHEAD_PIECES = 12  # asked for ahead of where the player is reading
IDLE_S = 600  # a film nobody has asked about for this long is closed
COUNT_S = 8.0  # how long a torrent is joined to count who has it
COUNT_CHECK_S = 0.5  # how often it's looked at meanwhile
COUNTING = "counting"  # the empty folder in the films' cache a counted torrent is given
KEEP_S = 24 * 3600  # what arrived of a film stays in the cache this long after it was played
SWEEP_EVERY_S = 600
CHUNK = 256 * 1024
READ_WAIT_S = 30  # how long libtorrent is given to hand over a piece it has
PIECES_KEPT = 48 * 1024 * 1024  # of pieces just read, this much is kept in memory a torrent
FLUSH_WAIT_S = 60  # how long a film that's all here is given to be all on the disk


class TorrentError(UserError):
    """A torrent couldn't be joined or played. The message is for the owner."""


# ---- the parts with no network in them ---------------------------------------------------


def magnet(info_hash: str, trackers: list[str] | tuple[str, ...] = ()) -> str:
    from urllib.parse import quote

    if not INFO_HASH.fullmatch(info_hash):
        raise TorrentError("That isn't a torrent's id.")
    return f"magnet:?xt=urn:btih:{info_hash}" + "".join(
        f"&tr={quote(tracker, safe='')}" for tracker in trackers
    )


def choose_file(files: list[tuple[str, int]], wanted: int | None) -> int:
    """Which file in a torrent is the film: the one the add-on named, or else the
    biggest video file (or the biggest file, if none looks like a video)."""
    if not files:
        raise TorrentError("That torrent has no files in it.")
    if wanted is not None and 0 <= wanted < len(files):
        return wanted
    videos = [i for i, (name, _) in enumerate(files) if _ending(name) in VIDEO_ENDINGS]
    return max(videos or range(len(files)), key=lambda i: files[i][1])


def _ending(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def content_type(name: str) -> str:
    return CONTENT_TYPES.get(_ending(name), "application/octet-stream")


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """The first and last byte a `Range` header asks for, in a file of `size` bytes:
    all of it when there's no header, None when the range can't be met (HTTP 416)."""
    if size <= 0:
        return None
    if not header:
        return 0, size - 1
    found = re.fullmatch(r"\s*bytes=(\d*)-(\d*)\s*", header)
    if found is None or (not found.group(1) and not found.group(2)):
        return None
    if not found.group(1):  # "-500": the last 500 bytes
        length = int(found.group(2))
        return (max(size - length, 0), size - 1) if length else None
    first = int(found.group(1))
    last = min(int(found.group(2)), size - 1) if found.group(2) else size - 1
    return (first, last) if first <= last and first < size else None


class Film(Protocol):
    """A film as the address serves it: its name and size, and its bytes once here."""

    name: str
    size: int

    def read(self, offset: int, length: int) -> bytes | None:
        """Bytes from `offset`, at most `length` of them and at least one, waiting for
        them to arrive. None when they didn't come in time or the film was closed."""


# ---- the address the film player opens ---------------------------------------------------


def serve(handler: BaseHTTPRequestHandler, film: Film, *, head: bool = False) -> None:
    """Answer one question about a film: the whole of it, or the range asked for."""
    wanted = parse_range(handler.headers.get("Range"), film.size)
    if wanted is None:
        handler.send_response(416)
        handler.send_header("Content-Range", f"bytes */{film.size}")
        handler.end_headers()
        return
    first, last = wanted
    partial = handler.headers.get("Range") is not None
    handler.send_response(206 if partial else 200)
    handler.send_header("Content-Type", content_type(film.name))
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(last - first + 1))
    if partial:
        handler.send_header("Content-Range", f"bytes {first}-{last}/{film.size}")
    handler.end_headers()
    if head:
        return
    offset = first
    while offset <= last:
        data = film.read(offset, min(CHUNK, last - offset + 1))
        if not data:
            return  # it didn't arrive, or the film was closed: the player asks again
        try:
            handler.wfile.write(data)
        except OSError:
            return  # the player went elsewhere (a seek, or it was shut)
        offset += len(data)


def sweep(cache: Path, skip: list[str] | tuple[str, ...] = ()) -> int:
    """Delete what has sat in the films' cache folder unused for over a day
    (`fileops` does it, and only inside the app's cache). Returns how many went."""
    from musicorg import fileops
    from musicorg.errors import MusicOrgError

    try:
        return fileops.sweep_cached(cache / "torrents", cache=cache, older_than_s=KEEP_S, skip=skip)
    except (MusicOrgError, OSError) as exc:
        log.warning("The films' cache couldn't be cleared: %s", exc)
        return 0


# ---- libtorrent: the only place a torrent is joined --------------------------------------


def _new_session() -> Any:
    if os.environ.get(REPLAY_ENV):
        raise ReplayMissError(
            "Replay mode: no torrent is joined. Stand in for torrents._new_session."
        )
    import libtorrent as lt

    return lt.session(
        {
            "listen_interfaces": "0.0.0.0:0",  # any free port, chosen by the system
            "enable_upnp": False,  # no port is opened on the router
            "enable_natpmp": False,
            "enable_dht": True,
            "enable_lsd": False,
            "user_agent": "musicorg",
            # What it must say back: a piece that was asked for (`read_piece`), and that
            # a film's last parts are on the disk (`flush_cache`). Nothing else.
            "alert_mask": int(lt.alert.category_t.storage_notification)
            | int(lt.alert.category_t.error_notification),
        }
    )


def upload_settings(limit: int | None) -> dict[str, int]:
    """libtorrent's settings for how fast films are sent on to others. None: no limit.
    A number: that many bytes a second at most, over every torrent together. 0: nothing
    is sent at all: no other computer is ever given a turn (which can make a film
    arrive more slowly, since others give more to those who give)."""
    if limit is None:
        return {"upload_rate_limit": 0, "unchoke_slots_limit": 8}  # libtorrent's own
    if limit <= 0:
        return {"upload_rate_limit": 1024, "unchoke_slots_limit": 0}
    return {"upload_rate_limit": int(limit), "unchoke_slots_limit": 8}


def who_has(told: Any) -> tuple[int, int]:
    """How many have a torrent, as far as libtorrent knows: `(seeds, everyone)`. Seeds
    have all of it; everyone is the seeds and the leechers (the others sharing it).

    Two ways of knowing, and the larger of the two for each figure:

    - **a tracker's own count**, where the torrent has a tracker and it has answered
      (`num_complete`, `num_incomplete`): the figures a torrent's page on the web shows;
    - **those it's connected to**, seeds and others apart (`num_seeds`, `num_peers`).
      Seeds it has met and isn't connected to just now count as seeds too (`list_seeds`).

    **A peer it has only heard of is not counted as a leecher** (`list_peers`). Whether
    such a peer has all of the film isn't known until it's been asked, and most are
    never asked. (As first built, on 2026-10-11, they were all taken for leechers: a
    count read "7/2045" for a torrent that read "40/5" a minute into playing. The
    owner saw it the same evening.) So without a tracker's count this is "at least
    this many, met so far", and it grows the longer the torrent is joined."""

    def count(name: str) -> int:
        value = getattr(told, name, 0)
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

    seeds = max(count("num_seeds"), count("list_seeds"), count("num_complete"))
    leechers = max(count("num_peers") - count("num_seeds"), count("num_incomplete"), 0)
    return seeds, seeds + leechers


@dataclass
class _Joined:
    """One torrent that's been joined: libtorrent's handle, and the film in it."""

    handle: Any
    info_hash: str
    file_index: int | None
    folder: Path
    last_asked: float = field(default_factory=time.monotonic)
    closed: bool = False
    # Keeping the film (the owner clicked Keep): the name to save it under, without its
    # ending, and the folders inside the Movies folder it goes in (an episode's: its
    # show's); then where it was saved, or why it couldn't be.
    keep_name: str | None = None
    keep_folder: tuple[str, ...] = ()
    kept_path: str | None = None
    keep_error: str | None = None
    playing: bool = True  # False once the player has let go but the film is still wanted
    # Kept as a file phones and tablets play (Settings → Downloads): converted first if
    # it isn't one. While that's being done, how far along it is, 0 to 1.
    convert: bool = False
    converting: float | None = None
    saving: bool = False  # it has all arrived and is being converted or copied now
    keep_note: str | None = None  # something the owner should know about how it was kept
    # Filled in once the torrent's details have arrived:
    index: int = 0
    name: str = ""
    size: int = 0
    path: Path | None = None
    file_offset: int = 0
    piece_length: int = 0
    top: str = ""  # the file or folder in the cache folder that the film is in
    # A film's bytes are asked of libtorrent, a piece at a time (`_piece`), not read
    # from its file. False only for a stand-in handle in a test, which has no pieces.
    through_libtorrent: bool = False
    _pieces: OrderedDict[int, bytes] = field(default_factory=OrderedDict, repr=False)
    _asked: dict[int, threading.Event] = field(default_factory=dict, repr=False)
    _pieces_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    flushed: threading.Event = field(default_factory=threading.Event, repr=False)
    # Another file of the same torrent being played while this one is kept (an episode
    # watched while another downloads): its number. See `turn_to`.
    beside: int | None = None

    def asked_now(self) -> None:
        self.last_asked = time.monotonic()

    def ready(self, wait_s: float) -> bool:
        """Wait for the torrent's details (its files), and choose the film in it."""
        until = time.monotonic() + wait_s
        while self.path is None:
            # Looked for before the clock is: with no time to wait (a film that's only
            # being kept, looked in on every two seconds) details that have arrived
            # must still be read. (They weren't: found by a real run, 2026-10-07.)
            info = self.handle.torrent_file() if self.handle.is_valid() else None
            if info is None:
                if self.closed or time.monotonic() >= until:
                    return False
                time.sleep(0.25)
                continue
            files = info.files()
            listed = [(files.file_path(i), files.file_size(i)) for i in range(files.num_files())]
            chosen = choose_file(listed, self.file_index)
            # Only the film is fetched, in the order it's read.
            self.handle.prioritize_files([4 if i == chosen else 0 for i in range(len(listed))])
            self.index = chosen
            self.name, self.size = Path(listed[chosen][0]).name, listed[chosen][1]
            self.file_offset = files.file_offset(chosen)
            self.piece_length = info.piece_length()
            self.path = self.folder / listed[chosen][0]
            self.top = Path(listed[chosen][0]).parts[0]
        return True

    def read(self, offset: int, length: int) -> bytes | None:
        if self.path is None:
            return None
        return self.read_file(self.path, self.file_offset, self.size, offset, length)

    def read_file(
        self, path: Path, file_offset: int, size: int, offset: int, length: int
    ) -> bytes | None:
        """Bytes of one file of the torrent, from `offset` in it: at most `length`, and
        no further than the end of the piece they're in (the next may not be here).

        **They're asked of libtorrent, not read from the file** (2026-10-14). A piece
        libtorrent says it has (`have_piece`) has passed its check, but its last blocks
        may still be waiting to be written: read from the file in that moment, they're
        whatever was there before, and the film player was given them as film. Measured
        that day with an open film arriving no faster than it was read: one part in
        121 served wasn't what the finished file holds. The owner saw it as a picture
        that broke into pink and purple while an episode was arriving, and played
        cleanly once it had all arrived. `read_piece` gives the piece as libtorrent
        itself holds it, written or not."""
        self.asked_now()
        if self.closed or self.piece_length <= 0 or not 0 <= offset < size:
            return None
        place = file_offset + offset
        piece = place // self.piece_length
        # Never past the end of the file (the piece's rest is the next file's), nor of
        # the piece.
        length = min(length, size - offset, (piece + 1) * self.piece_length - place)
        last_piece = (file_offset + size - 1) // self.piece_length
        for ahead, wanted in enumerate(range(piece, min(piece + AHEAD_PIECES, last_piece) + 1)):
            self.handle.set_piece_deadline(wanted, 500 * (ahead + 1))
        until = time.monotonic() + PIECE_WAIT_S
        while not self.handle.have_piece(piece):
            if self.closed or time.monotonic() > until:
                return None
            time.sleep(0.1)
        if not self.through_libtorrent:
            try:
                with path.open("rb") as file:
                    file.seek(offset)
                    return file.read(length) or None
            except OSError:
                return None
        whole = self._piece(piece)
        if whole is None:
            return None
        inside = place - piece * self.piece_length
        return whole[inside : inside + length] or None

    def _piece(self, piece: int) -> bytes | None:
        """One piece libtorrent has, as it holds it. The pieces just read are kept (a
        player reads a piece in several goes, and goes back a little), so each is asked
        for once."""
        with self._pieces_lock:
            held = self._pieces.get(piece)
            if held is not None:
                self._pieces.move_to_end(piece)
                return held
            waiting = self._asked.get(piece)
            first = waiting is None
            if waiting is None:
                waiting = self._asked[piece] = threading.Event()
        if first:
            self.handle.read_piece(piece)  # answered by `piece_arrived`, from the alerts
        until = time.monotonic() + READ_WAIT_S
        while not waiting.wait(0.25):
            if self.closed or time.monotonic() > until:
                with self._pieces_lock:
                    self._asked.pop(piece, None)
                return None
        with self._pieces_lock:
            return self._pieces.get(piece)

    def piece_arrived(self, piece: int, data: bytes | None) -> None:
        """libtorrent has handed over a piece that was asked for (None: it couldn't)."""
        with self._pieces_lock:
            if data:
                self._pieces[piece] = data
                self._pieces.move_to_end(piece)
                while len(self._pieces) > 2 and sum(map(len, self._pieces.values())) > PIECES_KEPT:
                    self._pieces.popitem(last=False)
            waiting = self._asked.pop(piece, None)
        if waiting is not None:
            waiting.set()

    def film(self, index: int | None) -> _View | None:
        """One file of the torrent, as the address serves it: the film this torrent was
        joined for (`index` None, or its own number), or another file of it by its
        number. None if the torrent has no such file. Its details must have arrived."""
        if self.path is None:
            return None
        if index is None or index == self.index:
            return _View(self, self.path, self.name, self.size, self.file_offset)
        info = self.handle.torrent_file()
        files = info.files() if info is not None else None
        if files is None or not 0 <= index < files.num_files():
            return None
        if index != self.beside:
            # The one played beside it before isn't fetched any more; this one is.
            if self.beside is not None:
                self.handle.file_priority(self.beside, 0)
            self.handle.file_priority(index, 4)
            self.beside = index
        inside = files.file_path(index)
        return _View(
            self, self.folder / inside, Path(inside).name, files.file_size(index),
            files.file_offset(index),
        )  # fmt: skip

    def turn_to(self, index: int | None) -> None:
        """The player wants another file of this torrent now (the next episode of a
        season that's one torrent). Until 2026-10-14 it was answered with the file the
        torrent was first joined for: the same episode played again under the next
        one's name (the owner: "it restarts the current film, but says its showing the
        next episode").

        A torrent that's only being played becomes that file's: it alone is fetched
        from now. One that's being kept keeps fetching its own film, and the other is
        fetched beside it (`film`)."""
        if index is None or index == (self.index if self.path is not None else self.file_index):
            return
        if self.keep_name is not None or self.saving:
            return  # kept: `film(index)` gives the other file, beside it
        self.file_index = index
        self.beside = None
        self.path = None  # `ready` chooses again, and asks for this file only

    @property
    def wanted(self) -> bool:
        """Being kept, and not there yet: it isn't closed for being left alone."""
        return self.keep_name is not None and self.kept_path is None and self.keep_error is None

    def arriving(self) -> bool:
        """Not all of the film is here yet (its details may not even be)."""
        if self.closed or not self.handle.is_valid():
            return False
        told = self.handle.status()
        return not (told.progress_ppm >= 1_000_000 or told.is_seeding)

    def all_here(self) -> bool:
        """Every byte of the film's file has arrived."""
        if self.path is None or not self.handle.is_valid():
            return False
        return bool(self.size) and self.handle.file_progress()[self.index] >= self.size

    def status(self) -> dict[str, Any]:
        self.asked_now()
        told = self.handle.status()
        if self.path is None:
            state = "finding"
        elif told.progress_ppm >= 1_000_000 or told.is_seeding:
            state = "complete"
        else:
            state = "fetching"
        return {
            "info_hash": self.info_hash,
            "keeping": self.keep_name is not None and self.kept_path is None,
            "kept_path": self.kept_path,
            "keep_error": self.keep_error,
            "keep_note": self.keep_note,
            "converting": self.converting,
            "state": state,
            "name": self.name or None,
            "bytes": self.size or None,
            # Counted by the engine itself, of those it's connected to now: everyone,
            # and how many of them have the whole film (2026-10-11). No add-on says this.
            "peers": int(told.num_peers),
            "seeds": min(int(told.num_seeds), int(told.num_peers)),
            # Who has the torrent, as a count from the owner's click reckons it
            # (`who_has`): the same measure for a film that's open as for one that isn't.
            "swarm_seeds": who_has(told)[0],
            "swarm_peers": who_has(told)[1],
            "bytes_per_second": int(told.download_rate),
            "progress": round(told.progress_ppm / 1_000_000, 4),
        }


@dataclass(frozen=True)
class _View:
    """One file of a joined torrent, as the address serves it: where it lies in the
    torrent doesn't change under a player that's part way through it, whatever the
    torrent is turned to next."""

    joined: _Joined
    path: Path
    name: str
    size: int
    file_offset: int

    def read(self, offset: int, length: int) -> bytes | None:
        return self.joined.read_file(self.path, self.file_offset, self.size, offset, length)


class Player:
    """The films being played from torrents, and the address they're played at. One
    for the engine, made the first time a film is asked for."""

    def __init__(
        self, cache: Path, movies: Path | None = None, *, upload: int | None = None
    ) -> None:
        self.folder = cache / "torrents"
        self.movies = movies  # where a kept film goes; None: films can't be kept
        self.upload = upload  # bytes a second sent on to others at most; None: no limit
        self._key = secrets.token_urlsafe(24)
        self._session: Any = None
        self._server: ThreadingHTTPServer | None = None
        self._joined: dict[str, _Joined] = {}
        self._sending: dict[str, int] | None = None  # what libtorrent was last told
        self._counting: set[str] = set()  # the torrents being counted just now
        self._hurry: set[str] = set()  # ...of which these are wanted to play: count no longer
        self._lock = threading.Lock()
        self._stopping = threading.Event()

    # -- what the app asks for --

    def play(self, info_hash: str, file_index: int | None, trackers: list[str]) -> dict[str, Any]:
        """Join a torrent (or carry on with one already joined) and give the address
        its film plays at. Returns at once: the address waits for the film."""
        info_hash = info_hash.lower()
        link = magnet(info_hash, trackers)
        if info_hash in self._counting:
            # It's being counted, which fetches nothing: that ends now, and the film is
            # joined in its own right (one torrent can't be both in one session).
            self._hurry.add(info_hash)
            until = time.monotonic() + 5
            while info_hash in self._counting and time.monotonic() < until:
                time.sleep(0.05)
        with self._lock:
            self._start()
            joined = self._joined.get(info_hash)
            if joined is None:
                import libtorrent as lt

                params = lt.parse_magnet_uri(link)
                params.save_path = str(self.folder)
                handle = self._session.add_torrent(params)
                joined = _Joined(handle, info_hash, file_index, self.folder)
                joined.through_libtorrent = True
                self._joined[info_hash] = joined
            else:
                # Joined already: perhaps for another file of it (the episode before).
                joined.turn_to(file_index)
            joined.asked_now()
            port = self._server.server_address[1] if self._server else 0
        self._apply_upload()  # a film that's arriving is sent on freely, from the start
        # The address names the file, so one torrent's films each have their own.
        which = "" if file_index is None else f"/{file_index}"
        return {
            "info_hash": info_hash,
            "url": f"http://127.0.0.1:{port}/{self._key}/{info_hash}{which}",
        }

    def status(self, info_hash: str) -> dict[str, Any]:
        joined = self._joined.get(info_hash.lower())
        if joined is None:
            raise TorrentError("That film isn't being played any more.")
        return joined.status()

    def count(self, info_hash: str, trackers: list[str]) -> dict[str, Any]:
        """Count who has a torrent, from the owner's click on Count (2026-10-11):
        `{info_hash, seeds, peers}`, seeds among the peers (`who_has`: a tracker's own
        count where there is one, else those met in the time it had).

        A film that's open or being kept is counted as it stands. Any other torrent is
        joined for `COUNT_S` seconds **to count and nothing more**: it's told to fetch
        nothing (`upload_mode`: no piece is ever asked for), given an empty folder so
        there's nothing of an earlier playing to read through, and left when the time
        is up (or at once, if the owner presses Play or Keep on it meanwhile). No
        piece of the film is fetched, written or kept. This takes those seconds: the
        app asks from a thread of its own."""
        info_hash = info_hash.lower()
        if not INFO_HASH.fullmatch(info_hash):
            raise TorrentError("That isn't a torrent's id.")
        with self._lock:
            joined = self._joined.get(info_hash)
            if joined is not None and joined.handle.is_valid():
                seeds, everyone = who_has(joined.handle.status())
                return {"info_hash": info_hash, "seeds": seeds, "peers": everyone}
            if info_hash in self._counting:
                raise TorrentError("That one is being counted already.")
            self._start()
            import libtorrent as lt

            params = lt.parse_magnet_uri(magnet(info_hash, trackers))
            params.save_path = str(self.folder / COUNTING)
            params.flags |= lt.torrent_flags.upload_mode  # it asks for no piece, ever
            handle = self._session.add_torrent(params)
            self._counting.add(info_hash)
            # Its trackers, if it has any, are asked for their own count: the surest
            # figures there are, and the quickest.
            ask = getattr(handle, "scrape_tracker", None)
            if ask is not None:
                try:
                    ask()
                except RuntimeError:
                    pass
        seeds = everyone = 0
        try:
            until = time.monotonic() + COUNT_S
            while time.monotonic() < until and not self._stopping.is_set():
                time.sleep(COUNT_CHECK_S)
                if not handle.is_valid():
                    break
                found = who_has(handle.status())
                seeds, everyone = max(seeds, found[0]), max(everyone, found[1])
                if info_hash in self._hurry:
                    break  # the owner pressed Play or Keep on it: what was found will do
        finally:
            with self._lock:
                # Left, always: a count never turns into the film's own joining.
                if self._session is not None and handle.is_valid():
                    self._session.remove_torrent(handle)
                self._counting.discard(info_hash)
                self._hurry.discard(info_hash)
        log.info("torrents: counted who has a torrent (%d with all of it, %d others)",
                 seeds, everyone - seeds)  # fmt: skip
        return {"info_hash": info_hash, "seeds": seeds, "peers": max(everyone, seeds)}

    def keep(
        self,
        info_hash: str,
        file_index: int | None,
        trackers: list[str],
        name: str,
        *,
        folder: tuple[str, ...] = (),
        convert: bool = False,
    ) -> dict[str, Any]:
        """Keep a film: fetch all of it, then copy it into the Movies folder under
        `name` (its own ending is added), in `folder` inside it (an episode's: its
        show's and season's, as `naming.kept_place` gives them; a film's: none). It
        carries on after the player is shut, for as long as the engine runs; a film
        that's already playing is kept from where it has got to. With `convert`, a
        film that phones and tablets can't play is made into an MP4 they can first
        (`convert`). Returns its status, as `status` does."""
        if self.movies is None:
            raise TorrentError("There's nowhere set to download films to on this computer.")
        if not name.strip():
            raise TorrentError("A film needs a name to be saved under.")
        self.play(info_hash, file_index, trackers)
        joined = self._joined[info_hash.lower()]
        joined.keep_name, joined.keep_error, joined.convert = name.strip(), None, convert
        joined.keep_folder = tuple(folder)
        return joined.status()

    def set_upload(self, limit: int | None) -> None:
        """Settings changed: how fast films are sent on to others, from now."""
        self.upload = limit
        self._apply_upload()

    def _apply_upload(self) -> None:
        """Tell libtorrent how fast films may be sent on, as things stand now: **with no
        limit while any film is still arriving, and at the owner's limit once every
        film that's open is all here** (the owner, 2026-10-12). Looked at again every
        couple of seconds (`_close_idle`), so a film that finishes arriving comes
        under the limit then; libtorrent is only told when the answer changes."""
        apply = getattr(self._session, "apply_settings", None)  # a test's stand-in has none
        if apply is None:
            return
        arriving = any(joined.arriving() for joined in list(self._joined.values()))
        wanted = upload_settings(None if arriving else self.upload)
        if wanted != self._sending:
            apply(wanted)
            self._sending = wanted

    def stop_keeping(self, info_hash: str, *, playing: bool = False) -> None:
        """The owner has changed their mind about keeping a film. It's left at once,
        unless the app says it's `playing` (then it plays on, and is left when the
        player is shut). What arrived stays in the cache its day, like any film's. One
        that's already being written into the Movies folder is finished: that takes a
        moment."""
        with self._lock:
            joined = self._joined.get(info_hash.lower())
            if joined is None or not joined.wanted or joined.saving:
                return
            joined.keep_name = None
            if playing:
                return
            self._joined.pop(info_hash.lower(), None)
        self._leave(joined, delete=False)

    def close(self, info_hash: str) -> None:
        """Leave a torrent. What arrived of it stays in the cache for a day, unless the
        film has been saved to the Movies folder, when the cache's copy goes at once.
        One that's being kept isn't left: the player has only let go of it, and it's
        left when it has been saved."""
        with self._lock:
            joined = self._joined.get(info_hash.lower())
            if joined is not None and joined.wanted:
                joined.playing = False
                return
            joined = self._joined.pop(info_hash.lower(), None)
        if joined is not None:
            self._leave(joined, delete=joined.kept_path is not None)

    def stop(self) -> None:
        """Leave every torrent and stop listening. What arrived stays its day."""
        self._stopping.set()
        with self._lock:
            leaving, self._joined = list(self._joined.values()), {}
            server, self._server = self._server, None
        for joined in leaving:
            self._leave(joined, delete=False)
        if server is not None:
            server.shutdown()
            server.server_close()
        if leaving and self._session is not None:
            time.sleep(1)  # libtorrent closes its files on its own thread
        self._session = None
        self._sending = None

    def sweep(self) -> int:
        """Delete what has been in the cache unused for over a day. Films open now stay."""
        return sweep(self.folder.parent, skip=[j.top for j in self._joined.values() if j.top])

    # -- the workings --

    def _leave(self, joined: _Joined, *, delete: bool) -> None:
        joined.closed = True
        if self._session is not None and joined.handle.is_valid():
            import libtorrent as lt

            if delete:
                # libtorrent deletes its own files.
                self._session.remove_torrent(joined.handle, lt.session.delete_files)
                return
            self._session.remove_torrent(joined.handle)
        if joined.top:
            from musicorg import fileops

            # Its day in the cache is counted from now, when it was last played.
            fileops.touch_cached(self.folder / joined.top, cache=self.folder.parent)

    def _start(self) -> None:
        if self._session is not None:
            return
        self._session = _new_session()
        self._apply_upload()
        self._stopping.clear()
        player = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:
                pass  # nothing on stdout or stderr: stdout carries the protocol

            def do_GET(self) -> None:
                player._answer(self, head=False)

            def do_HEAD(self) -> None:
                player._answer(self, head=True)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        threading.Thread(target=self._close_idle, daemon=True).start()
        if hasattr(self._session, "pop_alerts"):  # (a test's stand-in has none)
            threading.Thread(target=self._hear, args=(self._session,), daemon=True).start()

    def _hear(self, session: Any) -> None:
        """What libtorrent says back, for as long as the engine plays films: a piece
        that was asked for, and that a film's last parts are on the disk."""
        while not self._stopping.is_set() and self._session is session:
            try:
                session.wait_for_alert(500)
                alerts = session.pop_alerts()
            except RuntimeError:
                return
            for alert in alerts:
                kind = type(alert).__name__
                if kind not in ("read_piece_alert", "cache_flushed_alert"):
                    continue
                try:
                    joined = self._joined.get(str(alert.handle.info_hash()))
                except RuntimeError:
                    continue  # its torrent has been left meanwhile
                if joined is None:
                    continue
                if kind == "cache_flushed_alert":
                    joined.flushed.set()
                    continue
                error = getattr(alert, "error", None) or getattr(alert, "ec", None)
                failed = error is not None and error.value() != 0
                joined.piece_arrived(int(alert.piece), None if failed else bytes(alert.buffer))

    def _answer(self, handler: BaseHTTPRequestHandler, *, head: bool) -> None:
        # /<key>/<torrent>, or /<key>/<torrent>/<file's number> for one file of several.
        parts = handler.path.split("?", 1)[0].strip("/").split("/")
        well_formed = len(parts) in (2, 3) and (len(parts) == 2 or parts[2].isdigit())
        joined = self._joined.get(parts[1]) if well_formed else None
        if not well_formed or not secrets.compare_digest(parts[0], self._key) or joined is None:
            handler.send_error(404)
            return
        joined.asked_now()
        if not joined.ready(METADATA_WAIT_S):
            handler.send_error(504, "The torrent's details didn't arrive.")
            return
        film = joined.film(int(parts[2]) if len(parts) == 3 else None)
        if film is None:
            handler.send_error(404)
            return
        serve(handler, film, head=head)

    def _save_kept(self, joined: _Joined) -> None:
        """A film that's being kept and has all arrived: copy it into the Movies
        folder, converted first if that was asked for and it needs it. `fileops` does
        the writing; it never overwrites."""
        from musicorg import convert, fileops
        from musicorg.errors import MusicOrgError

        assert joined.path is not None and self.movies is not None
        joined.saving = True
        # Every piece has arrived; the last of them may not be written yet. The file is
        # copied once libtorrent says it's all on the disk (the same fault as `read_file`
        # mends: a film kept in that moment had holes in it).
        if joined.through_libtorrent:
            joined.flushed.clear()
            joined.handle.flush_cache()
            if not joined.flushed.wait(FLUSH_WAIT_S):
                log.warning("A kept film's last parts weren't confirmed written; kept anyway.")
        source, made = joined.path, None
        if joined.convert:
            try:
                wanted = convert.plan(joined.path.name, convert.streams_of(joined.path))
                if wanted is not None:
                    # ffmpeg writes this one file, in the films' cache folder.
                    made = self.folder / f".converting-{joined.info_hash}{convert.ENDING}"
                    joined.converting = 0.0
                    log.info("Converting a kept film (%s).", wanted.about)

                    def heard(done: float) -> None:
                        joined.converting = done

                    convert.run(
                        joined.path, made, wanted, progress=heard,
                        should_stop=lambda: self._stopping.is_set() or joined.closed,
                    )  # fmt: skip
                    source = made
            except MusicOrgError as exc:
                if self._stopping.is_set() or joined.closed:
                    joined.saving = False
                    return  # the engine is stopping: nothing is kept half-made
                # It's kept as it arrived instead, and the owner is told.
                joined.keep_note = f"It was left as it arrived: {exc.message}"
                log.warning("A kept film wasn't converted: %s", exc.message)
                source = joined.path
            joined.converting = None
        ending = source.suffix or ".mp4"
        try:
            saved = fileops.keep_media(
                source,
                self.movies.joinpath(*joined.keep_folder),
                f"{joined.keep_name}{ending}",
                cache=self.folder,
                allowed=[self.movies],
            )
            joined.kept_path = str(saved)
        except (MusicOrgError, ValueError) as exc:
            joined.keep_error = getattr(exc, "message", None) or str(exc)
            log.warning("A film couldn't be kept: %s", joined.keep_error)
        if made is not None:
            fileops.forget_cached(made, cache=self.folder.parent)
        joined.saving = False
        if not joined.playing:
            self.close(joined.info_hash)  # nobody is watching it: its cache goes

    def _close_idle(self) -> None:
        swept = time.monotonic()
        while not self._stopping.wait(2):
            now = time.monotonic()
            self._apply_upload()  # a film that has all arrived comes under the owner's limit
            if now - swept > SWEEP_EVERY_S:
                swept = now
                self.sweep()
            for joined in list(self._joined.values()):
                if joined.wanted:
                    # Being kept: its details are waited for here if nothing is playing
                    # it, and it's saved once it has all arrived.
                    # (On its own thread: converting a film can take a long while, and the
                    # other films mustn't wait for it.)
                    if not joined.saving and joined.ready(0) and joined.all_here():
                        joined.saving = True
                        threading.Thread(
                            target=self._save_kept, args=(joined,), daemon=True
                        ).start()
                    continue
                if now - joined.last_asked > IDLE_S:
                    log.info("A film nobody asked about for ten minutes was closed.")
                    self.close(joined.info_hash)
