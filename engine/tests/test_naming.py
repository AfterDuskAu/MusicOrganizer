"""Naming rules (contract section 2): sanitising, fallbacks, limits, the Windows budget."""

from __future__ import annotations

import unicodedata
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any

import pytest

from musicorg import naming
from musicorg.errors import PathTooLongError
from musicorg.naming import TrackMeta, candidate_names, library_path, safe_component

ROOT = PurePosixPath("/Users/someone/Music Organizer Library")


def name(root: PurePath = ROOT, platform: str = "darwin", **fields: Any) -> str:
    """The library path for these tag fields, as a "/"-separated string."""
    return library_path(TrackMeta(**fields), root, platform=platform).as_posix()


def windows_length(root: PurePath, relative: str) -> int:
    """Length of <root>\\_Replaced\\<relative> in UTF-16 units, as Windows counts it."""
    full = str(PureWindowsPath(str(root), naming.REPLACED_DIR, *relative.split("/")))
    return len(full.encode("utf-16-le")) // 2


# ---- the naming table ------------------------------------------------------------------

TABLE: list[tuple[str, dict[str, Any], str]] = [
    # the template and its fallbacks
    (
        "full template",
        dict(artist="Daft Punk", album="Discovery", year=2001, track=1, title="One More Time"),
        "Daft Punk/Discovery (2001)/01 One More Time.m4a",
    ),
    (
        "album artist wins over track artist",
        dict(
            artist="Drake feat. Rihanna",
            album_artist="Drake",
            album="Views",
            year=2016,
            track=12,
            title="Too Good",
        ),
        "Drake/Views (2016)/12 Too Good.m4a",
    ),
    (
        "no album artist: track artist",
        dict(artist="Adele", album="25", year=2015, track=1, title="Hello"),
        "Adele/25 (2015)/01 Hello.m4a",
    ),
    (
        "no artist at all",
        dict(album="Demos", year=1999, track=4, title="Take 4"),
        "Unknown Artist/Demos (1999)/04 Take 4.m4a",
    ),
    (
        "blank artists",
        dict(artist="   ", album_artist="", album="Demos", track=4, title="Take 4"),
        "Unknown Artist/Demos/04 Take 4.m4a",
    ),
    (
        "no year",
        dict(artist="Adele", album="25", track=1, title="Hello"),
        "Adele/25/01 Hello.m4a",
    ),
    (
        "year from a full date",
        dict(artist="A", album="B", year="2019-05-03", track=1, title="T"),
        "A/B (2019)/01 T.m4a",
    ),
    ("year zero", dict(artist="A", album="B", year=0, track=1, title="T"), "A/B/01 T.m4a"),
    (
        "year that isn't one",
        dict(artist="A", album="B", year="unknown", track=1, title="T"),
        "A/B/01 T.m4a",
    ),
    (
        "no album: Unsorted, without track number or year",
        dict(artist="Adele", track=5, year=2015, title="Hello"),
        "Adele/Unsorted/Hello.m4a",
    ),
    ("no album or artist", dict(title="Hello"), "Unknown Artist/Unsorted/Hello.m4a"),
    (
        "compilation",
        dict(
            artist="Toto",
            album_artist="Toto",
            album="Now 100",
            year=2018,
            track=5,
            compilation=True,
            title="Africa",
        ),
        "Various Artists/Now 100 (2018)/05 Africa.m4a",
    ),
    (
        "compilation flag without an album",
        dict(artist="Toto", compilation=True, title="Africa"),
        "Toto/Unsorted/Africa.m4a",
    ),
    (
        "no title: the source file's name",
        dict(artist="A", album="B", track=2, source_file="/rips/Some Song (live).mp3"),
        "A/B/02 Some Song (live).m4a",
    ),
    (
        "no title: a Windows source path",
        dict(artist="A", album="B", track=2, source_file=PureWindowsPath(r"C:\rips\x\Tune.mp3")),
        "A/B/02 Tune.m4a",
    ),
    (
        "blank title: the source file's name",
        dict(artist="A", album="B", title="  ", source_file="Take 2.m4a"),
        "A/B/Take 2.m4a",
    ),
    (
        "no title and no source file",
        dict(artist="A", album="B", track=1),
        "A/B/01 Unknown Title.m4a",
    ),
    # track and disc numbers
    (
        "multi-disc album",
        dict(artist="A", album="B", disc=2, disc_total=2, track=7, title="T"),
        "A/B/2-07 T.m4a",
    ),
    (
        "disc 1 of 2",
        dict(artist="A", album="B", disc=1, disc_total=2, track=3, title="T"),
        "A/B/1-03 T.m4a",
    ),
    (
        "disc 1 of 1",
        dict(artist="A", album="B", disc=1, disc_total=1, track=3, title="T"),
        "A/B/03 T.m4a",
    ),
    (
        "disc 2, total unknown",
        dict(artist="A", album="B", disc=2, track=7, title="T"),
        "A/B/2-07 T.m4a",
    ),
    (
        "tag-style numbers",
        dict(artist="A", album="B", disc="2/3", track="5/12", title="T"),
        "A/B/2-05 T.m4a",
    ),
    ("track 0: no prefix", dict(artist="A", album="B", track=0, title="T"), "A/B/T.m4a"),
    ("track above 99", dict(artist="A", album="B", track=104, title="T"), "A/B/104 T.m4a"),
    (
        "multi-disc without a track number",
        dict(artist="A", album="B", disc=2, disc_total=2, title="T"),
        "A/B/T.m4a",
    ),
    # extensions
    (
        "extension with a dot and capitals",
        dict(artist="A", album="B", track=1, title="T", ext=".MP3"),
        "A/B/01 T.mp3",
    ),
    ("flac", dict(artist="A", album="B", track=1, title="T", ext="flac"), "A/B/01 T.flac"),
    # illegal and control characters
    (
        "illegal characters",
        dict(artist="AC/DC", album='Live: "At" <Donington>?', track=1, title="Who*Made|Who\\Me"),
        "AC_DC/Live_ _At_ _Donington__/01 Who_Made_Who_Me.m4a",
    ),
    (
        "control characters",
        dict(artist="A\tB", album="C\nD", track=1, title="E\x00F\x7fG\x85H"),
        "A_B/C_D/01 E_F_G_H.m4a",
    ),
    (
        "undecodable bytes in a source file name",
        dict(artist="A", album="B", source_file="bad\udcffname.mp3"),
        "A/B/bad_name.m4a",
    ),
    # dots and spaces
    ("trailing dots on a folder", dict(artist="A", album="Vol. 2...", title="T"), "A/Vol. 2/T.m4a"),
    (
        "trailing dots in a title stay, before the extension",
        dict(artist="A", album="B", track=3, title="Wait for it..."),
        "A/B/03 Wait for it....m4a",
    ),
    (
        "dots before the year stay",
        dict(artist="R.E.M.", album="Up...", year=1998, track=1, title="T"),
        "R.E.M/Up... (1998)/01 T.m4a",
    ),
    ("leading dots", dict(artist=".hack", album="B", title=".x"), "_hack/B/_x.m4a"),
    ("nothing but dots", dict(artist="...", album="B", title="T"), "_/B/T.m4a"),
    (
        "outer spaces",
        dict(artist="  Adele  ", album=" 25 ", year=2015, track=1, title="  Hello  "),
        "Adele/25 (2015)/01 Hello.m4a",
    ),
    # Windows reserved names
    ("reserved folder name", dict(artist="CON", album="B", title="T"), "CON_/B/T.m4a"),
    ("reserved, any case", dict(artist="A", album="nul", title="T"), "A/nul_/T.m4a"),
    ("reserved, with an extension", dict(artist="A", title="Aux"), "A/Unsorted/Aux_.m4a"),
    ("reserved COM and LPT", dict(artist="com1", album="LPT9", title="T"), "com1_/LPT9_/T.m4a"),
    (
        "names that only start like reserved ones",
        dict(artist="CONCERT", album="COM10", track=1, title="Console"),
        "CONCERT/COM10/01 Console.m4a",
    ),
    (
        "reserved word plus a year isn't reserved",
        dict(artist="A", album="NUL", year=1999, title="T"),
        "A/NUL (1999)/T.m4a",
    ),
    (
        "reserved word after a track number isn't reserved",
        dict(artist="A", album="B", track=1, title="PRN"),
        "A/B/01 PRN.m4a",
    ),
    # Unicode
    (
        "emoji are kept",
        dict(artist="Coldplay", album="B", track=1, title="Higher Power \U0001f680"),
        "Coldplay/B/01 Higher Power \U0001f680.m4a",
    ),
    (
        "accents are kept",
        dict(
            artist="Sigur R\u00f3s",
            album="\u00c1g\u00e6tis byrjun",
            year=1999,
            track=1,
            title="Svefn-g-englar",
        ),
        "Sigur R\u00f3s/\u00c1g\u00e6tis byrjun (1999)/01 Svefn-g-englar.m4a",
    ),
    (
        "decomposed accents come out composed",
        dict(artist="Beyonce\u0301", album="Lemonade", year=2016, track=1, title="Hold Up"),
        "Beyonc\u00e9/Lemonade (2016)/01 Hold Up.m4a",
    ),
]


def test_table_has_at_least_40_cases() -> None:
    assert len(TABLE) >= 40


@pytest.mark.parametrize(("fields", "expected"), [c[1:] for c in TABLE], ids=[c[0] for c in TABLE])
def test_naming_table(fields: dict[str, Any], expected: str) -> None:
    assert name(**fields) == expected


def test_result_is_relative_to_music_folder() -> None:
    result = library_path(TrackMeta(artist="A", album="B", title="T"), ROOT, platform="darwin")
    assert isinstance(result, Path)
    assert not result.is_absolute()
    assert len(result.parts) == 3


# ---- Unicode normalisation and length limits -------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Beyonc\u00e9",
        "Caf\u00e9 del Mar",
        "\u00c5ngstr\u00f6m",
        "\ud55c\uad6d\uc5b4",
        "Pok\u00e9mon",
    ],
)
def test_nfc_and_nfd_give_the_same_path(text: str) -> None:
    nfc = unicodedata.normalize("NFC", text)
    nfd = unicodedata.normalize("NFD", text)
    assert nfc != nfd
    from_nfc = name(artist=nfc, album=nfc, title=nfc, track=1)
    from_nfd = name(artist=nfd, album=nfd, title=nfd, track=1)
    assert from_nfc == from_nfd
    assert unicodedata.is_normalized("NFC", from_nfd)


JAPANESE_120 = (
    "\u590f\u306e\u7d42\u308f\u308a\u306b\u541b\u3068\u898b\u305f\u82b1\u706b\u306e\u8272\u3092" * 8
)[:120]


def test_japanese_title_of_120_characters_fits_200_bytes() -> None:
    assert len(JAPANESE_120) == 120
    assert len(JAPANESE_120.encode("utf-8")) == 360
    file_name = name(artist="A", album="B", track=1, title=JAPANESE_120).split("/")[-1]
    assert len(file_name.encode("utf-8")) <= naming.MAX_BYTES
    # "01 " and ".m4a" take 7 bytes; 3-byte characters then fit 64 times into 193.
    assert file_name == "01 " + JAPANESE_120[:64] + ".m4a"


def test_japanese_folder_names_fit_200_bytes() -> None:
    artist, album, _ = name(artist=JAPANESE_120, album=JAPANESE_120, year=2020, title="T").split(
        "/"
    )
    assert artist == JAPANESE_120[:66]  # 198 bytes
    assert album == JAPANESE_120[:64] + " (2020)"  # the year is kept
    for component in (artist, album):
        assert len(component.encode("utf-8")) <= naming.MAX_BYTES


def test_long_title_is_cut_to_120_characters_keeping_the_extension() -> None:
    title = "A Very Long Title " * 20
    file_name = name(artist="A", album="B", track=1, title=title).split("/")[-1]
    assert len(file_name) <= naming.MAX_CHARS
    assert file_name.endswith(".m4a")
    assert file_name.startswith("01 A Very Long Title A Very")
    assert not file_name.removesuffix(".m4a").endswith(" ")


def test_long_album_keeps_its_year() -> None:
    album = name(artist="A", album="B" * 300, year=2001, title="T").split("/")[1]
    assert album == "B" * 113 + " (2001)"


def test_emoji_sequence_is_never_split() -> None:
    family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"
    file_name = name(artist="A", album="B", track=1, title="a" * 110 + family + "zz").split("/")[-1]
    assert file_name == "01 " + "a" * 110 + ".m4a"


def test_flags_are_never_split() -> None:
    flags = "\U0001f1ef\U0001f1f5" * 70  # 70 Japanese flags, two code points each
    title = name(artist="A", album="B", title=flags).split("/")[-1].removesuffix(".m4a")
    assert len(title) % 2 == 0
    assert title == flags[: len(title)]


def test_stacked_accents_are_cut_rather_than_emptied() -> None:
    title = "a" + "\u0301" * 300  # "zalgo" text: one character with 300 accents
    file_name = name(artist="A", album="B", title=title).split("/")[-1]
    assert len(file_name.encode("utf-8")) <= naming.MAX_BYTES
    assert file_name.startswith("\u00e1\u0301")  # NFC made the first accent part of the a


TRICKY = [
    "",
    " ",
    ".",
    "..",
    "...",
    " . ",
    "..a..",
    "a" * 500,
    "\u00e9" * 500,
    "\U0001f600" * 200,
    "CON",
    "con.txt",
    "LPT\u00b9",
    'x\\y/z:*?"<>|',
    "\x00\x01\x1f",
    "\ud800",
]


@pytest.mark.parametrize("text", TRICKY)
def test_components_are_always_safe(text: str) -> None:
    for component in name(artist=text, album=text, title=text, track=1).split("/"):
        assert component
        assert len(component) <= naming.MAX_CHARS + 1  # +1 for a reserved-name "_"
        assert len(component.encode("utf-8")) <= naming.MAX_BYTES + 1
        assert not component.startswith((" ", "."))
        assert not component.endswith((" ", "."))
        assert not set(component) & set('\\/:*?"<>|')
        assert all(unicodedata.category(ch) not in ("Cc", "Cs") for ch in component)
        assert component.split(".", 1)[0].upper() not in naming._RESERVED


# ---- the Windows path budget -----------------------------------------------------------

LONG_ROOT = PureWindowsPath("C:/Users/someone/" + "x" * 100)  # 117 characters


def test_windows_budget_cuts_the_title() -> None:
    fields = dict(artist="Artist Name", album="Album Name", year=2001, track=1, title="T" * 150)
    on_windows = name(root=LONG_ROOT, platform="win32", **fields)
    # 117 + "\_Replaced\Artist Name\Album Name (2001)\" leaves 96 of the 254 for the file.
    assert on_windows == "Artist Name/Album Name (2001)/01 " + "T" * 89 + ".m4a"
    assert windows_length(LONG_ROOT, on_windows) == 254
    assert naming.WINDOWS_MAX_PATH - naming.COLLISION_ROOM == 254

    # The same track on a Mac isn't cut beyond the 120-character component limit.
    on_mac = name(root=LONG_ROOT, platform="darwin", **fields)
    assert on_mac.endswith("01 " + "T" * 113 + ".m4a")


def test_windows_budget_counts_emoji_as_two() -> None:
    """Windows measures paths in UTF-16 units, where an emoji takes two."""
    root = PureWindowsPath("C:/" + "x" * 147)  # 150 characters
    fields = dict(artist="Artist", album="Album", track=1, title="\U0001f3b5" * 110)
    on_mac = name(root=root, platform="darwin", **fields)
    assert on_mac.endswith("01 " + "\U0001f3b5" * 48 + ".m4a")  # 192 of the 200 bytes
    on_windows = name(root=root, platform="win32", **fields)
    # 48 emoji would be 103 units for the file name, 23 over the budget: cut 12 emoji.
    assert on_windows.endswith("01 " + "\U0001f3b5" * 36 + ".m4a")
    assert windows_length(root, on_windows) == 253


def test_windows_budget_cuts_album_then_artist_when_the_title_is_not_enough() -> None:
    root = PureWindowsPath("C:/" + "r" * 197)  # 200 characters
    fields = dict(artist="A" * 30, album="B" * 30, track=1, title="T" * 30)
    artist, album, file_name = name(root=root, platform="win32", **fields).split("/")
    assert file_name == "01 " + "T" * naming.MIN_FIELD_CHARS + ".m4a"
    assert album == "B" * naming.MIN_FIELD_CHARS
    assert artist.startswith("A" * naming.MIN_FIELD_CHARS)
    assert windows_length(root, f"{artist}/{album}/{file_name}") <= 254


def test_windows_budget_counts_cover_jpg_too() -> None:
    """cover.jpg sits beside the tracks, so a very short file name doesn't set the budget."""
    root = PureWindowsPath("C:/" + "r" * 207)  # 210 characters
    fields = dict(artist="A" * 12, album="B" * 12, title="T")
    artist, album, file_name = name(root=root, platform="win32", **fields).split("/")
    assert file_name == "T.m4a"
    assert windows_length(root, f"{artist}/{album}/{file_name}") < 254
    assert album == "B" * naming.MIN_FIELD_CHARS  # cut for cover.jpg's sake
    assert windows_length(root, f"{artist}/{album}/{naming.COVER_NAME}") <= 254


def test_root_too_long_for_windows_is_refused() -> None:
    root = PureWindowsPath("C:/" + "r" * 240)
    fields = dict(artist="A" * 30, album="B" * 30, track=1, title="T" * 30)
    with pytest.raises(PathTooLongError) as caught:
        name(root=root, platform="win32", **fields)
    assert "243 characters" in caught.value.message
    assert "shorter path" in caught.value.message


def test_short_root_leaves_windows_names_alone() -> None:
    root = PureWindowsPath("C:/Music Organizer Library")
    fields = dict(artist="A" * 40, album="B" * 40, track=1, title="T" * 60)
    assert name(root=root, platform="win32", **fields) == name(root=root, **fields)


# ---- safe_component, candidate_names, junk and audio names -----------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Daft Punk", "Daft Punk"),
        ("", ""),
        ("   ", ""),
        ("a/b", "a_b"),
        (" .config ", "_config"),
        ("trailing. . .", "trailing"),
        ("Nul", "Nul_"),
        ("aux.tar.gz", "aux_.tar.gz"),
        ("Cafe\u0301", "Caf\u00e9"),
    ],
)
def test_safe_component(text: str, expected: str) -> None:
    assert safe_component(text) == expected


def test_safe_component_custom_limits() -> None:
    assert safe_component("abcdefgh", max_chars=5) == "abcde"
    assert safe_component("\u00e9\u00e9\u00e9\u00e9", max_bytes=5) == "\u00e9\u00e9"
    assert safe_component("abc   def", max_chars=5) == "abc"


def test_candidate_names() -> None:
    target = Path("Artist", "Album", "01 Mr. Brightside.m4a")
    names = list(candidate_names(target, limit=4))
    assert [n.name for n in names] == [
        "01 Mr. Brightside.m4a",
        "01 Mr. Brightside (2).m4a",
        "01 Mr. Brightside (3).m4a",
        "01 Mr. Brightside (4).m4a",
    ]
    assert all(n.parent == target.parent for n in names)


def test_candidate_names_without_extension() -> None:
    names = list(candidate_names(Path("Mr. Brightside"), limit=2))
    assert [n.name for n in names] == ["Mr. Brightside", "Mr. Brightside (2)"]


def test_candidate_names_has_a_limit() -> None:
    assert len(list(candidate_names(Path("a.m4a")))) == 1000


@pytest.mark.parametrize(
    ("file_name", "junk", "audio"),
    [
        (".DS_Store", True, False),
        ("._01 Song.mp3", True, False),
        ("Thumbs.db", True, False),
        ("DESKTOP.INI", True, False),
        ("01 Song.mp3", False, True),
        ("01 Song.M4A", False, True),
        ("song.flac", False, True),
        ("cover.jpg", False, False),
        ("notes.txt", False, False),
    ],
)
def test_junk_and_audio_names(file_name: str, junk: bool, audio: bool) -> None:
    assert naming.is_junk(file_name) is junk
    assert naming.is_audio_name(file_name) is audio


def test_library_paths_layout() -> None:
    paths = naming.LibraryPaths(Path("/lib"))
    assert paths.music == Path("/lib/Music")
    assert paths.replaced == Path("/lib/_Replaced")
    assert paths.staging == Path("/lib/_Staging")
    assert paths.calibration == Path("/lib/_Staging/calibration")
    assert paths.reports == Path("/lib/Reports")
    assert paths.state_file == Path("/lib/.musicorg/state.json")
    assert paths.lock_file == Path("/lib/.musicorg/lock")
    assert paths.lock_info_file == Path("/lib/.musicorg/lock.info")
    assert paths.managed == (paths.music, paths.replaced, paths.staging, paths.engine)
