"""`musicorg status`: what the engine knows about the library.

Step 02 only reports which library is in use. Item counts (step 05), queue state
(step 09a) and environment warnings (step 03a) are added by later steps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from musicorg import __version__

ENGINE_FOLDER = ".musicorg"


def get_status(library: Path | None) -> dict[str, Any]:
    exists = library is not None and library.is_dir()
    return {
        "engine_version": __version__,
        "library": str(library) if library is not None else None,
        "library_exists": exists,
        "is_library": exists and (library / ENGINE_FOLDER).is_dir(),
        "items_by_state": {},
        "queue": None,
        "warnings": [],
    }
