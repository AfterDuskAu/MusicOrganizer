"""`musicorg status`: what the engine knows about the library.

Reports which library is in use and its environment warnings (step 03a). Item counts
(step 05) and the queue state (step 09a) are added by later steps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from musicorg import __version__, library


def get_status(root: Path | None) -> dict[str, Any]:
    exists = root is not None and root.is_dir()
    is_library = root is not None and exists and library.is_library(root)
    return {
        "engine_version": __version__,
        "library": str(root) if root is not None else None,
        "library_exists": exists,
        "is_library": is_library,
        "items_by_state": {},
        "queue": None,
        "warnings": library.environment_warnings(root) if root is not None and is_library else [],
    }
