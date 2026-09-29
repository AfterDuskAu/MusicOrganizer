"""`musicorg sources add|list|remove`, `scan`, `index rebuild` and the counts in `status`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fileops_support import place

from musicorg import cli
from musicorg.errors import EXIT_LOCKED, EXIT_OK, EXIT_USER_ERROR
from musicorg.library import Library


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = cli.main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def rips(tmp_path: Path, samples: dict[str, Path]) -> Path:
    folder = tmp_path / "Rips"
    place(samples["mp3"], folder / "Drake - Hotline Bling (Official Video).mp3")
    place(
        samples["m4a"], folder / "Sub" / "Flight Facilities - Crave You (Adventure Club Remix).m4a"
    )
    place(samples["mp3"], folder / "Hello.mp3")  # a single word: low confidence
    return folder


@pytest.fixture
def closed(lib: Library) -> Library:
    """The library, not held open, as between commands."""
    lib.close()
    return lib


def test_add_list_scan_status_remove(
    capsys: pytest.CaptureFixture[str], closed: Library, rips: Path
) -> None:
    code, out, _ = run(capsys, "sources", "add", str(rips))
    assert code == EXIT_OK
    assert "Added the source s_" in out
    source = out.split()[3].rstrip(":")

    code, out, _ = run(capsys, "status")
    assert "Items: none indexed yet (run `musicorg scan`)" in out

    code, out, err = run(capsys, "scan")
    assert code == EXIT_OK
    assert "Scanned 3 audio files in 1 source(s)" in out
    assert "3 new, 0 changed, 0 unchanged, 0 gone" in out
    assert "3 of 3 files read" in err  # progress goes to stderr

    code, out, _ = run(capsys, "sources", "list")
    assert source in out
    assert "3 items, last scanned" in out

    code, out, _ = run(capsys, "status")
    assert "Items: 3" in out
    assert "new" in out
    assert "Low parse confidence (below 0.5): 1 (33.3%)" in out

    code, out, _ = run(capsys, "--json", "status")
    data = json.loads(out)
    assert (data["items"], data["low_confidence"], data["sources"]) == (3, 1, 1)
    assert data["items_by_state"] == {"new": 3}

    code, out, _ = run(capsys, "scan")
    assert "0 new, 0 changed, 3 unchanged, 0 gone" in out

    code, out, _ = run(capsys, "sources", "remove", source)
    assert code == EXIT_OK
    assert "3 items left the index. No files were touched." in out
    assert sorted(p.name for p in rips.rglob("*.m*")) == sorted(
        ["Drake - Hotline Bling (Official Video).mp3", "Hello.mp3",
         "Flight Facilities - Crave You (Adventure Club Remix).m4a"]
    )  # fmt: skip


def test_json_output(capsys: pytest.CaptureFixture[str], closed: Library, rips: Path) -> None:
    code, out, _ = run(capsys, "--json", "sources", "add", str(rips))
    source = json.loads(out)["id"]
    code, out, err = run(capsys, "--json", "scan")
    assert code == EXIT_OK
    result = json.loads(out)
    assert (result["files"], result["new"], result["sources"]) == (3, 3, [source])
    assert err == ""  # no progress lines in JSON mode
    code, out, _ = run(capsys, "--json", "sources", "list")
    assert json.loads(out)["sources"][0]["items"] == 3


def test_index_rebuild(capsys: pytest.CaptureFixture[str], closed: Library, rips: Path) -> None:
    run(capsys, "sources", "add", str(rips))
    run(capsys, "scan")
    code, out, _ = run(capsys, "index", "rebuild")
    assert code == EXIT_OK
    assert "Rebuilt the index: 0 library tracks." in out
    assert "3 new" in out


def test_refusals_are_plain_english(
    capsys: pytest.CaptureFixture[str], closed: Library, tmp_path: Path
) -> None:
    code, _, err = run(capsys, "scan")
    assert code == EXIT_USER_ERROR
    assert "No sources yet" in err
    code, _, err = run(capsys, "sources", "add", str(tmp_path))
    assert code == EXIT_USER_ERROR
    assert "contains the library" in err
    code, _, err = run(capsys, "sources", "remove", "s_nope")
    assert code == EXIT_USER_ERROR
    assert "There's no source s_nope" in err


def test_scanning_takes_the_lock_but_listing_doesnt(
    capsys: pytest.CaptureFixture[str], lib: Library, rips: Path
) -> None:
    code, _, err = run(capsys, "sources", "add", str(rips))  # `lib` still holds the lock
    assert code == EXIT_LOCKED
    code, out, _ = run(capsys, "sources", "list")
    assert code == EXIT_OK
    assert "No sources yet" in out
    code, _, _ = run(capsys, "status")
    assert code == EXIT_OK
