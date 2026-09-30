"""fileops staging and exports: `stage_path`, `discard_staged` and `clean_staging` (the only
outright deletes, never following links), `stage_copy` and `set_aside` (step 09b), and
`write_export` (never overwrites, never inside the library's own folders or a source)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fileops_support import age, files_in, put, staged, symlink_or_skip, tree

from musicorg import fileops, library
from musicorg.errors import OutsideLibraryError, UserError
from musicorg.library import Library


@pytest.fixture
def precious(tmp_path: Path) -> Path:
    """A file outside the library that must survive everything here."""
    return put(tmp_path / "outside" / "precious.mp3", b"irreplaceable")


# ---- staging ---------------------------------------------------------------------------


def test_stage_path(lib: Library) -> None:
    with fileops.batch(lib, "demo") as b:
        path = fileops.stage_path(b, "Artist: Song?.m4a")
        assert path == lib.paths.staging / b.batch_id / "Artist_ Song_.m4a"
        assert path.parent.is_dir()
        assert not path.exists()
        put(path)
        assert fileops.stage_path(b, "Artist: Song?.m4a").name == "Artist_ Song_ (2).m4a"


def test_stage_dir(lib: Library, precious: Path) -> None:
    with fileops.batch(lib, "demo") as b:
        folder = fileops.stage_dir(b, "job-7")
        assert folder == lib.paths.staging / b.batch_id / "job-7"
        assert folder.is_dir() and not any(folder.iterdir())
        # What a crashed run left behind is discarded, so a download starts clean.
        put(folder / "abc.m4a.part", b"half")
        put(folder / "sub" / "x.tmp")
        again = fileops.stage_dir(b, "job-7")
        assert again == folder
        assert folder.is_dir() and not any(folder.iterdir())
        assert precious.read_bytes() == b"irreplaceable"


def test_stage_dir_never_follows_a_link(lib: Library, precious: Path) -> None:
    with fileops.batch(lib, "demo") as b:
        parent = fileops.stage_dir(b, "job-1").parent
        symlink_or_skip(parent / "job-2", precious.parent)
        folder = fileops.stage_dir(b, "job-2")
        assert folder.is_dir() and not folder.is_symlink()
        assert precious.read_bytes() == b"irreplaceable"


def test_stage_dir_needs_the_lock(lib: Library) -> None:
    with fileops.batch(lib, "demo") as b:
        pass
    lib.close()
    with pytest.raises(RuntimeError):
        fileops.stage_dir(b, "job-1")


def test_discard_staged(lib: Library) -> None:
    with fileops.batch(lib, "demo") as b:
        one = staged(b, "one.m4a")
        folder = lib.paths.staging / b.batch_id
        put(folder / "job 7" / "abc.m4a.part")
        assert fileops.discard_staged(lib, one) == 1
        assert not one.exists()
        assert fileops.discard_staged(lib, folder) == 1
        assert not folder.exists()
        assert fileops.discard_staged(lib, folder) == 0


def test_discard_staged_never_follows_links(lib: Library, precious: Path) -> None:
    folder = lib.paths.staging / "b_old"
    folder.mkdir()
    symlink_or_skip(folder / "file link.mp3", precious)
    symlink_or_skip(folder / "folder link", precious.parent)
    assert fileops.discard_staged(lib, folder) == 2
    assert not folder.exists()
    assert precious.read_bytes() == b"irreplaceable"
    assert os.listdir(precious.parent) == [precious.name]


def test_discard_staged_removes_a_link_itself(lib: Library, precious: Path) -> None:
    link = lib.paths.staging / "link.mp3"
    symlink_or_skip(link, precious)
    assert fileops.discard_staged(lib, link) == 1
    assert not os.path.lexists(link)
    assert precious.read_bytes() == b"irreplaceable"


def test_discard_staged_refuses_anything_else(lib: Library, precious: Path) -> None:
    track = put(lib.paths.music / "A" / "Song.m4a")
    for path in (precious, track, lib.paths.staging, lib.paths.staging / "..", lib.root):
        with pytest.raises(OutsideLibraryError):
            fileops.discard_staged(lib, path)
    with pytest.raises(UserError, match="calibration folder is kept"):
        fileops.discard_staged(lib, lib.paths.calibration)
    assert track.exists()
    assert precious.exists()


def test_clean_staging(lib: Library) -> None:
    old = put(lib.paths.staging / "b_old" / "job 1" / "a.m4a")
    for path in (old, old.parent, old.parent.parent):
        age(path, 30)
    recent = put(lib.paths.staging / "b_new" / "b.m4a")
    loose = put(lib.paths.staging / "loose.tmp")
    age(loose, 25)
    calibration = put(lib.paths.calibration / "c.m4a")
    age(calibration, 1000)

    assert fileops.clean_staging(lib) == 2
    assert tree(lib.paths.staging) == ["b_new/", "b_new/b.m4a", "calibration/", "calibration/c.m4a"]
    assert recent.exists()
    assert calibration.exists()


def test_clean_staging_keeps_a_folder_with_recent_files(lib: Library) -> None:
    old = put(lib.paths.staging / "b_open" / "old.m4a")
    age(old, 48)
    age(old.parent, 48)
    put(lib.paths.staging / "b_open" / "new.m4a")
    age(old.parent, 48)  # an old folder, but it holds something recent
    assert fileops.clean_staging(lib) == 1
    assert files_in(lib.paths.staging) == ["b_open/new.m4a"]


def test_clean_staging_removes_links_but_never_follows_them(lib: Library, precious: Path) -> None:
    folder = put(lib.paths.staging / "b_x" / "keep.m4a").parent
    symlink_or_skip(folder / "file link.mp3", precious)
    symlink_or_skip(lib.paths.staging / "folder link", precious.parent)
    assert fileops.clean_staging(lib) == 2
    assert files_in(lib.paths.staging) == ["b_x/keep.m4a"]
    assert precious.read_bytes() == b"irreplaceable"
    assert os.listdir(precious.parent) == [precious.name]


@pytest.mark.parametrize("where", ["outside", "Music"])
def test_a_staging_folder_that_leads_elsewhere_is_refused(
    lib: Library, precious: Path, tmp_path: Path, where: str
) -> None:
    age(precious, 100)
    in_music = put(lib.paths.music / "Song.m4a", b"a library file")
    age(in_music, 100)
    os.replace(lib.paths.staging, tmp_path / "old staging")
    symlink_or_skip(lib.paths.staging, precious.parent if where == "outside" else lib.paths.music)
    with pytest.raises(OutsideLibraryError, match="leads somewhere else"):
        fileops.clean_staging(lib)
    with pytest.raises(OutsideLibraryError):
        fileops.discard_staged(lib, lib.paths.staging / precious.name)
    with pytest.raises(OutsideLibraryError):
        fileops.discard_staged(lib, lib.paths.staging / in_music.name)
    assert precious.read_bytes() == b"irreplaceable"
    assert in_music.read_bytes() == b"a library file"


def test_cleaning_staging_needs_the_lock(lib: Library) -> None:
    with library.open(lib.root, write=False) as read_only:
        lib.close()
        with pytest.raises(RuntimeError, match="open for writing"):
            fileops.clean_staging(read_only)
        with pytest.raises(RuntimeError, match="open for writing"):
            fileops.discard_staged(read_only, read_only.paths.staging / "x")


# ---- stage_copy and set_aside (step 09b) -----------------------------------------------


def test_stage_copy(lib: Library, precious: Path) -> None:
    info = os.stat(precious)
    with fileops.batch(lib, "demo") as b:
        copy = fileops.stage_copy(b, precious)
        assert copy == lib.paths.staging / b.batch_id / "precious.mp3"
        assert copy.read_bytes() == b"irreplaceable"
        # A second copy of the same name never overwrites the first.
        assert fileops.stage_copy(b, precious).name == "precious (2).mp3"
    assert precious.read_bytes() == b"irreplaceable"
    assert os.stat(precious).st_mtime_ns == info.st_mtime_ns
    assert files_in(lib.paths.music) == []


def test_stage_copy_refuses_library_files_and_folders(lib: Library, precious: Path) -> None:
    track = put(lib.paths.music / "A" / "Song.m4a")
    with fileops.batch(lib, "demo") as b:
        with pytest.raises(UserError, match="already inside the library"):
            fileops.stage_copy(b, track)
        with pytest.raises(UserError, match="isn't a file"):
            fileops.stage_copy(b, precious.parent)
        with pytest.raises(UserError, match="There's no file"):
            fileops.stage_copy(b, precious.parent / "missing.mp3")
    assert files_in(lib.paths.staging) == []


def test_stage_copy_that_doesnt_verify_is_discarded(
    lib: Library, precious: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fileops, "_copy_file", lambda src, dst: (put(dst, b"x"), "0" * 64)[1])
    with fileops.batch(lib, "demo") as b:
        with pytest.raises(fileops.IntegrityError, match="didn't match the original"):
            fileops.stage_copy(b, precious)
    assert files_in(lib.paths.staging) == []
    assert precious.read_bytes() == b"irreplaceable"


def test_set_aside(lib: Library) -> None:
    with fileops.batch(lib, "demo") as b:
        job = fileops.stage_dir(b, "job-1")
        first = put(job / "vid.m4a", b"one")
        kept = fileops.set_aside(lib, first, f"{b.batch_id}/kept", note={"verdict": "match"})
        assert kept == lib.paths.staging / b.batch_id / "kept" / "vid.m4a"
        assert kept.read_bytes() == b"one" and not first.exists()
        assert (kept.parent / "vid.m4a.json").read_text(encoding="utf-8").startswith("{")
        # A second file of the same name is kept too, never over the first.
        second = put(job / "vid.m4a", b"two")
        again = fileops.set_aside(lib, second, f"{b.batch_id}/kept")
        assert again.name == "vid (2).m4a"
        assert kept.read_bytes() == b"one"
        calibration = fileops.set_aside(lib, put(job / "c.m4a"), "calibration/b_1")
        assert calibration == lib.paths.calibration / "b_1" / "c.m4a"


def test_set_aside_stays_in_staging(lib: Library, precious: Path) -> None:
    track = put(lib.paths.music / "A" / "Song.m4a")
    loose = put(lib.paths.staging / "loose.m4a")
    for path in (precious, track):
        with pytest.raises(OutsideLibraryError):
            fileops.set_aside(lib, path, "kept")
    for folder in ("../Music", "/tmp", "a/../../x"):
        with pytest.raises(OutsideLibraryError):
            fileops.set_aside(lib, loose, folder)
    assert track.exists() and loose.exists()
    assert precious.read_bytes() == b"irreplaceable"


# ---- exports ---------------------------------------------------------------------------


def test_write_export_never_overwrites(lib: Library) -> None:
    first = fileops.write_export(lib, lib.paths.reports / "report.csv", b"a,b\n", sources=[])
    second = fileops.write_export(lib, lib.paths.reports / "report.csv", b"c,d\n", sources=[])
    assert first == lib.paths.reports / "report.csv"
    assert second == lib.paths.reports / "report (2).csv"
    assert first.read_bytes() == b"a,b\n"
    assert second.read_bytes() == b"c,d\n"
    assert files_in(lib.paths.reports) == ["report (2).csv", "report.csv"]


def test_write_export_anywhere_the_user_chose(lib: Library, tmp_path: Path) -> None:
    out = tmp_path / "Desktop"
    out.mkdir()
    assert fileops.write_export(lib, out / "r.md", b"# Report", sources=[]).read_bytes() == (
        b"# Report"
    )
    with pytest.raises(UserError, match="doesn't exist"):
        fileops.write_export(lib, tmp_path / "Nowhere" / "r.md", b"x", sources=[])


def test_write_export_puts_back_a_missing_reports_folder(lib: Library) -> None:
    lib.paths.reports.rmdir()
    path = fileops.write_export(lib, lib.paths.reports / "r.csv", b"x", sources=[])
    assert path.read_bytes() == b"x"


@pytest.mark.parametrize("folder", ["Music", "Music/A", "_Replaced", "_Staging", ".musicorg"])
def test_write_export_refuses_the_library_s_own_folders(lib: Library, folder: str) -> None:
    target = lib.root.joinpath(*folder.split("/"))
    target.mkdir(exist_ok=True)
    with pytest.raises(UserError, match="library's own folders"):
        fileops.write_export(lib, target / "report.csv", b"x", sources=[])
    assert not (target / "report.csv").exists()


def test_write_export_refuses_sources(lib: Library, tmp_path: Path) -> None:
    rips = put(tmp_path / "rips" / "Sub" / "song.mp3").parent.parent
    for folder in (rips, rips / "Sub"):
        with pytest.raises(UserError, match="source folder"):
            fileops.write_export(lib, folder / "report.csv", b"x", sources=[rips])
    assert files_in(rips) == ["Sub/song.mp3"]


def test_write_export_refuses_a_link_into_the_library(lib: Library, tmp_path: Path) -> None:
    sneaky = tmp_path / "sneaky"
    symlink_or_skip(sneaky, lib.paths.music)
    with pytest.raises(UserError, match="library's own folders"):
        fileops.write_export(lib, sneaky / "report.csv", b"x", sources=[])
    assert tree(lib.paths.music) == []


def test_write_export_needs_no_lock(lib: Library) -> None:
    lib.close()
    with library.open(lib.root, write=False) as read_only:
        path = fileops.write_export(read_only, read_only.paths.reports / "r.csv", b"x", sources=[])
    assert path.read_bytes() == b"x"
