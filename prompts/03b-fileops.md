# Step 03b: fileops (guarded operations, journal, recovery, undo, plans)

## Context
This is the most important module in the project. Every later step writes files **only** through it. Read `docs/LIBRARY_CONTRACT.md` section 6 again before starting. Every guarantee there is implemented here.

## Goal
Build `musicorg.fileops`: guarded operations, the reserve-then-replace move, journal, crash recovery, undo, plans with preconditions, staging cleanup and exports. Implement the CLI commands `journal list` and `undo`.

## Build
1. **`guard(path, allow=MANAGED)`:** resolve the path (following symlinks) and assert it's inside one of `Music/`, `_Replaced/`, `_Staging/` or `.musicorg/` of this library, or raise `OutsideLibraryError`. It's called on **every path an operation modifies: sources and destinations**.
2. **`_move_no_overwrite(src, target)`:** the only way anything moves (contract 6.4):
   1. For each name from `naming.candidate_names(target)`, try `open(name, 'xb')` to reserve it.
   2. Journal the reserved placeholder.
   3. `os.replace(src, placeholder)`.
   
   Never use `shutil.move`. EXDEV (different volume) → `CrossVolumeError` with a clear message. On Windows, `PermissionError` retries 5 × ~0.4 s, then raises `FileInUseError` ("That file is open in another app; close it and try again"), leaving everything untouched.
3. **Operations.** Each takes a `batch`, journals an intent with the full before-state, acts, then journals done:
   - `stage_path(batch, name) -> Path` in `_Staging/<batch_id>/`.
   - `commit(batch, staged, rel_target) -> Path`: from staging into `Music/`.
   - `copy_in(batch, external_src, rel_target) -> Path`:
     1. Read the source read-only into staging.
     2. Verify SHA-256 equality between copy and source.
     3. Commit.
     4. Assert the source's size and mtime are unchanged.
   - `supersede(batch, lib_file) -> Path`: into `_Replaced/<same rel path>`.
   - `move(batch, lib_file, rel_target) -> Path`: within `Music/`.
   - `trash(batch, lib_file)`: `send2trash`, library files only.
   - `write_tags(batch, lib_file, tags)`: journal and undo plumbing only. Step 04 implements the verified write. The before-state is the **complete** tag dict plus the cover hash, so undo can restore *exactly*: fields absent before are removed.
   - `write_sidecar(batch, lib_file, suffix, data: bytes)`:
     - If an identical sidecar exists, skip.
     - If a *different* `.lrc` exists, supersede it first.
     - If a different `cover.jpg` exists, leave it alone and log it. The owner may have chosen that art.
   - `discard_staged(path)` and `clean_staging(older_than_hours=24)`:
     - The only outright deletes: regular files and empty folders inside the resolved `_Staging/`.
     - Symlinks are unlinked, never followed.
     - `_Staging/calibration/` is skipped by `clean_staging`.
     - Both refuse if `_Staging/` itself resolves outside the root.
   - `write_export(path, data: bytes) -> Path`:
     - For reports and CSVs anywhere the user chose.
     - Never overwrites (` (2)` suffix).
     - Refuses paths inside `Music/`, `_Replaced/`, `_Staging/` or any registered source.
     - Not journaled.
4. **Journal:** JSONL in `.musicorg/journal/YYYY-MM-DD.jsonl`.
   - `intent` lines are flushed and fsynced **before** acting (`fcntl.F_FULLFSYNC` on macOS; plain fsync on Windows; don't fsync folders on Windows).
   - `batch_start` and `batch_end` lines carry summaries.
   - A batch is either an in-process context manager, or an **open batch** created by `apply` (step 09b) that stays open until its last job ends. Recovery never treats an open batch as crashed.
5. **Recovery** (the hook from step 03a `open`): for each `intent` without `done`, inspect the filesystem, then complete or roll back the operation, including removing empty reserved placeholders. Log each decision.
6. **Undo:** `undo(batch_id, dry_run=False)`.
   - Refuses while any job of the batch is running, and first cancels its queued jobs. The queue arrives in step 09a, so for now take a callable hook.
   - Reverses done operations in reverse order:
     - commit, copy_in → supersede the created file into `_Replaced/`
     - supersede → move back, collision-safe
     - move → move back
     - write_tags → restore the exact before-state
     - write_sidecar → supersede the new sidecar and restore the old one
     - trash → reported as "restore from the Trash by hand"
   - The undo is itself a journaled batch.
7. **Plans:** a `Plan` dataclass (`plan_id`, `kind`, `created_at`, `operations[]`). Each operation carries **preconditions**: source size, mtime and `sha1_head`, item state, target folder. Saved to `.musicorg/plans/`.
   - `validate(plan)` re-checks all of them.
   - `check_op(op)` is the same check for one operation, called by jobs right before acting (step 09b).
   - Ignore `.DS_Store`, `._*`, `Thumbs.db` and `desktop.ini`.
8. **CLI:**
   - `journal list` (no lock): recent batches with op counts and open/closed status.
   - `undo <batch_id> [--dry-run]`.
   - **`scripts/fileops_demo.py <root> <files…>`:** opens a library, `copy_in`s the given files in one batch and prints the `batch_id`, for manual checks.

## Tests
Per operation:
- success
- crash between intent and act
- crash between act and done (simulate by raising inside a monkeypatched step)
- collision
- undo
- **guard refusal on an external source path**: `trash`, `move`, `supersede` and `write_tags` on a file outside the library each raise `OutsideLibraryError`, and the file stays byte-identical

Plus:
- A symlink inside `Music/` pointing outside is refused as both source and destination.
- A target created between the name check and the move (simulate) never gets overwritten.
- `clean_staging` with a symlink to an external file: the external file survives. A `_Staging` that is itself a symlink outside: refusal.
- `copy_in` source unchanged: SHA-256 before and after.
- Plan validation fails after a precondition changes, and ignores `.DS_Store`.
- `write_export` never overwrites and refuses managed or source paths.
- `send2trash` is monkeypatched; one `@pytest.mark.integration` test uses the real Trash and is skipped in CI.

## Acceptance
- CI green on all three runners.
- **Manual check** (write the steps and results in `docs/CHANGELOG.md`):
  1. `musicorg init` a scratch library.
  2. `python scripts/fileops_demo.py <root> <3 fixture files>` prints a batch id.
  3. `musicorg journal list` shows it.
  4. `musicorg undo <id>`.
  5. `Music/` is empty, `_Replaced/` holds the three files, and the originals are untouched.
