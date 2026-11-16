"""Finding the external programs the engine needs: ffmpeg, ffprobe, fpcalc and deno;
`keep_awake()`, which stops the computer sleeping while the queue works; and `room_for()`,
whether a film coming in has room on the disk.

Search order for each tool:
1. the path set under "tools" in config.json
2. the folders on PATH
3. common install folders, because an app started from Finder doesn't get the shell's PATH

A copy that can't be run, or a deno older than 2.3, counts as missing, and the search
carries on to the next copy.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from musicorg.config import Config, config_path
from musicorg.errors import ToolMissingError

log = logging.getLogger(__name__)

TOOL_NAMES = ("ffmpeg", "ffprobe", "fpcalc", "deno")
MIN_DENO_VERSION = (2, 3)
# How long a tool may take to say its version before it's taken as stuck. Twenty seconds
# until 2026-11-17, when a song from Music Finder failed on the owner's busy Mac: the
# first start of deno (a 98 MB program) after a while is read from the Mac's spinning
# disk, which other work keeps at its limit, and that took 20 to 36 seconds even on a
# quiet evening (load 3), more under builds. Slow isn't stuck: yt-dlp itself starts deno
# with no limit at all. Two minutes is over three times the slowest start measured, and
# a tool that hasn't answered by then is said to be stuck rather than waited on for good.
VERSION_TIMEOUT_S = 120
# Started this long after the engine (`warm_up`), so the app's own start has the disk first.
WARM_UP_AFTER_S = 10.0
_TOO_SLOW = "it didn't answer within"

_VERSION_ARGS = {
    "ffmpeg": ["-version"],
    "ffprobe": ["-version"],
    "fpcalc": ["-version"],
    "deno": ["--version"],
}
_VERSION_PATTERNS = {
    "ffmpeg": re.compile(r"^\s*ffmpeg version (\S+)", re.MULTILINE),
    "ffprobe": re.compile(r"^\s*ffprobe version (\S+)", re.MULTILINE),
    "fpcalc": re.compile(r"fpcalc version (\S+)"),
    "deno": re.compile(r"^\s*deno (\d+\.\d+\.\d+\S*)", re.MULTILINE),
}
# Keeps a console window from flashing up on Windows. Zero (no flags) elsewhere.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class ToolInfo:
    """What the search found for one tool."""

    name: str
    path: Path | None = None
    version: str | None = None
    found_in: str | None = None  # "config.json", "PATH" or "fallback folder"
    problem: str | None = None  # why the copy at `path` can't be used
    note: str | None = None  # copies skipped on the way, worth showing in doctor

    @property
    def ok(self) -> bool:
        return self.path is not None and self.problem is None


def fallback_dirs(platform: str | None = None) -> list[Path]:
    """Common install folders, searched after PATH."""
    platform = platform or sys.platform
    deno_home = Path.home() / ".deno" / "bin"
    if platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        program_data = os.environ.get("ProgramData") or r"C:\ProgramData"
        return [
            Path(local) / "Microsoft" / "WinGet" / "Links",
            Path(program_data) / "chocolatey" / "bin",
            deno_home,
        ]
    return [
        Path("/usr/local/bin"),
        Path("/opt/homebrew/bin"),
        Path("/opt/local/bin"),
        deno_home,
    ]


def find(tool: str, config: Config | None = None) -> ToolInfo:
    """Look for a usable copy of `tool`. Never raises; check `.ok` on the result."""
    _check_name(tool)
    note: str | None = None
    candidates: list[tuple[Path, str]] = []

    override = config.tool_path(tool) if config is not None else None
    if override is not None:
        exe = _executable_at(override, tool)
        if exe is not None:
            candidates.append((exe, "config.json"))
        else:
            note = f"the path set in config.json ({override}) isn't a program, so it was ignored"
    candidates += [(p, "PATH") for p in _find_in_dirs(tool, _path_dirs())]
    candidates += [(p, "fallback folder") for p in _find_in_dirs(tool, fallback_dirs())]

    skipped: list[ToolInfo] = []
    seen: set[str] = set()
    for path, found_in in candidates:
        key = _same_file_key(path)
        if key in seen:
            continue
        seen.add(key)
        info = _inspect(tool, path, found_in)
        if info.ok:
            notes = ([note] if note else []) + [f"skipped {s.path}: {s.problem}" for s in skipped]
            return replace(info, note="; ".join(notes) or None)
        skipped.append(info)

    if skipped:
        return replace(skipped[0], note=note)
    return ToolInfo(name=tool, problem="not found", note=note)


# Tools found usable, for this run of the engine: by tool, the path set for it and PATH
# (what `find` goes by), with the file's size and time, so a copy that's replaced is
# looked at afresh.
_good: dict[tuple[str, str, str], tuple[Path, tuple[int, int]]] = {}
# One look for each tool at a time: a song's sound, its video and its lyrics are asked for
# within moments of each other, and on a busy disk two starts of deno at once are each
# slower than one. Whoever comes second waits for the first look and takes its answer.
_looking = {name: threading.Lock() for name in TOOL_NAMES}


def require(tool: str, config: Config | None = None) -> Path:
    """Path to a usable copy of `tool`, or ToolMissingError with install hints.

    A copy found usable is remembered for as long as the engine runs and its file is
    unchanged. Until 2026-10-13 every look-up at YouTube ran `deno --version` first,
    and every download `ffmpeg -version` too: a fifth of a second each on a quiet Mac,
    and on a very busy one more than the twenty seconds allowed, which failed the song
    (the owner's picture that day, with the Mac's load at 96). A copy that can't be
    used isn't remembered: it's tried again the next time it's wanted.

    Since 2026-11-17 the first look of an engine's run is made in the background soon
    after it starts (`warm_up`), and may take two minutes (`VERSION_TIMEOUT_S`): it was
    still the first song after each start of the app that waited for it, and failed."""
    _check_name(tool)
    override = config.tool_path(tool) if config is not None else None
    key = (tool, str(override or ""), os.environ.get("PATH", ""))
    with _looking[tool]:
        known = _good.get(key)
        if known is not None and _stamp(known[0]) == known[1]:
            return known[0]
        info = find(tool, config)
        if info.ok and info.path is not None:
            stamp = _stamp(info.path)
            if stamp is not None:
                _good[key] = (info.path, stamp)
            return info.path
    raise ToolMissingError(tool, missing_message(info))


def warm_up(names: tuple[str, ...] = ("deno",), *, after_s: float = WARM_UP_AFTER_S) -> None:
    """Look for the tools that playing a song needs, in the background, soon after the
    engine starts, so the first song the owner clicks doesn't wait for it (2026-11-17).

    On the owner's Mac the first start of deno is read from a busy spinning disk and took
    20 to 36 seconds; done here, it's done before anyone asks, and its file is in memory
    by then. A song asked for while this is under way waits for this same look
    (`require`), not a second one. Never raises: a tool that's missing or stuck is said
    when it's wanted, as before."""

    def look() -> None:
        time.sleep(after_s)
        for name in names:
            started = time.monotonic()
            try:
                path = require(name)
            except ToolMissingError as exc:
                log.warning("tools: %s isn't ready: %s", name, exc.message)
                continue
            except Exception as exc:  # never let a look-ahead stop the engine
                log.warning("tools: looking for %s went wrong: %s", name, exc)
                continue
            log.info("tools: %s is ready at %s (%.1f s)", name, path, time.monotonic() - started)

    threading.Thread(target=look, daemon=True, name="tools-warm-up").start()


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        found = os.stat(path)
    except OSError:
        return None
    return (found.st_size, found.st_mtime_ns)


def missing_message(info: ToolInfo, platform: str | None = None) -> str:
    """Plain-English explanation of why a tool can't be used, and how to fix it."""
    if info.path is None:
        first = f"{info.name} isn't installed, or Bear Media can't find it."
    elif info.problem is not None and info.problem.startswith(_TOO_SLOW):
        # It's installed: the computer was too busy to start it. Nothing to install.
        return (
            f"This computer is very busy just now: {info.name}, a helper the app needs for "
            f"this, took more than {_how_long(VERSION_TIMEOUT_S)} to start. Try again in a "
            "moment, or once whatever is keeping the computer busy has finished."
        )
    else:
        first = f"The {info.name} at {info.path} can't be used: {info.problem}."
    return f"{first} {install_hint(info.name, platform)}"


def _how_long(seconds: int) -> str:
    """As a person would say it: "2 minutes" for 120, "45 seconds" for 45."""
    if seconds >= 120 and seconds % 60 == 0:
        return f"{seconds // 60} minutes"
    return f"{seconds} seconds"


def install_hint(tool: str, platform: str | None = None) -> str:
    _check_name(tool)
    platform = platform or sys.platform
    if platform == "darwin":
        hint = {
            "ffmpeg": "Install it with `brew install ffmpeg`. If Homebrew isn't installed, or it "
            "compiles for ages on an Intel Mac, use a static Intel macOS build of ffmpeg or "
            "MacPorts (`sudo port install ffmpeg`) instead.",
            "fpcalc": "Install it with `brew install chromaprint`, or download the macOS "
            "universal build of fpcalc from https://github.com/acoustid/chromaprint/releases.",
            "deno": "Install it with `brew install deno`, or with "
            "`curl -fsSL https://deno.land/install.sh | sh` (installs to ~/.deno/bin).",
        }
    elif platform == "win32":
        hint = {
            "ffmpeg": "Install it with `winget install ffmpeg`.",
            "fpcalc": "Download chromaprint-fpcalc-<version>-windows-x86_64.zip from "
            "https://github.com/acoustid/chromaprint/releases (it isn't on winget), unzip it, "
            "and put fpcalc.exe in a folder on your PATH.",
            "deno": "Install it with `winget install DenoLand.Deno`.",
        }
    else:
        hint = {
            "ffmpeg": "Install ffmpeg with your package manager.",
            "fpcalc": "Install Chromaprint's fpcalc with your package manager.",
            "deno": "Install it with `curl -fsSL https://deno.land/install.sh | sh`.",
        }
    hint["ffprobe"] = hint["ffmpeg"].replace("Install it", "It comes with ffmpeg; install it")
    where = config_path()
    return (
        f"{hint[tool]} If it's already installed somewhere unusual, set its path under "
        f'"tools" in {where}.'
    )


def parse_version(tool: str, output: str) -> str | None:
    """Pull the version out of a tool's version output."""
    match = _VERSION_PATTERNS[tool].search(output)
    return match.group(1) if match else None


def deno_version_ok(version: str) -> bool:
    match = re.match(r"(\d+)\.(\d+)", version)
    if not match:
        return False
    return (int(match.group(1)), int(match.group(2))) >= MIN_DENO_VERSION


def _check_name(tool: str) -> None:
    if tool not in TOOL_NAMES:
        raise ValueError(f"unknown tool {tool!r}; expected one of {', '.join(TOOL_NAMES)}")


def _inspect(tool: str, path: Path, found_in: str) -> ToolInfo:
    """Run `tool` for its version and decide whether this copy is usable."""
    base = ToolInfo(name=tool, path=path, found_in=found_in)
    try:
        result = subprocess.run(
            [str(path), *_VERSION_ARGS[tool]],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=VERSION_TIMEOUT_S,
            check=False,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return replace(base, problem=f"{_TOO_SLOW} {_how_long(VERSION_TIMEOUT_S)}")
    except OSError as exc:
        return replace(base, problem=f"it couldn't be started ({exc.strerror or exc})")
    if result.returncode != 0:
        return replace(base, problem=f"it stopped with error code {result.returncode}")

    version = parse_version(tool, result.stdout + "\n" + result.stderr)
    if tool == "deno":
        if version is None:
            return replace(base, problem="its version couldn't be read")
        if not deno_version_ok(version):
            needed = ".".join(str(n) for n in MIN_DENO_VERSION)
            return replace(
                base,
                version=version,
                problem=f"version {version} is too old; {needed} or newer is needed",
            )
    return replace(base, version=version or "unknown")


def _path_dirs() -> list[Path]:
    dirs = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if entry:
            dirs.append(Path(entry))
    return dirs


def _executable_names(tool: str) -> list[str]:
    if os.name == "nt":
        exts = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
        return [tool + ext.lower() for ext in exts if ext]
    return [tool]


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _find_in_dirs(tool: str, dirs: list[Path]) -> list[Path]:
    found = []
    for folder in dirs:
        for name in _executable_names(tool):
            path = folder / name
            try:
                if _is_executable(path):
                    found.append(path)
            except OSError:
                continue
    return found


def _executable_at(path: Path, tool: str) -> Path | None:
    """A config.json path may name the program itself or the folder holding it."""
    try:
        if path.is_dir():
            matches = _find_in_dirs(tool, [path])
            return matches[0] if matches else None
        return path if _is_executable(path) else None
    except OSError:
        return None


def _same_file_key(path: Path) -> str:
    try:
        return os.path.normcase(os.path.realpath(path))
    except OSError:
        return os.path.normcase(str(path))


# ---- keeping the computer awake --------------------------------------------------------

CAFFEINATE = "/usr/bin/caffeinate"  # a macOS path, kept as text so it reads the same everywhere
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


@contextmanager
def keep_awake(platform: str | None = None) -> Iterator[None]:
    """Keep the computer from going to sleep (the screen may still turn off) while the
    queue works through hours of paced downloads. From the Photonizer project's lessons.

    - macOS: `caffeinate -i -w <this process>`, which also ends by itself if the engine
      crashes.
    - Windows: SetThreadExecutionState, which Windows drops when the process ends.
    - Anywhere else, or if it can't be done: nothing, and the queue still runs.
    """
    platform = platform or sys.platform
    if platform == "darwin":
        with _caffeinate():
            yield
    elif platform == "win32":
        with _execution_state():
            yield
    else:
        yield


@contextmanager
def _caffeinate() -> Iterator[None]:
    process = None
    try:
        process = subprocess.Popen(
            [CAFFEINATE, "-i", "-w", str(os.getpid())],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        log.info("Couldn't keep the Mac awake (%s); it may sleep during long runs", exc)
    try:
        yield
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


@contextmanager
def _execution_state() -> Iterator[None]:
    import ctypes

    kernel32 = getattr(getattr(ctypes, "windll", None), "kernel32", None)
    set_state = getattr(kernel32, "SetThreadExecutionState", None)
    if set_state is None or not set_state(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED):
        log.info("Couldn't keep Windows awake; it may sleep during long runs")
        yield
        return
    try:
        yield
    finally:
        set_state(_ES_CONTINUOUS)


# ---- room on the disk for a film ---------------------------------------------------------

ROOM_TO_SPARE = 1024**3  # a gigabyte left over on each disk, whatever comes


def _nearest(path: Path) -> Path:
    """`path`, or the nearest folder above it that's there."""
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return path


def room_for(coming: int, size: int, cache: Path, movies: Path) -> bool:
    """Whether there's room for the rest of a film in the app's cache (`coming` bytes)
    and for all of it in the Movies folder (`size`), with a gigabyte to spare on each
    disk: a film sent from a device (`sharing`, 2026-10-16) or fetched from a plugin's
    address (`fetch`, 2026-10-17). Only looked at; when it can't be told, the film is
    let come (a full disk is said then)."""
    try:
        here, there = _nearest(cache), _nearest(movies)
        if os.stat(here).st_dev == os.stat(there).st_dev:
            return shutil.disk_usage(here).free >= coming + size + ROOM_TO_SPARE
        return (shutil.disk_usage(here).free >= coming + ROOM_TO_SPARE
                and shutil.disk_usage(there).free >= size + ROOM_TO_SPARE)  # fmt: skip
    except OSError:
        return True
