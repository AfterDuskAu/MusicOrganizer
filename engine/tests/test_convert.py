"""Making a kept film one that phones and tablets play: what's decided for each kind of
file, and ffmpeg really doing it on small films made here."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from musicorg import convert, tools, torrents


def stream(kind: str, codec: str, index: int, **more: Any) -> dict[str, Any]:
    return {"codec_type": kind, "codec_name": codec, "index": index, **more}


def found(*streams: dict[str, Any], duration: str = "100.0") -> dict[str, Any]:
    return {"streams": list(streams), "format": {"duration": duration}}


def test_a_film_devices_already_play_is_left_alone() -> None:
    fine = found(stream("video", "h264", 0, pix_fmt="yuv420p"), stream("audio", "aac", 1))
    assert convert.plan("Film.mp4", fine) is None
    assert convert.plan("Film.MOV", fine) is None
    assert convert.plan("Film.m4v", found(stream("video", "hevc", 0), stream("audio", "aac", 1),
                                          stream("subtitle", "mov_text", 2))) is None  # fmt: skip


def test_the_right_picture_in_the_wrong_box_is_repacked_not_remade() -> None:
    wanted = convert.plan("Film.mkv", found(
        stream("video", "mjpeg", 0, disposition={"attached_pic": 1}),  # a cover, not the film
        stream("video", "h264", 1, pix_fmt="yuv420p"),
        stream("audio", "aac", 2, channels=2),
        stream("audio", "ac3", 3, channels=6),
        stream("audio", "dts", 4, channels=8),
        stream("subtitle", "subrip", 5),
        stream("subtitle", "hdmv_pgs_subtitle", 6),  # pictures: can't go in an MP4
    ))  # fmt: skip
    assert wanted is not None and not wanted.remakes_picture and wanted.duration_s == 100.0
    assert wanted.arguments == (
        "-map", "0:1", "-c:v", "copy",
        "-map", "0:2", "-c:a:0", "copy",
        "-map", "0:3", "-c:a:1", "aac", "-b:a:1", "384k",
        "-map", "0:4", "-c:a:2", "aac", "-b:a:2", "384k", "-ac:a:2", "6",
        "-map", "0:5", "-c:s", "mov_text",
        "-movflags", "+faststart",
    )  # fmt: skip
    h265 = convert.plan("Film.mkv", found(stream("video", "hevc", 0), stream("audio", "aac", 1)))
    assert h265 is not None and h265.arguments[:6] == (
        "-map",
        "0:0",
        "-c:v",
        "copy",
        "-tag:v",
        "hvc1",
    )
    # An MP4 whose sound isn't AAC is repacked too.
    assert convert.plan("Film.mp4", found(stream("video", "h264", 0), stream("audio", "ac3", 1)))


@pytest.mark.parametrize(
    "picture",
    [stream("video", "mpeg4", 0), stream("video", "vp9", 0), stream("video", "av1", 0),
     stream("video", "h264", 0, pix_fmt="yuv420p10le")],
)  # fmt: skip
def test_a_picture_devices_cant_play_is_made_again(picture: dict[str, Any]) -> None:
    wanted = convert.plan("Film.avi", found(picture, stream("audio", "mp3", 1, channels=2)))
    assert wanted is not None and wanted.remakes_picture
    assert wanted.arguments[:4] == ("-map", "0:0", "-c:v", "libx264")
    assert "192k" in wanted.arguments and wanted.about.startswith("making the picture again")


def test_a_file_with_no_picture_isnt_converted() -> None:
    with pytest.raises(convert.ConvertError):
        convert.plan("Sound.mka", found(stream("audio", "aac", 0)))


# ---- for real: small films made here by ffmpeg -------------------------------------------


def make(target: Path, picture: list[str], sound: list[str], subtitles: bool = False) -> Path:
    ffmpeg = str(tools.require("ffmpeg"))
    command = [ffmpeg, "-nostdin", "-y", "-v", "error",
               "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=10",
               "-f", "lavfi", "-i", "sine=frequency=440:duration=2"]  # fmt: skip
    if subtitles:
        words = target.with_suffix(".srt")
        words.write_text("1\n00:00:00,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
        command += ["-i", str(words)]
    command += ["-map", "0", "-map", "1", *(["-map", "2", "-c:s", "srt"] if subtitles else []),
                *picture, *sound, str(target)]  # fmt: skip
    subprocess.run(command, check=True, stdin=subprocess.DEVNULL)
    return target


def kinds(path: Path) -> list[tuple[str, str]]:
    return [(s["codec_type"], s["codec_name"]) for s in convert.streams_of(path)["streams"]]


def test_an_mkv_is_repacked_with_its_picture_untouched(tmp_path: Path) -> None:
    film = make(tmp_path / "film.mkv", ["-c:v", "libx264", "-pix_fmt", "yuv420p"],
                ["-c:a", "libmp3lame"], subtitles=True)  # fmt: skip
    wanted = convert.plan(film.name, convert.streams_of(film))
    assert wanted is not None and not wanted.remakes_picture
    heard: list[float] = []
    made = tmp_path / "made.mp4"
    convert.run(film, made, wanted, progress=heard.append)
    assert kinds(made) == [("video", "h264"), ("audio", "aac"), ("subtitle", "mov_text")]
    assert heard[-1] == 1.0 and all(0 <= step <= 1 for step in heard)
    # What was made needs nothing more.
    assert convert.plan(made.name, convert.streams_of(made)) is None

    def packets(path: Path) -> str:
        return subprocess.run(
            [str(tools.require("ffmpeg")), "-nostdin", "-v", "error", "-i", str(path),
             "-map", "0:v:0", "-c", "copy", "-f", "md5", "-"],
            capture_output=True, text=True, check=True,
        ).stdout  # fmt: skip

    assert packets(made) == packets(film)  # the very same picture, bit for bit


def test_an_old_avi_is_made_again_as_h264(tmp_path: Path) -> None:
    film = make(tmp_path / "film.avi", ["-c:v", "mpeg4"], ["-c:a", "libmp3lame"])
    wanted = convert.plan(film.name, convert.streams_of(film))
    assert wanted is not None and wanted.remakes_picture
    made = tmp_path / "made.mp4"
    convert.run(film, made, wanted)
    assert kinds(made) == [("video", "h264"), ("audio", "aac")]


def test_a_stopped_or_failed_conversion_says_so(tmp_path: Path) -> None:
    film = make(tmp_path / "film.mkv", ["-c:v", "libx264", "-pix_fmt", "yuv420p"],
                ["-c:a", "libmp3lame"])  # fmt: skip
    wanted = convert.plan(film.name, convert.streams_of(film))
    assert wanted is not None
    with pytest.raises(convert.ConvertError, match="stopped"):
        convert.run(film, tmp_path / "made.mp4", wanted, should_stop=lambda: True)
    broken = tmp_path / "broken.mkv"
    broken.write_bytes(b"not a film")
    with pytest.raises(convert.ConvertError):
        convert.streams_of(broken)
    with pytest.raises(convert.ConvertError, match="couldn't be converted"):
        convert.run(broken, tmp_path / "made2.mp4", wanted)


class Handle:
    def __init__(self, size: int) -> None:
        self.size = size

    def is_valid(self) -> bool:
        return True

    def file_progress(self) -> list[int]:
        return [self.size]


def kept(tmp_path: Path, film: Path, *, convert_it: bool) -> Any:
    movies = tmp_path / "Movies"
    movies.mkdir(exist_ok=True)
    player = torrents.Player(film.parent.parent, movies=movies)
    joined = torrents._Joined(Handle(film.stat().st_size), "ab" * 20, 0, film.parent)
    joined.size, joined.path, joined.name = film.stat().st_size, film, film.name
    joined.keep_name, joined.convert, joined.playing = "The Film (1921)", convert_it, False
    player._joined[joined.info_hash] = joined
    player._save_kept(joined)
    return joined


def test_a_kept_film_is_converted_when_settings_say_so(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "torrents"
    cache.mkdir(parents=True)
    film = make(cache / "film.mkv", ["-c:v", "libx264", "-pix_fmt", "yuv420p"],
                ["-c:a", "libmp3lame"])  # fmt: skip
    joined = kept(tmp_path, film, convert_it=True)
    saved = tmp_path / "Movies" / "The Film (1921).mp4"
    assert joined.kept_path == str(saved) and joined.keep_note is None
    assert kinds(saved) == [("video", "h264"), ("audio", "aac")]
    # The converted copy is gone from the cache; the film as it arrived stays its day.
    assert sorted(p.name for p in cache.iterdir()) == ["film.mkv"]
    assert joined.converting is None and joined.saving is False

    # Not asked for: it's kept exactly as it arrived.
    as_it_was = kept(tmp_path, film, convert_it=False)
    assert as_it_was.kept_path == str(tmp_path / "Movies" / "The Film (1921).mkv")


def test_a_film_that_cant_be_converted_is_kept_as_it_arrived(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "torrents"
    cache.mkdir(parents=True)
    film = cache / "film.mkv"
    film.write_bytes(b"not really a film")
    joined = kept(tmp_path, film, convert_it=True)
    assert joined.kept_path == str(tmp_path / "Movies" / "The Film (1921).mkv")
    assert joined.keep_error is None and "kept as it arrived" in (joined.keep_note or "")
