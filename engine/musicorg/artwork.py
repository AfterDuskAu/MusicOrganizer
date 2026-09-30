"""Cover art (step 10): square album covers, embedded in each file and saved once per
album folder as `cover.jpg`.

Where a cover comes from:

- **A track with an official YouTube Music match:** its album's cover. The largest
  thumbnail the album lists (usually 544 px) is asked for at 1200 px. Checked live
  (2026-09-30): a 1425 px original comes at 1200 px, and asking for more than the
  original returns the original, never an enlarged copy.
- **Anything else** (only-copy rips): no automatic cover, because a wrong cover is worse
  than none. The owner can give an `art_url` in the review spreadsheet: a picture
  (`https://…jpg`), or a page with one (SoundCloud, Bandcamp, YouTube…), whose picture
  is read with yt-dlp and nothing downloaded. The owner asked for this on 2026-09-30.

`prepare()` checks the picture:
- square within 2%, and at least 500 px; otherwise it's kept anyway and the problem logged
- converted to JPEG (quality 90, at most 1200 × 1200) in memory with Pillow
- a JPEG already 1200 px or smaller is left exactly as it came, never re-encoded

Fetches go through `youtube`'s rate limiter (CLAUDE.md rule 8).
"""

from __future__ import annotations

import io
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, UnidentifiedImageError

from musicorg import youtube
from musicorg.errors import YouTubeError

log = logging.getLogger(__name__)

MAX_PX = 1200
MIN_PX = 500
SQUARE_TOLERANCE = 0.02
JPEG_QUALITY = 90
_REMEMBERED = 16  # album covers kept in memory during a run (an album's songs come together)


@dataclass(frozen=True)
class Art:
    """A cover ready to embed: JPEG bytes and what was noticed about it."""

    data: bytes
    width: int
    height: int
    problems: tuple[str, ...] = ()
    mime: str = "image/jpeg"


@dataclass
class _Memory:
    covers: OrderedDict[str, Art | None] = field(default_factory=OrderedDict)

    def get(self, key: str) -> tuple[bool, Art | None]:
        if key in self.covers:
            self.covers.move_to_end(key)
            return True, self.covers[key]
        return False, None

    def put(self, key: str, art: Art | None) -> None:
        self.covers[key] = art
        while len(self.covers) > _REMEMBERED:
            self.covers.popitem(last=False)


_MEMORY = _Memory()


def album_art(browse_id: str, *, cache: Any | None = None) -> Art | None:
    """The cover of a YouTube Music album, or None if it has none or it can't be read."""
    known, art = _MEMORY.get(f"album {browse_id}")
    if known:
        return art
    art = None
    try:
        album = youtube.get_album(browse_id, cache=cache)
        if album.thumbnails:
            url, _, _ = album.thumbnails[-1]
            art = prepare(youtube.fetch_image(youtube.sized_thumbnail(url, MAX_PX)))
    except YouTubeError as exc:
        log.info("No cover for the album %s: %s", browse_id, exc)
    _MEMORY.put(f"album {browse_id}", art)
    return art


def art_from_url(url: str) -> Art | None:
    """The owner's `art_url`: a picture, or a page that offers one."""
    known, art = _MEMORY.get(f"url {url}")
    if known:
        return art
    art = None
    try:
        try:
            art = prepare(youtube.fetch_image(url))
        except YouTubeError as not_a_picture:
            picture = youtube.page_thumbnail(url)
            if picture is None:
                raise YouTubeError(f"{url} has no picture.") from not_a_picture
            art = prepare(youtube.fetch_image(picture))
    except YouTubeError as exc:
        log.warning("No cover from %s: %s", url, exc)
    _MEMORY.put(f"url {url}", art)
    return art


def prepare(data: bytes) -> Art:
    """Check a picture and make it a JPEG of at most 1200 px (see the module docstring).
    Raises YouTubeError if it isn't a picture Pillow can read."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise YouTubeError("That isn't a picture that can be read.") from exc
    width, height = image.size
    problems = []
    if abs(width - height) > SQUARE_TOLERANCE * max(width, height):
        problems.append(f"not square ({width}×{height})")
    if min(width, height) < MIN_PX:
        problems.append(f"small ({width}×{height}, under {MIN_PX} px)")
    if problems:
        log.info("Cover kept anyway: %s", "; ".join(problems))
    if image.format == "JPEG" and max(width, height) <= MAX_PX:
        return Art(data, width, height, tuple(problems))
    if max(width, height) > MAX_PX:
        image.thumbnail((MAX_PX, MAX_PX), Image.Resampling.LANCZOS)
    if image.mode != "RGB":
        image = image.convert("RGB")
    out = io.BytesIO()
    image.save(out, "JPEG", quality=JPEG_QUALITY)  # fileops-ok: in-memory
    return Art(out.getvalue(), image.size[0], image.size[1], tuple(problems))


def is_square(data: bytes | None) -> bool:
    """Whether an embedded cover is square (a converter's 16:9 video frame isn't)."""
    if not data:
        return False
    try:
        width, height = Image.open(io.BytesIO(data)).size
    except (UnidentifiedImageError, OSError):
        return False
    return abs(width - height) <= SQUARE_TOLERANCE * max(width, height)
