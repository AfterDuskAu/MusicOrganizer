"""normalize: artist, title and version parsing from rip names and tags.

tests/data/filenames.tsv holds the file-name cases: input → artist, title, version
tokens (separated by "; ") and confidence band (high ≥ 0.8, mid, low < 0.5).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from musicorg import normalize
from musicorg.normalize import Parsed, best_parse, compare_key, parse_filename, parse_tags
from musicorg.tags import TrackTags

TABLE = Path(__file__).parent / "data" / "filenames.tsv"


def cases() -> list[dict[str, str]]:
    with open(TABLE, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE))


def test_the_table_has_enough_cases() -> None:
    rows = cases()
    assert len(rows) >= 60
    assert len({row["input"] for row in rows}) == len(rows)
    notes = " ".join(row["what it shows"] for row in rows)
    for kind in ("remixer", "feat. in the title", "feat. in the artist", "inside a version",
                 "Japanese", "reversed", "bootleg", "slowed + reverb", "Monstercat"):  # fmt: skip
        assert kind in notes, kind


@pytest.mark.parametrize("row", cases(), ids=lambda row: row["input"])
def test_file_names(row: dict[str, str]) -> None:
    parsed = parse_filename(row["input"])
    versions = tuple(v for v in row["versions"].split("; ") if v)
    got = (parsed.artist or "", parsed.title or "", parsed.version_tokens, parsed.band)
    assert got == (row["artist"], row["title"], versions, row["band"]), row["what it shows"]


# ---- details the table doesn't show ----------------------------------------------------


def test_featured_artists_and_the_credit() -> None:
    parsed = parse_filename("Pegboard Nerds - Hero (feat. Elizaveta) [Monstercat Release]")
    assert parsed.artists == ("Pegboard Nerds", "Elizaveta")
    assert parsed.junk_removed == ("monstercat release",)
    duo = parse_filename("Simon & Garfunkel - The Sound of Silence")
    assert duo.artists == ("Simon", "Garfunkel")
    assert duo.credit == "Simon & Garfunkel"  # for matching a duo's name as a whole
    many = parse_filename("A feat. B - Title (feat. C & D)")
    assert many.artists == ("A", "B", "C", "D")
    assert many.credit == "A"


def test_the_remixer_named_as_the_artist_is_uncertain() -> None:
    parsed = parse_filename("Adventure Club - Crave You (Adventure Club Remix)")
    assert parsed.artist_uncertain
    assert not parse_filename(
        "Flight Facilities - Crave You (Adventure Club Remix)"
    ).artist_uncertain


def test_junk_is_recorded() -> None:
    parsed = parse_filename("y2mate.com - Artist - Title (Official Video) [HD] 🔥")
    assert set(parsed.junk_removed) == {"download site", "official video", "hd", "emoji"}


def test_to_dict() -> None:
    data = parse_filename("Artist - Title (X Remix)").to_dict()
    assert data["version_tokens"] == ["remix:x"]
    assert data["confidence"] == 0.9
    assert data["source"] == "filename"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Beyoncé", "Beyonce"),
        ("Simon & Garfunkel", "simon and garfunkel"),
        ("Florence + the Machine", "Florence the Machine"),
        ("Don't Stop Me Now", "Dont Stop Me Now"),
        ("Jay-Z", "Jay Z"),
        ("ＡＢＣ", "abc"),  # full-width
        ("  Too   many   spaces ", "too many spaces"),
        ("P!nk", "P nk"),
    ],
)
def test_compare_key_matches(a: str, b: str) -> None:
    assert compare_key(a) == compare_key(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("夜に駆ける", "夜に駈ける"),  # different characters stay different
        ("が", "か"),  # Japanese marks are kept, unlike Latin accents
        ("Closer", "Closer Tribute"),
    ],
)
def test_compare_key_keeps_differences(a: str, b: str) -> None:
    assert compare_key(a) != compare_key(b)


def test_compare_key_keeps_other_scripts() -> None:
    assert compare_key("米津玄師 — Lemon!") == "米津玄師 lemon"
    assert compare_key(None) == ""


def test_render_versions() -> None:
    tokens = ("remix:adventure club", "live:wembley", "slowed")
    assert normalize.render_versions(tokens) == "adventure club remix live wembley slowed"


# ---- tags ------------------------------------------------------------------------------


def test_real_tags_win() -> None:
    parsed = best_parse("something else", TrackTags(title="Crave You", artist="Flight Facilities"))
    assert (parsed.artist, parsed.title, parsed.source) == (
        "Flight Facilities",
        "Crave You",
        "tags",
    )
    assert parsed.confidence >= 0.9


def test_versions_in_the_name_are_kept_when_the_tags_agree() -> None:
    parsed = best_parse(
        "Flight Facilities - Crave You (Adventure Club Remix)",
        TrackTags(title="Crave You", artist="Flight Facilities"),
    )
    assert parsed.version_tokens == ("remix:adventure club",)
    assert parsed.source == "tags"


def test_versions_in_tags_are_parsed() -> None:
    parsed = parse_tags(
        TrackTags(title="Crave You (Adventure Club Remix)", artist="Flight Facilities")
    )
    assert (parsed.title, parsed.version_tokens) == ("Crave You", ("remix:adventure club",))


@pytest.mark.parametrize("channel", ["Trap Nation", "Unknown Artist", "https://y2mate.com"])
def test_a_channel_in_the_artist_tag_means_the_title_is_a_video_title(channel: str) -> None:
    parsed = parse_tags(
        TrackTags(title="Flight Facilities - Crave You (Official Video)", artist=channel)
    )
    assert (parsed.artist, parsed.title) == ("Flight Facilities", "Crave You")
    assert parsed.source == "tags"


def test_vevo_and_topic_channels_name_the_artist() -> None:
    vevo = parse_tags(TrackTags(title="Hotline Bling (Official Video)", artist="DrakeVEVO"))
    topic = parse_tags(TrackTags(title="Hotline Bling", artist="Drake - Topic"))
    for parsed in (vevo, topic):
        assert (parsed.artist, parsed.title, parsed.band) == ("Drake", "Hotline Bling", "mid")


def test_the_artist_repeated_in_the_title() -> None:
    parsed = parse_tags(TrackTags(title="Drake - Hotline Bling", artist="Drake"))
    assert (parsed.artist, parsed.title) == ("Drake", "Hotline Bling")


def test_no_tags_falls_back_to_the_name() -> None:
    for tags in (None, TrackTags(), TrackTags(artist="Drake")):
        parsed = best_parse("Drake - Hotline Bling (Official Video)", tags)
        assert (parsed.artist, parsed.title, parsed.source) == (
            "Drake",
            "Hotline Bling",
            "filename",
        )


def test_weak_tags_lose_to_a_good_name() -> None:
    parsed = best_parse("Drake - Hotline Bling", TrackTags(title="Track 01"))
    assert parsed == parse_filename("Drake - Hotline Bling")


def test_bands() -> None:
    assert [normalize.confidence_band(c) for c in (0.95, 0.8, 0.79, 0.5, 0.49, 0.0)] == [
        "high", "high", "mid", "mid", "low", "low"]  # fmt: skip
    assert Parsed(None, None).band == "low"
