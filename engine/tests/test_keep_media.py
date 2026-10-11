"""Keeping a film: the one writer that puts a fetched file in the Movies folder, and the
torrent player's part in it. Everything is under the test's own folders."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from test_rpc import Capture, code, opened, out, result, root, server  # noqa: F401  (fixtures)

from musicorg import config, fileops, naming, rpc, torrents
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

        return SimpleNamespace(
            progress_ppm=500_000, is_seeding=False, num_peers=3, num_seeds=2, download_rate=0
        )


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
    # Who has it is the engine's own count, of those it's connected to: no add-on says.
    assert (joined.status()["peers"], joined.status()["seeds"]) == (3, 2)

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


# ---- episodes of a series and anime, each in its show's folder (2026-10-08) --------------


def test_an_episode_is_kept_in_its_shows_folder(places: dict[str, Path]) -> None:
    player = torrents.Player(places["cache"].parent, movies=places["movies"])
    folder, name = naming.kept_place("series", "Pilot", show="A Show", season=1, episode=2)
    for count in ("", " (2)"):  # kept twice: the second never takes the first one's place
        joined = joined_film(places, player)
        joined.keep_name, joined.keep_folder = name, folder
        player._save_kept(joined)
        assert joined.keep_error is None
        assert joined.kept_path == str(
            places["movies"]
            / "Series"
            / "A Show"
            / "Season 1"
            / f"A Show S01E02 - Pilot{count}.mkv"
        )
        assert Path(joined.kept_path).read_bytes() == places["film"].read_bytes()
    # A film goes where films always went: loose in the Movies folder.
    joined = joined_film(places, player)
    joined.keep_name = "The Kid (1921)"
    player._save_kept(joined)
    assert joined.kept_path == str(places["movies"] / "The Kid (1921).mkv")
    assert sorted(p.name for p in places["movies"].iterdir()) == ["Series", "The Kid (1921).mkv"]


class Keeper:
    """Stands in for the torrent player: what it was asked to keep, and where."""

    def __init__(self, movies: Path) -> None:
        self.movies = movies
        self.asked: list[tuple[str, tuple[str, ...]]] = []

    def keep(
        self, info_hash: str, file_index: Any, trackers: Any, name: str, **more: Any
    ) -> dict[str, Any]:
        self.asked.append((name, more["folder"]))
        return {}

    def stop(self) -> None:
        pass


def test_the_app_says_what_its_keeping(opened: rpc.Server, places: dict[str, Path]) -> None:  # noqa: F811
    opened._films = keeper = Keeper(places["movies"])  # type: ignore[assignment]
    film = {"info_hash": "ab" * 20}
    result(opened, "torrent.keep", title="The Kid", year="1921", **film)
    result(opened, "torrent.keep", title="The Kid", kind="movie", **film)
    result(opened, "torrent.keep", title="Pilot", kind="series", show="A Show", season=1,
           episode=2, **film)  # fmt: skip
    result(
        opened, "torrent.keep", title="", kind="anime", show="A Show", season=1, episode=2, **film
    )
    result(opened, "torrent.keep", title="A Film", year="2001", kind="anime", **film)
    assert keeper.asked == [
        ("The Kid (1921)", ()),
        ("The Kid", ()),
        ("A Show S01E02 - Pilot", ("Series", "A Show", "Season 1")),
        ("A Show S01E02", ("Anime", "A Show", "Season 1")),
        ("A Film (2001)", ("Anime",)),
    ]
    for wrong in (
        {"title": "A Film", "kind": "cartoon"},  # not a kind
        {"title": "Pilot", "show": "A Show"},  # a show, and not said which kind
        {"title": "Pilot", "kind": "series", "season": 1, "episode": 2},  # of what?
        {"title": "Pilot", "kind": "series", "show": "A Show", "season": -1},
        {"title": "Pilot", "kind": "series", "show": "A Show", "episode": True},
        {"title": "Pilot", "kind": "series", "show": "A Show", "season": "1"},
        {"title": " ", "kind": "anime"},
        {"title": " "},
    ):
        assert code(opened, "torrent.keep", **film, **wrong) == rpc.INVALID_PARAMS, wrong
    assert len(keeper.asked) == 5
    opened._films = None


# ---- counting who has a torrent, from the owner's click (2026-10-11) -------------------------


class Counted:
    """A torrent as libtorrent would report it, for a count: who it finds, look by look."""

    def __init__(self, looks: list[dict[str, int]]) -> None:
        self.looks, self.asked = looks, 0

    def is_valid(self) -> bool:
        return True

    def status(self) -> Any:
        from types import SimpleNamespace

        look = self.looks[min(self.asked, len(self.looks) - 1)]
        self.asked += 1
        return SimpleNamespace(**look)


class CountingSession(Session):
    def __init__(self, handle: Counted) -> None:
        super().__init__()
        self.handle, self.added = handle, []

    def add_torrent(self, params: Any) -> Counted:
        self.added.append(params)
        return self.handle


def test_who_has_a_torrent_is_the_most_that_was_found() -> None:
    from types import SimpleNamespace as Told

    assert torrents.who_has(Told(num_seeds=2, num_peers=6)) == (2, 6)  # connected to
    # Heard of, from other peers and the DHT, where that's more.
    assert torrents.who_has(Told(num_seeds=2, num_peers=6, list_seeds=25, list_peers=30)) == (
        25,
        30,
    )
    # A tracker's own count, where one answered: 25 with all of it, 5 without.
    assert torrents.who_has(Told(num_seeds=1, num_peers=1, num_complete=25, num_incomplete=5)) == (
        25,
        30,
    )
    # "Not known" is -1 from a tracker: nothing, not one fewer.
    assert torrents.who_has(Told(num_seeds=0, num_peers=0, num_complete=-1, num_incomplete=-1)) == (
        0,
        0,
    )
    assert torrents.who_has(Told()) == (0, 0)
    assert torrents.who_has(Told(num_seeds="many", num_peers=True, list_peers=None)) == (0, 0)
    assert torrents.who_has(Told(num_seeds=9, num_peers=4)) == (9, 9)  # never fewer than its seeds


def test_a_torrent_is_counted_without_fetching_any_of_it(
    places: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import libtorrent as lt

    monkeypatch.setattr(torrents, "COUNT_S", 0.3)
    monkeypatch.setattr(torrents, "COUNT_CHECK_S", 0.05)
    handle = Counted([{"num_seeds": 0, "num_peers": 0},
                      {"num_seeds": 3, "num_peers": 8, "list_seeds": 25, "list_peers": 30},
                      {"num_seeds": 2, "num_peers": 4}])  # fmt: skip
    session = CountingSession(handle)
    monkeypatch.setattr(torrents, "_new_session", lambda: session)
    player = torrents.Player(places["cache"].parent, movies=places["movies"])
    before = sorted(p.name for p in places["cache"].rglob("*"))
    try:
        said = player.count("AB" * 20, ["udp://t.example:80"])
        # The most it found while it looked: 25 with all of it, 5 others.
        assert said == {"info_hash": "ab" * 20, "seeds": 25, "peers": 30}
        (params,) = session.added
        # Told to fetch nothing, and given an empty folder of its own, not the films'.
        assert params.flags & lt.torrent_flags.upload_mode
        assert Path(params.save_path) == places["cache"] / torrents.COUNTING
        # Left when the count was in, with nothing deleted and nothing of it remembered.
        assert session.removed == [(handle,)]
        assert player._joined == {} and player._counting == set()
        assert sorted(p.name for p in places["cache"].rglob("*")) == before
        with pytest.raises(torrents.TorrentError):
            player.count("not a hash", [])
        assert len(session.added) == 1
    finally:
        player.stop()


def test_a_film_thats_open_is_counted_as_it_stands(
    places: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    session = CountingSession(Counted([{}]))
    monkeypatch.setattr(torrents, "_new_session", lambda: session)
    player = torrents.Player(places["cache"].parent, movies=places["movies"])
    joined = joined_film(places, player)  # 3 connected to, 2 of them with all of it
    assert player.count(joined.info_hash, []) == {
        "info_hash": joined.info_hash, "seeds": 2, "peers": 3,
    }  # fmt: skip
    assert session.added == [] and session.removed == []  # nothing joined, nothing left
    assert joined.info_hash in player._joined


def test_pressing_play_on_a_torrent_being_counted_ends_the_count(
    places: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(torrents, "COUNT_S", 30.0)  # it would count for half a minute
    monkeypatch.setattr(torrents, "COUNT_CHECK_S", 0.05)
    handle = Counted([{"num_seeds": 4, "num_peers": 6}])
    session = CountingSession(handle)
    monkeypatch.setattr(torrents, "_new_session", lambda: session)
    player = torrents.Player(places["cache"].parent, movies=places["movies"])
    said: list[dict[str, Any]] = []
    counting = threading.Thread(target=lambda: said.append(player.count("ab" * 20, [])))
    try:
        counting.start()
        for _ in range(100):
            if "ab" * 20 in player._counting:
                break
            time.sleep(0.02)
        assert "ab" * 20 in player._counting
        with pytest.raises(torrents.TorrentError, match="counted already"):
            player.count("ab" * 20, [])
        started = time.monotonic()
        playing = player.play("ab" * 20, None, [])
        assert time.monotonic() - started < 5  # not the half minute
        counting.join(5)
        # The count gave what it had and left its torrent; the film was joined afresh,
        # in the films' own folder and free to fetch.
        assert said == [{"info_hash": "ab" * 20, "seeds": 4, "peers": 6}]
        assert session.removed == [(handle,)] and len(session.added) == 2
        assert Path(session.added[1].save_path) == places["cache"]
        assert playing["info_hash"] == "ab" * 20 and "ab" * 20 in player._joined
        assert player._counting == set() and player._hurry == set()
    finally:
        player.stop()


def test_the_app_asks_for_a_count(opened: rpc.Server, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    assert code(opened, "torrent.count") == rpc.INVALID_PARAMS
    assert code(opened, "torrent.count", info_hash="ab" * 20, trackers=[1]) == rpc.INVALID_PARAMS
    assert code(opened, "torrent.count", info_hash="not a torrent's id") == rpc.USER_ERROR
    assert "torrent.count" in rpc.SLOW_METHODS  # it takes seconds: nothing else waits behind it
    # Asked properly, the engine's player counts and says what it found.
    monkeypatch.setattr(torrents, "COUNT_S", 0.2)
    monkeypatch.setattr(torrents, "COUNT_CHECK_S", 0.05)
    session = CountingSession(Counted([{"num_seeds": 25, "num_peers": 30}]))
    monkeypatch.setattr(torrents, "_new_session", lambda: session)
    try:
        said = result(opened, "torrent.count", info_hash="AB" * 20)
        assert said == {"info_hash": "ab" * 20, "seeds": 25, "peers": 30}
        assert len(session.added) == 1 and len(session.removed) == 1
    finally:
        if opened._films is not None:
            opened._films.stop()
            opened._films = None
