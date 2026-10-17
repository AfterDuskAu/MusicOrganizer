"""A video from Bear Media's browser window (2026-10-18, the owner: "It offers a open in
browser option, and then if you click yes, it pulls the info needed once it opens").

The window (a real browser, in the app) fetches the video; the engine only takes the
pieces, puts them together and keeps them: `Fetcher.browser_*` and `browser.*`. The
videos here are made by ffmpeg, a few seconds of a test picture and a tone; the folders
and the app's cache are the test's own (MUSICORG_HOME). Nothing touches the network.
"""

from __future__ import annotations

import base64
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from test_fetch import cache, film, left_in_cache, movies
from test_rpc import Capture, code, opened, out, result, root, server  # noqa: F401  (fixtures)

from musicorg import config, fetch, rpc, tools

PAGE = "https://www.example.org/watch/a-video"
MEDIA = "https://media.example.org/a-video/master.m3u8"


def videos_folder() -> Path:
    return config.media_folders()["media"]


def fetcher() -> fetch.Fetcher:
    return fetch.Fetcher(cache(), movies(), videos=videos_folder())


def waited(fetching: fetch.Fetcher, which: str, seconds: float = 60) -> dict[str, Any]:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        status = fetching.status(which)
        if not status["keeping"]:
            return status
        time.sleep(0.05)
    raise AssertionError("the download didn't end")


def streams(path: Path) -> list[str]:
    said = subprocess.run(
        [str(tools.require("ffprobe")), "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return sorted(s["codec_type"] for s in json.loads(said.stdout)["streams"])


@pytest.fixture(scope="module")
def made(tmp_path_factory: pytest.TempPathFactory) -> dict[str, bytes]:
    folder = tmp_path_factory.mktemp("browser-videos")
    return {
        "mp4": film(folder / "film.mp4"),
        "ts": film(folder / "film.ts"),
        "picture": film(folder / "picture.mp4", sound=("-an",)),
        "sound": film(folder / "sound.m4a", picture=("-vn",)),
    }


def send(fetching: fetch.Fetcher, job: str, track: str, data: bytes, piece: int = 7000) -> None:
    for at in range(0, len(data), piece):
        fetching.browser_part(job, track, at, data[at : at + piece])


# ---- what the window sends, put together and kept ----------------------------------------


def test_a_streams_pieces_are_put_together_and_kept(made: dict[str, bytes]) -> None:
    fetching = fetcher()
    started = fetching.browser_start(PAGE, MEDIA, "A Stream", to_videos=True)
    assert started["state"] == fetch.FETCHING and started["keeping"]
    assert Path(started["file_path"]).parent == cache() / "fetched"
    send(fetching, started["id"], "video", made["ts"])
    fetching.browser_progress(started["id"], 0.5, size=len(made["ts"]) * 2)
    assert fetching.status(started["id"])["progress"] == 0.5
    fetching.browser_done(started["id"])
    status = waited(fetching, started["id"])
    assert status["keep_error"] is None, status
    kept = Path(status["kept_path"])
    assert kept == videos_folder() / "A Stream.mp4"  # an MPEG-TS stream is kept as an MP4
    assert streams(kept) == ["audio", "video"]
    assert left_in_cache() == [f"{started['id']}.json"]  # only the note that it was kept


def test_a_picture_and_its_sound_that_came_apart_are_joined(made: dict[str, bytes]) -> None:
    fetching = fetcher()
    started = fetching.browser_start(PAGE, MEDIA, "Two Tracks", folder=("18+",))
    send(fetching, started["id"], "video", made["picture"])
    send(fetching, started["id"], "audio", made["sound"])
    fetching.browser_done(started["id"])
    status = waited(fetching, started["id"])
    kept = Path(status["kept_path"])
    assert kept == movies() / "18+" / "Two Tracks.mp4"
    assert streams(kept) == ["audio", "video"]


def test_a_whole_file_the_window_saved_where_it_was_told_is_kept_as_it_is(
    made: dict[str, bytes],
) -> None:
    fetching = fetcher()
    started = fetching.browser_start(PAGE, "https://cdn.example.org/clip.mp4", "A Clip")
    Path(started["file_path"]).write_bytes(made["mp4"])  # what the window's download does
    fetching.browser_done(started["id"])
    status = waited(fetching, started["id"])
    assert Path(status["kept_path"]) == movies() / "A Clip.mp4"
    assert Path(status["kept_path"]).read_bytes() == made["mp4"]


def test_nothing_that_is_a_video_is_never_kept() -> None:
    fetching = fetcher()
    started = fetching.browser_start(PAGE, MEDIA, "Noise")
    fetching.browser_part(started["id"], "video", 0, b"not a video at all" * 100)
    fetching.browser_done(started["id"])
    status = waited(fetching, started["id"])
    assert status["kept_path"] is None and status["keep_error"]
    assert not any(movies().glob("Noise*")) if movies().exists() else True
    assert left_in_cache() == []
    with pytest.raises(fetch.FetchError):
        fetching.browser_done(started["id"])  # ended: nothing more is taken
    started = fetching.browser_start(PAGE, MEDIA, "Nothing")
    fetching.browser_done(started["id"])
    assert "Nothing of the video" in waited(fetching, started["id"])["keep_error"]


# ---- what's refused ---------------------------------------------------------------------------


def test_pieces_must_come_in_order_and_be_of_a_track(made: dict[str, bytes]) -> None:
    fetching = fetcher()
    started = fetching.browser_start(PAGE, MEDIA, "In Order")
    job = started["id"]
    assert fetching.browser_part(job, "video", 0, b"abcd") == {"bytes": 4}
    with pytest.raises(fetch.FetchError, match="went missing"):
        fetching.browser_part(job, "video", 10, b"efgh")
    assert fetching.browser_part(job, "video", 2, b"CDEF") == {"bytes": 6}  # sent again
    with pytest.raises(fetch.FetchError):
        fetching.browser_part(job, "subtitles", 0, b"x")
    with pytest.raises(fetch.FetchError):
        fetching.browser_part("b-unknown", "video", 0, b"x")
    part = cache() / "fetched" / f"{job}.video.part"
    assert part.read_bytes() == b"abCDEF"


def test_youtube_and_soundcloud_and_anything_not_on_the_web_are_refused() -> None:
    fetching = fetcher()
    for page, media in (
        ("https://www.youtube.com/watch?v=aqz-KE-bpKQ", MEDIA),
        (PAGE, "https://rr1---sn-x.googlevideo.com/videoplayback?id=1"),
        ("https://soundcloud.com/a/b", MEDIA),
        ("file:///etc/hosts", MEDIA),
        (PAGE, "blob:https://www.example.org/1234"),
    ):
        with pytest.raises(fetch.FetchError):
            fetching.browser_start(page, media, "Nope")
    with pytest.raises(fetch.FetchError):
        fetching.browser_start(PAGE, MEDIA, "   ")


def test_stopped_or_gone_quiet_what_came_is_let_go(monkeypatch: pytest.MonkeyPatch) -> None:
    fetching = fetcher()
    started = fetching.browser_start(PAGE, MEDIA, "Stopped")
    fetching.browser_part(started["id"], "video", 0, b"some of it")
    fetching.stop(started["id"])
    assert left_in_cache() == []
    with pytest.raises(fetch.FetchError):
        fetching.browser_part(started["id"], "video", 10, b"more")
    # A window shut without a word: after a while, it's said, and what came goes.
    monkeypatch.setattr(fetch, "BROWSER_QUIET_S", 0.05)
    quiet = fetching.browser_start(PAGE, MEDIA, "Quiet")
    fetching.browser_part(quiet["id"], "video", 0, b"some of it")
    time.sleep(0.1)
    status = fetching.status(quiet["id"])
    assert "stopped sending" in status["keep_error"] and not status["keeping"]
    assert left_in_cache() == []


def test_a_video_with_no_room_for_it_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "room_for", lambda *a: False)
    with pytest.raises(fetch.FetchError, match="room"):
        fetcher().browser_start(PAGE, MEDIA, "Huge", size=10**12)


# ---- browser.* over RPC ------------------------------------------------------------------------


def test_browser_methods_end_to_end(opened: rpc.Server, made: dict[str, bytes]) -> None:  # noqa: F811
    result(opened, "kids.set", on=False)
    started = result(opened, "browser.start", page_url=PAGE, media_url=MEDIA, title="Over RPC",
                     kind="movie", size=len(made["ts"]))  # fmt: skip
    job = started["id"]
    data = made["ts"]
    for at in range(0, len(data), 8000):
        piece = base64.b64encode(data[at : at + 8000]).decode()
        result(opened, "browser.part", id=job, track="video", at=at, data=piece)
    assert result(opened, "browser.progress", id=job, progress=1) == {}
    result(opened, "browser.done", id=job)
    until = time.monotonic() + 60
    while (status := result(opened, "stream.status", id=job))["keeping"]:
        assert time.monotonic() < until
        time.sleep(0.05)
    assert status["kept_path"] == str(movies() / "Over RPC.mp4")
    assert (
        code(opened, "browser.part", id=job, track="video", at=0, data="!!!") == rpc.INVALID_PARAMS
    )
    assert code(opened, "browser.progress", id=job, progress="most") == rpc.INVALID_PARAMS
    assert code(opened, "browser.start", page_url=PAGE, media_url=MEDIA, title="X",
                kind="music") == rpc.INVALID_PARAMS  # fmt: skip
    assert code(opened, "browser.part", id=job, track="video", at=0, data="") == rpc.USER_ERROR


def test_no_browser_window_in_a_childs_profile(opened: rpc.Server) -> None:  # noqa: F811
    asked = {"page_url": PAGE, "media_url": MEDIA, "title": "A"}
    # The app hasn't said which profile this is: as in a child's, nothing for adults.
    assert code(opened, "browser.start", kind="adult", **asked) == rpc.USER_ERROR
    result(opened, "kids.set", on=True)
    assert code(opened, "browser.start", **asked) == rpc.USER_ERROR
