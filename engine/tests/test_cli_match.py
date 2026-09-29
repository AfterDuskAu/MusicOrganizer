"""`musicorg match`, replaying recorded YouTube Music searches."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from musicorg import cli
from musicorg.errors import EXIT_OK, EXIT_USER_ERROR
from musicorg.index import item_id, open_index
from musicorg.library import Library
from musicorg.normalize import parse_filename

SOURCE = "s_000000000001"


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = cli.main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def scanned(lib: Library, tmp_path: Path) -> Library:
    """Two scanned rips whose searches are recorded: one AUTO, one for review."""
    with open_index(lib.paths, write=True) as index:
        index.put_source(SOURCE, str(tmp_path / "rips"), "2026-09-29T00:00:00Z")
        rows = []
        for name, seconds in (("The Chainsmokers - Closer", 244.9), ("Drake - One Dance", 174.7)):
            parsed = parse_filename(name)
            rows.append({
                "id": item_id(SOURCE, f"{name}.mp3"), "source_id": SOURCE,
                "rel_path": f"{name}.mp3", "size": 1, "mtime_ns": 1, "ext": ".mp3",
                "duration_s": seconds, "parsed_artist": parsed.artist,
                "parsed_title": parsed.title, "parse_confidence": parsed.confidence,
                "parsed_json": parsed.to_dict(), "scanned_at": "2026-09-29T00:00:00Z",
            })  # fmt: skip
        index.put_items(rows)
    lib.close()
    return lib


def test_match(capsys: pytest.CaptureFixture[str], scanned: Library) -> None:
    code, out, err = run(capsys, "match")
    assert code == EXIT_OK
    assert "Matched 2 items" in out
    assert "1 automatic, 1 to review, 0 not found" in out
    sample = scanned.paths.reports / "auto-sample.csv"
    assert f"Listen to 1 of the automatic matches: {sample}" in out
    assert "2 of 2 items" in err  # progress goes to stderr
    text = sample.read_text(encoding="utf-8-sig")
    assert "https://music.youtube.com/watch?v=r7zTKRonHXM" in text

    code, out, _ = run(capsys, "status")
    assert "matched_auto" in out and "review" in out

    code, out, _ = run(capsys, "match")
    assert "No new items to match" in out


def test_match_json_and_limit(capsys: pytest.CaptureFixture[str], scanned: Library) -> None:
    code, out, _ = run(capsys, "match", "--limit", "1", "--json")
    assert code == EXIT_OK
    result = json.loads(out)
    assert (result["items"], result["left"]) == (1, 1)
    assert set(result) >= {"matched_auto", "review", "not_found", "searches", "sample"}


def test_limit_must_be_positive(capsys: pytest.CaptureFixture[str], scanned: Library) -> None:
    code, _, err = run(capsys, "match", "--limit", "0")
    assert code == EXIT_USER_ERROR
    assert "--limit must be 1 or more" in err


def test_recheck(capsys: pytest.CaptureFixture[str], scanned: Library) -> None:
    run(capsys, "match")
    code, out, _ = run(capsys, "match", "--recheck")
    assert code == EXIT_OK
    assert "Re-checked 1 review and not-found items (no searching)." in out
    assert "Nothing changed." in out
    code, _, err = run(capsys, "match", "--recheck", "--rescan")
    assert code == EXIT_USER_ERROR and "takes neither" in err
