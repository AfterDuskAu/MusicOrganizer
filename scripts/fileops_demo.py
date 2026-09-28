"""Manual check for step 03b: copy files into a library in one batch.

Usage (from the repo root, with the engine's virtual environment):

    .venv/bin/python scripts/fileops_demo.py <library root> <file> [<file> ...]

Each file is copied (never moved) into `Music/Unknown Artist/Unsorted/`, then the
batch id is printed. `musicorg journal list` shows the batch, and `musicorg undo <id>`
reverses it.
"""

from __future__ import annotations

import sys
from pathlib import Path

from musicorg import fileops, library, naming
from musicorg.errors import MusicOrgError


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 1
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")  # type: ignore[attr-defined]  # Windows consoles
    root, files = Path(argv[0]).expanduser(), [Path(a).expanduser() for a in argv[1:]]
    try:
        with library.open(root, write=True, command="fileops_demo") as lib:
            with fileops.batch(lib, "demo") as b:
                for file in files:
                    meta = naming.TrackMeta(source_file=file.name, ext=file.suffix or "bin")
                    rel = naming.library_path(meta, lib.root)
                    landed = fileops.copy_in(b, file, rel)
                    print(f"Copied {file} to {landed.relative_to(lib.root)}", file=sys.stderr)
    except MusicOrgError as exc:
        print(exc.message, file=sys.stderr)
        return exc.exit_code
    print(b.batch_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
