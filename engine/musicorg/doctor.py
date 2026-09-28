"""`musicorg doctor`: checks that everything the engine needs is in place."""

from __future__ import annotations

import platform
import sys
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path

from musicorg import config, tools
from musicorg.config import Config

MIN_PYTHON = (3, 12)

# (distribution name, fix if it's missing)
_PACKAGES = (
    ("yt-dlp", 'Reinstall the engine: pip install -e "engine[dev]"'),
    ("yt-dlp-ejs", 'It comes with yt-dlp. Run: pip install -U "yt-dlp[default]"'),
    ("ytmusicapi", 'Reinstall the engine: pip install -e "engine[dev]"'),
)


@dataclass(frozen=True)
class Check:
    kind: str  # "python", "tool", "package", "config" or "folder"
    name: str
    ok: bool
    detail: str
    fix: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def run_checks(cfg: Config | None, config_problem: str | None = None) -> list[Check]:
    """Run every check. `cfg` is None when config.json couldn't be loaded."""
    checks = [_python_check()]
    checks += [_tool_check(name, cfg) for name in tools.TOOL_NAMES]
    checks += [_package_check(dist, fix) for dist, fix in _PACKAGES]
    checks.append(_config_check(config_problem))
    dirs = config.app_dirs()
    checks.append(_folder_check("Config folder", dirs.config))
    checks.append(_folder_check("Log folder", dirs.logs))
    return checks


def describe_platform() -> str:
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0]} {platform.machine()}"
    if sys.platform == "win32":
        return f"Windows {platform.version()} {platform.machine()}"
    return f"{platform.system()} {platform.release()} {platform.machine()}"


def _python_check() -> Check:
    version = ".".join(str(n) for n in sys.version_info[:3])
    ok = sys.version_info[:2] >= MIN_PYTHON
    needed = ".".join(str(n) for n in MIN_PYTHON)
    return Check(
        kind="python",
        name="Python",
        ok=ok,
        detail=version,
        fix=None if ok else f"Python {needed} or newer is needed. See README.md.",
    )


def _tool_check(name: str, cfg: Config | None) -> Check:
    info = tools.find(name, cfg)
    if info.ok:
        detail = f"{info.version}  {info.path}"
        if info.note:
            detail += f"  ({info.note})"
        return Check(kind="tool", name=name, ok=True, detail=detail)
    detail = "not found" if info.path is None else f"{info.path}: {info.problem}"
    if info.note:
        detail += f" ({info.note})"
    return Check(kind="tool", name=name, ok=False, detail=detail, fix=tools.install_hint(name))


def _package_check(dist: str, fix: str) -> Check:
    try:
        version = metadata.version(dist)
    except metadata.PackageNotFoundError:
        return Check(kind="package", name=dist, ok=False, detail="not installed", fix=fix)
    return Check(kind="package", name=dist, ok=True, detail=version)


def _config_check(problem: str | None) -> Check:
    path = config.config_path()
    if problem:
        return Check(
            kind="config",
            name="Settings file",
            ok=False,
            detail=problem,
            fix=f"Fix {path}, or move it away to go back to default settings.",
        )
    detail = str(path) if path.exists() else f"{path} (not created yet; defaults in use)"
    return Check(kind="config", name="Settings file", ok=True, detail=detail)


def _folder_check(name: str, path: Path) -> Check:
    problem = config.writable_problem(path)
    if problem:
        return Check(
            kind="folder",
            name=name,
            ok=False,
            detail=problem,
            fix="Check the folder's permissions, or free up disk space.",
        )
    return Check(kind="folder", name=name, ok=True, detail=str(path))
