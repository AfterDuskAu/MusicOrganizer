"""sharing (2026-10-04): the library, shared with a phone player at home; and, from
2026-10-10, the three things a phone may tell the computer were done on it.

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

from musicorg import browse, config, library, listening, naming, rpc, scan, sharing, tags
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
    assert hello == {"format": 1, "library": share.info, "paired": False, "accepts": ["changes"]}
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
    # Only a pairing code and a device's changes are posted, and nothing is ever deleted.
    assert ask(share, "/sync/v1/library", body={"code": "x"})[0] == 404
    for method in ("DELETE", "PUT", "PATCH"):
        for path in ("/sync/v1/library", "/sync/v1/changes", "/sync/v1/files/anything"):
            status, answer = ask(share, path, method=method)
            assert status == 501 and answer["error"] == "bad_request", (method, path)


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
        "favourite": True, "playCount": 2, "listened": 0.0, "added": "2026-01-31T09:30:00Z",
        "discovered": True,  # a download: nothing of the owner's is behind it
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
    assert "discovered" not in other  # the owner's own file, copied in
    assert other["audio"]["type"] == "mp3"
    assert other["cover"] == song["cover"]  # one file for the whole album
    assert (other["lyrics"]["size"], other["lyrics"]["type"]) == (len(WORDS.encode()), "txt")

    loose = tracks["Loose"]
    assert loose["audio"]["type"] == "flac" and "album" not in loose and "lyrics" not in loose
    assert "listened" not in loose  # it has no id of its own to keep its time by
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


def test_a_download_is_discovered_until_the_owner_moves_it_into_their_library(
    share: sharing.Share, filled: Library, samples: dict[str, Path]
) -> None:
    """`discovered` marks what the computer found for its owner, apart from what they
    brought themselves: what the app lists under Discover → Downloads."""
    album = "Music/Band/Album (2020)/"
    # The owner's own, whatever YouTube Music had to do with them: a rip replaced by its
    # official download, a rip kept with official details, a copy not identified yet.
    add(filled, samples["m4a"], album + "04 Replaced.m4a", title="Replaced", artist="Band",
        musicorg_id=str(uuid.uuid4()), source="youtube_music", source_id=SOURCE_ID.upper(),
        match="auto_exact", origin_path="/somewhere/rips/Replaced.mp3")  # fmt: skip
    add(filled, samples["mp3"], album + "05 Kept.mp3", title="Kept", artist="Band",
        musicorg_id=str(uuid.uuid4()), source="rip_copy", source_id=SOURCE_ID.lower(),
        match="user_details", origin_path="/somewhere/rips/Kept.mp3")  # fmt: skip
    add(filled, samples["mp3"], album + "06 Unsure.mp3", title="Unsure", artist="Band",
        musicorg_id=str(uuid.uuid4()), source="rip_copy", match="unconfirmed",
        origin_path="/somewhere/rips/Unsure.mp3")  # fmt: skip
    reindex(filled)
    key = paired(share)

    first = the_list(share, key)
    assert len(first["tracks"]) == 6
    assert {t["title"]: t["discovered"] for t in first["tracks"] if "discovered" in t} == {
        "Song": True
    }  # left out everywhere else, never false
    assert not any("discovered" in v for v in first["videos"])  # a song's, not a video's

    # Moved into the main library, it's one of the owner's songs. No file changed.
    listening.move(filled, [SONG_ID], to_library=True)
    moved = the_list(share, key)
    assert not any("discovered" in t for t in moved["tracks"])
    assert moved["revision"] != first["revision"]  # so a phone takes the new list

    listening.move(filled, [SONG_ID], to_library=False)  # and back under Downloads
    assert the_list(share, key) == first


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


def kept(kind: str, title: str, **about: Any) -> Path:
    """A made-up film or episode, where the engine keeps one of its kind."""
    inside, name = naming.kept_place(kind, title, **about)
    return keep(config.media_folders()["movies"].joinpath(*inside), name + ".mp4")


def test_series_and_anime_are_told_apart_by_their_folders(filled: Library) -> None:
    folders = config.media_folders()
    movies = folders["movies"]
    kept("movie", "A Film", year="1921")
    episode = kept("series", "Pilot", show="A Show", season=1, episode=2)
    episode.write_bytes(b"the second episode")
    kept("series", "", show="A Show", season=1, episode=1)
    kept("series", "Recap", show="A Show", season=0, episode=1)
    kept("anime", "Late", show="An Anime", episode=5)
    kept("anime", "An Anime Film", year="2001")
    keep(movies, "A Show S01E03.mp4")  # kept before there were folders: nothing is guessed
    keep(movies / "series" / "the.owners.own", "the.owners.own.s02e07.web.m4v")
    keep(folders["media"], "Series/A Clip.mp4")  # in the videos folder: a video, wherever

    share = sharing.Share(filled, films=True)
    share.start()
    try:
        key = paired(share)
        found = the_list(share, key)["movies"]
        always = ("id", "title", "added", "video")
        said = {m["title"]: {k: v for k, v in m.items() if k not in always} for m in found}
        assert said == {
            "A Film (1921)": {"kind": "movie"},
            "Pilot": {"kind": "series", "show": "A Show", "season": 1, "episode": 2},
            "A Show S01E01": {"kind": "series", "show": "A Show", "season": 1, "episode": 1},
            "Recap": {"kind": "series", "show": "A Show", "season": 0, "episode": 1},
            # What isn't known isn't said: no season here, and no show for a film.
            "Late": {"kind": "anime", "show": "An Anime", "episode": 5},
            "An Anime Film (2001)": {"kind": "anime"},
            "A Show S01E03": {"kind": "movie"},
            "the.owners.own.s02e07.web": {"kind": "series", "show": "the.owners.own",
                                          "season": 2, "episode": 7},
            "A Clip": {"kind": "video"},
        }  # fmt: skip
        assert all(set(m) >= {"id", "title", "kind", "added", "video"} for m in found)
        pilot = next(m for m in found if m["title"] == "Pilot")
        assert fetch(share, key, pilot["video"]) == b"the second episode"
        # Of where a file is kept, only its show's name is sent.
        sent = json.dumps(found)
        assert "Season" not in sent and "Specials" not in sent and "Movies" not in sent
        assert "/" not in sent and "\\" not in sent
    finally:
        share.stop()


def test_the_guards_hold_inside_a_shows_folder(filled: Library) -> None:
    folders = config.media_folders()
    show = kept("anime", "Real", show="A Show", season=1, episode=1).parent
    keep(show, "A Show S01E02 - Not Playable.mkv")
    keep(show, ".A Show S01E03 - Hidden.mp4")
    keep(show, "A Show S01E04 - Empty.mp4", b"")
    keep(show, "Project.fcpbundle/A Show S01E05.mov")  # another program's own
    outside = keep(filled.root.parent / "elsewhere", "A Show S01E06 - Private.mp4")
    try:
        (show / "A Show S01E06 - Private.mp4").symlink_to(outside)
        (folders["movies"] / "Series").symlink_to(outside.parent, target_is_directory=True)
        (folders["movies"] / "Anime" / "Linked").symlink_to(
            outside.parent, target_is_directory=True
        )
    except OSError:
        pass  # Windows without the right to make links: there's nothing to follow

    def everything() -> list[tuple[str, int, int]]:
        return sorted(
            (p.as_posix(), p.lstat().st_size, p.lstat().st_mtime_ns)
            for p in folders["movies"].rglob("*")
        )

    before = everything()
    found = sharing.kept_media(folders)
    assert [(entry["title"], entry["kind"], entry["show"]) for entry, _ in found] == [
        ("Real", "anime", "A Show")
    ]
    assert all(folders["movies"] in shared.path.parents for _, shared in found)
    assert everything() == before  # looked at, and nothing written


def test_a_films_id_is_the_one_it_had_before_episodes_were_told_apart(filled: Library) -> None:
    kept("movie", "A Film", year="1921")
    ((entry, shared),) = sharing.kept_media(config.media_folders())
    assert entry["id"] == sharing._name("m", "movie:A Film (1921).mp4")
    assert shared.id == sharing._name("f", "movie:A Film (1921).mp4")


# ---- carrying on a file that was cut off (2026-10-08) ---------------------------------------


def part(
    share: sharing.Share, key: str, file: dict[str, Any], asked: str, has: str | None = None
) -> tuple[int, bytes, dict[str, str]]:
    assert share.port is not None
    conn = http.client.HTTPConnection(LOOPBACK, share.port, timeout=10)
    sent = {"Authorization": "Bearer " + key, "Range": asked}
    if has is not None:
        sent["If-Range"] = has
    conn.request("GET", "/sync/v1/files/" + quote(file["id"], safe=""), headers=sent)
    response = conn.getresponse()
    data = response.read()
    headers = {name.lower(): value for name, value in response.getheaders()}
    assert headers["content-length"] == str(len(data))
    conn.close()
    return response.status, data, headers


def test_a_file_that_was_cut_off_is_carried_on(share: sharing.Share, filled: Library) -> None:
    key = paired(share)
    found = the_list(share, key)
    song = {t["title"]: t for t in found["tracks"]}["Song"]
    audio, whole = song["audio"], fetch(share, key, song["audio"])
    size, version = audio["size"], audio["version"]

    status, data, headers = part(share, key, audio, "bytes=100-", version)
    assert (status, data) == (206, whole[100:])
    assert headers["content-range"] == f"bytes 100-{size - 1}/{size}"
    assert headers["etag"] == f'"{version}"' and headers["accept-ranges"] == "bytes"
    assert part(share, key, audio, "bytes=10-19")[:2] == (206, whole[10:20])
    assert part(share, key, audio, f"bytes={size - 1}-{size + 50}")[:2] == (206, whole[-1:])
    assert part(share, key, audio, "bytes=0-")[:2] == (200, whole)  # all of it is all of it

    # The start it has is of another version: the whole file, never new bytes on old.
    assert part(share, key, audio, "bytes=100-", "an-older-version")[:2] == (200, whole)
    # Kinds of part this doesn't do: the whole file.
    assert part(share, key, audio, "bytes=-50")[:2] == (200, whole)
    assert part(share, key, audio, "bytes=0-1,5-6")[:2] == (200, whole)
    # A part that isn't in the file.
    status, data, headers = part(share, key, audio, f"bytes={size}-")
    assert (status, data, headers["content-range"]) == (416, b"", f"bytes */{size}")

    # What's kept inside a file's tags (a cover, lyrics) is carried on the same way.
    lyrics = {t["title"]: t for t in found["tracks"]}["Other"]["lyrics"]
    assert part(share, key, lyrics, "bytes=5-")[:2] == (206, WORDS.encode()[5:])
    # A part still needs a key.
    assert part(share, "not-a-key", audio, "bytes=100-")[0] == 401


# ---- what was done on a phone (2026-10-10) --------------------------------------------------


def did(kind: Any, **about: Any) -> dict[str, Any]:
    """A change as a phone sends it, under an id made up here as a phone makes one up."""
    return {"id": str(uuid.uuid4()).upper(), "kind": kind, "at": "2026-10-10T03:20:00Z", **about}


def its_own() -> str:
    """An id a phone makes up for a playlist made on it."""
    return "new-" + str(uuid.uuid4()).upper()


def ids(*changes: dict[str, Any]) -> list[str]:
    return [change["id"] for change in changes]


def tell(share: sharing.Share, key: str, *changes: Any) -> list[str]:
    """Send changes the way a phone does at a sync: the ids the computer dealt with."""
    status, answer = ask(share, "/sync/v1/changes", key=key, body={"changes": list(changes)})
    assert status == 200 and set(answer) == {"taken"}, answer
    return answer["taken"]


def post(
    share: sharing.Share, key: str, data: bytes, kind: str = "application/json"
) -> tuple[int, Any]:
    """Changes sent as these very bytes."""
    assert share.port is not None
    conn = http.client.HTTPConnection(LOOPBACK, share.port, timeout=30)
    sent = {"Authorization": "Bearer " + key, "Content-Type": kind}
    conn.request("POST", "/sync/v1/changes", data, sent)
    response = conn.getresponse()
    answer = json.loads(response.read())
    conn.close()
    return response.status, answer


def named(share: sharing.Share, key: str) -> tuple[dict[str, str], dict[str, str]]:
    """The list's ids as a phone knows them: its songs' by title, its playlists' by name."""
    found = the_list(share, key)
    return ({t["title"]: t["id"] for t in found["tracks"]},
            {p["name"]: p["id"] for p in found["playlists"]})  # fmt: skip


def playlists(lib: Library) -> list[tuple[str, list[str]]]:
    return [(p["name"], p["track_ids"]) for p in listening.get(lib)["playlists"]]


MORNING = ("Morning", [SONG_ID, OTHER_ID, SONG_ID, "gone-from-the-library"])


def test_a_song_put_in_a_playlist_on_a_phone(share: sharing.Share, filled: Library) -> None:
    listening.create_playlist(filled, "Evening")
    key = paired(share)
    tracks, lists = named(share, key)
    first = did("playlist_add", playlist=lists["Evening"], track=tracks["Other"])
    second = did("playlist_add", playlist=lists["Evening"], track=tracks["Song"])
    assert tell(share, key, first, second) == ids(first, second)
    assert playlists(filled) == [MORNING, ("Evening", [OTHER_ID, SONG_ID])]  # at the end, in turn
    # The next list has it, as the phone knows the songs.
    after = {p["name"]: p["tracks"] for p in the_list(share, key)["playlists"]}
    assert after["Evening"] == [tracks["Other"], tracks["Song"]]
    assert after["Morning"] == [tracks["Song"], tracks["Other"], tracks["Song"]]
    # A song the playlist has already isn't put in a second time: the phone can't see
    # what was put in on the computer since it last synced.
    again = did("playlist_add", playlist=lists["Evening"], track=tracks["Song"])
    assert tell(share, key, again) == ids(again)
    assert playlists(filled) == [MORNING, ("Evening", [OTHER_ID, SONG_ID])]


def test_a_change_that_arrives_twice_is_taken_once(share: sharing.Share, filled: Library) -> None:
    key = paired(share)
    tracks, _ = named(share, key)
    theirs = its_own()
    sent = [
        did("playlist_new", playlist=theirs, name="Driving"),
        did("playlist_add", playlist=theirs, track=tracks["Song"]),
        did("listened", track=tracks["Song"], seconds=215.5),
    ]
    assert tell(share, key, *sent) == ids(*sent)
    once = listening.get(filled)
    assert once["listened"] == {SONG_ID: 215.5}
    assert playlists(filled) == [MORNING, ("Driving", [SONG_ID])]
    # The answer was lost on the way: the phone sends all of it again, with one more.
    more = did("listened", track=tracks["Other"], seconds=10)
    assert tell(share, key, *sent, more) == ids(*sent, more)
    twice = listening.get(filled)
    assert twice["playlists"] == once["playlists"]
    assert twice["listened"] == {SONG_ID: 215.5, OTHER_ID: 10.0}
    # And once more after the app was closed and opened again: what was taken is
    # remembered with the library, not by the engine that took it.
    share.stop()
    again = sharing.Share(filled)
    again.start()
    try:
        assert tell(again, key, *sent, more) == ids(*sent, more)
        assert listening.get(filled) == twice
    finally:
        again.stop()


def test_a_playlist_made_on_a_phone(share: sharing.Share, filled: Library) -> None:
    key = paired(share)
    tracks, lists = named(share, key)
    theirs = its_own()
    made = did("playlist_new", playlist=theirs, name="  Driving ")
    first = did("playlist_add", playlist=theirs, track=tracks["Song"])
    assert tell(share, key, made, first) == ids(made, first)
    # The next list has it under an id of the computer's own.
    found = {p["name"]: p for p in the_list(share, key)["playlists"]}
    assert set(found) == {"Morning", "Driving"}
    assert found["Driving"]["id"].startswith("p-") and found["Driving"]["id"] != theirs
    assert found["Driving"]["tracks"] == [tracks["Song"]]
    assert found["Morning"]["id"] == lists["Morning"]

    # A song put in it afterwards, in a later request that still names the phone's own
    # id for it, lands in it: also when the app has been closed and opened in between.
    share.stop()
    again = sharing.Share(filled)
    again.start()
    try:
        later = did("playlist_add", playlist=theirs, track=tracks["Other"])
        assert tell(again, key, later) == ids(later)
        assert playlists(filled) == [MORNING, ("Driving", [SONG_ID, OTHER_ID])]
    finally:
        again.stop()


def test_a_playlist_made_on_a_phone_under_a_name_there_is(
    share: sharing.Share, filled: Library
) -> None:
    listening.create_playlist(filled, "Evening")  # made on the computer since the phone synced
    key = paired(share)
    tracks, _ = named(share, key)
    theirs = its_own()
    made = did("playlist_new", playlist=theirs, name="evening")
    put = did("playlist_add", playlist=theirs, track=tracks["Song"])
    assert tell(share, key, made, put) == ids(made, put)
    # The one there is, is used: there aren't two of one name.
    assert playlists(filled) == [MORNING, ("Evening", [SONG_ID])]


def test_time_spent_listening_comes_back_from_a_phone(
    share: sharing.Share, filled: Library
) -> None:
    key = paired(share)
    before = the_list(share, key)
    tracks = {t["title"]: t for t in before["tracks"]}
    assert tracks["Song"]["listened"] == 0.0
    first = did("listened", track=tracks["Song"]["id"], seconds=215.5)
    second = did("listened", track=tracks["Song"]["id"], seconds=60)
    other = did("listened", track=tracks["Other"]["id"], seconds=0.5)
    assert tell(share, key, first, second, other) == ids(first, second, other)
    listening.listened(filled, SONG_ID, 24.5)  # the computer's own player: the same figure

    after = the_list(share, key)
    tracks = {t["title"]: t for t in after["tracks"]}
    assert (tracks["Song"]["listened"], tracks["Other"]["listened"]) == (300.0, 0.5)
    assert after["revision"] != before["revision"]
    # It's time, not a count: a play is still a song heard to its end.
    assert tracks["Song"]["playCount"] == 2 and tracks["Other"]["playCount"] == 0
    assert listening.get(filled)["plays"].keys() == {SONG_ID}


def test_what_this_engine_doesnt_know_is_left_with_the_phone(
    share: sharing.Share, filled: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = paired(share)
    song = named(share, key)[0]["Song"]
    before = listening.get(filled)
    newer = did("favourite", track=song, on=True)  # a kind a newer phone might send
    odd = did(["playlist_add"], track=song)
    nameless = {"kind": "listened", "track": song, "seconds": 5}  # nothing to answer it by
    numbered = {"id": 7, "kind": "listened", "track": song, "seconds": 5}
    assert tell(share, key, newer, odd, nameless, numbered, "junk", None, 5, [newer]) == []
    assert listening.get(filled) == before

    # More than one request may carry: the first of them are taken, the rest wait.
    monkeypatch.setattr(sharing, "MAX_CHANGES", 2)
    many = [did("listened", track=song, seconds=10) for _ in range(5)]
    assert tell(share, key, *many) == ids(*many[:2])
    assert tell(share, key, *many[2:]) == ids(*many[2:4])
    assert tell(share, key, *many[4:]) == ids(*many[4:])
    assert listening.get(filled)["listened"] == {SONG_ID: 50.0}


def test_what_cant_be_done_is_dropped(share: sharing.Share, filled: Library) -> None:
    gone = listening.create_playlist(filled, "Gone")[-1]["id"]
    key = paired(share)
    found = the_list(share, key)
    tracks = {t["title"]: t["id"] for t in found["tracks"]}
    lists = {p["name"]: p["id"] for p in found["playlists"]}
    listening.delete_playlist(filled, gone)
    before = listening.get(filled)
    song, morning = tracks["Song"], lists["Morning"]
    sent = [
        did("playlist_add", playlist=lists["Gone"], track=song),  # deleted since the list
        did("playlist_add", playlist=its_own(), track=song),  # never made
        did("playlist_add", playlist=morning, track=song),  # it has the song
        did("playlist_add", playlist=morning, track="t-" + "0" * 24),  # no such song
        did("playlist_add", playlist=morning, track=found["videos"][0]["id"]),  # not a song
        did("playlist_add", playlist=morning, track=tracks["Loose"]),  # no id of its own
        did("playlist_add", playlist=morning, track=OTHER_ID),  # not an id a phone is given
        did("playlist_add", playlist=morning),
        did("playlist_add", track=song),
        did("playlist_add", playlist=["x"], track={"y": 1}),
        did("playlist_new", playlist=its_own(), name=""),
        did("playlist_new", playlist=its_own(), name=7),
        did("playlist_new", name="Nothing To Know It By"),
        did("listened", track=tracks["Loose"], seconds=30),
        did("listened", track="nothing", seconds=30),
        did("listened", track=song),
    ]
    not_times = (0, -5, "long", True, None, [1], float("nan"), float("inf"), 1e9, 10**400)
    sent += [did("listened", track=song, seconds=seconds) for seconds in not_times]
    assert tell(share, key, *sent) == ids(*sent)  # dealt with: the phone lets go of them
    assert listening.get(filled) == before


def test_changes_need_a_key(share: sharing.Share, filled: Library) -> None:
    key = paired(share)
    song = named(share, key)[0]["Song"]
    before = listening.get(filled)
    sent = {"changes": [did("listened", track=song, seconds=30),
                        did("playlist_new", playlist=its_own(), name="Sneaked In")]}  # fmt: skip
    for not_it in (None, "not-a-key-" + "x" * 20, key + "x"):
        status, answer = ask(share, "/sync/v1/changes", key=not_it, body=sent)
        assert (status, answer["error"]) == (401, "not_paired")
    # A web page behind the request, or one sent to another name: not from home.
    for headers in ({"Origin": "http://example.com"}, {"Host": "example.com"}):
        status, answer = ask(share, "/sync/v1/changes", key=key, body=sent, headers=headers)
        assert (status, answer["error"]) == (403, "not_home")
    assert ask(share, "/sync/v1/changes", key=key)[0] == 404  # it's sent, never asked for
    # A device that has been unpaired: its key has stopped working here too.
    sharing.forget(sharing.devices()[0]["id"])
    status, answer = ask(share, "/sync/v1/changes", key=key, body=sent)
    assert (status, answer["error"]) == (401, "not_paired")
    assert listening.get(filled) == before


def test_nonsense_is_turned_away_and_changes_nothing(
    share: sharing.Share, filled: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = paired(share)
    song = named(share, key)[0]["Song"]
    records = filled.paths.state_file.read_bytes()
    for data in (b"not json", b"[1, 2]", b'"changes"', b"7", b"\xff\xfe\xfd", b"[" * 100_000):
        status, answer = post(share, key, data)
        assert (status, answer["error"]) == (400, "bad_request"), data[:20]
    for data in (b"", b"{}", b'{"changes": "all"}', b'{"changes": {"id": "x"}}',
                 b'{"changes": []}', b'{"changes": [[], {}, 1, null]}'):  # fmt: skip
        assert post(share, key, data) == (200, {"taken": []}), data

    # More than a request may be: refused whole, and nothing of it is looked at.
    real = did("listened", track=song, seconds=30)
    padded = {"changes": [real], "padding": "x" * sharing.MAX_CHANGES_BODY}
    status, answer = post(share, key, json.dumps(padded).encode())
    assert (status, answer["error"]) == (413, "bad_request")
    assert filled.paths.state_file.read_bytes() == records  # not so much as written again

    # The library's records can't be saved just now: the phone is told, and keeps them.
    def fails(lib: Library, changes: list[Any]) -> dict[str, Any]:
        raise OSError("the disk is full")

    with monkeypatch.context() as broken:
        broken.setattr(listening, "from_devices", fails)
        status, answer = post(share, key, json.dumps({"changes": [real]}).encode())
        assert (status, answer["error"]) == (500, "unavailable")
    # The body is read as JSON whatever it says it is: the key is what's checked.
    data = json.dumps({"changes": [real]}).encode()
    assert post(share, key, data, "text/plain") == (200, {"taken": [real["id"]]})
    assert listening.get(filled)["listened"] == {SONG_ID: 30.0}


def test_changes_touch_nothing_but_the_librarys_records(
    share: sharing.Share, filled: Library
) -> None:
    key = paired(share)
    tracks, lists = named(share, key)
    before = snapshot(filled.root)
    theirs = its_own()
    sent = [
        did("playlist_new", playlist=theirs, name="Driving"),
        did("playlist_add", playlist=theirs, track=tracks["Song"]),
        did("playlist_add", playlist=lists["Morning"], track=tracks["Other"]),
        did("listened", track=tracks["Song"], seconds=215.5),
        did("listened", track=tracks["Loose"], seconds=30),
    ]
    assert tell(share, key, *sent) == ids(*sent)
    after = snapshot(filled.root)
    records = filled.paths.state_file.relative_to(filled.root).as_posix()
    assert after.pop(records) != before.pop(records)  # where playlists and time are kept
    # Every other file is as it was: no song, tag, cover or lyrics was touched, nothing
    # was added or taken away, and so there was nothing to journal.
    assert after == before
    assert any(name.startswith("Music/") for name in after)
    assert not list(filled.paths.journal.glob("*"))


def test_only_hashes_of_what_a_phone_made_up_are_kept(
    share: sharing.Share, filled: Library, caplog: pytest.LogCaptureFixture
) -> None:
    key = paired(share)
    song = named(share, key)[0]["Song"]
    theirs = its_own()
    sent = [
        did("playlist_new", playlist=theirs, name="A Name Of The Owner's"),
        did("playlist_add", playlist=theirs, track=song),
        did("listened", track=song, seconds=90),
    ]
    with caplog.at_level(logging.DEBUG):
        assert tell(share, key, *sent) == ids(*sent)
    kept = filled.paths.state_file.read_text(encoding="utf-8")
    assert "A Name Of The Owner's" in kept  # the one thing kept as the phone sent it
    for made_up in (theirs, song, key, *ids(*sent)):
        assert made_up not in kept, made_up
    assert "changes were taken (playlists made: 1, songs put in playlists: 1, minutes" in (
        caplog.text
    )
    for private in (key, "Bearer", "A Name Of The Owner's", theirs, song, SONG_ID, LOOPBACK,
                    *ids(*sent)):  # fmt: skip
        assert private not in caplog.text, private


def test_an_engine_without_the_librarys_lock_takes_nothing(
    share: sharing.Share, filled: Library
) -> None:
    key = paired(share)
    share.stop()
    filled.close()
    with library.open(filled.root, write=False) as reading:
        again = sharing.Share(reading)
        again.start()
        try:
            assert ask(again, "/sync/v1/hello")[1]["accepts"] == []
            sent = {"changes": [did("playlist_new", playlist=its_own(), name="Driving")]}
            status, answer = ask(again, "/sync/v1/changes", key=key, body=sent)
            assert (status, answer["error"]) == (404, "not_found")
            assert len(the_list(again, key)["tracks"]) == 3  # the list is given as ever
        finally:
            again.stop()
    assert playlists(filled) == [MORNING]


def test_the_app_is_told_what_a_phone_changed(
    opened: rpc.Server,  # noqa: F811
    out: Capture,  # noqa: F811
) -> None:
    result(opened, "sharing.set", on=True)
    share = opened._share
    assert share is not None
    shown = result(opened, "sharing.pair")
    key = ask(share, "/sync/v1/pair", body={"code": shown["code"], "device": "iPhone"})[1]["key"]
    assert ask(share, "/sync/v1/hello", key=key)[1]["accepts"] == ["changes"]

    made = did("playlist_new", playlist=its_own(), name="Driving")
    assert tell(share, key, made) == ids(made)
    assert out.notes("listening.changed") == [{}]
    assert [p["name"] for p in result(opened, "listening.get")["playlists"]] == ["Driving"]
    assert tell(share, key, made) == ids(made)  # again: nothing changed, so nothing is said
    assert tell(share, key, did("listened", track="nothing", seconds=5)) != []
    assert len(out.notes("listening.changed")) == 1
    result(opened, "sharing.set", on=False)
