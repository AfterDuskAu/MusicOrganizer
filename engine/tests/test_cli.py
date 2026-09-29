"""The `musicorg` command: entry point, global options, exit codes, stubs, status."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from musicorg import __version__, cli, library, status
from musicorg.config import Config
from musicorg.errors import (
    EXIT_INTERNAL,
    EXIT_LOCKED,
    EXIT_OK,
    EXIT_TOOL_MISSING,
    EXIT_USER_ERROR,
)


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = cli.main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


# ---- entry points ----------------------------------------------------------------------


def _installed_script() -> str:
    script = shutil.which("musicorg", path=sysconfig.get_path("scripts"))
    assert script, 'the musicorg command isn\'t installed; run pip install -e "engine[dev]"'
    return script


def test_console_script_version() -> None:
    result = subprocess.run([_installed_script(), "--version"], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.strip() == f"musicorg {__version__}"


def test_python_dash_m() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "musicorg", "--version"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert __version__ in result.stdout


def test_stub_through_console_script() -> None:
    result = subprocess.run(
        [_installed_script(), "match"], capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == EXIT_USER_ERROR
    assert "not implemented yet (step 06)" in result.stderr
    assert result.stdout == ""


def test_doctor_output_is_utf8_through_a_pipe() -> None:
    """On Windows a pipe defaults to the ANSI code page, which can't encode ✓ or ✗."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    result = subprocess.run([_installed_script(), "doctor"], capture_output=True, env=env)
    text = result.stdout.decode("utf-8")
    assert "Music Organizer doctor" in text
    assert "✓" in text or "✗" in text


# ---- parsing ---------------------------------------------------------------------------


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "--version")
    assert code == EXIT_OK
    assert out.strip() == f"musicorg {__version__}"


def test_help(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "--help")
    assert code == EXIT_OK
    for command in ("init", "status", "doctor", "sources", "queue", "serve"):
        assert command in out


def test_no_command_shows_help(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys)
    assert code == EXIT_USER_ERROR
    assert "usage: musicorg" in err
    assert out == ""


def test_unknown_command_is_user_error(capsys: pytest.CaptureFixture[str]) -> None:
    """argparse would exit with 2, which means "library locked" here."""
    code, out, err = run(capsys, "frobnicate")
    assert code == EXIT_USER_ERROR
    assert "invalid choice" in err
    assert "--help" in err
    assert out == ""


def test_bad_option_value_is_user_error(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys, "match", "--limit", "lots")
    assert code == EXIT_USER_ERROR
    assert "invalid int value" in err


def test_group_needs_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys, "sources")
    assert code == EXIT_USER_ERROR
    assert "required" in err


@pytest.mark.parametrize(
    ("args", "step"),
    [
        (["match", "--limit", "10", "--rescan"], "06"),
        (["report", "--out", "/tmp/r"], "07"),
        (["review", "export", "r.csv", "--include-auto"], "07"),
        (["review", "import", "r.csv"], "07"),
        (["plan", "replace", "--only", "auto", "--limit", "5", "--stage-only"], "09b"),
        (["plan", "adopt", "--include-not-found"], "09b"),
        (["plan", "show", "p_1"], "09b"),
        (["apply", "p_1"], "09b"),
        (["queue", "run"], "09a"),
        (["queue", "status"], "09a"),
        (["queue", "pause"], "09a"),
        (["queue", "resume"], "09a"),
        (["lyrics", "--missing"], "10"),
        (["artwork", "--missing"], "10"),
        (["serve"], "11"),
        (["doctor", "--update-ytdlp"], "09a"),
        (["doctor", "--rollback-ytdlp"], "09a"),
    ],
)
def test_stubs(capsys: pytest.CaptureFixture[str], args: list[str], step: str) -> None:
    code, out, err = run(capsys, *args)
    assert code == EXIT_USER_ERROR
    assert f"not implemented yet (step {step})" in err
    assert out == ""


def test_stub_error_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "--json", "serve")
    assert code == EXIT_USER_ERROR
    data = json.loads(out)
    assert data["ok"] is False
    assert data["error"]["exit_code"] == EXIT_USER_ERROR
    assert "step 11" in data["error"]["message"]


# ---- global options --------------------------------------------------------------------


def test_global_options_before_command(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code, out, _ = run(capsys, "--json", "--library", str(tmp_path), "status")
    assert code == EXIT_OK
    assert json.loads(out)["library"] == str(tmp_path)


def test_global_options_after_command(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code, out, _ = run(capsys, "status", "--library", str(tmp_path), "--json")
    assert code == EXIT_OK
    assert json.loads(out)["library"] == str(tmp_path)


def test_option_before_command_not_reset_by_command(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "--json", "status", "--verbose")
    assert code == EXIT_OK
    json.loads(out)  # still JSON


def test_library_path_expands_home(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "status", "--json", "--library", "~/Music Organizer Library")
    assert code == EXIT_OK
    assert json.loads(out)["library"] == str(Path.home() / "Music Organizer Library")


# ---- init ------------------------------------------------------------------------------


@pytest.fixture
def plenty_of_space(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library, "_free_bytes", lambda folder: 500 * 1000**3)


def test_init_creates_the_library_and_prints_the_layout(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = tmp_path / "Music Organizer Library"
    code, out, err = run(capsys, "init", str(root))
    assert code == EXIT_OK, err
    assert f"Created a new library at {root.resolve()}" in out
    for folder in ("Music/", "_Replaced/", "_Staging/", "Reports/", ".musicorg/"):
        assert folder in out
    for folder in ("Music", "_Replaced", "_Staging/calibration", "Reports", ".musicorg"):
        assert (root / folder).is_dir()


def test_init_prints_warnings(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(library, "_free_bytes", lambda folder: 2_500_000_000)
    code, out, _ = run(capsys, "init", str(tmp_path / "Library"))
    assert code == EXIT_OK
    assert "Warnings:" in out
    assert "Only 2.5 GB is free" in out


def test_init_json(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    root = tmp_path / "Library"
    code, out, _ = run(capsys, "init", str(root), "--json")
    assert code == EXIT_OK
    data = json.loads(out)
    assert data["root"] == str(root.resolve())
    assert data["already_library"] is False
    assert str(root.resolve() / "Music") in data["created"]
    assert isinstance(data["warnings"], list)


def test_init_again_says_so(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    root = tmp_path / "Library"
    run(capsys, "init", str(root))
    code, out, _ = run(capsys, "init", str(root))
    assert code == EXIT_OK
    assert "is already a Music Organizer library" in out
    assert "Nothing needed creating." in out


def test_init_refuses_a_rips_folder(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    rips = tmp_path / "rips"
    (rips / "Artist").mkdir(parents=True)
    (rips / "Artist" / "01 Song.mp3").write_bytes(b"x")
    code, out, err = run(capsys, "init", str(rips))
    assert code == EXIT_USER_ERROR
    assert "already holds music files" in err
    assert out == ""
    assert sorted(p.name for p in rips.iterdir()) == ["Artist"]


def test_init_while_locked_exits_2(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    root = tmp_path / "Library"
    library.init(root)
    with library.open(root, write=True, command="queue run"):
        code, out, err = run(capsys, "init", str(root))
    assert code == EXIT_LOCKED
    assert "`queue run`" in err
    assert out == ""


@pytest.mark.usefixtures("plenty_of_space")
def test_status_after_init(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    root = tmp_path / "Library"
    run(capsys, "init", str(root))
    code, out, _ = run(capsys, "status", "--json")  # no --library: init remembered it
    assert code == EXIT_OK
    data = json.loads(out)
    assert data["library"] == str(root.resolve())
    assert data["is_library"] is True


def test_status_shows_warnings(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "Library"
    library.init(root)
    monkeypatch.setattr(library, "_free_bytes", lambda folder: 1_000_000_000)
    code, out, _ = run(capsys, "status", "--library", str(root))
    assert code == EXIT_OK
    assert "Warnings:" in out
    assert "Only 1.0 GB is free" in out


# ---- status ----------------------------------------------------------------------------


def test_status_without_library(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "status")
    assert code == EXIT_OK
    assert __version__ in out
    assert "none chosen yet" in out


def test_status_json_shape(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "status", "--json")
    assert code == EXIT_OK
    data = json.loads(out)
    assert data == {
        "engine_version": __version__,
        "library": None,
        "library_exists": False,
        "is_library": False,
        "sources": 0,
        "items": 0,
        "items_by_state": {},
        "low_confidence": 0,
        "tracks": 0,
        "queue": None,
        "warnings": [],
    }


def test_status_uses_last_library_from_config(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    library = tmp_path / "Library"
    (library / ".musicorg").mkdir(parents=True)
    cfg = Config.load()
    cfg.last_library = library
    cfg.save()
    code, out, _ = run(capsys, "status")
    assert code == EXIT_OK
    assert f"Library: {library}\n" in out


def test_status_missing_folder(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code, out, _ = run(capsys, "status", "--library", str(tmp_path / "nope"))
    assert code == EXIT_OK
    assert "doesn't exist" in out


def test_status_folder_that_isnt_a_library(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, out, _ = run(capsys, "status", "--library", str(tmp_path))
    assert code == EXIT_OK
    assert "isn't a Music Organizer library" in out


def test_status_with_damaged_config(capsys: pytest.CaptureFixture[str], app_home: Path) -> None:
    (app_home / "config").mkdir()
    (app_home / "config" / "config.json").write_text("{oops", encoding="utf-8")
    code, out, err = run(capsys, "status")
    assert code == EXIT_USER_ERROR
    assert "damaged" in err
    assert out == ""


# ---- errors and logging ----------------------------------------------------------------


def test_internal_error_exit_code_and_log(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, app_home: Path
) -> None:
    def boom(library: Path | None) -> dict[str, object]:
        raise RuntimeError("boom")

    monkeypatch.setattr(status, "get_status", boom)
    code, out, err = run(capsys, "status")
    assert code == EXIT_INTERNAL
    log_path = app_home / "logs" / "musicorg.log"
    assert "Something unexpected went wrong (RuntimeError: boom)" in err
    assert str(log_path) in err
    assert "Traceback" not in err  # plain English on screen...
    assert "Traceback" in log_path.read_text(encoding="utf-8")  # ...details in the log
    assert out == ""


def test_internal_error_without_log_file_shows_details(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder")
    monkeypatch.setenv("MUSICORG_HOME", str(blocker))

    def boom(library: Path | None) -> dict[str, object]:
        raise RuntimeError("boom")

    monkeypatch.setattr(status, "get_status", boom)
    code, _, err = run(capsys, "status", "--library", str(tmp_path))
    assert code == EXIT_INTERNAL
    assert "logging to the screen only" in err
    assert "Traceback" in err


def test_tool_missing_exit_code(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from musicorg.errors import ToolMissingError

    def missing(library: Path | None) -> dict[str, object]:
        raise ToolMissingError("ffmpeg", "ffmpeg isn't installed.")

    monkeypatch.setattr(status, "get_status", missing)
    code, _, err = run(capsys, "status")
    assert code == EXIT_TOOL_MISSING
    assert "ffmpeg isn't installed." in err


def test_each_run_logs_to_file(capsys: pytest.CaptureFixture[str], app_home: Path) -> None:
    run(capsys, "status")
    run(capsys, "status", "--json")
    text = (app_home / "logs" / "musicorg.log").read_text(encoding="utf-8")
    assert text.count(f"musicorg {__version__}: status") == 2


def test_verbose_logs_to_stderr_not_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "status", "--json", "--verbose")
    assert code == EXIT_OK
    json.loads(out)  # stdout is pure JSON
    assert "status" in err  # the INFO line went to stderr
