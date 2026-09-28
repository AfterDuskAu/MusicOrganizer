"""Helpers for the fileops and tags tests: simulated crashes, sample files and covers,
and looking at the journal."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

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


def place(sample: Path, dest: Path) -> Path:
    """A copy of a sample audio file at `dest` (folders created), as test setup."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(sample, dest)
    return dest


def image(kind: str = "JPEG", color: tuple[int, int, int] = (200, 30, 30), size: int = 16) -> bytes:
    """A small cover image, made in memory."""
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buffer, kind)
    return buffer.getvalue()


def age(path: Path, hours: float) -> None:
    """Set a file's (or a link's own) modification time `hours` into the past."""
    when = os.lstat(path).st_mtime - hours * 3600
    if os.path.islink(path):
        if os.utime not in os.supports_follow_symlinks:
            return
        os.utime(path, (when, when), follow_symlinks=False)
    else:
        os.utime(path, (when, when))
