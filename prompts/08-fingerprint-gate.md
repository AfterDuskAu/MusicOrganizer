# Step 08: Fingerprint gate

## Context
Name and duration can agree while the audio is different: a "tribute" upload, a live take of the same length, a different mix. Contract rule: **no file is superseded without an audio fingerprint match.** This runs locally with Chromaprint's `fpcalc`, with no account or API key.

## Goal
Build `musicorg.fingerprint` with `compare(a: Path, b: Path) -> FingerprintResult`, plus a calibration script. Real calibration happens in step 09b, once downloads exist.

## Build
1. **`fingerprint(path, length_s=120) -> RawFP`:** run `fpcalc -raw -json -length <n> <path>` and parse `duration` and `fingerprint` (a list of 32-bit unsigned ints). Cache in the `fingerprints` table by (path, size, mtime).
2. **`compare(a, b)`:**
   - Slide one raw fingerprint against the other over ±`max_offset` items. One raw item is ~0.124 s, so ±120 items covers roughly ±15 s of intro difference.
   - For each offset with ≥ 50% overlap, compute the bit error rate: popcount(x XOR y) / 32, averaged over the overlap. `int.bit_count()` in plain Python is fast enough for ~1,000 items; no numpy.
   - Result: `ber` (best), `offset_s`, `overlap_ratio`, and `verdict` ∈ `match` / `uncertain` / `different`.
3. **Thresholds** (config, conservative defaults until calibrated):
   - `match`: `ber ≤ 0.15` and `overlap ≥ 0.6`
   - `uncertain`: `0.15 < ber ≤ 0.25`, which goes to review with reason `fingerprint_uncertain`
   - `different`: everything else
4. **`scripts/calibrate_fp.py <pairs.csv>`:** columns `a_path,b_path,same` (`yes`/`no`). Prints the BER distribution for each group and suggests thresholds with a safety margin. The run itself happens in step 09b.
5. **Known limit, stated in the code docstring:** clean and explicit edits of the same master fingerprint as a `match`, because only a few words differ. The gate can't tell them apart. **Step 06's clean/explicit rule decides that case.** The gate is never the only check.

## Tests
Using the step 02 fixtures:
- Melody A (M4A) vs melody A (MP3, a different encode): `match`.
- Melody A vs melody B: `different`.
- Noise vs the same noise with 2 s of silence prepended: `match`, `offset_s` ≈ 2.
- Noise vs melody A: `different`.
- `fpcalc` missing → `ToolMissingError` (and a CI failure, because `MUSICORG_REQUIRE_TOOLS=1`).

These tests run on **all three** CI runners, including Windows with the official fpcalc zip.

## Acceptance
- Tests pass on all three runners.
- `calibrate_fp.py` runs on a small synthetic pairs file and prints sensible output.
- Thresholds remain at the conservative defaults. Real calibration is part of step 09b's first batch.
