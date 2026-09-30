"""The only module that creates, writes, moves or deletes anything in a library.

CLAUDE.md rule 3 (enforced by tests/test_write_rules.py). It implements the safety
guarantees of docs/LIBRARY_CONTRACT.md section 6. In order below:

- the layout and the single-writer lock (step 03a)
- `guard()`: every path an operation changes must resolve inside a managed folder
- the journal, and batches: `batch()`, `open_batch()`, `resume_batch()`, `close_batch()`
- `_move_no_overwrite()`: the one way anything moves
- the operations: `stage_path`, `commit`, `copy_in`, `supersede`, `move`, `trash`,
  `write_tags`, `write_sidecar`; and for step 09b's jobs, `stage_copy` and `set_aside`
- `recover_journal()`: finishes or rolls back what a crash interrupted
- `undo()` and `list_batches()`
- plans with preconditions: `Plan`, `save_plan`, `load_plan`, `validate`, `check_op`
- staging cleanup (`discard_staged`, `clean_staging`) and `write_export`

The journal
-----------
`.musicorg/journal/YYYY-MM-DD.jsonl` (the date in UTC). One JSON object per line, each
with `type`, `ts` and `batch_id`:

- `batch_start`: `kind`, `open` (an open batch, made by `apply`), `undo_of` for an undo.
- `intent`: `op_id`, `op`, and what the operation is about to do, with the before-state.
  Flushed and fsynced before the operation touches anything.
- `reserved`: the name a move reserved (`placeholder`), fsynced before the move.
- `done`: the operation finished; `path` is where its file ended up.
- `failed`: it failed and was rolled back in the same run (`error`).
- `recovered`: after a crash, recovery `completed` it or `rolled_back` it.
- `batch_end`: `summary`, plus `interrupted` when recovery closed it after a crash.

Paths inside the library are stored relative to its root, with `/` ("Music/A/B.m4a"), so
the journal still works when the library's drive is mounted somewhere else. Only a
`copy_in` origin, which is outside the library, is stored as an absolute path.

Deletes
-------
Nothing here deletes user data. The only files and folders ever removed outright are:
regular files and empty folders inside `_Staging/`; an empty name a move reserved and
didn't use (contract 6.4); and, during undo, folders the undone batch created that are
empty again (ignoring `.DS_Store` and the other junk names, which go with them).
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import re
import secrets
import socket
import stat
import sys
import tempfile
import threading
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePath, PurePosixPath
from typing import Any, Protocol

import send2trash

from musicorg import __version__, naming, tags
from musicorg.errors import (
    CrossVolumeError,
    FileInUseError,
    FileOperationError,
    IntegrityError,
    LibraryLockedError,
    MusicOrgError,
    NotFoundError,
    OutsideLibraryError,
    SourceChangedError,
    UndoError,
    UserError,
)
from musicorg.naming import LibraryPaths

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

log = logging.getLogger(__name__)

# The folders `guard` accepts. MANAGED is all of them (contract section 1).
MANAGED = (naming.MUSIC_DIR, naming.REPLACED_DIR, naming.STAGING_DIR, naming.ENGINE_DIR)
MUSIC = (naming.MUSIC_DIR,)
REPLACED = (naming.REPLACED_DIR,)
STAGING = (naming.STAGING_DIR,)
ENGINE = (naming.ENGINE_DIR,)

# docs/ENGINE_API.md → Enums.
BATCH_KINDS = frozenset({"replace", "adopt", "lyrics", "artwork", "tidy", "undo", "demo"})
PLAN_KINDS = frozenset({"replace", "adopt", "lyrics", "artwork", "tidy"})
OPERATIONS = (
    "commit", "copy_in", "supersede", "restore", "move", "trash", "write_tags", "write_sidecar",
)  # fmt: skip

# Windows: a file another app has open can't be moved. Try again 5 times over ~2 s.
PERMISSION_RETRIES = 5
PERMISSION_RETRY_S = 0.4
_RETRY_PERMISSION_ERRORS = sys.platform == "win32"
_sleep = time.sleep

# F_FULLFSYNC on macOS (see _sync_file). It takes ~0.15 s on a spinning disk, so the
# tests switch it off (tests/conftest.py) and check it separately.
_FULL_FSYNC = sys.platform == "darwin"

SHA1_HEAD_BYTES = 1024 * 1024
_CHUNK = 1024 * 1024
# A reserved name is only removed by recovery if it was made after its intent; this
# allows for file systems that store times in 2-second steps (FAT, exFAT).
_CLOCK_SLACK_S = 2.0
_JOURNAL_FILE = re.compile(r"\d{4}-\d{2}-\d{2}\.jsonl")
_SIDECAR_SUFFIX = re.compile(r"\.[A-Za-z0-9]{1,8}")
_PLAN_ID = re.compile(r"p_[0-9A-Za-z-]{1,64}")
_O_BINARY = getattr(os, "O_BINARY", 0)
_WINDOWS_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
_WINDOWS_NOT_SAME_DEVICE = 17  # ERROR_NOT_SAME_DEVICE

FILE_CHANGED = "the file changed since the plan was made"


_MISSING = object()


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

# Libraries this process holds the lock of, by root. Operations refuse to run without it.
_held_locks: dict[str, LibraryLock] = {}


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
        if _held_locks.get(_key(self.paths.root)) is self:
            del _held_locks[_key(self.paths.root)]
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
        fd = os.open(lock_file, os.O_RDWR | os.O_CREAT | _O_BINARY, 0o644)
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
    _held_locks[_key(paths.root)] = lock
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


def holds_lock(paths: LibraryPaths) -> bool:
    """Whether this process has the library open for writing."""
    lock = _held_locks.get(_key(paths.root))
    return lock is not None and lock.held


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


def _require_lock(paths: LibraryPaths, what: str) -> None:
    if not holds_lock(paths):
        raise RuntimeError(
            f"{what} needs the library open for writing: library.open({paths.root}, write=True)"
        )


# ---- the path guard (contract 6.2) -----------------------------------------------------


def guard(paths: LibraryPaths, path: PurePath | str, allow: tuple[str, ...] = MANAGED) -> Path:
    """`path` resolved (following symlinks), if that's inside one of the library's `allow`
    folders; otherwise OutsideLibraryError.

    Called on every path an operation changes, sources and destinations alike. A managed
    folder itself doesn't count as inside it: nothing may replace or remove `Music/`.
    """
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError) as exc:  # e.g. a loop of links
        raise OutsideLibraryError(_outside_message(path, allow), path) from exc
    for name in allow:
        if _inside(resolved, paths.root / name):
            return resolved
    raise OutsideLibraryError(_outside_message(path, allow), path)


def _outside_message(path: PurePath | str, allow: tuple[str, ...]) -> str:
    names = [f"{name}" for name in allow]
    where = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]
    return (
        f"{path} isn't inside the library's {where} folder, so Music Organizer left it "
        "alone. It only ever changes files inside its own library."
    )


def _inside(path: PurePath, folder: PurePath) -> bool:
    """Whether `path` is strictly inside `folder`. Case matters except on Windows, so a
    differently-cased path is refused rather than guessed at."""
    key = _key(folder)
    return any(_key(parent) == key for parent in path.parents)


def _within(path: PurePath, folder: PurePath) -> bool:
    """Whether `path` is `folder` or inside it, ignoring case on macOS and Windows. For
    refusals, where erring towards "yes" is the safe side."""
    key = _fold_path(folder)
    return key in (_fold_path(path), *(_fold_path(p) for p in path.parents))


def _key(path: PurePath) -> str:
    return os.path.normcase(str(path))


def _fold_path(path: PurePath) -> str:
    text = unicodedata.normalize("NFC", os.path.normcase(str(path)))
    return text.casefold() if sys.platform in ("darwin", "win32") else text


# ---- the journal -----------------------------------------------------------------------

_journal_lock = threading.Lock()


def _append(paths: LibraryPaths, record: dict[str, Any], *, sync: bool) -> None:
    """Append one line to today's journal file. With `sync`, it's on the disk itself
    (F_FULLFSYNC on macOS) before this returns."""
    now = datetime.now(UTC)
    line = _encode_line({"type": record["type"], "ts": _stamp(now), **record})
    with _journal_lock:
        folder = _ensure_folder(paths, paths.journal, ENGINE)
        path = folder / f"{now:%Y-%m-%d}.jsonl"
        is_new = not path.exists()
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | _O_BINARY, 0o644)
        try:
            view = memoryview(line)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            if sync:
                _sync_file(fd)
        finally:
            os.close(fd)
        if is_new:
            _sync_folder(folder)


def _encode_line(record: dict[str, Any]) -> bytes:
    """One JSON line. Readable UTF-8, unless a name holds bytes that aren't valid UTF-8
    (possible in a rip's file name), in which case that line is written as ASCII escapes."""
    try:
        return (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    except UnicodeEncodeError:
        return (json.dumps(record, ensure_ascii=True) + "\n").encode("ascii")


def _stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _journal_lines(paths: LibraryPaths) -> Iterator[dict[str, Any]]:
    """Every readable journal line, oldest first. A line cut short by a crash is skipped."""
    try:
        files = sorted(p for p in paths.journal.iterdir() if _JOURNAL_FILE.fullmatch(p.name))
    except FileNotFoundError:
        return
    except OSError as exc:
        log.warning("Couldn't read the journal folder %s: %s", paths.journal, exc)
        return
    for path in files:
        try:
            data = path.read_bytes()
        except OSError as exc:
            log.warning("Couldn't read the journal file %s: %s", path, exc)
            continue
        lines = data.split(b"\n")
        for number, raw in enumerate(lines, 1):
            if not raw.strip():
                continue
            try:
                record = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                last = number == len(lines)  # no newline yet: a write in progress, or a crash
                (log.debug if last else log.warning)(
                    "Skipped a damaged line in the journal: %s line %d", path.name, number
                )
                continue
            if isinstance(record, dict) and isinstance(record.get("type"), str):
                yield record


@dataclass
class OpRecord:
    """One operation, as the journal tells it."""

    batch_id: str
    op_id: int
    op: str
    intent: dict[str, Any]
    reserved: dict[str, Any] | None = None
    done: dict[str, Any] | None = None
    failed: dict[str, Any] | None = None
    recovered: dict[str, Any] | None = None

    @property
    def status(self) -> str:
        """`done`, `failed` (rolled back), or `pending` (interrupted, not yet recovered)."""
        if self.done is not None:
            return "done"
        if self.recovered is not None:
            return "done" if self.recovered.get("decision") == "completed" else "failed"
        if self.failed is not None:
            return "failed"
        return "pending"

    @property
    def result_path(self) -> str | None:
        """Where the operation left its file, library-relative."""
        for record in (self.done, self.recovered):
            if record and isinstance(record.get("path"), str):
                return record["path"]
        return None


@dataclass
class BatchRecord:
    """One batch, as the journal tells it."""

    batch_id: str
    kind: str
    started_at: str
    open_batch: bool
    undo_of: str | None
    ops: list[OpRecord] = field(default_factory=list)
    end: dict[str, Any] | None = None
    undone_by: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        """docs/ENGINE_API.md → Batch status: `open`, `closed` or `interrupted`."""
        if self.end is None:
            return "open"
        return "interrupted" if self.end.get("interrupted") else "closed"


def read_journal(lib: LibraryPaths | _HasPaths) -> dict[str, BatchRecord]:
    """Every batch in the journal, oldest first. Needs no lock."""
    paths = _paths_of(lib)
    batches: dict[str, BatchRecord] = {}
    ops: dict[tuple[str, int], OpRecord] = {}
    for record in _journal_lines(paths):
        kind = record["type"]
        batch_id = record.get("batch_id")
        if not isinstance(batch_id, str):
            continue
        if kind == "batch_start":
            if batch_id not in batches:
                undo_of = record.get("undo_of")
                batches[batch_id] = BatchRecord(
                    batch_id=batch_id,
                    kind=str(record.get("kind", "")),
                    started_at=str(record.get("ts", "")),
                    open_batch=record.get("open") is True,
                    undo_of=undo_of if isinstance(undo_of, str) else None,
                )
            continue
        batch_record = batches.get(batch_id)
        if batch_record is None:
            continue
        if kind == "batch_end":
            if batch_record.end is None:
                batch_record.end = record
            continue
        op_id = record.get("op_id")
        if not isinstance(op_id, int) or isinstance(op_id, bool):
            continue
        if kind == "intent":
            if (batch_id, op_id) not in ops:
                op = OpRecord(batch_id, op_id, str(record.get("op", "")), record)
                ops[(batch_id, op_id)] = op
                batch_record.ops.append(op)
        elif kind in ("reserved", "done", "failed", "recovered"):
            op = ops.get((batch_id, op_id))
            if op is not None and getattr(op, kind) is None:
                setattr(op, kind, record)
    for batch_record in batches.values():
        undone = batches.get(batch_record.undo_of or "")
        if undone is not None:
            undone.undone_by.append(batch_record.batch_id)
    return batches


# ---- batches ---------------------------------------------------------------------------


class _HasPaths(Protocol):
    @property
    def paths(self) -> LibraryPaths: ...


def _paths_of(lib: LibraryPaths | _HasPaths) -> LibraryPaths:
    """Functions here take a library.Library or its LibraryPaths."""
    return lib if isinstance(lib, LibraryPaths) else lib.paths


_state_lock = threading.RLock()
_active_batches: set[str] = set()  # in-process batches that haven't ended
_in_flight: set[tuple[str, int]] = set()  # operations running right now in this process
_next_op_ids: dict[str, int] = {}


class Batch:
    """A group of operations journaled, summarised and undone together. Made by `batch()`
    (in-process) or `open_batch()` / `resume_batch()` (open batches, step 09b)."""

    def __init__(self, paths: LibraryPaths, batch_id: str, kind: str, *, open_batch: bool) -> None:
        self.paths = paths
        self.batch_id = batch_id
        self.kind = kind
        self.open_batch = open_batch
        self.counts: Counter[str] = Counter()  # finished operations, by operation
        self.failed = 0
        self.ended = False

    def __repr__(self) -> str:
        return f"Batch({self.batch_id!r}, kind={self.kind!r})"

    def summary(self) -> dict[str, Any]:
        return {"operations": dict(sorted(self.counts.items())), "failed": self.failed}

    def _next_op_id(self) -> int:
        with _state_lock:
            op_id = _next_op_ids.get(self.batch_id, 1)
            _next_op_ids[self.batch_id] = op_id + 1
        return op_id

    def _write(
        self, line_type: str, op_id: int | None = None, *, sync: bool = False, **fields: Any
    ) -> None:
        record: dict[str, Any] = {"type": line_type, "batch_id": self.batch_id}
        if op_id is not None:
            record["op_id"] = op_id
        record.update(fields)
        _append(self.paths, record, sync=sync)


@contextmanager
def batch(
    lib: LibraryPaths | _HasPaths, kind: str, *, undo_of: str | None = None
) -> Iterator[Batch]:
    """An in-process batch: `with fileops.batch(lib, "adopt") as b: fileops.copy_in(b, ...)`.

    The library must be open for writing. The batch ends when the block does. If the
    process dies first, the next write open finds it without an end and closes it as
    interrupted, after recovering its operations.
    """
    b = _start_batch(_paths_of(lib), kind, open_batch=False, undo_of=undo_of)
    with _state_lock:
        _active_batches.add(b.batch_id)
    try:
        yield b
    except Exception as exc:
        _end_batch(b, error=_describe(exc))
        raise
    else:
        _end_batch(b)
    finally:
        with _state_lock:
            _active_batches.discard(b.batch_id)


def open_batch(lib: LibraryPaths | _HasPaths, kind: str) -> Batch:
    """Start an open batch (step 09b's `apply`): it stays open across processes until
    `close_batch`, and recovery never treats it as crashed. Jobs join it with
    `resume_batch`."""
    return _start_batch(_paths_of(lib), kind, open_batch=True, undo_of=None)


def resume_batch(lib: LibraryPaths | _HasPaths, batch_id: str) -> Batch:
    """The open batch `batch_id`, so a job can add operations to it."""
    paths = _paths_of(lib)
    _require_lock(paths, "Adding to a batch")
    record = read_journal(paths).get(batch_id)
    if record is None:
        raise NotFoundError(f"There's no batch called {batch_id} in this library's journal.")
    if not record.open_batch or record.end is not None:
        raise UserError(f"Batch {batch_id} has already ended, so nothing more can be added to it.")
    with _state_lock:
        highest = max((op.op_id for op in record.ops), default=0)
        _next_op_ids[batch_id] = max(_next_op_ids.get(batch_id, 1), highest + 1)
    return Batch(paths, batch_id, record.kind, open_batch=True)


def close_batch(
    lib: LibraryPaths | _HasPaths, batch_id: str, *, closed_by: str | None = None
) -> None:
    """End an open batch, with a summary from the journal. Does nothing if it has ended."""
    paths = _paths_of(lib)
    _require_lock(paths, "Closing a batch")
    record = read_journal(paths).get(batch_id)
    if record is None:
        raise NotFoundError(f"There's no batch called {batch_id} in this library's journal.")
    if record.end is not None:
        return
    b = Batch(paths, batch_id, record.kind, open_batch=record.open_batch)
    b.counts, b.failed = _tally(record)
    _end_batch(b, closed_by=closed_by)


def _start_batch(paths: LibraryPaths, kind: str, *, open_batch: bool, undo_of: str | None) -> Batch:
    _require_lock(paths, "A batch")
    if kind not in BATCH_KINDS:
        raise ValueError(f"unknown batch kind {kind!r}; see docs/ENGINE_API.md → Enums")
    batch_id = _new_id("b")
    with _state_lock:
        _next_op_ids[batch_id] = 1
    b = Batch(paths, batch_id, kind, open_batch=open_batch)
    fields: dict[str, Any] = {"kind": kind, "open": open_batch}
    if undo_of is not None:
        fields["undo_of"] = undo_of
    fields.update(pid=os.getpid(), engine_version=__version__)
    b._write("batch_start", sync=True, **fields)
    log.info("Started batch %s (%s)", batch_id, kind)
    return b


def _end_batch(
    b: Batch,
    *,
    error: str | None = None,
    interrupted: bool = False,
    closed_by: str | None = None,
) -> None:
    fields: dict[str, Any] = {"summary": b.summary()}
    if error is not None:
        fields["error"] = error
    if interrupted:
        fields["interrupted"] = True
    if closed_by is not None:
        fields["closed_by"] = closed_by
    b._write("batch_end", sync=True, **fields)
    b.ended = True
    log.info("Ended batch %s: %s", b.batch_id, fields["summary"])


def _tally(record: BatchRecord) -> tuple[Counter[str], int]:
    counts: Counter[str] = Counter(op.op for op in record.ops if op.status == "done")
    failed = sum(op.status == "failed" for op in record.ops)
    return counts, failed


def _new_id(prefix: str) -> str:
    """e.g. b_20260929-141503-3fa2c1: sortable, readable, and unique enough to type."""
    return f"{prefix}_{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


class _Op:
    def __init__(self, op_id: int) -> None:
        self.op_id = op_id
        self.result: dict[str, Any] = {}


@contextmanager
def _operation(b: Batch, op: str, **intent: Any) -> Iterator[_Op]:
    """Journal an operation: `intent` (fsynced) now, then `done` with `result` when the
    block finishes, or `failed` if it raises an ordinary error (the block rolls back its
    own changes first). A crash, or anything that isn't an `Exception`, writes nothing
    more: the next write open's recovery deals with it."""
    _require_lock(b.paths, "A file operation")
    if b.ended:
        raise RuntimeError(f"batch {b.batch_id} has ended")
    handle = _Op(b._next_op_id())
    fields = {k: v for k, v in intent.items() if v is not None}
    b._write("intent", handle.op_id, sync=True, op=op, **fields)
    key = (b.batch_id, handle.op_id)
    with _state_lock:
        _in_flight.add(key)
    try:
        yield handle
    except Exception as exc:
        b.failed += 1
        b._write("failed", handle.op_id, error=_describe(exc))
        raise
    else:
        b.counts[op] += 1
        b._write("done", handle.op_id, **handle.result)
    finally:
        with _state_lock:
            _in_flight.discard(key)


def _describe(exc: BaseException) -> str:
    if isinstance(exc, MusicOrgError):
        return exc.message
    return f"{type(exc).__name__}: {exc}"


# ---- moving without overwriting (contract 6.4) -----------------------------------------


def _move_no_overwrite(b: Batch, op: _Op, src: Path, target: Path, new_dirs: list[Path]) -> Path:
    """The one way anything moves. The operation's intent is already journaled.

    1. Create the missing folders (listed in the intent).
    2. Reserve the first free name from `naming.candidate_names(target)` with
       `open(name, "xb")`, which fails rather than overwrite.
    3. Journal the reserved name, then `os.replace(src, reserved)`.

    On an error everything is put back: the reserved name and new folders are removed
    and `src` is where it was. Never `shutil.move`, which could copy and delete.
    """
    paths = b.paths
    placeholder: Path | None = None
    try:
        _make_dirs(paths, new_dirs)
        placeholder = _reserve(target)
        b._write("reserved", op.op_id, sync=True, placeholder=_rel(paths, placeholder))
        _replace(src, placeholder)
    except Exception:
        if placeholder is not None:
            _remove_placeholder(paths, placeholder)
        _prune_dirs(paths, new_dirs)
        raise
    _sync_folder(placeholder.parent)
    if _key(src.parent) != _key(placeholder.parent):
        _sync_folder(src.parent)
    return placeholder


def _reserve(target: Path) -> Path:
    """Create an empty file at the first free candidate name and return it. A name is
    taken if anything in the folder has it, ignoring case and Unicode form."""
    taken = _taken_names(target.parent)
    for candidate in naming.candidate_names(target):
        if _fold_name(candidate.name) in taken:
            continue
        try:
            with open(candidate, "xb"):
                pass
        except FileExistsError:
            continue
        except PermissionError:
            if sys.platform == "win32" and os.path.lexists(candidate):
                continue  # Windows reports an existing folder this way
            raise
        return candidate
    raise _too_many(target)


def _first_free(target: Path) -> Path:
    """The name `_reserve` would pick right now, without creating it."""
    taken = _taken_names(target.parent)
    for candidate in naming.candidate_names(target):
        if _fold_name(candidate.name) not in taken and not os.path.lexists(candidate):
            return candidate
    raise _too_many(target)


def _taken_names(folder: Path) -> set[str]:
    try:
        with os.scandir(folder) as entries:
            return {_fold_name(entry.name) for entry in entries}
    except (FileNotFoundError, NotADirectoryError):
        return set()  # a file in the way is reported when the folder is made


def _fold_name(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _too_many(target: Path) -> UserError:
    return UserError(
        f"There are already over a thousand files named like {target.name} in "
        f"{target.parent}, so Music Organizer stopped rather than add another."
    )


def _replace(src: Path, dst: Path) -> None:
    """`os.replace`, with plain-English errors for another drive and (Windows) a file
    another app has open, which gets 5 more tries over about 2 seconds first."""
    attempt = 0
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            if not _RETRY_PERMISSION_ERRORS:
                raise
            attempt += 1
            if attempt > PERMISSION_RETRIES:
                raise FileInUseError(
                    f"That file is open in another app; close it and try again ({src})."
                ) from exc
            _sleep(PERMISSION_RETRY_S)
        except OSError as exc:
            if exc.errno == errno.EXDEV or getattr(exc, "winerror", None) == (
                _WINDOWS_NOT_SAME_DEVICE
            ):
                raise CrossVolumeError(
                    f"Can't move {src.name} from {src.parent} to {dst.parent}: they're on "
                    "different drives. Music Organizer only moves files within one drive, "
                    "so the library's folders must all be on the same drive."
                ) from exc
            raise


def _missing_dirs(paths: LibraryPaths, folder: Path) -> list[Path]:
    """The folders between a managed folder and `folder` (inclusive) that don't exist
    yet, outermost first. `folder` must already have passed the guard."""
    missing = []
    current = folder
    managed = {_key(paths.root / name) for name in MANAGED}
    while _key(current) not in managed and not current.is_dir():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    if _key(current) in managed and not current.is_dir():
        raise UserError(
            f"The library's {current.name} folder is missing ({current}). Run "
            f"`musicorg init {paths.root}` to put it back, then try again."
        )
    return list(reversed(missing))


def _make_dirs(paths: LibraryPaths, folders: Iterable[Path]) -> None:
    for folder in folders:
        guard(paths, folder, (naming.MUSIC_DIR, naming.REPLACED_DIR, naming.STAGING_DIR))
        try:
            folder.mkdir()
        except FileExistsError as exc:
            if not folder.is_dir():
                raise UserError(
                    f"{folder} is in the way: a folder is needed there, but it's a file."
                ) from exc


def _prune_dirs(paths: LibraryPaths, folders: Iterable[Path]) -> list[Path]:
    """Remove those of `folders` that are empty apart from junk (.DS_Store and the like),
    deepest first. Anything else stays. Only for folders an operation created."""
    removed = []
    for folder in sorted(set(folders), key=lambda p: len(p.parts), reverse=True):
        if os.path.islink(folder) or not folder.is_dir():
            continue
        try:
            guard(paths, folder, (naming.MUSIC_DIR, naming.REPLACED_DIR, naming.STAGING_DIR))
            with os.scandir(folder) as entries:
                contents = list(entries)
            if any(
                not naming.is_junk(e.name) or not e.is_file(follow_symlinks=False) for e in contents
            ):
                continue
            for entry in contents:
                os.unlink(entry.path)
            os.rmdir(folder)
        except (OSError, OutsideLibraryError) as exc:
            log.debug("Left the folder %s: %s", folder, exc)
            continue
        log.info("Removed the empty folder %s", folder)
        removed.append(folder)
    return removed


def _remove_placeholder(paths: LibraryPaths, path: Path, not_before: float | None = None) -> bool:
    """Remove a name a move reserved but didn't fill: only an empty regular file, and
    only if it was made after `not_before` (a timestamp)."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_size != 0:
        log.warning("Left %s alone: it isn't the empty file Music Organizer reserved.", path)
        return False
    if not_before is not None and info.st_mtime < not_before - _CLOCK_SLACK_S:
        log.warning("Left %s alone: it's older than the operation that would have made it.", path)
        return False
    guard(paths, path)
    os.unlink(path)
    log.info("Removed the unused reserved name %s", path)
    return True


# ---- operations ------------------------------------------------------------------------


def stage_path(b: Batch, name: str) -> Path:
    """A free path in `_Staging/<batch_id>/` for a download or a copy in progress. The
    folder is created; the file isn't."""
    _require_lock(b.paths, "Staging")
    folder = _ensure_folder(b.paths, b.paths.staging / b.batch_id, STAGING)
    return _first_free(folder / (naming.safe_component(name) or "file"))


def stage_dir(b: Batch, name: str) -> Path:
    """An empty folder `_Staging/<batch_id>/<name>/` for one job's download (step 09a):
    yt-dlp writes only there. Whatever a crashed or requeued run left in it (a `.part`
    file, a half-finished download) is discarded first."""
    _require_lock(b.paths, "Staging")
    parent = _ensure_folder(b.paths, b.paths.staging / b.batch_id, STAGING)
    folder = parent / (naming.safe_component(name) or "job")
    if os.path.lexists(folder):
        discard_staged(b.paths, folder)
    return _ensure_folder(b.paths, folder, STAGING)


def commit(b: Batch, staged: PurePath | str, rel_target: PurePath | str) -> Path:
    """Move a finished file from `_Staging/` into `Music/<rel_target>`. Returns where it
    landed: `rel_target`, or ` (2)` etc. if that name is taken."""
    paths = b.paths
    src = guard(paths, staged, STAGING)
    _require_file(src)
    target = guard(paths, paths.music / _check_rel(rel_target), MUSIC)
    return _move_op(b, "commit", src, target, undoes=None)


def copy_in(b: Batch, external_src: PurePath | str, rel_target: PurePath | str) -> Path:
    """Copy a file from outside the library into `Music/<rel_target>`. The original is
    only ever opened for reading.

    1. Copy it into `_Staging/`, hashing what was read.
    2. Check the copy's SHA-256 matches, and that the original's size and modification
       time haven't changed meanwhile.
    3. Commit the copy. Returns where it landed.
    """
    paths = b.paths
    src = Path(external_src).expanduser().absolute()
    if _within(src.resolve(), paths.root):
        raise UserError(
            f"{src} is already inside the library. Only files from outside it can be copied in."
        )
    try:
        before = os.stat(src)
    except FileNotFoundError as exc:
        raise UserError(f"There's no file at {src}.") from exc
    except OSError as exc:
        raise FileOperationError(f"Couldn't read {src}: {exc.strerror or exc}.") from exc
    if not stat.S_ISREG(before.st_mode):
        raise UserError(f"{src} isn't a file, so it can't be copied in.")
    target = guard(paths, paths.music / _check_rel(rel_target), MUSIC)
    staged = stage_path(b, src.name)
    new_dirs = _missing_dirs(paths, target.parent)

    with _operation(
        b,
        "copy_in",
        origin=str(src),
        staged=_rel(paths, staged),
        dst=_rel(paths, target),
        expect=_rel(paths, _first_free(target)),
        new_dirs=[_rel(paths, d) for d in new_dirs],
        size=before.st_size,
        mtime_ns=before.st_mtime_ns,
    ) as op:
        try:
            digest = _copy_file(src, staged)
            if sha256_file(staged) != digest or staged.stat().st_size != before.st_size:
                raise IntegrityError(
                    f"The copy of {src.name} didn't match the original, so it wasn't added "
                    "to the library. The original is untouched. Check the drive, then try "
                    "again."
                )
            _check_unchanged(src, before, _CHANGED_WHILE_COPYING.format(path=src))
            final = _move_no_overwrite(b, op, staged, target, new_dirs)
        except Exception as exc:
            _discard_quietly(paths, staged)
            if isinstance(exc, OSError):
                raise FileOperationError(
                    f"Couldn't copy {src.name} into the library: {exc.strerror or exc}."
                ) from exc
            raise
        op.result.update(path=_rel(paths, final), sha256=digest)
    try:
        _check_unchanged(src, before, _CHANGED_WHILE_COPYING.format(path=src))
    except SourceChangedError:
        log.error(
            "The original %s changed while it was being copied in. The library's copy is "
            "the version from before the change.",
            src,
        )
    return final


def stage_copy(b: Batch, external_src: PurePath | str) -> Path:
    """Copy a file from outside the library into `_Staging/<batch_id>/`, so it can be
    tagged there and then committed (step 09b's adopt: nothing half-tagged ever lands in
    `Music/`). The original is only opened for reading. Like `copy_in`, the copy's SHA-256
    must equal what was read, and the original mustn't change meanwhile. Staging is
    scratch space, so this isn't journaled; `commit` is."""
    paths = b.paths
    src = Path(external_src).expanduser().absolute()
    if _within(src.resolve(), paths.root):
        raise UserError(
            f"{src} is already inside the library. Only files from outside it can be copied in."
        )
    try:
        before = os.stat(src)
    except FileNotFoundError as exc:
        raise UserError(f"There's no file at {src}.") from exc
    except OSError as exc:
        raise FileOperationError(f"Couldn't read {src}: {exc.strerror or exc}.") from exc
    if not stat.S_ISREG(before.st_mode):
        raise UserError(f"{src} isn't a file, so it can't be copied in.")
    staged = stage_path(b, src.name)
    try:
        digest = _copy_file(src, staged)
        if sha256_file(staged) != digest or staged.stat().st_size != before.st_size:
            raise IntegrityError(
                f"The copy of {src.name} didn't match the original, so it wasn't added to "
                "the library. The original is untouched. Check the drive, then try again."
            )
        _check_unchanged(src, before, _CHANGED_WHILE_COPYING.format(path=src))
    except Exception as exc:
        _discard_quietly(paths, staged)
        if isinstance(exc, OSError):
            raise FileOperationError(
                f"Couldn't copy {src.name} into the library: {exc.strerror or exc}."
            ) from exc
        raise
    return staged


def set_aside(
    lib: LibraryPaths | _HasPaths,
    staged: PurePath | str,
    folder: PurePath | str,
    note: dict[str, Any] | None = None,
) -> Path:
    """Keep a staged file by moving it to `_Staging/<folder>/` (step 09b), out of the job
    folder the queue discards:

    - a download no rip matched: `<batch_id>/kept`, removed by `clean_staging` after 24 h
    - a calibration download: `calibration/<batch_id>`, which is never cleaned

    `note`, if given, is saved beside it as `<name>.json` (what it was compared with).
    Never overwrites: a taken name gets ` (2)`. Returns where the file went."""
    paths = _paths_of(lib)
    _require_lock(paths, "Keeping a staged file")
    src = guard(paths, staged, STAGING)
    _require_file(src)
    target_dir = paths.staging
    for part in _check_rel(folder).parts:
        target_dir = _ensure_folder(paths, target_dir / part, STAGING)
    target = _reserve(guard(paths, target_dir / src.name, STAGING))
    try:
        _replace(src, target)
    except Exception:
        _remove_placeholder(paths, target)
        raise
    if note is not None:
        text = json.dumps(note, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        side = guard(paths, target.with_name(target.name + ".json"), STAGING)
        _write_new(side, text.encode("utf-8"), lambda p: _remove_placeholder(paths, p))
    _sync_folder(target_dir)
    return target


def supersede(b: Batch, lib_file: PurePath | str) -> Path:
    """Move a library file to `_Replaced/<the same path>` (contract 6.5). Returns where
    it went; ` (2)` etc. if something of that name was replaced before."""
    return _supersede(b, lib_file, undoes=None)


def move(b: Batch, lib_file: PurePath | str, rel_target: PurePath | str) -> Path:
    """Move a library file to `Music/<rel_target>`. Returns where it landed."""
    return _move(b, lib_file, rel_target, undoes=None)


def trash(b: Batch, lib_file: PurePath | str) -> None:
    """Send a library file to the system Trash (contract 6.6). Undo can't bring it back:
    it says to restore it from the Trash by hand."""
    paths = b.paths
    path = guard(paths, lib_file, MUSIC)
    _require_file(path)
    info = path.stat()
    with _operation(
        b, "trash", path=_rel(paths, path), size=info.st_size, mtime_ns=info.st_mtime_ns
    ):
        try:
            _send_to_trash(path)
        except Exception as exc:
            raise FileOperationError(
                f"Couldn't move {path.name} to the Trash: {_describe(exc)}. It's still in "
                "the library."
            ) from exc
        if os.path.lexists(path):
            raise FileOperationError(
                f"{path.name} is still in the library after moving it to the Trash."
            )


def write_tags(b: Batch, lib_file: PurePath | str, changes: tags.TrackTags) -> bool:
    """Change a file's tags, verified (contract 6.7): each field set in `changes` is
    written, REMOVE deletes it, and None leaves it as it is. Returns False, journaling
    nothing, if nothing would change.

    1. Hash the decoded audio, fresh.
    2. Copy the file into `_Staging/` and write the tags on the copy.
    3. Hash the copy's audio, and read its tags back.
    4. If both match: journal the intent with the complete before-state, keep the old
       cover in `.musicorg/undo-art/`, and swap the copy in with `os.replace`.
    5. If not: the copy is discarded, IntegrityError is raised, and the file is untouched.

    A file's MUSICORG_ID is set once: changing or removing it is refused, except by the
    undo of the batch that added it.
    """
    return _write_tags(b, lib_file, changes, undoes=None)


def write_sidecar(b: Batch, lib_file: PurePath | str, suffix: str, data: bytes) -> Path | None:
    """Write a file that goes with a track: `suffix` ".lrc" (or another extension) for
    `<track name>.lrc`, or "cover.jpg" for the album folder's cover.

    - An identical file is already there: nothing to do.
    - A different `.lrc` is there: it's superseded first.
    - A different `cover.jpg` is there: it's left alone (the owner may have chosen that
      art) and None is returned.
    Returns the sidecar's path.
    """
    paths = b.paths
    track = guard(paths, lib_file, MUSIC)
    _require_file(track)
    if not data:
        raise ValueError("a sidecar is never written empty")
    if suffix == naming.COVER_NAME:
        side = track.parent / naming.COVER_NAME
    elif _SIDECAR_SUFFIX.fullmatch(suffix) and suffix.lower() != track.suffix.lower():
        side = track.with_suffix(suffix)
    else:
        raise ValueError(f"not a sidecar suffix: {suffix!r}")
    side = guard(paths, side, MUSIC)
    digest = hashlib.sha256(data).hexdigest()
    if os.path.lexists(side):
        if not side.is_file():
            raise UserError(f"{side} is in the way: it should be a file, but it isn't.")
        if sha256_file(side) == digest:
            log.debug("%s is already up to date", side)
            return side
        if suffix == naming.COVER_NAME:
            log.info("Left the existing %s alone; the owner may have chosen that art.", side)
            return None
        _supersede(b, side, undoes=None)

    staged = stage_path(b, side.name)
    with _operation(
        b,
        "write_sidecar",
        staged=_rel(paths, staged),
        dst=_rel(paths, side),
        expect=_rel(paths, _first_free(side)),
        new_dirs=[],
        sha256=digest,
        size=len(data),
    ) as op:
        try:
            _write_bytes_new(staged, data)
            final = _move_no_overwrite(b, op, staged, side, [])
        except Exception as exc:
            _discard_quietly(paths, staged)
            if isinstance(exc, OSError):
                raise FileOperationError(
                    f"Couldn't write {side.name}: {exc.strerror or exc}."
                ) from exc
            raise
        op.result["path"] = _rel(paths, final)
    if final != side:
        log.warning("%s appeared meanwhile, so the new file is %s", side, final.name)
    return final


def _supersede(b: Batch, lib_file: PurePath | str, *, undoes: int | None) -> Path:
    paths = b.paths
    src = guard(paths, lib_file, MUSIC)
    _require_file(src)
    target = guard(paths, paths.replaced / src.relative_to(paths.music), REPLACED)
    return _move_op(b, "supersede", src, target, undoes=undoes)


def _restore(b: Batch, replaced: Path, rel_target: PurePath, *, undoes: int) -> Path:
    """Undo of supersede: back from `_Replaced/` to `Music/<rel_target>`."""
    paths = b.paths
    src = guard(paths, replaced, REPLACED)
    _require_file(src)
    target = guard(paths, paths.music / _check_rel(rel_target), MUSIC)
    return _move_op(b, "restore", src, target, undoes=undoes)


def _move(
    b: Batch, lib_file: PurePath | str, rel_target: PurePath | str, *, undoes: int | None
) -> Path:
    paths = b.paths
    src = guard(paths, lib_file, MUSIC)
    _require_file(src)
    target = guard(paths, paths.music / _check_rel(rel_target), MUSIC)
    if target.exists() and os.path.samefile(src, target):
        log.debug("%s is already at %s", src, target)
        return src
    return _move_op(b, "move", src, target, undoes=undoes)


def _move_op(b: Batch, op_name: str, src: Path, target: Path, *, undoes: int | None) -> Path:
    """A journaled move of one file (commit, supersede, restore, move)."""
    paths = b.paths
    info = src.stat()
    new_dirs = _missing_dirs(paths, target.parent)
    with _operation(
        b,
        op_name,
        src=_rel(paths, src),
        dst=_rel(paths, target),
        expect=_rel(paths, _first_free(target)),
        new_dirs=[_rel(paths, d) for d in new_dirs],
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
        undoes=undoes,
    ) as op:
        try:
            final = _move_no_overwrite(b, op, src, target, new_dirs)
        except OSError as exc:
            raise FileOperationError(
                f"Couldn't move {src.name} to {target.parent}: {exc.strerror or exc}."
            ) from exc
        op.result["path"] = _rel(paths, final)
    if final != target:
        log.info("%s was taken, so %s went to %s", target.name, src.name, final.name)
    return final


def _write_tags(
    b: Batch, lib_file: PurePath | str, changes: tags.TrackTags, *, undoes: int | None
) -> bool:
    paths = b.paths
    path = guard(paths, lib_file, (naming.MUSIC_DIR, naming.STAGING_DIR))
    _require_file(path)
    tags.check_writable(path)
    current = tags.read_tags(path)
    before = tags.to_record(current)
    after = tags.to_record(tags.merge(current, changes))
    if after == before:
        return False
    old_id = before.get("musicorg_id")
    if old_id is not None and after.get("musicorg_id") != old_id and undoes is None:
        raise UserError(
            f"{path.name} already has the Music Organizer id {old_id}. A track's id never "
            "changes, so its tags were left as they are."
        )

    info = path.stat()
    audio_md5 = tags.audio_hash(path)
    staged = stage_path(b, path.name)
    try:
        _copy_file(path, staged)
        tags.write_tags(staged, changes)
        if tags.audio_hash(staged) != audio_md5:
            raise IntegrityError(
                f"Writing the tags of {path.name} would have changed its audio, so nothing "
                "was changed. The file is untouched."
            )
        if tags.to_record(tags.read_tags(staged)) != after:
            raise IntegrityError(
                f"The new tags of {path.name} didn't read back as they were written, so "
                "nothing was changed. The file is untouched."
            )
    except Exception:
        _discard_quietly(paths, staged)
        raise

    with _operation(
        b,
        "write_tags",
        path=_rel(paths, path),
        staged=_rel(paths, staged),
        before=before,
        after=after,
        audio_md5=audio_md5,
        undoes=undoes,
    ):
        try:
            _check_unchanged(
                path,
                info,
                f"{path} changed while its tags were being written, so they weren't. Try "
                "again once nothing else is changing it.",
            )
            if current.cover is not None and before.get("cover") != after.get("cover"):
                _keep_undo_art(paths, current.cover, str(current.cover_mime))
            _replace(staged, path)
        except Exception as exc:
            _discard_quietly(paths, staged)
            if isinstance(exc, OSError):
                raise FileOperationError(
                    f"Couldn't write the tags of {path.name}: {exc.strerror or exc}."
                ) from exc
            raise
    _sync_folder(path.parent)
    return True


def _keep_undo_art(paths: LibraryPaths, data: bytes, mime: str) -> Path:
    """Keep a cover that a tag write replaces or removes, so undo can put it back:
    `.musicorg/undo-art/<sha256>.jpg` (or `.png`)."""
    digest = hashlib.sha256(data).hexdigest()
    folder = _ensure_folder(paths, paths.undo_art, ENGINE)
    target = folder / f"{digest}{'.png' if mime == tags.PNG else '.jpg'}"
    if target.is_file():
        return target  # the same image, kept before
    return _write_new(target, data, lambda p: _remove_placeholder(paths, p))


def _undo_art(paths: LibraryPaths, digest: str) -> bytes | None:
    for suffix in (".jpg", ".png"):
        try:
            data = (paths.undo_art / f"{digest}{suffix}").read_bytes()
        except OSError:
            continue
        if hashlib.sha256(data).hexdigest() == digest:
            return data
    return None


def _send_to_trash(path: Path) -> None:
    """The system Trash. Tests replace this; see tests/conftest.py."""
    send2trash.send2trash(str(path))


def _copy_file(src: Path, dest: Path) -> str:
    """Copy `src` (opened read-only) to a new file `dest`, flushed to disk. Returns the
    SHA-256 of what was read."""
    digest = hashlib.sha256()
    with open(src, "rb") as fin, open(dest, "xb") as fout:
        while chunk := fin.read(_CHUNK):
            digest.update(chunk)
            fout.write(chunk)
        fout.flush()
        _sync_file(fout.fileno())
    return digest.hexdigest()


def _write_bytes_new(path: Path, data: bytes) -> None:
    with open(path, "xb") as f:
        f.write(data)
        f.flush()
        _sync_file(f.fileno())


_CHANGED_WHILE_COPYING = (
    "{path} changed while it was being copied in, so it wasn't added. Try again once "
    "nothing else is changing it."
)


def _check_unchanged(path: Path, before: os.stat_result, message: str) -> None:
    """SourceChangedError(`message`) if the file's size or modification time changed."""
    try:
        now = os.stat(path)
    except OSError as exc:
        raise SourceChangedError(message) from exc
    if (now.st_size, now.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        raise SourceChangedError(message)


def _check_rel(rel_target: PurePath | str) -> PurePath:
    """A relative path inside a managed folder: no drive, no root, no `..`."""
    rel = PurePath(rel_target)
    if rel.anchor or not rel.parts or any(part in ("", ".", "..") for part in rel.parts):
        raise OutsideLibraryError(
            f"{rel_target} isn't a path inside the library, so Music Organizer left it alone.",
            rel_target,
        )
    return rel


def _require_file(path: Path) -> None:
    if not os.path.lexists(path):
        raise NotFoundError(f"There's no file at {path}.")
    if not path.is_file():
        raise UserError(f"{path} isn't a file.")


# ---- recovery (contract 6.3) -----------------------------------------------------------


@dataclass
class RecoveryDecision:
    batch_id: str
    op_id: int
    op: str
    decision: str  # completed or rolled_back
    detail: str


def recover_journal(paths: LibraryPaths) -> list[RecoveryDecision]:
    """Finish or roll back operations a crash interrupted (contract 6.3).

    library.open runs this on every write open, right after taking the lock. For each
    `intent` without `done`, the files say what happened; the decision is journaled and
    logged. Batches that never ended are closed as interrupted, except open batches
    (step 09b), which stay open until their last job ends.
    """
    _require_lock(paths, "Recovery")
    journal = read_journal(paths)
    with _state_lock:
        busy_ops = set(_in_flight)
        busy_batches = set(_active_batches)
    decisions = []
    for record in journal.values():
        for op in record.ops:
            if op.status != "pending" or (op.batch_id, op.op_id) in busy_ops:
                continue
            decision = _recover_op(paths, op)
            if decision is None:
                continue
            fields: dict[str, Any] = {"decision": decision.decision, "detail": decision.detail}
            if decision.decision == "completed" and op.result_path is None:
                path = _completed_path(paths, op)
                if path is not None:
                    fields["path"] = path
            _append(
                paths,
                {"type": "recovered", "batch_id": op.batch_id, "op_id": op.op_id, **fields},
                sync=True,
            )
            op.recovered = {"type": "recovered", **fields}
            log.info(
                "Recovery, batch %s operation %d (%s): %s. %s",
                op.batch_id,
                op.op_id,
                op.op,
                decision.decision,
                decision.detail,
            )
            decisions.append(decision)
    for record in journal.values():
        if record.end is None and not record.open_batch and record.batch_id not in busy_batches:
            b = Batch(paths, record.batch_id, record.kind, open_batch=False)
            b.counts, b.failed = _tally(record)
            _end_batch(b, interrupted=True)
            log.warning("Batch %s was interrupted; it's closed now.", record.batch_id)
    if decisions:
        completed = sum(d.decision == "completed" for d in decisions)
        log.warning(
            "Finished %d and rolled back %d file operations that were interrupted last "
            "time. The details are in the log.",
            completed,
            len(decisions) - completed,
        )
    return decisions


_MOVES = frozenset({"commit", "copy_in", "supersede", "restore", "move", "write_sidecar"})
# Operations whose source is fileops' own staged copy, removed when rolling back.
_OWN_STAGED = frozenset({"copy_in", "write_sidecar"})


def _recover_op(paths: LibraryPaths, op: OpRecord) -> RecoveryDecision | None:
    try:
        if op.op in _MOVES:
            return _recover_move(paths, op)
        if op.op == "trash":
            return _recover_trash(paths, op)
        if op.op == "write_tags":
            return _recover_tags(paths, op)
    except (OSError, MusicOrgError, KeyError, TypeError, ValueError) as exc:
        log.warning(
            "Couldn't recover batch %s operation %d (%s): %s. It will be tried again next time.",
            op.batch_id,
            op.op_id,
            op.op,
            _describe(exc),
        )
        return None
    log.warning("Unknown operation %r in batch %s; left as it is.", op.op, op.batch_id)
    return None


def _recover_move(paths: LibraryPaths, op: OpRecord) -> RecoveryDecision | None:
    intent = op.intent
    src_key = "staged" if op.op in _OWN_STAGED else "src"
    src = _abs(paths, intent[src_key])
    new_dirs = [_abs(paths, d) for d in intent.get("new_dirs", [])]

    def decided(decision: str, detail: str) -> RecoveryDecision:
        return RecoveryDecision(op.batch_id, op.op_id, op.op, decision, detail)

    def roll_back(detail: str) -> RecoveryDecision:
        if op.op in _OWN_STAGED:
            _discard_quietly(paths, src)
        _prune_dirs(paths, new_dirs)
        return decided("rolled_back", detail)

    if op.reserved is None:
        # Crashed before the name was journaled: the move itself never happened.
        expect = intent.get("expect")
        started = _parse_time(intent.get("ts"))
        if isinstance(expect, str):
            _remove_placeholder(
                paths, _abs(paths, expect), started.timestamp() if started else None
            )
        return roll_back(f"{intent[src_key]} was never moved")

    placeholder = _abs(paths, op.reserved["placeholder"])
    if os.path.lexists(src):
        _remove_placeholder(paths, placeholder)
        return roll_back(f"{intent[src_key]} was never moved")
    if placeholder.is_file():
        size, expected = placeholder.stat().st_size, intent.get("size")
        if expected is None or size == expected:
            return decided(
                "completed", f"{intent[src_key]} had already moved to {_rel(paths, placeholder)}"
            )
        if size == 0:  # still just the reserved name: the file vanished before the move
            _remove_placeholder(paths, placeholder)
            return roll_back(f"{intent[src_key]} disappeared before it was moved")
    log.warning(
        "Batch %s operation %d (%s): neither %s nor %s exists. Check by hand.",
        op.batch_id,
        op.op_id,
        op.op,
        src,
        placeholder,
    )
    return None


def _recover_trash(paths: LibraryPaths, op: OpRecord) -> RecoveryDecision:
    path = op.intent["path"]
    if os.path.lexists(_abs(paths, path)):
        return RecoveryDecision(
            op.batch_id, op.op_id, op.op, "rolled_back", f"{path} is still in the library"
        )
    return RecoveryDecision(
        op.batch_id, op.op_id, op.op, "completed", f"{path} had gone to the Trash"
    )


def _recover_tags(paths: LibraryPaths, op: OpRecord) -> RecoveryDecision | None:
    """The retagged copy either replaced the file or it didn't; the tags say which."""
    path = op.intent["path"]
    current = tags.to_record(tags.read_tags(_abs(paths, path)))
    if current == op.intent.get("after"):
        return RecoveryDecision(
            op.batch_id, op.op_id, op.op, "completed", f"{path} has the new tags"
        )
    if current == op.intent.get("before"):
        staged = op.intent.get("staged")
        if isinstance(staged, str):
            _discard_quietly(paths, _abs(paths, staged))
        return RecoveryDecision(
            op.batch_id, op.op_id, op.op, "rolled_back", f"{path} still has its old tags"
        )
    log.warning("The tags of %s are neither the old nor the new ones. Check them by hand.", path)
    return None


def _completed_path(paths: LibraryPaths, op: OpRecord) -> str | None:
    if op.op in _MOVES and op.reserved is not None:
        return op.reserved.get("placeholder")
    return None


# ---- undo (contract 6.8) ---------------------------------------------------------------


class BatchJobs(Protocol):
    """The queue's side of undo (step 09a provides it)."""

    def cancel_queued(self, batch_id: str) -> int:
        """Cancel the batch's queued jobs; returns how many."""
        ...

    def running(self, batch_id: str) -> int:
        """How many of the batch's jobs are running right now."""
        ...


@dataclass
class UndoStep:
    """One operation of the batch being undone, and what undo does about it."""

    op_id: int
    op: str
    action: str  # supersede, restore, move, write_tags, or none
    path: str
    to: str | None
    status: str  # docs/ENGINE_API.md → Undo step status: planned, done, skipped or manual
    note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UndoResult:
    batch_id: str
    dry_run: bool
    steps: list[UndoStep]
    undo_batch_id: str | None = None
    cancelled_jobs: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "dry_run": self.dry_run,
            "undo_batch_id": self.undo_batch_id,
            "cancelled_jobs": self.cancelled_jobs,
            "operations": [s.to_dict() for s in self.steps],
        }


@dataclass
class _Reversal:
    step: UndoStep
    run: Callable[[Batch], Path | None] | None = None
    prune: list[Path] = field(default_factory=list)


def undo(
    lib: LibraryPaths | _HasPaths,
    batch_id: str,
    *,
    dry_run: bool = False,
    jobs: BatchJobs | None = None,
) -> UndoResult:
    """Reverse a batch's finished operations, newest first, as a batch of its own
    (kind `undo`). With `dry_run`, only says what it would do.

    - commit, copy_in, write_sidecar, restore → the file goes to `_Replaced/`
    - supersede → the file comes back from `_Replaced/` (` (2)` if its name is taken)
    - move → moved back
    - write_tags → the fields it changed get their old values back; fields it added are
      removed. A field changed again since is left as it is.
    - trash → "restore it from the Trash by hand"

    First cancels the batch's queued jobs, and refuses while one is running. Operations
    already undone are skipped, so after an undo stops part way it can be run again.
    """
    paths = _paths_of(lib)
    _require_lock(paths, "Undo")
    journal = read_journal(paths)
    record = journal.get(batch_id)
    if record is None:
        raise NotFoundError(
            f"There's no batch called {batch_id} in this library's journal. "
            "`musicorg journal list` shows the recent ones."
        )
    with _state_lock:
        active = batch_id in _active_batches
    if active:
        raise UndoError(
            f"Batch {batch_id} is still running. Wait for it to finish, then try again."
        )
    cancelled = 0
    if jobs is not None and not dry_run:
        cancelled = jobs.cancel_queued(batch_id)
        if jobs.running(batch_id):
            raise UndoError(
                f"A job from batch {batch_id} is running right now, so it can't be undone "
                f"yet. Its queued jobs were cancelled ({cancelled}). Try again once the "
                "running job has finished."
            )

    undone_by = _undone_ops(journal, batch_id)
    to_undo = [op for op in reversed(record.ops) if op.status in ("done", "pending")]
    reversals = [_plan_reversal(paths, op, undone_by) for op in to_undo]
    result = UndoResult(batch_id, dry_run, [r.step for r in reversals], cancelled_jobs=cancelled)
    if dry_run:
        return result

    # Each step is planned again right before it runs: an earlier step may have put a
    # file back where a later one expects it (a tag write, then a move of the same file:
    # the move is undone first, and only then is the file where its tag write was).
    with ExitStack() as stack:
        ub: Batch | None = None
        done_count = 0
        for position, op in enumerate(to_undo):
            reversal = _plan_reversal(paths, op, undone_by)
            result.steps[position] = reversal.step
            if reversal.run is None:
                continue
            if ub is None:
                ub = stack.enter_context(batch(paths, "undo", undo_of=batch_id))
                result.undo_batch_id = ub.batch_id
            try:
                final = reversal.run(ub)
            except MusicOrgError as exc:
                raise UndoError(
                    f"The undo stopped at {reversal.step.path}: {exc.message} "
                    f"{done_count} operations were undone (as batch {ub.batch_id}). Once "
                    f"that's sorted out, run `musicorg undo {batch_id}` again: it carries on "
                    "where it stopped."
                ) from exc
            done_count += 1
            reversal.step.status = "done"
            if final is not None:
                reversal.step.to = _rel(paths, final)
            _prune_dirs(paths, reversal.prune)
    if record.open_batch and record.end is None:
        close_batch(paths, batch_id, closed_by=result.undo_batch_id or "undo")
    log.info(
        "Undo of %s: %d operations undone", batch_id, sum(s.status == "done" for s in result.steps)
    )
    return result


def _undone_ops(journal: dict[str, BatchRecord], batch_id: str) -> dict[int, str]:
    """Operations of `batch_id` that an earlier undo already reversed → that undo's id."""
    undone: dict[int, str] = {}
    for record in journal.values():
        if record.undo_of != batch_id:
            continue
        for op in record.ops:
            target = op.intent.get("undoes")
            if isinstance(target, int) and op.status == "done":
                undone.setdefault(target, record.batch_id)
    return undone


def _plan_reversal(paths: LibraryPaths, op: OpRecord, undone_by: dict[int, str]) -> _Reversal:
    intent = op.intent
    shown = op.result_path or intent.get("dst") or intent.get("path") or intent.get("src") or ""

    def step(action: str, path: str, to: str | None, status: str, note: str) -> UndoStep:
        return UndoStep(op.op_id, op.op, action, path, to, status, note)

    def skip(path: str, note: str) -> _Reversal:
        return _Reversal(step("none", path, None, "skipped", note))

    if op.op_id in undone_by:
        return skip(shown, f"Already undone (batch {undone_by[op.op_id]}).")
    if op.status == "pending":
        return skip(
            shown,
            "This operation was interrupted and it isn't clear whether it finished. "
            "Check this file by hand.",
        )
    prune = [_abs(paths, d) for d in intent.get("new_dirs", [])]

    if op.op in ("commit", "copy_in", "write_sidecar", "restore"):
        rel = op.result_path or ""
        current = _abs(paths, rel)
        if not rel or not current.is_file():
            return skip(rel, f"{rel} is no longer there, so there's nothing to take back.")
        to = PurePosixPath(naming.REPLACED_DIR, *PurePosixPath(rel).parts[1:]).as_posix()
        return _Reversal(
            step("supersede", rel, to, "planned", f"Move {rel} to {naming.REPLACED_DIR}/"),
            run=lambda b: _supersede(b, current, undoes=op.op_id),
            prune=prune,
        )

    if op.op in ("supersede", "move"):
        rel = op.result_path or ""
        original = str(intent["src"])
        current = _abs(paths, rel)
        if not rel or not current.is_file():
            return skip(rel, f"{rel} is no longer there, so it can't be put back.")
        back = PurePosixPath(original).relative_to(naming.MUSIC_DIR)
        if op.op == "supersede":
            return _Reversal(
                step("restore", rel, original, "planned", f"Put {rel} back at {original}"),
                run=lambda b: _restore(b, current, back, undoes=op.op_id),
                prune=prune,
            )
        return _Reversal(
            step("move", rel, original, "planned", f"Move {rel} back to {original}"),
            run=lambda b: _move(b, current, back, undoes=op.op_id),
            prune=prune,
        )

    if op.op == "trash":
        path = str(intent["path"])
        return _Reversal(
            step(
                "none",
                path,
                None,
                "manual",
                f"{path} went to the Trash. Restore it from the Trash by hand if you want it back.",
            )
        )

    if op.op == "write_tags":
        return _plan_tag_reversal(paths, op, step, skip)

    return skip(shown, f"Unknown operation {op.op!r}; left as it is.")


def _plan_tag_reversal(
    paths: LibraryPaths,
    op: OpRecord,
    step: Callable[[str, str, str | None, str, str], UndoStep],
    skip: Callable[[str, str], _Reversal],
) -> _Reversal:
    rel = str(op.intent["path"])
    path = _abs(paths, rel)
    if PurePosixPath(rel).parts[:1] == (naming.STAGING_DIR,):
        # Step 09b tags a download or copy in _Staging, then commits it: taking the
        # committed file back undoes both.
        return skip(rel, "Tagged before it went into the library; taking the file back covers it.")
    if not path.is_file():
        return skip(rel, f"{rel} is no longer there, so its tags can't be restored.")
    before = op.intent.get("before") or {}
    after = op.intent.get("after") or {}
    current = tags.to_record(tags.read_tags(path))
    changes: dict[str, Any] = {}
    changed_since = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key, _MISSING), after.get(key, _MISSING)
        if old == new:
            continue
        now = current.get(key, _MISSING)
        if now == new:
            changes[key] = tags.REMOVE if old is _MISSING else old
        elif now != old:
            changed_since.append(key)
    if "cover" not in changes:
        changes.pop("cover_mime", None)  # a cover's type only changes with the cover
    cover = None
    if changes.get("cover") not in (None, tags.REMOVE):
        cover = _undo_art(paths, changes["cover"])
        if cover is None:
            del changes["cover"]
            changes.pop("cover_mime", None)
            changed_since.append("cover (its saved copy is missing)")
    if not changes:
        if changed_since:
            return skip(
                rel,
                f"Its tags were changed again later ({', '.join(changed_since)}), "
                "so they're left as they are.",
            )
        return skip(rel, "Its tags are already as they were.")
    note = f"Restore the tags of {rel}"
    if changed_since:
        note += f" (except {', '.join(changed_since)}, changed again later)"
    return _Reversal(
        step("write_tags", rel, None, "planned", note),
        run=lambda b: _write_tags_step(b, path, tags.from_record(changes, cover), op.op_id),
    )


def _write_tags_step(b: Batch, path: Path, changes: tags.TrackTags, undoes: int) -> None:
    _write_tags(b, path, changes, undoes=undoes)


@dataclass
class BatchInfo:
    """A batch as `musicorg journal list` shows it."""

    batch_id: str
    kind: str
    status: str
    started_at: str
    ended_at: str | None
    operations: dict[str, int]
    failed: int
    pending: int
    undo_of: str | None
    undone_by: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def list_batches(lib: LibraryPaths | _HasPaths, limit: int | None = 20) -> list[BatchInfo]:
    """The most recent batches, newest first. Needs no lock."""
    records = list(read_journal(lib).values())
    records.reverse()
    if limit is not None:
        records = records[: max(limit, 0)]
    infos = []
    for record in records:
        counts, failed = _tally(record)
        infos.append(
            BatchInfo(
                batch_id=record.batch_id,
                kind=record.kind,
                status=record.status,
                started_at=record.started_at,
                ended_at=str(record.end.get("ts")) if record.end else None,
                operations={op: counts[op] for op in OPERATIONS if counts[op]},
                failed=failed,
                pending=sum(op.status == "pending" for op in record.ops),
                undo_of=record.undo_of,
                undone_by=list(record.undone_by),
            )
        )
    return infos


# ---- plans (contract 6.9) --------------------------------------------------------------

PLAN_SCHEMA = 1


@dataclass
class FileCheck:
    """A file as it was when the plan was made. `path` is library-relative for library
    files, otherwise absolute."""

    path: str
    size: int
    mtime_ns: int
    sha1_head: str

    @classmethod
    def of(cls, lib: LibraryPaths | _HasPaths, path: PurePath | str) -> FileCheck:
        paths = _paths_of(lib)
        p = Path(path).expanduser().absolute()
        info = os.stat(p)
        return cls(_stored(paths, p), info.st_size, info.st_mtime_ns, sha1_head(p))


@dataclass
class FolderCheck:
    """A target folder as it was when the plan was made: whether it existed, and its
    entries apart from junk (.DS_Store and the like)."""

    path: str
    exists: bool
    names: list[str]

    @classmethod
    def of(cls, lib: LibraryPaths | _HasPaths, folder: PurePath | str) -> FolderCheck:
        paths = _paths_of(lib)
        p = Path(folder).expanduser().absolute()
        return cls(_stored(paths, p), p.is_dir(), sorted(_listing(p)))


@dataclass
class PlanOp:
    """One planned operation and its preconditions. `action` says what the job will do;
    `params` carries whatever else it needs."""

    action: str
    op_id: int = 0
    item_id: str | None = None
    item_state: str | None = None  # docs/ENGINE_API.md → Item state
    source: FileCheck | None = None
    target: str | None = None  # library-relative, e.g. "Music/A/B (2020)/01 T.m4a"
    target_folder: FolderCheck | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    plan_id: str
    kind: str  # docs/ENGINE_API.md → Plan kind
    created_at: str
    operations: list[PlanOp]
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"schema": PLAN_SCHEMA, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        ops = []
        for raw in data["operations"]:
            raw = dict(raw)
            source = raw.pop("source", None)
            folder = raw.pop("target_folder", None)
            ops.append(
                PlanOp(
                    **raw,
                    source=FileCheck(**source) if source else None,
                    target_folder=FolderCheck(**folder) if folder else None,
                )
            )
        return cls(
            plan_id=data["plan_id"],
            kind=data["kind"],
            created_at=data["created_at"],
            operations=ops,
            summary=dict(data.get("summary") or {}),
        )


@dataclass
class PlanProblem:
    op_id: int
    message: str


def new_plan(kind: str, operations: list[PlanOp], summary: dict[str, Any] | None = None) -> Plan:
    """A plan with a fresh id. Its operations are numbered 1, 2, 3 ..."""
    if kind not in PLAN_KINDS:
        raise ValueError(f"unknown plan kind {kind!r}; see docs/ENGINE_API.md → Enums")
    for number, op in enumerate(operations, 1):
        op.op_id = number
    return Plan(_new_id("p"), kind, _stamp(datetime.now(UTC)), operations, dict(summary or {}))


def save_plan(lib: LibraryPaths | _HasPaths, plan: Plan) -> Path:
    """Save to `.musicorg/plans/<plan_id>.json`. Never overwrites."""
    paths = _paths_of(lib)
    _require_lock(paths, "Saving a plan")
    folder = _ensure_folder(paths, paths.plans, ENGINE)
    target = guard(paths, folder / f"{plan.plan_id}.json", ENGINE)
    text = json.dumps(plan.to_dict(), indent=2, ensure_ascii=False) + "\n"
    final = _write_new(target, text.encode("utf-8"), lambda p: _remove_placeholder(paths, p))
    if final != target:
        raise RuntimeError(f"plan {plan.plan_id} already existed")
    return final


def load_plan(lib: LibraryPaths | _HasPaths, plan_id: str) -> Plan:
    """A saved plan. Needs no lock."""
    paths = _paths_of(lib)
    if not _PLAN_ID.fullmatch(plan_id):
        raise NotFoundError(f"There's no plan called {plan_id}.")
    path = paths.plans / f"{plan_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise NotFoundError(f"There's no plan called {plan_id}.") from exc
    except (OSError, ValueError) as exc:
        raise UserError(f"The plan {plan_id} is damaged ({exc}). Make a new plan.") from exc
    try:
        return Plan.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise UserError(f"The plan {plan_id} is damaged ({exc}). Make a new plan.") from exc


ItemStateLookup = Callable[[str], str | None]


def validate(
    lib: LibraryPaths | _HasPaths, plan: Plan, *, item_state: ItemStateLookup | None = None
) -> list[PlanProblem]:
    """Re-check every operation's preconditions. An empty list means the plan still holds."""
    problems = []
    for op in plan.operations:
        problem = check_op(lib, op, item_state=item_state)
        if problem is not None:
            problems.append(problem)
    return problems


def check_op(
    lib: LibraryPaths | _HasPaths, op: PlanOp, *, item_state: ItemStateLookup | None = None
) -> PlanProblem | None:
    """Re-check one operation's preconditions, right before a job acts on it (step 09b).

    - the source file's size, modification time and `sha1_head` are unchanged
    - the item's state is unchanged (`item_state` looks it up)
    - the target folder is still a folder (or still absent), and nothing new has taken
      the target's name. Junk such as .DS_Store is ignored.
    """
    paths = _paths_of(lib)

    def problem(detail: str) -> PlanProblem:
        return PlanProblem(op.op_id, f"{FILE_CHANGED} ({detail})")

    if op.source is not None:
        source = _stored_abs(paths, op.source.path)
        try:
            info = os.stat(source)
        except OSError:
            return problem(f"{op.source.path} is missing")
        if not stat.S_ISREG(info.st_mode):
            return problem(f"{op.source.path} isn't a file any more")
        if info.st_size != op.source.size:
            return problem(f"{op.source.path}: size {op.source.size} → {info.st_size}")
        if info.st_mtime_ns != op.source.mtime_ns:
            return problem(f"{op.source.path} was modified")
        if sha1_head(source) != op.source.sha1_head:
            return problem(f"{op.source.path}: its contents changed")

    if op.item_id is not None and op.item_state is not None:
        if item_state is None:
            return problem(f"can't check the state of item {op.item_id}")
        now = item_state(op.item_id)
        if now != op.item_state:
            return problem(f"item {op.item_id} is now {now or 'gone'}, was {op.item_state}")

    if op.target_folder is not None:
        folder = _stored_abs(paths, op.target_folder.path)
        if os.path.lexists(folder) and not folder.is_dir():
            return problem(f"{op.target_folder.path} isn't a folder any more")
        if op.target is not None:
            name = _fold_name(PurePosixPath(op.target).name)
            before = {_fold_name(n) for n in op.target_folder.names}
            if name not in before and name in {_fold_name(n) for n in _listing(folder)}:
                return problem(f"something new is at {op.target}")
    return None


def sha1_head(path: PurePath | str) -> str:
    """SHA-1 of a file's first 1 MB: a quick fingerprint, used together with its size."""
    digest = hashlib.sha1(usedforsecurity=False)
    with open(path, "rb") as f:
        digest.update(f.read(SHA1_HEAD_BYTES))
    return digest.hexdigest()


def sha256_file(path: PurePath | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _listing(folder: Path) -> list[str]:
    """A folder's entry names, without junk. Empty if it doesn't exist."""
    try:
        with os.scandir(folder) as entries:
            return [
                unicodedata.normalize("NFC", e.name) for e in entries if not naming.is_junk(e.name)
            ]
    except (FileNotFoundError, NotADirectoryError):
        return []


# ---- staging (contract 6.6) ------------------------------------------------------------


def discard_staged(lib: LibraryPaths | _HasPaths, path: PurePath | str) -> int:
    """Delete something in `_Staging/`: a file, a link (never followed), or a folder with
    everything in it. Returns how many files and links were removed."""
    paths = _paths_of(lib)
    _require_lock(paths, "Discarding staged files")
    staging = _checked_staging(paths)
    given = Path(path).absolute()
    if given.name in ("", ".", ".."):
        raise OutsideLibraryError(f"{path} isn't something in _Staging.", path)
    # Where the entry itself is, without following it if it's a link.
    location = given.parent.resolve() / given.name
    if not _inside(location, staging):
        raise OutsideLibraryError(
            f"{path} isn't inside the library's _Staging folder, so it was left alone.", path
        )
    if _key(location) == _key(paths.calibration):
        raise UserError("The calibration folder is kept; discard what's inside it instead.")
    return _remove_tree(location)


def clean_staging(lib: LibraryPaths | _HasPaths, older_than_hours: float = 24) -> int:
    """Delete files in `_Staging/` older than `older_than_hours`, and folders that are
    then empty. Links are removed whatever their age, and never followed.
    `_Staging/calibration/` is skipped. Returns how many files and links were removed."""
    paths = _paths_of(lib)
    _require_lock(paths, "Cleaning _Staging")
    staging = _checked_staging(paths)
    cutoff = time.time() - older_than_hours * 3600
    removed = 0
    with os.scandir(staging) as entries:
        children = [Path(e.path) for e in entries]
    for child in children:
        if child.name == naming.CALIBRATION_DIR and child.is_dir() and not _is_link(child):
            continue
        removed += _remove_old(child, cutoff)
    if removed:
        log.info("Cleaned %d old files out of %s", removed, staging)
    return removed


def _discard_quietly(paths: LibraryPaths, path: Path) -> None:
    """discard_staged for clean-ups after an error: a problem is logged, not raised."""
    try:
        discard_staged(paths, path)
    except (OSError, MusicOrgError) as exc:
        log.warning("Couldn't remove %s from _Staging: %s", path, _describe(exc))


def _checked_staging(paths: LibraryPaths) -> Path:
    """`_Staging/` itself, refusing if it's a link or resolves anywhere else."""
    staging = paths.staging
    if _is_link(staging) or _key(staging.resolve()) != _key(staging):
        raise OutsideLibraryError(
            f"The library's _Staging folder ({staging}) leads somewhere else, so nothing in "
            "it was deleted. Replace it with an ordinary folder.",
            staging,
        )
    return staging


def _remove_tree(path: Path) -> int:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return 0
    if _is_link(path, info):
        _unlink_link(path)
        return 1
    if stat.S_ISREG(info.st_mode):
        os.unlink(path)
        return 1
    if stat.S_ISDIR(info.st_mode):
        with os.scandir(path) as entries:
            children = [Path(e.path) for e in entries]
        removed = sum(_remove_tree(child) for child in children)
        try:
            os.rmdir(path)
        except OSError as exc:
            log.warning("Left %s in _Staging: %s", path, exc)
        return removed
    log.warning("Left %s in _Staging: it isn't a file or a folder.", path)
    return 0


def _remove_old(path: Path, cutoff: float) -> int:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return 0
    if _is_link(path, info):
        _unlink_link(path)
        return 1
    if stat.S_ISREG(info.st_mode):
        if info.st_mtime >= cutoff:
            return 0
        os.unlink(path)
        return 1
    if stat.S_ISDIR(info.st_mode):
        with os.scandir(path) as entries:
            children = [Path(e.path) for e in entries]
        removed = sum(_remove_old(child, cutoff) for child in children)
        if info.st_mtime < cutoff:
            try:
                os.rmdir(path)
            except OSError:
                pass  # still holds something recent
        return removed
    return 0


def _is_link(path: Path, info: os.stat_result | None = None) -> bool:
    """A symlink, or on Windows a junction or other reparse point: never followed."""
    try:
        info = info or os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode) or os.path.isjunction(path):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT)


def _unlink_link(path: Path) -> None:
    try:
        os.unlink(path)
    except (IsADirectoryError, PermissionError):
        if sys.platform != "win32":
            raise
        os.rmdir(path)  # a link to a folder, on Windows: removes the link only


# ---- exports ---------------------------------------------------------------------------


def write_export(
    lib: LibraryPaths | _HasPaths, path: PurePath | str, data: bytes, *, sources: Iterable[Path]
) -> Path:
    """Save a report or CSV the user asked for, anywhere they chose, and return its path.

    - Never overwrites: ` (2)`, ` (3)` ... if the name is taken.
    - Refuses the library's managed folders and any registered source (`sources`).
    - Creates the library's Reports/ folder if it's missing; any other folder must exist.
    - Not journaled, and needs no lock.
    """
    paths = _paths_of(lib)
    target = Path(path).expanduser().absolute()
    if not target.name or naming.is_junk(target.name):
        raise ValueError(f"not a file name: {target.name!r}")
    folder = target.parent
    if not folder.is_dir():
        if _key(folder) == _key(paths.reports) and not os.path.lexists(folder):
            folder.mkdir()
        else:
            raise UserError(
                f"The folder {folder} doesn't exist. Create it first, or choose another folder."
            )
    real = folder.resolve() / target.name
    forbidden = [paths.music, paths.replaced, paths.staging, paths.engine]
    forbidden += [p.resolve() for p in forbidden]
    if any(_within(real, f) or _within(target, f) for f in forbidden):
        raise UserError(
            f"Reports can't be saved in {folder}: that's inside the library's own folders "
            f"(Music, _Replaced, _Staging, .musicorg). Choose another folder, such as "
            f"{paths.reports}."
        )
    for source in sources:
        source_path = Path(source)
        if _within(real, source_path.resolve()) or _within(target, source_path):
            raise UserError(
                f"Reports can't be saved in {folder}: it's inside the source folder "
                f"{source_path}, which Music Organizer never writes to. Choose another folder."
            )
    try:
        final = _write_new(real, data, _unlink_if_empty)
    except OSError as exc:
        raise FileOperationError(
            f"Couldn't save {target.name} in {folder}: {exc.strerror or exc}."
        ) from exc
    log.info("Saved %s", final)
    return folder / final.name


# ---- small helpers ---------------------------------------------------------------------


def _write_new(target: Path, data: bytes, remove_placeholder: Callable[[Path], object]) -> Path:
    """Write `data` as a new file at `target`, or ` (2)` etc. if that's taken. It appears
    complete or not at all: a temp file, fsynced, is renamed onto the reserved name."""
    placeholder = _reserve(target)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".musicorg-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            _sync_file(f.fileno())
        _replace(tmp, placeholder)
    except BaseException:
        tmp.unlink(missing_ok=True)
        remove_placeholder(placeholder)
        raise
    _sync_folder(target.parent)
    return placeholder


def _unlink_if_empty(path: Path) -> None:
    try:
        if path.stat().st_size == 0:
            path.unlink()
    except OSError:
        pass


def _ensure_folder(paths: LibraryPaths, folder: Path, allow: tuple[str, ...]) -> Path:
    """`folder`, created if missing. Its parent must exist."""
    folder = guard(paths, folder, allow)
    if not folder.is_dir():
        try:
            folder.mkdir()
        except FileExistsError as exc:
            if not folder.is_dir():
                raise UserError(f"{folder} is in the way: it should be a folder.") from exc
        except FileNotFoundError as exc:
            raise UserError(
                f"The library's folder {folder.parent} is missing. Run `musicorg init "
                f"{paths.root}` to put it back, then try again."
            ) from exc
    return folder


def _rel(paths: LibraryPaths, path: Path) -> str:
    """How the journal stores a path: relative to the library root, with `/`."""
    try:
        return path.relative_to(paths.root).as_posix()
    except ValueError:
        return str(path)


def _abs(paths: LibraryPaths, stored: str) -> Path:
    if Path(stored).is_absolute():
        return Path(stored)
    return paths.root.joinpath(*PurePosixPath(stored).parts)


def _stored(paths: LibraryPaths, path: Path) -> str:
    """A plan's path: library-relative inside the library, else absolute."""
    resolved = path.resolve()
    if _inside(resolved, paths.root):
        return _rel(paths, resolved)
    return str(path)


def _stored_abs(paths: LibraryPaths, stored: str) -> Path:
    return _abs(paths, stored)


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


def _sync_file(fd: int) -> None:
    """Flush a file to the disk itself. Plain fsync on macOS only reaches the drive's
    cache, so F_FULLFSYNC is used there when the drive supports it."""
    if _FULL_FSYNC:
        try:
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
            return
        except OSError:
            pass
    os.fsync(fd)


def _sync_folder(folder: Path) -> None:
    """Make a rename or a new file durable on macOS and Linux. Windows can't fsync a folder."""
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
