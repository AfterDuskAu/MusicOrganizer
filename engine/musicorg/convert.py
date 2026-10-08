"""Making a kept film one that phones and tablets play (the owner, 2026-10-08).

A film from a torrent arrives as whatever its maker chose: often MKV, sometimes AVI,
with sound in AC-3 or DTS. An iPhone, an iPad and most Android phones and tablets all
play one thing for certain: **an MP4 file with H.264 (or H.265) picture and AAC sound**.
With Settings → Downloads → "Convert kept films for phones and tablets" on, a film is
made into that before it's put in the Movies folder.

As little as possible is changed, because changing a picture costs time and quality:

- **Already that kind of file:** nothing is done.
- **Right picture, wrong box or sound** (most MKVs): the picture is copied exactly as it
  is, into an MP4. Sound that's already AAC is copied too; other sound is made AAC.
  This takes a minute or two and the picture loses nothing.
- **A picture those devices can't play** (an old AVI's Xvid, VP9, AV1, 10-bit H.264): it
  is made again as H.264. This is slow (it can take as long as the film) and loses a
  little quality, which can't be helped.
- **Subtitles** that are text come along. Ones that are pictures (from a Blu-ray or DVD)
  can't be put in an MP4 and are left out.

ffmpeg does the work and writes one file, the one this module names, in the films'
cache folder; `fileops.keep_media` then copies it to the Movies folder like any kept
film. Nothing here touches a library.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from musicorg import tools
from musicorg.errors import UserError

log = logging.getLogger(__name__)

DEVICE_BOXES = frozenset({".mp4", ".m4v", ".mov"})
DEVICE_PICTURES = frozenset({"h264", "hevc"})
DEVICE_PIXELS = frozenset({"yuv420p", "yuvj420p"})  # 8-bit; 10-bit H.264 plays on almost nothing
DEVICE_SOUND = frozenset({"aac"})
TEXT_SUBTITLES = frozenset({"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"})
PROBE_TIMEOUT_S = 60
ENDING = ".mp4"


class ConvertError(UserError):
    """A film couldn't be converted. The message is for the owner."""


@dataclass(frozen=True)
class Plan:
    """What has to be done to one film, as ffmpeg is told it."""

    arguments: tuple[str, ...]  # between the input and the output
    remakes_picture: bool  # the slow kind
    duration_s: float | None

    @property
    def about(self) -> str:
        return "making the picture again as H.264" if self.remakes_picture else "repacking"


def streams_of(path: Path) -> dict[str, Any]:
    """What's in a video file, from ffprobe: its `streams` and `format`."""
    command = [
        str(tools.require("ffprobe")), "-v", "error", "-show_format", "-show_streams",
        "-of", "json", str(path),
    ]  # fmt: skip
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=PROBE_TIMEOUT_S, check=False,
        )  # fmt: skip
        found = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise ConvertError(f"{path.name} couldn't be read to convert it: {exc}") from exc
    if not isinstance(found, dict):
        raise ConvertError(f"{path.name} couldn't be read to convert it.")
    return found


def plan(name: str, found: dict[str, Any]) -> Plan | None:
    """What a film needs, from its name (for its ending) and what ffprobe found in it.
    None: phones and tablets play it as it is."""
    streams = [s for s in found.get("streams") or [] if isinstance(s, dict)]
    pictures = [
        s
        for s in streams
        if s.get("codec_type") == "video"
        and not (s.get("disposition") or {}).get("attached_pic")  # a cover isn't the film
    ]
    if not pictures:
        raise ConvertError(f"{name} has no picture in it, so it wasn't converted.")
    picture = pictures[0]
    sounds = [s for s in streams if s.get("codec_type") == "audio"]
    subtitles = [s for s in streams if s.get("codec_type") == "subtitle"]
    text_subtitles = [s for s in subtitles if s.get("codec_name") in TEXT_SUBTITLES]

    codec = str(picture.get("codec_name") or "")
    picture_fine = codec in DEVICE_PICTURES and (
        codec != "h264" or str(picture.get("pix_fmt") or "yuv420p") in DEVICE_PIXELS
    )
    sound_fine = all(s.get("codec_name") in DEVICE_SOUND for s in sounds)
    box_fine = Path(name).suffix.lower() in DEVICE_BOXES
    if picture_fine and sound_fine and box_fine and len(text_subtitles) == len(subtitles):
        return None

    arguments = ["-map", f"0:{picture['index']}"]
    if picture_fine:
        arguments += ["-c:v", "copy"]
        if codec == "hevc":
            arguments += ["-tag:v", "hvc1"]  # the name Apple's players know H.265 by
    else:
        arguments += ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p"]
    for number, sound in enumerate(sounds):
        arguments += ["-map", f"0:{sound['index']}"]
        if sound.get("codec_name") in DEVICE_SOUND:
            arguments += [f"-c:a:{number}", "copy"]
        else:
            many = isinstance(sound.get("channels"), int) and sound["channels"] > 2
            arguments += [f"-c:a:{number}", "aac", f"-b:a:{number}", "384k" if many else "192k"]
            if many and sound["channels"] > 6:
                arguments += [f"-ac:a:{number}", "6"]
    for subtitle in text_subtitles:
        arguments += ["-map", f"0:{subtitle['index']}"]
    if text_subtitles:
        arguments += ["-c:s", "mov_text"]
    arguments += ["-movflags", "+faststart"]  # it starts at once when played over a network
    length = (found.get("format") or {}).get("duration")
    try:
        duration = float(length) if length is not None else None
    except (TypeError, ValueError):
        duration = None
    return Plan(tuple(arguments), not picture_fine, duration)


def run(
    source: Path,
    target: Path,
    wanted: Plan,
    *,
    progress: Callable[[float], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """Have ffmpeg write `target` (in the films' cache folder) from `source`. `progress`
    hears how far along it is, 0 to 1. If `should_stop` turns true, ffmpeg is stopped.
    On any failure the half-written file is left for the cache's sweep, and ConvertError
    says why."""
    command = [
        str(tools.require("ffmpeg")), "-nostdin", "-y", "-v", "error", "-i", str(source),
        *wanted.arguments, "-progress", "pipe:1", "-nostats", "-f", "mp4", str(target),
    ]  # fmt: skip
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )  # fmt: skip
    except OSError as exc:
        raise ConvertError(f"The converter couldn't be started: {exc}") from exc
    assert process.stdout is not None and process.stderr is not None
    try:
        for line in process.stdout:
            if should_stop is not None and should_stop():
                process.kill()
                raise ConvertError("Converting was stopped.")
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and progress is not None and wanted.duration_s:
                try:
                    progress(min(max(int(value) / 1_000_000 / wanted.duration_s, 0.0), 1.0))
                except ValueError:
                    pass
        said = process.stderr.read()
        code = process.wait()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    if code != 0:
        detail = (said.strip().splitlines() or ["no details"])[-1]
        raise ConvertError(f"{source.name} couldn't be converted: {detail}")
    if progress is not None:
        progress(1.0)
