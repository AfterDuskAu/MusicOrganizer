"""report: the decision report's counts, percentages, sections and files."""

from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from index_support import add_candidates, add_item, add_source, candidate

from musicorg import report
from musicorg.index import Index, open_index
from musicorg.library import Library

WHEN = datetime(2026, 9, 29, 14, 5).astimezone()


@pytest.fixture
def index(lib: Library, tmp_path: Path) -> Iterator[Index]:
    with open_index(lib.paths, write=True) as opened:
        add_source(opened, tmp_path / "rips")
        yield opened


@pytest.fixture
def filled(index: Index) -> Index:
    """10 items: 4 AUTO, 2 review, 2 not found (one with a poor result, one with none),
    a WAV with no match, and one not matched yet."""
    for n in range(4):
        add_item(index, f"Artist{n} - Song{n}", state="matched_auto", kbps=320,
                 flags=["suspect_upscale"] if n == 0 else [])  # fmt: skip
    add_item(index, "A - Late", state="review", reasons=["duration_mismatch"])
    add_item(index, "B - Near (X Remix)", state="review",
             reasons=["duration_mismatch", "title_fuzzy"], kbps=96)  # fmt: skip
    poor = add_item(index, "C - Obscure", state="not_found")
    add_candidates(index, poor, [candidate("v1", "Something Else", ("Nobody",), 90)])
    add_item(index, "D - Rare", state="not_found")
    add_item(index, "E - Wave", state="not_found", ext=".wav", kbps=1411, flags=["not_adoptable"])
    add_item(index, "F - Later", state="new", kbps=None)
    return index


def test_counts_and_percentages(lib: Library, filled: Index) -> None:
    data = report.gather(lib, filled, now=WHEN)
    assert data.total == 10
    assert data.states == Counter({"matched_auto": 4, "review": 2, "not_found": 2,
                                   "unsupported_format": 1, "new": 1})  # fmt: skip
    assert data.review_reasons == Counter({"duration_mismatch": 2, "title_fuzzy": 1})
    assert (data.no_results, data.low_score) == (1, 1)
    closest = data.misses[0]
    assert (closest["artist"], closest["title"]) == ("C", "Obscure")
    assert closest["best"] == "Something Else — Nobody"
    assert 0 < closest["score"] < 0.6
    assert data.misses[1]["score"] is None
    assert data.suspect_upscale == 1

    text = report.render_markdown(data)
    assert "| `matched_auto` | official match found; safe to queue | 4 | 40.0% |" in text
    assert "| `review` | needs your decision | 2 | 20.0% |" in text
    assert (
        "| `unsupported_format` | no match, and WebM/AAC/WAV can't be adopted yet | 1 | 10.0% |"
        in text
    )  # noqa: E501
    assert "| `matched_user` | you chose the match | 0 | 0.0% |" in text
    assert "| **Total** | | **10** | **100%** |" in text
    assert "| `duration_mismatch` | length more than 2 s off | 2 | 100.0% |" in text
    assert "| `title_fuzzy` | title not exactly the same | 1 | 50.0% |" in text
    assert "- No results at all: 1" in text
    assert "| 2 | D | Rare | no results | — |" in text


def test_version_profile(lib: Library, filled: Index) -> None:
    data = report.gather(lib, filled, now=WHEN)
    assert data.versions["remix"] == Counter({"all": 1, "review": 1})
    assert data.versions["original"]["all"] == 9
    assert data.versions["original"]["matched_auto"] == 4
    assert data.versions["original"]["other"] == 2  # the WAV and the unmatched one
    text = report.render_markdown(data)
    assert "| none (the original) | 9 | 4 | 0 | 1 | 2 | 2 |" in text
    assert "| remix | 1 | 0 | 0 | 1 | 0 | 0 |" in text


def test_quality(lib: Library, filled: Index) -> None:
    data = report.gather(lib, filled, now=WHEN)
    assert data.bitrates == Counter({"320 kbps and over": 4, "128–159 kbps": 3,
                                     "under 128 kbps": 1, "lossless (WAV, FLAC)": 1,
                                     "unknown": 1})  # fmt: skip
    assert data.formats == Counter({"MP3": 9, "WAV": 1})
    text = report.render_markdown(data)
    assert "`suspect_upscale`: 1 (10.0%)" in text
    assert "not a higher bitrate" in text


def test_cost_estimate() -> None:
    data = report.Report(root=Path("/lib"), generated=WHEN, total=1000)
    data.states["matched_auto"] = 650
    report._estimate_downloads(data, report.Config().throttle())
    assert (data.downloads, data.download_days) == (650, 3)  # 300 a day
    # 20 quiet-start pauses of 30 s, 630 of 16.5 s, and 10 s per download.
    assert data.download_hours == pytest.approx((20 * 30 + 630 * 16.5 + 650 * 10) / 3600)
    assert "**about 3 days**" in report.render_markdown(data)


@pytest.mark.parametrize(
    ("auto", "missing", "expected"),
    [
        (600, 100, ["Replace-first will clean most of the library quickly."]),
        (100, 300, ["Prioritise `plan adopt`."]),
        (650, 300, ["Prioritise `plan adopt`.", "Replace-first will clean most"]),
        (500, 200, ["Neither threshold is met: 50% matched automatically"]),
    ],
)
def test_recommendation(auto: int, missing: int, expected: list[str]) -> None:
    data = report.Report(root=Path("/lib"), generated=WHEN, total=1000)
    data.states.update({"matched_auto": auto, "not_found": missing})
    found = report.recommend(data)
    assert len(found) == len(expected)
    for text, wanted in zip(found, expected, strict=True):
        assert wanted in text


def test_files(lib: Library, filled: Index, tmp_path: Path) -> None:
    files = report.write(lib, filled, now=WHEN)
    assert files.markdown == lib.paths.reports / "report-2026-09-29.md"
    assert files.csv == lib.paths.reports / "report-2026-09-29.csv"
    assert files.markdown.read_text(encoding="utf-8").startswith("# Music Organizer")
    with open(files.csv, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 10
    wav = next(r for r in rows if r["format"] == "wav")
    assert (wav["state"], wav["flags"]) == ("unsupported_format", "not_adoptable")
    poor = next(r for r in rows if r["parsed_title"] == "Obscure")
    assert poor["match_url"] == "https://music.youtube.com/watch?v=v1"
    assert poor["source_path"] == str(tmp_path / "rips" / "C - Obscure.mp3")
    # Never overwritten; another folder can be chosen.
    again = report.write(lib, filled, now=WHEN)
    assert again.markdown.name == "report-2026-09-29 (2).md"
    elsewhere = tmp_path / "out"
    elsewhere.mkdir()
    assert report.write(lib, filled, out_dir=elsewhere, now=WHEN).csv.parent == elsewhere


def test_an_empty_library(lib: Library, index: Index) -> None:
    text = report.render_markdown(report.gather(lib, index, now=WHEN))
    assert "| **Total** | | **0** | **100%** |" in text
    assert "Nothing to download yet" in text


def test_minutes() -> None:
    assert [report.minutes(s) for s in (187, 59.6, 3600, None)] == ["3:07", "1:00", "60:00", ""]
