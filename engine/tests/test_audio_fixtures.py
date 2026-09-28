"""The generated audio fixtures exist and have the expected codec and length."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from conftest import (
    MELODY_A_PITCH_CLASSES,
    MELODY_B_PITCH_CLASSES,
    MELODY_SECONDS,
    SILENCE_SECONDS,
    AudioFixtures,
    melody_frequencies,
)


def probe(ffprobe: Path, path: Path) -> tuple[str, float]:
    result = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    return data["streams"][0]["codec_name"], float(data["format"]["duration"])


@pytest.mark.parametrize(
    ("name", "codec", "seconds"),
    [
        ("melody_a_m4a", "aac", MELODY_SECONDS),
        ("melody_a_mp3", "mp3", MELODY_SECONDS),
        ("melody_b_m4a", "aac", MELODY_SECONDS),
        ("noise_m4a", "aac", MELODY_SECONDS),
        ("noise_silence_m4a", "aac", MELODY_SECONDS + SILENCE_SECONDS),
    ],
)
def test_fixture_codec_and_duration(
    audio: AudioFixtures, ffprobe_path: Path, name: str, codec: str, seconds: float
) -> None:
    path = getattr(audio, name)
    actual_codec, duration = probe(ffprobe_path, path)
    assert actual_codec == codec
    assert duration == pytest.approx(seconds, abs=0.2)


def test_melodies_differ(audio: AudioFixtures) -> None:
    def digest(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    assert digest(audio.melody_a_m4a) != digest(audio.melody_b_m4a)


def test_melodies_share_no_pitch_class() -> None:
    assert not set(MELODY_A_PITCH_CLASSES) & set(MELODY_B_PITCH_CLASSES)
    a = melody_frequencies(MELODY_A_PITCH_CLASSES)
    assert len(a) == 40
    assert len(set(a)) > 5  # an actual tune, not one repeated tone
