"""The matcher's evaluation harness (step 06).

tests/data/match_cases.json holds hand-labelled cases: a parsed rip (from the owner's
scanned library, or made up where marked), its length, and the right answer: the
expected state and every videoId of the same recording (none when the right recording
isn't in the results). The searches are replayed from tests/fixtures/ytm/; record new
ones with scripts/record_ytm.py cases.

It fails on any false AUTO match (none allowed), or when fewer than 85% of the cases
have the right answer on top: the right recording first, or, when the results don't
hold it, no AUTO match.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from musicorg import match
from musicorg.normalize import Parsed

CASES = Path(__file__).parent / "data" / "match_cases.json"
TOP_1_NEEDED = 0.85


def cases() -> list[dict[str, Any]]:
    return json.loads(CASES.read_text(encoding="utf-8"))["cases"]


def outcomes() -> list[tuple[dict[str, Any], match.Outcome]]:
    results = []
    for case in cases():
        rip = match.Rip(Parsed.from_dict(case["parsed"]), case["duration_s"], case["explicit_tag"])
        results.append((case, match.match_item(rip, cache=None)))
    return results


def top_1_correct(case: dict[str, Any], outcome: match.Outcome) -> bool:
    expected = case["expected_video_ids"]
    if not expected:
        return outcome.state != "matched_auto"
    return bool(outcome.top) and outcome.top[0].candidate.video_id in expected


def describe(case: dict[str, Any], outcome: match.Outcome) -> str:
    top = outcome.top[0].candidate if outcome.top else None
    got = f"{top.video_id} {top.title!r} by {', '.join(top.artists)}" if top else "nothing"
    return (f"{case['id']}: got {outcome.state} ({got}); expected {case['expected_state']} "
            f"{case['expected_video_ids'] or '(no right recording listed)'}")  # fmt: skip


def test_the_cases_cover_what_the_brief_asks() -> None:
    all_cases = cases()
    assert len(all_cases) >= 30
    assert len({c["id"] for c in all_cases}) == len(all_cases)
    what = " ".join(c["what"] for c in all_cases)
    for kind in ("remix", "live", "explicit/clean", "cover", "tribute"):
        assert kind in what, kind
    states = {c["expected_state"] for c in all_cases}
    assert states == {"matched_auto", "review", "not_found"}


def test_no_false_auto_matches() -> None:
    wrong = [
        describe(case, outcome)
        for case, outcome in outcomes()
        if outcome.state == "matched_auto"
        and outcome.top[0].candidate.video_id not in case["expected_video_ids"]
    ]
    assert not wrong, "False AUTO matches:\n" + "\n".join(wrong)


def test_top_1_correctness() -> None:
    results = outcomes()
    misses = [
        describe(case, outcome) for case, outcome in results if not top_1_correct(case, outcome)
    ]
    rate = 1 - len(misses) / len(results)
    assert rate >= TOP_1_NEEDED, f"Top-1 {rate:.0%} of {len(results)}:\n" + "\n".join(misses)


def test_expected_states() -> None:
    """Not a brief criterion, but a regression guard: a rule change that moves a labelled
    case to another state should be a deliberate one (update the case's label)."""
    moved = [describe(case, outcome) for case, outcome in outcomes()
             if outcome.state != case["expected_state"]]  # fmt: skip
    assert not moved, "\n".join(moved)
