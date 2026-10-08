"""Keeping a film: the one writer that puts a fetched file in the Movies folder, and the
torrent player's part in it. Everything is under the test's own folders."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from musicorg import config, fileops, torrents
from musicorg.errors import FileOperationError, OutsideLibraryError


@pytest.fixture
def places(tmp_path: Path) -> dict[str, Path]:
    cache = tmp_path / "cache" / "torrents"
    (cache / "Film").mkdir(parents=True)
    film = cache / "Film" / "film.MKV"
    film.write_bytes(b"picture and sound" * 1000)
    movies = tmp_path / "Movies"
    movies.mkdir()
    return {"cache": cache, "film": film, "movies": movies, "tmp": tmp_path}


def keep(places: dict[str, Path], name: str = "The Kid (1921).mkv", **more: Any) -> Path:
    options: dict[str, Any] = {"cache": places["cache"], "allowed": [places["movies"]]}
    options.update(more)
    folder = options.pop("folder", places["movies"])
    return fileops.keep_media(places["film"], folder, name, **options)


def test_a_film_is_copied_whole_and_the_cache_is_left_alone(places: dict[str, Path]) -> None:
    saved = keep(places)
    assert saved == places["movies"] / "The Kid (1921).mkv"
    assert saved.read_bytes() == places["film"].read_bytes()
    assert places["film"].exists()
    assert [p.name for p in places["movies"].iterdir()] == ["The Kid (1921).mkv"]  # no temp left


def test_it_never_overwrites(places: dict[str, Path]) -> None:
    (places["movies"] / "The Kid (1921).mkv").write_bytes(b"the owner's own copy")
    saved = keep(places)
    assert saved.name == "The Kid (1921) (2).mkv"
    assert (places["movies"] / "The Kid (1921).mkv").read_bytes() == b"the owner's own copy"
    assert keep(places).name == "The Kid (1921) (3).mkv"


def test_the_name_is_made_safe_and_its_ending_lower_case(places: dict[str, Path]) -> None:
    saved = keep(places, name="What/If: Part 2?.MKV")
    assert saved.parent == places["movies"] and saved.suffix == ".mkv"
    assert "/" not in saved.name and ":" not in saved.name
    for wrong in ("", ".mkv", ".DS_Store"):
        with pytest.raises(ValueError):
            keep(places, name=wrong)


def test_only_a_file_from_the_apps_cache_is_ever_copied(places: dict[str, Path]) -> None:
    outside = places["tmp"] / "somebody's film.mkv"
    outside.write_bytes(b"x")
    with pytest.raises(OutsideLibraryError):
        fileops.keep_media(
            outside, places["movies"], "a.mkv", cache=places["cache"], allowed=[places["movies"]]
        )
    with pytest.raises(OutsideLibraryError):  # a folder isn't a film
        fileops.keep_media(
            places["cache"] / "Film", places["movies"], "a.mkv",
            cache=places["cache"], allowed=[places["movies"]],
        )  # fmt: skip
    assert list(places["movies"].iterdir()) == []


def test_only_into_the_places_kept_media_goes(places: dict[str, Path]) -> None:
    elsewhere = places["tmp"] / "Desktop"
    elsewhere.mkdir()
    with pytest.raises(OutsideLibraryError):
        keep(places, folder=elsewhere)
    with pytest.raises(OutsideLibraryError):  # the folder above the allowed one
        keep(places, folder=places["tmp"])
    # A folder inside the allowed one is fine, and is made.
    saved = keep(places, folder=places["movies"] / "Old Films" / "Silent")
    assert saved.parent == places["movies"] / "Old Films" / "Silent"
    # But never inside a library, even if that were allowed by mistake.
    with pytest.raises(OutsideLibraryError):
        keep(places, folder=places["movies"] / "Library", forbidden=[places["movies"] / "Library"])
    assert list(elsewhere.iterdir()) == []


def test_the_allowed_folder_is_made_only_where_its_parent_exists(places: dict[str, Path]) -> None:
    media = places["tmp"] / "Downloads" / "Media"
    with pytest.raises(FileOperationError):  # no Downloads folder: it isn't invented
        keep(places, folder=media, allowed=[media])
    (places["tmp"] / "Downloads").mkdir()
    assert keep(places, folder=media / "News", allowed=[media]).parent == media / "News"


def test_a_copy_that_fails_leaves_nothing_behind(
    places: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(src: Path, dst: Path) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fileops, "_replace", fail)
    with pytest.raises(FileOperationError, match="No space left"):
        keep(places)
    assert list(places["movies"].iterdir()) == []


def test_the_places_are_inside_the_tests_own_folder() -> None:
    home = config.app_dirs().config.parent
    assert config.media_folders() == {
        "movies": home / "Movies", "media": home / "Movies" / "Videos"}  # fmt: skip


class Handle:
    """Stands in for libtorrent's handle on a torrent whose film has all arrived."""

    def __init__(self, size: int) -> None:
        self.size = size

    def is_valid(self) -> bool:
        return True

    def file_progress(self) -> list[int]:
        return [0, self.size]

    def status(self) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(progress_ppm=500_000, is_seeding=False, num_peers=3, download_rate=0)


def joined_film(places: dict[str, Path], player: torrents.Player, *, here: bool = True) -> Any:
    size = places["film"].stat().st_size
    joined = torrents._Joined(Handle(size if here else size - 1), "ab" * 20, 1, places["cache"])
    joined.index, joined.size, joined.path = 1, size, places["film"]
    joined.name = places["film"].name
    player._joined[joined.info_hash] = joined
    return joined


def test_a_kept_film_is_saved_when_it_has_all_arrived(places: dict[str, Path]) -> None:
    player = torrents.Player(places["cache"].parent, movies=places["movies"])
    joined = joined_film(places, player, here=False)
    joined.keep_name = "The Kid (1921)"
    assert joined.wanted and not joined.all_here()
    assert joined.status()["keeping"] is True

    # The player lets go of it: it's still wanted, so it isn't left.
    player.close(joined.info_hash)
    assert joined.info_hash in player._joined and joined.playing is False

    joined.handle = Handle(joined.size)
    assert joined.all_here()
    player._save_kept(joined)
    assert joined.kept_path == str(places["movies"] / "The Kid (1921).mkv")
    assert Path(joined.kept_path).read_bytes() == places["film"].read_bytes()
    said = joined.status()
    assert said["keeping"] is False and said["kept_path"] == joined.kept_path
    assert joined.info_hash not in player._joined  # nobody was watching: it's left


def test_a_film_still_being_watched_stays_after_its_saved(places: dict[str, Path]) -> None:
    player = torrents.Player(places["cache"].parent, movies=places["movies"])
    joined = joined_film(places, player)
    joined.keep_name = "Sintel"
    player._save_kept(joined)
    assert joined.kept_path and joined.info_hash in player._joined


def test_a_film_that_cant_be_saved_says_why(places: dict[str, Path]) -> None:
    player = torrents.Player(places["cache"].parent, movies=places["tmp"] / "No" / "Movies")
    joined = joined_film(places, player)
    joined.keep_name = "Sintel"
    player._save_kept(joined)
    assert joined.kept_path is None and joined.keep_error
    assert joined.wanted is False and joined.status()["keep_error"] == joined.keep_error


def test_keeping_needs_somewhere_to_keep_and_a_name(places: dict[str, Path]) -> None:
    with pytest.raises(torrents.TorrentError):
        torrents.Player(places["cache"].parent).keep("ab" * 20, None, [], "Sintel")
    with pytest.raises(torrents.TorrentError):
        torrents.Player(places["cache"].parent, movies=places["movies"]).keep(
            "ab" * 20, None, [], "  "
        )


class Files:
    def num_files(self) -> int:
        return 2

    def file_path(self, i: int) -> str:
        return ["Film/notes.txt", "Film/film.MKV"][i]

    def file_size(self, i: int) -> int:
        return [10, 17000][i]

    def file_offset(self, i: int) -> int:
        return [0, 10][i]


class Arriving(Handle):
    """A torrent whose details arrive on the second look."""

    def __init__(self) -> None:
        super().__init__(17000)
        self.looks = 0
        self.priorities: list[int] = []

    def torrent_file(self) -> Any:
        from types import SimpleNamespace

        self.looks += 1
        if self.looks < 2:
            return None
        return SimpleNamespace(files=lambda: Files(), piece_length=lambda: 16384)

    def prioritize_files(self, priorities: list[int]) -> None:
        self.priorities = priorities


def test_a_films_details_are_read_even_with_no_time_to_wait(places: dict[str, Path]) -> None:
    handle = Arriving()
    joined = torrents._Joined(handle, "ab" * 20, None, places["cache"])
    assert joined.ready(0) is False  # not arrived yet: it doesn't wait
    assert joined.ready(0) is True  # arrived: read at once, with no waiting allowed
    assert joined.path == places["cache"] / "Film" / "film.MKV" and joined.index == 1
    assert handle.priorities == [0, 4]  # only the film is fetched


# ---- a film's day in the cache (the owner, 2026-10-08) -----------------------------------

DAY = torrents.KEEP_S


def aged(path: Path, seconds: float) -> None:
    import os
    import time

    then = time.time() - seconds
    os.utime(path, (then, then))


def test_what_was_played_over_a_day_ago_is_cleared_and_nothing_else(
    places: dict[str, Path],
) -> None:
    cache, app_cache = places["cache"], places["cache"].parent
    recent = cache / "Recent"
    recent.mkdir()
    (recent / "film.mp4").write_bytes(b"new")
    lone = cache / "single.mkv"
    lone.write_bytes(b"old")
    (cache / "Film" / "Subs").mkdir()
    (cache / "Film" / "Subs" / "en.srt").write_bytes(b"1")
    beside = app_cache / "yt-dlp"
    beside.mkdir()
    for old in (places["film"], cache / "Film" / "Subs" / "en.srt", cache / "Film" / "Subs",
                cache / "Film", lone, beside):  # fmt: skip
        aged(old, DAY + 60)
    # A folder is as new as the newest thing in it.
    aged(recent, DAY + 60)

    assert torrents.sweep(app_cache) == 2
    assert sorted(p.name for p in cache.iterdir()) == ["Recent"]
    assert beside.is_dir()  # only the films' folder is swept

    # One that's open now stays whatever its age.
    aged(recent / "film.mp4", DAY * 3)
    aged(recent, DAY * 3)
    assert torrents.sweep(app_cache, skip=["Recent"]) == 0
    assert torrents.sweep(app_cache) == 1 and list(cache.iterdir()) == []


def test_the_sweep_never_leaves_the_apps_cache(places: dict[str, Path], tmp_path: Path) -> None:
    outside = tmp_path / "Music"
    outside.mkdir()
    (outside / "song.m4a").write_bytes(b"x")
    aged(outside / "song.m4a", DAY * 9)
    app_cache = places["cache"].parent
    for wrong in (outside, app_cache, tmp_path):
        with pytest.raises(OutsideLibraryError):
            fileops.sweep_cached(wrong, cache=app_cache, older_than_s=1)
    with pytest.raises(OutsideLibraryError):
        fileops.touch_cached(outside / "song.m4a", cache=app_cache)
    # A link out of the cache is removed, not followed.
    link = places["cache"] / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("links can't be made here")
    import os

    os.utime(
        link, (1, 1), follow_symlinks=False
    ) if os.utime in os.supports_follow_symlinks else None
    fileops.sweep_cached(places["cache"], cache=app_cache, older_than_s=0, now=4_000_000_000)
    assert not link.is_symlink() and (outside / "song.m4a").exists()
    assert fileops.sweep_cached(app_cache / "nothing", cache=app_cache, older_than_s=0) == 0


class Session:
    def __init__(self) -> None:
        self.removed: list[tuple[Any, ...]] = []

    def remove_torrent(self, *given: Any) -> None:
        self.removed.append(given)


def test_a_film_thats_closed_stays_a_day_and_one_saved_to_movies_goes_at_once(
    places: dict[str, Path],
) -> None:
    import libtorrent as lt

    player = torrents.Player(places["cache"].parent, movies=places["movies"])
    player._session = session = Session()
    joined = joined_film(places, player)
    joined.top = "Film"
    aged(places["cache"] / "Film", DAY * 2)
    aged(places["film"], DAY * 2)

    player.close(joined.info_hash)
    # The torrent is left, its files are not deleted, and its day starts now.
    assert session.removed == [(joined.handle,)]
    assert places["film"].exists() and player.sweep() == 0

    # Played again and saved to the Movies folder: the cache's copy isn't needed.
    again = joined_film(places, player)
    again.top, again.keep_name, again.playing = "Film", "Sintel", False
    player._save_kept(again)
    assert session.removed[-1] == (again.handle, lt.session.delete_files)

    # The engine stopping leaves a film without deleting it either.
    last = joined_film(places, player)
    last.top = "Film"
    player.stop()
    assert session.removed[-1] == (last.handle,)


def test_keeping_can_be_stopped(places: dict[str, Path]) -> None:
    player = torrents.Player(places["cache"].parent, movies=places["movies"])
    joined = joined_film(places, player, here=False)
    joined.keep_name = "The Kid (1921)"
    # While it plays, stopping the keep leaves it playing.
    player.stop_keeping(joined.info_hash, playing=True)
    assert joined.info_hash in player._joined and not joined.wanted
    assert joined.status()["keeping"] is False
    # Nobody watching: it's left at once (found with a real torrent, which stayed joined
    # ten minutes more), and what arrived stays in the cache its day.
    joined.keep_name = "The Kid (1921)"
    assert joined.playing  # as a film that's only being kept is marked, too
    player.stop_keeping(joined.info_hash.upper())
    assert joined.info_hash not in player._joined
    assert places["film"].is_file() and list(places["movies"].iterdir()) == []
    # One that isn't joined, or isn't being kept: nothing happens.
    player.stop_keeping(joined.info_hash)
    other = joined_film(places, player, here=False)
    player.stop_keeping(other.info_hash)
    assert other.info_hash in player._joined
