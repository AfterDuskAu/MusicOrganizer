"""state.json: defaults, round trip, unknown keys, damaged files, crash safety, source ids."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import textwrap
import unicodedata
from datetime import date
from pathlib import Path

import pytest

from musicorg import state
from musicorg.errors import StateError


@pytest.fixture
def state_file(tmp_path: Path) -> Path:
    folder = tmp_path / ".musicorg"
    folder.mkdir()
    return folder / "state.json"


def temp_files(state_file: Path) -> list[Path]:
    return sorted(state_file.parent.glob(".state.json.*.tmp"))


def test_missing_file_gives_defaults_without_writing(state_file: Path) -> None:
    loaded = state.State.load(state_file)
    assert loaded.schema == state.STATE_SCHEMA
    assert not state_file.exists()


def test_create_if_missing(state_file: Path) -> None:
    assert state.create_if_missing(state_file) is True
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["schema"] == state.STATE_SCHEMA
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", data["created_at"])

    before = state_file.read_bytes()
    assert state.create_if_missing(state_file) is False
    assert state_file.read_bytes() == before


def test_round_trip_keeps_unknown_keys(state_file: Path) -> None:
    state_file.write_text(
        json.dumps({"schema": 1, "future_section": {"nested": [1, 2]}, "sources": {}}),
        encoding="utf-8",
    )
    with state.edit(state_file) as st:
        st.data["sources"]["s_0123456789ab"] = {"path": "/Users/zo\u00eb/Rips"}

    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["future_section"] == {"nested": [1, 2]}
    assert saved["sources"] == {"s_0123456789ab": {"path": "/Users/zo\u00eb/Rips"}}
    assert saved["schema"] == 1
    assert "zo\u00eb" in state_file.read_text(encoding="utf-8")  # readable, not \u-escaped
    assert temp_files(state_file) == []


def test_edit_does_not_save_after_an_error(state_file: Path) -> None:
    state.create_if_missing(state_file)
    before = state_file.read_bytes()
    with pytest.raises(RuntimeError), state.edit(state_file) as st:
        st.data["half"] = "done"
        raise RuntimeError("stopped halfway")
    assert state_file.read_bytes() == before


@pytest.mark.parametrize(
    ("content", "detail"),
    [
        ("{oops", "line 1"),
        ("[1, 2]", "JSON object"),
        ('{"sources": {}}', "schema version is missing"),
        ('{"schema": "one"}', "schema version is missing"),
    ],
)
def test_damaged_file_is_explained(state_file: Path, content: str, detail: str) -> None:
    state_file.write_text(content, encoding="utf-8")
    with pytest.raises(StateError) as caught:
        state.State.load(state_file)
    message = caught.value.message
    assert "damaged" in message
    assert detail in message
    assert "music files are not affected" in message


def test_newer_schema_is_refused(state_file: Path) -> None:
    state_file.write_text('{"schema": 99}', encoding="utf-8")
    with pytest.raises(StateError) as caught:
        state.State.load(state_file)
    assert "newer version of Bear Media" in caught.value.message


def test_failed_rename_leaves_the_old_file(
    state_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with state.edit(state_file) as st:
        st.data["answer"] = "old"
    before = state_file.read_bytes()

    def refuse(src: object, dst: object) -> None:
        raise PermissionError(13, "Permission denied")

    loaded = state.State.load(state_file)
    loaded.data["answer"] = "new"
    with monkeypatch.context() as m:
        m.setattr(os, "replace", refuse)
        with pytest.raises(StateError) as caught:
            loaded.save()
    assert "previous version of the file is unchanged" in caught.value.message
    assert state_file.read_bytes() == before
    assert temp_files(state_file) == []


CRASH_MID_SAVE = textwrap.dedent(
    """
    import os
    import sys
    from pathlib import Path

    from musicorg import state

    def crash(fd):
        os._exit(3)  # the process dies after writing the new copy, before the rename

    state._sync = crash
    with state.edit(Path(sys.argv[1])) as st:
        st.data["answer"] = "new"
    """
)


def test_crash_mid_save_leaves_the_old_file_intact(state_file: Path) -> None:
    with state.edit(state_file) as st:
        st.data["answer"] = "older"
    with state.edit(state_file) as st:  # the day's copy is made here, before this save
        st.data["answer"] = "old"
    before = state_file.read_bytes()

    result = subprocess.run(
        [sys.executable, "-c", CRASH_MID_SAVE, str(state_file)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 3, result.stderr

    assert state_file.read_bytes() == before
    leftovers = temp_files(state_file)
    assert len(leftovers) == 1  # the half-finished save, which nothing reads
    assert json.loads(leftovers[0].read_text(encoding="utf-8"))["answer"] == "new"

    # The library carries on: the old state loads, and the next save works.
    assert state.State.load(state_file).data["answer"] == "old"
    with state.edit(state_file) as st:
        st.data["answer"] = "newer"
    assert state.State.load(state_file).data["answer"] == "newer"


# ---- a copy of each day's first version (2026-10-11) ------------------------------------


def backups(state_file: Path) -> dict[str, bytes]:
    folder = state_file.parent / state.BACKUPS
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())} if folder.is_dir() else {}


def on(monkeypatch: pytest.MonkeyPatch, day: str) -> None:
    """Make it this day, by the computer's clock."""
    monkeypatch.setattr(state, "_today", lambda: date.fromisoformat(day))


def test_the_days_first_version_is_kept(state_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    on(monkeypatch, "2026-10-11")
    assert state.create_if_missing(state_file)
    assert backups(state_file) == {}  # a library's very first save: nothing was there to keep
    first = state_file.read_bytes()
    with state.edit(state_file) as st:
        st.data["answer"] = "one"
    # The file as it was before the day's first change, byte for byte.
    assert backups(state_file) == {"state-2026-10-11.json": first}
    with state.edit(state_file) as st:
        st.data["answer"] = "two"
    state.State.load(state_file).save()
    assert backups(state_file) == {"state-2026-10-11.json": first}  # once a day, never over it

    on(monkeypatch, "2026-10-12")
    assert state.State.load(state_file).data["answer"] == "two"  # reading keeps nothing
    assert len(backups(state_file)) == 1
    yesterday = state_file.read_bytes()
    with state.edit(state_file) as st:
        st.data["answer"] = "three"
    assert backups(state_file) == {
        "state-2026-10-11.json": first, "state-2026-10-12.json": yesterday,
    }  # fmt: skip
    assert json.loads(yesterday)["answer"] == "two"
    assert temp_files(state_file) == []
    # An edit that fails saves nothing, and keeps nothing.
    on(monkeypatch, "2026-10-13")
    with pytest.raises(RuntimeError), state.edit(state_file):
        raise RuntimeError("stop")
    assert len(backups(state_file)) == 2


def test_the_newest_seven_copies_are_kept(
    state_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    on(monkeypatch, "2026-09-30")
    state.create_if_missing(state_file)
    folder = state_file.parent / state.BACKUPS
    for day in range(1, 11):
        on(monkeypatch, f"2026-10-{day:02d}")
        if day == 3:  # things that aren't the engine's copies are left where they are
            (folder / "notes.txt").write_text("mine", encoding="utf-8")
            (folder / "state-2026-10-02.json.old").write_text("mine too", encoding="utf-8")
        with state.edit(state_file) as st:
            st.data["day"] = day
    kept = backups(state_file)
    copies = [name for name in kept if name.startswith("state-") and name.endswith(".json")]
    assert copies == [f"state-2026-10-{day:02d}.json" for day in range(4, 11)]
    assert json.loads(kept["state-2026-10-04.json"])["day"] == 3  # as it was before that day
    assert json.loads(kept["state-2026-10-10.json"])["day"] == 9
    assert kept["notes.txt"] == b"mine" and kept["state-2026-10-02.json.old"] == b"mine too"
    # A clock put back a year doesn't delete a newer copy to make room for an "older" day.
    on(monkeypatch, "2025-01-01")
    with state.edit(state_file) as st:
        st.data["day"] = 99
    assert "state-2026-10-10.json" in backups(state_file)


def test_a_copy_that_cant_be_made_never_stops_the_save(
    state_file: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    on(monkeypatch, "2026-10-11")
    state.create_if_missing(state_file)
    (state_file.parent / state.BACKUPS).write_text("in the way", encoding="utf-8")  # not a folder
    with caplog.at_level("WARNING"):
        for answer in ("one", "two", "three"):
            with state.edit(state_file) as st:
                st.data["answer"] = answer
    assert state.State.load(state_file).data["answer"] == "three"
    said = [r for r in caplog.records if "Couldn't keep today's copy" in r.getMessage()]
    assert len(said) == 1  # once a day, not at every save
    assert (state_file.parent / state.BACKUPS).read_text(encoding="utf-8") == "in the way"
    # Put right, the day's copy is made at the next save.
    (state_file.parent / state.BACKUPS).unlink()
    before = state_file.read_bytes()
    with state.edit(state_file) as st:
        st.data["answer"] = "four"
    assert backups(state_file) == {"state-2026-10-11.json": before}


CRASH_MID_COPY = textwrap.dedent(
    """
    import os
    import sys
    from pathlib import Path

    from musicorg import state

    def crash(fd):
        os._exit(3)  # the process dies while the day's copy is being written

    state._sync = crash
    with state.edit(Path(sys.argv[1])) as st:
        st.data["answer"] = "new"
    """
)


def test_a_crash_while_the_copy_is_made_leaves_no_half_copy(state_file: Path) -> None:
    with state.edit(state_file) as st:
        st.data["answer"] = "old"
    before = state_file.read_bytes()
    result = subprocess.run(
        [sys.executable, "-c", CRASH_MID_COPY, str(state_file)],
        capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    assert result.returncode == 3, result.stderr
    assert state_file.read_bytes() == before and temp_files(state_file) == []
    # Nothing has a backup's name: only the unfinished temp file, which nothing reads.
    left = list(backups(state_file))
    assert len(left) == 1 and left[0].startswith(".state-") and left[0].endswith(".tmp")
    # The next save makes the day's copy properly, and the stray file is no copy of anything.
    with state.edit(state_file) as st:
        st.data["answer"] = "newer"
    whole = [name for name in backups(state_file) if not name.startswith(".")]
    assert len(whole) == 1 and backups(state_file)[whole[0]] == before


# ---- source ids ------------------------------------------------------------------------


def test_source_id_format_and_hash(tmp_path: Path) -> None:
    rips = tmp_path / "Rips"
    rips.mkdir()
    source = state.source_id(rips)
    assert re.fullmatch(r"s_[0-9a-f]{12}", source)
    digest = hashlib.sha1(state.normalise_path(rips).encode("utf-8")).hexdigest()
    assert source == "s_" + digest[:12]


def test_source_id_is_stable_across_spellings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rips = tmp_path / "Caf\u00e9 Rips"
    rips.mkdir()
    expected = state.source_id(rips)
    assert state.source_id(Path(str(rips) + os.sep)) == expected
    assert state.source_id(rips / "sub" / "..") == expected
    nfd = Path(unicodedata.normalize("NFD", str(rips)))
    assert state.source_id(nfd) == expected
    monkeypatch.chdir(tmp_path)
    assert state.source_id(Path("Caf\u00e9 Rips")) == expected


def test_source_id_follows_symlinks(tmp_path: Path) -> None:
    rips = tmp_path / "Rips"
    rips.mkdir()
    link = tmp_path / "link-to-rips"
    try:
        link.symlink_to(rips, target_is_directory=True)
    except OSError:
        pytest.skip("this system doesn't allow symlinks here")
    assert state.source_id(link) == state.source_id(rips)


def test_different_folders_get_different_ids(tmp_path: Path) -> None:
    assert state.source_id(tmp_path / "Rips A") != state.source_id(tmp_path / "Rips B")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths ignore case")
def test_source_id_ignores_case_on_windows(tmp_path: Path) -> None:
    assert state.source_id(tmp_path / "Rips") == state.source_id(tmp_path / "RIPS")


def test_rejected_candidates() -> None:
    data = {"rejected": {"i_1": ["a", "b", 3], "i_2": "not a list"}}
    assert state.rejected(data) == {"i_1": {"a", "b"}}
    assert state.rejected({}) == {}
