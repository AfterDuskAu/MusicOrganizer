"""Playing a film from a torrent, while it arrives (the owner's yes to libtorrent, 2026-10-07).

A stream from an add-on can be a torrent: an info-hash, and sometimes which file in it
and some trackers. To play one, the engine joins the torrent, asks for the film's
pieces in the order a player needs them, and hands the app an address on this
computer (`http://127.0.0.1:<port>/…`) that the film player opens like any web video.
The player asks for ranges of bytes, as players do; each range is answered as soon as
its pieces are here, which is what makes seeking work. While a film arrives its pieces are
handed over by libtorrent; once all of it is here, they're read from its file, each checked
against the torrent's own fingerprint for it (2026-11-17: `_Joined.read_file`).

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
  the one folder it is given; this module opens them only to read. Beside them goes
  what libtorrent knows of the film when it's left (2026-10-17: `.<hash>.resume`,
  written by `fileops.write_cached`), so the film played again is there at once, not
  checked through from 0%; it goes with the film after its day. **It's written only once
  libtorrent has written out everything it holds of the film**, so it never says a part
  is here that isn't on the disk; on a slow disk that's waited for as long as it takes,
  away from the app (2026-11-18: `Player._leave`, `Player.stop`).
- **A torrent is joined only while its film is open** (or being kept): closing the film,
  ten minutes unused, or the engine stopping leaves the torrent. Nothing is fetched or
  shared for a film that's merely remembered.
- **Who has a torrent is asked of its trackers, never by joining it** (`scrape`, the
  owner, 2026-11-12: "Replace a manual count, to an automatic person detector"). When a
  film's ways to play are shown, the app asks how many have each of its torrents, and the
  trackers (the way's own and a few public ones) are asked, by a scrape: one small
  question each, every torrent of the page in it. They see the torrents' ids and this
  computer's address; nobody connects to this computer for it, and nothing is fetched.
  (From 2026-10-11 it was the owner's click on Count, which joined the torrent for a few
  seconds; a count heard from one tracker at most, the first the way listed, and a way
  whose first tracker didn't answer read 6/7 for a film its trackers said 251/67 of.)
- Nothing is joined by itself: only `torrent.play` and `torrent.keep`, each from the
  owner's click (and the next episode, by the owner's switch: the app's).

In replay mode (`MUSICORG_REPLAY_DIR`, every test) no torrent is ever joined:
`_new_session` refuses; and no tracker is asked but one on this computer (a test's).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import socket
import struct
import sys
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
# What's asked for first, before anything else of the film (2026-10-17), each with a
# deadline (libtorrent's top priority): its start, for a player to begin; its end, where an
# MP4's index or an MKV's cues may be; and, as it plays, what's just ahead of where the
# player is reading. Each at least a piece (two at the start and ahead), and few: among
# themselves they come in any order, so the more there are, the longer the very next one
# can take.
START_BYTES = 1024 * 1024
END_BYTES = 1024 * 1024
NEXT_BYTES = 1024 * 1024
NEXT_PIECES = 12  # at most
# After that, the next part of the film is wanted sooner than the rest of it, and the part
# after that a little sooner too: so the film arrives roughly in order from where it's
# played, not from all over. Moved on each time the player has read another step of it.
SOON_BYTES = 32 * 1024 * 1024
LATER_BYTES = 96 * 1024 * 1024
STEP_BYTES = 8 * 1024 * 1024
# libtorrent's priorities for those (1 to 7; what's asked for first has 7, the rest of the
# film its own 4). It weighs a piece's priority against how few others have it, so these
# only lean the order towards the player's; what's asked for first leans hardest. (Tried
# with the rest at 1 and these at 3 and 2, to lean harder: no quicker, measured.)
FILM_PRIORITY = 4
SOON_PRIORITY = 6
LATER_PRIORITY = 5
DEADLINE_MS = 250  # each piece asked for first is wanted this much after the one before it
NUDGE_S = 2.0  # a range still waiting for its piece asks for it again this often
PRIORITY_WAIT_S = 5.0  # how long a film's new file priorities are given to take hold
IDLE_S = 600  # a film nobody has asked about for this long is closed
# Sharers are reached over TCP first (2026-11-12, the owner: "think of a way to improve
# download speed from torrents"). Measured that day on open-licence films with libtorrent
# 2.1: with uTP, which libtorrent tries first for every sharer, nearly everything came over
# it, and uTP gives way whenever the home line is busy, so the last fifth of a film crawled;
# over TCP, they finished 3 to 4 times sooner (Sintel 28 s to 9 s, Big Buck Bunny 42-50 s to
# 11.5 s). So a film's first seconds are TCP's alone: every sharer then listed is tried over
# TCP, and those that answer stay on it.
#
# But uTP is never off for long (the same night: a film shared 3/9 started with the engine
# still uTP first, and found nobody on a video app's engine that wasn't). A sharer at home
# behind a router is reached only by libtorrent's hole punch, which is uTP's alone, both ways;
# with uTP off unless a film had had nothing at all, one that had a little over TCP never
# reached the rest. So uTP is always taken from sharers who reach us, and is let on for ours
# after this head start (`Player._tell_transport`). Then, for a while, a sharer that couldn't
# be reached is tried again this soon (libtorrent's own wait is a minute, then two); only for
# a while, since each try that fails counts, and after three libtorrent gives up on it.
TCP_HEAD_START_S = 5.0
RETRY_AFTER_S = 2
RETRY_FOR_S = 15.0
KEEP_S = 24 * 3600  # what arrived of a film stays in the cache this long after it was played
SWEEP_EVERY_S = 600
CHUNK = 256 * 1024
READ_WAIT_S = 30  # how long libtorrent is given to hand over a piece it has
PIECES_KEPT = 48 * 1024 * 1024  # of pieces just read, this much is kept in memory a torrent
# Whether all of a film has arrived is asked of libtorrent this often at most, until it has
# (from then on its pieces are read from its file: `_Joined.read_file`, 2026-11-17).
ARRIVED_LOOK_S = 1.0
SLOW_DISK_S = 2.0  # a piece read from a film's file taking longer than this is logged
FLUSH_WAIT_S = 60  # how long a film that's all here is given to be all on the disk
# What libtorrent knows of a film that was left (2026-10-17), kept beside it in the cache
# (`.<hash>.resume`), so it's all there at once when it's played again within its day.
RESUME = ".resume"
PARTS = ".parts"  # libtorrent's own file of the pieces a film shares with files not fetched
# How long a film the app has closed is given to be all on the disk before what's here of it
# is noted (2026-11-18). It was 3 s, for the app and the engine's stopping alike, and on the
# owner's Mac a film was left with nothing noted ("the disk was slow", twice on 17 Nov for a
# 6.5 GB film), so the next play checked all of it again, for minutes. libtorrent does its disk
# work at macOS's lowest priority (its `low_prio_disk`), behind everything else on a start-up
# disk that's often busy. Waited for on a thread of its own, with the film paused: the app isn't
# kept waiting, and played again meanwhile the film just carries on (`Player._take_back`).
LEAVE_FLUSH_S = 120.0
RESUME_WAIT_S = 2.0  # how long libtorrent is given to say what's here of a film
LEAVE_WAIT_S = 8.0  # how long a film played again waits for its leaving to be over
STOP_TRACKER_S = 1  # how long a tracker is given to hear that this computer has gone
# How much of what has arrived may wait in memory to be written (2026-10-17). libtorrent
# writes all of it before anything more of a film is read, the moment the film has all
# arrived: the player waited that long right then (the owner: "it pauses the show when
# moving to 100%"). With libtorrent's own 100 MB, a film arriving at about 10 MB/s took
# 1.5 to 3 seconds to write out on this computer, measured when it was left.
DISK_QUEUE_BYTES = 16 * 1024 * 1024
CHECKING = frozenset({"checking_files", "checking_resume_data", "queued_for_checking"})


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
    # One answer to a connection, and said so (2026-10-18). The film player's FFmpeg (8.1)
    # asks the next question on the same connection when it has read an answer to its end
    # and wasn't told the connection closes: after reading a film's last bytes (an MKV's
    # cues, read at the first jump in it) its next question met a closed connection, it
    # took the film for ended, and the picture stopped for good. The owner saw it as a
    # film that froze when its sound track was changed, and one that never started when
    # opened part way through. And an answer cut short (a piece that didn't come) must
    # end its connection, or the player waits on it for the rest.
    handler.send_header("Connection", "close")
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


# ---- who has a torrent, asked of its trackers (no torrent is joined) ---------------------

# Public trackers every way is asked of too, besides its own (the same six the video apps
# give every torrent). Most of a film's sharers are known to the first of them.
PUBLIC_TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://explodie.org:6969/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://open.demonii.com:1337/announce",
)
SCRAPE_S = 3.0  # a tracker is given this long in all, and the asking is over by then
# ...or this long after the first tracker answered, whichever is sooner (2026-11-12: one that
# never answers held the figure for the whole 3 s; measured, those that answer do within
# about 0.7 s). Those still out then are left, and what they say later doesn't count.
SCRAPE_AFTER_FIRST_S = 1.0
SCRAPE_AGAIN_S = 1.0  # a question nothing came back for in this long is sent again, once
SCRAPE_MOST = 70  # torrents in one question (a packet holds about 74)
SCRAPE_WAYS_MOST = 500  # torrents asked about at once, at most
UDP_TRACKER_ID = 0x41727101980  # what a UDP tracker's first question begins with (BEP 15)
_ON_THIS_COMPUTER = frozenset({"127.0.0.1", "::1", "localhost"})


def scrape(
    ways: list[tuple[str, list[str]]], *, public: tuple[str, ...] | list[str] = PUBLIC_TRACKERS
) -> dict[str, tuple[int, int]]:
    """Who has each torrent, as its trackers know it (the owner, 2026-11-12: "Replace a
    manual count, to an automatic person detector"): `{info_hash: (seeds, everyone)}`, for
    the torrents some tracker answered about (0 and 0 is an answer). `ways` are each a
    torrent's id and its own trackers; each is asked of those that are UDP trackers (a
    web address's are left out) and of `public`, every tracker once, about all the torrents
    it's asked about in one question (BEP 15's scrape), all the trackers at once. Over when
    each has answered or failed, `SCRAPE_AFTER_FIRST_S` after the first answered, or after
    `SCRAPE_S`, whichever is first.

    Seeds are the most any tracker said have all of it; everyone is that and the most any
    tracker said are still fetching it (each figure's largest, as libtorrent takes them:
    trackers know of many of the same people, so they aren't added up). It's who told a
    tracker lately that they have the film, not who answers: some have gone, and some
    can't be reached (`answered_by` says who did, once a film is open).

    Nothing is joined, nobody is connected to and nothing is written: each tracker is told
    the ids and sees this computer's address, as when a film is played."""
    asked: dict[tuple[str, int], list[str]] = {}
    for info_hash, trackers in ways[:SCRAPE_WAYS_MOST]:
        info_hash = info_hash.lower()
        if not INFO_HASH.fullmatch(info_hash):
            continue
        for url in [*trackers, *public]:
            where = _udp_tracker(url)
            if where is None:
                continue
            hashes = asked.setdefault(where, [])
            if info_hash not in hashes:
                hashes.append(info_hash)
    until = time.monotonic() + SCRAPE_S
    heard: list[dict[str, tuple[int, int]]] = []
    first: list[float] = []  # when the first tracker answered
    told = threading.Condition()

    def ask(where: tuple[str, int], hashes: list[str]) -> None:
        found = _ask_tracker(where, hashes, until)
        with told:
            heard.append(found)
            if found and not first:
                first.append(time.monotonic())
            told.notify_all()

    for where, hashes in asked.items():
        threading.Thread(target=ask, args=(where, hashes), daemon=True).start()
    with told:
        while len(heard) < len(asked):
            over = min(until, first[0] + SCRAPE_AFTER_FIRST_S) if first else until
            left = over - time.monotonic()
            if left <= 0:
                break
            told.wait(left)
        answers = list(heard)  # a tracker still out now is left: what it says doesn't count
    seeds: dict[str, int] = {}
    others: dict[str, int] = {}
    for found in answers:
        for info_hash, (has_all, fetching) in found.items():
            seeds[info_hash] = max(seeds.get(info_hash, 0), has_all)
            others[info_hash] = max(others.get(info_hash, 0), fetching)
    log.info(
        "torrents: asked %d trackers who has %d torrents: %d answered, about %d of them",
        len(asked), len({h for hashes in asked.values() for h in hashes}),
        sum(1 for found in answers if found), len(seeds),
    )  # fmt: skip
    return {one: (has_all, has_all + others[one]) for one, has_all in seeds.items()}


def _udp_tracker(url: str) -> tuple[str, int] | None:
    """A UDP tracker's host and port, or None for anything else (a web address's tracker,
    one with no port, one off this computer in replay mode)."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url.strip())
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    if parts.scheme.lower() != "udp" or not host or not port:
        return None
    if os.environ.get(REPLAY_ENV) and host.lower() not in _ON_THIS_COMPUTER:
        return None  # a test never reaches out
    return host.lower(), port


def _ask_tracker(
    where: tuple[str, int], hashes: list[str], until: float
) -> dict[str, tuple[int, int]]:
    """One UDP tracker's answer (BEP 15): `{info_hash: (seeders, leechers)}`, or nothing if
    it didn't answer, said it couldn't, or couldn't be found. First a question for a
    connection id, then the scrape: up to `SCRAPE_MOST` torrents in each."""
    host, port = where
    try:
        found = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    except (OSError, UnicodeError):
        return {}
    v4 = [one for one in found if one[0] == socket.AF_INET]
    v6 = [one for one in found if one[0] == socket.AF_INET6]
    if not (v4 or v6):
        return {}
    family, _, _, _, address = (v4 or v6)[0]
    said: dict[str, tuple[int, int]] = {}
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            connected = _exchange(sock, address, UDP_TRACKER_ID, 0, b"", until)
            if connected is None or len(connected) < 16:
                return {}
            (connection,) = struct.unpack_from(">Q", connected, 8)
            for start in range(0, len(hashes), SCRAPE_MOST):
                part = hashes[start : start + SCRAPE_MOST]
                asked = b"".join(bytes.fromhex(one) for one in part)
                answer = _exchange(sock, address, connection, 2, asked, until)
                if answer is None:
                    break
                for i, info_hash in enumerate(part):
                    if len(answer) < 8 + 12 * (i + 1):
                        break
                    seeders, _completed, leechers = struct.unpack_from(">iii", answer, 8 + 12 * i)
                    said[info_hash] = (max(seeders, 0), max(leechers, 0))
    except OSError:
        pass
    return said


def _exchange(
    sock: socket.socket, address: Any, connection: int, action: int, body: bytes, until: float
) -> bytes | None:
    """Ask a UDP tracker one thing (`action`: 0 for a connection id, 2 a scrape) and wait for
    its answer to it: the answer, or None if none came (sent again once after
    `SCRAPE_AGAIN_S`) or it was an error (action 3)."""
    tid = secrets.randbits(32)
    packet = struct.pack(">QII", connection, action, tid) + body
    for _ in range(2):
        if time.monotonic() >= until:
            return None
        sock.sendto(packet, address)
        wait_until = min(time.monotonic() + SCRAPE_AGAIN_S, until)
        while (left := wait_until - time.monotonic()) > 0:
            sock.settimeout(left)
            try:
                data = sock.recv(4096)
            except TimeoutError:
                break
            if len(data) < 8:
                continue
            said_action, said_tid = struct.unpack_from(">II", data)
            if said_tid != tid:
                continue  # an answer to something else
            return data if said_action == action else None
    return None


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
            # Leaving a torrent tells its trackers so; that isn't waited for long when
            # the engine stops (libtorrent's own 5 s held up the app's quitting).
            "stop_tracker_timeout": STOP_TRACKER_S,
            # What has arrived but isn't on the disk yet, at most (libtorrent's own was
            # 100 MB). All of it is written before a film is read again at the moment
            # it has all arrived, or when it's left: see `DISK_QUEUE_BYTES`.
            "max_queued_disk_bytes": DISK_QUEUE_BYTES,
            # What it must say back (`alert_mask`).
            "alert_mask": alert_mask(),
            # uTP always taken from sharers who reach us, and ours over TCP first
            # (`TCP_HEAD_START_S`). And sharers are tried sooner and faster than libtorrent's
            # own pace, since most listed never answer.
            "enable_incoming_utp": True,
            **transport_settings(head_start=False, retrying=False),
            "connection_speed": 150,
            "smooth_connects": False,
            "peer_connect_timeout": 7,
            "torrent_connect_boost": 100,
        }
    )


def transport_settings(*, head_start: bool, retrying: bool) -> dict[str, Any]:
    """libtorrent's settings for how sharers are reached: over TCP alone during a film's head
    start (`TCP_HEAD_START_S`), over uTP too otherwise; and for a while after a head start
    (`RETRY_FOR_S`), a sharer that couldn't be reached is tried again within seconds. During
    the head start a failed sharer waits libtorrent's minute: tried again within seconds,
    still over TCP, it would fail its three tries before uTP was ever let on."""
    return {
        "enable_outgoing_utp": not head_start,
        "min_reconnect_time": RETRY_AFTER_S if retrying and not head_start else 60,
    }


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


def alert_mask() -> int:
    """What libtorrent must say back: a piece that was asked for (`read_piece`), that a
    film's last parts are on the disk (`flush_cache`), what's here of a film that's being
    left (`save_resume_data`), and (2026-11-18) a film's state: that it has all arrived,
    since libtorrent then writes out all it holds of it unasked and says so as if asked
    (`_Joined.finish_heard`), and that a film's saved state wasn't taken."""
    import libtorrent as lt

    told = lt.alert.category_t
    return (
        int(told.storage_notification) | int(told.error_notification)
        | int(told.status_notification)
    )  # fmt: skip


def answered_by(handle: Any) -> set[str]:
    """Those connected to a torrent that have really answered, by address (2026-11-12, the
    owner: Count said 12/7, and Play found nobody). A tracker lists everyone who told it
    lately that they have the film or are fetching it, but many have gone, or sit behind a
    router that lets no one in (as this computer does: no port is opened on the router).
    Measured that day with open-licence films: of the addresses a tracker gives, a third or
    fewer answer. **Answered** is past the handshake, with the film or some of it: some
    answer for any torrent at all (a made-up one had 8 that day), with none of it to send."""
    import libtorrent as lt

    ask = getattr(handle, "get_peer_info", None)  # (a test's stand-in may have none)
    if ask is None:
        return set()
    found: set[str] = set()
    for peer in ask():
        flags = int(peer.flags)
        if flags & (int(lt.peer_info.connecting) | int(lt.peer_info.handshake)):
            continue
        if flags & int(lt.peer_info.seed) or peer.num_pieces > 0:
            found.add(str(peer.ip[0]))
    return found


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


def _pieces_for(length: int, piece_length: int, least: int, most: int | None = None) -> int:
    """How many pieces `length` bytes take: at least `least`, at most `most`."""
    wanted = max(least, -(-length // max(piece_length, 1)))
    return wanted if most is None else min(wanted, most)


def _v1(handle: Any) -> str:
    """The torrent's id as it was joined by (its v1 info-hash, the magnet link's `btih`).

    Not libtorrent's `info_hash()`: for a torrent made both ways at once (v1 and v2, as
    newer programs make them) that's the v2 id cut short, so nothing libtorrent said back
    about such a film was ever matched to it, and it never played: every piece the player
    asked for went unanswered (found 2026-10-18, with a test torrent made by libtorrent
    2.1, which makes them both ways)."""
    hashes = getattr(handle, "info_hashes", None)
    if hashes is not None:
        found = hashes()
        if found.has_v1():
            return str(found.v1)
    return str(handle.info_hash())


def checking(told: Any) -> bool:
    """Whether libtorrent is looking through what's already in the cache for a torrent
    (its own word for it, `state`): its `progress` then is how far it has looked, not how
    much is here (the owner saw "here: 5%" climb to 100% for a film that was all here)."""
    return str(getattr(told, "state", "")).rsplit(".", 1)[-1] in CHECKING


def _hold(handle: Any) -> None:
    """Pause a torrent that's being left: nothing more of it is fetched or sent, so what's
    here doesn't change while it's written out. Taken out of libtorrent's own queue first,
    which would otherwise start it again."""
    import libtorrent as lt

    if not hasattr(handle, "pause"):
        return  # (a test's stand-in)
    handle.unset_flags(lt.torrent_flags.auto_managed)
    handle.pause()


def _carry_on(handle: Any) -> None:
    """A torrent paused by `_hold` goes on as it did (a film played again)."""
    import libtorrent as lt

    if not hasattr(handle, "resume"):
        return  # (a test's stand-in)
    handle.set_flags(lt.torrent_flags.auto_managed)
    handle.resume()


def _pieces_here(handle: Any) -> list[bool]:
    """Which pieces of a torrent libtorrent has now (each checked; not each written yet)."""
    import libtorrent as lt

    return [bool(here) for here in handle.status(lt.torrent_handle.query_pieces).pieces]


def _trimmed(data: bytes, here: list[bool]) -> bytes:
    """What libtorrent said of a torrent (its resume data), saying a piece is here only if it
    was in `here`: what was here before libtorrent was asked to write it all out. A piece
    that came after may not be on the disk yet (2026-11-18). With the film paused first
    (`_hold`) none should come; it's fetched again if one did."""
    import libtorrent as lt

    params = lt.read_resume_data(data)
    params.have_pieces = [
        bool(have) and i < len(here) and here[i] for i, have in enumerate(params.have_pieces)
    ]
    return bytes(lt.write_resume_data_buf(params))


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
    # Writing out what libtorrent holds of the film (`ask_flush`, `all_written`): how many
    # of its write-outs it hasn't said are done yet, and a word each time it says so.
    _flushes_owed: int = field(default=0, repr=False)
    _miscounted: bool = field(default=False, repr=False)
    _flush_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _flush_said: threading.Event = field(default_factory=threading.Event, repr=False)
    # Another file of the same torrent being played while this one is kept (an episode
    # watched while another downloads): its number. See `turn_to`.
    beside: int | None = None
    _beside_offset: int = -1  # where that file starts in the torrent
    # Of each file being read (by where it starts): the piece the player was at when what
    # comes after it was last asked for sooner than the rest (`_ahead`).
    _banded: dict[int, int] = field(default_factory=dict, repr=False)
    _choosing: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # The torrent's details (libtorrent's `torrent_info`), once they've arrived: each piece's
    # size and fingerprint, for reading a film that's all here from its file (`_from_file`).
    _info: Any = field(default=None, repr=False)
    # Of each file played (by where it starts in the torrent): its number in the torrent;
    # those that have all arrived; and when libtorrent was last asked (`_arrived_all`).
    _file_at: dict[int, int] = field(default_factory=dict, repr=False)
    _arrived: set[int] = field(default_factory=set, repr=False)
    _looked: dict[int, float] = field(default_factory=dict, repr=False)
    # What libtorrent said is here, when the film was being left (`Player._resume_data`):
    # its resume data, ready to be saved; and that it has said (or couldn't).
    resume_data: bytes | None = field(default=None, repr=False)
    resumed: threading.Event = field(default_factory=threading.Event, repr=False)
    left: threading.Event = field(default_factory=threading.Event, repr=False)
    # Being left (`Player._let_go`): each leaving has its turn; a film played again while it's
    # being left, or taken over by the engine stopping, starts another, and the leaving whose
    # turn is over does nothing more (`Player._take_back`). `removing`: past taking back, it's
    # being taken out of libtorrent.
    leaves: int = 0
    removing: bool = False
    since: float = field(default_factory=time.monotonic)  # when it was joined (`TCP_HEAD_START_S`)

    def asked_now(self) -> None:
        self.last_asked = time.monotonic()

    # -- that what libtorrent holds of the film is on the disk (2026-11-18) --
    #
    # libtorrent says a piece is here once it has checked it, which may be before it's
    # written. Asked to write out all it holds of a torrent (`flush_cache`), it says when
    # that's done (`cache_flushed_alert`). But it also writes it all out by itself each time
    # the torrent has all arrived, and says so in the same words, unasked: a word that came
    # just after a film finished arriving could be taken for the answer to a question asked
    # a moment later, before what came since was written. So every write-out libtorrent owes
    # is counted, asked for or not: each torrent finishing says so first
    # (`torrent_finished_alert`), and the answers come in the order the write-outs began.
    # Measured with libtorrent 2.1 that day: through files wanted and unwanted, pieces read,
    # pausing, finishing twice and upload mode, every answer was one asked for or one
    # following a finish (4 asked, 2 finishes, 6 answers).

    def ask_flush(self) -> None:
        """Ask libtorrent to write out everything it holds of this torrent. `all_written`
        says when it has, with every write-out before it."""
        with self._flush_lock:
            self._flushes_owed += 1
        self.handle.flush_cache()

    def finish_heard(self) -> None:
        """libtorrent says the torrent has all arrived: it writes out what it holds of it
        now, unasked, and will say so as if asked (`flush_heard`)."""
        with self._flush_lock:
            self._flushes_owed += 1

    def flush_heard(self) -> None:
        """libtorrent says one of the torrent's write-outs is done (they end in the order
        they began)."""
        with self._flush_lock:
            self._flushes_owed -= 1
            if self._flushes_owed < 0 and not self._miscounted:
                # One more than was owed: libtorrent wrote out by itself for a reason not
                # known here, and this torrent's answers can't be told apart any more.
                self._miscounted = True
                log.warning("torrents: libtorrent said a film was written out unasked; "
                            "what's here of it won't be noted")  # fmt: skip
            self._flush_said.set()

    def all_written(self, until: float, *, still: Any = None) -> bool:
        """Wait until every write-out of this torrent asked for so far (`ask_flush`), and
        every one libtorrent began by itself, is done: then everything it held when the last
        was asked for is on the disk. False if that hasn't come by `until` (a
        `time.monotonic()`), or once `still()` says it isn't wanted any more; and never true
        once libtorrent's answers have been miscounted (`flush_heard`)."""
        while True:
            with self._flush_lock:
                if self._flushes_owed <= 0 and not self._miscounted:
                    return True
                self._flush_said.clear()
            left = until - time.monotonic()
            if left <= 0 or (still is not None and not still()):
                return False
            self._flush_said.wait(min(left, 0.2))

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
            with self._choosing:  # the player's first two questions may come together
                if self.path is None:
                    self._choose(info)
        return True

    def _choose(self, info: Any) -> None:
        """Choose the film in a torrent whose details have arrived, and ask for it: its
        start and end before anything else (2026-10-17), then as it's read (`_ahead`).

        The order of the asking matters. It was behind the owner's "when pressing next
        episode, it won't actually start playing until almost all the file is there"
        (98% of it, that day). libtorrent changes a torrent's file priorities a moment
        after it's asked to, and then gives every piece its file's priority again: the
        pieces the player had just asked for first (a deadline gives a piece the top
        priority) became ordinary pieces of the film, fetched rarest first, and a film's
        first pieces are among the commonest (everyone playing it has them). Measured
        with an open film: the top priority was taken away 20 times in 20 when the
        deadline was set at once, never when the file priorities were let take hold
        first. The player's first question then waited for that piece without asking
        again, for up to two minutes (`PIECE_WAIT_S`), while the rest of the episode
        arrived. A film opened afresh got by: its next questions came at once and asked
        again. Now nothing is asked for first until the priorities hold, and a question
        still waiting asks again every `NUDGE_S`."""
        files = info.files()
        listed = [(files.file_path(i), files.file_size(i)) for i in range(files.num_files())]
        chosen = choose_file(listed, self.file_index)
        # Nothing asked for the file played before is wanted first any more.
        clear = getattr(self.handle, "clear_piece_deadlines", None)  # (a test's stand-in has none)
        if clear is not None:
            clear()
        # Only the film is fetched.
        wanted = [FILM_PRIORITY if i == chosen else 0 for i in range(len(listed))]
        self.handle.prioritize_files(wanted)
        self.index = chosen
        self.name, self.size = Path(listed[chosen][0]).name, listed[chosen][1]
        self.file_offset = files.file_offset(chosen)
        self.piece_length = info.piece_length()
        self._info = info
        self._file_at[self.file_offset] = chosen
        self._settled(wanted)
        self._banded.clear()  # every piece has its file's priority again
        self._first(self.file_offset, self.size)
        self.top = Path(listed[chosen][0]).parts[0]
        self.path = self.folder / listed[chosen][0]

    def _settled(self, wanted: list[int]) -> None:
        """Wait (a moment, at most `PRIORITY_WAIT_S`) until the torrent's file priorities
        are `wanted`: only then is it safe to ask for pieces first (see `_choose`)."""
        told = getattr(self.handle, "get_file_priorities", None)  # (a test's stand-in has none)
        if told is None:
            return
        until = time.monotonic() + PRIORITY_WAIT_S
        while not self.closed:
            try:
                if [int(p) for p in told()] == wanted:
                    return
            except (RuntimeError, TypeError, ValueError):
                return
            if time.monotonic() > until:
                log.info("torrents: a film's file priorities took a while to take hold")
                return
            time.sleep(0.02)

    def _soon(self, pieces: list[int], after_ms: int = 0) -> None:
        """Ask for these pieces before anything else, the first of them first: each is
        given a deadline (which in libtorrent is the top priority too)."""
        ask = getattr(self.handle, "set_piece_deadline", None)  # (a test's stand-in may have none)
        if ask is None:
            return
        for n, piece in enumerate(pieces):
            ask(piece, after_ms + DEADLINE_MS * (n + 1))

    def _first(self, file_offset: int, size: int) -> None:
        """A film's start, and its end, are asked for before the rest of it: a player
        reads both before it shows anything (an MP4's index is often at its end, and an
        MKV keeps its cues there)."""
        if self.piece_length <= 0 or size <= 0:
            return
        first = file_offset // self.piece_length
        last = (file_offset + size - 1) // self.piece_length
        starting = _pieces_for(START_BYTES, self.piece_length, 2)
        ending = _pieces_for(END_BYTES, self.piece_length, 1)
        start = list(range(first, min(first + starting, last + 1)))
        tail = list(range(max(last + 1 - ending, start[-1] + 1), last + 1))
        self._soon(start)
        self._soon(tail, after_ms=DEADLINE_MS * len(start))

    def _ahead(self, file_offset: int, piece: int, last_piece: int) -> None:
        """The player is reading `piece` of the file that starts at `file_offset` (and
        ends in `last_piece`): what's just ahead of it is asked for before anything else,
        then the next part sooner than the rest, then the part after that (see
        `SOON_BYTES`). Those two are set again only once it has read another step on, or
        gone somewhere else in the film; never over the film's end, asked for apart."""
        length = self.piece_length
        ahead = _pieces_for(NEXT_BYTES, length, 2, most=NEXT_PIECES)
        window_end = min(piece + ahead - 1, last_piece)
        self._soon(list(range(piece, window_end + 1)))
        at = self._banded.get(file_offset)
        if at is not None and at <= piece < at + _pieces_for(STEP_BYTES, length, 1):
            return
        self._banded[file_offset] = piece
        order = getattr(self.handle, "prioritize_pieces", None)  # (a test's stand-in may have none)
        if order is None:
            return
        end_first = last_piece + 1 - _pieces_for(END_BYTES, length, 1)
        soon_end = min(window_end + _pieces_for(SOON_BYTES, length, 1), end_first - 1)
        later_end = min(soon_end + _pieces_for(LATER_BYTES, length, 1), end_first - 1)
        bands = [(p, SOON_PRIORITY) for p in range(window_end + 1, soon_end + 1)]
        bands += [(p, LATER_PRIORITY) for p in range(soon_end + 1, later_end + 1)]
        if bands:
            order(bands)

    def _again(self, piece: int, last_piece: int) -> None:
        """The player has waited `NUDGE_S` for `piece`: it, and what's just ahead of it,
        are asked for first again. Whatever changed the torrent's priorities meanwhile
        (another of its files fetched beside a kept one) put them back among the rest,
        and until 2026-10-17 a range waited up to `PIECE_WAIT_S` without asking again.
        (Clearing every deadline first, which makes libtorrent take back what it had
        asked the others for, was tried too: no quicker, measured.)"""
        ahead = _pieces_for(NEXT_BYTES, self.piece_length, 2, most=NEXT_PIECES)
        self._soon(list(range(piece, min(piece + ahead - 1, last_piece) + 1)))

    def _wants(self, file_offset: int) -> bool:
        """Whether the file that starts at `file_offset` is one this torrent fetches now
        (its film, or the one played beside a kept film). A player still reading one it
        has turned away from (the episode before) isn't given anything more of it."""
        if self.path is None:
            return False
        if file_offset == self.file_offset:
            return True
        return self.beside is not None and file_offset == self._beside_offset

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
        itself holds it, written or not.

        **Once all of the file has arrived, it's read from the file** (2026-11-17), a piece
        at a time, each piece only if it's the torrent's own (`_from_file`), and nothing
        more is asked for first. Asked of libtorrent, a film that was all here still waited
        on everything else libtorrent was doing with the disk: each piece was a job in its
        queue, behind what it had still to write and to check, and on this computer's
        spinning disk, with the songs downloading beside it, a jump waited 20 to 30 seconds
        and a film stood still for minutes (the owner: "all the film is there. but its
        stuck ... even now its been stuck on the same spot for over 2 minutes"). Measured
        that day with an open film all here and not in memory: two megabytes after a jump
        took 0.8 s through libtorrent, 0.07 s from its file."""
        self.asked_now()
        if self.closed or self.piece_length <= 0 or not 0 <= offset < size:
            return None
        place = file_offset + offset
        piece = place // self.piece_length
        # Never past the end of the file (the piece's rest is the next file's), nor of
        # the piece.
        length = min(length, size - offset, (piece + 1) * self.piece_length - place)
        last_piece = (file_offset + size - 1) // self.piece_length
        arrived = self.through_libtorrent and self._arrived_all(file_offset, size)
        if not arrived:
            # What's just ahead of the player is asked for before anything else (only of
            # a file this torrent fetches now: never more of the episode it has turned
            # from).
            if self._wants(file_offset):
                self._ahead(file_offset, piece, last_piece)
            until = time.monotonic() + PIECE_WAIT_S
            nudge = time.monotonic() + NUDGE_S
            while not self.handle.have_piece(piece):
                if self.closed or time.monotonic() > until:
                    return None
                if not self._wants(file_offset):
                    return None  # the torrent has turned to another file: this isn't coming
                if time.monotonic() > nudge:
                    self._again(piece, last_piece)
                    nudge = time.monotonic() + NUDGE_S
                time.sleep(0.1)
        if not self.through_libtorrent:
            try:
                with path.open("rb") as file:
                    file.seek(offset)
                    return file.read(length) or None
            except OSError:
                return None
        whole = self._piece(piece, (path, file_offset, size) if arrived else None)
        if whole is None:
            return None
        inside = place - piece * self.piece_length
        return whole[inside : inside + length] or None

    def _arrived_all(self, file_offset: int, size: int) -> bool:
        """Whether every byte of the torrent's file that starts at `file_offset` (and is
        `size` long) has arrived. Asked of libtorrent no more than once a second until it
        has; from then on it's known."""
        if file_offset in self._arrived:
            return True
        index = self._file_at.get(file_offset)
        looked = self._looked.get(file_offset)
        now = time.monotonic()
        if index is None or (looked is not None and now - looked < ARRIVED_LOOK_S):
            return False
        self._looked[file_offset] = now
        try:
            done = size > 0 and self.handle.file_progress()[index] >= size
        except (RuntimeError, AttributeError, IndexError, TypeError):
            return False  # (a test's stand-in may say nothing of it)
        if done:
            self._arrived.add(file_offset)
            log.info("torrents: all of a film has arrived; it's read from its file from now on")
        return done

    def _from_file(self, piece: int, path: Path, file_offset: int, size: int) -> bytes | None:
        """A piece of a file that has all arrived, read from the file, if it's the torrent's
        own: what was read must have the fingerprint the torrent gives that piece (its
        SHA-1, as libtorrent checks every piece that arrives), so nothing that isn't on the
        disk yet is ever given as film. libtorrent writes what has arrived a moment later
        (`DISK_QUEUE_BYTES` of it at most, on this computer sometimes many seconds later),
        and those last pieces are asked of it instead, as while the film arrives. So is a
        piece the file shares with the one beside it in the torrent (or with a torrent's
        padding), whose other part was never fetched or isn't in this file. None: ask
        libtorrent."""
        info = self._info
        if info is None:
            return None
        start = piece * self.piece_length
        try:
            length = int(info.piece_size(piece))
            wanted = bytes(info.hash_for_piece(piece))
        except (RuntimeError, AttributeError, IndexError, TypeError, ValueError):
            return None  # (a torrent made only the newer way, v2, has no SHA-1 for its pieces)
        if start < file_offset or start + length > file_offset + size:
            return None
        began = time.monotonic()
        try:
            with path.open("rb") as file:
                file.seek(start - file_offset)
                data = file.read(length)
        except OSError:
            return None
        took = time.monotonic() - began
        if took > SLOW_DISK_S:
            log.info("torrents: the disk took %.1f s to give a piece of a film", took)
        if len(data) != length or hashlib.sha1(data, usedforsecurity=False).digest() != wanted:
            return None
        return data

    def _piece(self, piece: int, arrived: tuple[Path, int, int] | None = None) -> bytes | None:
        """One piece libtorrent has, as it holds it; or, of a file that has all arrived
        (`arrived`: its path, where it starts in the torrent, and its size), as its file
        holds it, when that's the torrent's own (`_from_file`). The pieces just read are
        kept (a player reads a piece in several goes, and goes back a little), so each is
        asked for once."""
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
            found = self._from_file(piece, *arrived) if arrived is not None else None
            if found is not None:
                self.piece_arrived(piece, found)
                return found
            self.handle.read_piece(piece)  # answered by `piece_arrived`, from the alerts
        until = time.monotonic() + READ_WAIT_S
        while not waiting.wait(0.25):
            if self.closed or time.monotonic() > until:
                with self._pieces_lock:
                    self._asked.pop(piece, None)
                if not self.closed:
                    log.info("torrents: libtorrent didn't hand over a piece of a film in %d s",
                             READ_WAIT_S)  # fmt: skip
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
            # The one played beside it before isn't fetched any more; this one is, its
            # start and end first (once the priorities have taken hold: see `_choose`).
            if self.beside is not None:
                self.handle.file_priority(self.beside, 0)
            self.handle.file_priority(index, FILM_PRIORITY)
            self.beside, self._beside_offset = index, files.file_offset(index)
            both = (self.index, index)
            self._settled([FILM_PRIORITY if i in both else 0 for i in range(files.num_files())])
            self._banded.clear()  # every piece has its file's priority again
            self._first(files.file_offset(index), files.file_size(index))
        self._file_at[files.file_offset(index)] = index
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
        if checking(told):
            state = "checking"  # what's already here isn't known yet (2026-10-17)
        elif self.path is None:
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
            # Who has the torrent, as libtorrent knows it (`who_has`): a tracker's own
            # figures where one has answered, else those met so far.
            "swarm_seeds": who_has(told)[0],
            "swarm_peers": who_has(told)[1],
            # Of those connected, the ones that have really answered (`answered_by`): with
            # the film or some of it (2026-11-12).
            "answered": len(answered_by(self.handle)),
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
        # What `_tell_transport` last told libtorrent: in a head start, retrying soon.
        self._transport_told: tuple[bool, bool] | None = None
        self._transport_lock = threading.Lock()
        self._parting: dict[str, _Joined] = {}  # being left just now (what's here is saved)
        self._hearing: threading.Thread | None = None  # what libtorrent says back, heard
        self._lock = threading.Lock()
        self._stopping = threading.Event()

    # -- what the app asks for --

    def play(self, info_hash: str, file_index: int | None, trackers: list[str]) -> dict[str, Any]:
        """Join a torrent (or carry on with one already joined) and give the address
        its film plays at. Returns at once: the address waits for the film."""
        info_hash = info_hash.lower()
        link = magnet(info_hash, trackers)
        with self._lock:
            # Closed a moment ago, and still being left: it carries on, as it is.
            parting = None if self._take_back(info_hash) else self._parting.get(info_hash)
        if parting is not None:
            # Past taking back: it's being taken out of libtorrent, which is a moment.
            # Joined again after, from what was noted of it.
            parting.left.wait(LEAVE_WAIT_S)
        with self._lock:
            self._start()
            joined = self._joined.get(info_hash)
            if joined is None:
                # Its head start over TCP begins before libtorrent reaches anyone.
                self._tell_transport(joining=True)
                handle = self._session.add_torrent(self._params(info_hash, link))
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

    def _tell_transport(self, *, joining: bool = False) -> None:
        """Tell libtorrent how to reach sharers (`transport_settings`): over TCP alone while a
        film is being joined (`joining`) or is in its first seconds (`TCP_HEAD_START_S`), so
        every sharer then listed is tried over TCP; over uTP too otherwise, for those TCP
        couldn't reach (and hole-punched to). For a while after a film's head start, a sharer
        that couldn't be reached is tried again within seconds (`RETRY_FOR_S`). Told only when
        the answer changes; looked at again every couple of seconds (`_close_idle`)."""
        apply = getattr(self._session, "apply_settings", None)  # a test's stand-in has none
        if apply is None:
            return
        now = time.monotonic()
        ages = [now - one.since for one in list(self._joined.values()) if not one.closed]
        head_start = joining or any(age < TCP_HEAD_START_S for age in ages)
        retrying = not head_start and any(age < TCP_HEAD_START_S + RETRY_FOR_S for age in ages)
        with self._transport_lock:
            if (head_start, retrying) == self._transport_told:
                return
            self._transport_told = (head_start, retrying)
            apply(transport_settings(head_start=head_start, retrying=retrying))

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
            turn = self._parted(joined, delete=False)
            del self._joined[info_hash.lower()]
        self._let_go(joined, turn, delete=False)

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
            if joined is None:
                return
            delete = joined.kept_path is not None
            turn = self._parted(joined, delete=delete)
            del self._joined[info_hash.lower()]
        self._let_go(joined, turn, delete=delete)

    def stop(self) -> None:
        """Leave every torrent and stop listening. What arrived stays its day, and what
        libtorrent knows of it is saved beside it, so it's all there at once next time.

        Quickly (2026-10-17: the owner's app took about twenty seconds to quit, and was
        once reopened while the engine before was still stopping): libtorrent is given two
        seconds at most to say what's here, trackers a second to hear this computer has
        gone (libtorrent's own was five), and libtorrent is ended here, where the time
        it takes is known, not later on a thread of its own.

        **What's here is asked before libtorrent ends, and written only once it has**
        (2026-11-18). libtorrent writes out everything it holds before it ends: measured that
        day, ending it took as long as a write-out asked for (6.7 s with 256 MB waiting to be
        written, 50 s on a disk kept out of the Mac's file cache), and every piece it had said
        was here was in the file after. So nothing is waited for beforehand: until then it
        waited 3 s for the write-out, and on a slow disk left the film with nothing noted
        (with libtorrent's ending still to wait for). Films being left a moment ago are taken
        over from their own threads, and noted with the rest."""
        began = time.monotonic()
        self._stopping.set()
        with self._lock:
            leaving = list(self._joined.values())
            going = []
            for parting in list(self._parting.values()):
                if parting.removing:
                    going.append(parting)  # only being taken out of libtorrent: a moment
                else:
                    parting.leaves += 1  # its own thread lets go of it: it's noted here
                    leaving.append(parting)
            for joined in leaving:
                joined.closed = True  # and nothing still waiting for a piece waits on
                self._parting[joined.info_hash] = joined
            self._joined = {}
            server, self._server = self._server, None
            session = self._session
        for parting in going:
            parting.left.wait(RESUME_WAIT_S)
        # Everyone else is let go of at once: saying goodbye to them (some ways of being
        # connected take a second or two to close) goes on while what's here is asked.
        if hasattr(session, "pause"):  # (a test's stand-in has none)
            session.pause()
        if server is not None:
            server.shutdown()  # no film player is answered any more
            server.server_close()
        noted: dict[str, bytes] = {}
        try:
            noted = self._resume_data(leaving)  # kept in memory until libtorrent has ended
        finally:
            asked = time.monotonic()
            for joined in leaving:
                if session is not None and joined.handle.is_valid():
                    session.remove_torrent(joined.handle)
            self._session = None
            self._sending = None
        hearing, self._hearing = self._hearing, None
        if hearing is not None and hearing is not threading.current_thread():
            hearing.join(2)  # it lets go of the session within a fifth of a second
        heard, ended = time.monotonic(), session is not None
        # The last of the session: libtorrent ends now, here (writing out all it holds,
        # saying goodbye to the others, and closing its files), not later on a thread of its
        # own. Only if nothing else holds on to it does it end on this line, and only then is
        # what it said written: held elsewhere, it would end later, who knows when.
        alone = ended and sys.getrefcount(session) <= 2  # (this name, and the call's own)
        session = None
        gone, saved = time.monotonic(), 0
        try:
            if alone:
                saved = sum(self._write_note(info_hash, data) for info_hash, data in noted.items())
            elif noted:
                log.info("torrents: %d film(s) left without their state saved: libtorrent "
                         "was still in use", len(noted))  # fmt: skip
        finally:
            for joined in leaving:
                self._touch(joined)
                self._parting.pop(joined.info_hash, None)
                joined.left.set()
        if ended:
            log.info(
                "torrents: stopped in %.1f s (%d of %d film(s) noted, in %.1f s; libtorrent "
                "ended in %.1f s)", time.monotonic() - began, saved, len(leaving),
                asked - began, gone - heard,
            )  # fmt: skip

    def sweep(self) -> int:
        """Delete what has been in the cache unused for over a day. Films open now stay,
        with what libtorrent keeps beside them."""
        skip = []
        for joined in list(self._joined.values()):
            skip += [f".{joined.info_hash}{RESUME}", f".{joined.info_hash}{PARTS}"]
            if joined.top:
                skip.append(joined.top)
        return sweep(self.folder.parent, skip=skip)

    # -- the workings --

    def _parted(self, joined: _Joined, *, delete: bool) -> int:
        """(Under `_lock`.) A film the app has let go of is being left from now: it's
        closed, it's among those being left until it has gone, and its leaving has a turn
        of its own (`_Joined.leaves`), which it returns. One whose files go with it is past
        taking back from the start."""
        joined.closed = True  # nothing still waiting for a piece waits on
        joined.leaves += 1
        joined.removing = delete
        self._parting[joined.info_hash] = joined
        return joined.leaves

    def _let_go(self, joined: _Joined, turn: int, *, delete: bool) -> None:
        """Leave a film the app has closed. What's here is noted first, which waits on
        libtorrent writing it all out (a second or two while a film arrives quickly; on a
        busy disk, much longer): that's done on a thread of its own, so the app is answered
        at once. Played again meanwhile, the film carries on (`_take_back`)."""
        if self._hearing is None or delete:
            self._leave(joined, turn, delete=delete)  # nothing to wait for
            return
        threading.Thread(
            target=self._leave, args=(joined, turn), kwargs={"delete": False}, daemon=True,
            name="film-leaving",
        ).start()  # fmt: skip

    def _leave(self, joined: _Joined, turn: int, *, delete: bool) -> None:
        try:
            if delete:
                session = self._session
                if session is not None and joined.handle.is_valid():
                    import libtorrent as lt

                    # libtorrent deletes its own files; what it knew of them goes too.
                    session.remove_torrent(joined.handle, lt.session.delete_files)
                    self._forget_resume(joined.info_hash)
                return
            self._note_leaving(joined, turn)
            with self._lock:
                if joined.leaves != turn:
                    return  # played again meanwhile, or taken over by the engine stopping
                joined.removing = True  # past taking back
                session = self._session
            if session is not None and joined.handle.is_valid():
                session.remove_torrent(joined.handle)
            session = None  # (the engine's stopping ends libtorrent only once nobody holds it)
            self._touch(joined)
        except RuntimeError as exc:  # libtorrent's own trouble, on a thread of its own
            log.warning("torrents: a film couldn't be left cleanly: %s", exc)
        finally:
            with self._lock:
                mine = joined.leaves == turn
                if mine and self._parting.get(joined.info_hash) is joined:
                    self._parting.pop(joined.info_hash, None)
            if mine:
                joined.left.set()

    def _take_back(self, info_hash: str) -> bool:
        """(Under `_lock`.) A film played again while it's still being left carries on as
        it was (2026-11-18): its leaving is over before it was done, nothing of it is noted
        or taken out of libtorrent, and it's started again if it was paused for that. So a
        film closed by mistake and opened again, while a slow disk is still writing it out,
        is all there with nothing checked again (though what's read of it first waits for
        that writing, as libtorrent's reads do). Not once it's past taking back
        (`removing`: then it's joined again a moment later, from what was noted), nor
        while the engine stops."""
        parting = self._parting.get(info_hash)
        if parting is None or parting.removing or self._stopping.is_set():
            return False
        try:
            _carry_on(parting.handle)
        except RuntimeError as exc:  # libtorrent's own trouble: it's left, and joined again
            log.warning("torrents: a film being left couldn't carry on: %s", exc)
            return False
        parting.leaves += 1  # its leaving's turn is over
        parting.closed = False
        parting.playing = True
        # Everyone it was connected to was let go of when it was paused: they're reached
        # again as a film just joined reaches them (`TCP_HEAD_START_S`, `RETRY_FOR_S`).
        parting.since = time.monotonic()
        self._tell_transport(joining=True)
        # (Among those joined before it's out of those being left: what libtorrent says of
        # it is always heard, `_hear`.)
        self._joined[info_hash] = parting
        del self._parting[info_hash]
        log.info("torrents: a film played again while it was being left carries on")
        return True

    def _touch(self, joined: _Joined) -> None:
        """A film's day in the cache is counted from now, when it was last played: its
        files, and libtorrent's own beside them, together."""
        from musicorg import fileops

        for name in (joined.top, f".{joined.info_hash}{PARTS}", f".{joined.info_hash}{RESUME}"):
            if name:
                fileops.touch_cached(self.folder / name, cache=self.folder.parent)

    # -- what libtorrent knows of a film, kept for the next time (2026-10-17) --
    #
    # Without it, a film joined again is looked through from its start by libtorrent,
    # every piece checked against the torrent, before anything of it is known to be here:
    # the owner saw "here: 5%" climb through 7, 10, 20 to 100% for a film that had all
    # arrived the day before. With it, libtorrent takes its own word for what's here
    # (and has the torrent's details at once, with no wait for them from the others).
    # Only what's on the disk is ever noted: the film's last parts are written first.

    def _resume_file(self, info_hash: str) -> Path:
        return self.folder / f".{info_hash}{RESUME}"

    def _forget_resume(self, info_hash: str) -> None:
        from musicorg import fileops

        fileops.forget_cached(self._resume_file(info_hash), cache=self.folder.parent)

    def _params(self, info_hash: str, link: str) -> Any:
        """How to join a torrent to play it: as it was left, if that's known and its
        files are still in the cache; else afresh, from its magnet link. Either way into
        the films' cache folder."""
        import libtorrent as lt

        fresh = lt.parse_magnet_uri(link)
        params = self._resumed(info_hash)
        if params is None:
            params = fresh
        else:
            known = list(params.trackers)
            params.trackers = known + [t for t in fresh.trackers if t not in known]
        params.save_path = str(self.folder)
        # Never in upload mode, never paused. And not libtorrent's own "in
        # order" either: with it, a piece asked for first is passed over whenever a rarer
        # one is waiting (measured with an open film: its last 2 MB took 22 s to come, and
        # the player waited 10 s half a megabyte in). `_ahead` leans towards the order
        # instead.
        params.flags &= ~(
            lt.torrent_flags.upload_mode | lt.torrent_flags.paused
            | lt.torrent_flags.sequential_download
        )  # fmt: skip
        # Left to libtorrent's own queue, as a film joined afresh is, though it was noted as
        # paused and out of it (`_hold`, 2026-11-18).
        params.flags |= lt.torrent_flags.auto_managed
        return params

    def _resumed(self, info_hash: str) -> Any:
        """The film as libtorrent left it, or None. It's used only when it's this
        torrent's, has its details, and its files are still here: the film's own (or
        its folder), and libtorrent's file of shared pieces whenever some files weren't
        fetched (without it, pieces said to be here couldn't be read)."""
        import libtorrent as lt

        saved = self._resume_file(info_hash)
        try:
            data = saved.read_bytes()
        except OSError:
            return None
        try:
            params = lt.read_resume_data(data)
            info = params.ti
            ok = info is not None and str(info.info_hashes().v1) == info_hash
            if ok:
                files = info.files()
                top = Path(files.file_path(0)).parts[0] if files.num_files() else ""
                ok = bool(top) and (self.folder / top).exists()
                skipped = any(int(p) == 0 for p in params.file_priorities)
                if ok and skipped and not (self.folder / f".{info_hash}{PARTS}").is_file():
                    ok = False
        except (RuntimeError, ValueError, TypeError, AttributeError) as exc:
            log.info("torrents: a film's saved state couldn't be read (%s)", type(exc).__name__)
            ok = False
        if not ok:
            self._forget_resume(info_hash)
            return None
        return params

    def _note_leaving(self, joined: _Joined, turn: int) -> None:
        """Note what's here of a film the app has closed, beside it in the cache, once
        libtorrent has written it all out (2026-11-18). The film is paused first (nothing
        more of it is fetched or sent), what's here is looked at, and libtorrent is asked to
        write out all it holds of it; that's waited for as long as it takes, up to
        `LEAVE_FLUSH_S`, on this thread of its own. Only what was here before the write-out
        was asked for is noted (`_trimmed`). Played again meanwhile, or taken over by the
        engine stopping, it's left to that (`turn`)."""
        if self._hearing is None:
            return  # (a test's stand-in session says nothing back)
        if not joined.through_libtorrent or not joined.handle.is_valid():
            return
        if joined.handle.torrent_file() is None:
            return  # its details never came: there's nothing to note
        began = time.monotonic()

        def still() -> bool:
            return joined.leaves == turn

        try:
            with self._lock:  # (a film played again is started after it's paused, not before)
                if not still():
                    return
                _hold(joined.handle)
            here = _pieces_here(joined.handle)
            joined.ask_flush()
            if not joined.all_written(began + LEAVE_FLUSH_S, still=still):
                if still():
                    log.info("torrents: 1 film(s) left without their state saved: the disk "
                             "hadn't written it out in %d s", LEAVE_FLUSH_S)  # fmt: skip
                return
            written = time.monotonic()
            data = self._resume_data([joined]).get(joined.info_hash)
            if data is None or not still():
                return
            if self._write_note(joined.info_hash, _trimmed(data, here)):
                log.info("torrents: a film's state was saved as it was left (written out in "
                         "%.1f s)", written - began)  # fmt: skip
        except RuntimeError as exc:  # libtorrent's own trouble: the film is left anyway
            log.warning("torrents: what's here of a film couldn't be noted: %s", exc)

    def _resume_data(self, films: list[_Joined]) -> dict[str, bytes]:
        """What libtorrent says is here of each film (its resume data), asked of all of them
        together and waited for `RESUME_WAIT_S` at most; by the torrent's id. It's written
        only once all of it is on the disk (`_note_leaving`, `stop`)."""
        if self._hearing is None:
            return {}  # (a test's stand-in session says nothing back)
        import libtorrent as lt

        asked = []
        for joined in films:
            try:
                if not joined.through_libtorrent or not joined.handle.is_valid():
                    continue
                if joined.handle.torrent_file() is None:
                    continue  # its details never came: there's nothing to note
                joined.resumed.clear()
                joined.resume_data = None
                joined.handle.save_resume_data(lt.torrent_handle.save_info_dict)
                asked.append(joined)
            except RuntimeError as exc:  # libtorrent's own trouble: the others are asked
                log.warning("torrents: what's here of a film couldn't be asked: %s", exc)
        until = time.monotonic() + RESUME_WAIT_S
        said = {}
        for joined in asked:
            if joined.resumed.wait(max(0.0, until - time.monotonic())) and joined.resume_data:
                said[joined.info_hash] = joined.resume_data
        return said

    def _write_note(self, info_hash: str, data: bytes) -> bool:
        """Save what libtorrent said is here of a film, beside it in the cache."""
        from musicorg import fileops
        from musicorg.errors import MusicOrgError

        try:
            fileops.write_cached(self._resume_file(info_hash), data, cache=self.folder.parent)
        except (MusicOrgError, ValueError) as exc:
            log.warning("torrents: a film's state couldn't be saved: %s", exc)
            return False
        return True

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
        # (It looks for being shut down every tenth of a second, not every half: the
        # engine's stopping waits for that.)
        threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
        ).start()
        threading.Thread(target=self._close_idle, daemon=True).start()
        if hasattr(self._session, "pop_alerts"):  # (a test's stand-in has none)
            self._hearing = threading.Thread(target=self._hear, args=(self._session,), daemon=True)
            self._hearing.start()

    def _hear(self, session: Any) -> None:
        """What libtorrent says back, for as long as the engine plays films: a piece
        that was asked for, that a film's last parts are on the disk, that a film has all
        arrived (libtorrent writes it out then, unasked), what's here of a film that's
        being left, and that what was noted of one wasn't taken. Only of the torrent as
        it's joined now: one of the same film left a moment before may still be speaking.
        (Until the session is let go, so that what's here is heard even while the engine
        is stopping.)"""
        import libtorrent as lt

        heard = ("read_piece_alert", "cache_flushed_alert", "save_resume_data_alert",
                 "save_resume_data_failed_alert", "torrent_finished_alert",
                 "fastresume_rejected_alert")  # fmt: skip
        while self._session is session:
            try:
                session.wait_for_alert(200)  # (and it looks for being let go this often)
                alerts = session.pop_alerts()
            except RuntimeError:
                return
            for alert in alerts:
                kind = type(alert).__name__
                if kind not in heard:
                    continue
                try:
                    info_hash = _v1(alert.handle)
                except RuntimeError:
                    continue  # its torrent has been left meanwhile
                joined = self._joined.get(info_hash) or self._parting.get(info_hash)
                if joined is None or not alert.handle == joined.handle:
                    continue  # (or of the same film joined before, still being let go of)
                if kind == "cache_flushed_alert":
                    joined.flush_heard()
                    continue
                if kind == "torrent_finished_alert":
                    joined.finish_heard()
                    continue
                if kind == "fastresume_rejected_alert":
                    log.info("torrents: what was noted of a film wasn't taken, so what's here "
                             "is checked: %s", alert.message())  # fmt: skip
                    continue
                if kind.startswith("save_resume_data"):
                    said = None
                    if kind == "save_resume_data_alert":
                        try:
                            said = lt.write_resume_data_buf(alert.params)
                        except (RuntimeError, AttributeError):
                            pass
                    joined.resume_data = bytes(said) if said else None
                    joined.resumed.set()
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
            joined.ask_flush()
            if not joined.all_written(time.monotonic() + FLUSH_WAIT_S):
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
            self._tell_transport()  # a film's head start over TCP ends, and its quick retries
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
