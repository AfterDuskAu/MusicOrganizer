"""Shared test setup.

- Every test gets its own settings folder (MUSICORG_HOME), so the real config and logs
  are never touched.
- Audio fixtures are generated once per run with ffmpeg. If ffmpeg is missing the audio
  tests are skipped, unless MUSICORG_REQUIRE_TOOLS=1 (as in CI), which makes them fail.
- Tests marked `live` talk to the real network and only run with MUSICORG_LIVE=1.
- The system Trash is replaced by a folder for every test. The one test marked
  `integration` uses the real Trash; it only runs with MUSICORG_INTEGRATION=1, and never
  in CI.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from musicorg import fileops, library, state, tools

REQUIRE_TOOLS = os.environ.get("MUSICORG_REQUIRE_TOOLS") == "1"
RUN_LIVE = os.environ.get("MUSICORG_LIVE") == "1"
RUN_INTEGRATION = os.environ.get("MUSICORG_INTEGRATION") == "1" and not os.environ.get("CI")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: talks to the real network; needs MUSICORG_LIVE=1")
    config.addinivalue_line(
        "markers",
        "integration: uses the real system Trash; needs MUSICORG_INTEGRATION=1, never in CI",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip_live = pytest.mark.skip(reason="live test: set MUSICORG_LIVE=1 to run it")
    skip_integration = pytest.mark.skip(
        reason="uses the real Trash: set MUSICORG_INTEGRATION=1 to run it (never in CI)"
    )
    for item in items:
        if "live" in item.keywords and not RUN_LIVE:
            item.add_marker(skip_live)
        if "integration" in item.keywords and not RUN_INTEGRATION:
            item.add_marker(skip_integration)


@pytest.fixture(autouse=True)
def app_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private settings/log/cache folder for each test."""
    home = tmp_path_factory.mktemp("app-home")
    monkeypatch.setenv("MUSICORG_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def no_time_machine_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never run `tmutil`. The Time Machine warning tests supply its output."""
    monkeypatch.setattr(library, "_tmutil_destinationinfo", lambda: None)


@dataclass
class FakeTrash:
    """Stands in for the system Trash: files are moved into `folder`."""

    folder: Path
    sent: list[Path] = field(default_factory=list)


@pytest.fixture(autouse=True)
def fake_trash(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> FakeTrash:
    """Tests never touch the real Trash, except the one marked `integration`."""
    trash = FakeTrash(tmp_path_factory.mktemp("fake-trash"))
    if request.node.get_closest_marker("integration"):
        return trash

    def send(path: Path) -> None:
        os.replace(path, trash.folder / f"{len(trash.sent)}-{path.name}")
        trash.sent.append(path)

    monkeypatch.setattr(fileops, "_send_to_trash", send)
    return trash


@pytest.fixture(autouse=True)
def no_disk_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flushing to the disk itself takes up to a quarter of a second on a spinning disk,
    like the owner's iMac's, and a test can't tell the difference. So here fsync only
    checks that its file is open, and F_FULLFSYNC is skipped. test_fileops.py checks that
    the engine does use F_FULLFSYNC on macOS, and in what order it flushes."""
    monkeypatch.setattr(os, "fsync", os.fstat)
    monkeypatch.setattr(fileops, "_FULL_FSYNC", False)
    monkeypatch.setattr(state, "_sync", os.fstat)


@pytest.fixture
def lib(tmp_path: Path) -> Iterator[library.Library]:
    """A new library, open for writing."""
    root = tmp_path / "Library"
    library.init(root)
    opened = library.open(root, write=True, command="pytest")
    yield opened
    opened.close()


def require_tool(name: str) -> Path:
    """The real tool's path, or skip (fail when MUSICORG_REQUIRE_TOOLS=1)."""
    info = tools.find(name)
    if info.ok and info.path is not None:
        return info.path
    message = f"{name} is needed for this test. {tools.missing_message(info)}"
    if REQUIRE_TOOLS:
        pytest.fail(message)
    pytest.skip(message)


# ---- fake tools ------------------------------------------------------------------------

FakeTool = Callable[..., Path]


@pytest.fixture
def fake_tool() -> FakeTool:
    """Make a tiny program that prints `output` and exits with `exit_code`.

    A shell script on macOS and Linux, a .cmd file on Windows.
    """

    def make(folder: Path, name: str, output: str, exit_code: int = 0) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            path = folder / f"{name}.cmd"
            path.write_text(f"@echo off\r\necho {output}\r\nexit /b {exit_code}\r\n")
        else:
            path = folder / name
            path.write_text(f"#!/bin/sh\necho '{output}'\nexit {exit_code}\n")
            path.chmod(0o755)
        return path

    return make


@pytest.fixture
def isolated_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[..., None]:
    """Make the tool search see only the given folders, never the real tools."""

    def use(path_dirs: list[Path], fallback: list[Path] | None = None) -> None:
        monkeypatch.setenv("PATH", os.pathsep.join(str(p) for p in path_dirs))
        monkeypatch.setattr(tools, "fallback_dirs", lambda platform=None: list(fallback or []))

    return use


FFMPEG_VERSION_OUTPUT = "ffmpeg version 7.1.1 Copyright 2000-2025 the FFmpeg developers"
FFPROBE_VERSION_OUTPUT = "ffprobe version 7.1.1 Copyright 2000-2025 the FFmpeg developers"
FPCALC_VERSION_OUTPUT = "fpcalc version 1.6.1"
DENO_VERSION_OUTPUT = "deno 2.3.1 stable release"


@pytest.fixture
def all_fake_tools(fake_tool: FakeTool, isolated_path: Callable[..., None], tmp_path: Path) -> Path:
    """All four tools present and working, as fakes on an isolated PATH."""
    folder = tmp_path / "fake-bin"
    fake_tool(folder, "ffmpeg", FFMPEG_VERSION_OUTPUT)
    fake_tool(folder, "ffprobe", FFPROBE_VERSION_OUTPUT)
    fake_tool(folder, "fpcalc", FPCALC_VERSION_OUTPUT)
    fake_tool(folder, "deno", DENO_VERSION_OUTPUT)
    isolated_path([folder])
    return folder


# ---- generated audio -------------------------------------------------------------------

SAMPLE_RATE = 44100
MELODY_SECONDS = 20
NOTE_SECONDS = 0.5
SILENCE_SECONDS = 2
# Melody A and B use different pitch classes, so neither is an octave shift of the other.
MELODY_A_PITCH_CLASSES = (0, 2, 4, 7, 9)  # C D E G A
MELODY_B_PITCH_CLASSES = (1, 3, 6, 8, 10)  # C# D# F# G# A#


@dataclass(frozen=True)
class AudioFixtures:
    melody_a_m4a: Path
    melody_a_mp3: Path
    melody_b_m4a: Path
    noise_m4a: Path
    noise_silence_m4a: Path  # the same noise with 2 s of silence in front


def melody_frequencies(pitch_classes: tuple[int, ...]) -> list[float]:
    """A fixed 40-note sequence over two octaves, one note per 0.5 s."""
    notes = int(MELODY_SECONDS / NOTE_SECONDS)
    freqs = []
    for i in range(notes):
        pitch_class = pitch_classes[(i * 3 + i // 5) % len(pitch_classes)]
        octave = 4 + (i * 7 // 5) % 2
        midi = 12 * (octave + 1) + pitch_class
        freqs.append(440.0 * 2 ** ((midi - 69) / 12))
    return freqs


def melody_filter(pitch_classes: tuple[int, ...], seconds: float = MELODY_SECONDS) -> str:
    """An aevalsrc source whose frequency changes every 0.5 s (piecewise on floor(2t))."""
    steps = 1 / NOTE_SECONDS
    freq = "+".join(
        f"{f:.3f}*eq(floor(t*{steps:g}),{i})"
        for i, f in enumerate(melody_frequencies(pitch_classes))
    )
    return f"aevalsrc=exprs='0.5*sin(2*PI*t*({freq}))':sample_rate={SAMPLE_RATE}:duration={seconds}"


def _ffmpeg(ffmpeg: Path, *args: str) -> None:
    result = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed making a test fixture:\n{result.stderr}")


AAC = ("-c:a", "aac", "-b:a", "128k")
MP3 = ("-c:a", "libmp3lame", "-b:a", "192k")


@pytest.fixture(scope="session")
def ffmpeg_path() -> Path:
    return require_tool("ffmpeg")


@pytest.fixture(scope="session")
def ffprobe_path() -> Path:
    return require_tool("ffprobe")


@pytest.fixture(scope="session")
def audio(ffmpeg_path: Path, tmp_path_factory: pytest.TempPathFactory) -> AudioFixtures:
    folder = tmp_path_factory.mktemp("audio")
    fx = AudioFixtures(
        melody_a_m4a=folder / "melody_a.m4a",
        melody_a_mp3=folder / "melody_a.mp3",
        melody_b_m4a=folder / "melody_b.m4a",
        noise_m4a=folder / "noise.m4a",
        noise_silence_m4a=folder / "noise_silence.m4a",
    )
    melody_a = melody_filter(MELODY_A_PITCH_CLASSES)
    _ffmpeg(ffmpeg_path, "-f", "lavfi", "-i", melody_a, *AAC, str(fx.melody_a_m4a))
    _ffmpeg(ffmpeg_path, "-f", "lavfi", "-i", melody_a, *MP3, str(fx.melody_a_mp3))
    _ffmpeg(
        ffmpeg_path,
        "-f",
        "lavfi",
        "-i",
        melody_filter(MELODY_B_PITCH_CLASSES),
        *AAC,
        str(fx.melody_b_m4a),
    )
    _ffmpeg(
        ffmpeg_path,
        "-f",
        "lavfi",
        "-i",
        f"anoisesrc=color=pink:seed=42:sample_rate={SAMPLE_RATE}"
        f":amplitude=0.5:duration={MELODY_SECONDS}",
        *AAC,
        str(fx.noise_m4a),
    )
    # Concatenate the existing noise file, rather than generating new noise.
    _ffmpeg(
        ffmpeg_path,
        "-f",
        "lavfi",
        "-t",
        str(SILENCE_SECONDS),
        "-i",
        f"anullsrc=channel_layout=mono:sample_rate={SAMPLE_RATE}",
        "-i",
        str(fx.noise_m4a),
        "-filter_complex",
        "[0:a][1:a]concat=n=2:v=0:a=1[out]",
        "-map",
        "[out]",
        *AAC,
        str(fx.noise_silence_m4a),
    )
    return fx


SAMPLE_SECONDS = 3
# Every kind of file the engine meets. The last three are indexed but not adopted.
SAMPLE_ENCODINGS: dict[str, tuple[str, ...]] = {
    "m4a": AAC,
    "mp3": MP3,
    "flac": ("-c:a", "flac"),
    "opus": ("-c:a", "libopus", "-b:a", "96k"),
    "ogg": ("-c:a", "libvorbis", "-q:a", "4"),
    "webm": ("-c:a", "libopus", "-b:a", "96k"),
    "wav": ("-c:a", "pcm_s16le"),
    "aac": ("-c:a", "aac", "-b:a", "128k", "-f", "adts"),
}


@pytest.fixture(scope="session")
def samples(ffmpeg_path: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """3 seconds of melody A as `sample.<ext>` for each of SAMPLE_ENCODINGS, plus
    `bare.mp3` with no ID3 tags at all. Copy one before changing it."""
    folder = tmp_path_factory.mktemp("samples")
    melody = melody_filter(MELODY_A_PITCH_CLASSES, seconds=SAMPLE_SECONDS)
    files = {}
    for ext, encoding in SAMPLE_ENCODINGS.items():
        files[ext] = folder / f"sample.{ext}"
        _ffmpeg(ffmpeg_path, "-f", "lavfi", "-i", melody, *encoding, str(files[ext]))
    files["bare.mp3"] = folder / "bare.mp3"
    _ffmpeg(
        ffmpeg_path,
        "-f",
        "lavfi",
        "-i",
        melody,
        *MP3,
        "-id3v2_version",
        "0",
        "-write_id3v1",
        "0",
        str(files["bare.mp3"]),
    )
    return files
