# Step 03a: Library core (naming, init/open, lock, state)

## Context
Steps 03a and 03b build the safety core. A bug here can destroy someone's music without warning. Read `docs/LIBRARY_CONTRACT.md` sections 1, 2, 5 and 6 fully before writing code.

## Goal
Build `musicorg.naming`, `musicorg.library` (init/open), the lock, `musicorg.state`, the CLI `init` command, and the test that enforces "no writes outside fileops".

## Build
1. **`naming.py`:**
   - `safe_component(s, max_chars=120, max_bytes=200)` and `library_path(meta, root) -> Path` (relative to `Music/`), implementing contract section 2 exactly:
     - sanitising, reserved names, NFC
     - character *and* UTF-8 byte limits, truncating on character boundaries
     - fallbacks (Unknown Artist, Unsorted, title from file name)
     - the Windows full-path budget computed from the real root, measured under `_Replaced/`
   - `candidate_names(target)` yields `target`, `target (2)`, `target (3)` and so on. The actual reservation happens in step 03b.
2. **`library.py`:**
   - **`init(root)`:**
     - Refuses if the folder already holds audio files and isn't an existing library. This stops someone initialising on top of their rips folder.
     - Refuses if `_Staging/` would be on a different volume than `Music/`.
     - Creates the layout from contract section 1.
     - No sources exist yet at init. The source/root containment check happens in `sources add` (step 05).
   - **`open(root, write: bool)`:** read-only opens take no lock. Write opens take the lock, then run journal recovery (a hook that step 03b fills in). Returns a `Library` object.
   - **Environment warnings** (contract 6.10):
     - iCloud: the root is under `~/Library/Mobile Documents/`, or under `~/Desktop` / `~/Documents` while iCloud Desktop & Documents is on. Best effort.
     - Time Machine via `tmutil destinationinfo`.
     - Free space.
     - Long root on Windows.
3. **Lock** (contract 6.1):
   - One helper: `fcntl.flock(LOCK_EX|LOCK_NB)` on macOS; `msvcrt.locking(fd, LK_NBLCK, 1)` on byte 0 on Windows.
   - Write the PID, start time and command to `.musicorg/lock.info`.
   - A second writer gets `LibraryLockedError`, whose message reads `lock.info` ("Another Music Organizer process (PID 4412, `queue run`, started 14:02) is using this library").
   - Stale `lock.info` with no held lock is ignored.
4. **`state.py`:** load and save `.musicorg/state.json` atomically (write temp, fsync, `os.replace`), with a schema version and unknown keys preserved. **Stable source ids:** `s_` + the first 12 hex characters of SHA-1(normalised absolute path).
5. **CLI:** `musicorg init <root>` prints warnings and the created layout.
6. **Write-rule test** (`tests/test_write_rules.py`): parse every module in `musicorg/` with Python's `ast` module (not grep). Flag:
   - `open`/`Path.open`/`io.open` with a mode containing `w`, `a`, `x` or `+`
   - `os.rename`, `os.replace`, `os.remove`, `os.unlink`, `os.rmdir`, `os.makedirs`, `os.mkdir`
   - `shutil.move`, `shutil.copy`, `shutil.copy2`, `shutil.copyfile`, `shutil.copytree`, `shutil.rmtree`
   - `Path.write_text`, `write_bytes`, `touch`, `unlink`, `rename`, `replace`, `rmdir`, `mkdir`
   - `send2trash`
   - `.save(` and `.delete(`
   
   Allow-list, per `CLAUDE.md` rule 3: `fileops.py` (everything), `state.py`, `index.py` and `config.py` (their own files), `tags.py` (only the mutagen `.save(`). Anything else fails, unless the line carries `# fileops-ok: in-memory`.

## Tests
- Naming table test, at least 40 cases:
  - reserved names, illegal characters, trailing dots
  - emoji; a 120-character Japanese title that must fit in 200 bytes
  - NFC vs NFD input producing the same result
  - compilations; missing album, year and artist
  - the Windows budget with a long root
- Lock contention: a second process gets `LibraryLockedError` with holder details. Read-only open works while locked.
- `init` refuses a folder already holding audio (e.g. a rips folder).
- `state.json` survives a simulated crash mid-write: the old file stays intact.
- The write-rule test passes on the current code and **fails** on a planted `Path.write_text` in a scratch module (test the test).

## Acceptance
- CI green on all three runners.
- `musicorg init ~/Music\ Organizer\ Library` (or your chosen root) creates the layout and prints any warnings.
