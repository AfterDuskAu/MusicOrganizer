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

from musicorg import match, pipeline, review, scan, state, youtube
from musicorg.index import PASTED, Index, open_index
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


def test_official_is_an_accept_with_the_owners_word_over_an_unsure_fingerprint(
    lib: Library, index: Index, items: dict[str, str], tmp_path: Path
) -> None:
    rows = decide(export_rows(lib, index, tmp_path), Alpha="official", Bravo="Official: 3")
    result = review.import_csv(lib, index, write_rows(tmp_path / "edited.csv", rows))
    assert result.applied == {"accept": 1, "candidate": 1}
    data = state_json(lib)
    alpha, bravo = data["decisions"][items["Alpha"]], data["decisions"][items["Bravo"]]
    assert (alpha["decision"], alpha["video_id"], alpha["override"]) == (
        "accept", "Alp1xxxxxxx", True)  # fmt: skip
    assert (bravo["decision"], bravo["video_id"], bravo["override"]) == (
        "candidate", "Bra3xxxxxxx", True)  # fmt: skip
    assert index.item(items["Alpha"])["state"] == "matched_user"  # type: ignore[index]


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


# ---- a pasted link that scores low ------------------------------------------------------

# Replayed: Michael Franti & Spearhead's Bomb the World (4:29) and The Neighbourhood's
# Daddy Issues (4:21). Against the rip below they score 0.35 and 0.12.
FRANTI, DADDY = "SpTYh-uYgQs", "lqSgsq4Bn2c"
LINK = "https://music.youtube.com/watch?v="


@pytest.fixture
def stuck(index: Index) -> str:
    """The owner's stuck case: a remix filed under the remixer's name, whose official
    track is under the original artist's, so the right link scores low. Three other
    candidates score above it (0.80, 0.74 and 0.70)."""
    iid = add_item(index, "Kygo - Bomb the World R", state="review", seconds=240,
                   reasons=["version_mismatch"])  # fmt: skip
    add_candidates(index, iid, [
        candidate("Bom1xxxxxxx", "Bomb the World", ("Kygo",), 240),
        candidate("Bom2xxxxxxx", "Bomb the World (Live)", ("Kygo",), 300),
        candidate("Bom3xxxxxxx", "Bomb the World", ("Kygo",), 250),
    ])  # fmt: skip
    return iid


BY_SCORE = ["Bom1xxxxxxx", "Bom3xxxxxxx", "Bom2xxxxxxx"]


def row_decision(
    lib: Library, index: Index, tmp_path: Path, item_id: str, decision: str, link: str = ""
) -> review.ImportResult:
    """Export, fill in one row's decision (and url), and import: what the owner does."""
    rows = export_rows(lib, index, tmp_path)
    next(r for r in rows if r["item_id"] == item_id).update(decision=decision, url=link)
    return review.import_csv(lib, index, write_rows(tmp_path / "edited.csv", rows))


def order(index: Index, item_id: str) -> list[str]:
    return [c["video_id"] for c in index.candidates(item_id)]


def marked(index: Index, item_id: str) -> list[str]:
    return [c["video_id"] for c in index.candidates(item_id) if PASTED in c["payload"]]


def test_a_low_scoring_pasted_link_is_candidate_1_and_can_be_accepted(
    lib: Library, index: Index, stuck: str, tmp_path: Path
) -> None:
    item = index.item(stuck)
    assert item is not None
    track = youtube.get_track(FRANTI)
    assert track is not None
    low = match.assess(match.rip_of(item), track).score
    assert low < match.REVIEW_SCORE < min(c["score"] for c in index.candidates(stuck))

    result = row_decision(lib, index, tmp_path, stuck, "url", LINK + FRANTI)
    assert (result.applied, result.kept_in_review) == ({}, 1)
    # The message is true now, and names are printed as YouTube Music gives them.
    assert result.warnings == [
        "row 2: Bomb the World — Michael Franti & Spearhead scores 0.35 against this rip "
        "(below 0.60), so it stays in review; it's candidate 1 on the next export."
    ]
    kept = index.item(stuck)
    assert kept is not None
    assert (kept["state"], kept["reasons_json"]) == ("review", ["url_low_score"])
    assert stuck not in state_json(lib).get("decisions", {})  # scored, not trusted
    assert order(index, stuck) == [FRANTI, *BY_SCORE]
    assert marked(index, stuck) == [FRANTI]

    # The next export: the pasted track is candidate 1, with its real low score.
    row = next(r for r in export_rows(lib, index, tmp_path) if r["item_id"] == stuck)
    assert row["reasons"] == "url_low_score"
    assert (row["cand1_url"], row["cand1_score"]) == (LINK + FRANTI, f"{low:.3f}")
    assert (row["cand1_title"], row["cand1_artists"], row["cand1_duration"]) == (
        "Bomb the World", "Michael Franti & Spearhead", "4:29")  # fmt: skip
    assert [row[f"cand{n}_url"] for n in (2, 3)] == [LINK + v for v in BY_SCORE[:2]]
    assert [row[f"cand{n}_score"] for n in (2, 3)] == ["0.800", "0.739"]

    # `accept` takes it like any candidate 1: the owner's decision, with no score check.
    result = row_decision(lib, index, tmp_path, stuck, "accept")
    assert (result.applied, result.warnings) == ({"accept": 1}, [])
    accepted = index.item(stuck)
    assert accepted is not None
    assert (accepted["state"], accepted["reasons_json"]) == ("matched_user", [])
    entry = state_json(lib)["decisions"][stuck]
    assert (entry["decision"], entry["video_id"], entry["title"]) == (
        "accept", FRANTI, "Bomb the World")  # fmt: skip
    # Decided: the mark is gone, and what a plan would fetch for the rip is that video.
    assert marked(index, stuck) == []
    assert order(index, stuck) == [*BY_SCORE, FRANTI]
    decisions = state.decisions(lib.load_state().data)
    chosen = pipeline._chosen(index, accepted, decisions)
    assert chosen is not None and chosen["video_id"] == FRANTI
    assert chosen["payload"]["artists"] == ["Michael Franti & Spearhead"]


def test_pasting_the_same_link_again_adds_nothing(
    lib: Library, index: Index, stuck: str, tmp_path: Path
) -> None:
    row_decision(lib, index, tmp_path, stuck, "url", LINK + FRANTI)
    before = index.candidates(stuck)
    saved = lib.paths.state_file.read_bytes()
    again = row_decision(lib, index, tmp_path, stuck, "url", "https://youtu.be/" + FRANTI)
    assert (again.applied, again.kept_in_review) == ({}, 1)
    assert index.candidates(stuck) == before  # one of each, the pasted one still first
    assert order(index, stuck) == [FRANTI, *BY_SCORE]
    assert lib.paths.state_file.read_bytes() == saved


def test_pasting_another_low_scoring_link_moves_the_mark(
    lib: Library, index: Index, stuck: str, tmp_path: Path
) -> None:
    row_decision(lib, index, tmp_path, stuck, "url", LINK + FRANTI)
    result = row_decision(lib, index, tmp_path, stuck, "url", LINK + DADDY)
    assert result.kept_in_review == 1
    # The new link is first; the old one is an ordinary candidate again, by its score.
    assert order(index, stuck) == [DADDY, *BY_SCORE, FRANTI]
    assert marked(index, stuck) == [DADDY]
    row = next(r for r in export_rows(lib, index, tmp_path) if r["item_id"] == stuck)
    assert (row["cand1_title"], row["cand1_score"]) == ("Daddy Issues", "0.120")


def test_a_link_that_isnt_there_leaves_the_pasted_link_first(
    lib: Library, index: Index, stuck: str, tmp_path: Path
) -> None:
    row_decision(lib, index, tmp_path, stuck, "url", LINK + FRANTI)
    result = row_decision(lib, index, tmp_path, stuck, "url", "https://youtu.be/zzzzzzzzzzz")
    assert "isn't on YouTube Music" in result.warnings[0]
    assert index.item(stuck)["reasons_json"] == ["video_unavailable"]  # type: ignore[index]
    assert order(index, stuck) == [FRANTI, *BY_SCORE]  # nothing took its place
    assert marked(index, stuck) == [FRANTI]


def test_rejecting_the_pasted_link_takes_it_out(
    lib: Library, index: Index, stuck: str, tmp_path: Path
) -> None:
    row_decision(lib, index, tmp_path, stuck, "url", LINK + FRANTI)
    result = row_decision(lib, index, tmp_path, stuck, "reject:1")
    assert result.applied == {"reject": 1}
    assert state_json(lib)["rejected"] == {stuck: [FRANTI]}
    # What's left is in score order, and the item is classified from it as before.
    assert order(index, stuck) == BY_SCORE
    assert marked(index, stuck) == []
    item = index.item(stuck)
    assert item is not None
    assert (item["state"], item["reasons_json"]) == ("review", ["version_mismatch"])


def test_rejecting_another_candidate_leaves_the_pasted_link_first(
    lib: Library, index: Index, stuck: str, tmp_path: Path
) -> None:
    row_decision(lib, index, tmp_path, stuck, "url", LINK + FRANTI)
    row_decision(lib, index, tmp_path, stuck, "reject:2")  # the best by score
    assert state_json(lib)["rejected"] == {stuck: ["Bom1xxxxxxx"]}
    assert order(index, stuck) == [FRANTI, "Bom3xxxxxxx", "Bom2xxxxxxx"]
    assert marked(index, stuck) == [FRANTI]
    item = index.item(stuck)
    assert item is not None
    assert (item["state"], item["reasons_json"]) == ("review", ["url_low_score"])
    # And it can still be accepted.
    row_decision(lib, index, tmp_path, stuck, "accept")
    assert state_json(lib)["decisions"][stuck]["video_id"] == FRANTI


@pytest.mark.parametrize(
    ("decision", "new_state", "video_id"),
    [("cand:2", "matched_user", "Bom1xxxxxxx"), ("skip", "skipped", None),
     ("only_copy", "only_copy", None)],
)  # fmt: skip
def test_deciding_the_item_another_way_ends_the_pasted_links_turn(
    lib: Library, index: Index, stuck: str, tmp_path: Path, decision: str, new_state: str,
    video_id: str | None,
) -> None:  # fmt: skip
    row_decision(lib, index, tmp_path, stuck, "url", LINK + FRANTI)
    row_decision(lib, index, tmp_path, stuck, decision)
    assert index.item(stuck)["state"] == new_state  # type: ignore[index]
    assert state_json(lib)["decisions"][stuck].get("video_id") == video_id
    assert marked(index, stuck) == []
    assert order(index, stuck) == [*BY_SCORE, FRANTI]  # by score again


def test_the_review_page_decision_works_the_same(lib: Library, index: Index, stuck: str) -> None:
    """`decide_one` is what the review page and `review.decide` call."""
    pasted = review.decide_one(lib, index, stuck, "url", link=LINK + FRANTI)
    assert not pasted.changed and "candidate 1" in (pasted.warning or "")
    assert order(index, stuck) == [FRANTI, *BY_SCORE]
    used = review.decide_one(lib, index, stuck, "use", video_id=FRANTI)
    assert used.changed and used.warning is None
    entry = state_json(lib)["decisions"][stuck]
    assert (entry["decision"], entry["video_id"]) == ("accept", FRANTI)  # it was candidate 1
    assert index.item(stuck)["state"] == "matched_user"  # type: ignore[index]
    assert marked(index, stuck) == []


def test_a_link_that_scores_well_ends_a_low_links_turn(
    lib: Library, index: Index, stuck: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row_decision(lib, index, tmp_path, stuck, "url", LINK + FRANTI)
    assert marked(index, stuck) == [FRANTI]
    good = candidate("Bom9xxxxxxx", "Bomb the World", ("Kygo",), 240)
    monkeypatch.setattr(youtube, "get_track", lambda video_id: good)
    result = row_decision(lib, index, tmp_path, stuck, "url", LINK + "Bom9xxxxxxx")
    assert (result.applied, result.warnings) == ({"url": 1}, [])
    item = index.item(stuck)
    assert item is not None
    assert (item["state"], item["reasons_json"]) == ("matched_user", [])
    entry = state_json(lib)["decisions"][stuck]
    assert (entry["decision"], entry["video_id"]) == ("url", "Bom9xxxxxxx")
    # The low link is an ordinary candidate again, by its score; the accepted one is first
    # (it scores as much as the best of them, and was stored before it).
    assert marked(index, stuck) == []
    assert order(index, stuck) == ["Bom9xxxxxxx", *BY_SCORE, FRANTI]
    chosen = pipeline._chosen(index, item, state.decisions(lib.load_state().data))
    assert chosen is not None and chosen["video_id"] == "Bom9xxxxxxx"


def test_an_accept_from_a_sheet_exported_before_the_paste_ends_the_links_turn(
    lib: Library, index: Index, stuck: str, tmp_path: Path
) -> None:
    older = export_rows(lib, index, tmp_path)  # its cand1 is the best by score
    review.decide_one(lib, index, stuck, "url", link=LINK + FRANTI)
    assert marked(index, stuck) == [FRANTI]
    next(r for r in older if r["item_id"] == stuck)["decision"] = "accept"
    result = review.import_csv(lib, index, write_rows(tmp_path / "older.csv", older))
    assert result.applied == {"accept": 1}
    assert index.item(stuck)["state"] == "matched_user"  # type: ignore[index]
    assert state_json(lib)["decisions"][stuck]["video_id"] == "Bom1xxxxxxx"  # that sheet's cand1
    assert marked(index, stuck) == []
    assert order(index, stuck) == [*BY_SCORE, FRANTI]


def test_importing_the_sheet_with_the_link_again_after_the_accept_changes_nothing(
    lib: Library, index: Index, samples: dict[str, Path], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """Sheet 1 has `url` and the link; sheet 2, the next export, has `accept`. Importing
    sheet 1 once more must not put the accepted song back in review."""
    rips = tmp_path / "Rips"
    place(samples["mp3"], rips / "Kygo - Bomb the World R.mp3")
    index.remove_source("s_000000000001")
    scan.add_source(lib, index, rips)
    scan.scan(lib, index)

    def search(query: str, **kw: Any) -> list[youtube.Candidate]:
        return [candidate("Bom1xxxxxxx", "Bomb the World", ("Kygo",), 3),
                candidate("Bom2xxxxxxx", "Bomb the World (Live)", ("Kygo",), 9),
                candidate("Bom3xxxxxxx", "Bomb the World", ("Kygo",), 13)]  # fmt: skip

    monkeypatch.setattr(youtube, "search_songs", search)
    match.run(lib, index, rng=random.Random(1))
    (item,) = index.items()
    iid = item["id"]
    assert item["state"] == "review"

    def in_plans() -> tuple[list[str], list[str]]:
        """The videos `plan adopt --matched` and `plan replace` would take for the rip."""
        adopt = pipeline.plan_adopt(lib, index, matched=True).operations
        replace = pipeline.plan_replace(lib, index).operations
        return (
            [op.params["video_id"] for op in adopt if op.item_id == iid],
            [op.params["video_id"] for op in replace if op.item_id == iid],
        )

    sheet_1 = export_rows(lib, index, tmp_path)
    sheet_1[0].update(decision="url", url=LINK + FRANTI)
    links = write_rows(tmp_path / "links.csv", sheet_1)
    first = review.import_csv(lib, index, links)
    assert (first.applied, first.kept_in_review, len(first.warnings)) == ({}, 1, 1)
    assert in_plans() == ([], [])  # still in review

    sheet_2 = export_rows(lib, index, tmp_path)
    assert sheet_2[0]["cand1_url"] == LINK + FRANTI
    sheet_2[0]["decision"] = "accept"
    accepted = review.import_csv(lib, index, write_rows(tmp_path / "accepted.csv", sheet_2))
    assert accepted.applied == {"accept": 1}
    assert in_plans() == ([FRANTI], [FRANTI])
    saved = lib.paths.state_file.read_bytes()
    stored = index.candidates(iid)

    for _ in range(2):  # the sheet with the link, again and again
        again = review.import_csv(lib, index, links)
        assert (again.applied, again.unchanged, again.kept_in_review) == ({}, 1, 0)
        assert again.warnings == []
        kept = index.item(iid)
        assert kept is not None
        assert (kept["state"], kept["reasons_json"]) == ("matched_user", [])
        assert marked(index, iid) == []
        assert index.candidates(iid) == stored
        assert lib.paths.state_file.read_bytes() == saved
        assert in_plans() == ([FRANTI], [FRANTI])
    # The review page's "paste a link" says the same.
    pasted = review.decide_one(lib, index, iid, "url", link=LINK + FRANTI)
    assert (pasted.changed, pasted.warning) == (False, None)
    assert index.item(iid)["state"] == "matched_user"  # type: ignore[index]

    # A low link for another track is still only a suggestion: scored, not trusted.
    other = review.decide_one(lib, index, iid, "url", link=LINK + DADDY)
    assert "below 0.60" in (other.warning or "")
    assert state_json(lib)["decisions"][iid]["video_id"] == FRANTI


def test_a_rejected_link_pasted_again_is_the_owners_newer_word(
    lib: Library, index: Index, stuck: str, tmp_path: Path
) -> None:
    row_decision(lib, index, tmp_path, stuck, "url", LINK + FRANTI)
    full = {k: v for k, v in index.candidates(stuck)[0]["payload"].items() if k != PASTED}
    assert full["album"] and full["album_browse_id"]  # the link's own details
    row_decision(lib, index, tmp_path, stuck, "reject:1")
    assert state_json(lib)["rejected"] == {stuck: [FRANTI]}
    assert order(index, stuck) == BY_SCORE

    # Pasted again: the rejection is taken back, and the link is first like any pasted one.
    again = review.decide_one(lib, index, stuck, "url", link=LINK + FRANTI)
    assert again.changed and "below 0.60" in (again.warning or "")  # state.json did change
    assert state_json(lib)["rejected"] == {}
    assert stuck not in state_json(lib).get("decisions", {})
    assert order(index, stuck) == [FRANTI, *BY_SCORE]
    assert marked(index, stuck) == [FRANTI]

    # So rejecting another candidate, or a link that isn't there, leaves it first.
    row_decision(lib, index, tmp_path, stuck, "reject:2")
    assert state_json(lib)["rejected"] == {stuck: ["Bom1xxxxxxx"]}
    row_decision(lib, index, tmp_path, stuck, "url", "https://youtu.be/zzzzzzzzzzz")
    assert order(index, stuck) == [FRANTI, "Bom3xxxxxxx", "Bom2xxxxxxx"]
    assert marked(index, stuck) == [FRANTI]

    # And `accept` keeps the index's own copy of it, with its album.
    row_decision(lib, index, tmp_path, stuck, "accept")
    item = index.item(stuck)
    assert item is not None and item["state"] == "matched_user"
    chosen = pipeline._chosen(index, item, state.decisions(lib.load_state().data))
    assert chosen is not None and chosen["video_id"] == FRANTI
    assert chosen["payload"] == full
    assert state_json(lib)["rejected"] == {stuck: ["Bom1xxxxxxx"]}  # not both chosen and rejected


def test_a_rejected_link_that_scores_well_can_be_pasted_again(lib: Library, index: Index) -> None:
    """Replayed: xjj_OVvVQFc is Flight Facilities' Crave You (3:55)."""
    iid = add_item(index, "Flight Facilities - Crave You", state="not_found", seconds=235)
    link = "https://youtu.be/xjj_OVvVQFc"
    review.decide_one(lib, index, iid, "url", link=link)
    review.decide_one(lib, index, iid, "reject", video_id="xjj_OVvVQFc")
    assert state_json(lib)["rejected"] == {iid: ["xjj_OVvVQFc"]}
    assert iid not in state_json(lib)["decisions"]
    pasted = review.decide_one(lib, index, iid, "url", link=link)
    assert pasted.changed and pasted.warning is None
    assert state_json(lib)["rejected"] == {}
    assert state_json(lib)["decisions"][iid]["video_id"] == "xjj_OVvVQFc"
    assert index.item(iid)["state"] == "matched_user"  # type: ignore[index]
    assert order(index, iid) == ["xjj_OVvVQFc"]

    # An engine from before 2026-10-04 left such a link on the rejected list while it was
    # the decision. Pasting it once more takes it off, and changes nothing else.
    decided = state_json(lib)["decisions"][iid]
    with state.edit(lib.paths.state_file) as st:
        st.data["rejected"] = {iid: ["xjj_OVvVQFc"]}
    once_more = review.decide_one(lib, index, iid, "url", link=link)
    assert (once_more.changed, once_more.warning) == (True, None)
    assert state_json(lib)["rejected"] == {}
    assert state_json(lib)["decisions"][iid] == decided
    assert review.decide_one(lib, index, iid, "url", link=link).changed is False


def test_a_low_link_that_is_the_decision_comes_off_the_rejected_list_too(
    lib: Library, index: Index, stuck: str, tmp_path: Path
) -> None:
    """The same leftover for a link that scores low: rejected, pasted again and accepted
    by an older engine, so it is the decision and still on the rejected list."""
    row_decision(lib, index, tmp_path, stuck, "url", LINK + FRANTI)
    row_decision(lib, index, tmp_path, stuck, "accept")
    decided = state_json(lib)["decisions"][stuck]
    with state.edit(lib.paths.state_file) as st:
        st.data["rejected"] = {stuck: [FRANTI]}
    again = review.decide_one(lib, index, stuck, "url", link=LINK + FRANTI)
    assert (again.changed, again.warning) == (True, None)
    assert state_json(lib)["rejected"] == {}
    assert state_json(lib)["decisions"][stuck] == decided
    item = index.item(stuck)
    assert item is not None
    assert (item["state"], item["reasons_json"]) == ("matched_user", [])
    assert marked(index, stuck) == []
    assert FRANTI in order(index, stuck)


def test_a_pasted_link_is_scored_with_the_names_the_owner_confirmed(
    lib: Library, index: Index, stuck: str, tmp_path: Path
) -> None:
    """The matcher scores a candidate knowing the artist names the owner confirmed
    (`aliases`). A pasted link is scored the same way, so it isn't called low when the
    matcher itself would score it above 0.60."""
    item = index.item(stuck)
    track = youtube.get_track(FRANTI)
    assert item is not None and track is not None
    assert match.assess(match.rip_of(item), track).score < match.REVIEW_SCORE

    review.confirm_alias(lib, index, "Kygo", "Michael Franti & Spearhead")
    aliases = state.aliases(lib.load_state().data)
    as_the_matcher = match.assess(match.rip_of(item, aliases), track).score
    assert as_the_matcher >= match.REVIEW_SCORE

    result = row_decision(lib, index, tmp_path, stuck, "url", LINK + FRANTI)
    assert (result.applied, result.warnings) == ({"url": 1}, [])
    entry = state_json(lib)["decisions"][stuck]
    assert (entry["video_id"], entry["score"]) == (FRANTI, round(as_the_matcher, 3))
    assert index.item(stuck)["state"] == "matched_user"  # type: ignore[index]
    stored = next(c for c in index.candidates(stuck) if c["video_id"] == FRANTI)
    assert stored["score"] == as_the_matcher
    assert marked(index, stuck) == []


def test_the_matcher_leaves_an_item_alone_while_its_pasted_link_waits(
    lib: Library, index: Index, stuck: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A second item with no pasted link: the matcher treats it as it always did.
    plain = add_item(index, "Band - Alpha", state="review", reasons=["version_mismatch"])
    add_candidates(index, plain, [candidate("Alp1xxxxxxx", "Alpha", ("Band",), 205)])
    review.decide_one(lib, index, stuck, "url", link=LINK + FRANTI)
    waiting = (index.item(stuck), index.candidates(stuck))
    assert order(index, stuck) == [FRANTI, *BY_SCORE]

    # `match --recheck`: the other item is classified again; this one isn't touched.
    checked = match.recheck(lib, index)
    assert (checked.items, checked.waiting) == (1, 1)
    assert checked.changed == {"review → review": 1}
    assert index.item(plain)["reasons_json"] == ["duration_mismatch"]  # type: ignore[index]
    assert (index.item(stuck), index.candidates(stuck)) == waiting

    # `match --rescan`: nothing is searched for it, and nothing of it changes.
    found = youtube.get_track(FRANTI)
    assert found is not None
    asked: list[str] = []

    def search(query: str, **kw: Any) -> list[youtube.Candidate]:
        asked.append(query)
        if "Bomb" in query:
            return [found, candidate("Bom7xxxxxxx", "Bomb the World", ("Kygo",), 240)]
        return [candidate("Alp9xxxxxxx", "Alpha", ("Band",), 230)]

    monkeypatch.setattr(youtube, "search_songs", search)
    result = match.run(lib, index, rescan=True, rng=random.Random(1))
    assert (result.items, result.waiting) == (1, 1)
    assert asked and not any("Bomb" in query for query in asked)
    assert order(index, plain) == ["Alp9xxxxxxx"]
    assert (index.item(stuck), index.candidates(stuck)) == waiting
    assert marked(index, stuck) == [FRANTI]

    # The owner answers "not this one": the link's turn is over. The matcher takes the
    # item again and stores a fresh list, in score order, with no mark on anything.
    review.decide_one(lib, index, stuck, "reject", video_id=FRANTI)
    result = match.run(lib, index, rescan=True, rng=random.Random(1))
    assert (result.items, result.waiting) == (2, 0)
    assert any("Bomb" in query for query in asked)
    assert order(index, stuck) == ["Bom7xxxxxxx"]  # the rejected link is never proposed
    assert marked(index, stuck) == []
    assert match.recheck(lib, index).waiting == 0


def test_confirming_an_artists_other_name_keeps_the_other_songs_pasted_links(
    lib: Library, index: Index, stuck: str
) -> None:
    """The review page asks about the artist's name right after a pasted link is used,
    and saying yes re-checks that artist's other songs: their pasted links stay."""
    other = add_item(index, "Kygo - Daddy Issues R", state="review", seconds=261,
                     reasons=["version_mismatch"])  # fmt: skip
    add_candidates(index, other, [
        candidate("Dad1xxxxxxx", "Daddy Issues", ("Kygo",), 261),
        candidate("Dad2xxxxxxx", "Daddy Issues (Live)", ("Kygo",), 300),
        candidate("Dad3xxxxxxx", "Daddy Issues", ("Kygo",), 271),
    ])  # fmt: skip
    review.decide_one(lib, index, stuck, "url", link=LINK + FRANTI)
    pasted = review.decide_one(lib, index, other, "url", link=LINK + DADDY)
    assert "below 0.60" in (pasted.warning or "")
    before = (index.item(other), index.candidates(other))
    assert marked(index, other) == [DADDY] and order(index, other)[0] == DADDY

    used = review.decide_one(lib, index, stuck, "use", video_id=FRANTI)
    assert used.alias_offer == {"from": "Kygo", "to": "Michael Franti & Spearhead", "others": 1}
    confirmed = review.confirm_alias(lib, index, "Kygo", "Michael Franti & Spearhead")
    assert (confirmed.items, confirmed.waiting, confirmed.changed) == (0, 1, {})
    assert confirmed.to_dict()["waiting"] == 1  # what the page is told
    assert (index.item(other), index.candidates(other)) == before
    # And it can still be taken.
    review.decide_one(lib, index, other, "use", video_id=DADDY)
    assert state_json(lib)["decisions"][other]["video_id"] == DADDY


def test_a_pasted_link_the_fingerprint_gate_turned_down_isnt_waiting(
    lib: Library, index: Index, stuck: str
) -> None:
    """A video the gate found `different` is never proposed for the rip again (step 09b),
    so its mark doesn't hold the matcher off."""
    review.decide_one(lib, index, stuck, "url", link=LINK + FRANTI)
    with state.edit(lib.paths.state_file) as st:
        st.data.setdefault("gate", {})[stuck] = {
            FRANTI: {"verdict": "different", "ber": 0.45, "why": "another recording",
                     "checked_at": "2026-10-04T00:00:00Z"}}  # fmt: skip
    checked = match.recheck(lib, index)
    assert (checked.items, checked.waiting) == (1, 0)
    assert order(index, stuck) == BY_SCORE
    assert marked(index, stuck) == []
    item = index.item(stuck)
    assert item is not None
    assert (item["state"], item["reasons_json"]) == ("review", ["version_mismatch"])


def test_a_rebuild_leaves_no_mark_behind(
    lib: Library, index: Index, samples: dict[str, Path], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    rips = tmp_path / "Rips"
    place(samples["mp3"], rips / "Kygo - Bomb the World R.mp3")
    index.remove_source("s_000000000001")
    scan.add_source(lib, index, rips)
    scan.scan(lib, index)
    found = youtube.get_track(FRANTI)
    assert found is not None

    def search(query: str, **kw: Any) -> list[youtube.Candidate]:
        return [candidate("Bom1xxxxxxx", "Bomb the World", ("Kygo",), 3),
                candidate("Bom3xxxxxxx", "Bomb the World", ("Kygo",), 13), found]  # fmt: skip

    monkeypatch.setattr(youtube, "search_songs", search)
    match.run(lib, index, rng=random.Random(1))
    (item,) = index.items()
    by_score = ["Bom1xxxxxxx", "Bom3xxxxxxx", FRANTI]
    assert order(index, item["id"]) == by_score
    review.decide_one(lib, index, item["id"], "url", link=LINK + FRANTI)
    assert order(index, item["id"]) == [FRANTI, "Bom1xxxxxxx", "Bom3xxxxxxx"]

    scan.rebuild(lib, index)  # the candidates go, and the mark with them
    assert index.all_candidates() == {}
    assert index.item(item["id"])["state"] == "new"  # type: ignore[index]
    match.run(lib, index, rng=random.Random(1))
    assert order(index, item["id"]) == by_score
    assert marked(index, item["id"]) == []


# What `review export --include-auto` wrote for the `items` fixture (and one AUTO item)
# before a pasted link was put first: made by the unchanged code, 2026-10-04.
AS_BEFORE = [
    ["i_7a3812336f878b1f", "Band - Alpha.mp3", "3:20", "Band", "Alpha", "", "duration_mismatch",
     "Alpha", "Band", "", "3:25", "0.977", "https://music.youtube.com/watch?v=Alp1xxxxxxx",
     "Alpha (Live)", "Band", "live", "3:20", "0.800",
     "https://music.youtube.com/watch?v=Alp2xxxxxxx",
     "Alpha", "Other Band", "", "3:50", "0.750", "https://music.youtube.com/watch?v=Alp3xxxxxxx",
     "", "", "", "", "", "", ""],
    ["i_5ee1bdefb7373833", "Band - Bravo.mp3", "3:20", "Band", "Bravo", "", "duration_mismatch",
     "Bravo", "Band", "", "3:25", "0.977", "https://music.youtube.com/watch?v=Bra1xxxxxxx",
     "Bravo (Live)", "Band", "live", "3:20", "0.800",
     "https://music.youtube.com/watch?v=Bra2xxxxxxx",
     "Bravo", "Other Band", "", "3:50", "0.750", "https://music.youtube.com/watch?v=Bra3xxxxxxx",
     "", "", "", "", "", "", ""],
    ["i_bdb4370b52b57de3", "Band - Charlie.mp3", "3:20", "Band", "Charlie", "",
     "duration_mismatch",
     "Charlie", "Band", "", "3:25", "0.977", "https://music.youtube.com/watch?v=Cha1xxxxxxx",
     "Charlie (Live)", "Band", "live", "3:20", "0.800",
     "https://music.youtube.com/watch?v=Cha2xxxxxxx",
     "Charlie", "Other Band", "", "3:50", "0.750",
     "https://music.youtube.com/watch?v=Cha3xxxxxxx",
     "", "", "", "", "", "", ""],
    ["i_4ed2f2b9f7178b19", "Band - Delta.mp3", "3:20", "Band", "Delta", "", "duration_mismatch",
     "Delta", "Band", "", "3:25", "0.977", "https://music.youtube.com/watch?v=Del1xxxxxxx",
     "Delta (Live)", "Band", "live", "3:20", "0.800",
     "https://music.youtube.com/watch?v=Del2xxxxxxx",
     "Delta", "Other Band", "", "3:50", "0.750", "https://music.youtube.com/watch?v=Del3xxxxxxx",
     "", "", "", "", "", "", ""],
    ["i_8028302b11b20932", "Band - Echo.mp3", "3:20", "Band", "Echo", "", "",
     *[""] * 18, "", "", "", "", "", "", ""],
    ["i_b3fe10ac7c5694ce", "Band - Foxtrot.mp3", "3:20", "Band", "Foxtrot", "", "",
     "Foxtrot", "Band", "", "3:20", "1.000", "https://music.youtube.com/watch?v=Fox1xxxxxxx",
     "Foxtrot", "Band", "", "3:21", "1.000", "https://music.youtube.com/watch?v=Fox2xxxxxxx",
     *[""] * 6, "", "", "", "", "", "", ""],
]  # fmt: skip


def test_items_with_no_pasted_link_export_exactly_as_before(
    lib: Library, index: Index, items: dict[str, str], stuck: str, tmp_path: Path
) -> None:
    fox = add_item(index, "Band - Foxtrot", state="matched_auto")
    add_candidates(index, fox, [candidate("Fox1xxxxxxx", "Foxtrot", ("Band",), 200),
                                candidate("Fox2xxxxxxx", "Foxtrot", ("Band",), 201)])  # fmt: skip
    review.decide_one(lib, index, stuck, "url", link=LINK + FRANTI)  # another item's link

    result = review.export(lib, index, tmp_path / "review.csv", include_auto=True)
    with open(result.path, encoding="utf-8-sig", newline="") as f:
        header, *rows = list(csv.reader(f))
    assert header == review.COLUMNS
    expected = [[row[0], str(tmp_path / "rips" / row[1]), *row[2:]] for row in AS_BEFORE]
    assert [row for row in rows if row[0] != stuck] == expected
    # The row with the pasted link comes after the other review rows (its candidate 1
    # scores lowest) and before the not-found ones.
    assert [row[0] for row in rows].index(stuck) == 4


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


def test_art_url_for_an_only_copy_song(
    lib: Library, index: Index, items: dict[str, str], tmp_path: Path
) -> None:
    rows = decide(export_rows(lib, index, tmp_path), Echo="only_copy")
    link = "https://soundcloud.com/band/echo"
    next(r for r in rows if r["parsed_title"] == "Echo").update(art_url=link)
    review.import_csv(lib, index, write_rows(tmp_path / "edited.csv", rows))
    assert state_json(lib)["decisions"][items["Echo"]]["art_url"] == link


@pytest.mark.parametrize(
    ("decision", "art_url", "problem"),
    [
        ("only_copy", "http://example.com/a.jpg", "isn't an https:// link"),
        ("accept", "https://example.com/a.jpg", "art_url is for only_copy rows"),
    ],
)
def test_art_url_problems(
    lib: Library,
    index: Index,
    items: dict[str, str],
    tmp_path: Path,
    decision: str,
    art_url: str,
    problem: str,
) -> None:
    rows = decide(export_rows(lib, index, tmp_path), Alpha=decision)
    next(r for r in rows if r["parsed_title"] == "Alpha").update(art_url=art_url)
    with pytest.raises(review.ReviewImportError, match=problem):
        review.import_csv(lib, index, write_rows(tmp_path / "edited.csv", rows))
