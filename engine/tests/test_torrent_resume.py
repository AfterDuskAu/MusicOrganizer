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
from types import SimpleNamespace
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


def saved_state(
    info: Any,
    *,
    priorities: list[int] | None = None,
    have: list[bool] | None = None,
    held: bool = False,
) -> bytes:
    """What libtorrent writes of a torrent it's left: here, for one in upload mode (or, with
    `held`, paused and out of libtorrent's queue, as a film being left is), with the pieces
    in `have` here."""
    import libtorrent as lt

    params = lt.add_torrent_params()
    params.ti = info
    params.save_path = "/somewhere/else"
    params.flags |= lt.torrent_flags.upload_mode | lt.torrent_flags.sequential_download
    if held:
        params.flags = (params.flags | lt.torrent_flags.paused) & ~lt.torrent_flags.auto_managed
    params.trackers = ["udp://kept.example:80"]
    if priorities is not None:
        params.file_priorities = priorities
    if have is not None:
        params.have_pieces = have
    return bytes(lt.write_resume_data_buf(params))


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
    # And in libtorrent's own queue, as a film joined afresh is, though it was noted as
    # paused and out of it when it was left (2026-11-18).
    fileops.write_cached(
        player.folder / f".{info_hash}.resume", saved_state(info, held=True), cache=cache
    )
    held = player._params(info_hash, torrents.magnet(info_hash))
    assert held.flags & lt.torrent_flags.auto_managed and not held.flags & lt.torrent_flags.paused


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


@pytest.fixture
def noted(tmp_path: Path) -> bytes:
    """What libtorrent says of a film being left: a real torrent's state, all of it here."""
    info = a_torrent(tmp_path)
    return saved_state(info, have=[True] * info.num_pieces())


class Leaving:
    """libtorrent's handle on a torrent being left, saying back as libtorrent does: it writes
    out what it holds when asked (unless `writes` is False: a disk that doesn't get to it),
    and says what's here (`noted`; `here`: the pieces it has, as its status says)."""

    def __init__(self, noted: bytes, *, writes: bool = True) -> None:
        import libtorrent as lt

        self.joined: torrents._Joined | None = None
        self.noted, self.writes = noted, writes
        self.here = [True] * len(lt.read_resume_data(noted).have_pieces)
        self.told: list[str] = []
        self.note_when_removed: bool | None = None

    def is_valid(self) -> bool:
        return True

    def torrent_file(self) -> object:
        return object()  # its details are here

    def unset_flags(self, flags: object) -> None:
        pass

    def set_flags(self, flags: object) -> None:
        pass

    def pause(self) -> None:
        self.told.append("pause")

    def resume(self) -> None:
        self.told.append("resume")

    def status(self, flags: object = None) -> SimpleNamespace:
        return SimpleNamespace(pieces=self.here)

    def flush_cache(self) -> None:
        self.told.append("flush")
        if self.writes:
            assert self.joined is not None
            threading.Timer(0.05, self.joined.flush_heard).start()

    def save_resume_data(self, flags: object) -> None:
        self.told.append("save")
        assert self.joined is not None
        self.joined.resume_data = self.noted
        threading.Timer(0.05, self.joined.resumed.set).start()


class Session:
    """libtorrent's session, as far as leaving goes: each film taken out of it, and whether
    its state had been written by then."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def remove_torrent(self, handle: Leaving, *how: object) -> None:
        handle.told.append("removed")
        assert handle.joined is not None
        handle.note_when_removed = (self.folder / f".{handle.joined.info_hash}.resume").exists()


def leaving(player: torrents.Player, info_hash: str, noted: bytes, **how: bool) -> Leaving:
    handle = Leaving(noted, **how)
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


def asked_to_write_out(handle: Leaving) -> None:
    """Wait until a film being left has asked for it all to be written out."""
    for _ in range(100):
        if "flush" in handle.told:
            return
        time.sleep(0.02)
    raise AssertionError(handle.told)


def test_a_films_state_is_saved_beside_it_when_its_closed(cache: Path, noted: bytes) -> None:
    player = torrents.Player(cache)
    handle = leaving(player, "ab" * 20, noted)
    player._session = Session(player.folder)
    hearing(player)
    joined = player._joined["ab" * 20]
    player.close("ab" * 20)
    # The app isn't kept waiting for that (the next episode, from another torrent,
    # is joined after the one before is left): it's done on a thread of its own.
    assert not joined.left.is_set() and "ab" * 20 in player._parting
    assert joined.left.wait(5)
    # Paused (nothing more of it fetched), written out, what's here noted, then left.
    assert handle.told == ["pause", "flush", "save", "removed"]
    assert (player.folder / f".{'ab' * 20}.resume").read_bytes() == noted
    assert player._parting == {}


def test_a_film_left_while_the_disk_is_slow_is_waited_for_and_saved(
    cache: Path, noted: bytes
) -> None:
    # The owner's start-up disk, busy: libtorrent writes the film out long after it's asked
    # (here a second; on 2026-11-17, more than the 3 s that was all it was given, twice).
    player = torrents.Player(cache)
    handle = leaving(player, "ab" * 20, noted, writes=False)
    player._session = Session(player.folder)
    hearing(player)
    joined = player._joined["ab" * 20]
    started = time.monotonic()
    player.close("ab" * 20)
    assert time.monotonic() - started < 0.5  # the app is answered at once all the same
    asked_to_write_out(handle)
    threading.Timer(1.0, joined.flush_heard).start()  # the disk gets to it
    assert joined.left.wait(10) and time.monotonic() - started >= 1.0
    assert handle.told == ["pause", "flush", "save", "removed"]
    assert (player.folder / f".{'ab' * 20}.resume").read_bytes() == noted


def test_a_film_whose_disk_never_writes_it_out_is_left_without_its_state(
    cache: Path, noted: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(torrents, "LEAVE_FLUSH_S", 0.3)
    player = torrents.Player(cache)
    handle = leaving(player, "ab" * 20, noted, writes=False)
    player._session = Session(player.folder)
    hearing(player)
    joined = player._joined["ab" * 20]
    player.close("ab" * 20)
    # Nothing is noted that might not be on the disk; it's left all the same.
    assert joined.left.wait(5)
    assert handle.told == ["pause", "flush", "removed"]
    assert not (player.folder / f".{'ab' * 20}.resume").exists()


def test_a_piece_that_came_after_the_write_out_was_asked_for_isnt_noted(
    cache: Path, noted: bytes
) -> None:
    import libtorrent as lt

    player = torrents.Player(cache)
    handle = leaving(player, "ab" * 20, noted)
    # libtorrent had only some of it when the write-out was asked for; by the time it says
    # what's here, it says it has the rest too (which may not be written yet).
    handle.here = [i % 2 == 0 for i in range(len(handle.here))]
    player._session = Session(player.folder)
    hearing(player)
    joined = player._joined["ab" * 20]
    player.close("ab" * 20)
    assert joined.left.wait(5)
    saved = lt.read_resume_data((player.folder / f".{'ab' * 20}.resume").read_bytes())
    pieces = sum(lt.read_resume_data(noted).have_pieces)  # (all of it was here)
    assert list(saved.have_pieces)[:pieces] == handle.here[:pieces]
    assert 0 < sum(saved.have_pieces) < pieces


def test_whats_noted_says_only_what_was_here_and_nothing_else_changes(tmp_path: Path) -> None:
    import libtorrent as lt

    info = a_torrent(tmp_path)
    pieces = info.num_pieces()
    data = saved_state(info, have=[True] * pieces)
    assert torrents._trimmed(data, [True] * pieces) == data
    some = [i % 3 == 1 for i in range(pieces)]
    # (libtorrent fills its list of pieces out to whole bytes)
    assert list(lt.read_resume_data(torrents._trimmed(data, some)).have_pieces)[:pieces] == some
    assert not any(lt.read_resume_data(torrents._trimmed(data, [])).have_pieces)


def test_a_film_played_again_while_its_being_left_carries_on(cache: Path, noted: bytes) -> None:
    # Closed by mistake and opened again, while a slow disk is still writing it out: it
    # carries on as it was, all there, with nothing noted and nothing taken out.
    player = torrents.Player(cache)
    handle = leaving(player, "ab" * 20, noted, writes=False)
    player._session = Session(player.folder)
    hearing(player)
    joined = player._joined["ab" * 20]
    player.close("ab" * 20)
    asked_to_write_out(handle)
    started = time.monotonic()
    try:
        player.play("ab" * 20, None, [])
        assert time.monotonic() - started < 0.5
        assert player._joined["ab" * 20] is joined and player._parting == {}
        assert not joined.closed and joined.playing
        time.sleep(0.5)  # its leaving lets go of it
        assert handle.told == ["pause", "flush", "resume"]
        assert not joined.left.is_set()
        assert not (player.folder / f".{'ab' * 20}.resume").exists()
    finally:
        player.stop()
    # Left again later (here by the engine stopping), it's noted then.
    assert handle.told == ["pause", "flush", "resume", "save", "removed"]
    assert (player.folder / f".{'ab' * 20}.resume").exists()


def test_a_film_played_again_once_its_past_taking_back_waits_for_that(
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
    parting.removing = True  # what's here is noted, and it's being taken out of libtorrent
    player._parting["ab" * 20] = parting
    started = time.monotonic()
    threading.Timer(0.3, parting.left.set).start()
    try:
        player.play("ab" * 20, None, [])
        assert session.added and session.added[0] - started >= 0.25
    finally:
        player._joined.clear()
        player.stop()


def test_stopping_notes_every_film_once_libtorrent_has_ended(cache: Path, noted: bytes) -> None:
    # libtorrent writes out everything it holds before it ends: so nothing is waited for to
    # be written beforehand, and what it said is written only after it has ended.
    player = torrents.Player(cache)
    handles = [leaving(player, "ab" * 20, noted), leaving(player, "cd" * 20, noted)]
    player._session = Session(player.folder)
    hearing(player)
    started = time.monotonic()
    player.stop()
    assert time.monotonic() - started < 2
    for handle, info_hash in zip(handles, ("ab" * 20, "cd" * 20), strict=True):
        assert handle.told == ["save", "removed"]  # no write-out asked for, or waited for
        assert handle.note_when_removed is False
        assert (player.folder / f".{info_hash}.resume").read_bytes() == noted
    assert player._joined == {} and player._parting == {} and player._session is None


def test_stopping_notes_nothing_while_libtorrent_is_held_elsewhere(
    cache: Path, noted: bytes
) -> None:
    # Held elsewhere, libtorrent would end later, when that lets go: who knows when, so
    # whether all it said is on the disk by then isn't known either.
    player = torrents.Player(cache)
    handle = leaving(player, "ab" * 20, noted)
    session = Session(player.folder)
    player._session = session
    hearing(player)
    player.stop()
    assert handle.told == ["save", "removed"]
    assert not (player.folder / f".{'ab' * 20}.resume").exists()
    assert session is not None


def test_stopping_takes_over_a_film_still_being_left(cache: Path, noted: bytes) -> None:
    player = torrents.Player(cache)
    handle = leaving(player, "ab" * 20, noted, writes=False)  # the disk hasn't got to it
    player._session = Session(player.folder)
    hearing(player)
    joined = player._joined["ab" * 20]
    player.close("ab" * 20)
    asked_to_write_out(handle)
    started = time.monotonic()
    player.stop()
    # Not waited for: noted with the rest, once libtorrent has ended.
    assert time.monotonic() - started < 2
    time.sleep(0.5)  # its own leaving lets go of it, doing nothing more
    assert handle.told == ["pause", "flush", "save", "removed"]
    assert handle.note_when_removed is False
    assert (player.folder / f".{'ab' * 20}.resume").read_bytes() == noted
    assert joined.left.is_set() and player._parting == {}


# ---- that what libtorrent holds is on the disk -----------------------------------------------


class WritesOut:
    """libtorrent's handle, as far as writing out goes."""

    def __init__(self, info_hash: str) -> None:
        self.hash, self.asked = info_hash, 0

    def info_hash(self) -> str:
        return self.hash

    def flush_cache(self) -> None:
        self.asked += 1


def test_a_write_out_is_known_done_only_by_its_own_answer() -> None:
    joined = torrents._Joined(WritesOut("ab" * 20), "ab" * 20, None, Path("films"))
    # The film had just all arrived: libtorrent began writing it out by itself (and says
    # so first), and then the leaving asks for it all to be written out.
    joined.finish_heard()
    joined.ask_flush()
    joined.flush_heard()  # the answer to libtorrent's own: not the one asked for
    assert not joined.all_written(time.monotonic() + 0.1)
    joined.flush_heard()
    assert joined.all_written(time.monotonic())
    # Not wanted any more (played again): not waited for.
    joined.ask_flush()
    started = time.monotonic()
    assert not joined.all_written(time.monotonic() + 5, still=lambda: False)
    assert time.monotonic() - started < 0.5
    joined.flush_heard()
    # One answer more than was owed: the answers can't be told apart any more, and
    # nothing is taken as written from then on.
    joined.flush_heard()
    joined.ask_flush()
    joined.flush_heard()
    assert not joined.all_written(time.monotonic() + 0.1)


def test_only_what_libtorrent_says_of_the_torrent_joined_now_counts(cache: Path) -> None:
    # The same film, left a moment ago and joined again: what libtorrent says of the
    # torrent before (still being let go of) isn't this one's answer.
    now, before = WritesOut("ab" * 20), WritesOut("ab" * 20)

    def alert(kind: str, handle: WritesOut) -> object:
        return type(kind, (), {"handle": handle})()

    said = [
        alert("torrent_finished_alert", now),
        alert("cache_flushed_alert", before),
        alert("cache_flushed_alert", now),
    ]
    player = torrents.Player(cache)
    joined = torrents._Joined(now, "ab" * 20, None, player.folder)
    player._joined["ab" * 20] = joined

    class Speaking:
        def wait_for_alert(self, ms: int) -> None:
            pass

        def pop_alerts(self) -> list[object]:
            if said:
                out = list(said)
                said.clear()
                return out
            player._session = None  # and it's let go of
            return []

    session = Speaking()
    player._session = session
    joined.ask_flush()
    player._hear(session)
    # Asked, and libtorrent's own on finishing: one answered, one still to come.
    assert not joined.all_written(time.monotonic() + 0.1)
    joined.flush_heard()
    assert joined.all_written(time.monotonic())


def test_a_film_open_now_keeps_libtorrents_files_beside_it_through_a_sweep(
    cache: Path, noted: bytes
) -> None:
    player = torrents.Player(cache)
    leaving(player, "ab" * 20, noted)
    old = time.time() - torrents.KEEP_S * 2
    for name in ("Show", f".{'ab' * 20}.resume", f".{'ab' * 20}.parts"):
        made = player.folder / name
        made.mkdir() if name == "Show" else made.write_bytes(b"x")
        os.utime(made, (old, old))
    assert player.sweep() == 0
    player._joined.clear()
    assert player.sweep() == 3 and list(player.folder.iterdir()) == []
