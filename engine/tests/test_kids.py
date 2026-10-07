"""A child's profile: only clean songs (the owner, 2026-10-07)."""

from __future__ import annotations

from typing import Any

from musicorg import kids


def song(title: str, explicit: bool | None, artist: str = "Band") -> dict[str, Any]:
    return {"video_id": title, "title": title, "artists": [artist], "is_explicit": explicit}


def test_of_a_clean_and_an_explicit_version_only_the_clean_is_shown() -> None:
    found = [song("Night Out", True), song("Night Out (Clean)", False), song("Sunny", False)]
    shown, note = kids.clean_only(found, allow_explicit=False)
    assert [s["title"] for s in shown] == ["Night Out (Clean)", "Sunny"]
    assert note is None  # nothing is missing: the clean one is there


def test_the_same_title_by_someone_else_is_another_song() -> None:
    found = [song("Night Out", True, "Band"), song("Night Out", False, "Other")]
    shown, note = kids.clean_only(found, allow_explicit=False)
    assert [s["artists"] for s in shown] == [["Other"]]
    assert note == "No clean version was found for: Night Out (Band)."


def test_a_song_with_no_clean_version_is_left_out_and_named() -> None:
    found = [song("Loud", True), song("Loud", True), song("Sunny", False)]
    shown, note = kids.clean_only(found, allow_explicit=False)
    assert [s["title"] for s in shown] == ["Sunny"]
    assert note == "No clean version was found for: Loud (Band)."


def test_the_switch_lets_it_through_as_it_is_and_marked() -> None:
    found = [song("Loud", True), song("Night Out", True), song("Night Out (Clean)", False)]
    shown, note = kids.clean_only(found, allow_explicit=True)
    assert [(s["title"], s.get("only_explicit")) for s in shown] == [
        ("Loud", True),
        ("Night Out (Clean)", None),  # a clean version was found, so the explicit one stays out
    ]
    assert note is None


def test_a_song_with_no_mark_is_not_assumed_clean_and_not_called_explicit() -> None:
    shown, note = kids.clean_only([song("Who Knows", None)], allow_explicit=False)
    assert shown == []
    assert note == "1 song was left out because it isn't marked clean or explicit."
    shown, note = kids.clean_only([song("Who Knows", None)], allow_explicit=True)
    assert [s["only_explicit"] for s in shown] == [True]


def test_a_long_list_of_names_is_cut_short() -> None:
    found = [song(f"Loud {n}", True) for n in range(8)]
    _, note = kids.clean_only(found, allow_explicit=False)
    assert note is not None and note.endswith("Loud 4 (Band); and 3 more.")


def test_an_imported_song_found_only_explicit_counts_as_not_found() -> None:
    found = {"state": "found", "candidate": song("Loud", True)}
    assert kids.clean_found(found, allow_explicit=False) == {
        "state": "not_found",
        "why": "No clean version of this song was found.",
    }
    allowed = kids.clean_found(found, allow_explicit=True)
    assert allowed["state"] == "found" and allowed["candidate"]["only_explicit"] is True
    clean = {"state": "unsure", "candidate": song("Sunny", False)}
    assert kids.clean_found(clean, allow_explicit=False) is clean
    owned = {"state": "owned", "track_id": "abc"}
    assert kids.clean_found(owned, allow_explicit=False) is owned
