"""SoundCloud, the second place music comes from (2026-10-12): a search, a track played,
a track downloaded and left in the library's one format. Never the network: a search is
replayed from a recording, the downloader is a stand-in, and the conversion is yt-dlp's
own, run on a file made here."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yt_dlp
from conftest import AudioFixtures, require_tool
from test_download import FakeYoutubeDL, StreamYDL, fake_ydl  # noqa: F401  (a fixture)

from musicorg import fileops, tags, tools, youtube
from musicorg.errors import (
    DownloadError,
    FormatUnavailableError,
    VideoUnavailableError,
    YouTubeError,
    YouTubeRefusedError,
)
from musicorg.library import Library
from musicorg.youtube import RateLimiter

TRACK = "sc:1398629833"


def test_a_soundcloud_tracks_id_is_told_from_a_videos() -> None:
    assert youtube.is_soundcloud(TRACK)
    for other in ("dQw4w9WgXcQ", "sc:", "sc:12ab", "SC:123", "sc:1 ", 123, None):
        assert not youtube.is_soundcloud(other)
    assert youtube._address(TRACK) == "https://api.soundcloud.com/tracks/1398629833"
    assert youtube._address("dQw4w9WgXcQ") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    with pytest.raises(YouTubeError):
        youtube._address("https://soundcloud.com/someone/a-track")


def test_a_search_gives_tracks_in_the_candidate_shape() -> None:
    found = youtube.soundcloud_search("creative  commons piano", 5)  # replayed
    assert len(found) == 5
    first = found[0]
    assert first.video_id == TRACK
    assert first.title.startswith("Sad Emotional Piano Loop")
    assert first.artists == ("Soundset [Creative Commons Music]",)
    assert first.duration_s == 589
    assert first.thumbnail is not None and first.thumbnail.endswith("-t500x500.jpg")
    # SoundCloud has no albums and marks nothing explicit or clean.
    assert (first.album, first.album_browse_id, first.is_explicit, first.video_type) == (
        None, None, None, None,
    )  # fmt: skip
    assert first.plays is not None and first.year is not None
    assert all(youtube.is_soundcloud(c.video_id) for c in found)
    with pytest.raises(YouTubeError):
        youtube.soundcloud_search("   ")


def test_youtube_music_isnt_asked_about_a_soundcloud_track() -> None:
    # In replay mode a request that wasn't recorded fails: neither of these makes one.
    assert youtube.get_track(TRACK) is None
    assert youtube.get_lyrics(TRACK) is None


def test_a_count_is_written_short() -> None:
    assert [youtube._count(n) for n in (738, 1708, 24_000_000, 1_250_000, 3_000_000_000)] == [
        "738", "1.7K", "24M", "1.2M", "3B",
    ]  # fmt: skip


@pytest.fixture
def dest(lib: Library) -> Path:
    return fileops.stage_dir(fileops.open_batch(lib, "download"), "job-1")


class SoundCloudYDL(FakeYoutubeDL):
    """yt-dlp's stand-in for a SoundCloud track: the file is named by the track's number
    and, after the conversion asked for, it's an `.m4a`."""

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
        FakeYoutubeDL.calls.append({**self.opts, "url": url})
        number = url.rsplit("/", 1)[1]
        final = Path(self.opts["paths"]["home"]) / f"{number}.m4a"
        final.write_bytes(b"\0" * 4000)
        return {"id": number, "format_id": "http_mp3_1_0", "ext": "m4a",
                "requested_downloads": [{"filepath": str(final)}]}  # fmt: skip


def test_a_download_asks_for_soundclouds_sound_and_the_librarys_format(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dest: Path, lib: Library
) -> None:
    FakeYoutubeDL.calls = []
    monkeypatch.setattr(tools, "require", lambda name, config=None: tmp_path / "bin" / name)
    monkeypatch.setattr(youtube, "_LIMITER", RateLimiter(0, 0, sleep=lambda s: None))
    monkeypatch.setattr(youtube, "_make_ydl", lambda opts: SoundCloudYDL(opts))

    path, info = youtube.download_audio(TRACK, dest)
    assert path == (dest / "1398629833.m4a").resolve()
    assert info["format_id"] == "http_mp3_1_0"  # what SoundCloud delivered, as it was
    (opts,) = FakeYoutubeDL.calls
    assert opts["url"] == "https://api.soundcloud.com/tracks/1398629833"
    # Its own AAC first, then its MP3; never format 140, which is YouTube's.
    assert opts["format"] == youtube.SOUNDCLOUD_SOUND
    assert opts["format"].startswith("bestaudio[acodec^=mp4a]")
    assert opts["postprocessors"] == [
        {"key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "160"}
    ]
    # Into the staging folder only, like every download.
    assert opts["paths"] == {"home": str(dest), "temp": str(dest)}
    written = [p for p in lib.root.rglob("*") if p.is_file() and lib.paths.staging in p.parents]
    assert written == [path]


def test_a_youtube_download_is_as_it_was(
    fake_ydl: Callable[..., None],  # noqa: F811
    dest: Path,
) -> None:
    youtube.download_audio("dQw4w9WgXcQ", dest)
    opts = FakeYoutubeDL.calls[0]
    assert (opts["format"], opts["postprocessors"]) == ("140", [])


def convert_here(source: Path, dest: Path) -> tuple[Path, dict[str, Any]]:
    """yt-dlp itself, with the options a SoundCloud download gets, given a file on this
    computer in SoundCloud's place: the conversion is the installed yt-dlp's own."""
    require_tool("deno")
    opts = {
        **youtube.download_options(dest),
        **youtube.soundcloud_options(),
        "format": "bestaudio/best",
        "enable_file_urls": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(source.as_uri(), download=True)
    assert isinstance(info, dict)
    return youtube._downloaded_file(info, dest, source.stem), info


def test_an_mp3_from_soundcloud_is_made_into_aac_in_m4a(audio: AudioFixtures, dest: Path) -> None:
    require_tool("ffmpeg")
    before = tags.probe(audio.melody_a_mp3)
    path, _ = convert_here(audio.melody_a_mp3, dest)
    after = tags.probe(path)
    assert path.suffix == ".m4a" and path.parent == dest.resolve()
    assert (before.codec, after.codec) == ("mp3", "aac")
    assert after.duration_s is not None and before.duration_s is not None
    assert abs(after.duration_s - before.duration_s) < 0.2
    # Nothing but the one file is left in the job's folder: the MP3 is gone.
    assert [p.name for p in dest.iterdir()] == [path.name]


def test_soundclouds_own_aac_is_kept_as_it_came(audio: AudioFixtures, dest: Path) -> None:
    require_tool("ffmpeg")
    path, _ = convert_here(audio.melody_a_m4a, dest)
    assert path.suffix == ".m4a"
    assert tags.probe(path).codec == "aac"
    # Not converted: the sound decodes to exactly what it was.
    assert tags.audio_hash(path) == tags.audio_hash(audio.melody_a_m4a)


def test_a_track_is_played_from_soundclouds_mp3(monkeypatch: pytest.MonkeyPatch) -> None:
    StreamYDL.calls = []
    StreamYDL.info = {"id": "1398629833", "url": "https://example.invalid/track.mp3",
                      "format_id": "http_mp3_1_0", "duration": 589.113, "like_count": 16,
                      "http_headers": {"User-Agent": "x"}}  # fmt: skip
    seen: list[dict[str, Any]] = []

    def make(opts: dict[str, Any]) -> StreamYDL:
        seen.append(opts)
        return StreamYDL(opts)

    monkeypatch.setattr(tools, "require", lambda name, config=None: Path("bin") / name)
    monkeypatch.setattr(youtube, "_LIMITER", RateLimiter(0, 0, sleep=lambda s: None))
    monkeypatch.setattr(youtube, "_make_ydl", make)
    monkeypatch.setattr(youtube, "_recent", {})
    found = youtube.stream(TRACK)
    # It isn't format 140, and isn't asked to be.
    assert found == youtube.Stream(
        "https://example.invalid/track.mp3", {"User-Agent": "x"}, 589.113, 16
    )
    assert StreamYDL.calls == [("https://api.soundcloud.com/tracks/1398629833", False)]
    assert seen[0]["format"] == youtube.SOUNDCLOUD_PLAY


def test_soundcloud_turning_a_request_away_doesnt_pause_youtube() -> None:
    refused = youtube.download_problem(TRACK, Exception("HTTP Error 429: Too Many Requests"))
    assert isinstance(refused, DownloadError) and not isinstance(refused, YouTubeRefusedError)
    assert refused.network is True
    assert "SoundCloud" in str(refused) and "YouTube" not in str(refused)
    gone = youtube.download_problem(
        TRACK, Exception("ERROR: [soundcloud] 1: HTTP Error 404: Not Found")
    )
    assert isinstance(gone, VideoUnavailableError)
    odd = youtube.download_problem(TRACK, Exception("Requested format is not available"))
    assert isinstance(odd, FormatUnavailableError)
    # YouTube's own are worded and sorted as before.
    assert isinstance(
        youtube.download_problem("dQw4w9WgXcQ", Exception("HTTP Error 429")), YouTubeRefusedError
    )


def test_soundcloud_never_waits_on_deno(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dest: Path
) -> None:
    """deno is YouTube's helper. A Mac too busy to start it failed a song on 2026-10-13:
    SoundCloud doesn't need it, and isn't made to ask for it."""
    from musicorg.errors import ToolMissingError

    def require(name: str, config: object = None) -> Path:
        if name == "deno":
            raise ToolMissingError("deno", "deno can't be used just now.")
        return tmp_path / "bin" / name

    monkeypatch.setattr(tools, "require", require)
    monkeypatch.setattr(youtube, "_LIMITER", RateLimiter(0, 0, sleep=lambda s: None))
    monkeypatch.setattr(youtube, "_recent", {})
    seen: list[dict[str, Any]] = []
    StreamYDL.info = {"id": "1", "url": "https://example.invalid/track.mp3",
                      "format_id": "http_mp3_1_0", "duration": 60}  # fmt: skip

    def make(opts: dict[str, Any]) -> Any:
        seen.append(opts)
        return SoundCloudYDL(opts) if "paths" in opts else StreamYDL(opts)

    monkeypatch.setattr(youtube, "_make_ydl", make)
    FakeYoutubeDL.calls = []
    assert youtube.stream(TRACK).url == "https://example.invalid/track.mp3"
    assert youtube.download_audio(TRACK, dest)[0].name == "1398629833.m4a"
    assert all("js_runtimes" not in opts for opts in seen)
    # YouTube still needs it, and says so as itself: not as "failed on the network",
    # and without counting against YouTube as a request it turned away.
    before = youtube.limiter().failures
    with pytest.raises(ToolMissingError, match="can't be used just now"):
        youtube.stream("dQw4w9WgXcQ")
    with pytest.raises(ToolMissingError):
        youtube.download_audio("dQw4w9WgXcQ", dest)
    assert youtube.limiter().failures == before
