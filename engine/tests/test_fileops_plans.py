"""fileops plans: saving and loading, and preconditions that catch a changed file, a new
file at the target, or a changed item state, while ignoring .DS_Store and friends."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from fileops_support import put

from musicorg import fileops, library
from musicorg.errors import NotFoundError, UserError
from musicorg.fileops import FileCheck, FolderCheck, PlanOp
from musicorg.library import Library


@pytest.fixture
def rip(tmp_path: Path) -> Path:
    return put(tmp_path / "rips" / "Artist - Song.mp3", b"ID3" + bytes(range(256)) * 8000)


def adopt_op(lib: Library, rip: Path, target: str = "Music/Artist/Unsorted/Song.mp3") -> PlanOp:
    folder = lib.root.joinpath(*target.split("/")[:-1])
    return PlanOp(
        action="adopt",
        item_id="i_0123456789abcdef",
        item_state="only_copy",
        source=FileCheck.of(lib, rip),
        target=target,
        target_folder=FolderCheck.of(lib, folder),
        params={"tags": {"title": "Song", "artist": "Artist"}},
    )


def states(state: str | None) -> fileops.ItemStateLookup:
    return lambda item_id: state


def test_a_plan_is_saved_and_loaded(lib: Library, rip: Path) -> None:
    plan = fileops.new_plan("adopt", [adopt_op(lib, rip), adopt_op(lib, rip)], {"operations": 2})
    assert re.fullmatch(r"p_\d{8}-\d{6}-[0-9a-f]{6}", plan.plan_id)
    assert [op.op_id for op in plan.operations] == [1, 2]
    path = fileops.save_plan(lib, plan)
    assert path == lib.paths.plans / f"{plan.plan_id}.json"
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == 1
    assert fileops.load_plan(lib, plan.plan_id) == plan


def test_plans_are_loaded_without_the_lock(lib: Library, rip: Path) -> None:
    plan = fileops.new_plan("adopt", [adopt_op(lib, rip)])
    fileops.save_plan(lib, plan)
    with library.open(lib.root, write=False) as read_only:
        assert fileops.load_plan(read_only, plan.plan_id) == plan


def test_saving_a_plan_needs_the_lock(lib: Library, rip: Path) -> None:
    plan = fileops.new_plan("adopt", [adopt_op(lib, rip)])
    with library.open(lib.root, write=False) as read_only:
        lib.close()
        with pytest.raises(RuntimeError, match="open for writing"):
            fileops.save_plan(read_only, plan)


def test_plan_kinds_come_from_the_enums(rip: Path) -> None:
    for kind in ("replace", "adopt", "lyrics", "artwork"):
        assert fileops.new_plan(kind, []).kind == kind
    with pytest.raises(ValueError, match="plan kind"):
        fileops.new_plan("undo", [])


def test_missing_and_damaged_plans(lib: Library) -> None:
    for plan_id in ("p_20200101-000000-000000", "../state", ""):
        with pytest.raises(NotFoundError, match="no plan"):
            fileops.load_plan(lib, plan_id)
    put(lib.paths.plans / "p_20200101-000000-aaaaaa.json", "{not json")
    with pytest.raises(UserError, match="damaged"):
        fileops.load_plan(lib, "p_20200101-000000-aaaaaa")
    put(lib.paths.plans / "p_20200101-000000-bbbbbb.json", '{"plan_id": "x"}')
    with pytest.raises(UserError, match="damaged"):
        fileops.load_plan(lib, "p_20200101-000000-bbbbbb")


def test_library_files_are_recorded_relative(lib: Library) -> None:
    track = put(lib.paths.music / "A" / "Song.m4a")
    assert FileCheck.of(lib, track).path == "Music/A/Song.m4a"
    assert FolderCheck.of(lib, track.parent) == FolderCheck("Music/A", True, ["Song.m4a"])
    assert FolderCheck.of(lib, lib.paths.music / "Missing") == FolderCheck(
        "Music/Missing", False, []
    )


def test_an_unchanged_plan_is_valid(lib: Library, rip: Path) -> None:
    plan = fileops.new_plan("adopt", [adopt_op(lib, rip)])
    assert fileops.validate(lib, plan, item_state=states("only_copy")) == []


def grow(rip: Path) -> None:
    with open(rip, "ab") as f:
        f.write(b"more")


def touch_later(rip: Path) -> None:
    info = rip.stat()
    os.utime(rip, ns=(info.st_atime_ns, info.st_mtime_ns + 5_000_000_000))


def same_size_and_time(rip: Path) -> None:
    """Different bytes at the start, with the size and modification time put back."""
    info = rip.stat()
    data = bytearray(rip.read_bytes())
    data[:3] = b"XYZ"
    rip.write_bytes(bytes(data))
    os.utime(rip, ns=(info.st_atime_ns, info.st_mtime_ns))


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        (grow, "size"),
        (touch_later, "was modified"),
        (same_size_and_time, "contents changed"),
        (lambda rip: rip.unlink(), "missing"),
    ],
)
def test_a_changed_source_fails_the_plan(
    lib: Library, rip: Path, change: object, detail: str
) -> None:
    plan = fileops.new_plan("adopt", [adopt_op(lib, rip)])
    change(rip)  # type: ignore[operator]
    [problem] = fileops.validate(lib, plan, item_state=states("only_copy"))
    assert problem.op_id == 1
    assert problem.message.startswith(fileops.FILE_CHANGED)
    assert detail in problem.message


def test_junk_in_the_target_folder_is_ignored(lib: Library, rip: Path) -> None:
    folder = lib.paths.music / "Artist" / "Unsorted"
    put(folder / "Other Song.mp3")
    plan = fileops.new_plan("adopt", [adopt_op(lib, rip)])
    assert plan.operations[0].target_folder is not None
    assert plan.operations[0].target_folder.names == ["Other Song.mp3"]
    for junk in (".DS_Store", "._Song.mp3", "Thumbs.db", "desktop.ini"):
        put(folder / junk)
    put(folder / "Third Song.mp3")  # other new files are fine too
    assert fileops.validate(lib, plan, item_state=states("only_copy")) == []


def test_a_new_file_at_the_target_fails_the_plan(lib: Library, rip: Path) -> None:
    plan = fileops.new_plan("adopt", [adopt_op(lib, rip)])
    put(lib.paths.music / "Artist" / "Unsorted" / "SONG.mp3")  # any case
    [problem] = fileops.validate(lib, plan, item_state=states("only_copy"))
    assert "something new is at Music/Artist/Unsorted/Song.mp3" in problem.message


def test_a_target_folder_that_became_a_file_fails_the_plan(lib: Library, rip: Path) -> None:
    plan = fileops.new_plan("adopt", [adopt_op(lib, rip)])
    put(lib.paths.music / "Artist" / "Unsorted", b"a file where a folder goes")
    [problem] = fileops.validate(lib, plan, item_state=states("only_copy"))
    assert "isn't a folder any more" in problem.message


def test_the_item_state_is_checked(lib: Library, rip: Path) -> None:
    plan = fileops.new_plan("adopt", [adopt_op(lib, rip)])
    [problem] = fileops.validate(lib, plan, item_state=states("review"))
    assert "is now review, was only_copy" in problem.message
    [problem] = fileops.validate(lib, plan, item_state=states(None))
    assert "is now gone" in problem.message
    [problem] = fileops.validate(lib, plan)  # can't check it: not assumed fine
    assert "can't check the state" in problem.message


def test_check_op_is_the_same_check_for_one_operation(lib: Library, rip: Path) -> None:
    other = put(rip.parent / "Other.mp3", b"other")
    plan = fileops.new_plan("adopt", [adopt_op(lib, rip), adopt_op(lib, other, "Music/B/O.mp3")])
    grow(other)
    first, second = plan.operations
    assert fileops.check_op(lib, first, item_state=states("only_copy")) is None
    problem = fileops.check_op(lib, second, item_state=states("only_copy"))
    assert problem is not None
    assert problem.op_id == 2
    assert [p.op_id for p in fileops.validate(lib, plan, item_state=states("only_copy"))] == [2]


def test_sha1_head_reads_only_the_first_megabyte(tmp_path: Path) -> None:
    head = bytes(range(256)) * 4096  # exactly 1 MiB
    a = put(tmp_path / "a", head + b"tail one")
    b = put(tmp_path / "b", head + b"tail two")
    assert fileops.sha1_head(a) == fileops.sha1_head(b)
    assert fileops.sha1_head(put(tmp_path / "c", b"x" + head[1:])) != fileops.sha1_head(a)
