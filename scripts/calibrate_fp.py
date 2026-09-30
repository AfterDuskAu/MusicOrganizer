"""Calibrate the fingerprint gate (step 08; the real run is part of step 09b).

Usage (from the repo root, with the engine's virtual environment):

    .venv/bin/python scripts/calibrate_fp.py <pairs.csv>

pairs.csv has the columns `a_path,b_path,same`, where `same` is `yes` or `no`: whether
the two files are known to be the same recording. In the pipeline `a` is the rip and `b`
the download. Each path is either
- an audio file, fingerprinted whole with fpcalc, or
- a saved fingerprint: a `.json` file holding `fpcalc -raw -json` output
  (`{"duration": ..., "fingerprint": [...]}`), e.g. exported from the research database,
  so pairs already downloaded once needn't be downloaded again.
Relative paths are relative to the CSV's folder.

It prints, for the `same` and `different` groups: the bit error rate (BER) distribution,
the verdicts under the current thresholds (config.json, or the defaults), the pairs the
gate gets wrong, and suggested BER thresholds with a safety margin. Nothing is written.
"""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

from musicorg import fingerprint as fp
from musicorg.config import Config
from musicorg.errors import MusicOrgError, UserError

COLUMNS = ("a_path", "b_path", "same")
ANSWERS = {"yes": True, "no": False}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 1
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")  # type: ignore[attr-defined]  # Windows consoles
    try:
        pairs = read_pairs(Path(argv[0]).expanduser())
        limits = Config.load().fingerprint()
        results = [(a, b, same, fp.compare(load(a), load(b), thresholds=limits))
                   for a, b, same in pairs]  # fmt: skip
    except MusicOrgError as exc:
        print(exc.message, file=sys.stderr)
        return exc.exit_code
    print(report(results, limits))
    return 0


def read_pairs(csv_path: Path) -> list[tuple[Path, Path, bool]]:
    try:
        text = csv_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise UserError(f"Couldn't read {csv_path}: {exc.strerror or exc}.") from exc
    reader = csv.DictReader(text.splitlines())
    rows = list(reader)
    if not rows or any(c not in (reader.fieldnames or []) for c in COLUMNS):
        raise UserError(
            f"{csv_path.name} needs a header row with the columns {', '.join(COLUMNS)}, "
            "and at least one pair."
        )
    pairs, problems = [], []
    for n, row in enumerate(rows, start=2):
        answer = ANSWERS.get((row.get("same") or "").strip().lower())
        a, b = (row.get("a_path") or "").strip(), (row.get("b_path") or "").strip()
        if answer is None or not a or not b:
            problems.append(f"row {n}: needs a_path, b_path and same (yes or no)")
            continue
        pairs.append((csv_path.parent / a, csv_path.parent / b, answer))
    if problems:
        raise UserError(f"{csv_path.name} has rows that can't be used:\n" + "\n".join(problems))
    return pairs


def load(path: Path) -> fp.RawFP:
    """A saved fingerprint (.json) or an audio file."""
    if path.suffix.lower() == ".json":
        try:
            return fp.parse_fpcalc_json(path.read_text(encoding="utf-8"), path.name)
        except OSError as exc:
            raise UserError(f"Couldn't read {path}: {exc.strerror or exc}.") from exc
    return fp.fingerprint(path)


def report(
    results: list[tuple[Path, Path, bool, fp.FingerprintResult]], limits: dict[str, float]
) -> str:
    same = [r for _, _, s, r in results if s]
    diff = [r for _, _, s, r in results if not s]
    lines = [
        f"{len(results)} pairs: {len(same)} the same recording, {len(diff)} different.",
        "Current thresholds: match up to BER {match_ber}, uncertain up to {uncertain_ber}, "
        "overlap at least {min_overlap}, at most {max_end_extra_s:g} s extra at either end, "
        "no middle gap of {max_middle_gap_s:g} s.".format(**limits),
        "",
    ]
    for name, group in (("Same recording", same), ("Different", diff)):
        lines.append(f"{name} ({len(group)} pairs)")
        if not group:
            lines += ["  none", ""]
            continue
        bers = sorted(r.ber for r in group)
        lines.append(
            f"  BER  min {bers[0]:.3f}  10% {pct(bers, 10):.3f}  median {pct(bers, 50):.3f}"
            f"  90% {pct(bers, 90):.3f}  max {bers[-1]:.3f}"
        )
        counts = {v: sum(r.verdict == v for r in group) for v in fp.VERDICTS}
        lines.append("  verdicts  " + "  ".join(f"{v} {n}" for v, n in counts.items()))
        if group is same:
            starts = sorted(max(r.a_extra_s[0], r.b_extra_s[0]) for r in group)
            ends = sorted(max(r.a_extra_s[1], r.b_extra_s[1]) for r in group)
            gaps = sorted(r.middle_gap_s for r in group)
            lines.append(
                f"  extra audio at the start  90% {pct(starts, 90):.0f} s  max {starts[-1]:.0f} s;"
                f"  at the end  90% {pct(ends, 90):.0f} s  max {ends[-1]:.0f} s;"
                f"  longest middle gap {gaps[-1]:.0f} s"
            )
        lines.append("")

    wrong_accepts = [(a, b, r) for a, b, s, r in results if not s and r.verdict == "match"]
    missed = [(a, b, r) for a, b, s, r in results if s and r.verdict != "match"]
    if wrong_accepts:
        lines.append(f"DANGER: {len(wrong_accepts)} different pairs pass as a match:")
        lines += [f"  {a.name} / {b.name}: {r.why}" for a, b, r in wrong_accepts]
        lines.append("")
    if missed:
        lines.append(f"{len(missed)} same pairs would go to review:")
        lines += [f"  {a.name} / {b.name}: {r.verdict}. {r.why}" for a, b, r in missed]
        lines.append("")

    lines.append(suggest(same, diff, limits))
    return "\n".join(lines)


def suggest(
    same: list[fp.FingerprintResult], diff: list[fp.FingerprintResult], limits: dict[str, float]
) -> str:
    if not same or not diff:
        return "Suggestion: none yet. It needs pairs in both groups."
    worst_same = max(r.ber for r in same)
    best_diff = min(r.ber for r in diff)
    if worst_same >= best_diff:
        return (
            f"Suggestion: none. The groups overlap (a same pair at BER {worst_same:.3f}, a "
            f"different pair at {best_diff:.3f}), so no BER threshold separates them safely. "
            "Keep the current thresholds and look at the pairs above."
        )
    gap = best_diff - worst_same
    match = math.floor((worst_same + gap / 4) * 1000) / 1000
    uncertain = math.floor((worst_same + gap * 3 / 4) * 1000) / 1000
    return (
        f"Suggestion: match up to BER {match:.3f}, uncertain up to {uncertain:.3f} "
        f"(currently {limits['match_ber']} and {limits['uncertain_ber']}). The highest same "
        f"pair is at {worst_same:.3f} and the lowest different pair at {best_diff:.3f}; the "
        "margins keep a quarter of that gap on each side. Change config.json only after "
        "enough real pairs, and never above a different pair's BER."
    )


def pct(values: list[float], p: float) -> float:
    """Nearest-rank percentile of sorted values."""
    rank = max(1, math.ceil(p / 100 * len(values)))
    return values[rank - 1]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
