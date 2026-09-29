"""`musicorg report` and `musicorg review export|import`."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from index_support import add_candidates, add_item, add_source, candidate

from musicorg import cli, library, review
from musicorg.errors import EXIT_OK, EXIT_USER_ERROR
from musicorg.index import open_index
from musicorg.library import Library


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = cli.main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def matched(lib: Library, tmp_path: Path) -> Library:
    with open_index(lib.paths, write=True) as index:
        add_source(index, tmp_path / "rips")
        add_item(index, "Band - Auto", state="matched_auto")
        iid = add_item(index, "Band - Maybe", state="review", reasons=["duration_mismatch"])
        add_candidates(index, iid, [candidate("May1xxxxxxx", "Maybe", ("Band",), 205)])
        add_item(index, "Band - Nowhere", state="not_found")
    lib.close()
    return lib


def test_report(capsys: pytest.CaptureFixture[str], matched: Library) -> None:
    # It needs no lock: it runs while another command holds the library.
    with library.open(matched.root, write=True):
        code, out, _ = run(capsys, "report")
    assert code == EXIT_OK
    assert "Report on 3 items:" in out
    assert "matched_auto" in out and "33.3%" in out
    assert "Prioritise `plan adopt`" in out  # 1 of 3 not found: over 30%
    written = [line.split()[-1] for line in out.splitlines() if "report-" in line]
    assert [Path(p).suffix for p in written] == [".md", ".csv"]
    assert all(Path(p).parent == matched.paths.reports for p in written)


def test_review_round_trip(
    capsys: pytest.CaptureFixture[str], matched: Library, tmp_path: Path
) -> None:
    target = tmp_path / "review.csv"
    code, out, _ = run(capsys, "review", "export", str(target))
    assert code == EXIT_OK
    assert f"Wrote 2 items to review: {target}" in out

    rows = list(csv.DictReader(io.StringIO(target.read_text(encoding="utf-8-sig"))))
    rows[0]["decision"] = "accept"
    out_text = io.StringIO()
    writer = csv.DictWriter(out_text, fieldnames=review.COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    target.write_text(out_text.getvalue(), encoding="utf-8")  # as Numbers saves it: no BOM

    code, out, _ = run(capsys, "review", "import", str(target))
    assert code == EXIT_OK
    assert "Applied 1 decisions: 1 accept." in out
    assert "1 rows had no decision" in out
    code, out, _ = run(capsys, "review", "import", str(target))
    assert "Applied 0 decisions." in out
    assert "1 were already decided that way" in out


def test_a_refused_import_lists_the_rows(
    capsys: pytest.CaptureFixture[str], matched: Library, tmp_path: Path
) -> None:
    target = tmp_path / "review.csv"
    run(capsys, "review", "export", str(target))
    text = target.read_text(encoding="utf-8-sig").replace(",,,,,,,\n", ",,maybe,,,,,\n", 1)
    target.write_text(text, encoding="utf-8")
    code, _, err = run(capsys, "review", "import", str(target))
    assert code == EXIT_USER_ERROR
    assert "Nothing was imported" in err
    assert "row 2: 'maybe' isn't a decision" in err
