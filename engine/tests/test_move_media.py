"""Moving a kept film or video into another of the folders they're kept in (2026-10-17,
the owner: "right clicking on a downloaded movie/tv show, it can move to any download
folder, including the 18+ one, incase there is a problem with detection"):
`fileops.move_media`, `naming.kept_move_place` and `media.move`. Everything is under the
test's own folders, and the Trash is the stand-in from conftest.py."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import FakeTrash
from test_rpc import Capture, code, opened, out, result, root, server  # noqa: F401  (fixtures)

from musicorg import fileops, naming, rpc
from musicorg.errors import (
    CrossVolumeError,
    FileOperationError,
    NotFoundError,
    OutsideLibraryError,
    UserError,
)


@pytest.fixture
def places(tmp_path: Path) -> dict[str, Path]:
    movies = tmp_path / "Movies"
    videos = movies / "Videos"
    videos.mkdir(parents=True)
    film = movies / "The Kid (1921).mkv"
    film.write_bytes(b"picture and sound")
    return {"movies": movies, "videos": videos, "film": film, "tmp": tmp_path}


def move(places: dict[str, Path], path: Path, kind: str, **more: object) -> Path:
    base = places["videos"] if kind == naming.KEPT_VIDEO else places["movies"]
    folder = base.joinpath(*naming.kept_move_place(kind, path.name))
    return fileops.move_media(path, folder, places["movies"], places["videos"], **more)  # type: ignore[arg-type]


# ---- where a moved file goes -----------------------------------------------------------


def test_where_each_kind_goes() -> None:
    place = naming.kept_move_place
    assert place("movie", "Show S01E02.mkv") == ()
    assert place("video", "A Talk.mp4") == ()
    assert place("adult", "Show S01E02 - Title.mp4") == ("18+",)
    assert place("series", "The Show S01E02 - Pilot.mkv") == ("Series", "The Show", "Season 1")
    assert place("anime", "show.name.s02e10.1080p.mkv") == ("Anime", "show name", "Season 2")
    assert place("series", "Show S00E03.mkv") == ("Series", "Show", "Specials")
    # No show named before its numbers, or no numbers: loose in the kind's folder.
    assert place("series", "S01E02.mkv") == ("Series",)
    assert place("anime", "Spirited Away (2001).mkv") == ("Anime",)
    with pytest.raises(ValueError):
        place("music", "x.mp4")


# ---- fileops.move_media ------------------------------------------------------------------


def test_a_film_moves_into_series_under_its_show(places: dict[str, Path]) -> None:
    episode = places["movies"] / "A Show S01E02 - Two.mkv"
    episode.write_bytes(b"an episode")
    moved = move(places, episode, "series")
    assert moved == places["movies"] / "Series" / "A Show" / "Season 1" / "A Show S01E02 - Two.mkv"
    assert moved.read_bytes() == b"an episode" and not episode.exists()


def test_into_18_and_back_out_and_into_the_videos_folder(places: dict[str, Path]) -> None:
    there = move(places, places["film"], "adult")
    assert there == places["movies"] / "18+" / "The Kid (1921).mkv" and there.is_file()
    back = move(places, there, "movie")
    assert back == places["film"] and back.read_bytes() == b"picture and sound"
    clip = move(places, back, "video")
    assert clip == places["videos"] / "The Kid (1921).mkv" and not back.exists()
    # The 18+ folder it left empty went; the two folders themselves never do.
    assert not (places["movies"] / "18+").exists()
    film = move(places, clip, "movie")
    assert film.is_file() and places["videos"].is_dir()


def test_a_show_folder_it_leaves_empty_goes_and_one_with_more_stays(
    places: dict[str, Path],
) -> None:
    season = places["movies"] / "Series" / "A Show" / "Season 1"
    season.mkdir(parents=True)
    one, two = season / "A Show S01E01.mkv", season / "A Show S01E02.mkv"
    one.write_bytes(b"1")
    two.write_bytes(b"2")
    move(places, one, "movie")
    assert season.is_dir()  # the other episode is still there
    move(places, two, "anime")
    assert not (places["movies"] / "Series" / "A Show").exists()
    assert (places["movies"] / "Anime" / "A Show" / "Season 1" / two.name).read_bytes() == b"2"
    assert not (places["movies"] / "Series").exists() and places["movies"].is_dir()


def test_never_over_a_file_that_is_there(places: dict[str, Path]) -> None:
    (places["movies"] / "18+").mkdir()
    (places["movies"] / "18+" / "The Kid (1921).mkv").write_bytes(b"another")
    moved = move(places, places["film"], "adult")
    assert moved.name == "The Kid (1921) (2).mkv"
    assert (places["movies"] / "18+" / "The Kid (1921).mkv").read_bytes() == b"another"
    assert moved.read_bytes() == b"picture and sound"


def test_one_that_is_there_already_is_said_so(places: dict[str, Path]) -> None:
    with pytest.raises(UserError, match="there already"):
        move(places, places["film"], "movie")
    assert places["film"].exists()


def test_a_videos_folder_chosen_elsewhere_counts(places: dict[str, Path]) -> None:
    elsewhere = places["tmp"] / "Clips"
    elsewhere.mkdir()
    moved = fileops.move_media(places["film"], elsewhere, places["movies"], elsewhere)
    assert moved == elsewhere / places["film"].name
    back = fileops.move_media(moved, places["movies"], places["movies"], elsewhere)
    assert back == places["film"] and elsewhere.is_dir()


def test_nothing_from_or_to_anywhere_else(places: dict[str, Path]) -> None:
    stray = places["tmp"] / "stray.mkv"
    stray.write_bytes(b"x")
    with pytest.raises(OutsideLibraryError):
        fileops.move_media(stray, places["movies"], places["movies"], places["videos"])
    with pytest.raises(OutsideLibraryError):
        fileops.move_media(
            places["film"], places["tmp"] / "Out", places["movies"], places["videos"]
        )
    with pytest.raises(OutsideLibraryError):
        fileops.move_media(
            Path("relative.mkv"), places["movies"], places["movies"], places["videos"]
        )
    assert stray.exists() and places["film"].exists() and not (places["tmp"] / "Out").exists()


def test_never_a_folder_a_link_or_what_isnt_a_film(places: dict[str, Path]) -> None:
    (places["movies"] / "A Folder.mkv").mkdir()
    with pytest.raises(UserError):
        move(places, places["movies"] / "A Folder.mkv", "adult")
    notes = places["movies"] / "notes.txt"
    notes.write_text("x")
    with pytest.raises(UserError):
        move(places, notes, "adult")
    hidden = places["movies"] / ".hidden.mkv"
    hidden.write_bytes(b"x")
    with pytest.raises(UserError):
        move(places, hidden, "adult")
    outside = places["tmp"] / "elsewhere.mkv"
    outside.write_bytes(b"theirs")
    link = places["movies"] / "Linked.mkv"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("this computer can't make links")
    with pytest.raises(OutsideLibraryError):
        move(places, link, "adult")
    assert outside.exists() and link.is_symlink()
    with pytest.raises(UserError):
        fileops.move_media(
            places["videos"], places["movies"] / "18+", places["movies"], places["videos"]
        )
    with pytest.raises(NotFoundError):
        move(places, places["movies"] / "Gone.mkv", "adult")


def test_never_inside_a_library_or_a_source(places: dict[str, Path]) -> None:
    library = places["movies"] / "My Library"
    (library / "Music" / "Videos").mkdir(parents=True)
    song_video = library / "Music" / "Videos" / "Song.mp4"
    song_video.write_bytes(b"the library's")
    with pytest.raises(OutsideLibraryError):
        move(places, song_video, "movie", forbidden=[library])
    with pytest.raises(OutsideLibraryError):
        fileops.move_media(
            places["film"], library / "Music", places["movies"], places["videos"],
            forbidden=[library],
        )  # fmt: skip
    assert song_video.exists() and places["film"].exists()


def test_to_another_drive_it_is_copied_then_the_first_goes_to_the_trash(
    places: dict[str, Path], fake_trash: FakeTrash, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_replace = fileops._replace
    film = os.path.realpath(places["film"])

    def across(src: Path, dst: Path) -> None:
        if os.path.realpath(src) == film:
            raise CrossVolumeError("they're on different drives")
        real_replace(src, dst)

    monkeypatch.setattr(fileops, "_replace", across)
    moved = move(places, places["film"], "video")
    assert moved.read_bytes() == b"picture and sound"
    assert fake_trash.sent == [Path(film)] and not places["film"].exists()
    assert [p.name for p in places["videos"].iterdir()] == [moved.name]  # no temp file left


def test_to_another_drive_a_trash_that_fails_takes_the_copy_away(
    places: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    real_replace = fileops._replace
    film = os.path.realpath(places["film"])

    def across(src: Path, dst: Path) -> None:
        if os.path.realpath(src) == film:
            raise CrossVolumeError("they're on different drives")
        real_replace(src, dst)

    def refuse(path: Path) -> None:
        raise OSError("no Trash on that drive")

    monkeypatch.setattr(fileops, "_replace", across)
    monkeypatch.setattr(fileops, "_send_to_trash", refuse)
    with pytest.raises(FileOperationError, match="taken away again"):
        move(places, places["film"], "video")
    assert places["film"].read_bytes() == b"picture and sound"
    assert list(places["videos"].iterdir()) == []


def test_a_rename_that_fails_leaves_the_film_and_no_name_reserved(
    places: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(src: Path, dst: Path) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(fileops, "_replace", broken)
    with pytest.raises(FileOperationError, match="still where it was"):
        move(places, places["film"], "adult")
    assert places["film"].exists()
    assert list((places["movies"] / "18+").iterdir()) == []


# ---- media.move ----------------------------------------------------------------------------


def test_media_move_by_its_path(opened: rpc.Server, tmp_path: Path) -> None:  # noqa: F811
    movies = tmp_path / "Films"
    (movies / "Clips").mkdir(parents=True)
    result(opened, "settings.set", movies_folder=str(movies), videos_folder=str(movies / "Clips"))
    result(opened, "kids.set", on=False)
    film = movies / "A Show S02E03.mp4"
    film.write_bytes(b"x")
    assert code(opened, "media.move", path=str(film)) == rpc.INVALID_PARAMS
    assert code(opened, "media.move", path=str(film), to="music") == rpc.INVALID_PARAMS
    moved = result(opened, "media.move", path=str(film), to="series")["path"]
    assert moved == str(movies / "Series" / "A Show" / "Season 2" / film.name)
    there = result(opened, "media.move", path=moved, to="adult")["path"]
    assert there == str(movies / "18+" / film.name)
    clip = result(opened, "media.move", path=there, to="video")["path"]
    assert clip == str(movies / "Clips" / film.name) and Path(clip).exists()
    assert code(opened, "media.move", path=str(film), to="movie") == rpc.NOT_FOUND
    assert "media.move" in rpc.SLOW_METHODS


def test_media_move_nothing_for_adults_in_a_childs_profile(
    opened: rpc.Server,  # noqa: F811
    tmp_path: Path,
) -> None:
    movies = tmp_path / "Films"
    (movies / "18+").mkdir(parents=True)
    result(opened, "settings.set", movies_folder=str(movies))
    film = movies / "A Film (2000).mp4"
    film.write_bytes(b"x")
    theirs = movies / "18+" / "Theirs (2001).mp4"
    theirs.write_bytes(b"y")
    # Before the app has said which profile it is, as in a child's.
    assert code(opened, "media.move", path=str(film), to="adult") == rpc.USER_ERROR
    result(opened, "kids.set", on=True)
    assert code(opened, "media.move", path=str(film), to="adult") == rpc.USER_ERROR
    assert code(opened, "media.move", path=str(theirs), to="movie") == rpc.USER_ERROR
    assert film.exists() and theirs.exists()
    # Anything else is moved as usual.
    assert result(opened, "media.move", path=str(film), to="anime")["path"] == str(
        movies / "Anime" / film.name
    )


def test_media_move_never_takes_a_library_file(
    opened: rpc.Server,  # noqa: F811
    root: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    result(opened, "settings.set", movies_folder=str(tmp_path))
    result(opened, "kids.set", on=False)
    video = root / "Music" / "Videos" / "Artist" / "Song.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"the library's")
    assert code(opened, "media.move", path=str(video), to="movie") == rpc.OUTSIDE
    assert video.exists()


# ---- fileops.forget_cached_folder ------------------------------------------------------------


def test_a_cached_folder_is_cleared_and_nothing_else(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    work = cache / "fetched" / "p-1.page"
    (work / "inner").mkdir(parents=True)
    (work / "video.f1.mp4.part").write_bytes(b"part")
    (work / "inner" / "x").write_bytes(b"x")
    outside = tmp_path / "keep.txt"
    outside.write_text("mine")
    try:
        (work / "link").symlink_to(outside)
    except OSError:
        pass
    fileops.forget_cached_folder(work, cache=cache)
    assert not work.exists() and outside.read_text() == "mine"
    assert (cache / "fetched").is_dir()
    with pytest.raises(OutsideLibraryError):
        fileops.forget_cached_folder(cache, cache=cache)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(OutsideLibraryError):
        fileops.forget_cached_folder(elsewhere, cache=cache)
    assert elsewhere.is_dir()
