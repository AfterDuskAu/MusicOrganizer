"""normalize: artist, title and version parsing from rip names and tags.

tests/data/filenames.tsv holds the file-name cases: input → artist, title, version
tokens (separated by "; ") and confidence band (high ≥ 0.8, mid, low < 0.5). Its last
column, "full title", is the title a copy named from that rip is given: the clean title
and the versions, in the rip's own words.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from musicorg import normalize
from musicorg.normalize import (
    Parsed,
    best_parse,
    compare_key,
    full_title,
    parse_filename,
    parse_owner_title,
    parse_tags,
)
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


# ---- the full title: the clean title, then the versions in the rip's own words ---------

VERSIONED = [row for row in cases() if row["versions"]]


@pytest.mark.parametrize("row", cases(), ids=lambda row: row["input"])
def test_full_titles(row: dict[str, str]) -> None:
    parsed = parse_filename(row["input"])
    assert full_title(parsed) == row["full title"], row["what it shows"]
    if not row["versions"]:
        assert row["full title"] == row["title"]  # no version: nothing is added
        assert parsed.version_words == ()


@pytest.mark.parametrize(
    ("name", "title"),
    [
        ("Nirvana - Come As You Are R", "Come As You Are R"),  # the owner's mark, as typed
        ("Oh Wonder - Done Wrong (R)", "Done Wrong (R)"),
        ("Phantogram - Black Out Days R(slowed)", "Black Out Days R (slowed)"),
        ("Kodaline - High Hopes (Filous Remix) R", "High Hopes (Filous Remix)"),  # R adds nothing
        ("Alessia Cara - Here (Lucian Remix))", "Here (Lucian Remix)"),  # a named remixer
        ("Jill Scott - Still Here [Acoustic Version]", "Still Here (Acoustic Version)"),
        ("Artist - Title - Live", "Title (Live)"),
        ("Artist - Title slowed + reverb", "Title (slowed + reverb)"),
        ("Artist - Title (X Remix Official Audio)", "Title (X Remix)"),
        ("Nightcore - Angel With A Shotgun", "Angel With A Shotgun (Nightcore)"),
        ("Artist - Title (Live at Wembley Stadium)", "Title (Live at Wembley Stadium)"),
        ("Artist - Title (320 kbps) [Official Audio]", "Title"),  # junk is left out
    ],
)
def test_the_ways_a_rip_names_its_version(name: str, title: str) -> None:
    assert full_title(parse_filename(name)) == title


@pytest.mark.parametrize("row", VERSIONED, ids=lambda row: row["input"])
def test_a_full_title_reads_back_as_the_same_song_and_versions(row: dict[str, str]) -> None:
    """What `full_title` writes, the reader for the owner's titles understands: the same
    clean title, the same version tokens, and written again it is the same title."""
    parsed = parse_filename(row["input"])
    title = full_title(parsed)
    assert title is not None
    back = parse_owner_title(title)
    assert compare_key(back.title) == compare_key(parsed.title)
    assert set(back.version_tokens) == set(parsed.version_tokens)
    assert full_title(back) == title


@pytest.mark.parametrize("row", VERSIONED, ids=lambda row: row["input"])
def test_the_words_of_each_version_name_its_tokens(row: dict[str, str]) -> None:
    parsed = parse_filename(row["input"])
    assert parsed.version_words  # a version is never left without its words
    named: set[str] = set()
    for words in parsed.version_words:
        if normalize.is_owner_mark(words):
            named.add("remix")
        else:
            named.update(normalize.parse_title(f"Song ({words})").version_tokens)
    assert named == set(parsed.version_tokens)


def test_the_words_keep_the_rips_spelling() -> None:
    accented = parse_filename("Ry X - Howling (Âme Remix)")
    assert accented.version_tokens == ("remix:ame",)  # for comparing
    assert accented.version_words == ("Âme Remix",)  # for the title
    assert full_title(accented) == "Howling (Âme Remix)"
    shouted = parse_filename("Katy Perry - E.T. (AIZZO REMIX) CAR VIDEO LIMMA")
    assert full_title(shouted) == "E.T. CAR VIDEO LIMMA (AIZZO REMIX)"
    # The tokens can't write a title: they would give "aizzo remix".
    assert normalize.render_versions(shouted.version_tokens) == "aizzo remix"


@pytest.mark.parametrize(
    ("name", "words", "title"),
    [
        ("Come As You Are R", ("R",), "Come As You Are R"),
        ("Done Wrong (R)", ("(R)",), "Done Wrong (R)"),
        ("Done Wrong(R)", ("(R)",), "Done Wrong (R)"),
        ("Done Wrong [R]", ("[R]",), "Done Wrong [R]"),
        ("Black Out Days R(slowed)", ("R", "slowed"), "Black Out Days R (slowed)"),
        ("Black Out Days (slowed) R", ("slowed", "R"), "Black Out Days (slowed) R"),
        ("Black Out Days R - Live", ("R", "Live"), "Black Out Days R (Live)"),
        ("High Hopes (Filous Remix) R", ("Filous Remix",), "High Hopes (Filous Remix)"),
        ("High Hopes (Remix) R", ("Remix",), "High Hopes (Remix)"),
    ],
)
def test_the_owners_mark_stays_as_typed(name: str, words: tuple[str, ...], title: str) -> None:
    """The owner's R is their name for the song: never spelled out as "(Remix)", never
    dropped, and where the rip has it. Next to a named remix it adds nothing."""
    readers = (
        parse_filename(name),
        parse_tags(TrackTags(title=name, artist="Someone")),
        best_parse(name, TrackTags(title=name, artist="Someone")),
        parse_owner_title(name),
    )
    for parsed in readers:
        assert parsed.version_words == words
        assert full_title(parsed) == title
        assert {token.partition(":")[0] for token in parsed.version_tokens} >= {"remix"}


def test_what_is_not_the_owners_mark() -> None:
    for name in ("Song r", "R", "Solo Dolo R 1", "Mr. Right"):
        parsed = parse_owner_title(name)
        assert (parsed.title, parsed.version_tokens, parsed.version_words) == (name, (), ())
        assert full_title(parsed) == name
    assert normalize.is_owner_mark("R") and normalize.is_owner_mark("(R)")
    assert not normalize.is_owner_mark("Remix") and not normalize.is_owner_mark("r")


def test_full_title_without_a_version_or_a_title() -> None:
    assert full_title(parse_filename("Kanye West - Stronger")) == "Stronger"
    assert full_title(parse_filename("Artist - Title (Part 2)")) == "Title (Part 2)"
    assert full_title(Parsed(None, None)) is None
    assert full_title(parse_filename("-")) is None  # a name with nothing in it


def test_full_title_drops_repeats_and_extra_spaces() -> None:
    parsed = Parsed(
        "A", "Song", ("live:wembley",), version_words=("Live  at Wembley", "LIVE AT WEMBLEY")
    )
    assert full_title(parsed) == "Song (Live at Wembley)"


def test_words_come_from_tags_too() -> None:
    video_title = parse_tags(
        TrackTags(
            title="Flight Facilities - Crave You (Adventure Club Remix)", artist="Trap Nation"
        )
    )
    assert full_title(video_title) == "Crave You (Adventure Club Remix)"
    dash_part = parse_tags(TrackTags(title="Yesterday - Remastered 2009", artist="The Beatles"))
    assert dash_part.version_words == ("Remastered 2009",)
    assert full_title(dash_part) == "Yesterday (Remastered 2009)"
    nightcore = parse_tags(TrackTags(title="Angel With A Shotgun", artist="Nightcore"))
    assert full_title(nightcore) == "Angel With A Shotgun (Nightcore)"


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
    remix = best_parse("Artist - Song (X Remix)", TrackTags(title="Track 01"))
    assert full_title(remix) == "Song (X Remix)"  # the name's words come with the name


def test_best_parse_merges_the_words_of_tags_and_name() -> None:
    # The tags don't name the version, the file name does: the owner's mark is kept.
    marked = best_parse("Song R", TrackTags(title="Song", artist="Band"))
    assert (marked.title, marked.version_tokens, marked.source) == ("Song", ("remix",), "tags")
    assert full_title(marked) == "Song R"
    named = best_parse("Band - Song (X Remix)", TrackTags(title="Song", artist="Band"))
    assert full_title(named) == "Song (X Remix)"
    # The tags name the remix: the file name's R adds nothing, so it isn't named twice.
    lucian = best_parse("Here R", TrackTags(title="Here (Lucian Remix)", artist="Alessia Cara"))
    assert lucian.version_words == ("Lucian Remix",)
    assert full_title(lucian) == "Here (Lucian Remix)"
    # Both name the same version: the tags' spelling is the one kept.
    twice = best_parse("Here (LUCIAN REMIX))", TrackTags(title="Here (Lucian Remix)", artist="A"))
    assert full_title(twice) == "Here (Lucian Remix)"
    # Another kind of version in the file name is added after the tags'.
    live = best_parse("Song (Live)", TrackTags(title="Song R", artist="Band"))
    assert live.version_tokens == ("remix", "live")
    assert full_title(live) == "Song R (Live)"


def test_a_counter_after_the_owners_mark() -> None:
    """Two rips of "Solo Dolo R" in one folder: the second file is "Solo Dolo R 1"."""
    tagged = best_parse("Solo Dolo R 1", TrackTags(title="Solo Dolo R", artist="Kid Cudi"))
    assert (tagged.title, tagged.version_tokens) == ("Solo Dolo", ("remix",))
    assert full_title(tagged) == "Solo Dolo R"
    # With no title tag there is only the file name, where the R isn't last. It isn't
    # read as the mark (matching is as it was), and the name stays as typed.
    untagged = best_parse("Solo Dolo R 1", TrackTags(artist="Kid Cudi"))
    assert (untagged.title, untagged.version_tokens) == ("Solo Dolo R 1", ())
    assert full_title(untagged) == "Solo Dolo R 1"


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
    assert parsed.to_dict()["version_words"] == ["X Remix"]
    assert Parsed.from_dict(parsed.to_dict()).version_words == ("X Remix",)


def test_a_parse_stored_before_the_words_were_kept() -> None:
    """An index scanned by an older engine holds parses without `version_words`."""
    parsed = parse_filename("Nirvana - Come As You Are R")
    stored = parsed.to_dict()
    del stored["version_words"]
    old = Parsed.from_dict(stored)
    assert old == parsed  # the same parse, as far as matching goes
    assert (old.version_tokens, old.version_words) == (("remix",), ())
    # It can't say what the title is, and it doesn't pretend the song has no version:
    # the names have to be parsed again (scan.parse_again).
    assert full_title(old) is None
    assert full_title(parsed) == "Come As You Are R"


def test_the_words_are_not_part_of_comparing_two_parses() -> None:
    assert parse_filename("Artist - Title (Live)") == parse_filename("Artist - Title [LIVE]")
    assert parse_filename("Artist - Title (Live)") != parse_filename("Artist - Title")


# ---- titles the owner named ------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "clean", "versions"),
    [
        ("Lost Boy R", "Lost Boy", ("remix",)),
        ("Done Wrong (R)", "Done Wrong", ("remix",)),
        ("Black Out Days R (slowed)", "Black Out Days", ("slowed", "remix")),
        ("Lost Boy (Radio Remix)", "Lost Boy", ("remix:radio",)),
        ("Here (Lucian Remix)", "Here", ("remix:lucian",)),
        ("Still Here (Acoustic Version)", "Still Here", ("acoustic",)),
        ("Yesterday - Remastered 2009", "Yesterday", ("remaster:2009",)),
        ("Crave You (feat. Giselle)", "Crave You", ()),
        ("Come As You Are", "Come As You Are", ()),
    ],
)
def test_a_title_the_owner_named(title: str, clean: str, versions: tuple[str, ...]) -> None:
    parsed = parse_owner_title(title)
    assert (parsed.title, parsed.version_tokens, parsed.artist) == (clean, versions, None)


@pytest.mark.parametrize("title", ["Lost Boy R", "Vitamin R", "Done Wrong (R)"])
def test_an_official_title_is_never_read_for_the_owners_mark(title: str) -> None:
    """YouTube Music never writes the mark, so `parse_title` must not see one."""
    official = normalize.parse_title(title)
    assert (official.title, official.version_tokens, official.version_words) == (title, (), ())
    assert parse_owner_title(title).version_tokens == ("remix",)


def test_without_the_mark_both_readers_agree() -> None:
    for title in ("Crave You (Adventure Club Remix)", "Song - Radio Edit", "Hello", "Song r"):
        assert parse_owner_title(title) == normalize.parse_title(title)
        assert parse_owner_title(title).version_words == normalize.parse_title(title).version_words


@pytest.mark.parametrize(
    "name", ["(Official Video)", "[HD]", "(Official Video) [HD]", "", " ", "---", "\U0001f3b5"]
)
def test_a_name_with_nothing_to_read_gives_no_title(name: str) -> None:
    """A rip called only "(Official Video)", or nothing but symbols, names no song. That
    is an answer like any other (no title, no confidence), not an error: a scan or a
    plan that meets such a name must be able to go on."""
    parsed = normalize.parse_filename(name)
    assert (parsed.title, parsed.artist, parsed.version_tokens) == (None, None, ())
    assert parsed.confidence == 0.0
    assert normalize.full_title(parsed) is None
    # The same through the tags: an artist tag alone doesn't make a title.
    with_artist = normalize.best_parse(name, TrackTags(artist="Band"))
    assert (with_artist.title, with_artist.confidence) == (None, 0.0)
