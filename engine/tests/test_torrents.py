"""A film played from a torrent: the parts with no torrent in them, and the address a
player opens, served from a stand-in film. No torrent is ever joined in tests."""

from __future__ import annotations

import http.client
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from musicorg import torrents
from musicorg.errors import ReplayMissError

HASH = "ab" * 20
F = torrents.FILM_PRIORITY  # the priority a film is fetched at, under what's asked for first


def test_no_torrent_is_joined_in_tests(tmp_path: Path) -> None:
    with pytest.raises(ReplayMissError):
        torrents.Player(tmp_path).play(HASH, None, [])


def test_a_magnet_link_is_built_from_the_id_and_trackers() -> None:
    assert torrents.magnet(HASH) == f"magnet:?xt=urn:btih:{HASH}"
    assert torrents.magnet(HASH, ["udp://t.example:80/a"]) == (
        f"magnet:?xt=urn:btih:{HASH}&tr=udp%3A%2F%2Ft.example%3A80%2Fa"
    )
    for wrong in ("", "xyz", HASH.upper(), HASH + "0"):
        with pytest.raises(torrents.TorrentError):
            torrents.magnet(wrong)


def test_the_film_is_the_file_named_or_else_the_biggest_video() -> None:
    files = [("Film/sample.mkv", 10), ("Film/film.mkv", 900), ("Film/extras.zip", 5000),
             ("Film/poster.jpg", 1)]  # fmt: skip
    assert torrents.choose_file(files, 0) == 0  # the add-on said which
    assert torrents.choose_file(files, None) == 1
    assert torrents.choose_file(files, 99) == 1  # a number that isn't a file: as if none
    assert torrents.choose_file([("a.bin", 1), ("b.bin", 2)], None) == 1  # no video: the biggest
    with pytest.raises(torrents.TorrentError):
        torrents.choose_file([], None)


@pytest.mark.parametrize(
    ("header", "wanted"),
    [
        (None, (0, 999)),
        ("bytes=0-99", (0, 99)),
        ("bytes=500-", (500, 999)),
        ("bytes=990-5000", (990, 999)),  # past the end: up to the end
        ("bytes=-100", (900, 999)),  # the last hundred
        ("bytes=-5000", (0, 999)),
        ("bytes=1000-", None),  # starts past the end
        ("bytes=50-10", None),
        ("bytes=-0", None),
        ("bytes=-", None),
        ("lines=0-5", None),
    ],
)
def test_the_range_a_player_asks_for(header: str | None, wanted: tuple[int, int] | None) -> None:
    assert torrents.parse_range(header, 1000) == wanted


def test_what_kind_of_file_the_player_is_told() -> None:
    assert torrents.content_type("Film.MKV") == "video/x-matroska"
    assert torrents.content_type("film.mp4") == "video/mp4"
    assert torrents.content_type("film") == "application/octet-stream"


class StandIn:
    """A film whose bytes are all here, except from `missing` on."""

    name = "film.mp4"

    def __init__(self, data: bytes, missing: int | None = None) -> None:
        self.data, self.size, self.missing = data, len(data), missing
        self.asked: list[tuple[int, int]] = []

    def read(self, offset: int, length: int) -> bytes | None:
        self.asked.append((offset, length))
        if self.missing is not None and offset >= self.missing:
            return None
        end = offset + length if self.missing is None else min(offset + length, self.missing)
        return self.data[offset:end]


def ask(film: StandIn, headers: dict[str, str], method: str = "GET") -> tuple[int, dict, bytes]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            torrents.serve(self, film)

        def do_HEAD(self) -> None:
            torrents.serve(self, film, head=True)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        connection.request(method, "/film", headers=headers)
        response = connection.getresponse()
        try:
            body = response.read()
        except http.client.IncompleteRead as cut:
            body = cut.partial
        return response.status, dict(response.getheaders()), body
    finally:
        server.shutdown()
        server.server_close()


def test_the_whole_film_and_a_range_of_it() -> None:
    data = bytes(range(256)) * 4000  # about a megabyte: several chunks
    status, headers, body = ask(StandIn(data), {})
    assert (status, body) == (200, data)
    assert headers["Accept-Ranges"] == "bytes" and headers["Content-Type"] == "video/mp4"

    status, headers, body = ask(StandIn(data), {"Range": "bytes=1000-300999"})
    assert (status, body) == (206, data[1000:301000])
    assert headers["Content-Range"] == f"bytes 1000-300999/{len(data)}"
    assert headers["Content-Length"] == "300000"

    status, headers, body = ask(StandIn(data), {"Range": "bytes=0-9"}, method="HEAD")
    assert (status, body, headers["Content-Length"]) == (206, b"", "10")


def test_a_range_that_cant_be_met_is_refused() -> None:
    status, headers, _ = ask(StandIn(b"x" * 100), {"Range": "bytes=500-"})
    assert status == 416 and headers["Content-Range"] == "bytes */100"


def test_a_film_that_stops_arriving_ends_the_answer_where_it_got_to() -> None:
    film = StandIn(b"y" * 1000, missing=400)
    status, _, body = ask(film, {"Range": "bytes=0-999"})
    assert status == 206 and body == b"y" * 400  # what came; the player asks again


def test_each_answer_says_its_connection_closes_and_closes_it() -> None:
    """The film player's FFmpeg asks its next question on the same connection when it has
    read an answer to the end and wasn't told the connection closes. It was never told,
    and the connection was always closed: after reading a film's last bytes, the next
    question met a closed connection and the film stopped for good (2026-10-18: the
    owner's films that froze when the sound track was changed)."""
    data = bytes(range(256)) * 40

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # as the engine's own server

        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            torrents.serve(self, StandIn(data))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with socket.create_connection(server.server_address, timeout=10) as connection:
            # A player that asks for the film's last bytes, as FFmpeg does, then asks again.
            question = "GET /film HTTP/1.1\r\nHost: x\r\nRange: bytes={}\r\n\r\n"
            connection.sendall(question.format(f"{len(data) - 10}-").encode())
            heard = b""
            while chunk := connection.recv(65536):
                heard += chunk
            head, _, body = heard.partition(b"\r\n\r\n")
            assert b"\r\nConnection: close" in head
            assert body == data[-10:]  # and then the connection was closed: recv gave b""
    finally:
        server.shutdown()
        server.server_close()


class HandleOfBoth:
    """A torrent made both ways (v1 and v2), joined by its v1 id: libtorrent's own
    `info_hash()` gives the v2 id cut short."""

    class Hashes:
        v1 = "6d6a54f111139ede2060fd7aee9d2376fd2ccc24"

        def has_v1(self) -> bool:
            return True

    def info_hashes(self) -> HandleOfBoth.Hashes:
        return self.Hashes()

    def info_hash(self) -> str:
        return "1c91661348d82c09fd56b36c471bb5d50676e8d0"


def test_a_torrent_is_known_by_the_v1_id_it_was_joined_by() -> None:
    """What libtorrent says back about a torrent made both ways is matched to the film
    by its v1 id, as it was joined: by `info_hash()` it never was, and such a film never
    played (found 2026-10-18)."""
    assert torrents._v1(HandleOfBoth()) == HandleOfBoth.Hashes.v1


# ---- a joined torrent, with a stand-in for libtorrent (2026-10-14) -------------------------
#
# Two faults the owner met with a series played from one torrent: the picture broke up
# while an episode was arriving, and "play next" played the same episode again.

PIECE = 128
FILES = [("Show/E1.mkv", 300), ("Show/E2.mkv", 500), ("Show/notes.txt", 40)]
WHOLE = bytes(i % 251 for i in range(sum(size for _, size in FILES)))


class StandInFiles:
    def num_files(self) -> int:
        return len(FILES)

    def file_path(self, index: int) -> str:
        return FILES[index][0]

    def file_size(self, index: int) -> int:
        return FILES[index][1]

    def file_offset(self, index: int) -> int:
        return sum(size for _, size in FILES[:index])


class StandInInfo:
    def files(self) -> StandInFiles:
        return StandInFiles()

    def piece_length(self) -> int:
        return PIECE


class StandInHandle:
    """libtorrent's handle for one torrent: every piece is here, and none is on the disk."""

    def __init__(self) -> None:
        self.joined: torrents._Joined | None = None
        self.priorities: list[int] = []
        self.raised: list[tuple[int, int]] = []
        self.asked: list[int] = []
        self.broken: set[int] = set()
        # What it was told, in order: ("files", priorities), ("deadline", piece, ms,
        # whether the file priorities had taken hold by then), ("clear",).
        self.told: list[tuple] = []
        self.taking_hold = 0  # how many more looks before new file priorities hold
        self.missing: set[int] = set()  # pieces not here
        self.held = True  # the file priorities asked for last have taken hold

    def is_valid(self) -> bool:
        return True

    def torrent_file(self) -> StandInInfo:
        return StandInInfo()

    def prioritize_files(self, priorities: list[int]) -> None:
        self.priorities = priorities
        self.held = False
        self.told.append(("files", list(priorities)))

    def get_file_priorities(self) -> list[int]:
        # As libtorrent does, the new priorities hold a moment after they're asked for.
        if self.taking_hold > 0:
            self.taking_hold -= 1
            return [4] * len(FILES)
        self.held = True
        return list(self.priorities)

    def file_priority(self, index: int, priority: int) -> None:
        self.raised.append((index, priority))
        if self.priorities:
            self.priorities[index] = priority

    def clear_piece_deadlines(self) -> None:
        self.told.append(("clear",))

    def set_piece_deadline(self, piece: int, deadline: int) -> None:
        self.told.append(("deadline", piece, deadline, self.held))

    def have_piece(self, piece: int) -> bool:
        return piece not in self.missing

    def read_piece(self, piece: int) -> None:
        self.asked.append(piece)
        data = None if piece in self.broken else WHOLE[piece * PIECE : (piece + 1) * PIECE]
        assert self.joined is not None
        threading.Thread(target=self.joined.piece_arrived, args=(piece, data)).start()


def joined_for(tmp_path: Path, index: int | None) -> tuple[torrents._Joined, StandInHandle]:
    handle = StandInHandle()
    joined = torrents._Joined(handle, HASH, index, tmp_path)
    joined.through_libtorrent = True
    handle.joined = joined
    assert joined.ready(1)
    return joined, handle


def all_of(film: torrents.Film) -> bytes:
    got = b""
    while len(got) < film.size:
        part = film.read(len(got), 1000)
        assert part
        got += part
    return got


def test_a_films_bytes_are_asked_of_libtorrent_not_read_from_its_file(tmp_path: Path) -> None:
    joined, handle = joined_for(tmp_path, 1)
    # Nothing is on the disk at all: every byte comes as libtorrent holds it.
    assert not (tmp_path / "Show").exists()
    assert (joined.name, joined.size, handle.priorities) == ("E2.mkv", 500, [0, F, 0])
    # A read never runs past the end of the piece it starts in.
    first = joined.read(0, 1000)
    assert first == WHOLE[300:384]  # the file starts 300 bytes in: 84 bytes to the piece's end
    assert all_of(joined) == WHOLE[300:800]
    # Each piece was asked for once, however many reads it took.
    assert sorted(handle.asked) == [2, 3, 4, 5, 6] and len(handle.asked) == 5


def test_a_piece_that_cant_be_handed_over_ends_the_read(tmp_path: Path) -> None:
    joined, handle = joined_for(tmp_path, 0)
    handle.broken = {1}
    assert joined.read(0, 1000) == WHOLE[0:128]
    assert joined.read(128, 1000) is None  # the player asks again; nothing wrong is given
    handle.broken = set()
    assert joined.read(128, 1000) == WHOLE[128:256]


def test_only_the_pieces_just_read_are_kept_in_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(torrents, "PIECES_KEPT", 3 * PIECE)
    joined, handle = joined_for(tmp_path, 1)
    all_of(joined)
    assert len(joined._pieces) == 3 and list(joined._pieces) == [4, 5, 6]


def test_the_next_episode_of_a_season_in_one_torrent_is_the_next_episode(tmp_path: Path) -> None:
    joined, handle = joined_for(tmp_path, 0)
    assert joined.name == "E1.mkv"
    joined.turn_to(1)
    assert joined.ready(1)
    # The torrent is the second episode's now: it alone is fetched.
    assert (joined.name, joined.size, handle.priorities) == ("E2.mkv", 500, [0, F, 0])
    assert all_of(joined) == WHOLE[300:800]
    # Asked for the one it's on already, or for none in particular: nothing changes.
    joined.turn_to(1)
    joined.turn_to(None)
    assert joined.path is not None and joined.name == "E2.mkv"


def test_an_episode_played_while_another_is_kept_is_fetched_beside_it(tmp_path: Path) -> None:
    joined, handle = joined_for(tmp_path, 0)
    joined.keep_name = "Show S01E01"  # the owner clicked Download on the first episode
    joined.turn_to(1)
    assert joined.name == "E1.mkv"  # what's being kept is still this torrent's film
    second = joined.film(1)
    assert second is not None and (second.name, second.size) == ("E2.mkv", 500)
    assert all_of(second) == WHOLE[300:800]
    assert handle.raised == [(1, F)]  # fetched as well, with the first still wanted
    # Then a third: the second isn't fetched any more.
    third = joined.film(2)
    assert third is not None and all_of(third) == WHOLE[800:840]
    assert handle.raised == [(1, F), (1, 0), (2, F)]
    # The film itself, by its own number or by none; and no such file.
    for own in (joined.film(0), joined.film(None)):
        assert own is not None and all_of(own) == WHOLE[0:300]
    assert joined.film(9) is None


def test_a_part_way_player_keeps_its_file_when_the_torrent_turns(tmp_path: Path) -> None:
    joined, handle = joined_for(tmp_path, 0)
    watching = joined.film(None)
    assert watching is not None
    start = watching.read(0, 1000)
    joined.turn_to(1)
    assert joined.ready(1)
    # The old player's next read is still the first episode's, not the second's.
    assert start == WHOLE[0:128] and watching.read(128, 1000) == WHOLE[128:256]


def test_a_films_start_and_end_are_asked_for_first_once_its_priorities_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # (2026-10-17) libtorrent changes file priorities a moment after it's asked to, and
    # gives every piece its file's priority again as it does: a piece asked for first
    # before that moment was one among all the rest after it. The owner's next episode
    # didn't start until 98% of it was here.
    monkeypatch.setattr(torrents, "START_BYTES", PIECE)  # two pieces, the least
    monkeypatch.setattr(torrents, "END_BYTES", PIECE)  # one
    handle = StandInHandle()
    handle.taking_hold = 5  # the new priorities hold only on the sixth look
    joined = torrents._Joined(handle, HASH, 1, tmp_path)
    joined.through_libtorrent, handle.joined = True, joined
    assert joined.ready(1)
    assert handle.told[:2] == [("clear",), ("files", [0, F, 0])]
    deadlines = [t for t in handle.told if t[0] == "deadline"]
    # The second episode is pieces 2 to 6: its first two, then its last, in that order.
    assert [d[1] for d in deadlines] == [2, 3, 6]
    assert [d[2] for d in deadlines] == sorted(d[2] for d in deadlines)
    assert all(d[3] for d in deadlines)  # each only once the file priorities held


def test_the_next_episode_is_asked_for_from_its_start_and_the_one_before_isnt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(torrents, "START_BYTES", PIECE)
    monkeypatch.setattr(torrents, "END_BYTES", PIECE)
    joined, handle = joined_for(tmp_path, 0)
    before = joined.film(None)  # the first episode, as its player goes on reading it
    assert before is not None
    handle.told.clear()
    handle.taking_hold = 3
    joined.turn_to(1)  # Next Episode, in the same torrent
    assert joined.ready(1)
    # What was asked for first of the episode before isn't any more; only the next one
    # is fetched, its start and end first, once that has taken hold.
    assert handle.told[:2] == [("clear",), ("files", [0, F, 0])]
    deadlines = [t for t in handle.told if t[0] == "deadline"]
    assert [d[1] for d in deadlines] == [2, 3, 6] and all(d[3] for d in deadlines)
    # The first episode's player, still reading: what's here it's given, but nothing of
    # it is asked for, and it doesn't wait for what isn't here.
    handle.told.clear()
    assert before.read(0, 1000) == WHOLE[0:128]
    handle.missing = {1}
    started = time.monotonic()
    assert before.read(128, 1000) is None and time.monotonic() - started < 1
    assert not [t for t in handle.told if t[0] == "deadline"]


def test_what_the_player_reads_next_is_asked_for_first_and_again_while_it_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(torrents, "NUDGE_S", 0.1)
    joined, handle = joined_for(tmp_path, 1)
    handle.missing = {4}
    handle.told.clear()

    def arrives() -> None:
        time.sleep(0.6)
        handle.missing = set()

    threading.Thread(target=arrives).start()
    # Byte 212 of the second episode is in piece 4 (the episode starts 300 bytes in).
    assert joined.read(212, 10) == WHOLE[512:522]
    asked = [t[1] for t in handle.told if t[0] == "deadline"]
    assert asked[:3] == [4, 5, 6]  # what's just ahead, the next first
    assert asked.count(4) >= 3  # and the piece it waited for, again and again


def test_what_comes_after_is_wanted_sooner_than_the_rest_and_moves_on_as_its_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # So the film arrives roughly in order from where it's played, not from all over.
    monkeypatch.setattr(torrents, "NEXT_BYTES", PIECE)  # two pieces with a deadline
    monkeypatch.setattr(torrents, "SOON_BYTES", PIECE)  # then one sooner than the rest
    monkeypatch.setattr(torrents, "LATER_BYTES", PIECE)  # and one a little sooner
    monkeypatch.setattr(torrents, "STEP_BYTES", 2 * PIECE)
    monkeypatch.setattr(torrents, "END_BYTES", PIECE)  # the end is asked for apart
    joined, handle = joined_for(tmp_path, 1)  # the second episode: pieces 2 to 6
    ordered: list[list[tuple[int, int]]] = []
    handle.prioritize_pieces = ordered.append  # type: ignore[attr-defined]
    joined.read(0, 10)  # piece 2: 2 and 3 first, then 4 sooner, then 5; never 6, the end
    assert ordered == [[(4, torrents.SOON_PRIORITY), (5, torrents.LATER_PRIORITY)]]
    joined.read(84, 10)  # piece 3, within the step: nothing more is moved
    assert len(ordered) == 1
    handle.told.clear()
    joined.read(212, 10)  # piece 4, a step on: 4 and 5 first, and nothing between them
    assert [t[1] for t in handle.told if t[0] == "deadline"] == [4, 5]
    assert len(ordered) == 1  # and the end (asked for apart): nothing more moved
    joined.read(0, 10)  # back to the start (the player went back): from there again
    assert ordered[-1] == ordered[0] and len(ordered) == 2


def test_a_film_being_checked_says_so() -> None:
    import libtorrent as lt

    def told(state: object) -> SimpleNamespace:
        return SimpleNamespace(state=state, progress_ppm=50_000, is_seeding=False)

    assert torrents.checking(told(lt.torrent_status.states.checking_files))
    assert torrents.checking(told(lt.torrent_status.states.checking_resume_data))
    assert not torrents.checking(told(lt.torrent_status.states.downloading))
    assert not torrents.checking(SimpleNamespace())  # a stand-in that says nothing of it


def test_a_films_status_while_its_checked_isnt_how_much_is_here(tmp_path: Path) -> None:
    joined, handle = joined_for(tmp_path, 0)
    looks = {"state": "checking_files"}

    def status() -> SimpleNamespace:
        return SimpleNamespace(
            state=looks["state"], progress_ppm=50_000, is_seeding=False, num_peers=0,
            num_seeds=0, download_rate=0,
        )  # fmt: skip

    handle.status = status  # type: ignore[method-assign]
    assert joined.status()["state"] == "checking"  # the owner's "here: 5%" while it looked
    looks["state"] = "downloading"
    assert joined.status()["state"] == "fetching"


class StandInSession:
    def __init__(self) -> None:
        self.handle = StandInHandle()
        self.added: list[object] = []
        self.removed: list[object] = []

    def add_torrent(self, params: object) -> StandInHandle:
        self.added.append(params)
        return self.handle

    def remove_torrent(self, handle: object, *how: object) -> None:
        self.removed.append(handle)


def test_each_file_of_a_torrent_has_its_own_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = StandInSession()
    monkeypatch.setattr(torrents, "_new_session", lambda: session)
    player = torrents.Player(tmp_path)
    try:
        first = player.play(HASH, 0, [])["url"]
        session.handle.joined = player._joined[HASH]
        assert first.endswith(f"/{HASH}/0")

        def fetch(url: str) -> bytes:
            host = url.split("/")[2]
            connection = http.client.HTTPConnection(host, timeout=10)
            connection.request("GET", "/" + url.split("/", 3)[3])
            answer = connection.getresponse()
            assert answer.status == 200
            return answer.read()

        assert fetch(first) == WHOLE[0:300]
        # Play Next: the same torrent, the next file. Its own address, its own bytes.
        second = player.play(HASH, 1, [])["url"]
        assert second.endswith(f"/{HASH}/1") and second != first
        assert fetch(second) == WHOLE[300:800]
        assert player._joined[HASH].name == "E2.mkv"
        # An address with no file in it is the film the torrent is for.
        assert fetch(second.rsplit("/", 1)[0]) == WHOLE[300:800]
        # No such file, or an address that isn't one: not found.
        for wrong in (
            f"{second.rsplit('/', 1)[0]}/9",
            f"{second}/1",
            f"{second.rsplit('/', 1)[0]}/x",
        ):
            connection = http.client.HTTPConnection(wrong.split("/")[2], timeout=10)
            connection.request("GET", "/" + wrong.split("/", 3)[3])
            assert connection.getresponse().status == 404
    finally:
        player.stop()
