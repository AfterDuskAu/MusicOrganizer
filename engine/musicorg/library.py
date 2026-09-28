"""Creating and opening a library (docs/LIBRARY_CONTRACT.md sections 1, 5 and 6).

- `init(root)` checks the folder, creates the layout and writes a fresh state.json.
- `open(root, write)`: read-only opens take no lock. Write opens take the single-writer
  lock, then run journal recovery.
- `environment_warnings(root)`: iCloud, Time Machine, free space, long Windows paths
  (contract 6.10). Warnings, never blocks.

Folders and the lock are made by fileops (CLAUDE.md rule 3); nothing here writes.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any

from musicorg import config, fileops, naming, state
from musicorg.errors import ConfigError, UserError
from musicorg.naming import LibraryPaths

log = logging.getLogger(__name__)

FREE_SPACE_WARNING_BYTES = 5 * 1000**3  # 5 GB, as Finder counts them
WINDOWS_ROOT_WARNING_CHARS = 60
# How many files and folders init looks through for music before giving up.
AUDIO_SEARCH_LIMIT = 100_000
TMUTIL_TIMEOUT_S = 15


class Library:
    """An opened library. A writable one holds the lock until `close()`."""

    def __init__(self, paths: LibraryPaths, lock: fileops.LibraryLock | None) -> None:
        self.paths = paths
        self._lock = lock

    @property
    def root(self) -> Path:
        return self.paths.root

    @property
    def writable(self) -> bool:
        return self._lock is not None and self._lock.held

    def load_state(self) -> state.State:
        return state.State.load(self.paths.state_file)

    def close(self) -> None:
        """Release the lock, if this is a writable open. Safe to call twice."""
        if self._lock is not None:
            self._lock.release()

    def __enter__(self) -> Library:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


@dataclass
class InitResult:
    root: Path
    already_library: bool
    created: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "already_library": self.already_library,
            "created": [str(p) for p in self.created],
            "warnings": list(self.warnings),
        }


def root_path(root: Path) -> Path:
    """The library root as an absolute path, with `~` expanded and symlinks resolved."""
    return Path(root).expanduser().resolve()


def is_library(root: Path) -> bool:
    """Whether `root` holds a library (its .musicorg folder)."""
    return (Path(root) / naming.ENGINE_DIR).is_dir()


def init(root: Path, *, command: str = "init", remember: bool = True) -> InitResult:
    """Create a library at `root`, or complete the layout of an existing one.

    Refuses a folder that already holds music (it's probably a rips folder), a folder
    inside another library, and a layout where _Staging/ would be on a different drive
    from Music/. With `remember`, the library becomes the default for later commands.
    """
    paths = LibraryPaths(root_path(root))
    already = is_library(paths.root)
    _check_can_init(paths, already)
    _check_same_volume(paths)

    created = fileops.create_layout(paths)
    with _open_paths(paths, write=True, command=command):
        state.create_if_missing(paths.state_file)

    result = InitResult(root=paths.root, already_library=already, created=created)
    result.warnings = environment_warnings(paths.root)
    if remember:
        try:
            config.remember_library(paths.root)
        except ConfigError as exc:
            result.warnings.append(
                f"Couldn't save this library as the default for later commands: {exc.message}"
            )
    log.info("init %s: already a library=%s, created %d folders", paths.root, already, len(created))
    return result


def open(root: Path, write: bool, *, command: str | None = None) -> Library:
    """Open an existing library. `write=True` takes the lock (LibraryLockedError if another
    process has it) and runs journal recovery. `command` is shown to anyone who finds the
    library locked; it defaults to this process's command line."""
    paths = LibraryPaths(root_path(root))
    if not paths.root.is_dir():
        raise UserError(
            f"The library folder {paths.root} doesn't exist. If it's on an external drive, "
            "check that the drive is connected."
        )
    if not is_library(paths.root):
        raise UserError(
            f"{paths.root} isn't a Music Organizer library. Create one with "
            "`musicorg init <folder>`, or pass the right folder with --library."
        )
    return _open_paths(paths, write=write, command=command)


def environment_warnings(
    root: Path, *, platform: str | None = None, home: Path | None = None
) -> list[str]:
    """Things about where the library lives that could lose or damage files (contract
    6.10). Best effort: a check that can't run gives no warning."""
    platform = platform or sys.platform
    home = home or Path.home()
    root = Path(root)
    warnings: list[str | None] = []
    if platform == "darwin":
        warnings.append(_mac_cloud_warning(root, home))
        warnings.append(_time_machine_warning())
    if platform == "win32":
        warnings.append(_onedrive_warning(root))
        warnings.append(_long_windows_root_warning(root))
    warnings.append(_free_space_warning(root))
    return [w for w in warnings if w]


# ---- init checks -----------------------------------------------------------------------


def _check_can_init(paths: LibraryPaths, already: bool) -> None:
    root = paths.root
    if root.exists() and not root.is_dir():
        raise UserError(f"{root} is a file, not a folder. Choose a folder for the library.")
    if root.parent == root or _same_path(root, Path.home()):
        raise UserError(
            f"Music Organizer won't use {root} itself as a library. Choose a new folder "
            "for it, such as ~/Music Organizer Library."
        )
    if not root.exists() and not root.parent.is_dir():
        raise UserError(
            f"The folder {root.parent} doesn't exist. Create it first, or if it's on an "
            "external drive, check that the drive is connected."
        )
    for parent in root.parents:
        if is_library(parent):
            raise UserError(f"{root} is inside the library {parent}. Choose a folder outside it.")
    for folder in paths.layout_folders:
        if folder.exists() and not folder.is_dir():
            raise UserError(
                f"{folder} is in the way: the library needs a folder with that name, but "
                "there's a file there. Move it away, then try again."
            )
    if root.exists() and not already:
        _refuse_if_holds_music(root)


def _refuse_if_holds_music(root: Path) -> None:
    """Refuse to set up a library on top of existing music, such as a rips folder."""
    found, looked_at = _find_audio(root, AUDIO_SEARCH_LIMIT)
    if found is not None:
        example = found.relative_to(root)
        raise UserError(
            f"{root} already holds music files (for example {example}). Music Organizer "
            "won't set up a library on top of existing music. Choose a new, empty folder "
            "for the library, such as ~/Music Organizer Library. If this is your rips "
            "folder, add it as a source later instead; its files are never changed."
        )
    if looked_at >= AUDIO_SEARCH_LIMIT:
        raise UserError(
            f"{root} already holds a great many files. Choose a new, empty folder for the "
            "library, such as ~/Music Organizer Library."
        )


def _find_audio(root: Path, limit: int) -> tuple[Path | None, int]:
    """The first audio file under `root`, looking at up to `limit` entries, breadth first
    so music near the top is found quickly. Symlinked folders aren't followed."""
    looked_at = 0
    folders = deque([root])
    while folders:
        folder = folders.popleft()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    looked_at += 1
                    if looked_at >= limit:
                        return None, looked_at
                    if naming.is_junk(entry.name):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        folders.append(Path(entry.path))
                    elif naming.is_audio_name(entry.name) and entry.is_file():
                        return Path(entry.path), looked_at
        except OSError as exc:
            log.debug("Skipped %s while looking for music: %s", folder, exc)
    return None, looked_at


def _check_same_volume(paths: LibraryPaths) -> None:
    """Finished downloads move from _Staging/ into Music/ with a rename, which only works
    within one drive (contract 6.4)."""
    if _device(paths.music) != _device(paths.staging):
        raise UserError(
            f"{paths.staging} would be on a different drive from {paths.music}. Music "
            "Organizer moves finished downloads from _Staging into Music, which only works "
            "on the same drive. Remove whatever puts one of them on another drive (such as "
            "a link), or choose another folder for the library."
        )


def _device(path: Path) -> int:
    """The id of the drive `path` is on, or would be on once created."""
    existing = _nearest_existing(path)
    return os.stat(existing).st_dev


def _nearest_existing(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return path


def _open_paths(paths: LibraryPaths, write: bool, command: str | None) -> Library:
    if not write:
        return Library(paths, lock=None)
    lock = fileops.acquire_lock(paths, command or _this_command())
    try:
        fileops.recover_journal(paths)
    except BaseException:
        lock.release()
        raise
    return Library(paths, lock)


def _this_command() -> str:
    args = " ".join(sys.argv[1:])
    return (f"{Path(sys.argv[0]).name} {args}" if args else Path(sys.argv[0]).name)[:200]


# ---- environment warnings --------------------------------------------------------------


def _mac_cloud_warning(root: Path, home: Path) -> str | None:
    real = root.resolve()
    mobile_documents = home / "Library" / "Mobile Documents"
    if _is_within(real, mobile_documents.resolve()):
        return (
            f"The library is inside iCloud Drive ({root}). With “Optimize Mac Storage” on, "
            "macOS can replace music files with download placeholders. Move the library to "
            "a folder outside iCloud Drive, such as ~/Music Organizer Library."
        )
    if _is_within(real, (home / "Library" / "CloudStorage").resolve()):
        return (
            f"The library is inside a cloud-synced folder ({root}). Dropbox, OneDrive and "
            "Google Drive can replace files with online-only placeholders. Move the library "
            "to a folder that isn't synced, such as ~/Music Organizer Library."
        )
    cloud_docs = mobile_documents / "com~apple~CloudDocs"
    for name in ("Desktop", "Documents"):
        synced = os.path.lexists(cloud_docs / name)
        if synced and _is_within(real, (home / name).resolve()):
            return (
                f"The library is in your {name} folder, which iCloud Drive syncs "
                "(Desktop & Documents Folders is on). With “Optimize Mac Storage” on, macOS "
                "can replace music files with download placeholders. Move the library to a "
                "folder outside Desktop and Documents, such as ~/Music Organizer Library."
            )
    return None


def _time_machine_warning() -> str | None:
    output = _tmutil_destinationinfo()
    if output is None or "No destinations configured" not in output:
        return None
    return (
        "Time Machine has no backup disk set up, so nothing is backing up this library. "
        "Set one up in System Settings → General → Time Machine."
    )


def _tmutil_destinationinfo() -> str | None:
    """`tmutil destinationinfo` output (both streams), or None if it couldn't run."""
    try:
        result = subprocess.run(
            ["/usr/bin/tmutil", "destinationinfo"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=TMUTIL_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("Couldn't check Time Machine: %s", exc)
        return None
    return result.stdout + result.stderr


def _onedrive_warning(root: Path) -> str | None:
    real = root.resolve()
    for variable in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        folder = os.environ.get(variable)
        if folder and _is_within(real, Path(folder).resolve()):
            return (
                f"The library is inside OneDrive ({root}). OneDrive can replace files with "
                "online-only placeholders. Move the library to a folder that isn't synced, "
                "such as C:\\Music Organizer Library."
            )
    return None


def _long_windows_root_warning(root: Path) -> str | None:
    length = len(str(root))
    if length <= WINDOWS_ROOT_WARNING_CHARS:
        return None
    return (
        f"The library folder's path is {length} characters long. Windows limits a whole "
        "path to 259 characters, so long song titles will be shortened more than usual. "
        "A shorter path, such as C:\\Music Organizer Library, avoids that."
    )


def _free_space_warning(root: Path) -> str | None:
    free = _free_bytes(_nearest_existing(root))
    if free is None or free >= FREE_SPACE_WARNING_BYTES:
        return None
    return (
        f"Only {free / 1000**3:.1f} GB is free on the drive that holds the library. "
        "Downloads need room; free up at least 5 GB."
    )


def _free_bytes(folder: Path) -> int | None:
    try:
        return shutil.disk_usage(folder).free
    except OSError as exc:
        log.debug("Couldn't check free space on %s: %s", folder, exc)
        return None


def _is_within(path: Path, folder: Path) -> bool:
    """Whether `path` is `folder` or inside it, ignoring case on macOS and Windows."""
    return _fold(folder) in (_fold(path), *(_fold(p) for p in path.parents))


def _same_path(a: Path, b: Path) -> bool:
    try:
        return _fold(a.resolve()) == _fold(b.resolve())
    except OSError:
        return False


def _fold(path: Path) -> str:
    text = os.path.normcase(str(path))
    return text.casefold() if sys.platform in ("darwin", "win32") else text
