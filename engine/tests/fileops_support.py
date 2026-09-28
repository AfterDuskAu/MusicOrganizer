"""Helpers for the fileops tests: simulated crashes, fake tags, and looking at the journal."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from musicorg import fileops, library


class SimulatedCrash(BaseException):
    """Stands in for the process dying. fileops rolls back ordinary errors (Exception)
    in the same run; anything else is left for recovery, as after a real crash."""


def crash_in(monkeypatch: pytest.MonkeyPatch, name: str, *, after: bool = False) -> None:
    """Make fileops.<name> "crash": before it does anything, or right after it did its
    work (`after`)."""
    real = getattr(fileops, name)

    def crashing(*args: Any, **kwargs: Any) -> Any:
        if after:
            real(*args, **kwargs)
        raise SimulatedCrash(name)

    monkeypatch.setattr(fileops, name, crashing)


def journal(lib: library.Library) -> list[dict[str, Any]]:
    """Every journal line, oldest first."""
    lines = []
    for path in sorted(lib.paths.journal.glob("*.jsonl")):
        lines += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return lines


def lines_of(lib: library.Library, kind: str) -> list[dict[str, Any]]:
    return [line for line in journal(lib) if line["type"] == kind]


def tree(folder: Path) -> list[str]:
    """Everything under `folder`, relative, with `/`, folders ending in `/`."""
    return sorted(
        p.relative_to(folder).as_posix() + ("/" if p.is_dir() else "") for p in folder.rglob("*")
    )


def files_in(folder: Path) -> list[str]:
    return [name for name in tree(folder) if not name.endswith("/")]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def put(path: Path, data: bytes | str = b"music") -> Path:
    """Create a file (and its folders) directly, as test setup."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return path


def staged(b: fileops.Batch, name: str, data: bytes = b"downloaded audio") -> Path:
    """A finished download in the batch's staging folder."""
    return put(fileops.stage_path(b, name), data)


def symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"can't make symlinks here: {exc}")


def recover(lib: library.Library) -> list[fileops.RecoveryDecision]:
    return fileops.recover_journal(lib.paths)


class FakeTags:
    """Tags kept as JSON inside the file itself: {"audio": ..., "tags": {...}}. The cover
    is just another field here; step 04 brings the real thing."""

    def read(self, path: Path) -> dict[str, Any]:
        return dict(json.loads(path.read_text(encoding="utf-8"))["tags"])

    def write(self, path: Path, changes: Mapping[str, Any]) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, value in changes.items():
            if value is fileops.REMOVE:
                data["tags"].pop(key, None)
            else:
                data["tags"][key] = value
        path.write_text(json.dumps(data), encoding="utf-8")


def tagged(path: Path, tags: dict[str, Any], audio: str = "la la la") -> Path:
    return put(path, json.dumps({"audio": audio, "tags": tags}))


def audio_of(path: Path) -> str:
    return json.loads(path.read_text(encoding="utf-8"))["audio"]


def crash_tag_write(
    monkeypatch: pytest.MonkeyPatch, tags: FakeTags, *, after: bool
) -> Callable[..., None]:
    real = tags.write

    def crashing(path: Path, changes: Mapping[str, Any]) -> None:
        if after:
            real(path, changes)
        raise SimulatedCrash("tag write")

    monkeypatch.setattr(tags, "write", crashing)
    return crashing


def age(path: Path, hours: float) -> None:
    """Set a file's (or a link's own) modification time `hours` into the past."""
    when = os.lstat(path).st_mtime - hours * 3600
    if os.path.islink(path):
        if os.utime not in os.supports_follow_symlinks:
            return
        os.utime(path, (when, when), follow_symlinks=False)
    else:
        os.utime(path, (when, when))
