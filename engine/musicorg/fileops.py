"""The only module that creates, writes, moves or deletes anything in a library.

CLAUDE.md rule 3; enforced by tests/test_write_rules.py.

Step 03a (this far): creating the layout, and the single-writer lock (contract 6.1).
Step 03b adds the path guard, no-overwrite moves, the journal, crash recovery, undo,
plans, staging cleanup and exports.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import socket
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from musicorg import __version__
from musicorg.errors import LibraryLockedError, UserError
from musicorg.naming import LibraryPaths

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

log = logging.getLogger(__name__)


# ---- layout ----------------------------------------------------------------------------


def create_layout(paths: LibraryPaths) -> list[Path]:
    """Create the library root and its folders (contract section 1). Returns the folders
    that were created. Folders already there are left as they are.

    The root's parent must already exist: a missing parent usually means a drive that
    isn't connected, and creating it would put the library on the wrong disk.
    """
    created = []
    for folder in (paths.root, *paths.layout_folders):
        if folder.is_dir():
            continue
        try:
            folder.mkdir()
        except FileExistsError as exc:
            if folder.is_dir():  # made by someone else in the meantime
                continue
            raise UserError(
                f"{folder} is in the way: the library needs a folder with that name, "
                "but something else is there. Move it away, then try again."
            ) from exc
        except OSError as exc:
            raise UserError(f"Couldn't create the folder {folder}: {exc.strerror or exc}.") from exc
        log.info("Created %s", folder)
        created.append(folder)
    return created


# ---- the single-writer lock (contract 6.1) ---------------------------------------------


class LibraryLock:
    """The library's single-writer lock. Released by `release()` or when the process ends."""

    def __init__(self, paths: LibraryPaths, fd: int) -> None:
        self.paths = paths
        self._fd: int | None = fd

    @property
    def held(self) -> bool:
        return self._fd is not None

    def release(self) -> None:
        """Remove lock.info, then unlock. Safe to call twice."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            # Before unlocking, so a process that takes the lock next can't lose its own.
            self.paths.lock_info_file.unlink(missing_ok=True)
        except OSError as exc:
            log.debug("Couldn't remove %s: %s", self.paths.lock_info_file, exc)
        try:
            _unlock(fd)
        except OSError as exc:
            log.debug("Couldn't unlock %s: %s", self.paths.lock_file, exc)
        finally:
            os.close(fd)


def acquire_lock(paths: LibraryPaths, command: str) -> LibraryLock:
    """Take the library's lock without waiting, and write who holds it to lock.info.

    Raises LibraryLockedError, naming the holder from lock.info, if another process (or
    another open in this process) has it. A lock.info left behind by a process that
    crashed doesn't matter: only the lock itself counts.
    """
    lock_file = paths.lock_file
    try:
        fd = os.open(lock_file, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    except OSError as exc:
        raise UserError(
            f"Couldn't open the library's lock file {lock_file}: {exc.strerror or exc}."
        ) from exc
    try:
        got_it = _try_lock(fd)
    except OSError as exc:
        os.close(fd)
        raise UserError(
            f"Couldn't lock the library ({lock_file}: {exc.strerror or exc}). If the library "
            "is on a network drive, move it to a drive attached to this computer."
        ) from exc
    if not got_it:
        os.close(fd)
        holder = read_lock_info(paths)
        raise LibraryLockedError(describe_lock_holder(holder), holder)

    lock = LibraryLock(paths, fd)
    info = {
        "pid": os.getpid(),
        "command": command,
        "started_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host": socket.gethostname(),
        "engine_version": __version__,
    }
    try:
        _write_small_file(paths.lock_info_file, json.dumps(info, indent=2) + "\n")
    except OSError as exc:  # only used to explain who holds the lock
        log.warning("Couldn't write %s: %s", paths.lock_info_file, exc)
    log.info("Took the library lock for `%s`", command)
    return lock


def read_lock_info(paths: LibraryPaths) -> dict[str, Any] | None:
    """What the lock holder wrote to lock.info, or None if it's missing or unreadable."""
    try:
        data = json.loads(paths.lock_info_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def describe_lock_holder(holder: dict[str, Any] | None, now: datetime | None = None) -> str:
    """The message a second writer sees, e.g. "Another Music Organizer process (PID 4412,
    `queue run`, started 14:02) is using this library. ..." Times are local; the date is
    added if it isn't today."""
    details = []
    pid = holder.get("pid") if holder else None
    if isinstance(pid, int) and not isinstance(pid, bool):
        details.append(f"PID {pid}")
    command = holder.get("command") if holder else None
    if isinstance(command, str) and command:
        details.append(f"`{command}`")
    started = _parse_time(holder.get("started_at")) if holder else None
    if started is not None:
        local = started.astimezone()
        today = (now or datetime.now(UTC)).astimezone().date()
        when = f"{local:%H:%M}" if local.date() == today else f"{local:%Y-%m-%d %H:%M}"
        details.append(f"started {when}")
    host = holder.get("host") if holder else None
    if isinstance(host, str) and host and host != socket.gethostname():
        details.append(f"on the computer {host}")
    who = f" ({', '.join(details)})" if details else ""
    return (
        f"Another Music Organizer process{who} is using this library. "
        "Wait for it to finish, or stop it, then try again."
    )


def recover_journal(paths: LibraryPaths) -> None:
    """Finish or roll back operations a crash interrupted (contract 6.3).

    library.open calls this on every write open, right after taking the lock. The
    journal arrives in step 03b; until then there is nothing to recover.
    """


# ---- helpers ---------------------------------------------------------------------------


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _write_small_file(path: Path, text: str) -> None:
    """Write via a temp file and a rename, so a reader never sees half a file."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


if sys.platform == "win32":

    def _try_lock(fd: int) -> bool:
        """Lock byte 0 of the lock file (msvcrt.locking), without waiting."""
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EDEADLOCK):
                return False
            raise
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:

    def _try_lock(fd: int) -> bool:
        """flock(LOCK_EX | LOCK_NB): exclusive, without waiting."""
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
                return False
            raise
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
