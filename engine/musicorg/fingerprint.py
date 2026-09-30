"""The fingerprint gate (step 08): is this the same recording?

Name and length can agree while the audio differs: a tribute upload, a live take of the
same length, a different mix. Contract rule 7: no file is replaced without a fingerprint
match. Chromaprint's `fpcalc` makes the fingerprints locally, with no account or key.

How two files are compared:

1. **Fingerprint the whole file** (`fpcalc -raw -length 0`). A radio edit and the album
   version share their first two minutes, so a 120 s fingerprint can't tell them apart.
2. **Line them up.** Every offset within ±15 s is tried (the step 08 prompt), plus any
   offset where many identical fingerprint values agree ("offset voting"). Voting finds
   a music video's long intro (36 s, 69 s in the research) that ±15 s misses.
3. **The bit error rate (BER)** at the best offset, averaged over the overlap, with the
   overlap as a share of the shorter file: the prompt's `ber`, `offset_s`, `overlap_ratio`.
4. **Coverage, in 2 s windows.** Each window of each file counts as found when some
   lined-up offset gives it a BER under 0.25. That shows how much of each file the other
   explains, how much extra audio sits at each end, and any unmatched stretch in the
   middle (a cut, an inserted skit, an extended section). A single average hides those.

Verdicts (docs/ENGINE_API.md, Enums):

- `match`: BER ≤ 0.15 and overlap ≥ 0.6, and the shape fits: at most 15 s of extra audio
  at either end of either file, and no unmatched stretch of 10 s or more in the middle.
  (Research on the owner's rips: a rip's extra audio was at most 7 s at the start and 11 s
  at the end in 90% of true matches; no true match had a 10 s gap in the middle.)
- `uncertain`: BER ≤ 0.25; or a low BER whose shape doesn't fit (an extended mix, an
  edit, a clip); or a high BER with at least half of either file found (a cut or remix).
  Goes to review with reason `fingerprint_uncertain`.
- `different`: everything else.

The thresholds are in config.json under "fingerprint" (config.FINGERPRINT_DEFAULTS) and
stay at these conservative values until step 09b calibrates them.

**Known limit:** the clean and explicit edits of the same master fingerprint as a `match`,
because only a few words differ. The gate can't tell them apart; step 06's clean/explicit
rule decides that case. The gate is never the only check.
"""

from __future__ import annotations

import functools
import json
import logging
import struct
import subprocess
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath

from musicorg import tools
from musicorg.config import Config
from musicorg.errors import AudioError, NotFoundError
from musicorg.index import Index

log = logging.getLogger(__name__)

VERDICTS = ("match", "uncertain", "different")

ITEM_S = 0.1238  # seconds per raw Chromaprint item
WINDOW = 16  # items per coverage window, about 2 s
WINDOW_BER = 0.25  # a window below this counts as found
MAX_OFFSET_ITEMS = 120  # the ±15 s slide from the step 08 prompt
MIN_VOTES = 12  # identical values needed for a voted offset to count
MAX_VOTED_OFFSETS = 6
COMMON_VALUE = 20  # a value this frequent (silence, a drone) doesn't vote
FPCALC_TIMEOUT_S = 600
MOSTLY_READ = 0.9  # a file with a damaged spot, fingerprinted this far, still counts
FPCALC_TAIL_S = 3.0  # a whole-file fingerprint always stops ~2.7 s before the end


@dataclass(frozen=True)
class RawFP:
    """A raw Chromaprint fingerprint: 32-bit unsigned items, about 0.124 s each."""

    duration_s: float
    items: tuple[int, ...]


@dataclass(frozen=True)
class FingerprintResult:
    """How the audio of `a` compares with `b`.

    - `offset_s`: how much later the shared audio starts in `a` than in `b` (positive:
      `a` has extra audio in front).
    - `overlap_ratio`: the lined-up stretch as a share of the shorter file.
    - `a_coverage` / `b_coverage`: the share of each file found in the other.
    - `a_extra_s` / `b_extra_s`: unmatched audio at the (start, end) of each file.
    - `middle_gap_s`: the longest unmatched stretch inside the matched part of either.
    - `why`: plain English, for the review page and the log.
    """

    verdict: str
    ber: float
    offset_s: float
    overlap_ratio: float
    a_coverage: float
    b_coverage: float
    a_extra_s: tuple[float, float]
    b_extra_s: tuple[float, float]
    middle_gap_s: float
    why: str


# ---- fingerprints ----------------------------------------------------------------------


def fingerprint(path: PurePath | str, length_s: int = 0, *, index: Index | None = None) -> RawFP:
    """The raw fingerprint of `path`: the whole file by default (`length_s=0`), or its
    first `length_s` seconds. Whole-file fingerprints are cached in the index's
    `fingerprints` table by (path, size, mtime) when an index is given."""
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        stat = None
    if stat is None or not path.is_file():
        raise NotFoundError(f"There's no file at {path}.")
    key = str(path.resolve())
    use_cache = index is not None and length_s == 0
    if use_cache:
        assert index is not None
        cached = index.cached_fingerprint(key, stat.st_size, stat.st_mtime_ns)
        if cached is not None:
            duration, blob = cached
            return RawFP(duration_s=duration or 0.0, items=unpack(blob))

    command = [str(_fpcalc()), "-raw", "-json", "-length", str(int(length_s)), str(path)]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=FPCALC_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioError(f"Couldn't fingerprint the audio in {path.name}: {exc}") from exc
    detail = (result.stderr.strip().splitlines()[-1:] or ["no details"])[0]
    if result.returncode != 0:
        fp = _mostly_read(result.stdout, path.name)
        if fp is None:
            raise AudioError(f"Couldn't fingerprint the audio in {path.name}: {detail}")
        log.warning("%s has a damaged spot (%s); fingerprinted %.0f of its %.0f s anyway",
                    path, detail, _seconds(len(fp.items)), fp.duration_s)  # fmt: skip
    else:
        fp = parse_fpcalc_json(result.stdout, path.name)

    if use_cache and index is not None and index.write:
        index.put_fingerprint(key, stat.st_size, stat.st_mtime_ns, fp.duration_s, pack(fp.items))
    return fp


def _mostly_read(text: str, name: str) -> RawFP | None:
    """fpcalc stops with an error at a damaged spot in a file (an old rip with a missing
    MP3 frame header), but it still prints what it read up to there. That fingerprint is
    used when it covers at least `MOSTLY_READ` of the file (less the ~2.7 s any whole-file
    fingerprint stops short of the end), i.e. the damage is near the end. The comparison
    is as strict as ever: the unread seconds count as extra audio at the end, within the
    gate's usual allowance. (Found by step 09b's calibration run: a rip whose last frame is
    cut off, read to the end but reported as an error.)"""
    try:
        fp = parse_fpcalc_json(text, name)
    except AudioError:
        return None
    if fp.duration_s <= 0 or _seconds(len(fp.items)) < MOSTLY_READ * fp.duration_s - FPCALC_TAIL_S:
        return None
    return fp


def parse_fpcalc_json(text: str, name: str = "the file") -> RawFP:
    """Read `fpcalc -raw -json` output (also how a saved fingerprint is stored)."""
    try:
        data = json.loads(text)
        duration = float(data.get("duration") or 0.0)
        items = tuple(int(v) & 0xFFFFFFFF for v in data.get("fingerprint") or [])
    except (ValueError, TypeError, AttributeError) as exc:
        raise AudioError(f"Couldn't fingerprint the audio in {name}: unclear reply.") from exc
    return RawFP(duration_s=duration, items=items)


def pack(items: Sequence[int]) -> bytes:
    """Items as little-endian 32-bit words, for the index."""
    return struct.pack(f"<{len(items)}I", *items)


def unpack(blob: bytes) -> tuple[int, ...]:
    return struct.unpack(f"<{len(blob) // 4}I", blob[: len(blob) // 4 * 4])


@functools.cache
def _fpcalc() -> Path:
    return tools.require("fpcalc")


# ---- comparing -------------------------------------------------------------------------


def compare(
    a: PurePath | str | RawFP,
    b: PurePath | str | RawFP,
    *,
    thresholds: dict[str, float] | None = None,
    index: Index | None = None,
) -> FingerprintResult:
    """Compare the audio of `a` with `b` (paths, or fingerprints already made). In the
    pipeline `a` is the rip and `b` the download. `thresholds` defaults to config.json's."""
    fa = a if isinstance(a, RawFP) else fingerprint(a, index=index)
    fb = b if isinstance(b, RawFP) else fingerprint(b, index=index)
    limits = thresholds if thresholds is not None else Config.load().fingerprint()
    return compare_items(fa.items, fb.items, limits)


def compare_items(
    a: Sequence[int], b: Sequence[int], limits: dict[str, float]
) -> FingerprintResult:
    """The comparison itself, on raw fingerprint items (see the module docstring)."""
    if len(a) < WINDOW or len(b) < WINDOW:
        return FingerprintResult(
            verdict="different", ber=1.0, offset_s=0.0, overlap_ratio=0.0,
            a_coverage=0.0, b_coverage=0.0,
            a_extra_s=(_seconds(len(a)), 0.0), b_extra_s=(_seconds(len(b)), 0.0),
            middle_gap_s=0.0,
            why="One of the files has too little audio to compare (under 2 seconds).",
        )  # fmt: skip

    voted = _voted_offsets(a, b)
    shorter = min(len(a), len(b))
    best_ber, best_off, best_n = 1.0, 0, 0
    for off in sorted(set(range(-MAX_OFFSET_ITEMS, MAX_OFFSET_ITEMS + 1)) | set(voted)):
        lo, hi = max(0, off), min(len(a), len(b) + off)
        n = hi - lo
        if n * 2 < shorter:
            continue
        ber = sum((a[i] ^ b[i - off]).bit_count() for i in range(lo, hi)) / (32 * n)
        if ber < best_ber:
            best_ber, best_off, best_n = ber, off, n
    overlap = best_n / shorter

    offsets = [best_off, *(o for o in voted if o != best_off)]
    found_a = [_window_found(a, b, s, offsets) for s in range(0, len(a) - WINDOW + 1, WINDOW)]
    found_b = [
        _window_found(b, a, s, [-o for o in offsets]) for s in range(0, len(b) - WINDOW + 1, WINDOW)
    ]
    a_cov, a_extra, a_gap = _shape(found_a, len(a))
    b_cov, b_extra, b_gap = _shape(found_b, len(b))
    gap = max(a_gap, b_gap)

    common = dict(
        ber=round(best_ber, 4), offset_s=round(best_off * ITEM_S, 2),
        overlap_ratio=round(overlap, 3), a_coverage=round(a_cov, 3), b_coverage=round(b_cov, 3),
        a_extra_s=a_extra, b_extra_s=b_extra, middle_gap_s=gap,
    )  # fmt: skip
    lined_up = f"bit error rate {best_ber:.3f}, lined up at {best_off * ITEM_S:+.1f} s"

    if best_ber <= limits["match_ber"] and overlap >= limits["min_overlap"]:
        problems = _shape_problems(a_extra, b_extra, gap, limits)
        if not problems:
            return FingerprintResult(
                verdict="match", why=f"The same recording ({lined_up}).", **common
            )
        return FingerprintResult(
            verdict="uncertain",
            why=f"The audio matches ({lined_up}), but {'; and '.join(problems)}.",
            **common,
        )
    if best_ber <= limits["uncertain_ber"]:
        return FingerprintResult(
            verdict="uncertain", why=f"The audio is close but not clearly the same ({lined_up}).",
            **common,
        )  # fmt: skip
    if max(a_cov, b_cov) >= 0.5:
        return FingerprintResult(
            verdict="uncertain",
            why=(
                f"Parts of the audio are the same ({a_cov:.0%} of the first file, "
                f"{b_cov:.0%} of the second): maybe an edit, a remix or another cut."
            ),
            **common,
        )
    return FingerprintResult(verdict="different", why=f"Different audio ({lined_up}).", **common)


def _voted_offsets(a: Sequence[int], b: Sequence[int]) -> list[int]:
    """Offsets where many identical items agree (a[i] == b[i - offset]), best first.
    Neighbours of an offset already chosen (±2 items) are skipped."""
    where: dict[int, list[int]] = {}
    for j, value in enumerate(b):
        where.setdefault(value, []).append(j)
    votes: Counter[int] = Counter()
    for i, value in enumerate(a):
        js = where.get(value)
        if js and len(js) <= COMMON_VALUE:
            for j in js:
                votes[i - j] += 1
    chosen: list[int] = []
    for off, n in votes.most_common(50):
        if n < MIN_VOTES or len(chosen) >= MAX_VOTED_OFFSETS:
            break
        if all(abs(off - c) > 2 for c in chosen):
            chosen.append(off)
    return chosen


def _window_found(x: Sequence[int], y: Sequence[int], start: int, offsets: list[int]) -> bool:
    """Does the window of `x` at `start` match `y` at any of the offsets
    (x[i] lined up with y[i - offset])?"""
    for off in offsets:
        ys = start - off
        if ys < 0 or ys + WINDOW > len(y):
            continue
        errors = sum((x[start + k] ^ y[ys + k]).bit_count() for k in range(WINDOW))
        if errors / (32 * WINDOW) < WINDOW_BER:
            return True
    return False


def _shape(found: list[bool], length: int) -> tuple[float, tuple[float, float], float]:
    """Coverage, unmatched seconds at (start, end), and the longest unmatched stretch
    between the first and last matched windows, in seconds."""
    if not any(found):
        return 0.0, (_seconds(length), 0.0), 0.0
    first = found.index(True)
    last = len(found) - 1 - found[::-1].index(True)
    extra = (_seconds(first * WINDOW), _seconds(length - (last + 1) * WINDOW))
    longest = run = 0
    for hit in found[first : last + 1]:
        run = 0 if hit else run + 1
        longest = max(longest, run)
    return sum(found) / len(found), extra, _seconds(longest * WINDOW)


def _shape_problems(
    a_extra: tuple[float, float],
    b_extra: tuple[float, float],
    gap: float,
    limits: dict[str, float],
) -> list[str]:
    problems = []
    for name, (start, end) in (("first", a_extra), ("second", b_extra)):
        if start > limits["max_end_extra_s"]:
            problems.append(f"the {name} file has {start:.0f} s of extra audio at the start")
        if end > limits["max_end_extra_s"]:
            problems.append(f"the {name} file has {end:.0f} s of extra audio at the end")
    if gap >= limits["max_middle_gap_s"]:
        problems.append(
            f"a {gap:.0f} s stretch in the middle doesn't match (a cut, an added part or "
            "another edit)"
        )
    return problems


def _seconds(items: int) -> float:
    return round(items * ITEM_S, 1)
