"""fetch (2026-10-17): a film downloaded from a web address a plugin gave, and kept.

Every address here is a little web server on this computer's own loopback address,
started by the test and answering only it: no test reaches the network (replay mode is
on, and `fetch` refuses any address that isn't on this computer then). The films are
made here by ffmpeg, a few seconds of a test picture and a tone. The Movies folder and
the app's cache are the test's own (MUSICORG_HOME).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from conftest import LOOPBACK
from test_rpc import Capture, code, opened, out, result, root, server  # noqa: F401  (fixtures)
from test_sharing_video import Connection

from musicorg import config, convert, fetch, fileops, naming, rpc, tools
from musicorg.errors import OutsideLibraryError, ReplayMissError, UserError

# ---- a made-up plugin's web server ---------------------------------------------------------


@dataclass
class Page:
    data: bytes
    kind: str = "video/mp4"
    etag: str | None = '"one"'
    status: int = 200  # another: answered with that, and nothing more


@dataclass
class Site:
    """What the test's web server answers, and what it was asked."""

    pages: dict[str, Page] = field(default_factory=dict)
    folder: Path | None = None  # files served as they are (a playlist and its parts)
    cut_after: dict[str, int] = field(default_factory=dict)  # the next answer stops there
    no_ranges: bool = False
    asked: list[dict[str, str | None]] = field(default_factory=list)


def serve(site: Site) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            path = self.path.split("?")[0]
            site.asked.append({"path": path, "range": self.headers.get("Range"),
                               "if_range": self.headers.get("If-Range")})  # fmt: skip
            page = site.pages.get(path)
            if page is None and site.folder is not None:
                found = site.folder / path.lstrip("/")
                if found.is_file():
                    kind = "application/vnd.apple.mpegurl" if found.suffix == ".m3u8" else (
                        "video/mp2t")  # fmt: skip
                    page = Page(found.read_bytes(), kind, None)
            if page is None or page.status != 200:
                self.send_response(404 if page is None else page.status)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            first, status = 0, 200
            wanted = self.headers.get("Range")
            same = page.etag is None or self.headers.get("If-Range") in (None, page.etag)
            if wanted and not site.no_ranges and same:
                first = int(wanted.removeprefix("bytes=").split("-")[0])
                if first >= len(page.data):
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(page.data)}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
            body = page.data[first:]
            self.send_response(status)
            self.send_header("Content-Type", page.kind)
            self.send_header("Content-Length", str(len(body)))
            if page.etag:
                self.send_header("ETag", page.etag)
            if status == 206:
                last = len(page.data) - 1
                self.send_header("Content-Range", f"bytes {first}-{last}/{len(page.data)}")
            self.end_headers()
            cut = site.cut_after.pop(path, None)
            if cut is not None:
                self.wfile.write(body[: max(cut - first, 0)])
                self.wfile.flush()
                self.close_connection = True
                return
            self.wfile.write(body)

    listener = ThreadingHTTPServer((LOOPBACK, 0), Handler)
    listener.daemon_threads = True
    threading.Thread(target=listener.serve_forever, daemon=True).start()
    try:
        yield f"http://{LOOPBACK}:{listener.server_address[1]}"
    finally:
        listener.shutdown()
        listener.server_close()


@pytest.fixture
def site() -> Iterator[tuple[Site, str]]:
    made = Site()
    for address in serve(made):
        yield made, address


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    """Small parts, so a little film is several; and no long waits between tries."""
    monkeypatch.setattr(fetch, "PART_BYTES", 4096)
    monkeypatch.setattr(fetch, "TRY_WAITS_S", (0.05, 0.05))


def film(target: Path, picture: tuple[str, ...] = ("-c:v", "libx264", "-pix_fmt", "yuv420p"),
         sound: tuple[str, ...] = ("-c:a", "aac")) -> bytes:  # fmt: skip
    command = [str(tools.require("ffmpeg")), "-nostdin", "-y", "-v", "error",
               "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=10",
               "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
               *picture, *sound, str(target)]  # fmt: skip
    subprocess.run(command, check=True, stdin=subprocess.DEVNULL)
    return target.read_bytes()


@pytest.fixture(scope="module")
def mp4(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    return film(tmp_path_factory.mktemp("films") / "film.mp4")


@pytest.fixture(scope="module")
def mkv(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    return film(tmp_path_factory.mktemp("films") / "film.mkv", sound=("-c:a", "libmp3lame"))


def movies() -> Path:
    return config.media_folders()["movies"]


def cache() -> Path:
    return config.app_dirs().cache


def fetcher() -> fetch.Fetcher:
    return fetch.Fetcher(cache(), movies())


def waited(fetching: fetch.Fetcher, url: str, seconds: float = 60) -> dict[str, Any]:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        status = fetching.status(url)
        if not status["keeping"]:
            return status
        time.sleep(0.05)
    raise AssertionError("the download didn't end")


def left_in_cache() -> list[str]:
    folder = cache() / fileops.FETCHED_DIR
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


# ---- fetching and keeping --------------------------------------------------------------------


def test_a_film_is_fetched_whole_and_kept_in_the_movies_folder(
    site: tuple[Site, str], mp4: bytes
) -> None:
    served, address = site
    served.pages["/films/a.mp4"] = Page(mp4)
    fetching = fetcher()
    url = address + "/films/a.mp4"
    first = fetching.keep(url, "A Film (2001)", convert=True)
    assert first["id"] == fetch.fetch_id(url) and first["keeping"] is True
    ended = waited(fetching, url)
    kept = movies() / "A Film (2001).mp4"
    assert ended["kept_path"] == str(kept) and ended["keep_error"] is None
    assert (ended["state"], ended["progress"], ended["bytes"]) == ("complete", 1.0, len(mp4))
    assert kept.read_bytes() == mp4  # nothing needed converting: kept exactly as it came
    assert len(served.asked) == 1 and served.asked[0]["range"] is None
    # What arrived is let go of; a note says where it went, for its two days.
    assert left_in_cache() == [f"{ended['id']}.json"]
    # Asked again (the app carrying on a download it didn't hear the end of): it's kept.
    again = fetcher().keep(url, "A Film (2001)")
    assert again["kept_path"] == str(kept) and again["keeping"] is False
    assert len(served.asked) == 1 and sorted(p.name for p in movies().iterdir()) == [kept.name]


def test_a_film_for_adults_is_kept_in_the_18_folder(
    opened: rpc.Server,  # noqa: F811
    site: tuple[Site, str],
    mp4: bytes,
) -> None:
    served, address = site
    served.pages["/x"] = Page(mp4, "application/octet-stream")
    url = address + "/x"
    first = result(opened, "stream.keep", url=url, title="A Film", year="2001", kind="adult")
    assert first["keeping"] is True
    assert opened._fetcher is not None
    ended = waited(opened._fetcher, url)
    kept = movies() / naming.ADULT_DIR / "A Film (2001).mp4"  # an MP4, by what it is
    assert ended["kept_path"] == str(kept) and kept.read_bytes() == mp4
    assert result(opened, "stream.status", url=url)["kept_path"] == str(kept)
    assert result(opened, "stream.status", id=first["id"])["kept_path"] == str(kept)
    assert [p.name for p in movies().iterdir()] == [naming.ADULT_DIR]  # nothing loose
    # The same checks as a torrent's keep.
    for wrong in ({"title": "A", "kind": "cartoon"}, {"title": " "},
                  {"title": "A", "kind": "movie", "show": "A Show"}):  # fmt: skip
        assert code(opened, "stream.keep", url=url + "?again", **wrong) == rpc.INVALID_PARAMS
    assert code(opened, "stream.status") == rpc.INVALID_PARAMS
    assert code(opened, "stream.status", url=address + "/never") == rpc.USER_ERROR
    assert result(opened, "stream.stop", url=address + "/never") == {}


def test_a_film_cut_off_carries_on_from_where_it_got_to(site: tuple[Site, str], mp4: bytes) -> None:
    served, address = site
    served.pages["/a.mp4"] = Page(mp4)
    served.cut_after["/a.mp4"] = 10_000
    fetching = fetcher()
    url = address + "/a.mp4"
    fetching.keep(url, "A Film")
    ended = waited(fetching, url)
    assert ended["kept_path"] == str(movies() / "A Film.mp4")
    assert (movies() / "A Film.mp4").read_bytes() == mp4
    assert [one["range"] for one in served.asked] == [None, "bytes=10000-"]
    assert served.asked[1]["if_range"] == '"one"'  # only if it's still the same file


def stopped_after(
    fetching: fetch.Fetcher, url: str, parts: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep `url`, and stop the engine (`stop_all`) once `parts` parts have arrived; then
    wait for it to have stopped, as the engine does when the app is closed."""
    real = fileops.write_fetched_part
    written: list[int] = []

    def counted(*args: Any, **kwargs: Any) -> int:
        written.append(real(*args, **kwargs))
        if len(written) == parts:
            fetching.stop_all()
        return written[-1]

    monkeypatch.setattr(fileops, "write_fetched_part", counted)
    fetching.keep(url, "A Film")
    waited(fetching, url)
    monkeypatch.setattr(fileops, "write_fetched_part", real)


def test_a_film_carries_on_after_the_app_was_closed(
    site: tuple[Site, str], mp4: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    served, address = site
    served.pages["/a.mp4"] = Page(mp4)
    url = address + "/a.mp4"
    before = fetcher()
    stopped_after(before, url, 3, monkeypatch)
    said = before.status(url)
    assert said["keep_error"] is None and said["kept_path"] is None  # just stopped
    assert not movies().exists() or list(movies().iterdir()) == []
    job = fetch.fetch_id(url)
    assert left_in_cache() == [f"{job}.json", f"{job}.part"]  # kept, to carry on from
    note = json.loads((cache() / fileops.FETCHED_DIR / f"{job}.json").read_text())
    assert note["have"] == 3 * fetch.PART_BYTES and note["size"] == len(mp4)
    # The engine started again, and the app asks once more: it carries on from there.
    after = fetcher()
    after.keep(url, "A Film")
    ended = waited(after, url)
    assert (movies() / "A Film.mp4").read_bytes() == mp4 and ended["keep_error"] is None
    assert served.asked[-1]["range"] == f"bytes={3 * fetch.PART_BYTES}-"
    assert served.asked[-1]["if_range"] == '"one"'


def test_an_address_that_sends_nothing_is_given_up_on(site: tuple[Site, str]) -> None:
    served, address = site
    served.pages["/busy.mp4"] = Page(b"", status=503)
    fetching = fetcher()
    fetching.keep(address + "/busy.mp4", "A Film")
    ended = waited(fetching, address + "/busy.mp4")
    assert "isn't sending the film" in ended["keep_error"]
    assert len(served.asked) == len(fetch.TRY_WAITS_S) + 1  # asked again a few times first


def test_a_film_that_changed_meanwhile_starts_again(
    site: tuple[Site, str], mp4: bytes, mkv: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    served, address = site
    served.pages["/a"] = Page(mkv, "video/x-matroska")
    url = address + "/a"
    stopped_after(fetcher(), url, 2, monkeypatch)
    served.pages["/a"] = Page(mp4, etag='"two"')  # another file at the same address now
    again = fetcher()
    again.keep(url, "A Film")
    ended = waited(again, url)
    carried_on = f"bytes={2 * fetch.PART_BYTES}-"
    assert served.asked[-1]["range"] == carried_on and served.asked[-1]["if_range"] == '"one"'
    assert Path(ended["kept_path"]).read_bytes() == mp4  # the new file, whole, from its start
    assert Path(ended["kept_path"]).name == "A Film.mp4"


def test_an_address_that_takes_no_ranges_starts_again(site: tuple[Site, str], mkv: bytes) -> None:
    served, address = site
    served.pages["/film.mkv"] = Page(mkv, "video/x-matroska", etag=None)
    served.cut_after["/film.mkv"] = 5_000
    served.no_ranges = True
    fetching = fetcher()
    url = address + "/film.mkv"
    fetching.keep(url, "A Film")
    ended = waited(fetching, url)
    assert Path(ended["kept_path"]) == movies() / "A Film.mkv"
    assert Path(ended["kept_path"]).read_bytes() == mkv
    assert len(served.asked) == 2


def test_a_film_is_converted_for_phones_when_settings_say_so(
    site: tuple[Site, str], mkv: bytes
) -> None:
    served, address = site
    served.pages["/film.mkv"] = Page(mkv, "video/x-matroska")
    fetching = fetcher()
    url = address + "/film.mkv"
    fetching.keep(url, "A Film", folder=(naming.ADULT_DIR,), convert=True)
    ended = waited(fetching, url)
    kept = movies() / naming.ADULT_DIR / "A Film.mp4"
    assert ended["kept_path"] == str(kept) and ended["converting"] is None
    kinds = [(s["codec_type"], s["codec_name"]) for s in convert.streams_of(kept)["streams"]]
    assert kinds == [("video", "h264"), ("audio", "aac")]
    assert left_in_cache() == [f"{ended['id']}.json"]  # the converted copy went too


def test_a_playlist_of_parts_is_joined_into_one_film(
    site: tuple[Site, str], tmp_path: Path
) -> None:
    served, address = site
    parts = tmp_path / "hls"
    parts.mkdir()
    subprocess.run(
        [str(tools.require("ffmpeg")), "-nostdin", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=duration=3:size=160x120:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "libx264", "-g", "10", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-f", "hls", "-hls_time", "1", "-hls_list_size", "0", "-hls_playlist_type", "vod",
         str(parts / "film.m3u8")],
        check=True, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    served.folder = parts
    fetching = fetcher()
    url = address + "/film.m3u8"
    fetching.keep(url, "A Film", folder=(naming.ADULT_DIR,))
    ended = waited(fetching, url)
    kept = movies() / naming.ADULT_DIR / "A Film.mp4"
    assert ended["kept_path"] == str(kept) and ended["progress"] == 1.0
    kinds = [(s["codec_type"], s["codec_name"]) for s in convert.streams_of(kept)["streams"]]
    assert kinds == [("video", "h264"), ("audio", "aac")]  # copied, not made again
    assert left_in_cache() == [f"{ended['id']}.json"]
    # One that never ends (a live stream) is refused, with nothing kept.
    live = "\n".join(
        line for line in (parts / "film.m3u8").read_text().splitlines()
        if "ENDLIST" not in line and "PLAYLIST-TYPE" not in line
    )  # fmt: skip
    served.pages["/live"] = Page(live.encode(), "text/plain")  # it says what it is itself
    fetching.keep(address + "/live", "Live")
    refused = waited(fetching, address + "/live")
    assert "live stream" in refused["keep_error"] and refused["kept_path"] is None
    assert sorted(p.name for p in (movies() / naming.ADULT_DIR).iterdir()) == ["A Film.mp4"]


def test_what_isnt_a_film_is_never_kept(site: tuple[Site, str]) -> None:
    served, address = site
    served.pages["/page"] = Page(b"<html>a plugin's page</html>", "text/html")
    served.pages["/noise.mp4"] = Page(os.urandom(20_000), "video/mp4")
    fetching = fetcher()
    fetching.keep(address + "/page", "A Film")
    page = waited(fetching, address + "/page")
    assert "web page" in page["keep_error"]
    fetching.keep(address + "/noise.mp4", "A Film")
    noise = waited(fetching, address + "/noise.mp4")
    assert "isn't a film" in noise["keep_error"]
    assert not movies().exists() or list(movies().iterdir()) == []
    job = fetch.fetch_id(address + "/page")
    assert left_in_cache() in ([], [f"{job}.json"])  # nothing of the noise is left


def test_an_address_that_isnt_there_says_so(site: tuple[Site, str]) -> None:
    _, address = site
    fetching = fetcher()
    fetching.keep(address + "/gone.mp4", "A Film")
    ended = waited(fetching, address + "/gone.mp4")
    assert "HTTP 404" in ended["keep_error"] and LOOPBACK in ended["keep_error"]


def test_a_film_needs_room_on_this_mac(
    site: tuple[Site, str], mp4: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    served, address = site
    served.pages["/a.mp4"] = Page(mp4)
    asked: list[tuple[int, int]] = []

    def no_room(coming: int, size: int, cache: Path, movies: Path) -> bool:
        asked.append((coming, size))
        return False

    monkeypatch.setattr(tools, "room_for", no_room)
    fetching = fetcher()
    fetching.keep(address + "/a.mp4", "A Film")
    ended = waited(fetching, address + "/a.mp4")
    assert "room" in ended["keep_error"] and asked == [(len(mp4), len(mp4))]


def test_youtube_soundcloud_and_anything_not_on_the_web_are_refused(
    opened: rpc.Server,  # noqa: F811
) -> None:
    for gated in ("https://www.youtube.com/watch?v=abc", "https://youtu.be/abc",
                  "https://r3---sn-abc.googlevideo.com/videoplayback?x=1",
                  "https://m.youtube.com/watch", "https://soundcloud.com/a/b",
                  "https://cf-media.sndcdn.com/a.mp3"):  # fmt: skip
        with pytest.raises(fetch.FetchError, match="YouTube or SoundCloud"):
            fetch.check_address(gated)
    for wrong in ("file:///etc/hosts", "ftp://example.com/a.mp4", "not an address", "http://"):
        with pytest.raises(fetch.FetchError, match="web address"):
            fetch.check_address(wrong)
    # In a test nothing leaves this computer.
    with pytest.raises(ReplayMissError):
        fetch.check_address("https://films.example/a.mp4")
    assert fetch.check_address(f"http://{LOOPBACK}:1/a.mp4") == f"http://{LOOPBACK}:1/a.mp4"
    assert fetch.check_address("http://localhost:1/a.mp4")
    assert code(opened, "stream.keep", url="https://youtu.be/abc", title="A") == rpc.USER_ERROR
    assert code(opened, "stream.keep", title="A") == rpc.INVALID_PARAMS


def test_a_download_can_be_stopped_and_carried_on(
    site: tuple[Site, str], mp4: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    served, address = site
    served.pages["/a.mp4"] = Page(mp4)
    url = address + "/a.mp4"
    fetching = fetcher()
    stopped = threading.Event()
    real = fileops.write_fetched_part

    def slowly(*args: Any, **kwargs: Any) -> int:
        written = real(*args, **kwargs)
        if not stopped.is_set():
            stopped.set()
            fetching.stop(url)  # the owner presses Stop after the first part
        return written

    monkeypatch.setattr(fileops, "write_fetched_part", slowly)
    fetching.keep(url, "A Film")
    with pytest.raises(UserError):
        until = time.monotonic() + 10
        while time.monotonic() < until:
            fetching.status(url)  # gone from the list once stopped
            time.sleep(0.02)
    job = fetch.fetch_id(url)
    until = time.monotonic() + 10
    while left_in_cache() != [f"{job}.json", f"{job}.part"] and time.monotonic() < until:
        time.sleep(0.02)
    note = json.loads((cache() / fileops.FETCHED_DIR / f"{job}.json").read_text())
    assert note["have"] == fetch.PART_BYTES  # what arrived stays, noted
    assert not movies().exists() or list(movies().iterdir()) == []
    fetching.keep(url, "A Film")  # Download pressed again
    ended = waited(fetching, url)
    assert Path(ended["kept_path"]).read_bytes() == mp4
    assert served.asked[-1]["range"] == f"bytes={fetch.PART_BYTES}-"


def test_what_sat_unused_for_two_days_is_cleared_and_nothing_else(tmp_path: Path) -> None:
    folder = fileops.make_cached_folder(cache() / fileops.FETCHED_DIR, cache=cache())
    old, new = folder / "s-old.part", folder / "s-new.part"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    long_ago = time.time() - fetch.KEEP_S - 60
    os.utime(old, (long_ago, long_ago))
    assert fetch.sweep(cache()) == 1
    assert sorted(p.name for p in folder.iterdir()) == ["s-new.part"]
    os.utime(new, (long_ago, long_ago))
    assert fetch.sweep(cache(), skip=["s-new.part"]) == 0  # in use: stays
    assert fetch.sweep(tmp_path / "nowhere") == 0


def test_the_engine_stopping_stops_every_download(
    opened: rpc.Server,  # noqa: F811
) -> None:
    stopped: list[bool] = []

    class Fetching:
        movies = movies()

        def stop_all(self) -> None:
            stopped.append(True)

    opened._fetcher = Fetching()  # type: ignore[assignment]
    opened.shutdown()
    assert stopped == [True]


def test_nothing_of_the_address_is_logged(
    site: tuple[Site, str], mp4: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    served, address = site
    served.pages["/a.mp4"] = Page(mp4)
    url = address + "/a.mp4?token=" + hashlib.sha256(b"made up").hexdigest()
    fetching = fetcher()
    with caplog.at_level("DEBUG", logger="musicorg"):
        fetching.keep(url, "A Film")
        waited(fetching, url)
    assert "fetch:" in caplog.text
    assert "token" not in caplog.text and "/a.mp4" not in caplog.text


# ---- the writer: fileops (2026-10-17) ------------------------------------------------------

FILM = b"a made-up film, " * 4096  # 64 KiB


@pytest.fixture
def arriving(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    """The app's cache folder, and its `fetched/` folder in it."""
    here = tmp_path / "cache"
    folder = fileops.make_cached_folder(here / fileops.FETCHED_DIR, cache=here)
    yield here, folder


def test_a_fetched_part_is_written_where_it_belongs(arriving: tuple[Path, Path]) -> None:
    here, folder = arriving
    part = folder / "s-1.part"
    assert fileops.write_fetched_part(part, 0, Connection(FILM[:100]), 100, cache=here) == 100
    # With no length said, everything the answer has.
    assert fileops.write_fetched_part(part, 100, Connection(FILM[100:]), None,
                                      cache=here) == len(FILM) - 100  # fmt: skip
    assert part.read_bytes() == FILM
    if os.name != "nt":
        assert part.stat().st_mode & 0o077 == 0  # readable by this user only


def test_a_fetched_part_cut_off_counts_only_what_arrived(arriving: tuple[Path, Path]) -> None:
    """A crash, the network's or the computer's: what was counted is what's there."""
    here, folder = arriving
    part = folder / "s-1.part"
    assert fileops.write_fetched_part(part, 0, Connection(FILM, fails_after=30), 100,
                                      cache=here) == 30  # fmt: skip
    assert part.read_bytes() == FILM[:30]
    # Bytes beyond what the note counted (the computer stopped before it was saved) are
    # cut off by the next part, which goes where the note says.
    assert fileops.write_fetched_part(part, 20, Connection(b"X" * 5), 5, cache=here) == 5
    assert part.read_bytes() == FILM[:20] + b"X" * 5
    # Told to stop (the owner pressed Stop): it stops after the chunk it's on.
    big = FILM * 40
    written = fileops.write_fetched_part(part, 0, Connection(big), None, cache=here,
                                         going_on=lambda n: False)  # fmt: skip
    assert 0 < written < len(big) and part.stat().st_size == written
    with pytest.raises(UserError):
        fileops.write_fetched_part(part, written + 1, Connection(FILM), 10, cache=here)
    with pytest.raises(ValueError):
        fileops.write_fetched_part(part, -1, Connection(FILM), 10, cache=here)


def test_fetched_parts_are_written_only_in_the_caches_fetched_folder(
    arriving: tuple[Path, Path], tmp_path: Path
) -> None:
    here, folder = arriving
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    uploads = fileops.make_cached_folder(here / fileops.UPLOADS_DIR, cache=here)
    for wrong in (here / "s-1.part", uploads / "s-1.part", elsewhere / "s-1.part",
                  folder / "inside" / "s-1.part", folder / ".." / "s-1.part"):  # fmt: skip
        with pytest.raises(OutsideLibraryError):
            fileops.write_fetched_part(wrong, 0, Connection(FILM), 10, cache=here)
        with pytest.raises(OutsideLibraryError):
            fileops.write_fetched_note(wrong, "{}", cache=here)
    # And an upload's writer never writes here.
    with pytest.raises(OutsideLibraryError):
        fileops.write_upload_part(folder / "s-1.part", 0, Connection(FILM), 10, cache=here)
    for junk in (".hidden", ".DS_Store", "._s-1.part"):
        with pytest.raises(ValueError):
            fileops.write_fetched_part(folder / junk, 0, Connection(FILM), 10, cache=here)
    assert list(elsewhere.iterdir()) == [] and list(uploads.iterdir()) == []
    (folder / "s-2.part").mkdir()
    with pytest.raises(OutsideLibraryError):
        fileops.write_fetched_part(folder / "s-2.part", 0, Connection(FILM), 10, cache=here)
    target = elsewhere / "Precious.mp4"
    target.write_bytes(b"the owner's own")
    try:
        (folder / "s-3.part").symlink_to(target)
    except OSError:
        return  # Windows without the right to make links: there's nothing to follow
    with pytest.raises(OutsideLibraryError):
        fileops.write_fetched_part(folder / "s-3.part", 0, Connection(FILM), 10, cache=here)
    with pytest.raises(OutsideLibraryError):
        fileops.write_fetched_note(folder / "s-3.part", "{}", cache=here)
    assert target.read_bytes() == b"the owner's own"


def test_a_fetched_note_is_replaced_whole(
    arriving: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    here, folder = arriving
    note = folder / "s-1.json"
    fileops.write_fetched_note(note, '{"have": 1}', cache=here)
    fileops.write_fetched_note(note, '{"have": 2}', cache=here)
    assert json.loads(note.read_text(encoding="utf-8")) == {"have": 2}

    def fails(src: Path, dst: Path) -> None:
        raise OSError(5, "the disk went away")

    monkeypatch.setattr(fileops, "_replace", fails)
    with pytest.raises(fileops.FileOperationError):
        fileops.write_fetched_note(note, '{"have": 3}', cache=here)
    assert json.loads(note.read_text(encoding="utf-8")) == {"have": 2}  # the old one, whole
    assert [p.name for p in folder.iterdir()] == ["s-1.json"]  # and no temp file left
