"""`musicorg journal list` and `musicorg undo`, and the step 03b manual check run end to
end: init, scripts/fileops_demo.py, journal list, undo."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fileops_support import files_in, put, sha256, tree

from musicorg import cli, fileops
from musicorg.errors import EXIT_LOCKED, EXIT_OK, EXIT_USER_ERROR
from musicorg.library import Library

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "scripts" / "fileops_demo.py"


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = cli.main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def rips(tmp_path: Path) -> list[Path]:
    return [
        put(tmp_path / "rips" / f"Artist - Song {n}.mp3", f"rip {n} ".encode() * 1000)
        for n in (1, 2, 3)
    ]


def adopt(lib: Library, rips: list[Path]) -> str:
    with fileops.batch(lib, "demo") as b:
        for rip in rips:
            fileops.copy_in(b, rip, f"Unknown Artist/Unsorted/{rip.name}")
    lib.close()  # like a finished command: `undo` takes the lock next
    return b.batch_id


def test_journal_list_on_a_new_library(capsys: pytest.CaptureFixture[str], lib: Library) -> None:
    code, out, _ = run(capsys, "journal", "list")
    assert code == EXIT_OK
    assert "No changes have been made" in out


def test_journal_list(capsys: pytest.CaptureFixture[str], lib: Library, rips: list[Path]) -> None:
    batch_id = adopt(lib, rips)
    code, out, _ = run(capsys, "--library", str(lib.root), "journal", "list")
    assert code == EXIT_OK
    header, row = out.splitlines()
    assert header.split() == ["BATCH", "STARTED", "KIND", "STATUS", "CHANGES"]
    assert row.startswith(batch_id)
    assert "demo" in row
    assert "closed" in row
    assert row.endswith("3 copied in")

    code, out, _ = run(capsys, "journal", "list", "--json")
    [batch] = json.loads(out)["batches"]
    assert batch["batch_id"] == batch_id
    assert batch["operations"] == {"copy_in": 3}
    assert batch["status"] == "closed"


def test_undo(capsys: pytest.CaptureFixture[str], lib: Library, rips: list[Path]) -> None:
    batch_id = adopt(lib, rips)
    code, out, _ = run(capsys, "undo", batch_id, "--dry-run")
    assert code == EXIT_OK
    assert "would do this (nothing has been changed yet)" in out
    assert out.count("↩ Move Music/Unknown Artist/Unsorted/") == 3
    assert len(files_in(lib.paths.music)) == 3

    code, out, _ = run(capsys, "undo", batch_id)
    assert code == EXIT_OK
    assert "Undid 3 changes. The undo itself is batch b_" in out
    assert tree(lib.paths.music) == []

    code, out, _ = run(capsys, "undo", batch_id)
    assert code == EXIT_OK
    assert "Skipped: Already undone" in out
    assert "Nothing needed undoing." in out

    code, out, _ = run(capsys, "journal", "list")
    assert "undone by b_" in out
    assert f"undo of {batch_id}" in out


def test_undo_as_json(capsys: pytest.CaptureFixture[str], lib: Library, rips: list[Path]) -> None:
    batch_id = adopt(lib, rips[:1])
    code, out, _ = run(capsys, "--json", "undo", batch_id)
    assert code == EXIT_OK
    data = json.loads(out)
    assert data["batch_id"] == batch_id
    assert data["undo_batch_id"].startswith("b_")
    assert [op["status"] for op in data["operations"]] == ["done"]


def test_undo_of_an_unknown_batch(capsys: pytest.CaptureFixture[str], lib: Library) -> None:
    lib.close()
    code, _, err = run(capsys, "undo", "b_20200101-000000-000000")
    assert code == EXIT_USER_ERROR
    assert "no batch called b_20200101-000000-000000" in err


def test_undo_needs_the_lock(capsys: pytest.CaptureFixture[str], lib: Library) -> None:
    code, _, err = run(capsys, "undo", "b_20200101-000000-000000")  # `lib` holds the lock
    assert code == EXIT_LOCKED
    assert "Another Music Organizer process" in err


def test_journal_list_needs_no_lock(capsys: pytest.CaptureFixture[str], lib: Library) -> None:
    code, _, _ = run(capsys, "journal", "list")  # while `lib` holds the lock
    assert code == EXIT_OK


def test_no_library_chosen(capsys: pytest.CaptureFixture[str]) -> None:
    for args in (["journal", "list"], ["undo", "b_1"]):
        code, _, err = run(capsys, *args)
        assert code == EXIT_USER_ERROR
        assert "No library chosen yet" in err


def test_the_manual_check(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, rips: list[Path]
) -> None:
    """The step 03b acceptance check, as in docs/CHANGELOG.md."""
    root = tmp_path / "Scratch Library"
    before = {rip: (sha256(rip), rip.stat().st_mtime_ns) for rip in rips}
    assert run(capsys, "init", str(root))[0] == EXIT_OK

    demo = subprocess.run(
        [sys.executable, str(DEMO), str(root), *map(str, rips)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert demo.returncode == 0, demo.stderr
    batch_id = demo.stdout.strip()
    assert batch_id.startswith("b_")

    code, out, _ = run(capsys, "journal", "list")
    assert code == EXIT_OK
    assert batch_id in out
    assert "3 copied in" in out

    assert run(capsys, "undo", batch_id)[0] == EXIT_OK
    assert tree(root / "Music") == []
    assert files_in(root / "_Replaced") == [
        f"Unknown Artist/Unsorted/{rip.name}" for rip in sorted(rips)
    ]
    assert {rip: (sha256(rip), rip.stat().st_mtime_ns) for rip in rips} == before


def test_the_demo_script_explains_itself() -> None:
    result = subprocess.run(
        [sys.executable, str(DEMO)], capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == 1
    assert "Usage" in result.stderr
