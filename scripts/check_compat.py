"""Manual check for step 04: can other apps read the tags the engine writes?

Usage (from the repo root, with the engine's virtual environment):

    .venv/bin/python scripts/check_compat.py [<folder>]

Makes a small scratch library in <folder> (default: a new temporary folder) holding a
10-second test tone as an M4A and as an MP3. Both are tagged with every field Music
Organizer writes, through the same verified write the engine uses. Then it prints what
to check in Apple Music and in Kid3 or MusicBrainz Picard.
"""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
from pathlib import Path

from musicorg import fileops, library, tags, tools
from musicorg.errors import MusicOrgError
from PIL import Image, ImageDraw

LYRICS = "First line of the lyrics\nSecond line — 二行目\nThird line: Café 🎵"


def cover() -> bytes:
    """A 600×600 JPEG with a clear pattern, easy to recognise in a player."""
    picture = Image.new("RGB", (600, 600), (30, 60, 120))
    draw = ImageDraw.Draw(picture)
    draw.rectangle((60, 60, 540, 540), outline=(250, 200, 40), width=24)
    draw.ellipse((180, 180, 420, 420), fill=(250, 200, 40))
    buffer = io.BytesIO()
    picture.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def tone(folder: Path, name: str, codec: list[str]) -> Path:
    path = folder / name
    subprocess.run(
        [
            str(tools.require("ffmpeg")),
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=10",
            *codec,
            str(path),
        ],
        check=True,
    )
    return path


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")  # type: ignore[attr-defined]  # Windows consoles
    base = Path(argv[0]).expanduser() if argv else Path(tempfile.mkdtemp(prefix="musicorg-"))
    base.mkdir(parents=True, exist_ok=True)
    source = base / "source"
    source.mkdir(exist_ok=True)
    root = base / "Compatibility Library"
    art = cover()
    try:
        library.init(root, remember=False)
        files = [
            (tone(source, "tone.m4a", ["-c:a", "aac", "-b:a", "128k"]), "M4A", 1),
            (tone(source, "tone.mp3", ["-c:a", "libmp3lame", "-b:a", "192k"]), "MP3", 2),
        ]
        landed = []
        with library.open(root, write=True, command="check_compat") as lib:
            with fileops.batch(lib, "demo") as b:
                for path, label, number in files:
                    rel = f"Music Organizer/Compatibility Check (2026)/0{number} Test Tone {label}"
                    target = fileops.copy_in(b, path, rel + path.suffix)
                    fileops.write_tags(b, target, test_tags(label, number, art))
                    landed.append(target)
    except MusicOrgError as exc:
        print(exc.message, file=sys.stderr)
        return exc.exit_code

    print(f"Made two tagged test files in {root / 'Music'}:")
    for path in landed:
        print(f"  {path}")
    print(INSTRUCTIONS)
    return 0


def test_tags(label: str, number: int, art: bytes) -> tags.TrackTags:
    return tags.TrackTags(
        title=f"Test Tone {label} — テスト",
        artist="Music Organizer",
        album_artist="Music Organizer",
        album="Compatibility Check",
        year=2026,
        track=number,
        track_total=2,
        disc=1,
        disc_total=1,
        genre="Test",
        lyrics=LYRICS,
        cover=art,
        explicit=True,
        schema=tags.SCHEMA_VERSION,
        musicorg_id=tags.new_track_id(),
        source="other",
        source_id=f"compat-check-{label.lower()}",
        source_format=label.lower(),
        source_bitrate=128 if label == "M4A" else 192,
        acquired="2026-09-29T12:00:00Z",
        match="manual",
        match_score=1.0,
        only_copy=True,
        origin_path=f"/tmp/source/tone.{label.lower()}",
        version=["demo", "remix:someone"],
    )


INSTRUCTIONS = """
1. Apple Music: drag both files into the Music app (you can delete them from the library
   afterwards). For each, check in Get Info (⌘I):
   - Title: "Test Tone M4A — テスト" / "Test Tone MP3 — テスト"
   - Artist and album artist: Music Organizer; album: Compatibility Check; year 2026
   - Track 1 of 2 (M4A), 2 of 2 (MP3); disc 1 of 1; genre Test
   - Artwork: a yellow circle in a yellow square on dark blue
   - Lyrics tab: three lines, including 二行目 and 🎵
   - The M4A shows the explicit (E) mark. Apple Music may not show it for the MP3.
2. Kid3 or MusicBrainz Picard (both free): open both files and check these fields:
   MUSICORG_SCHEMA 1, MUSICORG_ID (a long id), MUSICORG_SOURCE other,
   MUSICORG_SOURCE_ID, MUSICORG_SOURCE_FORMAT, MUSICORG_SOURCE_BITRATE,
   MUSICORG_ACQUIRED, MUSICORG_MATCH manual, MUSICORG_MATCH_SCORE 1.000,
   MUSICORG_ONLY_COPY 1, MUSICORG_ORIGIN_PATH and MUSICORG_VERSION
   "demo; remix:someone". In the MP3 they're TXXX frames (ID3v2.3); in the M4A,
   ----:com.apple.iTunes:MUSICORG_* atoms.
3. Write what you saw in docs/CHANGELOG.md under step 04.
"""


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
