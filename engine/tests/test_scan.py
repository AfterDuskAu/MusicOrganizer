"""scan: sources, the read-only scan of rip folders, and rebuilding the index.

The fixture is a small rips folder made of generated audio with messy names, plus the
traps: hidden files, junk, a folder link, a library inside the source, and a file that
isn't audio at all.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fileops_support import place, sha256, symlink_or_skip
from mutagen.id3 import ID3, TSSE

from musicorg import fileops, library, normalize, scan, state, tags
from musicorg.errors import NotFoundError, UserError
from musicorg.index import Index, item_id, open_index
from musicorg.library import Library
from musicorg.normalize import Parsed
from musicorg.tags import TrackTags

RIPS = {
    "Drake - Hotline Bling (Official Video).mp3": "mp3",
    "Flight Facilities - Crave You (Adventure Club Remix).m4a": "m4a",
    "Sub Folder/Pegboard Nerds - Hero [Monstercat Release].flac": "flac",
    "Sub Folder/Deeper/Nightcore - Angel With A Shotgun.opus": "opus",
    "YOASOBI「夜に駆ける」Official Music Video.ogg": "ogg",
    "Some Artist - Some Clip.webm": "webm",
}


@pytest.fixture
def rips(tmp_path: Path, samples: dict[str, Path]) -> Path:
    """A rips folder with messy names, junk, a file that isn't audio, and a text file."""
    folder = tmp_path / "Rips"
    for name, kind in RIPS.items():
        place(samples[kind], folder / name)
    (folder / "garbage.mp3").write_bytes(os.urandom(5000))
    (folder / "notes.txt").write_text("not music")
    (folder / ".hidden.mp3").write_bytes(samples["mp3"].read_bytes())
    (folder / "._Drake - Hotline Bling (Official Video).mp3").write_bytes(b"\0" * 100)
    (folder / ".DS_Store").write_bytes(b"\0" * 100)
    return folder


@pytest.fixture
def index(lib: Library) -> Any:
    with open_index(lib.paths, write=True) as opened:
        yield opened


def add(lib: Library, index: Index, folder: Path) -> str:
    return str(scan.add_source(lib, index, folder)["id"])


def items_by_name(index: Index) -> dict[str, dict[str, Any]]:
    return {Path(item["rel_path"]).name: item for item in index.items()}


def snapshot(folder: Path) -> dict[str, tuple[str, int]]:
    """Every file and folder under `folder`: its content hash (files) and mtime."""
    found = {}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            found[str(path)] = ("link", 0)
        elif path.is_file():
            found[str(path)] = (sha256(path), path.stat().st_mtime_ns)
        else:
            found[str(path)] = ("folder", path.stat().st_mtime_ns)
    return found


# ---- scanning --------------------------------------------------------------------------


def test_a_scan_indexes_the_audio(lib: Library, index: Index, rips: Path) -> None:
    source = add(lib, index, rips)
    result = scan.scan(lib, index)
    assert (result.files, result.new, result.unchanged, result.gone) == (7, 7, 0, 0)
    assert result.unreadable == 1
    items = items_by_name(index)
    assert sorted(items) == sorted([Path(n).name for n in RIPS] + ["garbage.mp3"])

    drake = items["Drake - Hotline Bling (Official Video).mp3"]
    assert drake["id"] == item_id(source, "Drake - Hotline Bling (Official Video).mp3")
    assert (drake["parsed_artist"], drake["parsed_title"]) == ("Drake", "Hotline Bling")
    assert (drake["codec"], drake["ext"], drake["state"]) == ("mp3", ".mp3", "new")
    assert drake["duration_s"] == pytest.approx(3.0, abs=0.1)
    assert drake["bitrate_kbps"] > 100
    assert len(drake["sha1_head"]) == 40
    assert drake["parsed_json"]["junk_removed"] == ["official video"]
    assert drake["raw_tags_json"]["extra"]["encoder"].startswith("Lavf")

    remix = items["Flight Facilities - Crave You (Adventure Club Remix).m4a"]
    assert remix["parsed_version_json"] == ["remix:adventure club"]
    nested = items["Nightcore - Angel With A Shotgun.opus"]
    assert nested["rel_path"] == "Sub Folder/Deeper/Nightcore - Angel With A Shotgun.opus"
    assert nested["parse_confidence"] < 0.5
    assert items["YOASOBI「夜に駆ける」Official Music Video.ogg"]["parsed_title"] == "夜に駆ける"
    assert items["Some Artist - Some Clip.webm"]["flags_json"] == ["not_adoptable"]
    garbage = items["garbage.mp3"]
    assert garbage["flags_json"] == ["unreadable"]
    assert garbage["codec"] is None


def test_the_source_is_left_exactly_as_it_was(lib: Library, index: Index, rips: Path) -> None:
    drake = rips / "Drake - Hotline Bling (Official Video).mp3"
    link_target = place(drake, rips.parent / "Elsewhere" / "linked.mp3")
    symlink_or_skip(rips / "Linked Folder", link_target.parent)
    before = snapshot(rips)
    add(lib, index, rips)
    scan.scan(lib, index)
    scan.scan(lib, index)
    assert snapshot(rips) == before


def test_a_rescan_reads_only_new_and_changed_files(
    lib: Library, index: Index, rips: Path, monkeypatch: pytest.MonkeyPatch, samples: dict
) -> None:
    add(lib, index, rips)
    scan.scan(lib, index)
    probed: list[Path] = []
    real = tags.probe
    monkeypatch.setattr(tags, "probe", lambda path: probed.append(Path(path)) or real(path))

    again = scan.scan(lib, index)
    assert (again.new, again.changed, again.unchanged) == (0, 0, 7)
    assert probed == []

    changed = rips / "Drake - Hotline Bling (Official Video).mp3"
    with open(changed, "ab") as f:
        f.write(b"\0" * 10)
    place(samples["m4a"], rips / "New Artist - New Song.m4a")
    (rips / "garbage.mp3").unlink()
    third = scan.scan(lib, index)
    assert (third.new, third.changed, third.unchanged, third.gone) == (1, 1, 5, 1)
    assert sorted(p.name for p in probed) == sorted([changed.name, "New Artist - New Song.m4a"])
    assert "garbage.mp3" not in items_by_name(index)


def test_folder_links_are_never_followed(lib: Library, index: Index, rips: Path) -> None:
    outside = place(
        rips / "Drake - Hotline Bling (Official Video).mp3", rips.parent / "Out" / "x.mp3"
    )
    symlink_or_skip(rips / "Linked Folder", outside.parent)
    symlink_or_skip(rips / "linked file.mp3", outside)
    add(lib, index, rips)
    result = scan.scan(lib, index)
    assert result.skipped_links == 2
    assert result.files == 7
    assert "x.mp3" not in items_by_name(index)


def test_a_library_inside_a_source_is_skipped(
    lib: Library, index: Index, tmp_path: Path, samples: dict[str, Path]
) -> None:
    # Registered directly: `sources add` refuses a folder holding the library, but the
    # library could be moved into a source later.
    with state.edit(lib.paths.state_file) as st:
        st.data["sources"] = {
            "s_whole": {"path": str(tmp_path), "added_at": "2026-01-01T00:00:00Z"}
        }
    place(samples["mp3"], tmp_path / "Rips" / "Artist - Song.mp3")
    place(samples["m4a"], lib.paths.music / "Artist" / "In The Library.m4a")
    other = tmp_path / "Rips" / "Another Library"
    library.init(other, remember=False)
    place(samples["m4a"], other / "Music" / "In Another Library.m4a")
    result = scan.scan(lib, index)
    assert list(items_by_name(index)) == ["Artist - Song.mp3"]
    assert sorted(result.skipped_libraries) == sorted([str(lib.root), str(other.resolve())])


def test_a_missing_source_keeps_its_items(lib: Library, index: Index, rips: Path) -> None:
    source = add(lib, index, rips)
    scan.scan(lib, index)
    rips.rename(rips.parent / "Unplugged Drive")
    result = scan.scan(lib, index)
    assert result.missing_sources == [source]
    assert len(index.items()) == 7


@pytest.mark.skipif(sys.platform == "win32" or os.getuid() == 0, reason="POSIX permissions")
def test_an_unreadable_folder_keeps_its_items(lib: Library, index: Index, rips: Path) -> None:
    add(lib, index, rips)
    scan.scan(lib, index)
    sub = rips / "Sub Folder"
    sub.chmod(0)
    try:
        result = scan.scan(lib, index)
    finally:
        sub.chmod(0o755)
    assert result.unreadable_folders == ["Sub Folder"]
    assert result.gone == 0
    assert len(index.items()) == 7


def test_scanning_one_source(lib: Library, index: Index, rips: Path, tmp_path: Path) -> None:
    other = tmp_path / "More Rips"
    place(rips / "Drake - Hotline Bling (Official Video).mp3", other / "Drake - Headlines.mp3")
    first, second = add(lib, index, rips), add(lib, index, other)
    result = scan.scan(lib, index, source_ids=[second])
    assert result.sources == [second]
    assert [i["source_id"] for i in index.items()] == [second]
    with pytest.raises(NotFoundError, match="no source s_nope"):
        scan.scan(lib, index, source_ids=["s_nope"])
    assert first


def test_a_scan_needs_a_source(lib: Library, index: Index) -> None:
    with pytest.raises(UserError, match="sources add"):
        scan.scan(lib, index)


# ---- parsing the names again -----------------------------------------------------------

# Rips laid out as the owner's are, with their own tags: file → (title tag, artist tag).
TAGGED = {
    "Nirvana/Unknown Album/Come As You Are R.mp3": ("Come As You Are R", "Nirvana"),
    "Alessia Cara/Unknown Album/Here (Lucian Remix)).mp3": ("Here (Lucian Remix))", "Alessia Cara"),
    "Jill Scott/Beautifully Human/17 Still Here [Acoustic Version].m4a": (
        "Still Here [Acoustic Version]",
        "Jill Scott",
    ),
    "Band/Unknown Album/Melody R.mp3": ("Melody", "Band"),  # the version only in the name
    "Kid Cudi/Unknown Album/Solo Dolo R 1.mp3": (None, "Kid Cudi"),  # no title tag
}
# The title a copy named from each rip is given (normalize.full_title).
FULL_TITLES = {
    "Come As You Are R.mp3": "Come As You Are R",
    "Here (Lucian Remix)).mp3": "Here (Lucian Remix)",
    "17 Still Here [Acoustic Version].m4a": "Still Here (Acoustic Version)",
    "Melody R.mp3": "Melody R",
    "Solo Dolo R 1.mp3": "Solo Dolo R 1",
    "Flight Facilities - Crave You (Adventure Club Remix).m4a": "Crave You (Adventure Club Remix)",
    "Drake - Hotline Bling (Official Video).mp3": "Hotline Bling",
    "Nightcore - Angel With A Shotgun.opus": "Angel With A Shotgun (Nightcore)",
}


@pytest.fixture
def tagged_rips(rips: Path, samples: dict[str, Path]) -> Path:
    """The rips folder, with some rips that carry a title and an artist tag."""
    for name, (title, artist) in TAGGED.items():
        path = place(samples[Path(name).suffix.lstrip(".")], rips / name)
        tags.write_tags(path, TrackTags(title=title, artist=artist))
    return rips


def test_parse_again_is_the_scans_own_parse(lib: Library, index: Index, tagged_rips: Path) -> None:
    add(lib, index, tagged_rips)
    scan.scan(lib, index)
    items = items_by_name(index)
    assert len(items) == len(RIPS) + len(TAGGED) + 1  # and garbage.mp3, which has no tags
    for name, item in items.items():
        again = scan.parse_again(item)
        stored = Parsed.from_dict(item["parsed_json"])
        assert again == stored, name
        assert again.version_words == stored.version_words, name
        assert (again.title, again.artist, list(again.version_tokens), again.confidence) == (
            item["parsed_title"],
            item["parsed_artist"],
            item["parsed_version_json"],
            item["parse_confidence"],
        ), name
    assert items["Come As You Are R.mp3"]["parsed_json"]["version_words"] == ["R"]
    titles = {name: normalize.full_title(scan.parse_again(items[name])) for name in FULL_TITLES}
    assert titles == FULL_TITLES


def test_parse_again_serves_an_index_from_an_older_engine(
    lib: Library, index: Index, tagged_rips: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An older engine stored no version words, and a scan doesn't read an unchanged file
    again. The names are parsed again from what the index holds; no rip is opened."""
    add(lib, index, tagged_rips)
    scan.scan(lib, index)
    old_items = {}
    for name, item in items_by_name(index).items():
        parsed_json = {k: v for k, v in item["parsed_json"].items() if k != "version_words"}
        old_items[name] = {**item, "parsed_json": parsed_json}

    def no_reading(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("parse_again read a rip")

    for name in ("read_tags", "read_extra", "probe"):
        monkeypatch.setattr(tags, name, no_reading)
    monkeypatch.setattr(fileops, "sha1_head", no_reading)
    before = snapshot(tagged_rips)

    stale = Parsed.from_dict(old_items["Come As You Are R.mp3"]["parsed_json"])
    assert (stale.title, stale.version_tokens, stale.version_words) == (
        "Come As You Are",
        ("remix",),
        (),
    )
    assert normalize.full_title(stale) is None  # the stored parse can't name a copy
    for name, title in FULL_TITLES.items():
        again = scan.parse_again(old_items[name])
        assert normalize.full_title(again) == title, name
        assert again == Parsed.from_dict(old_items[name]["parsed_json"]), name  # same matching
    assert snapshot(tagged_rips) == before


def test_parse_again_needs_only_the_path_and_the_stored_tags() -> None:
    item = {
        "rel_path": "Phantogram/Unknown Album/Black Out Days R(slowed).mp3",
        "raw_tags_json": {"tags": {"title": "Black Out Days R(slowed)", "artist": "Phantogram"}},
    }
    parsed = scan.parse_again(item)
    assert (parsed.artist, parsed.title) == ("Phantogram", "Black Out Days")
    assert parsed.version_tokens == ("slowed", "remix")
    assert normalize.full_title(parsed) == "Black Out Days R (slowed)"
    untagged = scan.parse_again({"rel_path": "Artist - Song (X Remix).mp3", "raw_tags_json": None})
    assert (untagged.artist, normalize.full_title(untagged)) == ("Artist", "Song (X Remix)")


# ---- flags -----------------------------------------------------------------------------


@pytest.fixture
def mp3_320(ffmpeg_path: Path, tmp_path: Path) -> Callable[[str, str], Path]:
    """A 320 kbps MP3 with the given name and encoder tag."""

    def make(name: str, encoder: str) -> Path:
        path = tmp_path / "Rips" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [str(ffmpeg_path), "-v", "error", "-nostdin", "-f", "lavfi", "-i",
             "sine=frequency=440:duration=2", "-c:a", "libmp3lame", "-b:a", "320k", str(path)],
            check=True,
        )  # fmt: skip
        id3 = ID3(path)
        id3.delall("TSSE")
        id3.add(TSSE(encoding=3, text=[encoder]))
        id3.save(path)
        return path

    return make


def test_suspect_upscales(
    lib: Library, index: Index, mp3_320: Callable[[str, str], Path], tmp_path: Path
) -> None:
    mp3_320("Artist - Clean Rip.mp3", "LAME 3.100")
    mp3_320("Artist - Video Rip (Official Video).mp3", "LAME 3.100")
    mp3_320("Artist - Converter Rip.mp3", "Lavf58.29.100")
    add(lib, index, tmp_path / "Rips")
    scan.scan(lib, index)
    flagged = {name: item["flags_json"] for name, item in items_by_name(index).items()}
    assert flagged == {
        "Artist - Clean Rip.mp3": [],
        "Artist - Video Rip (Official Video).mp3": ["suspect_upscale"],
        "Artist - Converter Rip.mp3": ["suspect_upscale"],
    }


# ---- states from state.json and the library --------------------------------------------


def test_decisions_and_links_set_the_state(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    source = add(lib, index, rips)
    drake = "Drake - Hotline Bling (Official Video).mp3"
    remix = "Flight Facilities - Crave You (Adventure Club Remix).m4a"
    with state.edit(lib.paths.state_file) as st:
        st.data["decisions"] = {item_id(source, drake): {"decision": "only_copy"},
                                item_id(source, "garbage.mp3"): {"decision": "skip"}}  # fmt: skip
        st.data["superseded"] = {state.normalise_path(rips / remix): tags.new_track_id()}
    adopted = place(samples["flac"], lib.paths.music / "A" / "Hero.flac")
    tags.write_tags(adopted, TrackTags(
        source="rip_copy", only_copy=True,
        origin_path=str(rips / "Sub Folder" / "Pegboard Nerds - Hero [Monstercat Release].flac"),
    ))  # fmt: skip
    scan.scan_library(lib, index)
    scan.scan(lib, index)
    states = {name: item["state"] for name, item in items_by_name(index).items()}
    assert states[drake] == "only_copy"
    assert states["garbage.mp3"] == "skipped"
    assert states[remix] == "superseded"
    assert states["Pegboard Nerds - Hero [Monstercat Release].flac"] == "adopted"
    assert states["Some Artist - Some Clip.webm"] == "new"


# ---- rebuilding ------------------------------------------------------------------------


def test_a_rebuild_keeps_ids_and_decisions(
    lib: Library, rips: Path, samples: dict[str, Path]
) -> None:
    queue = lib.paths.queue_file
    queue.write_bytes(b"the queue: not a cache")
    with open_index(lib.paths, write=True) as index:
        source = add(lib, index, rips)
        scan.scan(lib, index)
        before = {item["id"]: item["rel_path"] for item in index.items()}
    drake = item_id(source, "Drake - Hotline Bling (Official Video).mp3")
    with state.edit(lib.paths.state_file) as st:
        st.data["decisions"] = {drake: {"decision": "only_copy"}}
    place(samples["m4a"], lib.paths.music / "Artist" / "01 Song.m4a")

    for suffix in ("", "-wal", "-shm"):
        Path(f"{lib.paths.index_file}{suffix}").unlink(missing_ok=True)
    with open_index(lib.paths, write=True) as index:
        assert index.items() == []
        result = scan.rebuild(lib, index)
        after = {item["id"]: item["rel_path"] for item in index.items()}
        assert after == before
        assert index.item(drake)["state"] == "only_copy"
        assert [t["rel_path"] for t in index.library_tracks()] == ["Music/Artist/01 Song.m4a"]
        assert index.sources()[source]["items"] == 7
    assert result.library_tracks == 1
    assert queue.read_bytes() == b"the queue: not a cache"


def test_a_rebuild_recomputes_what_the_cache_held(lib: Library, rips: Path) -> None:
    with open_index(lib.paths, write=True) as index:
        add(lib, index, rips)
        scan.scan(lib, index)
        index.set_state(index.items()[0]["id"], "review", ["title_fuzzy"])
        scan.rebuild(lib, index)
        assert {i["state"] for i in index.items()} == {"new"}  # a cache: recomputed later


def test_saved_videos_are_the_librarys_and_never_a_rip(
    lib: Library, index: Index, rips: Path, video_mp4: Path
) -> None:
    """A rebuild finds the videos in Music/Videos/ again (files are the truth), but an
    .mp4 in a source folder, or anywhere else in Music/, isn't taken for a song."""
    saved = lib.paths.music / "Videos" / "Band" / "Song.mp4"
    stray = lib.paths.music / "Band" / "Album" / "Clip.mp4"
    for path in (saved, stray, rips / "Videos" / "Band" / "Home Movie.mp4"):
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(video_mp4, path)
    tags.write_tags(saved, tags.TrackTags(title="Song", artist="Band", musicorg_id="t_v"))
    add(lib, index, rips)
    result = scan.rebuild(lib, index)
    assert result.library_tracks == 1
    (row,) = index.library_tracks()
    assert (row["rel_path"], row["musicorg_id"]) == ("Music/Videos/Band/Song.mp4", "t_v")
    assert row["duration_s"] == pytest.approx(20, abs=0.5)
    assert "Home Movie.mp4" not in items_by_name(index)


def test_a_rebuild_with_no_sources(lib: Library, index: Index) -> None:
    result = scan.rebuild(lib, index)
    assert (result.library_tracks, result.scan.files) == (0, 0)


# ---- sources ---------------------------------------------------------------------------


def test_adding_a_source(lib: Library, index: Index, rips: Path) -> None:
    source = scan.add_source(lib, index, rips)
    assert source["id"] == state.source_id(rips)
    assert source["path"] == str(rips.resolve())
    assert state.sources(lib.load_state().data) == {
        source["id"]: {"path": str(rips.resolve()), "added_at": source["added_at"]}
    }
    listed = scan.list_sources(lib, index)
    assert [(s["id"], s["items"], s["available"]) for s in listed] == [(source["id"], 0, True)]


def test_a_source_containing_the_library_is_refused(lib: Library, index: Index) -> None:
    with pytest.raises(UserError, match="contains the library"):
        scan.add_source(lib, index, lib.root.parent)
    assert state.sources(lib.load_state().data) == {}


def test_a_source_inside_the_library_is_refused(lib: Library, index: Index) -> None:
    for folder in (lib.root, lib.paths.reports, lib.paths.music):
        with pytest.raises(UserError, match="inside the library"):
            scan.add_source(lib, index, folder)


def test_odd_sources_are_refused(lib: Library, index: Index, rips: Path) -> None:
    with pytest.raises(UserError, match="doesn't exist"):
        scan.add_source(lib, index, rips / "Missing")
    with pytest.raises(UserError, match="is a file"):
        scan.add_source(lib, index, rips / "notes.txt")
    scan.add_source(lib, index, rips)
    with pytest.raises(UserError, match="already a source"):
        scan.add_source(lib, index, rips)
    with pytest.raises(UserError, match="overlaps"):
        scan.add_source(lib, index, rips / "Sub Folder")  # inside a source
    assert len(state.sources(lib.load_state().data)) == 1


def test_a_folder_holding_a_source_is_refused(lib: Library, index: Index, tmp_path: Path) -> None:
    inner = tmp_path / "Outer" / "Inner"
    inner.mkdir(parents=True)
    scan.add_source(lib, index, inner)
    with pytest.raises(UserError, match="overlaps"):
        scan.add_source(lib, index, tmp_path / "Outer")


def test_removing_a_source_forgets_it_but_touches_nothing(
    lib: Library, index: Index, rips: Path
) -> None:
    source = add(lib, index, rips)
    scan.scan(lib, index)
    drake = item_id(source, "Drake - Hotline Bling (Official Video).mp3")
    with state.edit(lib.paths.state_file) as st:
        st.data["decisions"] = {drake: {"decision": "skip"}}
    before = snapshot(rips)
    removed = scan.remove_source(lib, index, source)
    assert removed["items_forgotten"] == 7
    assert index.items() == []
    assert snapshot(rips) == before
    assert state.decisions(lib.load_state().data) == {drake: {"decision": "skip"}}
    with pytest.raises(NotFoundError):
        scan.remove_source(lib, index, source)

    add(lib, index, rips)  # added again: the decision reattaches
    scan.scan(lib, index)
    assert index.item(drake)["state"] == "skipped"


def test_a_source_that_vanished_is_listed_as_unavailable(
    lib: Library, index: Index, rips: Path
) -> None:
    add(lib, index, rips)
    rips.rename(rips.parent / "gone")
    assert [s["available"] for s in scan.list_sources(lib, index)] == [False]


def test_state_json_is_the_record_of_sources(lib: Library, index: Index, rips: Path) -> None:
    source = add(lib, index, rips)
    with state.edit(lib.paths.state_file) as st:
        del st.data["sources"][source]
        st.data["sources"]["s_other"] = {"path": str(rips), "added_at": "2026-01-01T00:00:00Z"}
    scan.scan(lib, index)
    assert list(index.sources()) == ["s_other"]
