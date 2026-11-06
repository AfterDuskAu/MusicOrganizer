"""sharing (2026-11-05): a video app waiting for its profile's watching to change.

A paired video app asks for its profile's watching `?after=<the revision it has>`: the
computer answers as soon as that watching changes (the app gave a new one, after another
device's sync or a change on the computer), or after `WATCHING_WAIT_S` with 304 and
nothing else. Waiting doesn't keep the computer awake, at most `MOST_WAITING` wait at once,
and switching sharing off or the device hanging up ends a wait.

Every share here listens on this computer's own loopback address only (conftest), and is
asked over real HTTP. No address, pairing code or key is written in this file: each is
made while the test runs.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from typing import Any

import pytest
from conftest import LOOPBACK
from test_sharing import Awake, ask, before_long, filled, paired
from test_sharing import share as share  # noqa: F401  (a fixture)
from test_sharing_video import KID, WATCHING, paired_with, title

from musicorg import config, sharing, tools

_ = filled  # a fixture, used by `share`


def waiting(
    share: sharing.Share, key: str, after: str, *, timeout: float = 10
) -> tuple[int, Any, float]:
    """One wait for a change, as a video app asks: (status, the JSON answer or None, how
    many seconds it took)."""
    assert share.port is not None
    conn = http.client.HTTPConnection(LOOPBACK, share.port, timeout=timeout)
    started = time.monotonic()
    conn.request(
        "GET", f"/sync/v1/watching?{sharing.WAIT_QUERY}={after}",
        headers={"Authorization": "Bearer " + key},
    )  # fmt: skip
    response = conn.getresponse()
    data = response.read()
    took = time.monotonic() - started
    conn.close()
    return response.status, (json.loads(data) if data else None), took


def later(seconds: float, do: Any) -> threading.Thread:
    """Do something on another thread, a little later: as the app gives a new watching."""

    def run() -> None:
        time.sleep(seconds)
        do()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_hello_says_a_video_app_may_wait_for_a_change(share: sharing.Share) -> None:
    accepts = ask(share, "/sync/v1/hello")[1]["accepts"]
    assert accepts[-4:] == ["watching", "watching_wait", "setup", "forget"]
    share.films = True
    assert ask(share, "/sync/v1/hello")[1]["accepts"][-5:] == [
        "watching", "watching_wait", "films", "setup", "forget",
    ]  # fmt: skip


def test_a_watching_not_at_that_revision_is_answered_at_once(share: sharing.Share) -> None:
    key = paired(share, "iPad")
    revision = sharing.set_watching(config.current_profile(), WATCHING)
    status, given, took = waiting(share, key, "an-older-revision")
    assert status == 200
    assert given["revision"] == revision
    assert given["watchlist"][0]["id"] == "tt0000001"
    assert took < 2


def test_a_wait_is_answered_as_soon_as_the_watching_changes(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sharing, "WATCHING_WAIT_S", 8.0)
    key = paired(share, "iPad")
    profile = config.current_profile()
    revision = sharing.set_watching(profile, WATCHING)
    changed = {**WATCHING, "watchlist": [*WATCHING["watchlist"], title(3)]}
    later(0.5, lambda: sharing.set_watching(profile, changed))
    status, given, took = waiting(share, key, revision)
    assert status == 200
    assert given["revision"] != revision
    assert [one["id"] for one in given["watchlist"]][-1] == "tt0000003"
    assert 0.4 < took < 4  # when it changed, not at the end of the wait


def test_nothing_changing_is_304_with_nothing_else_after_the_wait(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sharing, "WATCHING_WAIT_S", 0.5)
    key = paired(share, "iPad")
    revision = sharing.set_watching(config.current_profile(), WATCHING)
    status, given, took = waiting(share, key, revision)
    assert (status, given) == (304, None)
    assert 0.4 < took < 3
    # The same watching given again is the same revision: still nothing new.
    later(0.1, lambda: sharing.set_watching(config.current_profile(), WATCHING))
    assert waiting(share, key, revision)[:2] == (304, None)


def test_another_profiles_change_doesnt_answer_a_wait(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sharing, "WATCHING_WAIT_S", 1.0)
    theirs = paired_with(share, monkeypatch, KID)
    revision = sharing.set_watching(KID, WATCHING)
    later(0.2, lambda: sharing.set_watching(config.current_profile(), {"watchlist": [title(9)]}))
    status, given, took = waiting(share, theirs, revision)
    assert (status, given) == (304, None)
    assert took > 0.9


def test_waiting_doesnt_keep_the_computer_awake_but_a_change_does(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    awake = Awake()
    monkeypatch.setattr(tools, "keep_awake", awake)
    monkeypatch.setattr(sharing, "AWAKE_CHECK_S", 0.01)
    monkeypatch.setattr(sharing, "WATCHING_WAIT_S", 0.3)
    key = paired(share, "iPad")
    profile = config.current_profile()
    revision = sharing.set_watching(profile, WATCHING)
    for _ in range(3):
        assert waiting(share, key, revision)[0] == 304
    assert awake.kept == 0
    monkeypatch.setattr(sharing, "WATCHING_WAIT_S", 8.0)
    later(0.2, lambda: sharing.set_watching(profile, {**WATCHING, "watched": []}))
    assert waiting(share, key, revision)[0] == 200
    assert before_long(lambda: awake.kept == 1)


def test_a_wait_answered_with_nothing_isnt_a_sync(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sharing, "WATCHING_WAIT_S", 0.3)
    key = paired(share, "iPad")
    revision = sharing.set_watching(config.current_profile(), WATCHING)
    assert waiting(share, key, revision)[0] == 304
    assert all(one.get("last_synced") is None for one in sharing.devices())
    assert waiting(share, key, "an-older-revision")[0] == 200
    assert any(one.get("last_synced") for one in sharing.devices())


def test_only_so_many_wait_at_once(share: sharing.Share, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sharing, "MOST_WAITING", 1)
    monkeypatch.setattr(sharing, "WATCHING_WAIT_S", 3.0)
    key = paired(share, "iPad")
    revision = sharing.set_watching(config.current_profile(), WATCHING)
    first = later(0, lambda: waiting(share, key, revision))
    assert before_long(lambda: share._waiting == 1)
    status, given, took = waiting(share, key, revision)
    assert (status, given) == (304, None)
    assert took < 1  # answered at once: the device asks again later
    first.join(timeout=10)
    assert share._waiting == 0


def test_switching_sharing_off_ends_a_wait_without_an_answer(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sharing, "WATCHING_WAIT_S", 30.0)
    key = paired(share, "iPad")
    revision = sharing.set_watching(config.current_profile(), WATCHING)
    ended: dict[str, Any] = {}

    def wait() -> None:
        started = time.monotonic()
        try:
            ended["answer"] = waiting(share, key, revision, timeout=20)[0]
        except (OSError, http.client.HTTPException) as exc:
            ended["answer"] = type(exc).__name__
        ended["took"] = time.monotonic() - started

    thread = threading.Thread(target=wait, daemon=True)
    thread.start()
    assert before_long(lambda: share._waiting == 1)
    share.stop()
    thread.join(timeout=10)
    assert ended["answer"] != 200 and ended["answer"] != 304  # no answer: the line was closed
    assert ended["took"] < 5
    share.start()  # the fixture stops it again


def test_a_device_that_hangs_up_stops_waiting(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sharing, "WATCHING_WAIT_S", 30.0)
    key = paired(share, "iPad")
    revision = sharing.set_watching(config.current_profile(), WATCHING)
    assert share.port is not None
    line = socket.create_connection((LOOPBACK, share.port), timeout=5)
    line.sendall(
        f"GET /sync/v1/watching?{sharing.WAIT_QUERY}={revision} HTTP/1.1\r\n"
        f"Host: {LOOPBACK}:{share.port}\r\nAuthorization: Bearer {key}\r\n\r\n".encode()
    )
    assert before_long(lambda: share._waiting == 1)
    line.close()  # the app went to the background
    assert before_long(lambda: share._waiting == 0)


def test_only_a_paired_device_may_wait(share: sharing.Share) -> None:
    revision = sharing.set_watching(config.current_profile(), WATCHING)
    status, given, took = waiting(share, "not-a-key-" + "x" * 20, revision)
    assert status == 401
    assert given["error"] == "not_paired"
    assert took < 2
