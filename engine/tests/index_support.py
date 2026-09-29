"""Test helpers: scanned items and match candidates put straight into the index."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from musicorg import match
from musicorg.index import Index, item_id
from musicorg.normalize import parse_filename
from musicorg.youtube import OFFICIAL_AUDIO, Candidate

SOURCE = "s_000000000001"


def add_source(index: Index, folder: Path) -> None:
    index.put_source(SOURCE, str(folder), "2026-09-29T00:00:00Z")


def add_item(
    index: Index,
    name: str,
    *,
    state: str = "new",
    seconds: float = 200.0,
    reasons: list[str] | None = None,
    flags: list[str] | None = None,
    ext: str = ".mp3",
    kbps: int | None = 128,
) -> str:
    """An item for a rip called "<name><ext>", parsed from its name."""
    parsed = parse_filename(name)
    rel = f"{name}{ext}"
    iid = item_id(SOURCE, rel)
    index.put_items([{
        "id": iid, "source_id": SOURCE, "rel_path": rel, "size": 1, "mtime_ns": 1, "ext": ext,
        "duration_s": seconds, "bitrate_kbps": kbps, "raw_tags_json": {"tags": {}},
        "parsed_artist": parsed.artist, "parsed_title": parsed.title,
        "parsed_version_json": list(parsed.version_tokens),
        "parse_confidence": parsed.confidence, "parsed_json": parsed.to_dict(),
        "flags_json": flags or [], "state": state, "reasons_json": reasons or [],
        "scanned_at": "2026-09-29T00:00:00Z",
    }])  # fmt: skip
    return iid


def candidate(
    video_id: str, title: str, artists: tuple[str, ...], seconds: int = 200, **kw: Any
) -> Candidate:
    return Candidate(video_id, title, artists, kw.get("album", "Album"), "MPREb_1", seconds,
                     kw.get("explicit", False), kw.get("video_type", OFFICIAL_AUDIO))  # fmt: skip


def add_candidates(index: Index, iid: str, options: list[Candidate]) -> None:
    """Score `options` against the item and store them, keeping the item's state."""
    item = index.item(iid)
    assert item is not None
    rip = match.rip_of(item)
    scored = sorted((match.assess(rip, c) for c in options), key=lambda s: -s.score)
    rows = [{"id": s.candidate_id(iid), "video_id": s.candidate.video_id, "score": s.score,
             "reasons": list(s.reasons), "payload": s.payload(iid)} for s in scored]  # fmt: skip
    index.set_match(iid, item["state"], item["reasons_json"], rows)
