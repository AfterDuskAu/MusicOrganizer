"""tags: every field round-trips in M4A, MP3, FLAC, Opus and Ogg Vorbis with the audio
unchanged; fields the engine doesn't manage survive; messy MP3s read without errors; other
files give empty tags with a warning; probe and the audio hash.

These call tags.write_tags directly. In the engine only fileops does (CLAUDE.md rule 3),
on a staged copy; test_fileops_tags.py covers that path.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from fileops_support import image, place
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, COMM, ID3, RVA2, TCOM, TMOO, TSOP, TXXX, PictureType
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.oggopus import OggOpus

from musicorg import tags
from musicorg.errors import AudioError, NotFoundError, UserError
from musicorg.tags import REMOVE, TrackTags

WRITABLE = ["m4a", "mp3", "flac", "opus", "ogg"]
VORBIS = ["flac", "opus", "ogg"]

FULL = TrackTags(
    title="夜に駆ける 🎵",
    artist="YOASOBI",
    album_artist="Various Artists",
    album="THE BOOK (Déluxe Édition)",
    year=2021,
    track=3,
    track_total=12,
    disc=1,
    disc_total=2,
    genre="J-Pop",
    lyrics="沈むように\n溶けてゆくように\nCafé — naïve 😀",
    cover=image("JPEG"),
    explicit=True,
    schema=1,
    musicorg_id="3f0c5d2e-8a4b-4c1d-9e7f-2a6b8c0d1e3f",
    source="youtube_music",
    source_id="dQw4w9WgXcQ",
    source_format="140",
    source_bitrate=129,
    acquired="2026-10-02T09:14:00Z",
    match="auto_exact",
    match_score=0.987,
    only_copy=True,
    origin_path="/Users/someone/rips/Ядерный — ソング.mp3",
    version=["remix:adventure club", "extended"],
)


@pytest.fixture
def copy_of(samples: dict[str, Path], tmp_path: Path) -> Callable[[str], Path]:
    """A fresh copy of a sample file to change."""

    def make(kind: str) -> Path:
        ext = kind.rsplit(".", 1)[-1]
        return place(samples[kind], tmp_path / kind.replace(".", "-") / f"track.{ext}")

    return make


def written(changes: TrackTags) -> TrackTags:
    """What a file with no tags should read as after `changes`."""
    return tags.merge(TrackTags(), changes)


# ---- round trips -----------------------------------------------------------------------


@pytest.mark.parametrize("kind", WRITABLE)
def test_every_field_round_trips(copy_of: Callable[[str], Path], kind: str) -> None:
    path = copy_of(kind)
    audio = tags.audio_hash(path)
    tags.write_tags(path, FULL)
    got = tags.read_tags(path)
    assert got == written(FULL)
    assert got.cover == FULL.cover
    assert got.cover_mime == "image/jpeg"
    assert got.warnings == []
    assert tags.audio_hash(path) == audio


@pytest.mark.parametrize("kind", WRITABLE)
def test_a_png_cover(copy_of: Callable[[str], Path], kind: str) -> None:
    path = copy_of(kind)
    png = image("PNG", (10, 200, 10))
    tags.write_tags(path, TrackTags(cover=png))
    got = tags.read_tags(path)
    assert (got.cover, got.cover_mime) == (png, "image/png")


@pytest.mark.parametrize("kind", WRITABLE)
def test_a_later_write_changes_only_what_it_names(
    copy_of: Callable[[str], Path], kind: str
) -> None:
    path = copy_of(kind)
    tags.write_tags(path, FULL)
    change = TrackTags(title="New Title", lyrics=REMOVE, cover=REMOVE, version=["live"])
    tags.write_tags(path, change)
    expected = written(FULL)
    expected.title, expected.lyrics, expected.version = "New Title", None, ["live"]
    expected.cover = expected.cover_mime = None
    assert tags.read_tags(path) == expected


@pytest.mark.parametrize("kind", WRITABLE)
def test_track_and_disc_numbers(copy_of: Callable[[str], Path], kind: str) -> None:
    path = copy_of(kind)
    tags.write_tags(path, TrackTags(track=3, track_total=12, disc=2))
    got = tags.read_tags(path)
    assert (got.track, got.track_total, got.disc, got.disc_total) == (3, 12, 2, None)
    tags.write_tags(path, TrackTags(track=5))  # the total stays
    assert (tags.read_tags(path).track, tags.read_tags(path).track_total) == (5, 12)
    tags.write_tags(path, TrackTags(track=REMOVE))  # a total alone isn't kept
    got = tags.read_tags(path)
    assert (got.track, got.track_total, got.disc) == (None, None, 2)


@pytest.mark.parametrize("kind", WRITABLE)
def test_none_leaves_a_field_and_empty_text_is_never_written(
    copy_of: Callable[[str], Path], kind: str
) -> None:
    path = copy_of(kind)
    tags.write_tags(path, TrackTags(title="Kept", artist="Someone"))
    tags.write_tags(path, TrackTags(title="", artist="   ", album="Album"))
    got = tags.read_tags(path)
    assert (got.title, got.artist, got.album) == ("Kept", "Someone", "Album")


@pytest.mark.parametrize("kind", WRITABLE)
def test_flags(copy_of: Callable[[str], Path], kind: str) -> None:
    path = copy_of(kind)
    tags.write_tags(path, TrackTags(explicit=False, only_copy=True))
    got = tags.read_tags(path)
    assert (got.explicit, got.only_copy) == (False, True)
    tags.write_tags(path, TrackTags(only_copy=False))  # "not only-copy" means absent
    assert tags.read_tags(path).only_copy is None


def test_the_match_score_keeps_three_decimals(copy_of: Callable[[str], Path]) -> None:
    path = copy_of("m4a")
    tags.write_tags(path, TrackTags(match_score=0.98765))
    assert tags.read_tags(path).match_score == 0.988


# ---- how the tags look in each format --------------------------------------------------


def test_mp3_is_id3v23_with_the_year_in_tyer(copy_of: Callable[[str], Path]) -> None:
    path = copy_of("mp3")
    tags.write_tags(path, FULL)
    raw = path.read_bytes()
    assert raw.startswith(b"ID3\x03")
    assert b"TYER" in raw
    assert b"TDRC" not in raw
    id3 = ID3(path, translate=False)
    assert id3.version == (2, 3, 0)
    assert id3["TXXX:MUSICORG_ID"].text == [FULL.musicorg_id]
    assert id3["TXXX:MUSICORG_VERSION"].text == ["remix:adventure club; extended"]
    assert id3["TXXX:ITUNESADVISORY"].text == ["1"]
    assert id3["TRCK"].text == ["3/12"]
    [front] = [f for f in id3.getall("APIC") if f.type == PictureType.COVER_FRONT]
    assert front.mime == "image/jpeg"


def test_m4a_atoms(copy_of: Callable[[str], Path]) -> None:
    path = copy_of("m4a")
    tags.write_tags(path, FULL)
    atoms = MP4(path).tags
    assert atoms is not None
    assert atoms["----:com.apple.iTunes:MUSICORG_ID"] == [FULL.musicorg_id.encode()]
    assert atoms["----:com.apple.iTunes:MUSICORG_SOURCE_FORMAT"] == [b"140"]
    assert atoms["rtng"] == [1]
    assert atoms["trkn"] == [(3, 12)]
    assert atoms["disk"] == [(1, 2)]
    assert atoms["©day"] == ["2021"]
    assert atoms["covr"][0].imageformat == MP4Cover.FORMAT_JPEG


@pytest.mark.parametrize("kind", VORBIS)
def test_vorbis_comments(copy_of: Callable[[str], Path], kind: str) -> None:
    path = copy_of(kind)
    tags.write_tags(path, FULL)
    import mutagen

    comments = mutagen.File(path).tags
    assert comments["MUSICORG_ID"] == [FULL.musicorg_id]
    assert comments["TRACKNUMBER"] == ["3"]
    assert comments["TRACKTOTAL"] == ["12"]
    assert comments["DATE"] == ["2021"]
    assert comments["ITUNESADVISORY"] == ["1"]
    assert comments["MUSICORG_VERSION"] == ["remix:adventure club; extended"]


# ---- what the engine doesn't manage survives -------------------------------------------


def test_unmanaged_m4a_atoms_survive(copy_of: Callable[[str], Path]) -> None:
    path = copy_of("m4a")
    audio = MP4(path)
    gapless = MP4FreeForm(b" 00000000 00000840 000001C0 0000000000020A00")
    audio.tags["----:com.apple.iTunes:iTunSMPB"] = [gapless]
    audio.tags["----:com.apple.iTunes:SOMETHING_ELSE"] = [MP4FreeForm(b"kept")]
    audio.tags["©wrt"] = ["A Composer"]
    audio.tags["cpil"] = True
    audio.save()
    tags.write_tags(path, FULL)
    tags.write_tags(path, TrackTags(title=REMOVE, cover=REMOVE))
    after = MP4(path).tags
    assert after is not None
    assert after["----:com.apple.iTunes:iTunSMPB"] == [gapless]
    assert after["----:com.apple.iTunes:SOMETHING_ELSE"] == [b"kept"]
    assert after["©wrt"] == ["A Composer"]
    assert after["cpil"] is True


def test_unmanaged_id3_frames_survive(copy_of: Callable[[str], Path]) -> None:
    path = copy_of("mp3")
    id3 = ID3(path)
    id3.add(TCOM(encoding=3, text=["A Composer"]))
    id3.add(COMM(encoding=3, lang="eng", desc="", text=["a comment"]))
    id3.add(TXXX(encoding=3, desc="OTHER", text=["kept"]))
    id3.add(TSOP(encoding=3, text=["Beatles, The"]))  # v2.4 frames players read in v2.3 too
    id3.add(TMOO(encoding=3, text=["Calm"]))
    id3.add(RVA2(desc="track", channel=1, gain=-3.5, peak=0.9))
    id3.add(APIC(encoding=0, mime="image/png", type=PictureType.OTHER, desc="", data=image("PNG")))
    id3.save(path)
    tags.write_tags(path, FULL)
    after = ID3(path)
    assert after["TCOM"].text == ["A Composer"]
    assert after["COMM::eng"].text == ["a comment"]
    assert after["TXXX:OTHER"].text == ["kept"]
    assert after["TSSE"].text  # ffmpeg's encoder note
    assert after["TSOP"].text == ["Beatles, The"]
    assert after["TMOO"].text == ["Calm"]
    assert after["RVA2:track"].gain == pytest.approx(-3.5)
    assert ID3(path).version == (2, 3, 0)
    pictures = {f.type: f.data for f in after.getall("APIC")}
    assert pictures == {PictureType.OTHER: image("PNG"), PictureType.COVER_FRONT: FULL.cover}


def test_unknown_id3_frames_in_a_v23_file_survive(samples: dict[str, Path], tmp_path: Path) -> None:
    path = tmp_path / "unknown.mp3"
    unknown = frame23(b"ZZZZ", b"opaque data")
    path.write_bytes(id3v23([frame23(b"TIT2", b"\x00Title"), unknown]) + mp3_audio(samples))
    tags.write_tags(path, TrackTags(artist="Someone"))
    after = ID3(path)
    assert after.unknown_frames == [unknown]
    assert (after["TIT2"].text, after["TPE1"].text) == (["Title"], ["Someone"])


@pytest.mark.parametrize("kind", VORBIS)
def test_unmanaged_vorbis_fields_survive(copy_of: Callable[[str], Path], kind: str) -> None:
    import mutagen

    path = copy_of(kind)
    audio = mutagen.File(path)
    audio.tags["COMPOSER"] = ["A Composer"]
    back = Picture()
    back.type, back.mime, back.data = PictureType.COVER_BACK, "image/png", image("PNG")
    if isinstance(audio, FLAC):
        audio.add_picture(back)
    else:
        import base64

        audio.tags["METADATA_BLOCK_PICTURE"] = [base64.b64encode(back.write()).decode()]
    audio.save()
    tags.write_tags(path, FULL)
    tags.write_tags(path, TrackTags(cover=REMOVE))
    after = mutagen.File(path)
    assert after.tags["COMPOSER"] == ["A Composer"]
    if isinstance(after, FLAC):
        assert [p.type for p in after.pictures] == [PictureType.COVER_BACK]
    else:
        assert len(after.tags["METADATA_BLOCK_PICTURE"]) == 1
    assert tags.read_tags(path).cover is None


# ---- messy MP3s ------------------------------------------------------------------------


def syncsafe(n: int) -> bytes:
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def frame23(frame_id: bytes, data: bytes) -> bytes:
    return frame_id + len(data).to_bytes(4, "big") + b"\x00\x00" + data


def id3v23(frames: list[bytes]) -> bytes:
    body = b"".join(frames)
    return b"ID3\x03\x00\x00" + syncsafe(len(body)) + body


def mp3_audio(samples: dict[str, Path]) -> bytes:
    return samples["bare.mp3"].read_bytes()


def test_an_id3v1_only_rip(samples: dict[str, Path], tmp_path: Path) -> None:
    v1 = (
        b"TAG"
        + b"Old Title".ljust(30, b"\0")
        + b"Old Artist".ljust(30, b"\0")
        + b"Old Album".ljust(30, b"\0")
        + b"1999"
        + b"".ljust(30, b"\0")
        + bytes([17])  # Rock
    )
    path = tmp_path / "v1.mp3"
    path.write_bytes(mp3_audio(samples) + v1)
    got = tags.read_tags(path)
    assert (got.title, got.artist, got.album, got.year, got.genre) == (
        "Old Title",
        "Old Artist",
        "Old Album",
        1999,
        "Rock",
    )


def test_an_id3v22_rip(samples: dict[str, Path], tmp_path: Path) -> None:
    cover = image("JPEG")
    pic = b"\x00JPG\x03\x00" + cover  # encoding, format, front cover, empty description

    def frame22(frame_id: bytes, data: bytes) -> bytes:
        return frame_id + len(data).to_bytes(3, "big") + data

    body = frame22(b"TT2", b"\x00Old Title") + frame22(b"PIC", pic)
    path = tmp_path / "v22.mp3"
    path.write_bytes(b"ID3\x02\x00\x00" + syncsafe(len(body)) + body + mp3_audio(samples))
    got = tags.read_tags(path)
    assert (got.title, got.cover, got.cover_mime) == ("Old Title", cover, "image/jpeg")
    tags.write_tags(path, TrackTags(artist="Someone"))  # saved as v2.3 from now on
    assert ID3(path).version == (2, 3, 0)
    assert tags.read_tags(path).title == "Old Title"


def test_duplicate_frames(samples: dict[str, Path], tmp_path: Path) -> None:
    path = tmp_path / "dup.mp3"
    frames = [frame23(b"TIT2", b"\x00First"), frame23(b"TIT2", b"\x00Second")]
    path.write_bytes(id3v23(frames) + mp3_audio(samples))
    assert tags.read_tags(path).title == "First; Second"


def test_mis_encoded_text_is_repaired(samples: dict[str, Path], tmp_path: Path) -> None:
    path = tmp_path / "mojibake.mp3"
    utf8_in_latin1 = b"\x00" + "Café Tacvba".encode()  # says Latin-1, is UTF-8
    latin1 = b"\x00" + "Café".encode("latin-1")  # really Latin-1: left alone
    frames = [frame23(b"TPE1", utf8_in_latin1), frame23(b"TALB", latin1)]
    path.write_bytes(id3v23(frames) + mp3_audio(samples))
    got = tags.read_tags(path)
    assert (got.artist, got.album) == ("Café Tacvba", "Café")
    assert any("repaired" in w for w in got.warnings)


def test_a_broken_tag_gives_warnings_not_errors(samples: dict[str, Path], tmp_path: Path) -> None:
    broken = tmp_path / "broken.mp3"
    bad_frame = b"TIT2" + (10_000).to_bytes(4, "big") + b"\x00\x00\x00short"
    broken.write_bytes(id3v23([bad_frame]) + mp3_audio(samples))
    assert isinstance(tags.read_tags(broken), TrackTags)

    garbage = tmp_path / "garbage.mp3"
    garbage.write_bytes(os.urandom(4000))
    got = tags.read_tags(garbage)
    assert got == TrackTags()
    assert got.warnings


def test_a_bare_mp3_gets_tags(copy_of: Callable[[str], Path]) -> None:
    path = copy_of("bare.mp3")
    assert tags.read_tags(path) == TrackTags()
    tags.write_tags(path, FULL)
    assert tags.read_tags(path) == written(FULL)


# ---- files whose tags aren't read ------------------------------------------------------


@pytest.mark.parametrize("kind", ["webm", "wav", "aac"])
def test_files_that_arent_adopted_give_empty_tags(
    copy_of: Callable[[str], Path], kind: str
) -> None:
    path = copy_of(kind)
    got = tags.read_tags(path)
    assert got == TrackTags()
    assert got.warnings == [f"track.{kind}: tags aren't read from .{kind} files"]
    with pytest.raises(UserError, match="doesn't write tags"):
        tags.check_writable(path)
    with pytest.raises(UserError, match="doesn't write tags"):
        tags.write_tags(path, TrackTags(title="x"))


def test_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(NotFoundError):
        tags.read_tags(tmp_path / "missing.m4a")


# ---- checking values -------------------------------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        TrackTags(source="spotify"),
        TrackTags(match="probably"),
        TrackTags(year=0),
        TrackTags(year="2021"),  # type: ignore[arg-type]
        TrackTags(track=-1),
        TrackTags(match_score=1.5),
        TrackTags(explicit="yes"),  # type: ignore[arg-type]
        TrackTags(cover=b"not an image"),
        TrackTags(cover=image("JPEG"), cover_mime="image/png"),
        TrackTags(cover_mime="image/png"),
        TrackTags(version=["remix:a; live"]),
        TrackTags(title=5),  # type: ignore[arg-type]
    ],
)
def test_values_outside_the_schema_are_refused(changes: TrackTags) -> None:
    with pytest.raises(ValueError):
        tags.prepare(changes)


def test_the_journal_form(copy_of: Callable[[str], Path]) -> None:
    record = tags.to_record(written(FULL))
    assert record["cover"] == __import__("hashlib").sha256(FULL.cover).hexdigest()
    assert record["version"] == ["remix:adventure club", "extended"]
    assert "warnings" not in record
    restored = tags.from_record(record, FULL.cover)
    assert tags.merge(TrackTags(), restored) == written(FULL)
    with pytest.raises(ValueError, match="image is needed"):
        tags.from_record(record)
    removal = tags.from_record({"cover": REMOVE, "cover_mime": REMOVE, "title": REMOVE})
    assert (removal.cover, removal.title) == (REMOVE, REMOVE)


def test_new_track_ids() -> None:
    first, second = tags.new_track_id(), tags.new_track_id()
    assert first != second
    assert uuid.UUID(first).version == 4


# ---- probe and the audio hash ----------------------------------------------------------

CODECS = {
    "m4a": "aac",
    "mp3": "mp3",
    "flac": "flac",
    "opus": "opus",
    "ogg": "vorbis",
    "webm": "opus",
    "wav": "pcm_s16le",
    "aac": "aac",
}


@pytest.mark.parametrize("kind", sorted(CODECS))
def test_probe(samples: dict[str, Path], kind: str) -> None:
    info = tags.probe(samples[kind])
    assert info.codec == CODECS[kind]
    assert info.duration_s == pytest.approx(3.0, abs=0.1)
    assert info.sample_rate in (44100, 48000)
    assert info.channels == 1
    assert info.bitrate_kbps is not None and info.bitrate_kbps > 0


def test_probe_and_hash_refuse_what_isnt_audio(tmp_path: Path) -> None:
    text = tmp_path / "notes.m4a"
    text.write_text("not audio at all")
    with pytest.raises(AudioError, match="notes.m4a"):
        tags.probe(text)
    with pytest.raises(AudioError, match="notes.m4a"):
        tags.audio_hash(text)
    with pytest.raises(NotFoundError):
        tags.audio_hash(tmp_path / "missing.m4a")


def test_audio_hash(samples: dict[str, Path], copy_of: Callable[[str], Path]) -> None:
    first = tags.audio_hash(samples["m4a"])
    assert re.fullmatch(r"[0-9a-f]{32}", first)
    assert tags.audio_hash(copy_of("m4a")) == first  # the same audio, another file
    assert tags.audio_hash(samples["mp3"]) != first  # another encoding of the melody
    assert tags.audio_hash(samples["wav"]) != first


def test_opus_pictures_are_base64_blocks(copy_of: Callable[[str], Path]) -> None:
    path = copy_of("opus")
    tags.write_tags(path, TrackTags(cover=image("JPEG")))
    assert len(OggOpus(path).tags["METADATA_BLOCK_PICTURE"]) == 1
