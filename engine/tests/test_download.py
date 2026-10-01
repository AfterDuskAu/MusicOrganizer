"""youtube.download_audio with a stand-in for yt-dlp: the options (format 140 only, no
postprocessors, everything inside the staging folder), progress, what each of yt-dlp's
error messages becomes, and the guards. The real download is the `live` test at the end."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yt_dlp

from musicorg import fileops, tools, youtube
from musicorg.errors import (
    DownloadError,
    FormatUnavailableError,
    ReplayMissError,
    VideoUnavailableError,
    YouTubeError,
    YouTubeRefusedError,
)
from musicorg.library import Library
from musicorg.youtube import RateLimiter

VIDEO = "dQw4w9WgXcQ"
# Alessia Cara, "Here": official audio (MUSIC_VIDEO_TYPE_ATV) in the recorded searches.
LIVE_VIDEO = "dI5JaT6gjvw"


class FakeYoutubeDL:
    """Writes `<id>.m4a.part`, reports progress, renames it to `<id>.m4a`, like yt-dlp."""

    calls: list[dict[str, Any]] = []

    def __init__(self, opts: dict[str, Any], error: str | None = None) -> None:
        self.opts = opts
        self.error = error

    def __enter__(self) -> FakeYoutubeDL:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
        FakeYoutubeDL.calls.append(self.opts)
        if self.error:
            raise yt_dlp.utils.DownloadError(self.error)
        video_id = url.rsplit("v=", 1)[1]
        home = Path(self.opts["paths"]["home"])
        name = self.opts["outtmpl"]["default"] % {"id": video_id, "ext": "m4a"}
        part = home / f"{name}.part"
        part.write_bytes(b"\0" * 4000)
        for hook in self.opts["progress_hooks"]:
            hook({"status": "downloading", "downloaded_bytes": 2000, "total_bytes": 4000})
        final = home / name
        part.rename(final)
        for hook in self.opts["progress_hooks"]:
            hook({"status": "finished", "downloaded_bytes": 4000, "total_bytes": 4000})
        return {
            "id": video_id,
            "format_id": "140",
            "ext": "m4a",
            "acodec": "mp4a.40.2",
            "requested_downloads": [{"filepath": str(final)}],
        }


@pytest.fixture
def fake_ydl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[[str | None], None]:
    """Stand in for yt-dlp; call it with an error message to make downloads fail."""
    FakeYoutubeDL.calls = []
    monkeypatch.setattr(tools, "require", lambda name, config=None: tmp_path / "bin" / name)
    monkeypatch.setattr(youtube, "_LIMITER", RateLimiter(0, 0, sleep=lambda s: None))

    def use(error: str | None = None) -> None:
        monkeypatch.setattr(youtube, "_make_ydl", lambda opts: FakeYoutubeDL(opts, error))

    use()
    return use


@pytest.fixture
def dest(lib: Library) -> Path:
    b = fileops.open_batch(lib, "replace")
    return fileops.stage_dir(b, "job-1")


def test_downloads_format_140_into_the_staging_folder_only(
    fake_ydl: Callable[..., None], dest: Path, lib: Library
) -> None:
    progress: list[tuple[int, int | None]] = []
    path, info = youtube.download_audio(VIDEO, dest, progress=lambda d, t: progress.append((d, t)))
    assert path == (dest / f"{VIDEO}.m4a").resolve()
    assert info["format_id"] == "140"
    assert progress == [(2000, 4000), (4000, 4000)]
    # Nothing anywhere else in the library, and no .part left.
    everything = [p for p in lib.root.rglob("*") if p.is_file()]
    written = [p for p in everything if lib.paths.staging in p.parents]
    assert written == [path]

    opts = FakeYoutubeDL.calls[0]
    assert opts["format"] == "140"
    assert opts["postprocessors"] == []
    assert opts["paths"] == {"home": str(dest), "temp": str(dest)}
    assert opts["outtmpl"] == {"default": "%(id)s.%(ext)s"}
    assert opts["noplaylist"] is True and opts["quiet"] is True and opts["noprogress"] is True
    assert opts["js_runtimes"]["deno"]["path"].endswith("deno")
    assert opts["ffmpeg_location"].endswith("ffmpeg")
    assert Path(opts["cachedir"]).name == "yt-dlp"
    assert opts["logger"] is not None


def test_goes_through_the_rate_limiter(fake_ydl: Callable[..., None], dest: Path) -> None:
    before = youtube.limiter().requests
    youtube.download_audio(VIDEO, dest)
    assert youtube.limiter().requests == before + 1


# What yt-dlp says → what the queue gets. The research's lesson: "Sign in to confirm your
# age" is one video, not a block.
@pytest.mark.parametrize(
    ("message", "expected", "network"),
    [
        ("ERROR: [youtube] x: Sign in to confirm you’re not a bot. Use --cookies",
         YouTubeRefusedError, None),
        ("ERROR: [youtube] x: Sign in to confirm you're not a bot", YouTubeRefusedError, None),
        ("ERROR: unable to download video data: HTTP Error 429: Too Many Requests",
         YouTubeRefusedError, None),
        ("ERROR: [youtube] x: Sign in to confirm your age. This video may be inappropriate "
         "for some users.", VideoUnavailableError, False),
        ("ERROR: [youtube] x: Private video. Sign in if you've been granted access",
         VideoUnavailableError, False),
        ("ERROR: [youtube] x: Video unavailable. The uploader has not made this video "
         "available in your country", VideoUnavailableError, False),
        ("ERROR: [youtube] x: Join this channel to get access to members-only content",
         VideoUnavailableError, False),
        ("ERROR: [youtube] x: Requested format is not available. Use --list-formats",
         FormatUnavailableError, False),
        ("ERROR: unable to download video data: HTTP Error 403: Forbidden", DownloadError, True),
        ("ERROR: [youtube] x: Read timed out.", DownloadError, True),
        ("ERROR: The downloaded file is empty", DownloadError, False),
        ("ERROR: something nobody has seen before", DownloadError, False),
    ],
)  # fmt: skip
def test_error_messages(
    fake_ydl: Callable[..., None],
    dest: Path,
    message: str,
    expected: type[Exception],
    network: bool | None,
) -> None:
    fake_ydl(message)
    with pytest.raises(expected) as err:
        youtube.download_audio(VIDEO, dest)
    assert type(err.value) is expected
    if network is not None:
        assert isinstance(err.value, DownloadError)
        assert err.value.network is network
    assert "ERROR:" not in str(err.value)  # plain English, not yt-dlp's raw line
    assert not any(dest.iterdir())


def test_refuses_a_folder_outside_staging(fake_ydl: Callable[..., None], tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="_Staging"):
        youtube.download_audio(VIDEO, tmp_path)
    assert FakeYoutubeDL.calls == []


def test_refuses_a_bad_video_id(fake_ydl: Callable[..., None], dest: Path) -> None:
    with pytest.raises(YouTubeError):
        youtube.download_audio("../../etc", dest)


def test_a_download_that_left_no_file(
    fake_ydl: Callable[..., None], dest: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Nothing(FakeYoutubeDL):
        def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
            return {"id": VIDEO, "format_id": "140"}

    monkeypatch.setattr(youtube, "_make_ydl", lambda opts: Nothing(opts))
    with pytest.raises(DownloadError, match="empty"):
        youtube.download_audio(VIDEO, dest)


def test_replay_mode_never_downloads(dest: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(youtube, "_LIMITER", RateLimiter(0, 0, sleep=lambda s: None))
    with pytest.raises(ReplayMissError):
        youtube.download_audio(VIDEO, dest)


@pytest.mark.live
def test_live_download(lib: Library) -> None:
    """Run by hand once (MUSICORG_LIVE=1): one real song into a scratch library's staging."""
    from musicorg import tags

    b = fileops.open_batch(lib, "replace")
    folder = fileops.stage_dir(b, "job-live")
    path, info = youtube.download_audio(LIVE_VIDEO, folder)
    assert info["format_id"] == "140"
    probe = tags.probe(path)
    assert probe.codec == "aac"
    assert probe.bitrate_kbps is not None and 100 <= probe.bitrate_kbps <= 160
    outside = [p for p in lib.root.rglob("*") if p.is_file() and lib.paths.staging not in p.parents]
    assert all(lib.paths.engine in p.parents for p in outside)
    assert [p for p in folder.iterdir()] == [path]


# ---- stream: where the app plays a song from (v0.2) ------------------------------------


class StreamYDL:
    info: Any = {}
    calls: list[tuple[str, bool]] = []

    def __init__(self, opts: dict[str, Any]) -> None:
        self.opts = opts

    def __enter__(self) -> StreamYDL:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = True) -> Any:
        StreamYDL.calls.append((url, download))
        if isinstance(StreamYDL.info, Exception):
            raise StreamYDL.info
        return StreamYDL.info


def test_stream_gives_an_address_and_downloads_nothing(
    fake_ydl: Callable[[str | None], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    StreamYDL.calls = []
    StreamYDL.info = {"url": "https://example.invalid/audio", "format_id": "140",
                      "duration": 187, "http_headers": {"User-Agent": "x"}}  # fmt: skip
    monkeypatch.setattr(youtube, "_make_ydl", StreamYDL)
    found = youtube.stream("abcdefghijk")
    assert found == youtube.Stream("https://example.invalid/audio", {"User-Agent": "x"}, 187.0)
    assert StreamYDL.calls == [("https://www.youtube.com/watch?v=abcdefghijk", False)]

    with pytest.raises(YouTubeError):
        youtube.stream("not an id")
    StreamYDL.info = {"url": "https://example.invalid/audio", "format_id": "251"}
    with pytest.raises(FormatUnavailableError):
        youtube.stream("abcdefghijk")
    StreamYDL.info = {"format_id": "140"}
    with pytest.raises(DownloadError):
        youtube.stream("abcdefghijk")
    StreamYDL.info = yt_dlp.utils.DownloadError("Video unavailable")
    with pytest.raises(VideoUnavailableError):
        youtube.stream("abcdefghijk")


def test_video_lists_the_pictures_the_app_can_show(
    fake_ydl: Callable[[str | None], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    def picture(format_id: str, height: int, **more: Any) -> dict[str, Any]:
        return {"format_id": format_id, "ext": "mp4", "protocol": "https", "acodec": "none",
                "vcodec": "avc1.640028", "height": height, "fps": 24, "tbr": 1000.0,
                "url": f"https://example.invalid/{format_id}", **more}  # fmt: skip

    StreamYDL.calls = []
    StreamYDL.info = {
        "url": "https://example.invalid/audio", "format_id": "140", "duration": 245,
        "http_headers": {"User-Agent": "x"},
        "formats": [
            {"format_id": "140", "ext": "m4a", "protocol": "https", "vcodec": "none",
             "acodec": "mp4a.40.2", "url": "https://example.invalid/audio"},
            picture("160", 144),
            picture("137", 1080),
            picture("137-low", 1080, tbr=500.0),  # the same size, a thinner copy
            picture("299", 1080, fps=60),
            picture("136", 720),
            picture("18", 360, acodec="mp4a.40.2"),  # picture and sound in one: not used
            picture("270", 1080, protocol="m3u8_native"),
            picture("248", 1080, ext="webm", vcodec="vp9"),
            picture("399", 1080, vcodec="av01.0.08M.0"),
            picture("313", 2160, ext="webm", vcodec="vp9"),  # 4K is never H.264
            picture("bad", 480, url=None),
        ],
    }  # fmt: skip
    monkeypatch.setattr(youtube, "_make_ydl", StreamYDL)
    found = youtube.video("abcdefghijk")
    assert found.video_id == "abcdefghijk"
    assert found.audio == youtube.Stream(
        "https://example.invalid/audio", {"User-Agent": "x"}, 245.0
    )
    assert [(q.label, q.url.rsplit("/", 1)[1]) for q in found.qualities] == [
        ("1080p60", "299"), ("1080p", "137"), ("720p", "136"), ("144p", "160"),
    ]  # fmt: skip
    assert StreamYDL.calls == [("https://www.youtube.com/watch?v=abcdefghijk", False)]

    StreamYDL.info = {"url": "https://example.invalid/audio", "format_id": "140"}
    assert youtube.video("abcdefghijk").qualities == ()
    with pytest.raises(YouTubeError):
        youtube.video("not an id")
    StreamYDL.info = None
    with pytest.raises(DownloadError):
        youtube.video("abcdefghijk")


# ---- download_video: a video saved whole (v0.2) ----------------------------------------


class VideoYDL(FakeYoutubeDL):
    """yt-dlp fetching a picture and a sound and leaving one joined `<id>.mp4`."""

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
        FakeYoutubeDL.calls.append(self.opts)
        if self.error:
            raise yt_dlp.utils.DownloadError(self.error)
        video_id = url.rsplit("v=", 1)[1]
        final = Path(self.opts["paths"]["home"]) / f"{video_id}.mp4"
        final.write_bytes(b"\0" * 9000)
        for hook in self.opts["progress_hooks"]:
            hook({"status": "finished", "downloaded_bytes": 9000, "total_bytes": 9000})
        return {"id": video_id, "format_id": "136+140", "ext": "mp4",
                "requested_downloads": [{"filepath": str(final)}]}  # fmt: skip


def test_download_video_asks_for_one_picture_size_joined_to_format_140(
    fake_ydl: Callable[..., None], dest: Path, lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(youtube, "_make_ydl", lambda opts: VideoYDL(opts))
    before = youtube.limiter().requests
    path, info = youtube.download_video(VIDEO, dest, height=720)
    assert path == (dest / f"{VIDEO}.mp4").resolve()
    assert info["format_id"] == "136+140"
    assert youtube.limiter().requests == before + 1
    written = [p for p in lib.root.rglob("*") if p.is_file() and lib.paths.staging in p.parents]
    assert written == [path]  # nowhere but the staging folder

    opts = FakeYoutubeDL.calls[0]
    # H.264 in MP4, served whole, at exactly this height; never a "best available".
    assert opts["format"] == (
        "bestvideo[vcodec^=avc1][ext=mp4][protocol=https][height=720][fps<=?30]+140"
    )
    assert opts["merge_output_format"] == "mp4"
    assert opts["postprocessors"] == []  # joined by yt-dlp's own merger: no converting
    assert opts["paths"] == {"home": str(dest), "temp": str(dest)}
    assert opts["ffmpeg_location"].endswith("ffmpeg")

    youtube.download_video(VIDEO, dest, height=1080, fps=60)
    assert "[height=1080][fps>30]+140" in FakeYoutubeDL.calls[1]["format"]


def test_download_video_refuses_what_it_cant_do(
    fake_ydl: Callable[..., None], dest: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for height in (0, 99, True, "720", 5000):
        with pytest.raises(YouTubeError, match="picture height"):
            youtube.download_video(VIDEO, dest, height=height)  # type: ignore[arg-type]
    with pytest.raises(YouTubeError):
        youtube.download_video("not an id", dest, height=720)
    with pytest.raises(ValueError, match="_Staging"):
        youtube.download_video(VIDEO, tmp_path, height=720)
    monkeypatch.setattr(
        youtube, "_make_ydl",
        lambda opts: VideoYDL(opts, "ERROR: [youtube] x: Requested format is not available"),
    )  # fmt: skip
    with pytest.raises(FormatUnavailableError, match="720p picture"):
        youtube.download_video(VIDEO, dest, height=720)
    assert len(FakeYoutubeDL.calls) == 1  # asked once: nothing else is tried in its place
