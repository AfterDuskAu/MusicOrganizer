"""Who has a torrent, asked of its trackers (2026-11-12, the owner: "Replace a manual count,
to an automatic person detector"): BEP 15's scrape, against made-up trackers on this
computer. Nothing here reaches the network."""

from __future__ import annotations

import socket
import struct
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from test_rpc import Capture, code, opened, out, result, root, server  # noqa: F401  (fixtures)

from musicorg import rpc, torrents

FILM = "ab" * 20
OTHER = "cd" * 20


class Tracker:
    """A UDP tracker on this computer that answers a scrape with the figures it's given,
    `{info_hash: (seeders, completed, leechers)}` (a torrent it doesn't know: all 0). It
    can drop the first questions, answer every scrape with an error, or say nothing."""

    def __init__(
        self,
        figures: dict[str, tuple[int, int, int]],
        *,
        drop: int = 0,
        error: bool = False,
        silent: bool = False,
    ) -> None:
        self.figures, self.drop, self.error, self.silent = figures, drop, error, silent
        self.asked: list[tuple[int, list[str]]] = []  # (action, the torrents asked about)
        self.connection = 0x1234_5678_9ABC
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.05)
        self.url = f"udp://127.0.0.1:{self.sock.getsockname()[1]}/announce"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                data, sender = self.sock.recvfrom(4096)
            except TimeoutError:
                continue
            except OSError:
                return
            connection, action, tid = struct.unpack_from(">QII", data)
            body = data[16:]
            hashes = [body[i : i + 20].hex() for i in range(0, len(body), 20)]
            self.asked.append((action, hashes))
            if self.silent:
                continue
            if self.drop > 0:
                self.drop -= 1
                continue
            if action == 0 and connection == torrents.UDP_TRACKER_ID:
                self.sock.sendto(struct.pack(">IIQ", 0, tid, self.connection), sender)
            elif action == 2 and connection == self.connection:
                if self.error:
                    self.sock.sendto(struct.pack(">II", 3, tid) + b"not today", sender)
                    continue
                said = b"".join(
                    struct.pack(">iii", *self.figures.get(one, (0, 0, 0))) for one in hashes
                )
                self.sock.sendto(struct.pack(">II", 2, tid) + said, sender)

    def scrapes(self) -> list[list[str]]:
        return [hashes for action, hashes in self.asked if action == 2]

    def close(self) -> None:
        self._stop.set()
        self._thread.join(1)
        self.sock.close()


@pytest.fixture
def trackers() -> Iterator[list[Tracker]]:
    made: list[Tracker] = []
    yield made
    for one in made:
        one.close()


def test_every_tracker_is_asked_once_and_each_figures_largest_is_taken(
    trackers: list[Tracker],
) -> None:
    big = Tracker({FILM: (250, 900, 62), OTHER: (5, 10, 3)})
    small = Tracker({FILM: (27, 100, 140)})
    trackers += [big, small]
    started = time.monotonic()
    said = torrents.scrape(
        [
            (FILM.upper(), [small.url, "https://tracker.example/announce", "wss://x.example"]),
            (OTHER, [small.url.replace("/announce", "")]),  # the same tracker, put another way
            ("not a torrent's id", [small.url]),
        ],
        public=(big.url,),
    )
    assert time.monotonic() - started < 1  # over once every tracker has answered
    # Seeds: the most any tracker said have all of it; everyone: that and the most any said
    # are still fetching it. Each figure's own largest (trackers know of the same people).
    assert said == {FILM: (250, 250 + 140), OTHER: (5, 5 + 3)}
    # Each tracker asked once for a connection, then once about all its torrents.
    for one in (big, small):
        assert [action for action, _ in one.asked] == [0, 2]
        assert sorted(one.scrapes()[0]) == sorted([FILM, OTHER])


def test_a_torrent_no_tracker_answered_about_isnt_listed(trackers: list[Tracker]) -> None:
    quiet = Tracker({}, silent=True)
    refusing = Tracker({FILM: (9, 9, 9)}, error=True)
    unknown = Tracker({})  # answers: nobody has it
    trackers += [quiet, refusing, unknown]
    said = torrents.scrape([(FILM, [quiet.url, refusing.url])], public=())
    assert said == {}  # one said nothing, the other an error: nothing known
    said = torrents.scrape([(FILM, [quiet.url, unknown.url])], public=())
    assert said == {FILM: (0, 0)}  # 0 and 0 is an answer


def test_a_question_nothing_came_back_for_is_sent_again_once(
    trackers: list[Tracker], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(torrents, "SCRAPE_AGAIN_S", 0.2)
    once = Tracker({FILM: (4, 4, 1)}, drop=1)  # loses the first connection question
    twice = Tracker({FILM: (7, 7, 2)}, drop=2)  # loses it both times
    trackers += [once, twice]
    said = torrents.scrape([(FILM, [once.url, twice.url])], public=())
    assert said == {FILM: (4, 5)}
    assert [action for action, _ in once.asked] == [0, 0, 2]
    assert [action for action, _ in twice.asked] == [0, 0]  # given up on after two


def test_the_asking_is_over_in_its_time_however_slow_a_tracker_is(
    trackers: list[Tracker], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(torrents, "SCRAPE_S", 0.4)
    monkeypatch.setattr(torrents, "SCRAPE_AGAIN_S", 1.0)
    quiet = Tracker({}, silent=True)
    answering = Tracker({FILM: (3, 3, 0)})
    trackers += [quiet, answering]
    started = time.monotonic()
    said = torrents.scrape([(FILM, [quiet.url, answering.url])], public=())
    assert time.monotonic() - started < 0.9
    assert said == {FILM: (3, 3)}


def test_a_tracker_that_never_answers_doesnt_hold_up_the_others(trackers: list[Tracker]) -> None:
    # 2026-11-12: one that never answered held the figure for the whole 3 s. Now the asking
    # is over a second after the first tracker answered.
    quick = Tracker({FILM: (12, 12, 4)})
    quiet = Tracker({}, silent=True)
    trackers += [quick, quiet]
    started = time.monotonic()
    said = torrents.scrape([(FILM, [quick.url, quiet.url])], public=())
    took = time.monotonic() - started
    assert torrents.SCRAPE_AFTER_FIRST_S - 0.1 < took < torrents.SCRAPE_AFTER_FIRST_S + 0.6
    assert took < torrents.SCRAPE_S - 1
    assert said == {FILM: (12, 16)}


def test_many_torrents_are_asked_about_seventy_at_a_time(trackers: list[Tracker]) -> None:
    many = [f"{n:040x}" for n in range(1, 151)]
    tracker = Tracker({one: (n, n, 1) for n, one in enumerate(many)})
    trackers.append(tracker)
    said = torrents.scrape([(one, []) for one in many], public=(tracker.url,))
    assert [len(asked) for asked in tracker.scrapes()] == [70, 70, 10]
    assert len(said) == 150 and said[many[149]] == (149, 150)


def test_only_udp_trackers_are_asked_and_in_tests_only_this_computers() -> None:
    assert torrents._udp_tracker("udp://127.0.0.1:6969/announce") == ("127.0.0.1", 6969)
    assert torrents._udp_tracker(" UDP://LocalHost:80 ") == ("localhost", 80)
    for not_one in ("http://127.0.0.1:80/announce", "udp://127.0.0.1/announce", "udp://", "",
                    "udp://127.0.0.1:99999/announce", "wss://tracker.example"):  # fmt: skip
        assert torrents._udp_tracker(not_one) is None, not_one
    # Every test runs in replay mode: a tracker anywhere else is never asked, the public
    # ones included.
    assert torrents._udp_tracker("udp://tracker.example:1337/announce") is None
    assert all(torrents._udp_tracker(one) is None for one in torrents.PUBLIC_TRACKERS)


def test_outside_replay_mode_a_trackers_address_is_taken_as_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(torrents.REPLAY_ENV, raising=False)
    assert torrents._udp_tracker("udp://Tracker.Example:1337/announce") == (
        "tracker.example", 1337,
    )  # fmt: skip


def test_nothing_is_joined_to_ask(trackers: list[Tracker], monkeypatch: pytest.MonkeyPatch) -> None:
    def never() -> Any:
        raise AssertionError("a torrent was joined")

    monkeypatch.setattr(torrents, "_new_session", never)
    tracker = Tracker({FILM: (1, 1, 1)})
    trackers.append(tracker)
    assert torrents.scrape([(FILM, [tracker.url])], public=()) == {FILM: (1, 2)}


def test_the_app_asks_who_has_a_films_torrents(
    opened: rpc.Server,  # noqa: F811
    trackers: list[Tracker],
) -> None:
    assert code(opened, "torrent.scrape") == rpc.INVALID_PARAMS
    assert code(opened, "torrent.scrape", ways="ab") == rpc.INVALID_PARAMS
    assert code(opened, "torrent.scrape", ways=[{"trackers": []}]) == rpc.INVALID_PARAMS
    assert code(opened, "torrent.scrape", ways=[{"info_hash": FILM, "trackers": [1]}]) == (
        rpc.INVALID_PARAMS
    )
    assert "torrent.scrape" in rpc.SLOW_METHODS  # it can take seconds: nothing waits behind it
    tracker = Tracker({FILM: (25, 40, 5)})
    trackers.append(tracker)
    said = result(
        opened, "torrent.scrape",
        ways=[{"info_hash": FILM.upper(), "trackers": [tracker.url]}, {"info_hash": OTHER}],
    )  # fmt: skip
    # The other torrent has no tracker but the public ones, which a test never asks.
    assert said == {"counts": [{"info_hash": FILM, "seeds": 25, "peers": 30}]}
    assert opened._films is None  # no player was made, nothing joined
