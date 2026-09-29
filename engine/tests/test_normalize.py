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


def test_an_artist_tag_and_a_title_only_name() -> None:
    # Apple Music shows such a file under its artist, with the file name as its name.
    parsed = best_parse("Two Rivers", TrackTags(artist="Hippie Sabotage"))
    assert (parsed.artist, parsed.title, parsed.band) == ("Hippie Sabotage", "Two Rivers", "high")
    assert "artist from tags" in parsed.notes
    repeated = best_parse("Portugal The Man Do You", TrackTags(artist="Portugal the Man"))
    assert (repeated.artist, repeated.title) == ("Portugal the Man", "Do You")
    featured = best_parse("Magic Stick feat. 50 Cent", TrackTags(artist="Lil' Kim"))
    assert featured.artists == ("Lil' Kim", "50 Cent")


def test_an_artist_tag_does_not_override_the_name() -> None:
    for artist in ("Unknown Artist", "Y2meta.app", "Trap Nation"):
        assert best_parse("Two Rivers", TrackTags(artist=artist)).artist is None
    parsed = best_parse("Drake - Hotline Bling", TrackTags(artist="Someone Else"))
    assert parsed == parse_filename("Drake - Hotline Bling")


def test_download_sites_in_tags() -> None:
    parsed = parse_tags(TrackTags(title="Y2meta.app - Drake - One Dance", artist="x2mate.com"))
    assert (parsed.artist, parsed.title) == ("Drake", "One Dance")
    entity = parse_tags(TrackTags(title="Can&#39t Stop", artist="Red Hot Chili Peppers"))
    assert entity.title == "Can't Stop"


def test_the_converter_video_id_is_noted() -> None:
    parsed = parse_filename("onlymp3.to - Jake Hill - Mine-Ab3dE_6hIjK-192k-1660198157171")
    assert (parsed.artist, parsed.title) == ("Jake Hill", "Mine")
    assert "video id Ab3dE_6hIjK" in parsed.notes
    assert "download site" in parsed.junk_removed


def test_weak_tags_lose_to_a_good_name() -> None:
    parsed = best_parse("Drake - Hotline Bling", TrackTags(title="Track 01"))
    assert parsed == parse_filename("Drake - Hotline Bling")


def test_bands() -> None:
    assert [normalize.confidence_band(c) for c in (0.95, 0.8, 0.79, 0.5, 0.49, 0.0)] == [
        "high", "high", "mid", "mid", "low", "low"]  # fmt: skip
    assert Parsed(None, None).band == "low"


# ---- titles from YouTube Music ---------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "clean", "versions"),
    [
        ("Crave You (feat. Giselle)", "Crave You", ()),
        ("Yesterday - Remastered 2009", "Yesterday", ("remaster:2009",)),
        ("Mr. Brightside - Jacques Lu Cont Remix", "Mr. Brightside", ("remix:jacques lu cont",)),
        ("Song - Radio Edit", "Song", ("radio edit",)),
        ("Love Story (Taylor's Version)", "Love Story (Taylor's Version)", ()),
        ('I Ain\'t Worried (From "Top Gun: Maverick")', "I Ain't Worried", ()),
        ("From Me To You", "From Me To You", ()),
        ("Love Story - Taylor's Version", "Love Story - Taylor's Version", ()),
    ],
)
def test_parse_title(title: str, clean: str, versions: tuple[str, ...]) -> None:
    parsed = normalize.parse_title(title)
    assert (parsed.title, parsed.version_tokens, parsed.artist) == (clean, versions, None)


def test_featured_artists_in_a_title() -> None:
    assert normalize.parse_title("Crave You (feat. Giselle)").artists == ("Giselle",)


def test_versions_after_a_dash_in_tags() -> None:
    parsed = parse_tags(TrackTags(title="Yesterday - Remastered 2009", artist="The Beatles"))
    assert (parsed.title, parsed.version_tokens) == ("Yesterday", ("remaster:2009",))


def test_from_dict_round_trip() -> None:
    parsed = parse_filename("A feat. B - Title (X Remix) [Official Video]")
    assert Parsed.from_dict(parsed.to_dict()) == parsed
    assert Parsed.from_dict({"artist": "A", "title": "T", "unknown": 1}).title == "T"
