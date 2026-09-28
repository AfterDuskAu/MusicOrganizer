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
    assert "newer version of Music Organizer" in caught.value.message


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
