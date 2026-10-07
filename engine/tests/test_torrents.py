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
