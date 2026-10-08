"""Settings (config.json) and the app's own folders for config, logs and cache.

Under rule 3 of CLAUDE.md this module may write config.json (and beside it accounts.json,
downloads.json and devices.json), atomically, and it is the one place that creates the
app's own folders. The library is never stored here.

Setting MUSICORG_HOME puts all three folders under that one folder instead of the
platform defaults. Tests use it so they never touch the real settings.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import platformdirs

from musicorg.errors import ConfigError

APP_NAME = "MusicOrganizer"
CONFIG_FILE_NAME = "config.json"
# The download queue's pace (step 09a; the report's cost estimate uses it too): one
# download at a time, a random pause between them, slower pauses at the start of a
# session, and at most `daily_cap` in any 24 hours.
THROTTLE_DEFAULTS = {
    "pause_min_s": 8,
    "pause_max_s": 25,
    "quiet_start_downloads": 20,
    "quiet_start_min_s": 20,
    "quiet_start_max_s": 40,
    # 250, not the 300 the research found safe: a margin on purpose (owner, 2026-10-01).
    # Songs and videos count alike.
    "daily_cap": 250,
    # How long the whole queue waits after YouTube refuses us (step 09a).
    "youtube_pause_hours": 6,
}
# The most the owner may raise `daily_cap` to (owner, 2026-10-01; it was 300). 500 is the
# most the research ever ran with; above 250 the app warns that YouTube may refuse this
# computer for some hours.
MAX_DAILY_CAP = 500
# The fingerprint gate's thresholds (step 08). Conservative until step 09b calibrates them.
# `match_ber` and `uncertain_ber` are bit error rates; the rest come from the research on the
# owner's rips (Sep 2026): extra audio at either end of a rip was at most 7 s (start) and
# 11 s (end) in 90% of true matches, and no true match had a 10 s gap in the middle.
FINGERPRINT_DEFAULTS: dict[str, float] = {
    "match_ber": 0.15,
    "uncertain_ber": 0.25,
    "min_overlap": 0.6,
    "max_end_extra_s": 15.0,
    "max_middle_gap_s": 10.0,
}
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


MEDIA_KINDS = ("movies", "media")


def default_media_folders() -> dict[str, Path]:
    """Where kept films and videos go until the owner chooses somewhere else: films in
    the computer's own Movies folder (Videos on Windows), and videos that aren't music
    in `Videos` inside it (the owner, 2026-10-08; before that, `Media` in Downloads).
    With `MUSICORG_HOME` set (every test), both are inside that folder, so the real
    ones are never touched."""
    home = os.environ.get(HOME_ENV)
    movies = Path(home).expanduser() / "Movies" if home else Path(platformdirs.user_videos_dir())
    return {"movies": movies, "media": movies / "Videos"}


def media_folders() -> dict[str, Path]:
    """Where films (`movies`) and videos that aren't music (`media`) the owner keeps are
    put, outside the library: the folders chosen in the app's Settings → Downloads
    (config.json), or else the usual ones (`default_media_folders`).

    Nothing is created here: `fileops.keep_media` makes a folder when it first saves."""
    folders = default_media_folders()
    try:
        chosen = Config.load().data.get("media_folders")
    except ConfigError:
        chosen = None
    if isinstance(chosen, dict):
        for kind in MEDIA_KINDS:
            value = chosen.get(kind)
            if isinstance(value, str) and value and Path(value).expanduser().is_absolute():
                folders[kind] = Path(value).expanduser()
    return folders


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
        # Changes to the download pace (THROTTLE_DEFAULTS); empty means the defaults.
        "throttle": {},
        # Changes to the fingerprint gate (FINGERPRINT_DEFAULTS); empty means the defaults.
        "fingerprint": {},
        # When a song has both an explicit and a clean official version and the rip says
        # neither, match the explicit one (step 06).
        "prefer_explicit": True,
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

    @property
    def prefer_explicit(self) -> bool:
        value = self.data.get("prefer_explicit", True)
        return value if isinstance(value, bool) else True

    def throttle(self) -> dict[str, int]:
        """The download pace: the defaults, with any the owner set in config.json."""
        values = dict(THROTTLE_DEFAULTS)
        chosen = self.data.get("throttle")
        if isinstance(chosen, dict):
            for key, value in chosen.items():
                if key in values and isinstance(value, int) and not isinstance(value, bool):
                    values[key] = value
        values["daily_cap"] = min(max(values["daily_cap"], 1), MAX_DAILY_CAP)
        return values

    def set_daily_cap(self, downloads: int) -> None:
        """The owner's own limit on downloads in 24 hours: 1 to MAX_DAILY_CAP."""
        if isinstance(downloads, bool) or not 1 <= downloads <= MAX_DAILY_CAP:
            raise ConfigError(f"Downloads per day should be a number from 1 to {MAX_DAILY_CAP}.")
        chosen = self.data.get("throttle")
        if not isinstance(chosen, dict):
            chosen = self.data["throttle"] = {}
        chosen["daily_cap"] = downloads

    def fingerprint(self) -> dict[str, float]:
        """The fingerprint gate's thresholds: the defaults, with any set in config.json."""
        values = dict(FINGERPRINT_DEFAULTS)
        chosen = self.data.get("fingerprint")
        if isinstance(chosen, dict):
            for key, value in chosen.items():
                if key in values and isinstance(value, int | float) and not isinstance(value, bool):
                    values[key] = float(value)
        return values

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


def remember_ytdlp_versions(versions: dict[str, str | None]) -> None:
    """Record the yt-dlp versions from before an update (`doctor --update-ytdlp`), for
    `doctor --rollback-ytdlp`."""
    cfg = Config.load()
    cfg.data["ytdlp_previous"] = dict(versions)
    cfg.save()


def remember_library(root: Path) -> None:
    """Make `root` the library that commands use when --library isn't given."""
    cfg = Config.load()
    cfg.last_library = root
    cfg.save()


# ---- profiles: whose sign-ins, and one count of downloads for the computer ----------------

PROFILE_ENV = "MUSICORG_PROFILE"
DEFAULT_PROFILE = "default"
_PROFILE_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")


def current_profile() -> str:
    """Which of the app's local profiles this engine is running for. The app names it
    when it starts the engine (`MUSICORG_PROFILE`); without that, as from the command
    line, it's the first profile. A profile is a person's own library and sign-ins on
    this computer: there's nothing online about it."""
    given = os.environ.get(PROFILE_ENV, "")
    return given if _PROFILE_ID.fullmatch(given) else DEFAULT_PROFILE


# ---- sign-ins (imports, v0.3) --------------------------------------------------------------

ACCOUNTS_FILE_NAME = "accounts.json"


def accounts_path() -> Path:
    """Where sign-ins are kept: beside config.json, in the app's own settings folder.
    Never in the library and never in the repo. A separate file from config.json, so
    settings can be shown to someone helping without a login in them."""
    return app_dirs().config / ACCOUNTS_FILE_NAME


def _all_accounts() -> dict[str, dict[str, Any]]:
    """Every profile's sign-ins. A missing or damaged file reads as none: the owner
    just signs in again."""
    try:
        loaded = json.loads(accounts_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    profiles = loaded.get("profiles") if isinstance(loaded, dict) else None
    if not isinstance(profiles, dict):
        return {}
    return {k: dict(v) for k, v in profiles.items() if isinstance(k, str) and isinstance(v, dict)}


def load_accounts() -> dict[str, dict[str, Any]]:
    """This profile's sign-ins, by service ("spotify"). Another profile's are never
    given out: one person's Spotify isn't the next person's."""
    mine = _all_accounts().get(current_profile()) or {}
    return {k: dict(v) for k, v in mine.items() if isinstance(k, str) and isinstance(v, dict)}


def save_account(service: str, account: dict[str, Any] | None) -> None:
    """Save one service's sign-in for this profile, or remove it (`None`). Written
    atomically, and readable by this user only (the temp file it's written through is
    made that way)."""
    everyone = _all_accounts()
    mine = everyone.get(current_profile()) or {}
    if account is None:
        mine.pop(service, None)
    else:
        mine[service] = dict(account)
    if mine:
        everyone[current_profile()] = mine
    else:
        everyone.pop(current_profile(), None)
    path = accounts_path()
    ensure_app_dir(path.parent)
    text = json.dumps({"profiles": everyone}, indent=2, ensure_ascii=False) + "\n"
    try:
        _write_atomic(path, text)
    except OSError as exc:
        raise ConfigError(
            f"Couldn't save the sign-in on this computer: {exc.strerror or exc}."
        ) from exc


# ---- devices paired for sharing (2026-10-04) -----------------------------------------------

DEVICES_FILE_NAME = "devices.json"


def devices_path() -> Path:
    """Where the devices paired with this computer are kept (`sharing`): beside
    config.json, in the app's own settings folder. Never in the library and never in
    the repo. What's kept of a device's key is its SHA-256, so the file can't be used
    to pass for a device."""
    return app_dirs().config / DEVICES_FILE_NAME


def _all_devices() -> dict[str, list[dict[str, Any]]]:
    """Every profile's paired devices. A missing or damaged file reads as none: the
    owner just pairs again."""
    try:
        loaded = json.loads(devices_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    profiles = loaded.get("profiles") if isinstance(loaded, dict) else None
    if not isinstance(profiles, dict):
        return {}
    return {
        k: [dict(d) for d in v if isinstance(d, dict)]
        for k, v in profiles.items()
        if isinstance(k, str) and isinstance(v, list)
    }


def load_devices() -> list[dict[str, Any]]:
    """The devices paired with this profile's library. Another profile's are never
    given out: a phone paired for one person's music can't read the next person's."""
    return _all_devices().get(current_profile()) or []


def save_devices(devices: list[dict[str, Any]]) -> None:
    """Replace this profile's paired devices. Written atomically, and readable by this
    user only (the temp file it's written through is made that way)."""
    everyone = _all_devices()
    if devices:
        everyone[current_profile()] = [dict(d) for d in devices]
    else:
        everyone.pop(current_profile(), None)
    path = devices_path()
    ensure_app_dir(path.parent)
    text = json.dumps({"profiles": everyone}, indent=2, ensure_ascii=False) + "\n"
    try:
        _write_atomic(path, text)
    except OSError as exc:
        raise ConfigError(
            f"Couldn't save the paired device on this computer: {exc.strerror or exc}."
        ) from exc


# ---- add-ons: where movies and channels are listed -----------------------------------------

ADDONS_FILE_NAME = "addons.json"


def addons_path() -> Path:
    """Where the owner's add-ons are kept: beside config.json, in the app's own settings
    folder. Each is an add-on's address and what its manifest said, nothing private."""
    return app_dirs().config / ADDONS_FILE_NAME


def load_addons() -> list[dict[str, Any]] | None:
    """The add-ons the owner has, in their order. None when none were ever saved (the
    app then starts with its own few); a damaged file reads the same way."""
    try:
        loaded = json.loads(addons_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    listed = loaded.get("addons") if isinstance(loaded, dict) else None
    if not isinstance(listed, list):
        return None
    return [dict(one) for one in listed if isinstance(one, dict)]


def addons_offered() -> list[str] | None:
    """The addresses of the app's own add-ons that have been put in the owner's list at
    some time (so one they removed isn't put back by itself). None: not kept yet."""
    try:
        loaded = json.loads(addons_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    offered = loaded.get("offered") if isinstance(loaded, dict) else None
    if not isinstance(offered, list):
        return None
    return [one for one in offered if isinstance(one, str)]


def save_addons(addons: list[dict[str, Any]], *, offered: list[str] | None = None) -> None:
    """Replace the list of add-ons. Written atomically. `offered`: the app's own add-ons
    put in the list so far; left as it was when not given."""
    path = addons_path()
    ensure_app_dir(path.parent)
    if offered is None:
        offered = addons_offered()
    kept: dict[str, Any] = {"addons": addons}
    if offered is not None:
        kept["offered"] = offered
    text = json.dumps(kept, indent=2, ensure_ascii=False) + "\n"
    try:
        _write_atomic(path, text)
    except OSError as exc:
        raise ConfigError(
            f"Couldn't save the list of add-ons on this computer: {exc.strerror or exc}."
        ) from exc


# ---- the day's downloads, for the whole computer ------------------------------------------

DOWNLOAD_TIMES_FILE_NAME = "downloads.json"


def download_times_path() -> Path:
    return app_dirs().config / DOWNLOAD_TIMES_FILE_NAME


def load_download_times() -> list[str]:
    """When this computer's recent downloads started, whichever library they went into.
    YouTube counts a computer, not a person: with several profiles the daily limit has
    to be one count. Missing or damaged reads as none."""
    try:
        loaded = json.loads(download_times_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    times = loaded.get("times") if isinstance(loaded, dict) else None
    return [t for t in times if isinstance(t, str)] if isinstance(times, list) else []


def save_download_times(times: list[str]) -> None:
    """Replace the list (the queue passes the last 24 hours' worth)."""
    path = download_times_path()
    ensure_app_dir(path.parent)
    try:
        _write_atomic(path, json.dumps({"times": list(times)}, indent=2) + "\n")
    except OSError as exc:
        raise ConfigError(
            f"Couldn't save the count of downloads to {path}: {exc.strerror or exc}."
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


def save_media_folder(kind: str, folder: Path | None) -> Config:
    """Set where kept films (`movies`) or kept videos (`media`) go, in config.json.
    None: the usual folder again. Only the choice is written; no folder is made."""
    if kind not in MEDIA_KINDS:
        raise ValueError(f"not a kind of kept media: {kind!r}")
    config = Config.load()
    chosen = config.data.get("media_folders")
    if not isinstance(chosen, dict):
        chosen = config.data["media_folders"] = {}
    if folder is None:
        chosen.pop(kind, None)
    else:
        folder = folder.expanduser()
        if not folder.is_absolute() or not folder.is_dir():
            raise ConfigError(f"{folder} isn't a folder on this computer.")
        chosen[kind] = str(folder)
    config.save()
    return config


# How fast a torrent may send what it has fetched on to others (the owner, 2026-10-08):
# no limit, 5, 3 or 1 megabyte a second, or nothing at all. Bytes a second; None: no limit.
TORRENT_UPLOADS: dict[str, int | None] = {
    "max": None, "5": 5_000_000, "3": 3_000_000, "1": 1_000_000, "none": 0,
}  # fmt: skip
TORRENT_UPLOAD_DEFAULT = "max"


def torrent_upload() -> str:
    """The owner's choice of `TORRENT_UPLOADS`, from config.json."""
    try:
        chosen = Config.load().data.get("torrent_upload")
    except ConfigError:
        chosen = None
    return (
        chosen if isinstance(chosen, str) and chosen in TORRENT_UPLOADS else TORRENT_UPLOAD_DEFAULT
    )


def save_torrent_upload(limit: str) -> Config:
    if limit not in TORRENT_UPLOADS:
        raise ConfigError("The upload speed should be one of: " + ", ".join(TORRENT_UPLOADS) + ".")
    config = Config.load()
    config.data["torrent_upload"] = limit
    config.save()
    return config


def save_daily_cap(downloads: int) -> Config:
    """Set the owner's downloads-per-day limit in config.json and save it."""
    config = Config.load()
    config.set_daily_cap(downloads)
    config.save()
    return config
