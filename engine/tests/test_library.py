"""library: init, open, the single-writer lock, and environment warnings."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from musicorg import fileops, library, naming, state
from musicorg.config import Config
from musicorg.errors import EXIT_LOCKED, LibraryLockedError, UserError
from musicorg.naming import LibraryPaths

CHILD_TIMEOUT_S = 120


@pytest.fixture
def plenty_of_space(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library, "_free_bytes", lambda folder: 500 * 1000**3)


@pytest.fixture
def lib_root(tmp_path: Path) -> Path:
    root = tmp_path / "Music Organizer Library"
    library.init(root)
    return root.resolve()


def listing(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))


# ---- init ------------------------------------------------------------------------------


def test_init_creates_the_layout(tmp_path: Path) -> None:
    root = tmp_path / "Music Organizer Library"
    result = library.init(root)
    paths = LibraryPaths(root.resolve())

    assert result.root == paths.root
    assert result.already_library is False
    assert result.created == [paths.root, *paths.layout_folders]
    for folder in paths.layout_folders:
        assert folder.is_dir(), folder
    assert paths.calibration == paths.root / "_Staging" / "calibration"
    assert json.loads(paths.state_file.read_text(encoding="utf-8"))["schema"] == 1
    assert library.is_library(paths.root)
    # The lock was taken and released: no holder details are left behind.
    assert not paths.lock_info_file.exists()


def test_init_in_an_existing_empty_folder(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    result = library.init(root)
    assert result.already_library is False
    assert root.resolve() not in result.created
    assert (root / "Music").is_dir()


def test_init_remembers_the_library(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    library.init(root)
    assert Config.load().last_library == root.resolve()


def test_init_expands_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    result = library.init(Path("~/Music Organizer Library"))
    assert result.root == (tmp_path / "Music Organizer Library").resolve()


def test_init_twice_changes_nothing(lib_root: Path) -> None:
    state_before = (lib_root / ".musicorg" / "state.json").read_bytes()
    result = library.init(lib_root)
    assert result.already_library is True
    assert result.created == []
    assert (lib_root / ".musicorg" / "state.json").read_bytes() == state_before


def test_init_restores_missing_folders(lib_root: Path) -> None:
    (lib_root / "Reports").rmdir()
    (lib_root / "_Staging" / "calibration").rmdir()
    result = library.init(lib_root)
    assert result.already_library is True
    assert result.created == [lib_root / "_Staging" / "calibration", lib_root / "Reports"]


def test_init_on_a_library_that_holds_music_is_fine(lib_root: Path) -> None:
    (lib_root / "Music" / "Artist").mkdir()
    (lib_root / "Music" / "Artist" / "01 Song.m4a").write_bytes(b"audio")
    assert library.init(lib_root).already_library is True


@pytest.mark.parametrize(
    "audio_file", ["song.mp3", "Artist/Album/01 Song.M4A", "a/b/c/d/track.flac"]
)
def test_init_refuses_a_folder_holding_music(tmp_path: Path, audio_file: str) -> None:
    rips = tmp_path / "rips"
    (rips / audio_file).parent.mkdir(parents=True, exist_ok=True)
    (rips / audio_file).write_bytes(b"not really audio")
    (rips / "notes.txt").write_text("hello", encoding="utf-8")
    before = listing(rips)

    with pytest.raises(UserError) as caught:
        library.init(rips)

    message = caught.value.message
    assert "already holds music files" in message
    assert str(Path(audio_file)) in message
    assert "source" in message
    assert listing(rips) == before  # nothing was created or changed
    assert Config.load().last_library is None


def test_init_ignores_junk_files(tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "._song.mp3").write_bytes(b"resource fork")
    (folder / ".DS_Store").write_bytes(b"finder")
    (folder / "notes.txt").write_text("not music", encoding="utf-8")
    assert library.init(folder).already_library is False


def test_init_gives_up_on_a_huge_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library, "AUDIO_SEARCH_LIMIT", 5)
    folder = tmp_path / "busy"
    folder.mkdir()
    for n in range(10):
        (folder / f"file{n}.txt").write_text("x", encoding="utf-8")
    with pytest.raises(UserError, match="a great many files"):
        library.init(folder)
    assert not (folder / "Music").exists()


def test_music_search_does_not_follow_folder_links(tmp_path: Path) -> None:
    rips = tmp_path / "rips"
    rips.mkdir()
    (rips / "song.mp3").write_bytes(b"x")
    root = tmp_path / "Library"
    root.mkdir()
    try:
        (root / "link-to-rips").symlink_to(rips, target_is_directory=True)
    except OSError:
        pytest.skip("this system doesn't allow symlinks here")
    assert library.init(root).already_library is False


def test_init_refuses_a_file(tmp_path: Path) -> None:
    file = tmp_path / "file.txt"
    file.write_text("x", encoding="utf-8")
    with pytest.raises(UserError, match="is a file, not a folder"):
        library.init(file)


def test_init_refuses_a_missing_parent(tmp_path: Path) -> None:
    with pytest.raises(UserError, match="doesn't exist"):
        library.init(tmp_path / "Volumes" / "Unplugged Drive" / "Library")
    assert not (tmp_path / "Volumes").exists()


def test_init_refuses_a_folder_inside_a_library(lib_root: Path) -> None:
    with pytest.raises(UserError, match="is inside the library"):
        library.init(lib_root / "Music" / "Nested")
    assert not (lib_root / "Music" / "Nested").exists()


def test_init_refuses_the_home_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with pytest.raises(UserError, match="won't use"):
        library.init(tmp_path)


def test_init_refuses_a_file_in_the_way(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    root.mkdir()
    (root / "Music").write_text("a file called Music", encoding="utf-8")
    with pytest.raises(UserError, match="is in the way"):
        library.init(root)
    assert not (root / ".musicorg").exists()


def test_init_refuses_staging_on_another_drive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def device(path: Path) -> int:
        return 2 if path.name == naming.STAGING_DIR else 1

    monkeypatch.setattr(library, "_device", device)
    root = tmp_path / "Library"
    with pytest.raises(UserError, match="different drive"):
        library.init(root)
    assert not root.exists()


def test_same_drive_check_uses_real_devices(tmp_path: Path) -> None:
    paths = LibraryPaths(tmp_path / "Library")
    assert (
        library._device(paths.music) == library._device(paths.staging) == os.stat(tmp_path).st_dev
    )


# ---- open ------------------------------------------------------------------------------


def test_open_a_missing_folder(tmp_path: Path) -> None:
    with pytest.raises(UserError, match="doesn't exist"):
        library.open(tmp_path / "nope", write=False)


def test_open_a_folder_that_is_not_a_library(tmp_path: Path) -> None:
    with pytest.raises(UserError, match="isn't a Music Organizer library"):
        library.open(tmp_path, write=True)
    assert not (tmp_path / ".musicorg").exists()


def test_read_only_open_takes_no_lock(lib_root: Path) -> None:
    with library.open(lib_root, write=False) as reader:
        assert reader.writable is False
        assert reader.root == lib_root
        assert reader.load_state().schema == 1
        with library.open(lib_root, write=True) as writer:
            assert writer.writable is True


def test_second_writer_is_refused(lib_root: Path) -> None:
    with library.open(lib_root, write=True, command="queue run") as first:
        assert first.writable
        with pytest.raises(LibraryLockedError) as caught:
            library.open(lib_root, write=True, command="scan")
        error = caught.value
        assert error.exit_code == EXIT_LOCKED
        assert f"PID {os.getpid()}" in error.message
        assert "`queue run`" in error.message
        assert error.holder is not None and error.holder["command"] == "queue run"
    # Closing released it.
    with library.open(lib_root, write=True) as again:
        assert again.writable


def test_lock_info_is_written_and_removed(lib_root: Path) -> None:
    info_file = lib_root / ".musicorg" / "lock.info"
    lib = library.open(lib_root, write=True, command="apply p_1")
    info = json.loads(info_file.read_text(encoding="utf-8"))
    assert info["pid"] == os.getpid()
    assert info["command"] == "apply p_1"
    started = datetime.fromisoformat(info["started_at"])
    assert abs(datetime.now(UTC) - started) < timedelta(minutes=5)
    lib.close()
    lib.close()  # twice is harmless
    assert not info_file.exists()
    assert lib.writable is False


def test_stale_lock_info_is_ignored(lib_root: Path) -> None:
    info_file = lib_root / ".musicorg" / "lock.info"
    info_file.write_text(
        json.dumps({"pid": 999999, "command": "queue run", "started_at": "2026-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    with library.open(lib_root, write=True, command="init"):
        info = json.loads(info_file.read_text(encoding="utf-8"))
        assert info["pid"] == os.getpid()
        assert info["command"] == "init"


def test_unreadable_lock_info_gives_a_plain_message(lib_root: Path) -> None:
    with library.open(lib_root, write=True):
        (lib_root / ".musicorg" / "lock.info").write_text("{garbage", encoding="utf-8")
        with pytest.raises(LibraryLockedError) as caught:
            library.open(lib_root, write=True)
    assert caught.value.message.startswith("Another Music Organizer process is using this library.")
    assert caught.value.holder is None


def test_recovery_runs_on_write_opens_only(lib_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(fileops, "recover_journal", lambda paths: calls.append(paths.root))
    library.open(lib_root, write=False).close()
    assert calls == []
    library.open(lib_root, write=True).close()
    assert calls == [lib_root]


def test_failed_recovery_releases_the_lock(lib_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(paths: LibraryPaths) -> None:
        raise UserError("recovery failed")

    monkeypatch.setattr(fileops, "recover_journal", broken)
    with pytest.raises(UserError, match="recovery failed"):
        library.open(lib_root, write=True)
    monkeypatch.undo()
    with library.open(lib_root, write=True) as lib:
        assert lib.writable


def test_describe_lock_holder() -> None:
    now = datetime.now(UTC)
    holder = {"pid": 4412, "command": "queue run", "started_at": now.isoformat()}
    local = now.astimezone()
    assert fileops.describe_lock_holder(holder, now=now) == (
        f"Another Music Organizer process (PID 4412, `queue run`, started {local:%H:%M}) "
        "is using this library. Wait for it to finish, or stop it, then try again."
    )

    two_days_ago = now - timedelta(days=2)
    holder["started_at"] = two_days_ago.isoformat()
    assert f"started {two_days_ago.astimezone():%Y-%m-%d %H:%M}" in fileops.describe_lock_holder(
        holder, now=now
    )

    holder["host"] = "another-mac.local"
    assert "on the computer another-mac.local" in fileops.describe_lock_holder(holder)

    assert fileops.describe_lock_holder(None).startswith(
        "Another Music Organizer process is using this library."
    )
    assert "PID" not in fileops.describe_lock_holder({"pid": "not a number"})


# ---- the lock across processes ---------------------------------------------------------

HOLD_LOCK = textwrap.dedent(
    """
    import sys
    from pathlib import Path

    from musicorg import library

    lib = library.open(Path(sys.argv[1]), write=True, command=sys.argv[2])
    print("locked", flush=True)
    sys.stdin.readline()
    lib.close()
    """
)


def _readline(proc: subprocess.Popen[str]) -> str:
    """One line from the child, or fail the test if it doesn't come."""
    lines: list[str] = []
    assert proc.stdout is not None
    reader = threading.Thread(target=lambda: lines.append(proc.stdout.readline()), daemon=True)
    reader.start()
    reader.join(CHILD_TIMEOUT_S)
    if reader.is_alive() or not lines:
        proc.kill()
        pytest.fail("the child process didn't answer in time")
    return lines[0]


@contextmanager
def lock_held_by_child(root: Path, command: str) -> Iterator[subprocess.Popen[str]]:
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLD_LOCK, str(root), command],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = _readline(proc)
        if line.strip() != "locked":
            proc.kill()
            _, err = proc.communicate(timeout=CHILD_TIMEOUT_S)
            pytest.fail(f"the child couldn't take the lock: {line!r}\n{err}")
        yield proc
    finally:
        if proc.poll() is None:
            assert proc.stdin is not None
            proc.stdin.write("\n")
            proc.stdin.flush()
            proc.wait(timeout=CHILD_TIMEOUT_S)


def test_second_process_gets_the_holders_details(lib_root: Path) -> None:
    with lock_held_by_child(lib_root, "queue run") as child:
        with pytest.raises(LibraryLockedError) as caught:
            library.open(lib_root, write=True, command="scan")
        message = caught.value.message
        assert f"PID {child.pid}" in message
        assert "`queue run`" in message
        assert "started " in message
        assert message.endswith("Wait for it to finish, or stop it, then try again.")

        # Read-only opens still work while the other process writes.
        with library.open(lib_root, write=False) as reader:
            assert reader.load_state().schema == 1

    assert child.returncode == 0
    with library.open(lib_root, write=True) as lib:
        assert lib.writable


def test_second_process_cli_exits_with_code_2(lib_root: Path) -> None:
    with library.open(lib_root, write=True, command="test holder"):
        result = subprocess.run(
            [sys.executable, "-m", "musicorg", "init", str(lib_root)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=CHILD_TIMEOUT_S,
        )
    assert result.returncode == EXIT_LOCKED
    assert f"PID {os.getpid()}" in result.stderr
    assert "`test holder`" in result.stderr
    assert result.stdout == ""


# ---- environment warnings --------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


def mac_warnings(root: Path, home: Path) -> list[str]:
    return library.environment_warnings(root, platform="darwin", home=home)


@pytest.mark.usefixtures("plenty_of_space")
def test_no_warnings_for_a_plain_folder(fake_home: Path) -> None:
    root = fake_home / "Music Organizer Library"
    assert mac_warnings(root, fake_home) == []
    short_windows_root = Path("C:/Music Organizer Library")
    assert library.environment_warnings(short_windows_root, platform="win32", home=fake_home) == []


@pytest.mark.usefixtures("plenty_of_space")
def test_icloud_drive_warning(fake_home: Path) -> None:
    root = fake_home / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Library"
    root.mkdir(parents=True)
    (warning,) = mac_warnings(root, fake_home)
    assert "iCloud Drive" in warning
    assert "placeholders" in warning


@pytest.mark.usefixtures("plenty_of_space")
def test_cloud_storage_warning(fake_home: Path) -> None:
    root = fake_home / "Library" / "CloudStorage" / "Dropbox" / "Library"
    (warning,) = mac_warnings(root, fake_home)
    assert "cloud-synced folder" in warning


@pytest.mark.usefixtures("plenty_of_space")
@pytest.mark.parametrize("folder", ["Desktop", "Documents"])
def test_icloud_desktop_and_documents_warning(fake_home: Path, folder: str) -> None:
    root = fake_home / folder / "Library"
    root.mkdir(parents=True)
    assert mac_warnings(root, fake_home) == []  # iCloud Desktop & Documents is off

    (fake_home / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / folder).mkdir(
        parents=True
    )
    (warning,) = mac_warnings(root, fake_home)
    assert f"your {folder} folder" in warning
    assert "iCloud Drive" in warning


@pytest.mark.usefixtures("plenty_of_space")
def test_time_machine_warning(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = fake_home / "Library"
    monkeypatch.setattr(
        library, "_tmutil_destinationinfo", lambda: "tmutil: No destinations configured.\n"
    )
    (warning,) = mac_warnings(root, fake_home)
    assert "Time Machine has no backup disk" in warning

    configured = "====\nName          : Backups\nKind          : Local\nID            : 1234\n"
    monkeypatch.setattr(library, "_tmutil_destinationinfo", lambda: configured)
    assert mac_warnings(root, fake_home) == []

    # Only asked on a Mac.
    monkeypatch.setattr(
        library, "_tmutil_destinationinfo", lambda: "tmutil: No destinations configured.\n"
    )
    windows_warnings = library.environment_warnings(root, platform="win32", home=fake_home)
    assert not any("Time Machine" in w for w in windows_warnings)


def test_free_space_warning(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library, "_free_bytes", lambda folder: 3_200_000_000)
    (warning,) = mac_warnings(fake_home / "Library", fake_home)
    assert "Only 3.2 GB is free" in warning

    monkeypatch.setattr(library, "_free_bytes", lambda folder: None)  # couldn't tell
    assert mac_warnings(fake_home / "Library", fake_home) == []


def test_free_space_is_checked_on_the_nearest_existing_folder(tmp_path: Path) -> None:
    missing = tmp_path / "not" / "yet"
    assert library._nearest_existing(missing) == tmp_path
    assert library._free_bytes(tmp_path) is not None


@pytest.mark.usefixtures("plenty_of_space")
def test_long_root_warning_on_windows(fake_home: Path) -> None:
    short = Path("C:/Music Organizer Library")
    long = Path("C:/Users/someone/Documents/My Music Collection/Music Organizer Library 2026")
    assert len(str(long)) > 60
    assert library.environment_warnings(short, platform="win32", home=fake_home) == []
    (warning,) = library.environment_warnings(long, platform="win32", home=fake_home)
    assert f"{len(str(long))} characters long" in warning
    assert mac_warnings(long, fake_home) == []  # not a problem on a Mac


@pytest.mark.usefixtures("plenty_of_space")
def test_onedrive_warning_on_windows(
    fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    onedrive = tmp_path / "OneDrive"
    monkeypatch.setenv("OneDrive", str(onedrive))
    warnings = library.environment_warnings(onedrive / "Library", platform="win32", home=fake_home)
    assert any("inside OneDrive" in w for w in warnings)


def test_init_reports_warnings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library, "_free_bytes", lambda folder: 1_000_000_000)
    result = library.init(tmp_path / "Library")
    assert any("Only 1.0 GB is free" in w for w in result.warnings)


def test_state_file_is_created_by_init_only(lib_root: Path) -> None:
    state_file = lib_root / ".musicorg" / "state.json"
    state_file.unlink()
    with library.open(lib_root, write=True) as lib:
        assert lib.load_state().schema == state.STATE_SCHEMA  # defaults, not saved
    assert not state_file.exists()
    library.init(lib_root)
    assert state_file.exists()
