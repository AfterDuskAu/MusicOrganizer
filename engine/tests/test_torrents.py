"""A film played from a torrent: the parts with no torrent in them, and the address a
player opens, served from a stand-in film. No torrent is ever joined in tests."""

from __future__ import annotations

import http.client
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from musicorg import torrents
from musicorg.errors import ReplayMissError

HASH = "ab" * 20


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

    def is_valid(self) -> bool:
        return True

    def torrent_file(self) -> StandInInfo:
        return StandInInfo()

    def prioritize_files(self, priorities: list[int]) -> None:
        self.priorities = priorities

    def file_priority(self, index: int, priority: int) -> None:
        self.raised.append((index, priority))

    def set_piece_deadline(self, piece: int, deadline: int) -> None:
        pass

    def have_piece(self, piece: int) -> bool:
        return True

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
    assert (joined.name, joined.size, handle.priorities) == ("E2.mkv", 500, [0, 4, 0])
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
    assert (joined.name, joined.size, handle.priorities) == ("E2.mkv", 500, [0, 4, 0])
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
    assert handle.raised == [(1, 4)]  # fetched as well, with the first still wanted
    # Then a third: the second isn't fetched any more.
    third = joined.film(2)
    assert third is not None and all_of(third) == WHOLE[800:840]
    assert handle.raised == [(1, 4), (1, 0), (2, 4)]
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


class StandInSession:
    def __init__(self) -> None:
        self.handle = StandInHandle()

    def add_torrent(self, params: object) -> StandInHandle:
        return self.handle

    def remove_torrent(self, handle: object, *how: object) -> None:
        pass


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
