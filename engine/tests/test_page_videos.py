"""The videos on any web page, and downloading one (2026-10-17, the owner: "add in a "URL"
which would get the video from the page from any website, shows which videos are on the
page, once selected and quality are selected. use yt-dlp engine to download").

The pages are served by a little web server on this computer's own loopback address,
started by the test (test_fetch's), and read by the real yt-dlp: in replay mode, which
every test runs in, nothing but an address on this computer is ever read. The videos are
made here by ffmpeg. The folders and the app's cache are the test's own (MUSICORG_HOME).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import LOOPBACK
from test_fetch import Page, Site, cache, film, left_in_cache, movies, serve
from test_rpc import Capture, code, opened, out, result, root, server  # noqa: F401  (fixtures)

from musicorg import config, fetch, rpc, tools, youtube
from musicorg.errors import DownloadError, ReplayMissError, YouTubeError

# A page on this computer that nothing answers (port 9, "discard"): for what's refused or
# stood in for before anything is asked.
NOWHERE = f"http://{LOOPBACK}:9"


@pytest.fixture(scope="module")
def clips(tmp_path_factory: pytest.TempPathFactory) -> dict[str, bytes]:
    folder = tmp_path_factory.mktemp("clips")
    return {"big": film(folder / "big.mp4"), "small": film(folder / "small.mp4")}


ONE = """<html><head><title>One clip</title><meta property="og:title" content="A Test Clip">
</head><body><video controls><source src="big.mp4" type="video/mp4">
<source src="small.mp4" type="video/mp4"></video></body></html>"""

TWO = """<html><head><title>Two clips</title></head><body>
<iframe src="https://www.youtube.com/embed/aqz-KE-bpKQ"></iframe>
<video controls src="big.mp4"></video><p>and</p><video controls src="small.mp4"></video>
</body></html>"""


@pytest.fixture
def site(clips: dict[str, bytes]) -> Iterator[tuple[Site, str]]:
    made = Site()
    made.pages["/one.html"] = Page(ONE.encode(), "text/html", None)
    made.pages["/two.html"] = Page(TWO.encode(), "text/html", None)
    made.pages["/big.mp4"] = Page(clips["big"])
    made.pages["/small.mp4"] = Page(clips["small"])
    for address in serve(made):
        yield made, address


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


# ---- addresses ---------------------------------------------------------------------------


def test_only_a_web_address_and_in_tests_only_this_computers() -> None:
    for wrong in ("", "not an address", "ftp://example.org/a.mp4", "file:///etc/hosts"):
        with pytest.raises(YouTubeError):
            youtube.page_address(wrong)
    with pytest.raises(ReplayMissError):
        youtube.page_address("https://example.org/videos")
    assert youtube.page_address(f" {NOWHERE}/a ") == f"{NOWHERE}/a"


def test_a_youtube_video_is_known_by_its_address() -> None:
    for link in (
        "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
        "https://youtu.be/aqz-KE-bpKQ?t=3",
        "https://www.youtube.com/embed/aqz-KE-bpKQ",
        "https://m.youtube.com/shorts/aqz-KE-bpKQ",
        "https://music.youtube.com/watch?list=x&v=aqz-KE-bpKQ",
    ):
        assert youtube.youtube_id_in(link) == "aqz-KE-bpKQ", link
    assert youtube.youtube_id_in("https://www.youtube.com/@channel") is None
    assert youtube.youtube_id_in("https://example.org/watch?v=aqz-KE-bpKQ") is None


def test_soundcloud_is_never_read_here() -> None:
    answer = youtube.page_videos("https://soundcloud.com/someone/a-track")
    assert answer["videos"] == [] and "Music Finder" in answer["note"]


def test_a_youtube_video_is_one_look_up_for_the_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def looked_up(video_id: str, **_: Any) -> dict[str, Any]:
        asked.append(video_id)
        return {"title": "A Video", "duration": 61, "thumbnail": "https://i.example/x.jpg"}

    monkeypatch.setattr(youtube, "_looked_up", looked_up)
    answer = youtube.page_videos("https://youtu.be/aqz-KE-bpKQ")
    assert asked == ["aqz-KE-bpKQ"]
    [found] = answer["videos"]
    assert found["service"] == "youtube" and found["video_id"] == "aqz-KE-bpKQ"
    assert found["title"] == "A Video" and found["qualities"] is None
    assert youtube.page_videos("https://www.youtube.com/@channel")["videos"] == []
    assert asked == ["aqz-KE-bpKQ"]


# ---- what's on a page ------------------------------------------------------------------------


def test_the_videos_on_a_page_are_listed_and_a_youtube_player_only_named(
    site: tuple[Site, str],
) -> None:
    made, address = site
    answer = youtube.page_videos(f"{address}/two.html")
    assert answer["title"] == "Two clips"
    first, big, small = answer["videos"]
    assert first["service"] == "youtube" and first["video_id"] == "aqz-KE-bpKQ"
    assert first["qualities"] is None and first["item"] is None
    assert (big["item"], small["item"]) == (2, 3)
    assert big["url"] == small["url"] == f"{address}/two.html"
    assert big["service"] is None and big["site"] == LOOPBACK
    assert [q["label"] for q in big["qualities"]] == ["MP4"]
    # The page was read; none of its videos was fetched, and YouTube was never asked.
    assert [a["path"] for a in made.asked] == ["/two.html"]


def test_one_video_with_two_files_offers_both(site: tuple[Site, str]) -> None:
    _, address = site
    answer = youtube.page_videos(f"{address}/one.html")
    [found] = answer["videos"]
    assert found["title"].startswith("A Test Clip") and found["item"] is None
    assert [q["label"] for q in found["qualities"]] == ["MP4", "MP4 (2)"]


def test_a_page_with_no_video_says_so(site: tuple[Site, str]) -> None:
    made, address = site
    made.pages["/none.html"] = Page(b"<html><title>Words</title><p>Nothing</p></html>", "text/html")
    with pytest.raises(YouTubeError, match="No video"):
        youtube.page_videos(f"{address}/none.html")


def test_a_site_that_turns_programs_away_isnt_said_to_want_a_sign_in() -> None:
    """2026-10-18: tesla.com answered 403 to the page itself (its guard lets only web
    browsers in), and the app said the video was only for someone signed in."""
    page = "https://www.example.org/support/videos/watch/a-video"

    def said(text: str) -> str:
        return str(youtube._page_problem(Exception(text), page))

    turned = said(
        f"ERROR: [generic] a-video: Unable to download webpage: HTTP Error 403: Forbidden "
        f"(caused by <HTTPError 403: Forbidden>) {page}"
    )
    assert "turned Bear Media away" in turned and "signed in" not in turned
    assert page not in turned  # never the address itself
    assert "turned" in said("ERROR: unable to download video data: HTTP Error 403: Forbidden")
    assert "signed in" in said("ERROR: [vimeo] 1: This video is private. Use --cookies to sign in")
    assert "signed in" in said("ERROR: HTTP Error 401: Unauthorized")
    assert "any more" in said("ERROR: HTTP Error 404: Not Found")


def test_a_site_closed_to_this_connection_says_so_not_that_it_cant_be_reached() -> None:
    """2026-10-18: a site answered 410 ("gone") to every page with the owner's VPN off,
    and the app said it "couldn't be reached just now", with yt-dlp's own words after."""
    page = "https://www.example.org/view_video.php?viewkey=0123456789abc"

    def said(text: str) -> DownloadError:
        found = youtube._page_problem(Exception(text), page)
        assert isinstance(found, DownloadError)
        return found

    gone = said(
        "ERROR: [Example] 0123456789abc: Unable to download webpage: HTTP Error 410: Gone "
        "(caused by <HTTPError 410: Gone>)"
    )
    assert "won't show its pages to this connection (HTTP 410)" in str(gone)
    assert "reached" not in str(gone) and "0123456789abc" not in str(gone)
    assert not gone.network  # the site answered: nothing of the network's to retry
    assert "HTTP 451" in str(said("ERROR: HTTP Error 451: Unavailable For Legal Reasons"))
    assert "too many requests" in str(said("ERROR: Unable to download webpage: HTTP Error 429"))
    assert "trouble" in str(said("ERROR: Unable to download webpage: HTTP Error 503"))
    assert "refused that page (HTTP 400)" in str(said("ERROR: HTTP Error 400: Bad Request"))
    # A connection that really failed is still said to be one.
    lost = said("ERROR: Unable to download webpage: <urlopen error timed out>")
    assert "couldn't be reached" in str(lost) and lost.network


def test_qualities_sharpest_first_with_sound_beside_a_picture_alone() -> None:
    info = {
        "formats": [
            {"format_id": "sb0", "ext": "mhtml", "vcodec": "none", "format_note": "storyboard"},
            {"format_id": "a1", "vcodec": "none", "acodec": "mp4a", "tbr": 128, "filesize": 1000},
            {"format_id": "v360", "vcodec": "avc1", "acodec": "none", "height": 360, "tbr": 500,
             "filesize": 5000},
            {"format_id": "v720", "vcodec": "avc1", "acodec": "none", "height": 720, "tbr": 1500,
             "filesize_approx": 9000},
            {"format_id": "v720vp9", "vcodec": "vp9", "acodec": "none", "height": 720, "tbr": 1600},
            {"format_id": "v1080", "vcodec": "avc1", "acodec": "none", "height": 1080, "fps": 60},
            {"format_id": "hls-480p", "vcodec": "avc1", "acodec": "mp4a", "protocol": "m3u8"},
        ]
    }  # fmt: skip
    found = youtube.page_qualities(info)
    assert [q["label"] for q in found] == ["1080p60", "720p", "480p", "360p"]
    assert found[0]["size"] is None  # its picture's size isn't known
    assert found[1]["size"] == 10_000  # the picture's and the sound's
    assert found[1]["id"].startswith("v720+bestaudio/v720/")  # H.264 chosen over VP9
    assert found[2]["id"].startswith("hls-480p/")  # sound in it already
    assert "[height<=360]" in found[3]["id"]
    for quality in found:
        assert youtube.check_page_quality(quality["id"]) == quality["id"]
    with pytest.raises(YouTubeError):
        youtube.check_page_quality("best; rm -rf /")
    assert youtube.page_qualities({"url": "http://x/y.mp4"})[0]["label"] == "As it is"
    assert youtube.page_qualities({}) == []


# ---- downloading one -------------------------------------------------------------------------


def test_a_video_from_a_page_is_kept_in_the_videos_folder(
    site: tuple[Site, str], clips: dict[str, bytes]
) -> None:
    _, address = site
    page = f"{address}/two.html"
    small = youtube.page_videos(page)["videos"][2]
    quality = small["qualities"][0]["id"]
    fetching = fetcher()
    started = fetching.keep_page(page, small["item"], quality, "A Small Clip", to_videos=True)
    status = waited(fetching, started["id"])
    assert status["keep_error"] is None, status
    kept = Path(status["kept_path"])
    assert kept == videos_folder() / "A Small Clip.mp4"
    assert kept.read_bytes() == clips["small"]
    assert status["progress"] == 1.0
    # Nothing of it is left in the cache but the note that it was kept.
    assert left_in_cache() == [f"{started['id']}.json"]
    # Asked again within its two days, it's kept already: nothing is fetched.
    again = fetching.keep_page(page, small["item"], quality, "A Small Clip", to_videos=True)
    assert again["kept_path"] == str(kept) and not again["keeping"]


def test_a_video_from_a_page_can_go_in_the_18_folder(
    site: tuple[Site, str], clips: dict[str, bytes]
) -> None:
    _, address = site
    found = youtube.page_videos(f"{address}/one.html")["videos"][0]
    fetching = fetcher()
    started = fetching.keep_page(
        found["url"], None, found["qualities"][1]["id"], "A Film (2001)", folder=("18+",)
    )
    status = waited(fetching, started["id"])
    assert status["kept_path"] == str(movies() / "18+" / "A Film (2001).mp4")
    assert Path(status["kept_path"]).read_bytes() == clips["small"]


def test_youtube_and_soundcloud_are_never_downloaded_here(tmp_path: Path) -> None:
    fetching = fetcher()
    for address in ("https://www.youtube.com/watch?v=aqz-KE-bpKQ", "https://soundcloud.com/a/b"):
        with pytest.raises(fetch.FetchError):
            fetching.keep_page(address, None, "best", "Nope")
    work = cache() / "fetched" / "x.page"
    work.mkdir(parents=True)
    with pytest.raises(YouTubeError):
        youtube.download_page_video("https://youtu.be/aqz-KE-bpKQ", None, "best", work)
    with pytest.raises(ValueError):
        youtube.download_page_video(f"{NOWHERE}/a", None, "best", tmp_path)


def test_a_page_download_stopped_leaves_what_came_for_next_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    began = []

    def slow(url: str, item: Any, quality: str, dest: Path, **hooks: Any) -> Path:
        (dest / "video.mp4.part").write_bytes(b"some of it")
        hooks["progress"](10, 100)
        began.append(dest)
        while not hooks["should_stop"]():
            time.sleep(0.01)
        raise youtube.PageStopped("stopped")

    monkeypatch.setattr(youtube, "download_page_video", slow)
    fetching = fetcher()
    started = fetching.keep_page(f"{NOWHERE}/page", 2, "best", "A Clip")
    until = time.monotonic() + 10
    while not began and time.monotonic() < until:
        time.sleep(0.01)
    assert fetching.status(started["id"])["progress"] == 0.1
    fetching.stop(started["id"])
    time.sleep(0.2)
    assert (began[0] / "video.mp4.part").read_bytes() == b"some of it"
    assert not videos_folder().exists() or not any(videos_folder().iterdir())


def test_a_page_download_with_no_room_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    def big(url: str, item: Any, quality: str, dest: Path, **hooks: Any) -> Path:
        hooks["progress"](0, 10**12)
        assert hooks["should_stop"]()
        raise youtube.PageStopped("stopped")

    monkeypatch.setattr(youtube, "download_page_video", big)
    monkeypatch.setattr(tools, "room_for", lambda *a: False)
    fetching = fetcher()
    started = fetching.keep_page(f"{NOWHERE}/page", None, "best", "Huge")
    assert "room" in waited(fetching, started["id"])["keep_error"]


# ---- page.videos and page.keep -----------------------------------------------------------------


def test_page_methods_end_to_end(
    opened: rpc.Server,  # noqa: F811
    site: tuple[Site, str],
    clips: dict[str, bytes],
) -> None:
    _, address = site
    result(opened, "kids.set", on=False)
    page = f"{address}/two.html"
    answer = result(opened, "page.videos", url=page)
    big = answer["videos"][1]
    quality = big["qualities"][0]["id"]
    which = {"url": page, "item": big["item"], "quality": quality}
    started = result(opened, "page.keep", title="Big Clip", **which)
    assert started["id"] == fetch.page_id(page, big["item"], quality)
    until = time.monotonic() + 60
    while (status := result(opened, "stream.status", **which))["keeping"]:
        assert time.monotonic() < until
        time.sleep(0.05)
    assert status["kept_path"] == str(config.media_folders()["media"] / "Big Clip.mp4")
    assert Path(status["kept_path"]).read_bytes() == clips["big"]
    assert result(opened, "stream.stop", **which) == {}
    assert {"page.videos", "media.move"} <= rpc.SLOW_METHODS
    # Filed where the owner chose, as a move files one.
    assert code(opened, "page.keep", title="X", kind="music", **which) == rpc.INVALID_PARAMS
    assert code(opened, "page.keep", title="  ", **which) == rpc.INVALID_PARAMS


def test_no_page_in_a_childs_profile_and_nothing_for_adults_before_its_said(
    opened: rpc.Server,  # noqa: F811
) -> None:
    which = {"url": f"{NOWHERE}/page", "quality": "best", "title": "A"}
    # The app hasn't said which profile this is: as in a child's, nothing for adults.
    assert code(opened, "page.keep", kind="adult", **which) == rpc.USER_ERROR
    result(opened, "kids.set", on=True)
    assert code(opened, "page.videos", url=f"{NOWHERE}/page") == rpc.USER_ERROR
    assert code(opened, "page.keep", **which) == rpc.USER_ERROR
