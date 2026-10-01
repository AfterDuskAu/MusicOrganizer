"""fingerprint: the step 08 prompt's four comparisons on the generated audio, fpcalc
missing, the whole-file cache in the index, and the research's shape checks (extra audio
at the ends, a stretch in the middle that doesn't match) on audio built from the same
generated pieces."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import (
    AAC,
    MELODY_B_PITCH_CLASSES,
    SAMPLE_RATE,
    AudioFixtures,
    _ffmpeg,
    melody_filter,
    require_tool,
)

from musicorg import fingerprint as fp
from musicorg import library
from musicorg.config import FINGERPRINT_DEFAULTS, Config
from musicorg.errors import AudioError, NotFoundError, ToolMissingError
from musicorg.index import open_index

LIMITS = dict(FINGERPRINT_DEFAULTS)


@pytest.fixture(autouse=True)
def fresh_fpcalc_lookup() -> None:
    fp._fpcalc.cache_clear()
    yield
    fp._fpcalc.cache_clear()


@pytest.fixture
def fpcalc() -> Path:
    return require_tool("fpcalc")


def compare(a: Path, b: Path) -> fp.FingerprintResult:
    return fp.compare(a, b, thresholds=LIMITS)


# ---- the step 08 prompt's tests --------------------------------------------------------


def test_same_melody_different_encodes_match(fpcalc: Path, audio: AudioFixtures) -> None:
    result = compare(audio.melody_a_m4a, audio.melody_a_mp3)
    assert result.verdict == "match", result.why
    assert result.ber <= 0.15
    assert abs(result.offset_s) < 0.5
    assert result.overlap_ratio >= 0.9
    assert result.a_coverage >= 0.9 and result.b_coverage >= 0.9
    assert result.why.startswith("The same recording")


def test_different_melodies_are_different(fpcalc: Path, audio: AudioFixtures) -> None:
    result = compare(audio.melody_a_m4a, audio.melody_b_m4a)
    assert result.verdict == "different", result.why
    assert result.ber > 0.25


def test_noise_with_silence_in_front_matches_two_seconds_later(
    fpcalc: Path, audio: AudioFixtures
) -> None:
    result = compare(audio.noise_silence_m4a, audio.noise_m4a)
    assert result.verdict == "match", result.why
    assert result.offset_s == pytest.approx(2.0, abs=0.3)
    assert result.a_extra_s[0] == pytest.approx(2.0, abs=2.1)  # the silence, in 2 s windows

    # The other way round, the sign flips.
    back = compare(audio.noise_m4a, audio.noise_silence_m4a)
    assert back.verdict == "match", back.why
    assert back.offset_s == pytest.approx(-2.0, abs=0.3)


def test_noise_and_melody_are_different(fpcalc: Path, audio: AudioFixtures) -> None:
    result = compare(audio.noise_m4a, audio.melody_a_m4a)
    assert result.verdict == "different", result.why


def test_fpcalc_missing(
    isolated_path: Callable[..., None], tmp_path: Path, audio: AudioFixtures
) -> None:
    isolated_path([tmp_path / "no-tools"])
    with pytest.raises(ToolMissingError) as err:
        fp.fingerprint(audio.melody_a_m4a)
    assert err.value.tool == "fpcalc"
    assert "fpcalc" in err.value.message


# ---- fingerprints and the cache --------------------------------------------------------


def test_whole_file_by_default(fpcalc: Path, audio: AudioFixtures) -> None:
    whole = fp.fingerprint(audio.melody_a_m4a)
    first = fp.fingerprint(audio.melody_a_m4a, length_s=10)
    assert whole.duration_s == pytest.approx(20, abs=0.5)
    # About 8 items a second: the whole 20 s, and only about half of it with -length 10.
    assert len(whole.items) > 120
    assert len(first.items) < len(whole.items) * 0.7


def test_audio_held_in_memory_fingerprints_like_the_file(
    fpcalc: Path, audio: AudioFixtures, ffmpeg_path: Path, tmp_path: Path
) -> None:
    # As YouTube serves a stream: the index of the file at its start, so it can be read
    # from beginning to end without seeking.
    served = tmp_path / "served.m4a"
    subprocess.run(
        [str(ffmpeg_path), "-v", "error", "-i", str(audio.melody_a_m4a), "-c", "copy",
         "-movflags", "+faststart", str(served)],
        check=True,
    )  # fmt: skip
    from_file = fp.fingerprint(served)
    from_memory = fp.fingerprint_bytes(served.read_bytes())
    assert from_memory.items == from_file.items and len(from_memory.items) > 120
    with pytest.raises(AudioError):
        fp.fingerprint_bytes(b"not audio at all")


def test_cache_in_the_index(
    fpcalc: Path,
    audio: AudioFixtures,
    lib: library.Library,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    song = tmp_path / "song.m4a"
    song.write_bytes(audio.melody_a_m4a.read_bytes())
    with open_index(lib.paths, write=True) as index:
        made = fp.fingerprint(song, index=index)

        def no_fpcalc(*args: object, **kwargs: object) -> None:
            raise AssertionError("fpcalc ran although the fingerprint was cached")

        with monkeypatch.context() as m:
            m.setattr(subprocess, "run", no_fpcalc)
            again = fp.fingerprint(song, index=index)
        assert again == made

        # A changed file is fingerprinted again.
        song.write_bytes(audio.melody_b_m4a.read_bytes())
        changed = fp.fingerprint(song, index=index)
        assert changed.items != made.items

        # A first-N-seconds fingerprint is never cached, and never read from the cache.
        part = fp.fingerprint(song, length_s=5, index=index)
        assert len(part.items) < len(changed.items)
        assert fp.fingerprint(song, index=index) == changed


def test_read_only_index_is_not_written(
    fpcalc: Path, audio: AudioFixtures, lib: library.Library
) -> None:
    song = audio.melody_a_m4a
    with open_index(lib.paths, write=False) as reader:
        fp.fingerprint(song, index=reader)
    stat = song.stat()
    with open_index(lib.paths, write=True) as writer:
        assert (
            writer.cached_fingerprint(str(song.resolve()), stat.st_size, stat.st_mtime_ns) is None
        )


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(NotFoundError):
        fp.fingerprint(tmp_path / "nothing.m4a")


def test_not_audio(fpcalc: Path, tmp_path: Path) -> None:
    text = tmp_path / "notes.m4a"
    text.write_text("not audio at all", encoding="utf-8")
    with pytest.raises(AudioError) as err:
        fp.fingerprint(text)
    assert "notes.m4a" in err.value.message


def damaged(source: Path, dest: Path, at: float, length: int = 800) -> Path:
    """A copy of `source` with `length` bytes zeroed at `at` (0–1) of the way in: a missing
    MP3 frame header, like an old rip's (step 09b's calibration run). fpcalc stops there."""
    data = bytearray(source.read_bytes())
    start = int(len(data) * at)
    data[start : start + length] = bytes(length)
    dest.write_bytes(data)
    return dest


def test_a_rip_with_a_damaged_spot_is_still_compared(
    fpcalc: Path, audio: AudioFixtures, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rip = damaged(audio.melody_a_mp3, tmp_path / "old rip.mp3", at=0.95)
    assert subprocess.run([str(fpcalc), "-raw", "-json", str(rip)],
                          capture_output=True).returncode != 0  # fmt: skip
    assert compare(rip, audio.melody_a_m4a).verdict == "match"
    assert compare(rip, audio.melody_b_m4a).verdict == "different"
    assert "damaged spot" in caplog.text


def test_a_rip_damaged_early_on_is_refused(
    fpcalc: Path, audio: AudioFixtures, tmp_path: Path
) -> None:
    rip = damaged(audio.melody_a_mp3, tmp_path / "old rip.mp3", at=0.3, length=4000)
    with pytest.raises(AudioError, match="Couldn't fingerprint"):
        fp.fingerprint(rip)


def test_pack_round_trip() -> None:
    items = (0, 1, 2**31, 2**32 - 1, 123456789)
    assert fp.unpack(fp.pack(items)) == items


def test_parse_fpcalc_json() -> None:
    parsed = fp.parse_fpcalc_json('{"duration": 12.5, "fingerprint": [1, -1, 3]}')
    assert parsed.duration_s == 12.5
    assert parsed.items == (1, 2**32 - 1, 3)  # negative values from some builds wrap
    with pytest.raises(AudioError):
        fp.parse_fpcalc_json("not json")


# ---- comparing: thresholds and edge cases, on fingerprints directly --------------------


def test_too_short_to_compare() -> None:
    result = fp.compare_items([1] * 5, [1] * 100, LIMITS)
    assert result.verdict == "different"
    assert "too little audio" in result.why


def test_thresholds_come_from_config(tmp_path: Path) -> None:
    cfg = Config.load(tmp_path / "config.json")
    assert cfg.fingerprint() == FINGERPRINT_DEFAULTS
    cfg.data["fingerprint"] = {
        "match_ber": 0.1,
        "max_end_extra_s": 20,
        "bogus": 1,
        "min_overlap": "x",
    }
    values = cfg.fingerprint()
    assert values["match_ber"] == 0.1
    assert values["max_end_extra_s"] == 20.0
    assert values["min_overlap"] == FINGERPRINT_DEFAULTS["min_overlap"]
    assert "bogus" not in values


def test_stricter_threshold_turns_a_match_uncertain(fpcalc: Path, audio: AudioFixtures) -> None:
    a, b = fp.fingerprint(audio.noise_silence_m4a), fp.fingerprint(audio.noise_m4a)
    loose = fp.compare(a, b, thresholds=LIMITS)
    assert loose.verdict == "match"
    strict = fp.compare(a, b, thresholds=dict(LIMITS, max_end_extra_s=1.0))
    assert strict.verdict == "uncertain"
    assert "extra audio at the start" in strict.why


# ---- the research's shapes, built from the generated pieces ----------------------------


@dataclass(frozen=True)
class Shapes:
    base: Path  # 20 s of pink noise
    long_intro: Path  # 30 s of melody B, then the base (a music video's long intro)
    short_intro: Path  # 8 s of melody B, then the base
    extended: Path  # the base, then 25 s of melody B (an extended ending)
    inserted: Path  # the base's first 10 s, 14 s of melody B, the base's last 10 s
    clip: Path  # the base's first 12 s


@pytest.fixture(scope="module")
def shapes(tmp_path_factory: pytest.TempPathFactory) -> Shapes:
    ffmpeg = require_tool("ffmpeg")
    folder = tmp_path_factory.mktemp("shapes")
    base = folder / "base.wav"
    _ffmpeg(
        ffmpeg, "-f", "lavfi", "-i",
        f"anoisesrc=color=pink:seed=7:sample_rate={SAMPLE_RATE}:amplitude=0.5:duration=20",
        "-c:a", "pcm_s16le", str(base),
    )  # fmt: skip

    def melody(seconds: int) -> Path:
        out = folder / f"melody_{seconds}.wav"
        _ffmpeg(
            ffmpeg, "-f", "lavfi", "-i", melody_filter(MELODY_B_PITCH_CLASSES, seconds=seconds),
            "-ac", "1", "-c:a", "pcm_s16le", str(out),
        )  # fmt: skip
        return out

    def join(name: str, *parts: tuple[Path, float, float]) -> Path:
        """Concatenate (file, start, end) pieces into an AAC file."""
        args: list[str] = []
        labels = ""
        for n, (path, start, end) in enumerate(parts):
            args += ["-ss", str(start), "-to", str(end), "-i", str(path)]
            labels += f"[{n}:a]aformat=sample_rates={SAMPLE_RATE}:channel_layouts=mono[p{n}];"
        chain = "".join(f"[p{n}]" for n in range(len(parts)))
        out = folder / f"{name}.m4a"
        _ffmpeg(
            ffmpeg, *args, "-filter_complex",
            f"{labels}{chain}concat=n={len(parts)}:v=0:a=1[out]", "-map", "[out]", *AAC, str(out),
        )  # fmt: skip
        return out

    m30, m8, m25, m14 = melody(30), melody(8), melody(25), melody(14)
    return Shapes(
        base=join("base", (base, 0, 20)),
        long_intro=join("long_intro", (m30, 0, 30), (base, 0, 20)),
        short_intro=join("short_intro", (m8, 0, 8), (base, 0, 20)),
        extended=join("extended", (base, 0, 20), (m25, 0, 25)),
        inserted=join("inserted", (base, 0, 10), (m14, 0, 14), (base, 10, 20)),
        clip=join("clip", (base, 0, 12)),
    )


def test_short_intro_matches(fpcalc: Path, shapes: Shapes) -> None:
    result = compare(shapes.short_intro, shapes.base)
    assert result.verdict == "match", result.why
    assert result.offset_s == pytest.approx(8.0, abs=0.5)
    assert result.b_coverage >= 0.9


def test_long_intro_is_found_but_uncertain(fpcalc: Path, shapes: Shapes) -> None:
    """30 s is beyond the ±15 s slide: offset voting still lines the files up, and the
    30 s of extra audio makes it uncertain rather than a match or "different"."""
    result = compare(shapes.long_intro, shapes.base)
    assert result.offset_s == pytest.approx(30.0, abs=0.5)
    assert result.ber <= 0.15
    assert result.b_coverage >= 0.9
    assert result.verdict == "uncertain", result.why
    assert "extra audio at the start" in result.why


def test_extended_ending_is_uncertain(fpcalc: Path, shapes: Shapes) -> None:
    """The prompt's single BER over the overlap would call this a match: the whole of
    the shorter file lines up. The 25 s extra at the end says it's another version."""
    result = compare(shapes.extended, shapes.base)
    assert result.ber <= 0.15 and result.overlap_ratio >= 0.9
    assert result.verdict == "uncertain", result.why
    assert "extra audio at the end" in result.why
    assert result.a_extra_s[1] >= 20


def test_inserted_middle_is_not_a_match(fpcalc: Path, shapes: Shapes) -> None:
    result = compare(shapes.inserted, shapes.base)
    assert result.verdict == "uncertain", result.why
    assert result.b_coverage >= 0.8  # all of the base is in there, in two pieces
    assert result.middle_gap_s >= 10


def test_clip_of_the_start(fpcalc: Path, shapes: Shapes) -> None:
    """12 s of a 20 s file: 8 s missing at the end is within the 15 s allowance."""
    result = compare(shapes.clip, shapes.base)
    assert result.verdict == "match", result.why
    assert result.b_extra_s[1] == pytest.approx(8.0, abs=2.1)
    stricter = fp.compare(shapes.clip, shapes.base, thresholds=dict(LIMITS, max_end_extra_s=5))
    assert stricter.verdict == "uncertain"


# ---- scripts/calibrate_fp.py -----------------------------------------------------------

CALIBRATE = Path(__file__).resolve().parents[2] / "scripts" / "calibrate_fp.py"


def run_calibrate(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CALIBRATE), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )


def test_calibrate_on_a_small_synthetic_pairs_file(
    fpcalc: Path, audio: AudioFixtures, tmp_path: Path
) -> None:
    # One side as a saved fingerprint, the way research data can be reused.
    saved = tmp_path / "melody_a.fp.json"
    made = fp.fingerprint(audio.melody_a_mp3)
    saved.write_text(
        json.dumps({"duration": made.duration_s, "fingerprint": list(made.items)}),
        encoding="utf-8",
    )
    pairs = tmp_path / "pairs.csv"
    pairs.write_text(
        "a_path,b_path,same\n"
        f"{audio.melody_a_m4a},{saved.name},yes\n"
        f"{audio.noise_silence_m4a},{audio.noise_m4a},YES\n"
        f"{audio.melody_a_m4a},{audio.melody_b_m4a},no\n"
        f"{audio.noise_m4a},{audio.melody_a_m4a},no\n",
        encoding="utf-8",
    )
    result = run_calibrate(str(pairs))
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "4 pairs: 2 the same recording, 2 different." in out
    assert "Same recording (2 pairs)" in out and "Different (2 pairs)" in out
    assert "verdicts  match 2  uncertain 0  different 0" in out
    assert "verdicts  match 0  uncertain 0  different 2" in out
    assert "DANGER" not in out
    assert "Suggestion: match up to BER" in out


def test_calibrate_refuses_bad_rows(tmp_path: Path) -> None:
    pairs = tmp_path / "pairs.csv"
    pairs.write_text("a_path,b_path,same\nx.m4a,y.m4a,maybe\n", encoding="utf-8")
    result = run_calibrate(str(pairs))
    assert result.returncode == 1
    assert "row 2" in result.stderr

    pairs.write_text("a,b\n1,2\n", encoding="utf-8")
    result = run_calibrate(str(pairs))
    assert result.returncode == 1
    assert "a_path, b_path, same" in result.stderr

    usage = run_calibrate()
    assert usage.returncode == 1
    assert "Usage" in usage.stderr
