"""`musicorg doctor`: checks that everything the engine needs is in place, and
`--update-ytdlp` / `--rollback-ytdlp` (step 09a)."""

from __future__ import annotations

import platform
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path

from musicorg import config, library, tools
from musicorg.config import Config
from musicorg.errors import LibraryLockedError, UserError

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


# ---- updating yt-dlp (step 09a) --------------------------------------------------------

YTDLP_PACKAGES = ("yt-dlp", "yt-dlp-ejs")
PREVIOUS_KEY = "ytdlp_previous"  # in config.json: the versions from before the last update
PIP_TIMEOUT_S = 900


@dataclass(frozen=True)
class UpdateResult:
    before: dict[str, str | None]
    after: dict[str, str | None]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def installed_versions() -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    for dist in YTDLP_PACKAGES:
        try:
            found[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            found[dist] = None
    return found


def update_ytdlp(library_root: Path | None) -> UpdateResult:
    """`pip install -U "yt-dlp[default]"` in the engine's own Python, after recording the
    current versions in config.json for `rollback_ytdlp`. Refused while the library is in
    use (a download could be half way through); the library stays locked meanwhile."""
    with _library_not_in_use(library_root, "doctor --update-ytdlp"):
        before = installed_versions()
        config.remember_ytdlp_versions(before)
        _pip("install", "-U", "yt-dlp[default]")
        return UpdateResult(before, installed_versions())


def rollback_ytdlp(cfg: Config, library_root: Path | None) -> UpdateResult:
    """Reinstall the versions recorded by the last `update_ytdlp`."""
    previous = cfg.data.get(PREVIOUS_KEY)
    if not isinstance(previous, dict) or not isinstance(previous.get("yt-dlp"), str):
        raise UserError(
            "There's no earlier yt-dlp version to go back to: `musicorg doctor "
            "--update-ytdlp` records one when it updates."
        )
    pins = [f"{dist}=={previous[dist]}" for dist in YTDLP_PACKAGES if previous.get(dist)]
    with _library_not_in_use(library_root, "doctor --rollback-ytdlp"):
        before = installed_versions()
        _pip("install", *pins)
        return UpdateResult(before, installed_versions())


@contextmanager
def _library_not_in_use(root: Path | None, command: str) -> Iterator[None]:
    if root is None or not library.is_library(root):
        yield
        return
    try:
        lib = library.open(root, write=True, command=command)
    except LibraryLockedError as exc:
        raise UserError(
            f"yt-dlp can't be changed while the library is in use. {exc.message}"
        ) from exc
    with lib:
        yield


def _pip(*args: str) -> None:
    command = [sys.executable, "-m", "pip", *args]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=PIP_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UserError(f"pip couldn't run: {exc}") from exc
    if result.returncode != 0:
        tail = "\n".join((result.stderr or result.stdout).strip().splitlines()[-5:])
        raise UserError(f"pip didn't finish ({' '.join(args)}):\n{tail}")
