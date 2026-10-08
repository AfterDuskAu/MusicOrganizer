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

**A film that's already kept** (2026-10-08) can be converted too, from the app's lists
(`Converter`): the copy is made the same way and put beside the film as an MP4, under a
name that isn't taken. The film itself is only read, and is left where it is: the owner
deletes it when they've seen the copy plays.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
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


class Converter:
    """One film already in the Movies folder or the videos folder, being made into a
    copy phones and tablets play. One at a time: it can take as long as the film."""

    def __init__(self, cache: Path) -> None:
        self.cache = cache  # the app's own cache folder
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._now: dict[str, Any] | None = None  # the film being converted
        self._last: dict[str, Any] | None = None  # how the one before it ended

    def start(
        self,
        film: Path,
        folders: list[Path],
        *,
        forbidden: list[Path],
        finished: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Begin converting `film`, which must be a real file inside one of `folders`
        (where kept films and videos go). Returns `{needed, remakes_picture}` at once;
        `needed` false means phones and tablets play it as it is, and nothing is done.
        `finished` hears how it ended."""
        film = Path(film)
        top = _folder_of(film, folders)
        if top is None:
            raise ConvertError(
                "Only a movie or video in your Movies or Videos folder can be converted here."
            )
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ConvertError(
                    "Another movie is being converted. Try this one when it has finished."
                )
            wanted = plan(film.name, streams_of(film))
            if wanted is None:
                return {"needed": False, "remakes_picture": False}
            self._stopping.clear()
            self._now = {"path": str(film), "progress": 0.0,
                         "remakes_picture": wanted.remakes_picture}  # fmt: skip
            self._last = None
            self._thread = threading.Thread(
                target=self._work, args=(film, top, wanted, forbidden, finished),
                name="convert", daemon=True,
            )  # fmt: skip
            self._thread.start()
        return {"needed": True, "remakes_picture": wanted.remakes_picture}

    def status(self) -> dict[str, Any]:
        """`converting`: the film being converted (`path`, `progress` 0 to 1,
        `remakes_picture`) or None. `last`: how the one before ended (`path`, and
        `saved` or `error`) or None."""
        with self._lock:
            return {"converting": dict(self._now) if self._now else None,
                    "last": dict(self._last) if self._last else None}  # fmt: skip

    def stop(self) -> None:
        """The engine is stopping: ffmpeg is stopped, and nothing half-made is kept."""
        self._stopping.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(5)

    def _work(
        self,
        film: Path,
        top: Path,
        wanted: Plan,
        forbidden: list[Path],
        finished: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        from musicorg import fileops
        from musicorg.errors import MusicOrgError

        ended: dict[str, Any] = {"path": str(film)}
        made: Path | None = None
        folder = self.cache / "torrents"  # the films' cache folder
        try:
            fileops.make_cached_folder(folder, cache=self.cache)
            name = hashlib.sha256(str(film).encode("utf-8", "surrogatepass")).hexdigest()[:16]
            made = folder / f".converting-{name}{ENDING}"
            log.info("Converting a kept film (%s).", wanted.about)

            def heard(done: float) -> None:
                with self._lock:
                    if self._now is not None:
                        self._now["progress"] = round(done, 3)

            run(film, made, wanted, progress=heard, should_stop=self._stopping.is_set)
            saved = fileops.keep_media(
                made, film.parent, f"{film.stem}{ENDING}", cache=folder,
                allowed=[top], forbidden=forbidden,
            )  # fmt: skip
            ended["saved"] = str(saved)
        except (MusicOrgError, ValueError, OSError) as exc:
            ended["error"] = getattr(exc, "message", None) or str(exc)
            log.warning("A kept film wasn't converted: %s", ended["error"])
        finally:
            if made is not None:
                fileops.forget_cached(made, cache=self.cache)
        with self._lock:
            self._now, self._last = None, ended
        if finished is not None and not self._stopping.is_set():
            finished(ended)


def _folder_of(film: Path, folders: list[Path]) -> Path | None:
    """The one of `folders` a film is really inside, or None. A link isn't a film here:
    what it points at may be anywhere."""
    try:
        if film.is_symlink() or not film.is_file():
            return None
        real = Path(os.path.realpath(film))
    except OSError:
        return None
    for folder in folders:
        try:
            if Path(os.path.realpath(folder)) in real.parents:
                return folder
        except OSError:
            continue
    return None
