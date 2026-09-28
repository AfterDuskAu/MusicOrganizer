"""config.json: defaults, round trip, unknown keys, atomic writes, damaged files."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from musicorg import config
from musicorg.config import Config
from musicorg.errors import ConfigError


def test_app_dirs_follow_musicorg_home(app_home: Path) -> None:
    dirs = config.app_dirs()
    assert dirs.config == app_home / "config"
    assert dirs.logs == app_home / "logs"
    assert dirs.cache == app_home / "cache"
    assert config.config_path() == app_home / "config" / "config.json"


def test_platform_dirs_used_without_musicorg_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MUSICORG_HOME")
    dirs = config.app_dirs()
    for folder in (dirs.config, dirs.logs, dirs.cache):
        assert "MusicOrganizer" in folder.parts


def test_missing_file_gives_defaults() -> None:
    cfg = Config.load()
    assert cfg.last_library is None
    assert cfg.tool_path("ffmpeg") is None
    assert cfg.data["schema"] == config.CONFIG_SCHEMA
    assert not cfg.path.exists()  # loading never creates the file


def test_round_trip(tmp_path: Path) -> None:
    cfg = Config.load()
    cfg.last_library = tmp_path / "Music Organizer Library"
    cfg.set_tool_path("fpcalc", tmp_path / "bin" / "fpcalc")
    cfg.save()

    again = Config.load()
    assert again.last_library == tmp_path / "Music Organizer Library"
    assert again.tool_path("fpcalc") == tmp_path / "bin" / "fpcalc"
    assert again.data == cfg.data


def test_unknown_keys_are_preserved() -> None:
    path = config.config_path()
    path.parent.mkdir(parents=True)
    original = {
        "schema": 1,
        "last_library": None,
        "future_setting": {"nested": [1, 2, 3]},
        "tools": {"deno": "/somewhere/deno", "some_future_tool": "/x"},
    }
    path.write_text(json.dumps(original), encoding="utf-8")

    cfg = Config.load()
    cfg.last_library = Path("/Volumes/Music/Library")
    cfg.save()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["future_setting"] == {"nested": [1, 2, 3]}
    assert saved["tools"]["some_future_tool"] == "/x"
    assert saved["tools"]["deno"] == "/somewhere/deno"
    assert saved["last_library"] == str(Path("/Volumes/Music/Library"))


def test_non_ascii_values_survive() -> None:
    cfg = Config.load()
    cfg.last_library = Path("/Users/zoë/Música/ライブラリ")
    cfg.save()
    assert Config.load().last_library == Path("/Users/zoë/Música/ライブラリ")


def test_save_leaves_no_temp_files() -> None:
    cfg = Config.load()
    cfg.save()
    cfg.save()
    assert sorted(p.name for p in config.config_path().parent.iterdir()) == ["config.json"]


def test_failed_write_keeps_old_file(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = Config.load()
    cfg.last_library = Path("/old")
    cfg.save()
    before = config.config_path().read_bytes()

    def crash(src: object, dst: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", crash)
    cfg.last_library = Path("/new")
    with pytest.raises(ConfigError, match="Couldn't save your settings"):
        cfg.save()

    assert config.config_path().read_bytes() == before
    assert sorted(p.name for p in config.config_path().parent.iterdir()) == ["config.json"]


def test_damaged_file_gives_plain_message() -> None:
    path = config.config_path()
    path.parent.mkdir(parents=True)
    path.write_text('{"last_library": "/x",,}', encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        Config.load()
    assert "damaged" in info.value.message
    assert str(path) in info.value.message


def test_non_object_file_is_damaged() -> None:
    path = config.config_path()
    path.parent.mkdir(parents=True)
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ConfigError, match="damaged"):
        Config.load()


def test_tool_path_expands_home() -> None:
    cfg = Config.load()
    cfg.data["tools"] = {"deno": "~/.deno/bin/deno"}
    assert cfg.tool_path("deno") == Path.home() / ".deno" / "bin" / "deno"


def test_tool_path_ignores_bad_values() -> None:
    cfg = Config.load()
    cfg.data["tools"] = "not a dict"
    assert cfg.tool_path("deno") is None
    cfg.set_tool_path("deno", Path("/x/deno"))
    assert cfg.tool_path("deno") == Path("/x/deno")
    cfg.set_tool_path("deno", None)
    assert cfg.tool_path("deno") is None


def test_ensure_app_dir_refuses_other_folders(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        config.ensure_app_dir(tmp_path / "somewhere-else")
    assert not (tmp_path / "somewhere-else").exists()


def test_writable_problem(app_home: Path) -> None:
    assert config.writable_problem(config.app_dirs().logs) is None
    assert config.app_dirs().logs.is_dir()


def test_writable_problem_reports_blocked_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder")
    monkeypatch.setenv("MUSICORG_HOME", str(blocker))
    problem = config.writable_problem(config.app_dirs().logs)
    assert problem is not None
    assert "Couldn't create the folder" in problem
