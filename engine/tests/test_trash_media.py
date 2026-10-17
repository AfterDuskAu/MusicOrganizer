"""Moving a film or video the owner chose to the Trash, from the Movies folder or the
videos folder (2026-10-17): `fileops.trash_media` and `media.trash`. Everything is
under the test's own folders, and the Trash is the stand-in from conftest.py (one test
puts back the real `_send_to_trash`, with `send2trash` itself replaced)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from conftest import FakeTrash
from test_rpc import Capture, code, opened, out, result, root, server  # noqa: F401  (fixtures)

from musicorg import fileops, rpc
from musicorg.errors import FileOperationError, NotFoundError, OutsideLibraryError, UserError

# The real one, taken before conftest.py's stand-in replaces it for each test.
REAL_SEND_TO_TRASH = fileops._send_to_trash


@pytest.fixture
def places(tmp_path: Path) -> dict[str, Path]:
    movies = tmp_path / "Movies"
    videos = movies / "Videos"
    (movies / "Series" / "A Show" / "Season 1").mkdir(parents=True)
    videos.mkdir()
    film = movies / "The Kid (1921).mkv"
    film.write_bytes(b"picture and sound")
    return {"movies": movies, "videos": videos, "film": film, "tmp": tmp_path}


def trash(places: dict[str, Path], path: Path, **more: object) -> Path:
    return fileops.trash_media(path, places["movies"], places["videos"], **more)  # type: ignore[arg-type]


def test_a_film_goes_to_the_trash(places: dict[str, Path], fake_trash: FakeTrash) -> None:
    gone = trash(places, places["film"])
    assert gone == places["film"]
    assert not places["film"].exists()
    assert fake_trash.sent == [places["film"]]
    assert (fake_trash.folder / "0-The Kid (1921).mkv").read_bytes() == b"picture and sound"
    assert places["movies"].is_dir()  # the folder itself stays, with what else is in it
    assert (places["movies"] / "Series").is_dir()


def test_an_episode_and_a_downloaded_video_can_go_too(
    places: dict[str, Path], fake_trash: FakeTrash
) -> None:
    episode = places["movies"] / "Series" / "A Show" / "Season 1" / "A Show S01E01.MP4"
    episode.write_bytes(b"one")
    clip = places["videos"] / "A Talk.mp4"
    clip.write_bytes(b"two")
    trash(places, episode)
    trash(places, clip)
    assert fake_trash.sent == [episode, clip]
    assert (places["movies"] / "Series" / "A Show" / "Season 1").is_dir()  # folders stay


def test_a_videos_folder_chosen_elsewhere_counts(
    places: dict[str, Path], fake_trash: FakeTrash
) -> None:
    elsewhere = places["tmp"] / "Clips"
    elsewhere.mkdir()
    clip = elsewhere / "Match.webm"
    clip.write_bytes(b"x")
    fileops.trash_media(clip, places["movies"], elsewhere)
    assert fake_trash.sent == [clip]


def test_nothing_outside_the_two_folders(places: dict[str, Path], fake_trash: FakeTrash) -> None:
    stray = places["tmp"] / "stray.mkv"
    stray.write_bytes(b"not kept")
    with pytest.raises(OutsideLibraryError):
        trash(places, stray)
    # Nor by a way round: "..", or a folder whose name only starts the same.
    with pytest.raises(OutsideLibraryError):
        trash(places, places["movies"] / ".." / "stray.mkv")
    lookalike = places["tmp"] / "Movies Old"
    lookalike.mkdir()
    (lookalike / "film.mkv").write_bytes(b"x")
    with pytest.raises(OutsideLibraryError):
        trash(places, lookalike / "film.mkv")
    with pytest.raises(OutsideLibraryError):
        trash(places, Path("Movies") / "The Kid (1921).mkv")  # not a whole path
    assert stray.exists() and fake_trash.sent == []


def test_never_a_folder(places: dict[str, Path], fake_trash: FakeTrash) -> None:
    for folder in (places["movies"], places["videos"]):
        with pytest.raises(UserError, match="never moved"):  # the folders themselves
            trash(places, folder)
    for folder in (places["movies"] / "Series", places["movies"] / "Series" / "A Show"):
        with pytest.raises(UserError):
            trash(places, folder)
    deep = places["movies"] / "Series" / "A Show.mkv"  # a folder with a film's ending
    deep.mkdir()
    with pytest.raises(UserError):
        trash(places, deep)
    assert fake_trash.sent == [] and places["movies"].is_dir() and deep.is_dir()


def test_only_a_film_or_a_video(places: dict[str, Path], fake_trash: FakeTrash) -> None:
    for name in ("notes.txt", "The Kid (1921).srt", ".hidden.mkv", ".DS_Store"):
        other = places["movies"] / name
        other.write_bytes(b"the owner's")
        with pytest.raises(UserError):
            trash(places, other)
        assert other.exists()
    assert fake_trash.sent == []


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need extra rights on Windows")
def test_links_are_never_followed_out(places: dict[str, Path], fake_trash: FakeTrash) -> None:
    outside = places["tmp"] / "Elsewhere"
    outside.mkdir()
    real = outside / "real.mkv"
    real.write_bytes(b"somewhere else")
    # A link to a film elsewhere: left alone, link and film.
    link = places["movies"] / "link.mkv"
    link.symlink_to(real)
    with pytest.raises(OutsideLibraryError):
        trash(places, link)
    # A film reached through a linked folder that points out of the Movies folder.
    door = places["movies"] / "Door"
    door.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OutsideLibraryError):
        trash(places, door / "real.mkv")
    assert real.exists() and link.is_symlink() and fake_trash.sent == []


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need extra rights on Windows")
def test_a_movies_folder_that_is_a_link_still_works(
    places: dict[str, Path], fake_trash: FakeTrash
) -> None:
    alias = places["tmp"] / "Films"
    alias.symlink_to(places["movies"], target_is_directory=True)
    fileops.trash_media(alias / "The Kid (1921).mkv", alias, alias / "Videos")
    assert not places["film"].exists() and len(fake_trash.sent) == 1


def test_never_inside_a_library_or_a_source(places: dict[str, Path], fake_trash: FakeTrash) -> None:
    library = places["movies"] / "My Library"
    (library / "Music" / "Videos").mkdir(parents=True)
    song_video = library / "Music" / "Videos" / "Song.mp4"
    song_video.write_bytes(b"the library's")
    with pytest.raises(OutsideLibraryError):
        trash(places, song_video, forbidden=[library])
    rips = places["movies"] / "Rips"
    rips.mkdir()
    (rips / "Old.avi").write_bytes(b"a source's")
    with pytest.raises(OutsideLibraryError):
        trash(places, rips / "Old.avi", forbidden=[library, rips])
    assert song_video.exists() and (rips / "Old.avi").exists() and fake_trash.sent == []


def test_a_film_that_has_gone(places: dict[str, Path], fake_trash: FakeTrash) -> None:
    with pytest.raises(NotFoundError):
        trash(places, places["movies"] / "Gone (1999).mkv")
    assert fake_trash.sent == []


def test_a_trash_that_fails_leaves_the_film(
    places: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(path: Path) -> None:
        raise OSError("the Trash is on another drive")

    monkeypatch.setattr(fileops, "_send_to_trash", refuse)
    with pytest.raises(FileOperationError, match="still where it was"):
        trash(places, places["film"])
    assert places["film"].read_bytes() == b"picture and sound"

    # A Trash that says yes and leaves the file there is a failure too.
    monkeypatch.setattr(fileops, "_send_to_trash", lambda path: None)
    with pytest.raises(FileOperationError, match="still there"):
        trash(places, places["film"])


def test_it_is_send2trash_and_nothing_else(
    places: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real `_send_to_trash`, with send2trash itself replaced: the film is handed to
    send2trash, and nothing deletes it outright."""
    binned = places["tmp"] / "binned"
    binned.mkdir()
    handed: list[str] = []

    def send2trash(path: str) -> None:
        handed.append(path)
        os.replace(path, binned / Path(path).name)

    def never(*args: object, **kwargs: object) -> None:
        raise AssertionError("a film was deleted outright")

    monkeypatch.setattr(fileops, "_send_to_trash", REAL_SEND_TO_TRASH)
    monkeypatch.setattr(fileops.send2trash, "send2trash", send2trash)
    for name in ("unlink", "remove", "rmdir"):
        monkeypatch.setattr(os, name, never)
    monkeypatch.setattr(shutil, "rmtree", never)
    trash(places, places["film"])
    monkeypatch.undo()  # os and shutil back, before the test's folders are cleared
    assert handed == [os.path.realpath(places["film"])]
    assert (binned / places["film"].name).exists()


# ---- media.trash ---------------------------------------------------------------------


def test_media_trash_by_its_path(opened: rpc.Server, tmp_path: Path, fake_trash: FakeTrash) -> None:  # noqa: F811
    movies = tmp_path / "Films"
    movies.mkdir()
    result(opened, "settings.set", movies_folder=str(movies))
    film = movies / "Nosferatu (1922).mp4"
    film.write_bytes(b"x")
    assert code(opened, "media.trash") == rpc.INVALID_PARAMS
    assert result(opened, "media.trash", path=str(film)) == {"path": str(film)}
    assert fake_trash.sent == [film] and not film.exists()
    # Gone already: said so, nothing else happens.
    assert code(opened, "media.trash", path=str(film)) == rpc.NOT_FOUND


def test_media_trash_never_takes_a_library_file(
    opened: rpc.Server,  # noqa: F811
    root: Path,  # noqa: F811
    tmp_path: Path,
    fake_trash: FakeTrash,
) -> None:
    # A Movies folder that holds the library (allowed: it's only never *inside* one).
    result(opened, "settings.set", movies_folder=str(tmp_path))
    video = root / "Music" / "Videos" / "Artist" / "Song.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"the library's")
    assert code(opened, "media.trash", path=str(video)) == rpc.OUTSIDE
    assert video.exists() and fake_trash.sent == []
    assert "media.trash" in opened.methods
