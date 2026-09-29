"""match: queries, scoring, classification and `run`. The hand-labelled cases against
real recorded results are in test_match_harness.py."""

from __future__ import annotations

import csv
import random
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from musicorg import match, state, youtube
from musicorg.errors import YouTubePausedError
from musicorg.index import Index, item_id, open_index
from musicorg.library import Library
from musicorg.match import Rip
from musicorg.normalize import Parsed, parse_filename
from musicorg.youtube import OFFICIAL_AUDIO, Candidate

MV = "MUSIC_VIDEO_TYPE_OMV"


def cand(
    video_id: str = "v1",
    title: str = "Song",
    artists: tuple[str, ...] = ("Artist",),
    duration: int | None = 200,
    explicit: bool | None = False,
    video_type: str = OFFICIAL_AUDIO,
) -> Candidate:
    return Candidate(video_id, title, artists, "Album", "MPREb_1", duration, explicit, video_type)


def rip(name: str = "Artist - Song", duration: float | None = 200.0, **kw: Any) -> Rip:
    return Rip(parse_filename(name), duration, **kw)


# ---- queries ---------------------------------------------------------------------------


def test_queries() -> None:
    assert match.queries(parse_filename("Drake - Hotline Bling (Official Video)")) == [
        "Drake Hotline Bling"
    ]
    remix = parse_filename("Flight Facilities - Crave You (Adventure Club Remix)")
    assert match.queries(remix) == [
        "Flight Facilities Crave You",
        "Flight Facilities Crave You adventure club remix",
    ]
    # A low-confidence parse also tries the title alone and the reversed order.
    reversed_ = parse_filename("Blinding Lights (Official Video) - The Weeknd")
    assert match.queries(reversed_) == [
        "The Weeknd Blinding Lights", "Blinding Lights", "Blinding Lights The Weeknd"]  # fmt: skip
    assert match.queries(parse_filename("Hello")) == ["Hello"]
    assert match.queries(Parsed(None, None)) == []


def test_at_most_three_queries() -> None:
    parsed = parse_filename("Crave You (Adventure Club Remix) - Flight Facilities")
    assert len(match.queries(parsed)) == 3


def test_soft_versions_stay_out_of_queries() -> None:
    parsed = parse_filename("Artist - Song (Explicit) (2011 Remaster)")
    assert match.queries(parsed) == ["Artist Song"]


# ---- scoring ---------------------------------------------------------------------------


def test_a_perfect_match() -> None:
    value, reasons = match.score(parse_filename("Artist - Song"), 200.0, cand())
    assert value == 1.0
    assert reasons == ["artist exact", "title exact", "version match", "duration Δ0s"]


def test_the_weights() -> None:
    # Duration: full marks up to 2 s, nothing from 15 s, linear between.
    assert match.score(parse_filename("Artist - Song"), 208.5, cand())[0] == pytest.approx(0.95)
    assert match.score(parse_filename("Artist - Song"), 230.0, cand())[0] == pytest.approx(0.9)
    # Version (0.20) is all or nothing.
    value, reasons = match.score(parse_filename("Artist - Song (X Remix)"), 200.0, cand())
    assert value == pytest.approx(0.8)
    assert "version: rip x remix, this original" in reasons
    assert "version_mismatch" in reasons
    # Artist and title are token_sort_ratio, 0.35 each.
    value, _ = match.score(parse_filename("Someone Else - Song"), 200.0, cand())
    assert 0.65 < value < 0.8


def test_a_tribute_is_not_the_original() -> None:
    """token_set_ratio would score "Closer" against "Closer (Tribute to …)" as 100."""
    tribute = cand(title="Closer (Tribute to The Chainsmokers & Halsey)", artists=("Hit Crew",))
    scored = match.assess(rip("The Chainsmokers - Closer"), tribute)
    assert not scored.auto
    assert {"artist_mismatch", "title_fuzzy"} <= set(scored.codes)
    assert scored.score < match.REVIEW_SCORE


def test_soft_versions_dont_count() -> None:
    scored = match.assess(rip("Artist - Song (2011 Remaster) (Explicit)"), cand(explicit=True))
    assert scored.auto


def test_versions_in_the_candidate_title() -> None:
    live = match.assess(rip("Artist - Song"), cand(title="Song - Live at Wembley"))
    assert not live.auto and "version_mismatch" in live.codes
    remix = match.assess(rip("Artist - Song (X Remix)"), cand(title="Song (X Remix)"))
    assert remix.auto and remix.version_tokens == ("remix:x",)
    featured = match.assess(rip("Artist - Song"), cand(title="Song (feat. Guest)"))
    assert featured.auto


def test_only_official_audio_is_auto() -> None:
    scored = match.assess(rip(), cand(video_type=MV))
    assert not scored.auto and "not_official_audio" in scored.codes


def test_the_duration_limit() -> None:
    assert match.assess(rip(duration=202.0), cand()).auto
    late = match.assess(rip(duration=202.5), cand())
    assert not late.auto and "duration_mismatch" in late.codes
    unknown = match.assess(rip(duration=None), cand())
    assert not unknown.auto and "duration unknown" in unknown.reasons


def test_artist_names_ignore_spaces_and_accents() -> None:
    assert match.assess(rip("Cold Play - Song"), cand(artists=("Coldplay",))).auto
    assert match.assess(rip("Jay-Z - Song"), cand(artists=("JAŸ-Z",))).auto
    assert match.assess(rip("Simon & Garfunkel - Song"), cand(artists=("Simon & Garfunkel",))).auto


def test_the_main_artist_must_match_for_auto() -> None:
    # Rihanna is featured on the candidate, but the rip's main artist is someone else.
    scored = match.assess(
        rip("Eminem & Rihanna - Run This Town"),
        cand(title="Run This Town (feat. Rihanna & Kanye West)", artists=("JAŸ-Z",)),
    )
    assert not scored.auto and "artist_mismatch" in scored.codes
    assert scored.score >= 0.9  # but the pairing finds her, so it ranks first


def test_a_title_only_rip_can_rank_but_never_auto() -> None:
    scored = match.assess(rip("Mase-Feel So Good"), cand(title="Feel so Good", artists=("Mase",)))
    assert scored.score == 1.0
    assert not scored.auto


# ---- clean and explicit ----------------------------------------------------------------


def test_an_explicit_rip_needs_an_explicit_match() -> None:
    explicit_rip = rip(explicit_tag=True)
    assert match.assess(explicit_rip, cand(explicit=True)).auto
    clean = match.assess(explicit_rip, cand(explicit=False))
    assert not clean.auto and "rip is explicit, this is clean" in clean.reasons
    assert not match.assess(explicit_rip, cand(explicit=None)).auto
    marked = rip("Artist - Song (Clean)")
    assert not match.assess(marked, cand(explicit=True)).auto
    assert match.assess(marked, cand(explicit=False)).auto


def test_an_explicit_clean_pair_prefers_explicit() -> None:
    pair = [cand("clean", explicit=False), cand("dirty", explicit=True)]
    outcome = match.classify(rip(), pair)
    assert outcome.state == "matched_auto"
    assert outcome.top[0].candidate.video_id == "dirty"
    assert "explicit/clean pair: chose explicit" in outcome.top[0].reasons
    clean = match.classify(rip(), pair, prefer_explicit=False)
    assert clean.top[0].candidate.video_id == "clean"
    assert "explicit/clean pair: chose clean" in clean.top[0].reasons


def test_the_pair_rule_never_falls_back_to_the_other_version() -> None:
    # The explicit version is 5 s off, so it can't be AUTO; the clean one isn't taken
    # instead.
    pair = [cand("clean", explicit=False), cand("dirty", explicit=True, duration=205)]
    outcome = match.classify(rip(), pair)
    assert outcome.state == "review"
    assert outcome.top[0].candidate.video_id == "dirty"
    assert "duration_mismatch" in outcome.reasons


# ---- classifying -----------------------------------------------------------------------


def test_classify_states() -> None:
    assert match.classify(rip(), []).state == "not_found"
    auto = match.classify(rip(), [cand("a", duration=230), cand("b")])
    assert (auto.state, auto.top[0].candidate.video_id, auto.reasons) == ("matched_auto", "b", [])
    review = match.classify(rip(), [cand(duration=230)])
    assert (review.state, review.reasons) == ("review", ["duration_mismatch"])
    far = match.classify(rip(), [cand(title="Other", artists=("Nobody",), duration=100)])
    assert far.state == "not_found" and far.top


def test_review_keeps_the_top_three_best_first() -> None:
    options = [cand(str(n), duration=200 + 3 + n) for n in range(5)]
    outcome = match.classify(rip(), options)
    assert [s.candidate.video_id for s in outcome.top] == ["0", "1", "2"]


def test_low_parse_confidence_is_a_reason() -> None:
    outcome = match.classify(rip("Artist Song"), [cand(title="Song")])  # title only: 0.3
    assert outcome.state == "review"
    assert outcome.reasons == ["artist_mismatch", "low_parse_confidence"]


def test_rejected_candidates_are_never_proposed() -> None:
    outcome = match.classify(rip(), [cand("a"), cand("b", duration=230)], rejected={"a"})
    assert outcome.state == "review"
    assert [s.candidate.video_id for s in outcome.top] == ["b"]


def test_match_item_stops_at_an_auto_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    searched: list[str] = []

    def search(query: str, **kw: Any) -> list[Candidate]:
        searched.append(query)
        return [cand(title="Song (X Remix)")]

    monkeypatch.setattr(youtube, "search_songs", search)
    outcome = match.match_item(rip("Artist - Song (X Remix)"), cache=None)
    assert outcome.state == "matched_auto"
    assert searched == ["Artist Song"]


# ---- run -------------------------------------------------------------------------------

SOURCE = "s_000000000001"


def add_items(index: Index, names: dict[str, str], source_path: Path) -> list[str]:
    """Items named "<artist> - <title>", each 200 s long, in the given states, from a
    source folder beside the library."""
    index.put_source(SOURCE, str(source_path / "rips"), "2026-09-29T00:00:00Z")
    rows = []
    for name, item_state in names.items():
        parsed = parse_filename(name)
        rows.append({
            "id": item_id(SOURCE, f"{name}.mp3"), "source_id": SOURCE, "rel_path": f"{name}.mp3",
            "size": 1, "mtime_ns": 1, "ext": ".mp3", "duration_s": 200.0,
            "raw_tags_json": {"tags": {}}, "parsed_artist": parsed.artist,
            "parsed_title": parsed.title, "parsed_version_json": list(parsed.version_tokens),
            "parse_confidence": parsed.confidence, "parsed_json": parsed.to_dict(),
            "flags_json": [], "state": item_state, "scanned_at": "2026-09-29T00:00:00Z",
        })  # fmt: skip
    index.put_items(rows)
    return [row["id"] for row in rows]


@pytest.fixture
def index(lib: Library) -> Iterator[Index]:
    with open_index(lib.paths, write=True) as opened:
        yield opened


class FakeSearch:
    """Answers every search with a matching official track, or a poorer one for titles
    starting with "Near"; titles starting with "Nothing" find nothing."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []
        self.fail_on: str | None = None

    def __call__(self, query: str, *, cache: Any = None, refresh: bool = False) -> list[Candidate]:
        self.calls.append((query, refresh))
        if self.fail_on and self.fail_on in query:
            raise youtube.paused_error(youtube.limiter()._now())
        artist, _, title = query.partition(" ")
        if title.startswith("Nothing"):
            return []
        duration = 230 if title.startswith("Near") else 200
        return [cand(f"vid-{title}", title, (artist,), duration)]


@pytest.fixture
def fake_search(monkeypatch: pytest.MonkeyPatch) -> FakeSearch:
    fake = FakeSearch()
    monkeypatch.setattr(youtube, "search_songs", fake)
    return fake


def test_run(lib: Library, index: Index, fake_search: FakeSearch, tmp_path: Path) -> None:
    ids = add_items(index, {"A - One": "new", "B - Near": "new", "C - Nothing": "new",
                            "D - Done": "matched_user"}, tmp_path)  # fmt: skip
    result = match.run(lib, index, rng=random.Random(1))
    assert (result.items, result.matched_auto, result.review, result.not_found) == (3, 1, 1, 1)
    states = {i: index.item(i)["state"] for i in ids}  # type: ignore[index]
    assert list(states.values()) == ["matched_auto", "review", "not_found", "matched_user"]
    assert index.item(ids[1])["reasons_json"] == ["duration_mismatch"]  # type: ignore[index]

    best = index.candidates(ids[0])[0]
    assert best["video_id"] == "vid-One"
    payload = best["payload"]
    for key in ("candidate_id", "video_id", "title", "artists", "album", "album_browse_id",
                "duration_s", "is_official_audio", "is_explicit", "version_tokens", "score",
                "reasons"):  # fmt: skip
        assert key in payload, key  # the Candidate shape in docs/ENGINE_API.md
    assert payload["candidate_id"] == best["id"] and best["id"].startswith("c_")
    assert index.candidates(ids[2]) == []


def test_run_writes_the_listening_sample(
    lib: Library, index: Index, fake_search: FakeSearch, tmp_path: Path
) -> None:
    names = {f"Artist{n} - Song{n}": "new" for n in range(25)}
    add_items(index, names, tmp_path)
    result = match.run(lib, index, rng=random.Random(1))
    assert result.sample == lib.paths.reports / "auto-sample.csv"
    assert result.sample_size == 20
    with open(result.sample, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 20
    assert rows[0]["link"].startswith("https://music.youtube.com/watch?v=vid-Song")
    assert rows[0]["rip"].startswith(str(tmp_path / "rips"))
    # Never overwritten: the next run's sample gets its own name.
    again = match.run(lib, index, rng=random.Random(2))
    assert again.items == 0
    assert again.sample == lib.paths.reports / "auto-sample (2).csv"


def test_no_sample_without_auto_matches(
    lib: Library, index: Index, fake_search: FakeSearch, tmp_path: Path
) -> None:
    add_items(index, {"A - Nothing": "new"}, tmp_path)
    result = match.run(lib, index)
    assert (result.sample, result.sample_size) == (None, 0)
    assert not lib.paths.reports.exists() or not any(lib.paths.reports.iterdir())


def test_limit(lib: Library, index: Index, fake_search: FakeSearch, tmp_path: Path) -> None:
    add_items(index, {"A - One": "new", "B - Two": "new", "C - Three": "new"}, tmp_path)
    result = match.run(lib, index, limit=2)
    assert (result.items, result.left) == (2, 1)
    assert index.counts_by_state() == {"matched_auto": 2, "new": 1}


def test_rescan(lib: Library, index: Index, fake_search: FakeSearch, tmp_path: Path) -> None:
    add_items(index, {"A - One": "review", "B - Two": "not_found", "C - Three": "matched_auto",
                      "D - Four": "new"}, tmp_path)  # fmt: skip
    assert match.run(lib, index).items == 1
    assert all(not refresh for _, refresh in fake_search.calls)
    result = match.run(lib, index, rescan=True)
    assert result.items == 2  # review and not_found; matched_auto stays
    assert all(refresh for _, refresh in fake_search.calls[1:])  # the cache is skipped


def test_an_interrupted_run_carries_on(
    lib: Library, index: Index, fake_search: FakeSearch, tmp_path: Path
) -> None:
    ids = add_items(index, {"A - One": "new", "B - Two": "new"}, tmp_path)
    fake_search.fail_on = "Two"
    with pytest.raises(YouTubePausedError):
        match.run(lib, index)
    assert index.item(ids[0])["state"] == "matched_auto"  # type: ignore[index]
    assert index.item(ids[1])["state"] == "new"  # type: ignore[index]
    fake_search.fail_on = None
    assert match.run(lib, index).items == 1
    assert index.counts_by_state() == {"matched_auto": 2}


def test_rejected_candidates_from_state_json(
    lib: Library, index: Index, fake_search: FakeSearch, tmp_path: Path
) -> None:
    ids = add_items(index, {"A - One": "new"}, tmp_path)
    with state.edit(lib.paths.state_file) as st:
        st.data["rejected"] = {ids[0]: ["vid-One"]}
    match.run(lib, index)
    assert index.item(ids[0])["state"] == "not_found"  # type: ignore[index]
    assert index.candidates(ids[0]) == []


def test_progress_with_an_estimate(
    lib: Library, index: Index, fake_search: FakeSearch, tmp_path: Path
) -> None:
    add_items(index, {"A - One": "new", "B - Two": "new"}, tmp_path)
    seen: list[tuple[int, int, float | None]] = []
    match.run(lib, index, progress=lambda done, total, left: seen.append((done, total, left)))
    assert [(d, t) for d, t, _ in seen] == [(1, 2), (2, 2)]
    assert seen[-1][2] == 0


def test_the_search_cache_is_the_index(lib: Library, index: Index) -> None:
    assert index.cached_search("k", max_age_days=30) is None
    index.put_search("k", [{"videoId": "x"}])
    assert index.cached_search("k", max_age_days=30) == [{"videoId": "x"}]
    assert index.conn is not None
    index.conn.execute("UPDATE search_cache SET fetched_at = '2020-01-01T00:00:00Z'")
    assert index.cached_search("k", max_age_days=30) is None


def test_rip_of_an_index_row() -> None:
    parsed = parse_filename("Artist - Song (Explicit)")
    row = {"parsed_json": parsed.to_dict(), "duration_s": 12.5,
           "raw_tags_json": {"tags": {"explicit": True}}}  # fmt: skip
    found = match.rip_of(row)
    assert (found.parsed, found.duration_s, found.explicit_tag) == (parsed, 12.5, True)
    assert found.says == "explicit"


def test_a_track_marked_clean_is_only_for_a_clean_rip() -> None:
    edited = cand(title="Song (Edited)", explicit=False)
    neutral = match.assess(rip(), edited)
    assert not neutral.auto
    assert "this is a clean version; the rip doesn't say clean" in neutral.reasons
    assert "version_mismatch" in neutral.codes
    assert not match.assess(rip(explicit_tag=True), edited).auto
    assert match.assess(rip("Artist - Song (Clean)"), edited).auto
    assert match.assess(rip("Artist - Song [Censored]"), cand(title="Song (Clean)")).auto
    # With prefer_explicit off, a rip that says neither may take it.
    assert match.assess(rip(), edited, prefer_explicit=False).auto


def test_the_owners_remix_mark_is_never_auto_without_a_remixer() -> None:
    # "R" means remix, but not whose: the right remix can rank first, never AUTO.
    options = [cand("original", duration=240), cand("remix", "Song (Filous Remix)")]
    outcome = match.classify(rip("Artist - Song R"), options)
    assert outcome.state == "review"
    assert outcome.top[0].candidate.video_id == "remix"
