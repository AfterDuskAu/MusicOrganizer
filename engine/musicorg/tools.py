"""Finding the external programs the engine needs: ffmpeg, ffprobe, fpcalc and deno.

Search order for each tool:
1. the path set under "tools" in config.json
2. the folders on PATH
3. common install folders, because an app started from Finder doesn't get the shell's PATH

A copy that can't be run, or a deno older than 2.3, counts as missing, and the search
carries on to the next copy.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from musicorg.config import Config, config_path
from musicorg.errors import ToolMissingError

TOOL_NAMES = ("ffmpeg", "ffprobe", "fpcalc", "deno")
MIN_DENO_VERSION = (2, 3)
VERSION_TIMEOUT_S = 20

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


def require(tool: str, config: Config | None = None) -> Path:
    """Path to a usable copy of `tool`, or ToolMissingError with install hints."""
    info = find(tool, config)
    if info.ok and info.path is not None:
        return info.path
    raise ToolMissingError(tool, missing_message(info))


def missing_message(info: ToolInfo, platform: str | None = None) -> str:
    """Plain-English explanation of why a tool can't be used, and how to fix it."""
    if info.path is None:
        first = f"{info.name} isn't installed, or Music Organizer can't find it."
    else:
        first = f"The {info.name} at {info.path} can't be used: {info.problem}."
    return f"{first} {install_hint(info.name, platform)}"


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
        return replace(base, problem=f"it didn't answer within {VERSION_TIMEOUT_S} seconds")
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
