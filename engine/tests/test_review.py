"""review: the spreadsheet round trip (export → edit → import), its checks, and that
importing is safe to repeat and survives an index rebuild."""

from __future__ import annotations

import csv
import io
import json
import random
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fileops_support import place
from index_support import add_candidates, add_item, add_source, candidate

from musicorg import match, review, scan, youtube
from musicorg.index import Index, open_index
from musicorg.library import Library


@pytest.fixture
def index(lib: Library, tmp_path: Path) -> Iterator[Index]:
    with open_index(lib.paths, write=True) as opened:
        add_source(opened, tmp_path / "rips")
        yield opened


@pytest.fixture
def items(index: Index) -> dict[str, str]:
    """Four items in review with three candidates each, and one not found."""
    ids = {}
    for name in ("Alpha", "Bravo", "Charlie", "Delta"):
        iid = add_item(index, f"Band - {name}", state="review", reasons=["duration_mismatch"])
        add_candidates(index, iid, [
            candidate(f"{name[:3]}1xxxxxxx", name, ("Band",), 205),
            candidate(f"{name[:3]}2xxxxxxx", f"{name} (Live)", ("Band",), 200),
            candidate(f"{name[:3]}3xxxxxxx", name, ("Other Band",), 230),
        ])  # fmt: skip
        ids[name] = iid
    ids["Echo"] = add_item(index, "Band - Echo", state="not_found")
    return ids


def export_rows(lib: Library, index: Index, tmp_path: Path, **kw: Any) -> list[dict[str, str]]:
    result = review.export(lib, index, tmp_path / "review.csv", **kw)
    with open(result.path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_rows(path: Path, rows: list[dict[str, str]], encoding: str = "utf-8-sig") -> Path:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=review.COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    path.write_bytes(out.getvalue().encode(encoding))
    return path


def decide(rows: list[dict[str, str]], **decisions: str) -> list[dict[str, str]]:
    """Fill in the decision column by parsed title."""
    for row in rows:
        if row["parsed_title"] in decisions:
            row["decision"] = decisions[row["parsed_title"]]
    return rows


def state_json(lib: Library) -> dict[str, Any]:
    return json.loads(lib.paths.state_file.read_text(encoding="utf-8"))


# ---- export ----------------------------------------------------------------------------


def test_export(lib: Library, index: Index, items: dict[str, str], tmp_path: Path) -> None:
    result = review.export(lib, index, tmp_path / "review.csv")
    assert (result.path, result.rows) == (tmp_path / "review.csv", 5)
    data = result.path.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")  # UTF-8 with a BOM, for Excel
    rows = export_rows(lib, index, tmp_path)  # a second export gets " (2)"
    assert list(rows[0]) == review.COLUMNS
    assert [r["parsed_title"] for r in rows][-1] == "Echo"  # not found after review
    alpha = next(r for r in rows if r["parsed_title"] == "Alpha")
    assert alpha["source_path"] == str(tmp_path / "rips" / "Band - Alpha.mp3")
    assert (alpha["duration"], alpha["reasons"]) == ("3:20", "duration_mismatch")
    assert alpha["cand1_url"] == "https://music.youtube.com/watch?v=Alp1xxxxxxx"
    assert (alpha["cand1_title"], alpha["cand1_duration"]) == ("Alpha", "3:25")
    assert alpha["cand2_version"] == "live"
    assert alpha["decision"] == alpha["fingerprint"] == ""
    assert (tmp_path / "review (2).csv").exists()


def test_export_can_include_auto(
    lib: Library, index: Index, items: dict[str, str], tmp_path: Path
) -> None:
    add_item(index, "Band - Foxtrot", state="matched_auto")
    assert len(export_rows(lib, index, tmp_path)) == 5
    assert len(export_rows(lib, index, tmp_path, include_auto=True)) == 6


# ---- import ----------------------------------------------------------------------------


def test_round_trip(lib: Library, index: Index, items: dict[str, str], tmp_path: Path) -> None:
    rows = decide(export_rows(lib, index, tmp_path), Alpha="accept", Bravo="cand:3",
                  Charlie="reject:1", Echo="only_copy")  # fmt: skip
    next(r for r in rows if r["parsed_title"] == "Echo").update(artist_fix="The Band")
    result = review.import_csv(lib, index, write_rows(tmp_path / "edited.csv", rows))
    assert result.applied == {"accept": 1, "candidate": 1, "reject": 1, "only_copy": 1}
    assert (result.unchanged, result.blank) == (0, 1)  # Delta had no decision

    state_of = {name: index.item(iid)["state"] for name, iid in items.items()}  # type: ignore[index]
    assert state_of == {"Alpha": "matched_user", "Bravo": "matched_user", "Charlie": "review",
                        "Delta": "review", "Echo": "only_copy"}  # fmt: skip
    data = state_json(lib)
    alpha = data["decisions"][items["Alpha"]]
    assert (alpha["decision"], alpha["video_id"], alpha["title"]) == (
        "accept", "Alp1xxxxxxx", "Alpha")  # fmt: skip
    assert alpha["candidate_id"] == match.candidate_id(items["Alpha"], "Alp1xxxxxxx")
    assert data["decisions"][items["Bravo"]]["video_id"] == "Bra3xxxxxxx"
    assert data["decisions"][items["Echo"]] == {
        "decision": "only_copy", "artist_fix": "The Band",
        "decided_at": data["decisions"][items["Echo"]]["decided_at"]}  # fmt: skip
    # The rejected candidate is gone and never proposed again.
    assert data["rejected"] == {items["Charlie"]: ["Cha1xxxxxxx"]}
    assert "Cha1xxxxxxx" not in [c["video_id"] for c in index.candidates(items["Charlie"])]
    # The chosen candidate keeps the index's own copy, with its album.
    kept = index.candidates(items["Bravo"])
    chosen = next(c for c in kept if c["video_id"] == "Bra3xxxxxxx")
    assert chosen["payload"]["album"] == "Album"


def test_importing_twice_changes_nothing(
    lib: Library, index: Index, items: dict[str, str], tmp_path: Path
) -> None:
    rows = decide(export_rows(lib, index, tmp_path), Alpha="accept", Bravo="reject:2",
                  Echo="skip")  # fmt: skip
    edited = write_rows(tmp_path / "edited.csv", rows)
    review.import_csv(lib, index, edited)
    saved = lib.paths.state_file.read_bytes()
    states = index.counts_by_state()
    again = review.import_csv(lib, index, edited)
    assert again.applied == {}
    assert again.unchanged == 3
    assert lib.paths.state_file.read_bytes() == saved
    assert index.counts_by_state() == states


def test_every_bad_row_is_reported_and_nothing_imported(
    lib: Library, index: Index, items: dict[str, str], tmp_path: Path
) -> None:
    rows = export_rows(lib, index, tmp_path)
    rows[0]["decision"] = "accept"  # a good row: still not imported
    rows[1]["decision"] = "maybe"
    rows[2]["source_path"] = "/somewhere/else.mp3"
    rows[3]["decision"], rows[3]["url"] = "url", "https://vimeo.com/123"
    rows[4]["decision"] = "cand:2"  # Echo has no candidates
    rows.append({**rows[0], "decision": "skip"})  # the same item twice
    rows.append({**rows[0], "item_id": "i_0000000000000000"})
    before = lib.paths.state_file.read_bytes()
    with pytest.raises(review.ReviewImportError) as refused:
        review.import_csv(lib, index, write_rows(tmp_path / "edited.csv", rows))
    problems = refused.value.problems
    assert [p.split(":")[0] for p in problems] == ["row 3", "row 4", "row 5", "row 6", "row 7",
                                                   "row 8"]  # fmt: skip
    assert "'maybe' isn't a decision" in problems[0]
    assert "source_path doesn't match" in problems[1]
    assert "isn't a YouTube link" in problems[2]
    assert "cand:2 but the row has no candidate 2" in problems[3]
    assert "also on row 2" in problems[4]
    assert "unknown item_id" in problems[5]
    assert "Nothing was imported" in refused.value.message
    assert lib.paths.state_file.read_bytes() == before


def test_text_survives_the_round_trip(lib: Library, index: Index, tmp_path: Path) -> None:
    tricky = 'Say "Hi", 世界; Beyoncé'
    iid = add_item(index, f"Band - {tricky}", state="not_found")
    rows = export_rows(lib, index, tmp_path)
    assert rows[0]["parsed_title"] == tricky
    fix = 'Line one, "quoted"\nline two — 夜に駆ける'
    rows[0].update(decision="only_copy", title_fix=fix)
    review.import_csv(lib, index, write_rows(tmp_path / "edited.csv", rows))
    assert state_json(lib)["decisions"][iid]["title_fix"] == fix


def test_a_file_not_saved_as_utf8_is_refused(lib: Library, index: Index, tmp_path: Path) -> None:
    add_item(index, "Band - Café", state="not_found")
    rows = decide(export_rows(lib, index, tmp_path), Café="skip")
    with pytest.raises(review.ReviewImportError, match="Save it as 'CSV UTF-8'"):
        review.import_csv(lib, index, write_rows(tmp_path / "old.csv", rows, "cp1252"))


def test_a_file_without_the_columns_is_refused(lib: Library, index: Index, tmp_path: Path) -> None:
    path = tmp_path / "other.csv"
    path.write_text("name,artist\nx,y\n", encoding="utf-8")
    with pytest.raises(review.ReviewImportError, match="item_id, source_path, decision"):
        review.import_csv(lib, index, path)


@pytest.mark.parametrize(
    ("link", "video_id"),
    [
        ("https://music.youtube.com/watch?v=xjj_OVvVQFc", "xjj_OVvVQFc"),
        ("https://www.youtube.com/watch?v=xjj_OVvVQFc&list=RDx", "xjj_OVvVQFc"),
        ("youtube.com/watch?feature=share&v=xjj_OVvVQFc", "xjj_OVvVQFc"),
        ("https://youtu.be/xjj_OVvVQFc?si=abc", "xjj_OVvVQFc"),
        ("https://www.youtube.com/playlist?list=PL123", None),
        ("https://vimeo.com/12345678901", None),
        ("xjj_OVvVQFc", None),
    ],
)
def test_links(link: str, video_id: str | None) -> None:
    found = review._LINK.fullmatch(link)
    assert (found.group("id") if found else None) == video_id


# ---- pasted links ----------------------------------------------------------------------


def test_a_pasted_link_is_scored_not_trusted(lib: Library, index: Index, tmp_path: Path) -> None:
    """Replayed: xjj_OVvVQFc is Flight Facilities' Crave You (3:55)."""
    right = add_item(index, "Flight Facilities - Crave You", state="not_found", seconds=235)
    wrong = add_item(index, "Somebody - Other Song", state="not_found", seconds=100)
    gone = add_item(index, "Somebody - Lost Song", state="not_found")
    rows = export_rows(lib, index, tmp_path)
    for row in rows:
        row["decision"] = "url"
        row["url"] = ("https://youtu.be/zzzzzzzzzzz" if row["item_id"] == gone
                      else "https://music.youtube.com/watch?v=xjj_OVvVQFc")  # fmt: skip
    result = review.import_csv(lib, index, write_rows(tmp_path / "edited.csv", rows))
    assert result.applied == {"url": 1}
    assert result.kept_in_review == 2
    assert index.item(right)["state"] == "matched_user"  # type: ignore[index]
    entry = state_json(lib)["decisions"][right]
    assert (entry["decision"], entry["video_id"]) == ("url", "xjj_OVvVQFc")
    assert entry["score"] >= 0.9
    low = index.item(wrong)
    assert (low["state"], low["reasons_json"]) == ("review", ["url_low_score"])  # type: ignore[index]
    assert index.candidates(wrong)[0]["video_id"] == "xjj_OVvVQFc"  # shown on the next export
    assert index.item(gone)["reasons_json"] == ["video_unavailable"]  # type: ignore[index]
    assert wrong not in state_json(lib)["decisions"]
    assert any("below 0.60" in w for w in result.warnings)
    assert any("isn't on YouTube Music" in w for w in result.warnings)


# ---- after a rebuild -------------------------------------------------------------------


def test_a_spreadsheet_from_before_a_rebuild_still_imports(
    lib: Library, index: Index, samples: dict[str, Path], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    rips = tmp_path / "Rips"
    place(samples["mp3"], rips / "Band - Alpha.mp3")
    place(samples["mp3"], rips / "Band - Bravo.mp3")
    index.remove_source("s_000000000001")
    scan.add_source(lib, index, rips)
    scan.scan(lib, index)

    def search(query: str, **kw: Any) -> list[youtube.Candidate]:
        title = query.split()[-1]
        return [candidate(f"{title[:3]}1xxxxxxx", title, ("Band",), 60),
                candidate(f"{title[:3]}2xxxxxxx", title, ("Band",), 90)]  # fmt: skip

    monkeypatch.setattr(youtube, "search_songs", search)
    match.run(lib, index, rng=random.Random(1))
    rows = decide(export_rows(lib, index, tmp_path), Alpha="cand:2", Bravo="reject:1")
    assert len(rows) == 2

    scan.rebuild(lib, index)  # candidates gone, states back to new
    assert index.all_candidates() == {}
    result = review.import_csv(lib, index, write_rows(tmp_path / "edited.csv", rows))
    assert result.applied == {"candidate": 1, "reject": 1}
    alpha = next(i for i in index.items() if i["parsed_title"] == "Alpha")
    assert alpha["state"] == "matched_user"
    assert index.candidates(alpha["id"])[0]["video_id"] == "Alp2xxxxxxx"
    assert state_json(lib)["decisions"][alpha["id"]]["video_id"] == "Alp2xxxxxxx"
    # And a rebuild keeps the decision (state.json is the record).
    scan.rebuild(lib, index)
    assert index.item(alpha["id"])["state"] == "matched_user"  # type: ignore[index]
