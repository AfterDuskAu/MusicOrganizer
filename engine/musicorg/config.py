"""Settings (config.json) and the app's own folders for config, logs and cache.

Under rule 3 of CLAUDE.md this module may write config.json, atomically, and it is the
one place that creates the app's own folders. The library is never stored here.

Setting MUSICORG_HOME puts all three folders under that one folder instead of the
platform defaults. Tests use it so they never touch the real settings.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import platformdirs

from musicorg.errors import ConfigError

APP_NAME = "MusicOrganizer"
CONFIG_FILE_NAME = "config.json"
CONFIG_SCHEMA = 1
HOME_ENV = "MUSICORG_HOME"


@dataclass(frozen=True)
class AppDirs:
    config: Path
    logs: Path
    cache: Path


def app_dirs() -> AppDirs:
    """Where the engine keeps its own files. Nothing is created here; see ensure_app_dir."""
    home = os.environ.get(HOME_ENV)
    if home:
        base = Path(home).expanduser()
        return AppDirs(config=base / "config", logs=base / "logs", cache=base / "cache")
    dirs = platformdirs.PlatformDirs(APP_NAME, appauthor=False)
    return AppDirs(
        config=Path(dirs.user_config_dir),
        logs=Path(dirs.user_log_dir),
        cache=Path(dirs.user_cache_dir),
    )


def config_path() -> Path:
    return app_dirs().config / CONFIG_FILE_NAME


def ensure_app_dir(path: Path) -> Path:
    """Create one of the app's own folders. Refuses any other folder."""
    if path not in _app_dir_list():
        raise ValueError(f"{path} is not one of the app's own folders")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(
            f"Couldn't create the folder {path}: {exc.strerror or exc}. "
            "Check that you have permission to write there."
        ) from exc
    return path


def writable_problem(path: Path) -> str | None:
    """None if one of the app's own folders can be created and written, else why not."""
    try:
        ensure_app_dir(path)
        with tempfile.TemporaryFile(dir=path, prefix=".write-check-"):
            pass
    except ConfigError as exc:
        return exc.message
    except OSError as exc:
        return f"Can't write in {path}: {exc.strerror or exc}."
    return None


def default_data() -> dict[str, Any]:
    return {
        "schema": CONFIG_SCHEMA,
        "last_library": None,
        # Paths to ffmpeg, ffprobe, fpcalc or deno, when they're somewhere unusual.
        "tools": {},
        # Download throttle settings; step 09a fills in the defaults.
        "throttle": {},
    }


class Config:
    """config.json. Keys this version doesn't know about are kept when saving."""

    def __init__(self, data: dict[str, Any] | None = None, path: Path | None = None) -> None:
        self.path = path if path is not None else config_path()
        self.data: dict[str, Any] = data if data is not None else default_data()

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """Read config.json, or return the defaults if it doesn't exist yet."""
        path = path if path is not None else config_path()
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls(None, path)
        except OSError as exc:
            raise ConfigError(
                f"Couldn't read your settings file {path}: {exc.strerror or exc}."
            ) from exc
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"Your settings file {path} is damaged (line {exc.lineno}: {exc.msg}). "
                "Fix it, or move it somewhere else to start again with default settings."
            ) from exc
        if not isinstance(loaded, dict):
            raise ConfigError(
                f"Your settings file {path} is damaged (it should hold a JSON object). "
                "Fix it, or move it somewhere else to start again with default settings."
            )
        data = default_data()
        data.update(loaded)
        return cls(data, path)

    @property
    def last_library(self) -> Path | None:
        value = self.data.get("last_library")
        return Path(value) if isinstance(value, str) and value else None

    @last_library.setter
    def last_library(self, root: Path | None) -> None:
        self.data["last_library"] = str(root) if root is not None else None

    def tool_path(self, tool: str) -> Path | None:
        tools = self.data.get("tools")
        if not isinstance(tools, dict):
            return None
        value = tools.get(tool)
        return Path(value).expanduser() if isinstance(value, str) and value else None

    def set_tool_path(self, tool: str, path: Path | None) -> None:
        tools = self.data.get("tools")
        if not isinstance(tools, dict):
            tools = {}
            self.data["tools"] = tools
        if path is None:
            tools.pop(tool, None)
        else:
            tools[tool] = str(path)

    def save(self) -> None:
        """Write config.json atomically: temp file, fsync, rename over the old one."""
        if self.path.parent in _app_dir_list():
            ensure_app_dir(self.path.parent)
        text = json.dumps(self.data, indent=2, ensure_ascii=False) + "\n"
        try:
            _write_atomic(self.path, text)
        except OSError as exc:
            raise ConfigError(
                f"Couldn't save your settings to {self.path}: {exc.strerror or exc}."
            ) from exc


def _app_dir_list() -> list[Path]:
    dirs = app_dirs()
    return [dirs.config, dirs.logs, dirs.cache]


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def _fsync_dir(folder: Path) -> None:
    """Make a rename durable on macOS and Linux. Windows has no directory fsync."""
    if os.name == "nt":
        return
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
