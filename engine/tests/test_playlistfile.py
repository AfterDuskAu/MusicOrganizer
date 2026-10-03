"""playlistfile: a playlist saved as a file (the way in for Amazon Music), for imports.

The files are written here, in the shapes export services give: no real export from
Amazon Music was at hand when this was written.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_rpc import Capture, code, opened, out, result, root, server  # noqa: F401  (fixtures)

from musicorg import imports, playlistfile, rpc
from musicorg.errors import UserError


def saved(tmp_path: Path, name: str, text: str, encoding: str = "utf-8") -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


def song(title: str, *artists: str, album: str | None = None, seconds: int | None = None) -> dict:
    return {"title": title, "artists": list(artists), "album": album, "duration_s": seconds,
            "is_explicit": None}  # fmt: skip


# ---- a table -----------------------------------------------------------------------------


def test_a_csv_with_the_columns_in_any_order_by_any_of_their_names(tmp_path: Path) -> None:
    path = saved(
        tmp_path,
        "My Amazon Playlist.csv",
        "Album,Artist name,Track name,ISRC,Duration\r\n"
        "Discovery,Daft Punk,One More Time,GBDUW0000053,5:20\r\n"
        '"Hello, World","Tyler, The Creator",Song; With a Semicolon,,215\r\n'
        ",,,,\r\n"
        "Greatest,Queen;David Bowie,Under Pressure,,248000\r\n",
    )
    read = playlistfile.read(path)
    assert read["name"] == "My Amazon Playlist" and read["more"] is False
    assert read["playlists"] == []
    assert read["tracks"] == [
        song("One More Time", "Daft Punk", album="Discovery", seconds=320),
        # A comma is part of the artist's name; a ";" in a title is the title's.
        song("Song; With a Semicolon", "Tyler, The Creator", album="Hello, World", seconds=215),
        song("Under Pressure", "Queen", "David Bowie", album="Greatest", seconds=248),
    ]


def test_spotifys_own_export_reads_too(tmp_path: Path) -> None:
    path = saved(
        tmp_path,
        "liked.csv",
        "Track URI,Track Name,Artist Name(s),Album Name,Duration (ms)\n"
        "spotify:track:x,Here,Alessia Cara,Know-It-All,199000\n",
    )
    assert playlistfile.read(path)["tracks"] == [
        song("Here", "Alessia Cara", album="Know-It-All", seconds=199)
    ]


@pytest.mark.parametrize(
    ("name", "text", "encoding"),
    [
        ("tabs.tsv", "Title\tArtist\nDéjà Vu\tBeyoncé\n", "utf-8"),
        ("semicolons.csv", "Title;Artist\nDéjà Vu;Beyoncé\n", "utf-8"),
        ("marked.csv", "\ufeffTitle,Artist\nDéjà Vu,Beyoncé\n", "utf-8"),
        ("sheet.txt", "Title\tArtist\r\nDéjà Vu\tBeyoncé\r\n", "utf-16"),
        ("old.csv", "Title,Artist\nDéjà Vu,Beyoncé\n", "cp1252"),
    ],
)
def test_however_the_table_was_saved(tmp_path: Path, name: str, text: str, encoding: str) -> None:
    assert playlistfile.read(saved(tmp_path, name, text, encoding))["tracks"] == [
        song("Déjà Vu", "Beyoncé")
    ]


def test_a_file_of_several_playlists_gives_their_names_and_one_of_them(tmp_path: Path) -> None:
    path = saved(
        tmp_path,
        "everything.csv",
        "Track name,Artist name,Playlist name\n"
        "One,Band,Road Trip\nTwo,Band,Gym\nThree,Band,Road Trip\n",
    )
    first = playlistfile.read(path)
    assert first["playlists"] == ["Road Trip", "Gym"] and first["name"] == "Road Trip"
    assert [t["title"] for t in first["tracks"]] == ["One", "Three"]
    gym = playlistfile.read(path, "Gym")
    assert gym["name"] == "Gym" and [t["title"] for t in gym["tracks"]] == ["Two"]
    with pytest.raises(UserError, match="no playlist of that name"):
        playlistfile.read(path, "Sleep")


def test_one_named_playlist_gives_the_import_its_name(tmp_path: Path) -> None:
    path = saved(tmp_path, "export.csv", "Title,Artist,Playlist\nOne,Band,Road Trip\n")
    read = playlistfile.read(path)
    assert read["name"] == "Road Trip" and read["playlists"] == []


def test_a_table_with_no_song_column_says_what_it_needs(tmp_path: Path) -> None:
    with pytest.raises(UserError, match="Title or Track name"):
        playlistfile.read(saved(tmp_path, "odd.csv", "Artist,Album\nBand,Record\n"))


# ---- lines, and M3U ----------------------------------------------------------------------


def test_a_text_file_is_a_song_a_line(tmp_path: Path) -> None:
    path = saved(
        tmp_path,
        "road trip.txt",
        "# my list\n\nDaft Punk - One More Time\nJay-Z - 99 Problems\nJust A Title\n"
        "AC/DC - Back In Black - Live\n",
    )
    read = playlistfile.read(path)
    assert read["name"] == "road trip"
    assert read["tracks"] == [
        song("One More Time", "Daft Punk"),
        song("99 Problems", "Jay-Z"),  # a hyphen inside a name isn't the " - " between them
        song("Just A Title"),
        song("Back In Black - Live", "AC/DC"),
    ]


def test_an_m3u_playlist(tmp_path: Path) -> None:
    path = saved(
        tmp_path,
        "mix.m3u8",
        "#EXTM3U\n"
        "#EXTINF:320,Daft Punk - One More Time\n/Volumes/Music/01.mp3\n"
        "#EXTINF:-1,Queen - Under Pressure\nhttp://example.invalid/stream\n"
        "C:\\Music\\Alessia Cara - Here.mp3\n"
        "#EXTINF:200,\nsongs/Band - Named By Its File.m4a\n",
    )
    assert playlistfile.read(path)["tracks"] == [
        song("One More Time", "Daft Punk", seconds=320),
        song("Under Pressure", "Queen"),
        song("Here", "Alessia Cara"),
        song("Named By Its File", "Band", seconds=200),
    ]


# ---- what's refused ----------------------------------------------------------------------


def test_what_cant_be_a_playlist_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(UserError, match="CSV"):
        playlistfile.read(saved(tmp_path, "songs.xlsx", "x"))
    with pytest.raises(UserError, match="couldn't be opened"):
        playlistfile.read(tmp_path / "gone.csv")
    with pytest.raises(UserError, match="No songs"):
        playlistfile.read(saved(tmp_path, "empty.csv", "Title,Artist\n"))
    with pytest.raises(UserError, match="No songs"):
        playlistfile.read(saved(tmp_path, "empty.txt", "\n# nothing\n"))
    monkeypatch.setattr(playlistfile, "MAX_BYTES", 10)
    with pytest.raises(UserError, match="too big"):
        playlistfile.read(saved(tmp_path, "big.csv", "Title,Artist\nOne,Band\n"))


def test_a_long_file_is_cut_and_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(playlistfile, "MAX_TRACKS", 2)
    read = playlistfile.read(saved(tmp_path, "long.txt", "A - 1\nA - 2\nA - 3\n"))
    assert len(read["tracks"]) == 2 and read["more"] is True


def test_the_file_is_only_read(tmp_path: Path) -> None:
    path = saved(tmp_path, "list.csv", "Title,Artist\nHere,Alessia Cara\n")
    before = (
        path.read_bytes(),
        path.stat().st_mtime_ns,
        sorted(p.name for p in tmp_path.iterdir()),
    )
    playlistfile.read(path)
    assert before == (
        path.read_bytes(), path.stat().st_mtime_ns, sorted(p.name for p in tmp_path.iterdir()),
    )  # fmt: skip


# ---- as an import, and over RPC ----------------------------------------------------------


def test_a_file_as_an_import(tmp_path: Path) -> None:
    path = saved(tmp_path, "Gym.csv", "Title,Artist\nHere,Alessia Cara\n")
    read = imports.from_file(str(path))
    assert read["source"] == "file" and read["name"] == "Gym"
    # Where the file was isn't in the answer: only its name, as the playlist's.
    assert str(tmp_path) not in str(read)
    for bad in ("", "  ", "Gym.csv"):
        with pytest.raises(UserError, match="Choose"):
            imports.from_file(bad)


def test_a_file_over_rpc(opened: rpc.Server, tmp_path: Path) -> None:  # noqa: F811
    path = saved(
        tmp_path, "all.csv", "Title,Artist,Playlist name\nOne,Band,Road Trip\nTwo,Band,Gym\n"
    )
    read = result(opened, "import.playlist", source="file", path=str(path))
    assert read["playlists"] == ["Road Trip", "Gym"] and read["name"] == "Road Trip"
    gym = result(opened, "import.playlist", source="file", path=str(path), playlist="Gym")
    assert [t["title"] for t in gym["tracks"]] == ["Two"]
    assert code(opened, "import.playlist", source="file") == rpc.INVALID_PARAMS
    assert code(opened, "import.playlist", source="file", path=str(tmp_path / "no.csv")) == (
        rpc.USER_ERROR
    )
