"""Finding ffmpeg, ffprobe, fpcalc and deno."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import REQUIRE_TOOLS, FakeTool

from musicorg import tools
from musicorg.config import Config
from musicorg.errors import EXIT_TOOL_MISSING, ToolMissingError

Isolate = Callable[..., None]


# ---- version parsing -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "output", "expected"),
    [
        (
            "ffmpeg",
            "ffmpeg version 7.1.1 Copyright (c) 2000-2025 the FFmpeg developers\n"
            "built with Apple clang",
            "7.1.1",
        ),
        (
            "ffmpeg",
            "ffmpeg version N-117821-g2a1b3c4d5e-tessus  https://evermeet.cx/ffmpeg/",
            "N-117821-g2a1b3c4d5e-tessus",
        ),
        (
            "ffmpeg",
            "ffmpeg version 2025-01-15-git-4f3c9f2f03-full_build-www.gyan.dev",
            "2025-01-15-git-4f3c9f2f03-full_build-www.gyan.dev",
        ),
        ("ffprobe", "ffprobe version 7.1.1 Copyright (c) 2007-2025", "7.1.1"),
        ("fpcalc", "fpcalc version 1.6.1 (FFmpeg Lavc61.19.100 Lavf61.7.100 SwR5.3.100)", "1.6.1"),
        (
            "deno",
            "deno 2.3.1 (stable, release, x86_64-apple-darwin)\nv8 13.5\ntypescript 5.8",
            "2.3.1",
        ),
        ("deno", "deno 2.5.0+1a2b3c4 (canary, release, aarch64-apple-darwin)", "2.5.0+1a2b3c4"),
        ("deno", "something else entirely", None),
        ("ffmpeg", "", None),
    ],
)
def test_parse_version(tool: str, output: str, expected: str | None) -> None:
    assert tools.parse_version(tool, output) == expected


@pytest.mark.parametrize(
    ("version", "ok"),
    [
        ("1.46.3", False),
        ("2.0.0", False),
        ("2.2.12", False),
        ("2.3.0", True),
        ("2.3.1", True),
        ("2.10.0", True),
        ("3.0.0", True),
        ("garbage", False),
    ],
)
def test_deno_minimum_version(version: str, ok: bool) -> None:
    assert tools.deno_version_ok(version) is ok


# ---- searching -------------------------------------------------------------------------


def test_finds_tool_on_path(fake_tool: FakeTool, isolated_path: Isolate, tmp_path: Path) -> None:
    exe = fake_tool(tmp_path / "bin", "ffmpeg", "ffmpeg version 7.1.1 Copyright")
    isolated_path([tmp_path / "bin"])
    info = tools.find("ffmpeg")
    assert info.ok
    assert info.path == exe
    assert info.version == "7.1.1"
    assert info.found_in == "PATH"


def test_fallback_folder_when_not_on_path(
    fake_tool: FakeTool, isolated_path: Isolate, tmp_path: Path
) -> None:
    exe = fake_tool(tmp_path / "fallback", "fpcalc", "fpcalc version 1.6.1")
    isolated_path([tmp_path / "empty"], fallback=[tmp_path / "fallback"])
    info = tools.find("fpcalc")
    assert info.ok
    assert info.path == exe
    assert info.found_in == "fallback folder"


def test_path_comes_before_fallback(
    fake_tool: FakeTool, isolated_path: Isolate, tmp_path: Path
) -> None:
    on_path = fake_tool(tmp_path / "bin", "fpcalc", "fpcalc version 1.6.1")
    fake_tool(tmp_path / "fallback", "fpcalc", "fpcalc version 1.5.0")
    isolated_path([tmp_path / "bin"], fallback=[tmp_path / "fallback"])
    assert tools.find("fpcalc").path == on_path


def test_config_override_wins(fake_tool: FakeTool, isolated_path: Isolate, tmp_path: Path) -> None:
    fake_tool(tmp_path / "bin", "ffmpeg", "ffmpeg version 7.1.1")
    custom = fake_tool(tmp_path / "custom", "ffmpeg", "ffmpeg version 6.0")
    isolated_path([tmp_path / "bin"])
    cfg = Config.load()
    cfg.set_tool_path("ffmpeg", custom)
    info = tools.find("ffmpeg", cfg)
    assert info.path == custom
    assert info.version == "6.0"
    assert info.found_in == "config.json"


def test_config_override_can_be_a_folder(
    fake_tool: FakeTool, isolated_path: Isolate, tmp_path: Path
) -> None:
    custom = fake_tool(tmp_path / "custom", "deno", "deno 2.4.0 stable")
    isolated_path([])
    cfg = Config.load()
    cfg.set_tool_path("deno", tmp_path / "custom")
    assert tools.find("deno", cfg).path == custom


def test_bad_override_is_ignored_with_note(
    fake_tool: FakeTool, isolated_path: Isolate, tmp_path: Path
) -> None:
    exe = fake_tool(tmp_path / "bin", "ffmpeg", "ffmpeg version 7.1.1")
    isolated_path([tmp_path / "bin"])
    cfg = Config.load()
    cfg.set_tool_path("ffmpeg", tmp_path / "nowhere" / "ffmpeg")
    info = tools.find("ffmpeg", cfg)
    assert info.ok
    assert info.path == exe
    assert info.note is not None and "config.json" in info.note


def test_missing_tool(isolated_path: Isolate, tmp_path: Path) -> None:
    isolated_path([tmp_path / "empty"], fallback=[tmp_path / "also-empty"])
    info = tools.find("fpcalc")
    assert not info.ok
    assert info.path is None
    assert info.problem == "not found"


@pytest.mark.parametrize("version", ["1.46.3", "2.2.12"])
def test_old_deno_counts_as_missing(
    fake_tool: FakeTool, isolated_path: Isolate, tmp_path: Path, version: str
) -> None:
    exe = fake_tool(tmp_path / "bin", "deno", f"deno {version} stable")
    isolated_path([tmp_path / "bin"])
    info = tools.find("deno")
    assert not info.ok
    assert info.path == exe
    assert info.version == version
    assert "too old" in (info.problem or "")
    with pytest.raises(ToolMissingError) as err:
        tools.require("deno")
    assert "too old" in err.value.message
    assert err.value.exit_code == EXIT_TOOL_MISSING


def test_new_enough_deno_found_after_old_one(
    fake_tool: FakeTool, isolated_path: Isolate, tmp_path: Path
) -> None:
    fake_tool(tmp_path / "bin", "deno", "deno 1.46.3 stable")
    newer = fake_tool(tmp_path / "home-deno", "deno", "deno 2.3.0 stable")
    isolated_path([tmp_path / "bin"], fallback=[tmp_path / "home-deno"])
    info = tools.find("deno")
    assert info.ok
    assert info.path == newer
    assert info.note is not None and "too old" in info.note


def test_deno_without_version_is_unusable(
    fake_tool: FakeTool, isolated_path: Isolate, tmp_path: Path
) -> None:
    fake_tool(tmp_path / "bin", "deno", "hello")
    isolated_path([tmp_path / "bin"])
    assert "version" in (tools.find("deno").problem or "")


def test_tool_that_fails_is_unusable(
    fake_tool: FakeTool, isolated_path: Isolate, tmp_path: Path
) -> None:
    fake_tool(tmp_path / "bin", "ffprobe", "dyld: Library not loaded", exit_code=3)
    isolated_path([tmp_path / "bin"])
    info = tools.find("ffprobe")
    assert not info.ok
    assert "error code 3" in (info.problem or "")


def test_require_returns_path(fake_tool: FakeTool, isolated_path: Isolate, tmp_path: Path) -> None:
    exe = fake_tool(tmp_path / "bin", "fpcalc", "fpcalc version 1.6.1")
    isolated_path([tmp_path / "bin"])
    assert tools.require("fpcalc") == exe


def test_require_explains_missing_tool(isolated_path: Isolate, tmp_path: Path) -> None:
    isolated_path([tmp_path / "empty"])
    with pytest.raises(ToolMissingError) as err:
        tools.require("ffmpeg")
    assert err.value.tool == "ffmpeg"
    assert "isn't installed" in err.value.message
    assert "config.json" in err.value.message


def test_unknown_tool_name_is_a_bug() -> None:
    with pytest.raises(ValueError):
        tools.find("vlc")


# ---- hints and folders -----------------------------------------------------------------


def test_install_hints_mac() -> None:
    assert "brew install ffmpeg" in tools.install_hint("ffmpeg", "darwin")
    assert "brew install ffmpeg" in tools.install_hint("ffprobe", "darwin")
    assert "brew install chromaprint" in tools.install_hint("fpcalc", "darwin")
    deno = tools.install_hint("deno", "darwin")
    assert "brew install deno" in deno and "deno.land/install.sh" in deno


def test_install_hints_windows() -> None:
    assert "winget install ffmpeg" in tools.install_hint("ffmpeg", "win32")
    assert "winget install DenoLand.Deno" in tools.install_hint("deno", "win32")
    fpcalc = tools.install_hint("fpcalc", "win32")
    assert "chromaprint/releases" in fpcalc and "isn't on winget" in fpcalc


def test_fallback_dirs_mac() -> None:
    dirs = tools.fallback_dirs("darwin")
    assert Path("/usr/local/bin") in dirs
    assert Path("/opt/homebrew/bin") in dirs
    assert Path("/opt/local/bin") in dirs
    assert Path.home() / ".deno" / "bin" in dirs


def test_fallback_dirs_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(Path("C:/Users/me/AppData/Local")))
    monkeypatch.setenv("ProgramData", str(Path("C:/ProgramData")))
    dirs = tools.fallback_dirs("win32")
    assert Path("C:/Users/me/AppData/Local/Microsoft/WinGet/Links") in dirs
    assert Path("C:/ProgramData/chocolatey/bin") in dirs


# ---- the real tools (needed in CI) -----------------------------------------------------


@pytest.mark.parametrize("name", tools.TOOL_NAMES)
def test_real_tool_present(name: str) -> None:
    info = tools.find(name)
    if not info.ok:
        message = tools.missing_message(info)
        if REQUIRE_TOOLS:
            pytest.fail(message)
        pytest.skip(message)
    assert info.path is not None and info.path.exists()
    assert info.version
    if name == "deno":
        assert tools.deno_version_ok(info.version)


@pytest.mark.skipif(os.name == "nt", reason="uses a POSIX file mode")
def test_non_executable_file_is_ignored(isolated_path: Isolate, tmp_path: Path) -> None:
    folder = tmp_path / "bin"
    folder.mkdir()
    (folder / "ffmpeg").write_text("#!/bin/sh\necho 'ffmpeg version 1'\n")
    isolated_path([folder])
    assert tools.find("ffmpeg").path is None
