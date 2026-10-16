"""sharing (2026-10-16): what's watched, and films, for a video app on a paired device.

A device paired with any profile on this computer may give the computer its watching and
ask for its profile's, list the films in the Movies folder and fetch one, and send a film
of its own, which is copied into the Movies folder once it has all arrived. The music's
requests still answer only the devices of the profile whose library is shared.

Every share here listens on this computer's own loopback address only (conftest), and is
asked over real HTTP. No address, pairing code or key is written in this file: each is
made while the test runs.
"""

from __future__ import annotations

import errno
import hashlib
import http.client
import io
import json
import logging
import os
import socket
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from conftest import LOOPBACK
from test_rpc import Capture, code, opened, out, result, root, server  # noqa: F401  (fixtures)
from test_sharing import Awake, ask, before_long, fetch, filled, keep, kept, paired, the_list
from test_sharing import share as share  # noqa: F401  (a fixture)

from musicorg import config, fileops, naming, rpc, sharing, tools
from musicorg.errors import FileOperationError, OutsideLibraryError, UserError
from musicorg.library import Library

_ = filled  # a fixture, used by `share`

KID = "kid-profile"  # another profile on the same computer
FILM = b"a made-up film, " * 4096  # 64 KiB


def paired_with(share: sharing.Share, monkeypatch: pytest.MonkeyPatch, profile: str) -> str:
    """Pair a device while `profile` is the one open in the app, as a video app is paired
    with its owner's profile; then the share's own profile is open again."""
    monkeypatch.setenv(config.PROFILE_ENV, profile)
    try:
        return paired(share, "iPad")
    finally:
        monkeypatch.delenv(config.PROFILE_ENV)


def title(number: int, **more: Any) -> dict[str, Any]:
    return {"id": f"tt{number:07d}", "type": "movie", "name": f"Made-up Film {number}", **more}


WATCHING = {
    "watchlist": [title(1, poster="https://pictures.example/1.jpg", year="2020"),
                  {"id": "tt0000002", "type": "series", "name": "A Made-up Show",
                   "genres": ["Drama"]}],
    "watched": [{"id": "tt0000002:1:1", "at": "2025-10-15T20:00:00Z"}],
    "progress": [{"id": "tt0000002", "type": "series", "name": "A Made-up Show",
                  "episode": "tt0000002:1:2", "detail": "S1 E2 · Its Title", "seconds": 600,
                  "length": 2400.5, "at": "2025-10-15T21:00:00Z"}],
}  # fmt: skip


def send_watching(share: sharing.Share, key: str, body: Any) -> tuple[int, Any]:
    return ask(share, "/sync/v1/watching", key=key, body=body)


def raw(share: sharing.Share, method: str, path: str, data: bytes, headers: dict[str, str]) -> Any:
    """A request sent as these very bytes: (status, the JSON answer)."""
    assert share.port is not None
    conn = http.client.HTTPConnection(LOOPBACK, share.port, timeout=30)
    conn.request(method, path, data, headers)
    response = conn.getresponse()
    answer = json.loads(response.read())
    conn.close()
    return response.status, answer


def start(share: sharing.Share, key: str, **film: Any) -> tuple[int, Any]:
    about = {"kind": "movie", "title": "A Made-up Film", "year": 2021, "type": "mkv",
             "size": len(FILM), **film}  # fmt: skip
    return ask(share, "/sync/v1/uploads", key=key, body=about)


def put(
    share: sharing.Share, key: str, upload: str, first: int, data: bytes, size: int = len(FILM)
) -> tuple[int, Any]:
    """One part of a film, as a device sends it."""
    last = first + len(data) - 1
    return raw(share, "PUT", "/sync/v1/uploads/" + upload, data, {
        "Authorization": "Bearer " + key, "Content-Type": "application/octet-stream",
        "Content-Range": f"bytes {first}-{last}/{size}",
    })  # fmt: skip


def uploads() -> Path:
    return config.app_dirs().cache / fileops.UPLOADS_DIR


def movies() -> Path:
    return config.media_folders()["movies"]


# ---- hello ---------------------------------------------------------------------------------


def test_hello_says_what_a_video_app_may_ask_and_whether_a_device_is_known(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    hello = ask(share, "/sync/v1/hello")[1]
    assert hello["accepts"] == ["changes", "favourites", "plays", "playlists", "watching",
                                "forget"]  # fmt: skip
    assert (hello["paired"], hello["known"]) == (False, False)
    share.films = True
    assert ask(share, "/sync/v1/hello")[1]["accepts"][-3:] == ["watching", "films", "forget"]

    mine, theirs = paired(share), paired_with(share, monkeypatch, KID)
    for key, paired_here in ((mine, True), (theirs, False)):
        said = ask(share, "/sync/v1/hello", key=key)[1]
        assert (said["paired"], said["known"]) == (paired_here, True)
    assert ask(share, "/sync/v1/hello", key=theirs + "x")[1]["known"] is False
    # Unpaired from its own profile, it's known nowhere.
    (gone,) = config.load_devices(KID)
    sharing.forget(gone["id"], profile=KID)
    assert ask(share, "/sync/v1/hello", key=theirs)[1]["known"] is False


# ---- which profile -------------------------------------------------------------------------


def test_a_device_of_another_profile_gets_only_a_video_apps_requests(
    share: sharing.Share, filled: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    keep(movies(), "A Film (2020).mp4", b"film bytes")
    share.films = True
    mine, theirs = paired(share), paired_with(share, monkeypatch, KID)
    song = the_list(share, mine)["tracks"][0]["audio"]

    # The music is the open profile's: another profile's device isn't paired for it.
    for path in ("/sync/v1/library", "/sync/v1/files/" + quote(song["id"], safe=""),
                 "/sync/v1/whatever"):  # fmt: skip
        status, answer = ask(share, path, key=theirs)
        assert status in (401, 404) and answer["error"] in ("not_paired", "not_found"), path
    assert ask(share, "/sync/v1/library", key=theirs)[0] == 401
    status, answer = ask(share, "/sync/v1/changes", key=theirs, body={"changes": []})
    assert (status, answer["error"]) == (401, "not_paired")

    # What a video app asks for, it's given: films, a film's bytes, its own watching.
    films = ask(share, "/sync/v1/films", key=theirs)[1]
    assert [m["title"] for m in films["movies"]] == ["A Film (2020)"]
    assert fetch(share, theirs, films["movies"][0]["video"]) == b"film bytes"
    sharing.set_watching(KID, WATCHING)
    sharing.set_watching(config.current_profile(), {"watchlist": [title(9)]})
    assert ask(share, "/sync/v1/watching", key=theirs)[1]["watchlist"][0]["id"] == "tt0000001"
    assert ask(share, "/sync/v1/watching", key=mine)[1]["watchlist"][0]["id"] == "tt0000009"
    assert start(share, theirs)[0] == 200

    # A song's file is never a film's, whatever key asks: only the open profile's gets it.
    status, answer = ask(share, "/sync/v1/files/" + quote(song["id"], safe=""), key=theirs)
    assert (status, answer["error"]) == (404, "not_found")
    assert fetch(share, mine, song)


def test_a_video_apps_requests_keep_the_computer_awake(
    filled: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    awake = Awake()
    monkeypatch.setattr(tools, "keep_awake", awake)
    monkeypatch.setattr(sharing, "AWAKE_CHECK_S", 0.01)
    share = sharing.Share(filled, films=True)
    share.start()
    try:
        ask(share, "/sync/v1/watching", key="not-a-key-" + "x" * 20)
        ask(share, "/sync/v1/films")
        time.sleep(0.1)
        assert awake.kept == 0  # nobody without a key keeps it awake
        theirs = paired_with(share, monkeypatch, KID)
        ask(share, "/sync/v1/films", key=theirs)
        assert before_long(lambda: awake.kept == 1)
    finally:
        share.stop()


def test_a_device_of_another_profile_can_forget_the_computer(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine, theirs = paired(share), paired_with(share, monkeypatch, KID)
    status, answer = ask(share, "/sync/v1/forget", key=theirs, body={})
    assert (status, answer) == (200, {"forgotten": True})
    assert config.load_devices(KID) == []
    assert len(sharing.devices()) == 1 and ask(share, "/sync/v1/hello", key=mine)[1]["paired"]


# ---- what's watched ------------------------------------------------------------------------


def test_only_the_agreements_fields_are_kept_checked_and_cut() -> None:
    now = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    sent = {
        "watchlist": [
            title(1, poster="https://pictures.example/1.jpg", posterShape="landscape",
                  year=2020, genres=[f"Genre {n}" * 10 for n in range(30)], anime=True,
                  stream={"infoHash": "made-up"}, url="https://elsewhere.example/play"),
            title(2, poster="http://pictures.example/2.jpg", posterShape="not a shape!"),
            title(1, name="The same id again"),  # once: the first
            {"id": "kitsu:1", "type": "anime", "name": "A Made-up Anime"},
            {"id": "a" * 101, "type": "movie", "name": "An id too long"},
            {"id": "tt0000003", "type": "channel", "name": "Not a title"},
            {"id": "tt0000004", "type": "movie", "name": "  "},
            {"id": "tt0000005", "type": "movie", "name": "N" * 400 + "\x00\ud800"},
            "not a title", None, 7,
        ],
        "watched": [
            {"id": "tt0000002:1:1", "at": "2026-10-15T20:00:00.250+02:00"},
            {"id": "tt0000002:1:1", "at": "2026-10-01T00:00:00Z"},  # once: the first
            {"id": "tt0000002:1:2", "at": "2099-01-01T00:00:00Z"},  # later than now: now
            {"id": "tt0000002:1:3", "at": "not a time"},
            {"id": "tt0000002:1:4"},
            {"id": "tt0000002:1:5", "at": "0001-01-01T00:00:00Z"},
            {"id": " tt0000006", "at": "2026-10-15T20:00:00Z"},
        ],
        "progress": [
            {"id": "tt0000002", "type": "series", "name": "Show", "episode": "tt0000002:1:2",
             "seconds": 60, "length": 2400, "at": "2026-10-15T20:00:00Z", "extra": True,
             "title": title(2, type="series")},
            {"id": "tt0000002", "type": "series", "name": "Show", "episode": "tt0000002:1:5",
             "seconds": 30.5, "length": 2400, "at": "2026-10-15T21:00:00Z"},  # later: wins
            {"id": "tt0000007", "type": "movie", "name": "NaN", "seconds": float("nan"),
             "length": 1, "at": "2026-10-15T20:00:00Z"},
            {"id": "tt0000008", "type": "movie", "name": "Bool", "seconds": True,
             "length": 1, "at": "2026-10-15T20:00:00Z"},
            {"id": "tt0000009", "type": "movie", "name": "Too long", "seconds": 1,
             "length": 10**9, "at": "2026-10-15T20:00:00Z"},
            {"id": "tt0000010", "type": "movie", "name": "No time", "seconds": 1, "length": 2},
        ],
        "somethingElse": ["never kept"],
    }  # fmt: skip
    checked = sharing.check_watching(sent, now=now)
    assert set(checked) == {"watchlist", "watched", "progress"}

    first, second, anime, cut = checked["watchlist"]
    genres = [(f"Genre {n}" * 10)[:50].rstrip() for n in range(20)]  # 20, of 50 characters
    assert first == {"id": "tt0000001", "type": "movie", "name": "Made-up Film 1",
                     "poster": "https://pictures.example/1.jpg", "posterShape": "landscape",
                     "year": "2020", "genres": genres, "anime": True}  # fmt: skip
    assert second == {"id": "tt0000002", "type": "movie", "name": "Made-up Film 2"}
    assert anime == {"id": "kitsu:1", "type": "series", "name": "A Made-up Anime", "anime": True}
    assert cut["id"] == "tt0000005" and cut["name"] == "N" * 300

    assert checked["watched"] == [
        {"id": "tt0000002:1:1", "at": "2026-10-15T18:00:00Z"},
        {"id": "tt0000002:1:2", "at": "2026-10-16T12:00:00Z"},
    ]
    (line,) = checked["progress"]
    assert line == {"id": "tt0000002", "type": "series", "name": "Show", "seconds": 30.5,
                    "length": 2400.0, "at": "2026-10-15T21:00:00Z",
                    "episode": "tt0000002:1:5"}  # fmt: skip
    # What's kept can always be written as JSON a device reads: no NaN, no lone surrogate.
    json.dumps(checked, allow_nan=False).encode("utf-8")


def test_a_device_sends_its_watching_and_the_app_takes_it(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    told: list[str] = []
    share._watched = told.append
    theirs = paired_with(share, monkeypatch, KID)
    status, answer = send_watching(share, theirs, WATCHING)
    assert (status, answer) == (200, {"taken": True})
    assert told == [KID]
    (sent,) = sharing.sent_watching()
    (device,) = config.load_devices(KID)
    assert (sent["profile"], sent["device"], sent["kind"]) == (KID, device["id"], "iPad")
    assert sent["watching"] == sharing.check_watching(WATCHING)
    assert sent["id"].startswith("w_") and sent["at"]

    # A newer sending replaces the one the app hasn't taken; another device's stays.
    again = {**WATCHING, "watchlist": [title(5)]}
    assert send_watching(share, theirs, again) == (200, {"taken": True})
    (newer,) = sharing.sent_watching()
    assert newer["id"] != sent["id"] and newer["watching"]["watchlist"] == [title(5)]
    mine = paired(share)
    assert send_watching(share, mine, {"watched": []})[0] == 200
    assert [s["profile"] for s in sharing.sent_watching()] == [KID, config.current_profile()]
    # The app letting go of the older one doesn't let go of the newer.
    assert sharing.done_watching([sent["id"]]) == 2
    assert sharing.done_watching([newer["id"]]) == 1
    assert told == [KID, KID, config.current_profile()]


def test_a_devices_watching_is_checked_before_any_is_kept(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    theirs = paired_with(share, monkeypatch, KID)
    sent = {"Authorization": "Bearer " + theirs, "Content-Type": "application/json"}
    for data in (b"not json", b"[1, 2]", b'"watching"', b"\xff\xfe", b"[" * 100_000,
                 b'{"watchlist": "all"}', b'{"progress": {"id": "x"}}'):  # fmt: skip
        status, answer = raw(share, "POST", "/sync/v1/watching", data, sent)
        assert (status, answer["error"]) == (400, "bad_request"), data[:20]
    for too_many in ({"watchlist": [title(1)] * (sharing.MOST_TITLES + 1)},
                     {"progress": [{}] * (sharing.MOST_PROGRESS + 1)},
                     {"watched": [{}] * (sharing.MOST_WATCHED + 1)}):  # fmt: skip
        status, answer = send_watching(share, theirs, too_many)
        assert (status, answer["error"]) == (413, "bad_request")
        assert "at most" in answer["message"]
    big = b'{"watched": [' + b'{"id": "tt1", "at": "2026-10-15T20:00:00Z"},' * 200_000 + b"{}]}"
    assert len(big) > sharing.MAX_WATCHING_BODY
    status, answer = raw(share, "POST", "/sync/v1/watching", big, sent)
    assert (status, answer["error"]) == (413, "bad_request")
    assert sharing.sent_watching() == []
    # Without a key, nothing is looked at; with nothing in it, it's an empty watching.
    status, answer = send_watching(share, "not-a-key-" + "x" * 20, WATCHING)
    assert (status, answer["error"]) == (401, "not_paired")
    assert raw(share, "POST", "/sync/v1/watching", b"", sent)[0] == 200
    (empty,) = sharing.sent_watching()
    assert empty["watching"] == {"watchlist": [], "watched": [], "progress": []}


def test_a_profiles_watching_is_given_as_the_app_last_gave_it(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    theirs = paired_with(share, monkeypatch, KID)
    status, answer = ask(share, "/sync/v1/watching", key=theirs)
    assert (status, answer["error"]) == (500, "unavailable")  # the app hasn't given it yet
    revision = sharing.set_watching(KID, WATCHING)
    status, given = ask(share, "/sync/v1/watching", key=theirs)
    assert status == 200
    assert given == {"format": 1, "revision": revision, **sharing.check_watching(WATCHING)}
    assert (config.load_devices(KID)[0]["last_synced"] or "") != ""
    # Its revision changes when it does, and only then.
    assert sharing.set_watching(KID, WATCHING) == revision
    assert sharing.set_watching(KID, {**WATCHING, "watched": []}) != revision


def test_what_was_sent_waits_across_a_restart(
    share: sharing.Share, filled: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    theirs = paired_with(share, monkeypatch, KID)
    sharing.set_watching(KID, WATCHING)
    assert send_watching(share, theirs, {"watchlist": [title(5)]})[0] == 200
    share.stop()
    again = sharing.Share(filled)
    again.start()
    try:
        (sent,) = sharing.sent_watching()
        assert sent["watching"]["watchlist"] == [title(5)]
        assert ask(again, "/sync/v1/watching", key=theirs)[1]["watchlist"][0]["id"] == "tt0000001"
    finally:
        again.stop()
    # It's kept in the app's settings folder, readable by this user only, with no key.
    kept_at = config.watching_path()
    assert kept_at.parent == config.app_dirs().config
    if os.name != "nt":
        assert kept_at.stat().st_mode & 0o077 == 0
    text = kept_at.read_text(encoding="utf-8")
    assert theirs not in text and hashlib.sha256(theirs.encode()).hexdigest() not in text


def test_the_app_gives_and_takes_watching(
    opened: rpc.Server,  # noqa: F811
    out: Capture,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert code(opened, "watching.set", profile="not a profile!", watching={}) == rpc.INVALID_PARAMS
    assert code(opened, "watching.set", profile=KID, watching=[]) == rpc.INVALID_PARAMS
    assert code(opened, "watching.set", profile=KID) == rpc.INVALID_PARAMS
    many = {"watchlist": [title(1)] * (sharing.MOST_TITLES + 1)}
    assert code(opened, "watching.set", profile=KID, watching=many) == rpc.INVALID_PARAMS
    given = result(opened, "watching.set", profile=KID, watching=WATCHING)
    assert set(given) == {"revision"}
    assert result(opened, "watching.sent") == {"sent": []}

    result(opened, "sharing.set", on=True)
    share = opened._share
    assert share is not None
    monkeypatch.setenv(config.PROFILE_ENV, KID)
    shown = result(opened, "sharing.pair")["code"]
    key = ask(share, "/sync/v1/pair", body={"code": shown, "device": "iPad"})[1]["key"]
    (device,) = result(opened, "sharing.status")["devices"]
    monkeypatch.delenv(config.PROFILE_ENV)

    assert ask(share, "/sync/v1/watching", key=key)[1]["revision"] == given["revision"]
    assert send_watching(share, key, {"watchlist": [title(5)]})[0] == 200
    assert out.notes("watching.changed") == [{"profile": KID}]
    (sent,) = result(opened, "watching.sent")["sent"]
    assert set(sent) == {"id", "profile", "device", "kind", "at", "watching"}
    assert (sent["profile"], sent["device"], sent["kind"]) == (KID, device["id"], "iPad")
    assert sent["watching"] == {"watchlist": [title(5)], "watched": [], "progress": []}
    assert code(opened, "watching.done", ids=[1]) == rpc.INVALID_PARAMS
    assert result(opened, "watching.done", ids=[sent["id"], "w_not_there"]) == {"left": 0}
    assert result(opened, "watching.sent") == {"sent": []}
    # The key is the device's alone: nothing the app was sent has it.
    said = out.data.decode("utf-8")
    assert key not in said and hashlib.sha256(key.encode()).hexdigest() not in said
    result(opened, "sharing.set", on=False)


# ---- the films -----------------------------------------------------------------------------


def test_the_films_are_listed_only_while_shared(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    folders = config.media_folders()
    keep(folders["movies"], "A Film (2020).mp4", b"a film")
    kept("series", "Pilot", show="A Made-up Show", season=1, episode=1)
    clip = keep(folders["media"], "A Clip.mp4", b"a clip")  # the videos folder: not a film
    theirs, mine = paired_with(share, monkeypatch, KID), paired(share)

    for key in (theirs, mine):
        status, answer = ask(share, "/sync/v1/films", key=key)
        assert (status, answer["error"]) == (403, "not_shared")
        assert "Settings" in answer["message"]
    share.films = True
    films = ask(share, "/sync/v1/films", key=theirs)[1]
    assert set(films) == {"format", "revision", "movies"} and films["format"] == 1
    assert sorted((m["title"], m["kind"]) for m in films["movies"]) == [
        ("A Film (2020)", "movie"), ("Pilot", "series")
    ]  # fmt: skip
    # Exactly as the list of everything gives them.
    everything = the_list(share, mine)["movies"]
    assert [m for m in everything if m["kind"] != "video"] == films["movies"]
    assert films["revision"] == ask(share, "/sync/v1/films", key=mine)[1]["revision"]
    keep(folders["movies"], "Another Film.mov")
    assert ask(share, "/sync/v1/films", key=mine)[1]["revision"] != films["revision"]

    # A clip in the videos folder is the open profile's devices' only.
    (video,) = [m for m in everything if m["kind"] == "video"]
    assert fetch(share, mine, video["video"]) == clip.read_bytes()
    status, _ = ask(share, "/sync/v1/files/" + quote(video["video"]["id"], safe=""), key=theirs)
    assert status == 404
    # Switched off again: no film is given out, by the id it had either.
    share.films = False
    share._films_built_at = None
    film = films["movies"][0]["video"]
    status, _ = ask(share, "/sync/v1/files/" + quote(film["id"], safe=""), key=theirs)
    assert status == 404


# ---- a film sent from a device -------------------------------------------------------------


def test_a_film_arrives_in_parts_and_is_kept_in_the_movies_folder(
    share: sharing.Share, filled: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    share.films = True
    theirs = paired_with(share, monkeypatch, KID)
    status, began = start(share, theirs)
    assert status == 200 and began["have"] == 0 and began["already"] is False
    upload = began["upload"]
    assert upload.startswith("u-") and len(upload) == 26
    half = len(FILM) // 2

    assert put(share, theirs, upload, 0, FILM[:half]) == (200, {"have": half})
    # Asked again (the same device, name and size): the same upload, from where it got to.
    assert start(share, theirs)[1] == {"upload": upload, "have": half, "already": False}
    assert start(share, theirs, size=len(FILM) + 1)[1]["upload"] != upload
    # A part that doesn't start where what arrived ends: nothing is written.
    status, answer = put(share, theirs, upload, half + 10, FILM[half + 10 :])
    assert (status, answer["error"], answer["have"]) == (409, "wrong_place", half)
    status, answer = put(share, theirs, upload, 0, FILM[:10])
    assert (status, answer["have"]) == (409, half)
    assert (uploads() / f"{upload}.part").read_bytes() == FILM[:half]

    status, answer = put(share, theirs, upload, half, FILM[half:])
    assert (status, answer) == (200, {"have": len(FILM), "kept": True})
    film = movies() / "A Made-up Film (2021).mkv"
    assert film.read_bytes() == FILM
    # What arrived is let go once it's kept.
    assert not any(uploads().glob(upload + ".*"))
    # Sent again, it's there already: nothing more is sent.
    assert start(share, theirs)[1] == {"upload": upload, "have": 0, "already": True}
    assert start(share, theirs, type="mp4")[1]["already"] is True  # any ending
    # And nothing of the library's was touched.
    assert not any(filled.root.rglob("*.mkv"))


def test_episodes_and_anime_go_in_their_folders_never_over_a_file(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    share.films = True
    key = paired(share)
    places = []
    for about in (
        {"kind": "series", "show": "A Made-up Show", "season": 1, "episode": 2,
         "title": "Its Title", "type": "mp4"},
        {"kind": "anime", "show": "A Made-up Anime", "season": 0, "episode": 1, "title": ""},
        {"kind": "anime", "title": "An Anime Film", "year": "1999", "type": "webm"},
    ):  # fmt: skip
        status, began = start(share, key, **about)
        assert status == 200 and began["already"] is False, began
        places.append(began["upload"])
    # A file of the first one's name appears while it's arriving: it isn't written over.
    season = movies() / "Series" / "A Made-up Show" / "Season 1"
    episode = keep(season, "A Made-up Show S01E02 - Its Title.mp4", b"the owner's own copy")
    for upload in places:
        assert put(share, key, upload, 0, FILM)[1] == {"have": len(FILM), "kept": True}
    assert episode.read_bytes() == b"the owner's own copy"
    assert (season / "A Made-up Show S01E02 - Its Title (2).mp4").read_bytes() == FILM
    anime = movies() / "Anime"
    assert (anime / "A Made-up Anime" / "Specials" / "A Made-up Anime S00E01.mkv").is_file()
    assert (anime / "An Anime Film (1999).webm").is_file()
    # And each is read back from where it is as what it is.
    found = sorted(naming.kept_details(p.relative_to(movies()).as_posix())["kind"]
                   for p in movies().rglob("*") if p.is_file())  # fmt: skip
    assert found == ["anime", "anime", "series", "series"]


def test_what_a_device_says_of_a_film_is_checked(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = paired(share)
    # Not while the films switch is off.
    status, answer = start(share, key)
    assert (status, answer["error"]) == (403, "not_shared")
    status, answer = put(share, key, "u-" + "0" * 24, 0, FILM[:10])
    assert (status, answer["error"]) == (403, "not_shared")
    share.films = True
    for wrong in ({"kind": "video"}, {"kind": "documentary"}, {"show": "A Show"},
                  {"kind": "series", "season": 1}, {"title": "", "kind": "movie"},
                  {"kind": "series", "show": "A Show", "season": -1},
                  {"kind": "series", "show": "A Show", "episode": True},
                  {"type": "exe"}, {"type": None}, {"size": 0}, {"size": "12"},
                  {"size": True}, {"size": sharing.MOST_UPLOAD + 1}, {"title": 7}):  # fmt: skip
        status, answer = start(share, key, **wrong)
        assert (status, answer["error"]) == (400, "bad_request"), wrong
        assert answer["message"]
    sent = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    for data in (b"not json", b"[]", b"7"):
        assert raw(share, "POST", "/sync/v1/uploads", data, sent)[0] == 400
    assert raw(share, "POST", "/sync/v1/uploads", b"{" + b" " * 5000 + b"}", sent)[0] == 413
    assert start(share, "not-a-key-" + "x" * 20)[0] == 401
    assert start(share, key, size=sharing.MOST_UPLOAD)[0] in (200, 500)  # 200 GB is the most
    assert list(uploads().glob("*.part")) == []  # nothing was sent: nothing is written


def test_a_part_is_checked_before_any_of_it_is_written(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    share.films = True
    mine, theirs = paired(share), paired_with(share, monkeypatch, KID)
    upload = start(share, mine)[1]["upload"]
    for wrong in ("not-an-upload", "u-" + "0" * 24, upload.upper()):
        status, answer = put(share, mine, wrong, 0, FILM[:10])
        assert (status, answer["error"]) == (404, "not_found"), wrong
    # Another device's upload is as good as none.
    status, answer = put(share, theirs, upload, 0, FILM[:10])
    assert (status, answer["error"]) == (404, "not_found")
    status, answer = put(share, "not-a-key-" + "x" * 20, upload, 0, FILM[:10])
    assert (status, answer["error"]) == (401, "not_paired")
    headers = {"Authorization": "Bearer " + mine}
    path = "/sync/v1/uploads/" + upload
    for ranges in (None, "bytes=0-9", "bytes 0-9/12", "bytes 5-4/" + str(len(FILM)),
                   f"bytes 0-{len(FILM)}/{len(FILM)}", "bytes 0-19/" + str(len(FILM))):  # fmt: skip
        sent = dict(headers, **({"Content-Range": ranges} if ranges else {}))
        status, answer = raw(share, "PUT", path, FILM[:10], sent)
        assert (status, answer["error"]) == (400, "bad_request"), ranges
    monkeypatch.setattr(sharing, "MOST_PART", 10)
    status, answer = put(share, mine, upload, 0, FILM[:11])
    assert (status, answer["error"]) == (413, "bad_request")
    assert not (uploads() / f"{upload}.part").exists()
    # Nothing else is ever put.
    for other in ("/sync/v1/library", "/sync/v1/uploads", "/sync/v1/watching"):
        assert raw(share, "PUT", other, b"", headers)[0] == 501


def test_a_part_cut_off_is_kept_as_far_as_it_came(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    share.films = True
    key = paired(share)
    upload = start(share, key)[1]["upload"]
    assert share.port is not None
    with socket.create_connection((LOOPBACK, share.port), timeout=10) as conn:
        head = (f"PUT /sync/v1/uploads/{upload} HTTP/1.1\r\nHost: localhost\r\n"
                f"Authorization: Bearer {key}\r\nContent-Length: {len(FILM)}\r\n"
                f"Content-Range: bytes 0-{len(FILM) - 1}/{len(FILM)}\r\n\r\n")  # fmt: skip
        conn.sendall(head.encode() + FILM[:1000])
        conn.shutdown(socket.SHUT_WR)  # the device went out of reach
        conn.recv(4096)
    assert before_long(lambda: start(share, key)[1]["have"] == 1000)
    assert put(share, key, upload, 1000, FILM[1000:])[1] == {"have": len(FILM), "kept": True}
    assert (movies() / "A Made-up Film (2021).mkv").read_bytes() == FILM


def test_a_film_that_cant_be_kept_is_kept_for_another_try(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    share.films = True
    key = paired(share)
    upload = start(share, key)[1]["upload"]
    real = fileops.keep_media
    full = OSError(errno.ENOSPC, "No space left on device")

    def no_room(*args: Any, **kwargs: Any) -> Path:
        raise FileOperationError(f"Couldn't keep it in {movies()}: no room.") from full

    monkeypatch.setattr(fileops, "keep_media", no_room)
    with caplog.at_level(logging.WARNING):
        status, answer = put(share, key, upload, 0, FILM)
    assert (status, answer["error"], answer["have"]) == (500, "unavailable", len(FILM))
    assert "room" in answer["message"] and str(movies()) not in answer["message"]
    assert "couldn't be kept" in caplog.text
    assert (uploads() / f"{upload}.part").read_bytes() == FILM  # kept, for another try
    # Started again, it's kept now: nothing more is sent.
    monkeypatch.setattr(fileops, "keep_media", real)
    assert start(share, key)[1] == {"upload": upload, "have": 0, "already": True}
    assert (movies() / "A Made-up Film (2021).mkv").read_bytes() == FILM
    assert list(uploads().iterdir()) == []


def test_a_film_needs_room_on_the_computer(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    share.films = True
    key = paired(share)

    class Usage:
        free = sharing.ROOM_TO_SPARE + len(FILM)  # room for it once, not twice

    monkeypatch.setattr(sharing.shutil, "disk_usage", lambda path: Usage)
    status, answer = start(share, key)
    assert (status, answer["error"]) == (500, "unavailable") and "room" in answer["message"]
    Usage.free += len(FILM)
    assert start(share, key)[0] == 200


def test_what_stopped_arriving_two_days_ago_is_cleared(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    share.films = True
    key = paired(share)
    old = start(share, key, title="An Old Film")[1]["upload"]
    assert put(share, key, old, 0, FILM[:100])[0] == 200
    long_ago = time.time() - sharing.UPLOAD_KEEP_S - 60
    for ending in (".part", ".json"):
        os.utime(uploads() / f"{old}{ending}", (long_ago, long_ago))
    new = start(share, key, title="A New Film")[1]["upload"]  # sweeps as it starts
    assert sorted(p.name for p in uploads().iterdir()) == [f"{new}.json"]
    assert start(share, key, title="An Old Film")[1]["have"] == 0
    assert sharing.sweep_uploads(config.app_dirs().cache) == 0
    assert sharing.sweep_uploads(config.app_dirs().cache / "nowhere") == 0


def test_nothing_private_is_logged_or_said(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    share.films = True
    keep(movies(), "A Film.mp4", b"a film")
    with caplog.at_level(logging.DEBUG):
        theirs = paired_with(share, monkeypatch, KID)
        answers = [ask(share, "/sync/v1/hello", key=theirs),
                   ask(share, "/sync/v1/films", key=theirs),
                   ask(share, "/sync/v1/watching", key=theirs),
                   send_watching(share, theirs, WATCHING), send_watching(share, theirs, []),
                   start(share, theirs)]  # fmt: skip
        upload = answers[-1][1]["upload"]
        answers += [put(share, theirs, upload, 5, FILM), put(share, theirs, upload, 0, FILM),
                    ask(share, "/sync/v1/library", key=theirs)]  # fmt: skip
    logged = caplog.text
    assert "sharing" in logged
    said = json.dumps(answers)
    for private in (theirs, hashlib.sha256(theirs.encode()).hexdigest(), LOOPBACK, "Bearer"):
        assert private not in logged, private
        assert private not in said, private
    home = str(config.app_dirs().cache.parent)
    assert home not in said  # nothing of where anything is kept is said to a device


# ---- the writer: fileops (2026-10-16) ------------------------------------------------------


class Connection(io.RawIOBase):
    """Stands in for a device's connection: `data`, then the connection ends (or `fails`
    after so many bytes)."""

    def __init__(self, data: bytes, fails_after: int | None = None) -> None:
        self.data, self.at, self.fails_after = data, 0, fails_after

    def read(self, n: int = -1) -> bytes:  # type: ignore[override]
        if self.fails_after is not None and self.at >= self.fails_after:
            raise TimeoutError("the device went silent")
        end = len(self.data) if n < 0 else self.at + n
        if self.fails_after is not None:
            end = min(end, self.fails_after)
        chunk, self.at = self.data[self.at : end], min(end, len(self.data))
        return chunk


@pytest.fixture
def arriving(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    """The app's cache folder, and its uploads folder in it."""
    cache = tmp_path / "cache"
    folder = fileops.make_cached_folder(cache / fileops.UPLOADS_DIR, cache=cache)
    yield cache, folder


def test_a_part_is_written_where_it_belongs(arriving: tuple[Path, Path]) -> None:
    cache, folder = arriving
    part = folder / "u-1.part"
    assert fileops.write_upload_part(part, 0, Connection(FILM[:100]), 100, cache=cache) == 100
    assert fileops.write_upload_part(part, 100, Connection(FILM[100:]), len(FILM) - 100,
                                     cache=cache) == len(FILM) - 100  # fmt: skip
    assert part.read_bytes() == FILM
    if os.name != "nt":
        assert part.stat().st_mode & 0o077 == 0  # readable by this user only


def test_a_part_cut_off_counts_only_what_arrived(arriving: tuple[Path, Path]) -> None:
    """A crash, the network's or the computer's: what was counted is what's there."""
    cache, folder = arriving
    part = folder / "u-1.part"
    # The connection ends early, or goes silent: what arrived is written, and counted.
    assert fileops.write_upload_part(part, 0, Connection(FILM[:30]), 100, cache=cache) == 30
    assert fileops.write_upload_part(part, 30, Connection(FILM[30:], fails_after=20), 100,
                                     cache=cache) == 20  # fmt: skip
    assert part.read_bytes() == FILM[:50]
    # Bytes beyond what was counted (the computer stopped before the note was saved) are
    # cut off by the next part, which goes where the count says.
    assert fileops.write_upload_part(part, 40, Connection(b"X" * 5), 5, cache=cache) == 5
    assert part.read_bytes() == FILM[:40] + b"X" * 5
    # Told to stop (sharing was switched off): it stops after the chunk it's on.
    asked: list[int] = []
    big = FILM * 40  # more than one chunk
    written = fileops.write_upload_part(part, 0, Connection(big), len(big), cache=cache,
                                        going_on=lambda n: asked.append(n) or False)  # fmt: skip
    assert written == asked[0] < len(big) and part.stat().st_size == written


def test_a_part_must_follow_on_from_what_arrived(arriving: tuple[Path, Path]) -> None:
    cache, folder = arriving
    part = folder / "u-1.part"
    with pytest.raises(UserError):
        fileops.write_upload_part(part, 10, Connection(FILM), 10, cache=cache)
    assert part.stat().st_size == 0
    with pytest.raises(ValueError):
        fileops.write_upload_part(part, -1, Connection(FILM), 10, cache=cache)


def test_parts_are_written_only_in_the_caches_uploads_folder(
    arriving: tuple[Path, Path], tmp_path: Path
) -> None:
    cache, folder = arriving
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (cache / "torrents").mkdir()
    for wrong in (cache / "u-1.part", cache / "torrents" / "u-1.part", elsewhere / "u-1.part",
                  folder / "inside" / "u-1.part", folder / ".." / "u-1.part"):  # fmt: skip
        with pytest.raises(OutsideLibraryError):
            fileops.write_upload_part(wrong, 0, Connection(FILM), 10, cache=cache)
        with pytest.raises(OutsideLibraryError):
            fileops.write_upload_note(wrong, "{}", cache=cache)
    for junk in (".hidden", ".DS_Store", "._u-1.part"):
        with pytest.raises(ValueError):
            fileops.write_upload_part(folder / junk, 0, Connection(FILM), 10, cache=cache)
    assert list(elsewhere.iterdir()) == [] and not (cache / "u-1.part").exists()
    # Something in the way of a name: a folder, or a link to somewhere else.
    (folder / "u-2.part").mkdir()
    with pytest.raises(OutsideLibraryError):
        fileops.write_upload_part(folder / "u-2.part", 0, Connection(FILM), 10, cache=cache)
    target = elsewhere / "Precious.mp4"
    target.write_bytes(b"the owner's own")
    try:
        (folder / "u-3.part").symlink_to(target)
        moved = tmp_path / "moved-uploads"
        moved.mkdir()
    except OSError:
        return  # Windows without the right to make links: there's nothing to follow
    with pytest.raises(OutsideLibraryError):
        fileops.write_upload_part(folder / "u-3.part", 0, Connection(FILM), 10, cache=cache)
    with pytest.raises(OutsideLibraryError):
        fileops.write_upload_note(folder / "u-3.part", "{}", cache=cache)
    assert target.read_bytes() == b"the owner's own"
    # The uploads folder itself a link to somewhere else: nothing is written through it.
    (folder / "u-3.part").unlink()
    (folder / "u-2.part").rmdir()
    folder.rmdir()
    folder.symlink_to(moved, target_is_directory=True)
    with pytest.raises(OutsideLibraryError):
        fileops.write_upload_part(folder / "u-1.part", 0, Connection(FILM), 10, cache=cache)
    assert list(moved.iterdir()) == []


def test_a_note_is_replaced_whole(
    arriving: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    cache, folder = arriving
    note = folder / "u-1.json"
    fileops.write_upload_note(note, '{"have": 1}', cache=cache)
    fileops.write_upload_note(note, '{"have": 2}', cache=cache)
    assert json.loads(note.read_text(encoding="utf-8")) == {"have": 2}

    def fails(src: Path, dst: Path) -> None:
        raise OSError(errno.EIO, "the disk went away")

    monkeypatch.setattr(fileops, "_replace", fails)
    with pytest.raises(FileOperationError):
        fileops.write_upload_note(note, '{"have": 3}', cache=cache)
    assert json.loads(note.read_text(encoding="utf-8")) == {"have": 2}  # the old one, whole
    assert [p.name for p in folder.iterdir()] == ["u-1.json"]  # and no temp file left
