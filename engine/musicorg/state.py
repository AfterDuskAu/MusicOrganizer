"""The library's .musicorg/state.json: everything the files can't carry (contract section 5).

- Saved atomically: a temp file next to it, fsync, then a rename over the old one. A
  crash mid-save leaves the old file intact (plus a stray temp file, which is ignored).
- Carries a schema version. A file from a newer engine is refused rather than guessed at.
- Keys this version doesn't know about are kept when saving.
- Only a process holding the library's lock should save it.

Under rule 3 of CLAUDE.md this module may write state.json itself. Other modules change
it through `edit()` or `create_if_missing()`, never by writing the file.

Keys (contract section 5), each written by the step named:
- "sources": {source_id: {"path": resolved folder, "added_at": ISO time}} (step 05)
- "decisions": {item_id: {"decision": RPC decision (docs/ENGINE_API.md → Enums),
  "decided_at": ISO time, plus details}} (step 07). The details: `accept` and
  `candidate` carry "video_id", "candidate_id" and "title"; `url` also "url" and
  "score"; `only_copy` any of "artist_fix", "title_fix", "album_fix". A rejection isn't
  a decision: it goes in "rejected".
- "superseded": {normalised rip path: MUSICORG_ID} (step 09b)
- "rejected": {item_id: [YouTube videoId, ...]}: candidates the owner turned down, never
  proposed again (read by step 06, written by step 07)
- "aliases": {compare key of an artist name in the rips: {"name": the name the owner
  confirmed is the same artist, "from": the rips' spelling, "decided_at": ISO time}}
  (step 07b: "Biggie Smalls" → "The Notorious B.I.G.")
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from musicorg.errors import StateError

STATE_SCHEMA = 1


def default_data() -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


class State:
    """The contents of state.json. Change `data`, then `save()` (or use `edit()`)."""

    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self.path = path
        self.data = data

    @classmethod
    def load(cls, path: Path) -> State:
        """Read state.json. A missing file gives the defaults; nothing is written."""
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls(path, default_data())
        except OSError as exc:
            raise StateError(
                f"Couldn't read the library's records in {path}: {exc.strerror or exc}."
            ) from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StateError(_damaged(path, f"line {exc.lineno}: {exc.msg}")) from exc
        if not isinstance(data, dict):
            raise StateError(_damaged(path, "it should hold a JSON object"))
        schema = data.get("schema")
        if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
            raise StateError(_damaged(path, "its schema version is missing"))
        if schema > STATE_SCHEMA:
            raise StateError(
                f"The library's records in {path} were written by a newer version of Music "
                f"Organizer (records version {schema}; this engine reads up to "
                f"{STATE_SCHEMA}). Update the engine, then try again."
            )
        return cls(path, data)

    @property
    def schema(self) -> int:
        return int(self.data["schema"])

    def save(self) -> None:
        """Write state.json atomically: temp file, fsync, rename over the old one."""
        self.data.setdefault("schema", STATE_SCHEMA)
        text = json.dumps(self.data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        try:
            _write_atomic(self.path, text)
        except OSError as exc:
            raise StateError(
                f"Couldn't save the library's records to {self.path}: {exc.strerror or exc}. "
                "The previous version of the file is unchanged."
            ) from exc


@contextmanager
def edit(path: Path) -> Iterator[State]:
    """Load state.json, let the caller change it, and save it if no error was raised."""
    state = State.load(path)
    yield state
    state.save()


def create_if_missing(path: Path) -> bool:
    """Write a fresh state.json if there isn't one. Returns whether one was written."""
    if path.exists():
        return False
    State(path, default_data()).save()
    return True


def normalise_path(path: Path) -> str:
    """The form of a folder path that source ids are made from: `~` expanded, absolute,
    symlinks resolved, NFC, and case-folded where the system does that (Windows)."""
    absolute = Path(path).expanduser().resolve()
    return os.path.normcase(unicodedata.normalize("NFC", str(absolute)))


def source_id(path: Path) -> str:
    """A registered source's stable id: `s_` + 12 hex characters of SHA-1 of its path."""
    data = normalise_path(path).encode("utf-8", "surrogatepass")
    return "s_" + hashlib.sha1(data, usedforsecurity=False).hexdigest()[:12]


def _damaged(path: Path, detail: str) -> str:
    return (
        f"The library's records in {path} are damaged ({detail}). Restore that file from "
        "a backup, such as Time Machine. Your music files are not affected."
    )


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            _sync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _sync_folder(path.parent)


def _sync(fd: int) -> None:
    """Flush a file to the disk itself. Plain fsync on macOS only reaches the drive's
    cache, so F_FULLFSYNC is used there when the drive supports it."""
    if sys.platform == "darwin":
        import fcntl

        try:
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
            return
        except OSError:
            pass
    os.fsync(fd)


def _sync_folder(folder: Path) -> None:
    """Make a rename durable on macOS and Linux. Windows has no folder fsync."""
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


def sources(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The registered sources, by id. Tolerates a missing or malformed key."""
    value = data.get("sources")
    return (
        {k: v for k, v in value.items() if isinstance(v, dict)} if isinstance(value, dict) else {}
    )


def decisions(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The owner's review decisions, by item id."""
    value = data.get("decisions")
    return (
        {k: v for k, v in value.items() if isinstance(v, dict)} if isinstance(value, dict) else {}
    )


def superseded(data: dict[str, Any]) -> dict[str, str]:
    """Rip path (normalised) → MUSICORG_ID of the library file that replaced it."""
    value = data.get("superseded")
    return {k: v for k, v in value.items() if isinstance(v, str)} if isinstance(value, dict) else {}


def rejected(data: dict[str, Any]) -> dict[str, set[str]]:
    """Item id → the videoIds the owner rejected for it."""
    value = data.get("rejected")
    if not isinstance(value, dict):
        return {}
    return {
        k: {v for v in ids if isinstance(v, str)}
        for k, ids in value.items()
        if isinstance(ids, list)
    }


def aliases(data: dict[str, Any]) -> dict[str, str]:
    """The compare key of an artist name in the rips → the name the owner confirmed."""
    value = data.get("aliases")
    if not isinstance(value, dict):
        return {}
    return {
        k: v["name"]
        for k, v in value.items()
        if isinstance(v, dict) and isinstance(v.get("name"), str) and v["name"]
    }
