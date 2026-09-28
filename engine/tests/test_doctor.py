"""`musicorg doctor` output and exit codes."""

from __future__ import annotations

import json
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

import pytest
from conftest import FakeTool

from musicorg import cli, doctor
from musicorg.errors import EXIT_OK, EXIT_TOOL_MISSING, EXIT_USER_ERROR


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = cli.main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def packages_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend yt-dlp, yt-dlp-ejs and ytmusicapi are installed, whatever the real venv has."""
    monkeypatch.setattr(doctor.metadata, "version", lambda dist: "2026.9.1")


def test_all_good(
    capsys: pytest.CaptureFixture[str], all_fake_tools: Path, packages_installed: None
) -> None:
    code, out, err = run(capsys, "doctor")
    assert code == EXIT_OK, out
    assert "✗" not in out
    for line in (
        "✓ Python",
        "✓ ffmpeg: 7.1.1",
        "✓ ffprobe: 7.1.1",
        "✓ fpcalc: 1.6.1",
        "✓ deno: 2.3.1",
        "✓ yt-dlp: 2026.9.1",
        "✓ yt-dlp-ejs",
        "✓ ytmusicapi",
        "✓ Settings file",
        "✓ Config folder",
        "✓ Log folder",
    ):
        assert line in out
    assert "Everything is in place." in out


def test_missing_tool_has_fix_and_exit_code(
    capsys: pytest.CaptureFixture[str],
    fake_tool: FakeTool,
    isolated_path: Callable[..., None],
    tmp_path: Path,
    packages_installed: None,
) -> None:
    folder = tmp_path / "bin"
    fake_tool(folder, "ffmpeg", "ffmpeg version 7.1.1")
    fake_tool(folder, "ffprobe", "ffprobe version 7.1.1")
    fake_tool(folder, "deno", "deno 2.1.0 stable")  # too old
    isolated_path([folder])  # and no fpcalc at all

    code, out, _ = run(capsys, "doctor")
    assert code == EXIT_TOOL_MISSING
    assert "✗ fpcalc: not found" in out
    assert "✗ deno:" in out and "too old" in out
    assert out.count("Fix: ") == 2
    assert "2 problems found" in out


def test_missing_package(
    capsys: pytest.CaptureFixture[str], all_fake_tools: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def version(dist: str) -> str:
        if dist == "yt-dlp-ejs":
            raise metadata.PackageNotFoundError(dist)
        return "1.0"

    monkeypatch.setattr(doctor.metadata, "version", version)
    code, out, _ = run(capsys, "doctor")
    assert code == EXIT_USER_ERROR
    assert "✗ yt-dlp-ejs: not installed" in out
    assert 'pip install -U "yt-dlp[default]"' in out


def test_damaged_config_is_reported_not_fatal(
    capsys: pytest.CaptureFixture[str],
    all_fake_tools: Path,
    app_home: Path,
    packages_installed: None,
) -> None:
    (app_home / "config").mkdir()
    (app_home / "config" / "config.json").write_text("{oops", encoding="utf-8")
    code, out, _ = run(capsys, "doctor")
    assert code == EXIT_USER_ERROR
    assert "✗ Settings file" in out and "damaged" in out
    assert "✓ ffmpeg" in out  # the other checks still ran


def test_unwritable_folders(
    capsys: pytest.CaptureFixture[str],
    all_fake_tools: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    packages_installed: None,
) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder")
    monkeypatch.setenv("MUSICORG_HOME", str(blocker))
    code, out, _ = run(capsys, "doctor")
    assert code == EXIT_USER_ERROR
    assert "✗ Config folder" in out
    assert "✗ Log folder" in out


def test_config_override_used(
    capsys: pytest.CaptureFixture[str],
    all_fake_tools: Path,
    fake_tool: FakeTool,
    tmp_path: Path,
    packages_installed: None,
) -> None:
    from musicorg.config import Config

    custom = fake_tool(tmp_path / "custom", "fpcalc", "fpcalc version 1.5.1")
    cfg = Config.load()
    cfg.set_tool_path("fpcalc", custom)
    cfg.save()
    code, out, _ = run(capsys, "doctor")
    assert code == EXIT_OK
    assert f"✓ fpcalc: 1.5.1  {custom}" in out


def test_json(
    capsys: pytest.CaptureFixture[str], all_fake_tools: Path, packages_installed: None
) -> None:
    code, out, _ = run(capsys, "doctor", "--json")
    assert code == EXIT_OK
    data = json.loads(out)
    assert data["ok"] is True
    names = [c["name"] for c in data["checks"]]
    assert names == [
        "Python",
        "ffmpeg",
        "ffprobe",
        "fpcalc",
        "deno",
        "yt-dlp",
        "yt-dlp-ejs",
        "ytmusicapi",
        "Settings file",
        "Config folder",
        "Log folder",
    ]
    assert all(set(c) == {"kind", "name", "ok", "detail", "fix"} for c in data["checks"])


def test_real_packages_are_installed() -> None:
    """The engine's own dependencies, as installed by pip install -e "engine[dev]"."""
    for dist in ("yt-dlp", "yt-dlp-ejs", "ytmusicapi"):
        assert metadata.version(dist)
