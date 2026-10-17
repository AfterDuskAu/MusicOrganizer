"""What libtorrent knows of a film that was left, kept beside it in the app's cache
(2026-10-17), so a film played again within its day is all there at once instead of
checked from 0% ("here: 5%", then 7, 10, 20, 100, the owner saw); and the one writer
that saves it, `fileops.write_cached`. No torrent is ever joined: a torrent's details
are made here from a file of the test's own, and libtorrent only reads and writes them."""

from __future__ import annotations

import errno
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from musicorg import fileops, torrents
from musicorg.errors import FileOperationError, OutsideLibraryError

# Making a torrent here uses libtorrent's older calls, the plainest ones for a test.
pytestmark = pytest.mark.filterwarnings("ignore:.*deprecated:DeprecationWarning")

# ---- fileops.write_cached ------------------------------------------------------------------


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    (tmp_path / "cache" / "torrents").mkdir(parents=True)
    return tmp_path / "cache"


def test_a_note_is_saved_in_the_cache_and_replaced_whole(cache: Path) -> None:
    note = cache / "torrents" / ".abc.resume"
    assert fileops.write_cached(note, b"first", cache=cache) == note
    assert note.read_bytes() == b"first"
    fileops.write_cached(note, b"second, longer", cache=cache)
    assert note.read_bytes() == b"second, longer"
    assert [p.name for p in note.parent.iterdir()] == [".abc.resume"]  # no temp file left


def test_a_note_that_cant_be_put_in_place_leaves_the_old_one_whole(
    cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = cache / "torrents" / ".abc.resume"
    fileops.write_cached(note, b"what was here", cache=cache)

    def fails(src: Path, dst: Path) -> None:  # the crash between writing and renaming
        raise OSError(errno.EIO, "the disk went away")

    monkeypatch.setattr(fileops, "_replace", fails)
    with pytest.raises(FileOperationError, match="the disk went away"):
        fileops.write_cached(note, b"half of something new", cache=cache)
    assert note.read_bytes() == b"what was here"
    assert [p.name for p in note.parent.iterdir()] == [".abc.resume"]


def test_a_note_written_but_not_yet_renamed_is_never_read_as_the_note(
    cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The crash after the temp file is written, before it's renamed: what's under the
    # note's name is still the old note (or nothing), and the temp file goes.
    note = cache / "torrents" / ".abc.resume"
    real = fileops._sync_file

    def dies(fd: int) -> None:
        real(fd)
        raise KeyboardInterrupt  # the engine stopped right there

    monkeypatch.setattr(fileops, "_sync_file", dies)
    with pytest.raises(KeyboardInterrupt):
        fileops.write_cached(note, b"new", cache=cache)
    assert not note.exists() and list(note.parent.iterdir()) == []


def test_a_note_is_written_only_directly_in_a_real_folder_of_the_apps_cache(
    cache: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "Music"
    elsewhere.mkdir()
    for wrong in (elsewhere / ".x.resume", cache / ".x.resume", tmp_path / ".x.resume",
                  cache / "torrents" / "missing" / ".x.resume",
                  cache / "torrents" / ".." / ".." / ".x.resume"):  # fmt: skip
        with pytest.raises(OutsideLibraryError):
            fileops.write_cached(wrong, b"x", cache=cache)
    for junk in (".DS_Store", "._x.resume", ""):
        with pytest.raises((ValueError, OutsideLibraryError)):
            fileops.write_cached(cache / "torrents" / junk, b"x", cache=cache)
    # Something else in the note's place: a folder, a link to the owner's own file.
    (cache / "torrents" / ".dir.resume").mkdir()
    with pytest.raises(OutsideLibraryError):
        fileops.write_cached(cache / "torrents" / ".dir.resume", b"x", cache=cache)
    precious = elsewhere / "song.m4a"
    precious.write_bytes(b"the owner's own")
    try:
        (cache / "torrents" / ".link.resume").symlink_to(precious)
        (cache / "torrents" / "away").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        return  # Windows without the right to make links: nothing to follow
    with pytest.raises(OutsideLibraryError):
        fileops.write_cached(cache / "torrents" / ".link.resume", b"x", cache=cache)
    with pytest.raises(OutsideLibraryError):  # a folder of the cache that's a link out of it
        fileops.write_cached(cache / "torrents" / "away" / ".x.resume", b"x", cache=cache)
    assert precious.read_bytes() == b"the owner's own" and sorted(os.listdir(elsewhere)) == [
        "song.m4a"
    ]


# ---- a film's state, read back when it's joined again ---------------------------------------


def a_torrent(where: Path) -> Any:
    """A real torrent's details, of a two-file "show" made here (nothing is shared)."""
    import libtorrent as lt

    show = where / "made" / "Show"
    show.mkdir(parents=True)
    (show / "E1.mkv").write_bytes(os.urandom(16384 * 6))
    (show / "notes.txt").write_bytes(b"n" * 100)
    files = lt.file_storage()
    lt.add_files(files, str(show))
    made = lt.create_torrent(files, 16384)
    lt.set_piece_hashes(made, str(where / "made"))
    return lt.torrent_info(made.generate())


def saved_state(info: Any, *, priorities: list[int] | None = None) -> bytes:
    """What libtorrent writes of a torrent it's left: here, for one in upload mode."""
    import libtorrent as lt

    params = lt.add_torrent_params()
    params.ti = info
    params.save_path = "/somewhere/else"
    params.flags |= lt.torrent_flags.upload_mode | lt.torrent_flags.sequential_download
    params.trackers = ["udp://kept.example:80"]
    if priorities is not None:
        params.file_priorities = priorities
    return lt.write_resume_data_buf(params)


def test_a_film_played_again_is_joined_as_it_was_left(cache: Path, tmp_path: Path) -> None:
    import libtorrent as lt

    info = a_torrent(tmp_path)
    info_hash = str(info.info_hashes().v1)
    player = torrents.Player(cache)
    (player.folder / "Show").mkdir()  # its files are still in the cache
    fileops.write_cached(player.folder / f".{info_hash}.resume", saved_state(info), cache=cache)

    params = player._params(info_hash, torrents.magnet(info_hash, ["udp://t.example:80"]))
    # Its details at once (no waiting for them from the others), in the films' folder.
    assert params.ti is not None and str(params.ti.info_hashes().v1) == info_hash
    assert params.save_path == str(player.folder)
    assert params.trackers == ["udp://kept.example:80", "udp://t.example:80"]
    # Never left in a count's upload mode, never paused, never libtorrent's "in order".
    for flag in ("upload_mode", "paused", "sequential_download"):
        assert not params.flags & getattr(lt.torrent_flags, flag), flag


def test_what_was_noted_is_used_only_while_its_film_is_still_there(
    cache: Path, tmp_path: Path
) -> None:
    info = a_torrent(tmp_path)
    info_hash = str(info.info_hashes().v1)
    player = torrents.Player(cache)
    note = player.folder / f".{info_hash}.resume"
    magnet = torrents.magnet(info_hash)

    def joined_afresh() -> bool:
        return player._params(info_hash, magnet).ti is None and not note.exists()

    # Its film has gone from the cache (swept after its day): joined afresh, note gone.
    fileops.write_cached(note, saved_state(info), cache=cache)
    assert joined_afresh()
    # A file of it wasn't fetched, and libtorrent's file of the pieces it shares with the
    # others has gone: pieces said to be here couldn't be read. Afresh.
    (player.folder / "Show").mkdir()
    fileops.write_cached(note, saved_state(info, priorities=[4, 0]), cache=cache)
    assert joined_afresh()
    (player.folder / f".{info_hash}.parts").write_bytes(b"pieces")
    fileops.write_cached(note, saved_state(info, priorities=[4, 0]), cache=cache)
    assert player._params(info_hash, magnet).ti is not None
    # Not this torrent's, or not anything libtorrent can read: afresh.
    other = "cd" * 20
    (player.folder / f".{other}.resume").write_bytes(saved_state(info))
    assert player._params(other, torrents.magnet(other)).ti is None
    assert not (player.folder / f".{other}.resume").exists()
    note.write_bytes(b"not what libtorrent writes")
    assert joined_afresh()


# ---- a film's state, saved when it's left -------------------------------------------------


class Leaving:
    """libtorrent's handle on a torrent being left, saying back as libtorrent does."""

    def __init__(self, *, writes: bool = True) -> None:
        self.joined: torrents._Joined | None = None
        self.writes = writes
        self.told: list[str] = []

    def is_valid(self) -> bool:
        return True

    def torrent_file(self) -> object:
        return object()  # its details are here

    def flush_cache(self) -> None:
        self.told.append("flush")
        if self.writes:
            assert self.joined is not None
            threading.Timer(0.05, self.joined.flushed.set).start()

    def save_resume_data(self, flags: object) -> None:
        self.told.append("save")
        assert self.joined is not None
        self.joined.resume_data = b"d4:whati1ee"
        threading.Timer(0.05, self.joined.resumed.set).start()


class Session:
    def __init__(self, handles: list[Leaving]) -> None:
        self.handles, self.removed = handles, []

    def remove_torrent(self, handle: Leaving, *how: object) -> None:
        handle.told.append("removed")
        self.removed.append(handle)


def leaving(player: torrents.Player, info_hash: str, **how: bool) -> Leaving:
    handle = Leaving(**how)
    joined = torrents._Joined(handle, info_hash, None, player.folder)
    joined.through_libtorrent, handle.joined = True, joined
    joined.top = "Show"
    player._joined[info_hash] = joined
    return handle


def hearing(player: torrents.Player) -> None:
    done = threading.Thread(target=lambda: None)
    done.start()
    done.join()
    player._hearing = done  # as if it heard what libtorrent says back


def test_a_films_state_is_saved_beside_it_when_its_closed(cache: Path) -> None:
    player = torrents.Player(cache)
    handle = leaving(player, "ab" * 20)
    player._session = Session([handle])
    hearing(player)
    joined = player._joined["ab" * 20]
    player.close("ab" * 20)
    # The app isn't kept waiting for that (the next episode, from another torrent,
    # is joined after the one before is left): it's done on a thread of its own.
    assert not joined.left.is_set() and "ab" * 20 in player._parting
    assert joined.left.wait(5)
    # Written to the disk first, then what's here noted, then left.
    assert handle.told == ["flush", "save", "removed"]
    assert (player.folder / f".{'ab' * 20}.resume").read_bytes() == b"d4:whati1ee"
    assert player._parting == {}


def test_a_film_left_while_the_disk_is_slow_is_left_without_its_state(
    cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(torrents, "RESUME_FLUSH_S", 0.2)
    player = torrents.Player(cache)
    handle = leaving(player, "ab" * 20, writes=False)
    player._session = Session([handle])
    hearing(player)
    joined = player._joined["ab" * 20]
    started = time.monotonic()
    player.close("ab" * 20)
    # Nothing is noted that might not be on the disk; it's left all the same, soon.
    assert joined.left.wait(5) and time.monotonic() - started < 2
    assert handle.told == ["flush", "removed"]
    assert not (player.folder / f".{'ab' * 20}.resume").exists()


def test_stopping_saves_every_films_state_together_and_doesnt_wait_long(cache: Path) -> None:
    player = torrents.Player(cache)
    handles = [leaving(player, "ab" * 20), leaving(player, "cd" * 20)]
    player._session = Session(handles)
    hearing(player)
    started = time.monotonic()
    player.stop()
    assert time.monotonic() - started < 2
    for handle, info_hash in zip(handles, ("ab" * 20, "cd" * 20), strict=True):
        assert handle.told == ["flush", "save", "removed"]
        assert (player.folder / f".{info_hash}.resume").exists()
    assert player._joined == {} and player._parting == {} and player._session is None


def test_a_film_played_again_while_its_being_left_waits_for_that(
    cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Added:
        def __init__(self) -> None:
            self.added: list[float] = []

        def add_torrent(self, params: object) -> object:
            self.added.append(time.monotonic())
            return object()

    session = Added()
    monkeypatch.setattr(torrents, "_new_session", lambda: session)
    player = torrents.Player(cache)
    parting = torrents._Joined(object(), "ab" * 20, None, player.folder)
    player._parting["ab" * 20] = parting
    started = time.monotonic()
    threading.Timer(0.3, parting.left.set).start()
    try:
        player.play("ab" * 20, None, [])
        assert session.added and session.added[0] - started >= 0.25
    finally:
        player._joined.clear()
        player.stop()


def test_a_film_open_now_keeps_libtorrents_files_beside_it_through_a_sweep(cache: Path) -> None:
    player = torrents.Player(cache)
    leaving(player, "ab" * 20)
    old = time.time() - torrents.KEEP_S * 2
    for name in ("Show", f".{'ab' * 20}.resume", f".{'ab' * 20}.parts"):
        made = player.folder / name
        made.mkdir() if name == "Show" else made.write_bytes(b"x")
        os.utime(made, (old, old))
    assert player.sweep() == 0
    player._joined.clear()
    assert player.sweep() == 3 and list(player.folder.iterdir()) == []
