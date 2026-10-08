"""sharing (2026-10-04): the library, shared read-only with a phone player at home.

Every test's share listens on this computer's own loopback address only (conftest), and
is asked over real HTTP from here. The library is a test library of generated tones. No
address, pairing code or key is written in this file: each is made while the test runs.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from conftest import LOOPBACK
from test_rpc import Capture, code, opened, out, result, root, server  # noqa: F401  (fixtures)

from musicorg import browse, config, library, listening, rpc, scan, sharing, tags
from musicorg.index import open_index
from musicorg.library import Library

SONG = "Music/Band/Album (2020)/01 Song.m4a"
OTHER = "Music/Band/Album (2020)/02 Other.mp3"
LOOSE = "Music/Solo/Unsorted/Loose.flac"
OPUS = "Music/Band/Album (2020)/03 Hidden.opus"
VIDEO = "Music/Videos/Band/Song.mp4"
LRC = "[00:01.00]Made-up line\n[00:02.50]Another made-up line\n"
WORDS = "Made-up words\nwith a second line, and an accent: café"
JPEG = b"\xff\xd8\xff" + b"a made-up picture"
PNG = b"\x89PNG\r\n\x1a\n" + b"another made-up picture"
ALBUM_COVER, VIDEO_COVER = JPEG + b" for the album", JPEG + b" of the video"
# The library's own ids and where each file came from: none of it may reach a phone.
SONG_ID, OTHER_ID, VIDEO_ID = (str(uuid.uuid4()) for _ in range(3))
SOURCE_ID = "vid" + "A1b2C3d4"  # the shape of a YouTube id, made up


def address(*parts: int) -> str:
    """A made-up network address, put together here so that none is written down."""
    return ".".join(str(part) for part in parts)


def add(lib: Library, sample: Path, rel: str, **details: object) -> Path:
    path = lib.root.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(sample, path)
    tags.write_tags(path, tags.TrackTags(**details))  # type: ignore[arg-type]
    return path


def reindex(lib: Library) -> None:
    with open_index(lib.paths, write=True) as index:
        index.reset()
        scan.scan_library(lib, index)


@pytest.fixture
def filled(lib: Library, samples: dict[str, Path], video_mp4: Path) -> Library:
    song = add(lib, samples["m4a"], SONG, title="Song", artist="Band", album_artist="Band",
               album="Album", year=2020, track=1, disc=1, genre="Rock", explicit=True,
               musicorg_id=SONG_ID, source="youtube_music", source_id=SOURCE_ID,
               acquired="2026-01-31T09:30:00Z", cover=PNG)  # fmt: skip
    # As bytes: written as text, Windows turns each line ending into two, and the file
    # is then longer than the words the tests compare it with.
    song.with_suffix(".lrc").write_bytes(LRC.encode())
    (song.parent / "cover.jpg").write_bytes(ALBUM_COVER)
    add(lib, samples["mp3"], OTHER, title="Other", artist="Band feat. Guest", album="Album",
        musicorg_id=OTHER_ID, lyrics=WORDS, origin_path="/somewhere/rips/Other.mp3")  # fmt: skip
    add(lib, samples["flac"], LOOSE, title="Loose", artist="Solo", cover=JPEG)
    add(lib, samples["opus"], OPUS, title="Hidden", artist="Band")
    add(lib, video_mp4, VIDEO, title="Song", artist="Band", musicorg_id=VIDEO_ID,
        source="youtube_music", source_id=SOURCE_ID[::-1], cover=VIDEO_COVER)  # fmt: skip
    reindex(lib)
    listening.set_favourite(lib, SONG_ID, True)
    listening.played(lib, SONG_ID)
    listening.played(lib, SONG_ID)
    made = listening.create_playlist(lib, "Morning")[-1]["id"]
    listening.set_playlist_tracks(lib, made, [SONG_ID, OTHER_ID, SONG_ID, "gone-from-the-library"])
    return lib


@pytest.fixture
def share(filled: Library) -> Iterator[sharing.Share]:
    made = sharing.Share(filled)
    made.start()
    yield made
    made.stop()


def ask(
    share: sharing.Share,
    path: str,
    *,
    key: str | None = None,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    method: str | None = None,
) -> tuple[int, Any]:
    """One request to the share, as a phone would make it: (status, the JSON answer, or
    the bytes of a file)."""
    assert share.port is not None
    conn = http.client.HTTPConnection(LOOPBACK, share.port, timeout=10)
    sent = dict(headers or {})
    if key is not None:
        sent["Authorization"] = "Bearer " + key
    if body is not None:
        sent.setdefault("Content-Type", "application/json")
        conn.request(method or "POST", path, json.dumps(body), sent)
    else:
        conn.request(method or "GET", path, headers=sent)
    response = conn.getresponse()
    data = response.read()
    kind = response.getheader("Content-Type") or ""
    assert response.getheader("Content-Length") == str(len(data))
    assert "Python" not in (response.getheader("Server") or "")  # nothing about this computer
    conn.close()
    return response.status, json.loads(data) if kind.startswith("application/json") else data


def paired(share: sharing.Share, device: str = "iPhone") -> str:
    """Pair a device the way a phone does, and return its key."""
    shown = share.new_code()["code"]
    status, answer = ask(share, "/sync/v1/pair", body={"code": shown, "device": device})
    assert status == 200, answer
    return answer["key"]


def wrong(shown: str) -> str:
    """A six-digit code that isn't the one showing."""
    return f"{(int(shown) + 1) % 1_000_000:06d}"


def the_list(share: sharing.Share, key: str) -> dict[str, Any]:
    status, found = ask(share, "/sync/v1/library", key=key)
    assert status == 200, found
    return found


def fetch(share: sharing.Share, key: str, file: dict[str, Any]) -> bytes:
    status, data = ask(share, "/sync/v1/files/" + quote(file["id"], safe=""), key=key)
    assert status == 200, data
    return data


# ---- who may ask -------------------------------------------------------------------------


def test_only_addresses_at_home_may_ask() -> None:
    at_home = [(10, 0, 0, 5), (10, 255, 255, 254), (172, 16, 0, 1), (172, 31, 255, 254),
               (192, 168, 1, 20), (169, 254, 10, 10), (127, 0, 0, 1)]  # fmt: skip
    elsewhere = [(8, 8, 8, 8), (172, 15, 0, 1), (172, 32, 0, 1), (192, 169, 1, 1),
                 (169, 253, 1, 1), (100, 64, 0, 1), (11, 0, 0, 1), (203, 0, 113, 9),
                 (0, 0, 0, 0), (255, 255, 255, 255)]  # fmt: skip
    for parts in at_home:
        assert sharing.at_home(address(*parts)), parts
    for parts in elsewhere:
        assert not sharing.at_home(address(*parts)), parts
    for text in ("", "localhost", "::1", "fe80::1", "not an address", address(10, 0, 0)):
        assert not sharing.at_home(text), text


def test_a_request_must_be_sent_to_this_computer_at_home() -> None:
    home = address(192, 168, 1, 20)
    for host in (home, f"{home}:8000", "localhost", "localhost:1", "A-Computer.local:8000",
                 "a-computer.local."):  # fmt: skip
        assert sharing.host_at_home(host), host
    for host in (None, "", "example.com", "example.com:80", address(8, 8, 8, 8) + ":80",
                 "[::1]:80", "local", "notlocal.example"):  # fmt: skip
        assert not sharing.host_at_home(host), host


def test_callers_from_elsewhere_are_dropped_unanswered(
    share: sharing.Share, caplog: pytest.LogCaptureFixture
) -> None:
    listener = share._http
    assert listener is not None
    outside = address(203, 0, 113, 9)
    with caplog.at_level(logging.DEBUG, logger="musicorg"):
        assert listener.verify_request(None, (address(192, 168, 1, 20), 50000))
        assert not listener.verify_request(None, (outside, 50000))
        assert not listener.verify_request(None, (outside, 50001))
    said = [r.getMessage() for r in caplog.records if "turned away" in r.getMessage()]
    assert len(said) == 1  # once for a run of them
    assert outside not in caplog.text  # and never the caller's address


def test_a_web_page_cant_use_it(share: sharing.Share) -> None:
    for headers in ({"Host": "example.com"}, {"Origin": "http://example.com"}):
        status, answer = ask(share, "/sync/v1/hello", headers=headers)
        assert (status, answer["error"]) == (403, "not_home")
    # A pairing request must be JSON, which a web page can't send without asking first.
    shown = share.new_code()["code"]
    status, answer = ask(share, "/sync/v1/pair", body={"code": shown, "device": "iPhone"},
                         headers={"Content-Type": "text/plain"})  # fmt: skip
    assert (status, answer["error"]) == (403, "wrong_code")
    assert sharing.devices() == []


def test_it_listens_on_this_computer_only_in_tests(share: sharing.Share) -> None:
    listener = share._http
    assert listener is not None and listener.server_address[0] == LOOPBACK


# ---- hello and pairing -------------------------------------------------------------------


def test_hello_needs_no_key(share: sharing.Share, filled: Library) -> None:
    status, hello = ask(share, "/sync/v1/hello")
    assert status == 200
    assert hello == {"format": 1, "library": share.info, "paired": False}
    assert hello["library"]["name"] == filled.root.name
    key = paired(share)
    assert ask(share, "/sync/v1/hello", key=key)[1]["paired"] is True
    assert ask(share, "/sync/v1/hello", key=key + "x")[1]["paired"] is False


def test_a_librarys_id_never_changes(filled: Library, tmp_path: Path) -> None:
    first = sharing.library_info(filled)
    assert first["id"].startswith("lib-") and len(first["id"]) == 28
    assert first["name"] == filled.root.name
    # The folder is renamed or moved: the same library, so the same id.
    filled.close()
    moved = tmp_path / "Moved Library"
    filled.root.rename(moved)
    with library.open(moved, write=False) as again:
        assert sharing.library_info(again) == {"id": first["id"], "name": "Moved Library"}


def test_everything_else_needs_a_key(share: sharing.Share) -> None:
    for path in ("/sync/v1/library", "/sync/v1/files/anything", "/sync/v1/whatever"):
        status, answer = ask(share, path)
        assert (status, answer["error"]) == (401, "not_paired"), path
        assert answer["message"]
        status, answer = ask(share, path, key="not-a-key-" + "x" * 20)
        assert (status, answer["error"]) == (401, "not_paired"), path
    assert ask(share, "/somewhere/else")[0] == 404
    assert ask(share, "/sync/v1/library", body={"code": "x"})[0] == 404  # only pair is posted
    status, answer = ask(share, "/sync/v1/library", method="DELETE")
    assert status == 501 and answer["error"] == "bad_request"  # nothing can be changed


def test_pairing_with_the_code_on_the_screen(share: sharing.Share) -> None:
    # No code is showing until the owner asks for one.
    status, answer = ask(share, "/sync/v1/pair", body={"code": "0" * 6, "device": "iPhone"})
    assert (status, answer["error"]) == (403, "wrong_code")
    assert share.status()["pairing"] is False

    shown = share.new_code()
    assert len(shown["code"]) == 6 and shown["code"].isdigit() and shown["seconds"] == 300
    assert share.status()["pairing"] is True
    status, answer = ask(share, "/sync/v1/pair", body={"code": wrong(shown["code"])})
    assert (status, answer["error"]) == (403, "wrong_code")

    status, answer = ask(share, "/sync/v1/pair", body={"code": shown["code"], "device": "iPad"})
    assert status == 200 and len(answer["key"]) >= 40
    assert ask(share, "/sync/v1/hello", key=answer["key"])[1]["paired"] is True
    assert [d["device"] for d in sharing.devices()] == ["iPad"]

    # A code works once.
    assert share.status()["pairing"] is False
    status, answer = ask(share, "/sync/v1/pair", body={"code": shown["code"], "device": "iPad"})
    assert (status, answer["error"]) == (403, "wrong_code")
    assert len(sharing.devices()) == 1


def test_a_code_runs_out_and_can_be_taken_back(filled: Library) -> None:
    now = [1000.0]
    share = sharing.Share(filled, clock=lambda: now[0])
    share.start()
    try:
        shown = share.new_code()["code"]
        now[0] += sharing.CODE_LIFE_S + 1
        assert share.pair(shown, "iPhone")[0] == 403
        assert share.status()["pairing"] is False

        second = share.new_code()["code"]
        share.stop_pairing()  # the owner closed the code's window
        assert share.pair(second, "iPhone")[0] == 403

        third = share.new_code()["code"]
        now[0] += sharing.CODE_LIFE_S - 1
        assert share.status()["pairing_seconds_left"] == 1
        assert share.pair(third, "iPhone")[0] == 200
    finally:
        share.stop()


def test_wrong_codes_lock_pairing_for_a_while(filled: Library) -> None:
    now = [1000.0]
    share = sharing.Share(filled, clock=lambda: now[0])
    share.start()
    try:
        shown = share.new_code()["code"]
        for _ in range(sharing.WRONG_TRIES):
            assert share.pair(wrong(shown), "iPhone")[0] == 403
        status, answer = share.pair(shown, "iPhone")  # even the right one, for now
        assert (status, answer["error"]) == (429, "too_many_tries")
        assert sharing.devices() == []
        now[0] += sharing.LOCK_S + 1
        assert share.pair(shown, "iPhone")[0] == 200
    finally:
        share.stop()


def test_a_device_says_only_what_kind_it_is(share: sharing.Share) -> None:
    paired(share, "iPad")
    paired(share, "Somebody's very own <b>phone</b> with a long name")
    assert [d["device"] for d in sharing.devices()] == ["iPad", "Device"]


def test_a_code_needs_sharing_to_be_on(filled: Library) -> None:
    share = sharing.Share(filled)
    with pytest.raises(sharing.UserError):
        share.new_code()


# ---- the paired devices ------------------------------------------------------------------


def test_keys_are_kept_as_hashes_readable_by_this_user_only(share: sharing.Share) -> None:
    key = paired(share)
    path = config.devices_path()
    saved = path.read_text(encoding="utf-8")
    assert key not in saved
    assert hashlib.sha256(key.encode()).hexdigest() in saved
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0
    # Nothing the app is given has the key, or its hash, in it.
    for given in (sharing.devices(), share.status(), sharing.status_off(share.lib)):
        text = json.dumps(given)
        assert key not in text and hashlib.sha256(key.encode()).hexdigest() not in text
        assert sharing.KEY_FIELD not in text
    assert set(sharing.devices()[0]) == {"id", "device", "paired_at", "last_synced"}


def test_forgetting_a_device_stops_its_key(share: sharing.Share) -> None:
    first, second = paired(share), paired(share, "iPad")
    gone = next(d["id"] for d in sharing.devices() if d["device"] == "iPhone")
    sharing.forget(gone)
    assert ask(share, "/sync/v1/library", key=first)[0] == 401
    assert ask(share, "/sync/v1/hello", key=first)[1]["paired"] is False
    assert ask(share, "/sync/v1/library", key=second)[0] == 200
    with pytest.raises(sharing.NotFoundError):
        sharing.forget(gone)


def test_a_device_is_paired_with_one_profile(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = paired(share)
    monkeypatch.setenv(config.PROFILE_ENV, "someone-else")
    assert sharing.devices() == []
    assert ask(share, "/sync/v1/library", key=key)[0] == 401
    monkeypatch.delenv(config.PROFILE_ENV)
    assert ask(share, "/sync/v1/library", key=key)[0] == 200


def test_a_sync_is_noted_for_the_device(share: sharing.Share) -> None:
    told = []
    share._changed = lambda: told.append(1)
    key = paired(share)
    assert sharing.devices()[0]["last_synced"] is None and len(told) == 1
    the_list(share, key)
    assert sharing.devices()[0]["last_synced"] is not None and len(told) == 2


# ---- the list ----------------------------------------------------------------------------


def test_the_list_carries_what_a_player_needs(share: sharing.Share, filled: Library) -> None:
    found = the_list(share, paired(share))
    assert found["format"] == 1 and found["library"] == share.info and found["revision"]
    tracks = {t["title"]: t for t in found["tracks"]}
    assert set(tracks) == {"Song", "Other", "Loose"}  # an Opus file has no name in format 1

    song = tracks["Song"]
    assert song["duration"] == pytest.approx(3, abs=0.5)
    audio = filled.root.joinpath(*SONG.split("/"))
    assert {k: v for k, v in song.items() if k not in ("id", "duration", "audio", "cover",
                                                       "lyrics")} == {
        "title": "Song", "artist": "Band", "album": "Album", "albumArtist": "Band",
        "trackNumber": 1, "discNumber": 1, "year": 2020, "genre": "Rock", "explicit": True,
        "favourite": True, "playCount": 2, "added": "2026-01-31T09:30:00Z",
    }  # fmt: skip
    assert (song["audio"]["size"], song["audio"]["type"]) == (audio.stat().st_size, "m4a")
    # The album folder's cover.jpg comes before the picture inside the song.
    assert (song["cover"]["size"], song["cover"]["type"]) == (len(ALBUM_COVER), "jpg")
    assert (song["lyrics"]["size"], song["lyrics"]["type"]) == (len(LRC.encode()), "lrc")

    other = tracks["Other"]
    assert (other["artist"], other["explicit"], other["favourite"], other["playCount"]) == (
        "Band feat. Guest", False, False, 0,
    )  # fmt: skip
    assert "year" not in other and "added" not in other and "albumArtist" not in other
    assert other["audio"]["type"] == "mp3"
    assert other["cover"] == song["cover"]  # one file for the whole album
    assert (other["lyrics"]["size"], other["lyrics"]["type"]) == (len(WORDS.encode()), "txt")

    loose = tracks["Loose"]
    assert loose["audio"]["type"] == "flac" and "album" not in loose and "lyrics" not in loose
    assert (loose["cover"]["size"], loose["cover"]["type"]) == (len(JPEG), "jpg")  # its own

    (video,) = found["videos"]
    assert (video["title"], video["artist"], video["track"]) == ("Song", "Band", song["id"])
    assert video["duration"] > 19 and video["video"]["type"] == "mp4"
    assert video["cover"]["size"] == len(VIDEO_COVER)

    (playlist,) = found["playlists"]
    assert playlist["name"] == "Morning"
    assert playlist["tracks"] == [song["id"], other["id"], song["id"]]

    every = [f["id"] for t in found["tracks"] for f in (t["audio"], t.get("cover"),
                                                        t.get("lyrics")) if f]  # fmt: skip
    assert len({t["id"] for t in found["tracks"]}) == 3 and len(set(every)) == len(every) - 1


def test_nothing_in_the_list_says_where_a_song_came_from(
    share: sharing.Share, filled: Library
) -> None:
    text = json.dumps(the_list(share, paired(share)))
    for private in (SONG_ID, OTHER_ID, VIDEO_ID, SOURCE_ID, SOURCE_ID[::-1], "MUSICORG",
                    "musicorg", "youtube", "rips", "Music/", "Album (2020)", "01 Song",
                    ".m4a", str(filled.root), str(filled.root.parent)):  # fmt: skip
        assert private not in text, private


def test_every_file_arrives_as_the_list_said(share: sharing.Share, filled: Library) -> None:
    key = paired(share)
    found = the_list(share, key)
    tracks = {t["title"]: t for t in found["tracks"]}
    files = [f for t in found["tracks"] for f in (t["audio"], t.get("cover"), t.get("lyrics"))]
    files += [f for v in found["videos"] for f in (v["video"], v.get("cover"))]
    for file in filter(None, files):
        assert len(fetch(share, key, file)) == file["size"], file

    def on_disk(rel: str) -> bytes:
        return filled.root.joinpath(*rel.split("/")).read_bytes()

    assert fetch(share, key, tracks["Song"]["audio"]) == on_disk(SONG)
    assert fetch(share, key, tracks["Song"]["cover"]) == ALBUM_COVER
    assert fetch(share, key, tracks["Song"]["lyrics"]) == LRC.encode()
    assert fetch(share, key, tracks["Other"]["lyrics"]) == WORDS.encode()
    assert fetch(share, key, tracks["Loose"]["cover"]) == JPEG
    assert fetch(share, key, found["videos"][0]["video"]) == on_disk(VIDEO)
    assert fetch(share, key, found["videos"][0]["cover"]) == VIDEO_COVER


def test_only_files_the_list_names_are_given_out(share: sharing.Share, filled: Library) -> None:
    key = paired(share)
    the_list(share, key)
    for name in ("nothing", "../" + SONG, SONG, str(filled.paths.state_file),
                 "../../state.json", ""):  # fmt: skip
        status, answer = ask(share, "/sync/v1/files/" + quote(name, safe=""), key=key)
        assert (status, answer["error"]) == (404, "not_found"), name


def test_ids_stay_the_same_and_survive_a_rename(share: sharing.Share, filled: Library) -> None:
    key = paired(share)
    first = the_list(share, key)
    assert the_list(share, key) == first  # nothing changed: the same list, the same revision

    # The song is filed somewhere else (a tidy, or its details were corrected).
    old = filled.root.joinpath(*SONG.split("/"))
    new = filled.root / "Music" / "Band" / "Another Album" / "07 Song.m4a"
    new.parent.mkdir(parents=True)
    old.rename(new)
    old.with_suffix(".lrc").rename(new.with_suffix(".lrc"))
    reindex(filled)
    second = the_list(share, key)
    before = next(t for t in first["tracks"] if t["title"] == "Song")
    after = next(t for t in second["tracks"] if t["title"] == "Song")
    assert after["id"] == before["id"]
    assert after["audio"] == before["audio"] and after["lyrics"] == before["lyrics"]
    assert second["playlists"] == first["playlists"]
    assert second["videos"][0]["track"] == before["id"]


def test_a_files_version_changes_when_its_bytes_do(share: sharing.Share, filled: Library) -> None:
    key = paired(share)
    before = {t["title"]: t for t in the_list(share, key)["tracks"]}
    path = filled.root.joinpath(*OTHER.split("/"))
    tags.write_tags(path, tags.TrackTags(lyrics=WORDS + "\nand a third line", genre="Pop"))
    about = path.stat()
    os.utime(path, ns=(about.st_atime_ns, about.st_mtime_ns + 10**9))
    after = {t["title"]: t for t in the_list(share, key)["tracks"]}
    assert after["Other"]["id"] == before["Other"]["id"]
    assert after["Other"]["genre"] == "Pop"
    for part in ("audio", "lyrics"):
        assert after["Other"][part]["id"] == before["Other"][part]["id"]
        assert after["Other"][part]["version"] != before["Other"][part]["version"]
    assert after["Song"] == before["Song"]


def test_a_file_changed_since_the_list_isnt_sent_as_the_old_one(
    share: sharing.Share, filled: Library
) -> None:
    key = paired(share)
    tracks = {t["title"]: t for t in the_list(share, key)["tracks"]}
    lrc = filled.root.joinpath(*SONG.split("/")).with_suffix(".lrc")
    lrc.write_text(LRC + "[00:04.00]A line added later\n", encoding="utf-8")
    other = filled.root.joinpath(*OTHER.split("/"))
    tags.write_tags(other, tags.TrackTags(lyrics="Different words\n"))
    filled.root.joinpath(*LOOSE.split("/")).unlink()
    for file in (tracks["Song"]["lyrics"], tracks["Other"]["lyrics"], tracks["Other"]["audio"],
                 tracks["Loose"]["audio"], tracks["Loose"]["cover"]):  # fmt: skip
        status, answer = ask(share, "/sync/v1/files/" + file["id"], key=key)
        assert (status, answer["error"]) == (404, "not_found"), file


def test_a_list_from_before_the_engine_restarted_still_works(
    filled: Library, share: sharing.Share
) -> None:
    key = paired(share)
    found = the_list(share, key)
    share.stop()
    again = sharing.Share(filled)  # the app was closed and opened again
    again.start()
    try:
        song = next(t for t in found["tracks"] if t["title"] == "Song")
        assert len(fetch(again, key, song["audio"])) == song["audio"]["size"]
    finally:
        again.stop()


def test_details_come_from_the_index_when_the_app_has_read_them(
    share: sharing.Share, filled: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = paired(share)
    with open_index(filled.paths, write=True) as index:
        browse.tracks(filled, index)  # what the app does when it opens
    monkeypatch.setattr(tags, "read_tags", lambda path: pytest.fail("opened a song for the list"))
    found = {t["title"]: t for t in the_list(share, key)["tracks"]}
    assert found["Other"]["lyrics"]["size"] == len(WORDS.encode())
    assert found["Loose"]["cover"]["size"] == len(JPEG)


def test_without_them_each_file_is_read_once(
    share: sharing.Share, filled: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = paired(share)
    first = the_list(share, key)  # the index has no details yet: every file's tags are read
    monkeypatch.setattr(tags, "read_tags", lambda path: pytest.fail("read a song a second time"))
    assert the_list(share, key) == first


# ---- read-only, and off means off --------------------------------------------------------


def snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    """Every file in the library with its size and when it was last written. SQLite's
    own working files beside the index are left out: reading the index keeps those."""
    found = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file() and not path.name.endswith(("-wal", "-shm")):
            about = path.stat()
            found[path.relative_to(folder).as_posix()] = (about.st_size, about.st_mtime_ns)
    return found


def test_a_whole_sync_writes_nothing_in_the_library(share: sharing.Share, filled: Library) -> None:
    before = snapshot(filled.root)
    key = paired(share)
    found = the_list(share, key)
    for track in found["tracks"]:
        for part in ("audio", "cover", "lyrics"):
            if part in track:
                fetch(share, key, track[part])
    for video in found["videos"]:
        fetch(share, key, video["video"])
        fetch(share, key, video["cover"])
    assert snapshot(filled.root) == before
    assert not list(filled.paths.journal.glob("*"))  # and nothing was journaled: nothing changed


def test_off_means_nothing_is_listening(share: sharing.Share) -> None:
    key = paired(share)
    port = share.port
    assert port is not None and share.status()["on"] is True
    share.new_code()
    share.stop()
    status = share.status()
    assert (status["on"], status["port"], status["address"], status["pairing"]) == (
        False, None, None, False,
    )  # fmt: skip
    conn = http.client.HTTPConnection(LOOPBACK, port, timeout=5)
    with pytest.raises(OSError):
        conn.request("GET", "/sync/v1/hello", headers={"Authorization": "Bearer " + key})
        conn.getresponse()
    share.stop()  # twice is fine


def test_nothing_private_is_logged(share: sharing.Share, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        shown = share.new_code()["code"]
        ask(share, "/sync/v1/pair", body={"code": wrong(shown), "device": "iPhone"})
        status, answer = ask(share, "/sync/v1/pair", body={"code": shown, "device": "iPhone"})
        key = answer["key"]
        found = the_list(share, key)
        fetch(share, key, found["tracks"][0]["audio"])
        ask(share, "/sync/v1/files/nothing", key=key)
        ask(share, "/sync/v1/library", key="not-the-key-" + "y" * 20)
        sharing.forget(sharing.devices()[0]["id"])
    logged = caplog.text
    assert "sharing" in logged  # something was said
    for private in (shown, wrong(shown), key, hashlib.sha256(key.encode()).hexdigest(),
                    LOOPBACK, "Bearer", "not-the-key"):  # fmt: skip
        assert private not in logged, private


def test_the_computers_own_address_is_one_at_home(monkeypatch: pytest.MonkeyPatch) -> None:
    class Probe:
        answer = address(192, 168, 1, 20)
        sent: list[bytes] = []

        def __init__(self, *args: Any) -> None:
            pass

        def __enter__(self) -> Probe:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def connect(self, where: tuple[str, int]) -> None:
            if self.answer is None:
                raise OSError("no network")

        def getsockname(self) -> tuple[str, int]:
            return (self.answer, 50000)

        def send(self, data: bytes) -> None:  # never called: nothing is sent
            self.sent.append(data)

    monkeypatch.setattr(sharing.socket, "socket", Probe)
    assert sharing.own_address() == address(192, 168, 1, 20)
    Probe.answer = address(203, 0, 113, 9)  # not a home network: nothing to show
    assert sharing.own_address() is None
    Probe.answer = LOOPBACK
    assert sharing.own_address() is None
    Probe.answer = None  # type: ignore[assignment]
    assert sharing.own_address() is None
    assert Probe.sent == []


# ---- over RPC ----------------------------------------------------------------------------


def test_sharing_is_off_until_its_switched_on(
    opened: rpc.Server,  # noqa: F811
    out: Capture,  # noqa: F811
) -> None:
    status = result(opened, "sharing.status")
    assert status == {"on": False, "port": None, "address": None, "name": "Library",
                      "service": "_homemusicsync._tcp", "devices": [], "pairing": False,
                      "pairing_seconds_left": 0, "films": False}  # fmt: skip
    assert code(opened, "sharing.pair") == rpc.USER_ERROR  # no code while it's off
    assert code(opened, "sharing.set") == rpc.INVALID_PARAMS
    assert code(opened, "sharing.forget", device_id="d_nobody") == rpc.NOT_FOUND

    status = result(opened, "sharing.set", on=True)
    assert status["on"] is True and isinstance(status["port"], int)
    assert result(opened, "sharing.set", on=True)["port"] == status["port"]  # already on
    share = opened._share
    assert share is not None and share._http is not None
    assert share._http.server_address[0] == LOOPBACK

    shown = result(opened, "sharing.pair")
    assert set(shown) == {"code", "seconds"}
    assert result(opened, "sharing.status")["pairing"] is True
    assert result(opened, "sharing.stop_pairing")["pairing"] is False
    shown = result(opened, "sharing.pair")
    status, answer = ask(share, "/sync/v1/pair", body={"code": shown["code"], "device": "iPhone"})
    assert status == 200
    assert out.notes("sharing.changed") == [{}]
    (device,) = result(opened, "sharing.status")["devices"]
    assert device["device"] == "iPhone"
    # The key is given to the phone and to nobody else: nothing sent to the app has it.
    assert answer["key"] not in out.data.decode("utf-8")

    assert the_list(share, answer["key"])["tracks"] == []
    assert len(out.notes("sharing.changed")) == 2
    assert result(opened, "sharing.forget", device_id=device["id"])["devices"] == []
    assert ask(share, "/sync/v1/library", key=answer["key"])[0] == 401

    port = share.port
    assert result(opened, "sharing.set", on=False)["on"] is False
    assert opened._share is None and share.port is None
    conn = http.client.HTTPConnection(LOOPBACK, port, timeout=5)
    with pytest.raises(OSError):
        conn.request("GET", "/sync/v1/hello")
        conn.getresponse()


def test_sharing_needs_a_library(server: rpc.Server) -> None:  # noqa: F811
    result(server, "engine.hello", client="pytest", client_version="1")
    assert code(server, "sharing.status") == rpc.USER_ERROR
    assert code(server, "sharing.set", on=True) == rpc.USER_ERROR


def test_sharing_stops_with_the_engine(opened: rpc.Server) -> None:  # noqa: F811
    result(opened, "sharing.set", on=True)
    share = opened._share
    assert share is not None and share.listening
    opened.shutdown()
    assert not share.listening and opened._share is None


def test_an_engine_nobody_asked_never_listens(opened: rpc.Server) -> None:  # noqa: F811
    assert opened._share is None
    assert "sharing.set" in result(opened, "engine.hello", client="pytest")["capabilities"]
    assert opened._share is None


# ---- kept films and videos (2026-10-08) ----------------------------------------------------


def keep(folder: Path, name: str, data: bytes = b"a made-up film") -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_kept_films_are_not_shared_until_switched_on(share: sharing.Share, filled: Library) -> None:
    keep(config.media_folders()["movies"], "A Film.mp4")
    key = paired(share)
    assert the_list(share, key)["movies"] == []
    before = the_list(share, key)["revision"]

    share.films = True
    found = the_list(share, key)
    assert [m["title"] for m in found["movies"]] == ["A Film"]
    assert found["revision"] != before
    assert found["tracks"] and found["videos"]  # the library is there as before

    share.films = False
    assert the_list(share, key)["movies"] == []
    # What the list no longer names isn't given out, even by the id it had.
    share._built_at = None
    status, _ = ask(share, "/sync/v1/files/" + quote(found["movies"][0]["video"]["id"], safe=""),
                    key=key)  # fmt: skip
    assert status == 404


def test_only_films_a_phone_plays_are_listed(filled: Library) -> None:
    folders = config.media_folders()
    film = keep(folders["movies"], "A Film.mp4", b"film bytes")
    keep(folders["movies"], "Season 1/Episode 1.m4v")
    keep(folders["movies"], "Old Film.mkv")  # a phone can't play it
    keep(folders["movies"], ".hidden.mp4")
    keep(folders["movies"], "Empty.mp4", b"")
    keep(folders["movies"], "Project.fcpbundle/Clip.mov")  # another program's own
    keep(folders["movies"], ".cache/Clip.mp4")
    clip = keep(folders["media"], "A Clip.MOV", b"clip bytes")
    outside = keep(filled.root.parent / "elsewhere", "Private.mp4")
    try:
        (folders["movies"] / "Link.mp4").symlink_to(outside)
        (folders["movies"] / "Linked").symlink_to(outside.parent, target_is_directory=True)
    except OSError:
        pass  # Windows without the right to make links: there's nothing to follow

    share = sharing.Share(filled, films=True)
    share.start()
    try:
        key = paired(share)
        found = the_list(share, key)
        movies = {m["title"]: m for m in found["movies"]}
        assert set(movies) == {"A Film", "Episode 1", "A Clip"}
        assert (movies["A Film"]["kind"], movies["A Film"]["video"]["type"]) == ("movie", "mp4")
        assert movies["Episode 1"]["video"]["type"] == "m4v"
        assert (movies["A Clip"]["kind"], movies["A Clip"]["video"]["type"]) == ("video", "mov")
        assert movies["A Film"]["video"]["size"] == film.stat().st_size
        assert fetch(share, key, movies["A Film"]["video"]) == b"film bytes"
        assert fetch(share, key, movies["A Clip"]["video"]) == clip.read_bytes()
        # Nothing says where a film is kept: ids are names only.
        sent = json.dumps(found["movies"])
        assert "Movies" not in sent and "Downloads" not in sent and "Season" not in sent

        # A film written again is a new version, and the old one's bytes aren't sent.
        old = movies["A Film"]["video"]
        film.write_bytes(b"a longer film now")
        share._built_at = None
        status, _ = ask(share, "/sync/v1/files/" + quote(old["id"], safe=""), key=key)
        assert status == 404
        again = {m["title"]: m for m in the_list(share, key)["movies"]}["A Film"]
        assert again["id"] == movies["A Film"]["id"]
        assert again["video"]["version"] != old["version"]
    finally:
        share.stop()


def test_no_kept_folders_is_an_empty_list(filled: Library) -> None:
    assert sharing.kept_media(config.media_folders()) == []


def test_the_app_switches_kept_films(opened: rpc.Server) -> None:  # noqa: F811
    assert result(opened, "sharing.status")["films"] is False
    assert code(opened, "sharing.set", on=True, films="yes") == rpc.INVALID_PARAMS
    assert opened._share is None
    assert result(opened, "sharing.set", on=True)["films"] is False
    assert result(opened, "sharing.set", on=True, films=True)["films"] is True
    assert result(opened, "sharing.set", on=True)["films"] is True  # unsaid: left as it is
    assert result(opened, "sharing.set", on=True, films=False)["films"] is False
    assert result(opened, "sharing.set", on=False)["films"] is False
    assert result(opened, "sharing.set", on=True, films=True)["films"] is True
    result(opened, "sharing.set", on=False)


def test_videos_kept_inside_the_movies_folder_are_listed_once(filled: Library) -> None:
    folders = config.media_folders()
    assert folders["media"].parent == folders["movies"]  # Videos, in Movies: the usual places
    keep(folders["movies"], "A Film.mp4")
    keep(folders["media"], "A Clip.mp4")
    keep(folders["media"], "Channel/Another Clip.mp4")
    found = {entry["title"]: entry["kind"] for entry, _ in sharing.kept_media(folders)}
    assert found == {"A Film": "movie", "A Clip": "video", "Another Clip": "video"}
    # One folder chosen for both: everything in it is a movie, once.
    same = sharing.kept_media({"movies": folders["media"], "media": folders["media"]})
    assert sorted(entry["kind"] for entry, _ in same) == ["movie", "movie"]
