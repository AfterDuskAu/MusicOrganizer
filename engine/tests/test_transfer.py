"""A profile's export and import (2026-10-12): what an export holds and leaves out, where
it may be made, and what an import brings into a library."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from conftest import AudioFixtures

from musicorg import config, fileops, library, listening, pipeline, scan, tags, transfer
from musicorg.errors import FileOperationError, OutsideLibraryError, UserError
from musicorg.index import open_index
from musicorg.library import Library


def song(lib: Library, audio: AudioFixtures, rel: str, title: str) -> str:
    """A tagged song in the library, indexed. Returns its MUSICORG_ID."""
    path = lib.paths.music / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(audio.melody_a_m4a, path)
    track_id = tags.new_track_id()
    tags.write_tags(path, tags.TrackTags(title=title, artist="Band", musicorg_id=track_id))
    with open_index(lib.paths, write=True) as index:
        scan.scan_library(lib, index)
    return track_id


@pytest.fixture
def filled(lib: Library, audio: AudioFixtures, app_home: Path) -> dict[str, Any]:
    """A library with two songs, a cover, a saved video's file, records, and a film and
    a video kept outside it."""
    first = song(lib, audio, "Band/Tunes (2020)/01 One.m4a", "One")
    second = song(lib, audio, "Band/Tunes (2020)/02 Two.m4a", "Two")
    (lib.paths.music / "Band/Tunes (2020)/cover.jpg").write_bytes(b"a cover")
    (lib.paths.music / "Band/Tunes (2020)/.DS_Store").write_bytes(b"junk")
    listening.set_favourite(lib, first, True)
    made = listening.create_playlist(lib, "Road Trip")[-1]["id"]
    listening.set_playlist_tracks(lib, made, [first, second])
    folders = config.media_folders()
    (folders["movies"] / "Series" / "A Show").mkdir(parents=True)
    (folders["movies"] / "A Film (2020).mp4").write_bytes(b"film" * 100)
    (folders["movies"] / "Series" / "A Show" / "A Show S01E01.mkv").write_bytes(b"episode")
    (folders["movies"] / "notes.txt").write_bytes(b"not a film")
    folders["media"].mkdir(parents=True, exist_ok=True)
    (folders["media"] / "A Talk.mp4").write_bytes(b"talk")
    return {"ids": [first, second], "playlist": made}


def files_in(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def test_an_export_holds_what_was_ticked_and_says_so(
    lib: Library, filled: dict[str, Any], tmp_path: Path
) -> None:
    drive = tmp_path / "drive"
    drive.mkdir()
    ticks: list[tuple[int, int, str]] = []
    said = transfer.export_profile(
        lib, drive, parts=transfer.PARTS, profile="D", app={"look": "warm"},
        tick=lambda done, total, name: ticks.append((done, total, name)), today="2026-10-12",
    )  # fmt: skip
    folder = Path(said["folder"])
    assert folder == drive / "Bear Media Export D 2026-10-12"
    assert files_in(folder) == [
        "Movies/A Film (2020).mp4",
        "Movies/Series/A Show/A Show S01E01.mkv",
        "Music/Band/Tunes (2020)/01 One.m4a",
        "Music/Band/Tunes (2020)/02 Two.m4a",
        "Music/Band/Tunes (2020)/cover.jpg",
        "Videos/A Talk.mp4",
        "export.json",
        "records/state.json",
        "settings/addons.json",
        "settings/app.json",
    ]
    manifest = json.loads((folder / "export.json").read_text())
    assert (manifest["format"], manifest["profile"]) == (1, "D")
    assert manifest["parts"]["music"]["files"] == 3  # two songs and their cover
    assert manifest["parts"]["movies"]["files"] == 2 and manifest["parts"]["videos"]["files"] == 1
    assert manifest["left_out"][:2] == ["sign-ins", "paired devices"]
    # The copies are whole, and the library is as it was.
    original = lib.paths.music / "Band/Tunes (2020)/01 One.m4a"
    assert (folder / "Music/Band/Tunes (2020)/01 One.m4a").read_bytes() == original.read_bytes()
    assert json.loads((folder / "settings/app.json").read_text()) == {"look": "warm"}
    assert ticks[0][:2] == (0, 6) and ticks[-1] == (6, 6, "")
    # A second export the same day is a second folder: nothing is written into the first.
    again = transfer.export_profile(lib, drive, parts=["records"], profile="D", today="2026-10-12")
    assert Path(again["folder"]).name == "Bear Media Export D 2026-10-12 (2)"
    assert files_in(Path(again["folder"])) == ["export.json", "records/state.json"]


def test_no_sign_in_is_ever_in_an_export(
    lib: Library, filled: dict[str, Any], tmp_path: Path
) -> None:
    secret = "k" + "9" * 30  # a made-up key, as an add-on's own set-up carries one
    plain = {"id": "a.plain", "name": "Plain", "address": "https://plain.invalid/manifest.json",
             "version": "1", "types": ["movie"]}  # fmt: skip
    own = {"id": "a.own", "name": "Set Up", "version": "1", "types": ["movie"],
           "address": f"https://own.invalid/{secret}/manifest.json"}  # fmt: skip
    config.save_addons([plain, own], offered=[])
    config.accounts_path().parent.mkdir(parents=True, exist_ok=True)
    config.accounts_path().write_text(json.dumps({"spotify": {"refresh_token": secret}}))
    assert transfer.contents(lib)["parts"]["settings"] == {
        "addons": 1, "addons_left_out": ["Set Up"],
    }  # fmt: skip
    drive = tmp_path / "drive"
    drive.mkdir()
    said = transfer.export_profile(lib, drive, parts=transfer.PARTS, profile="D")
    folder = Path(said["folder"])
    everything = b"".join(p.read_bytes() for p in folder.rglob("*") if p.is_file())
    assert secret.encode() not in everything
    assert json.loads((folder / "settings/addons.json").read_text())["addons"] == [plain]
    assert "the add-on Set Up (set up again by its own address)" in said["left_out"]
    assert not any("accounts" in name or "devices" in name for name in files_in(folder))


def test_an_export_is_never_made_inside_the_library_or_a_missing_folder(
    lib: Library, filled: dict[str, Any], tmp_path: Path
) -> None:
    for inside in (lib.root, lib.paths.music, lib.paths.reports):
        inside.mkdir(exist_ok=True)
        with pytest.raises(UserError):
            transfer.export_profile(lib, inside, parts=["records"], profile="D")
    with pytest.raises(UserError):
        transfer.export_profile(lib, tmp_path / "nowhere", parts=["records"], profile="D")
    with pytest.raises(UserError):
        transfer.export_profile(lib, tmp_path, parts=["everything"], profile="D")
    assert [p.name for p in lib.root.iterdir() if "Export" in p.name] == []


def test_export_file_keeps_to_its_folder_and_never_writes_over(
    tmp_path: Path, lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = fileops.make_export_folder(tmp_path, "An Export", forbidden=[lib.root])
    source = tmp_path / "song.m4a"
    source.write_bytes(b"sound")
    first = fileops.export_file(source, root, "Music/A/song.m4a", forbidden=[lib.root])
    second = fileops.export_file(b"other", root, "Music/A/song.m4a", forbidden=[lib.root])
    assert (first.name, second.name) == ("song.m4a", "song (2).m4a")
    assert (first.read_bytes(), second.read_bytes()) == (b"sound", b"other")
    assert source.read_bytes() == b"sound"  # the original is only read
    for bad in ("../outside.m4a", "/etc/outside", "Music/../../outside"):
        with pytest.raises(OutsideLibraryError):
            fileops.export_file(source, root, bad, forbidden=[lib.root])
    with pytest.raises(UserError):
        fileops.export_file(source, lib.paths.music, "x.m4a", forbidden=[lib.root])
    for missing in (tmp_path / "gone.m4a", tmp_path):  # nothing there, and a folder
        with pytest.raises(UserError):
            fileops.export_file(missing, root, "Music/B/x.m4a", forbidden=[lib.root])
    assert not (root / "Music" / "B").exists()

    # A copy that fails part way leaves nothing behind: no half file, no temp file.
    def fails(src: Path, dst: Path) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(fileops, "_replace", fails)
    with pytest.raises(FileOperationError, match="No space left"):
        fileops.export_file(source, root, "Music/A/full.m4a", forbidden=[lib.root])
    assert sorted(p.name for p in root.rglob("*") if p.is_file()) == ["song (2).m4a", "song.m4a"]
    assert not list(tmp_path.glob("outside*"))


@pytest.fixture
def exported(lib: Library, filled: dict[str, Any], tmp_path: Path) -> Path:
    drive = tmp_path / "drive"
    drive.mkdir()
    said = transfer.export_profile(lib, drive, parts=transfer.PARTS, profile="D", app={"a": 1})
    return Path(said["folder"])


def another_library(tmp_path: Path) -> Library:
    root = tmp_path / "second library"
    library.init(root)
    return library.open(root, write=True, command="test")


def test_an_import_brings_an_export_into_another_library(
    exported: Path, filled: dict[str, Any], tmp_path: Path, app_home: Path
) -> None:
    folders = config.media_folders()
    shutil.rmtree(folders["movies"])  # another computer: nothing kept on it yet
    folders["movies"].mkdir()
    before = files_in(exported)
    other = another_library(tmp_path)
    try:
        would = transfer.import_profile(other, exported, parts=transfer.PARTS, dry_run=True)
        assert would["songs"] == {"brought": 2, "already": 0, "beside": 1}
        assert would["films"] == {"brought": 3, "already": 0} and would["records"] == "taken"
        assert files_in(other.paths.music) == []  # a dry run writes nothing

        said = transfer.import_profile(other, exported, parts=transfer.PARTS)
        assert files_in(other.paths.music) == [
            "Band/Tunes (2020)/01 One.m4a", "Band/Tunes (2020)/02 Two.m4a",
            "Band/Tunes (2020)/cover.jpg",
        ]  # fmt: skip
        with open_index(other.paths, write=False) as index:
            assert sorted(row["musicorg_id"] for row in index.library_tracks()) == sorted(
                filled["ids"]
            )
        # The records came with it: the heart, and the playlist with its songs.
        shown = listening.get(other)
        assert shown["favourites"] == [filled["ids"][0]]
        assert [(p["name"], p["track_ids"]) for p in shown["playlists"]] == [
            ("Road Trip", filled["ids"])
        ]
        # Films and videos went where kept ones go, with a series in its folder.
        assert files_in(folders["movies"]) == [
            "A Film (2020).mp4", "Series/A Show/A Show S01E01.mkv", "Videos/A Talk.mp4",
        ]  # fmt: skip
        assert (said["records"], said["app"], said["profile"]) == ("taken", {"a": 1}, "D")

        # Brought in again, nothing is added: every song and film is here already, and
        # the library has records of its own now.
        again = transfer.import_profile(other, exported, parts=transfer.PARTS)
        assert again["songs"] == {"brought": 0, "already": 2, "beside": 0}
        assert again["films"] == {"brought": 0, "already": 3} and again["records"] == "kept_own"
        assert len(files_in(other.paths.music)) == 3

        # The songs came in one batch, and undo takes them back out.
        pipeline.undo(other, said["batch_id"])
        assert [f for f in files_in(other.paths.music) if f.endswith(".m4a")] == []
    finally:
        other.close()
    assert files_in(exported) == before  # the export itself was only read


def test_a_library_with_records_of_its_own_keeps_them(
    exported: Path, tmp_path: Path, audio: AudioFixtures
) -> None:
    other = another_library(tmp_path)
    try:
        mine = song(other, audio, "Mine/Own.m4a", "Own")
        listening.set_favourite(other, mine, True)
        said = transfer.import_profile(other, exported, parts=["records"])
        assert said["records"] == "kept_own"
        assert listening.get(other)["favourites"] == [mine]
        assert listening.get(other)["playlists"] == []
    finally:
        other.close()


def test_only_a_whole_export_of_this_kind_is_read(
    exported: Path, tmp_path: Path, lib: Library
) -> None:
    with pytest.raises(UserError, match="isn't a Bear Media export"):
        transfer.import_profile(lib, tmp_path, parts=["music"])
    (exported / "export.json").write_text(json.dumps({"format": 99}))
    with pytest.raises(UserError, match="newer Bear Media"):
        transfer.import_profile(lib, exported, parts=["music"])


def test_one_export_or_import_at_a_time_with_how_far_it_has_got() -> None:
    import threading

    runner = transfer.Runner()
    assert runner.status() == {"working": None, "last": None}
    go, ended = threading.Event(), []

    def work(tick: transfer.Tick) -> dict[str, Any]:
        tick(1, 4, "a song.m4a")
        go.wait(5)
        return {"folder": "somewhere"}

    runner.start("export", work, finished=ended.append)
    with pytest.raises(UserError, match="under way already"):
        runner.start("import", work)
    assert runner.status()["working"] == {
        "kind": "export",
        "done": 1,
        "total": 4,
        "name": "a song.m4a",
    }
    go.set()
    runner.wait(5)
    done = {"kind": "export", "ok": True, "summary": {"folder": "somewhere"}}
    assert runner.status() == {"working": None, "last": done} and ended == [done]

    def fails(tick: transfer.Tick) -> dict[str, Any]:
        raise UserError("That folder isn't there.")

    runner.start("import", fails)
    runner.wait(5)
    assert runner.status()["last"] == {
        "kind": "import", "ok": False, "message": "That folder isn't there.",
    }  # fmt: skip
