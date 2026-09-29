"""`musicorg status`: what the engine knows about the library.

The library in use, its environment warnings (step 03a), sources and item counts by
state with how many parses have low confidence (step 05). The queue state arrives in
step 09a. Needs no lock: the index is opened read-only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from musicorg import __version__, library, normalize, state
from musicorg.errors import MusicOrgError
from musicorg.index import Index
from musicorg.naming import LibraryPaths


def get_status(root: Path | None) -> dict[str, Any]:
    exists = root is not None and root.is_dir()
    is_library = root is not None and exists and library.is_library(root)
    result: dict[str, Any] = {
        "engine_version": __version__,
        "library": str(root) if root is not None else None,
        "library_exists": exists,
        "is_library": is_library,
        "sources": 0,
        "items": 0,
        "items_by_state": {},
        "low_confidence": 0,
        "tracks": 0,
        "queue": None,
        "warnings": library.environment_warnings(root) if root is not None and is_library else [],
    }
    if root is None or not is_library:
        return result
    paths = LibraryPaths(library.root_path(root))
    try:
        result["sources"] = len(state.sources(state.State.load(paths.state_file).data))
        with Index(paths, write=False) as index:
            counts = index.counts_by_state()
            result["items_by_state"] = counts
            result["items"] = sum(counts.values())
            result["low_confidence"] = index.low_confidence_count(normalize.LOW_CONFIDENCE)
            result["tracks"] = index.library_track_count()
    except MusicOrgError as exc:
        result["warnings"].append(exc.message)
    return result
