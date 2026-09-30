"""artwork (step 10): checking and preparing covers, the official album cover at 1200 px,
and the owner's art_url (a picture, or a page with one). Pictures are made in memory;
nothing reaches the network."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from musicorg import artwork, youtube
from musicorg.errors import YouTubeError
from musicorg.youtube import Album


def picture(width: int, height: int, kind: str = "JPEG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buffer, kind)
    return buffer.getvalue()


def size_of(data: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size


def test_a_small_jpeg_is_kept_exactly() -> None:
    data = picture(600, 600)
    art = artwork.prepare(data)
    assert art.data == data and art.problems == ()


def test_a_large_picture_is_made_1200_px() -> None:
    art = artwork.prepare(picture(1425, 1425, "PNG"))
    assert size_of(art.data) == (1200, 1200)
    assert Image.open(io.BytesIO(art.data)).format == "JPEG"
    assert (art.width, art.height, art.mime) == (1200, 1200, "image/jpeg")


def test_a_non_square_or_small_picture_is_flagged_but_kept() -> None:
    wide = artwork.prepare(picture(1280, 720))
    assert wide.problems == ("not square (1280×720)",)
    assert size_of(wide.data) == (1200, 675)
    small = artwork.prepare(picture(300, 300))
    assert small.problems == ("small (300×300, under 500 px)",)


def test_not_a_picture() -> None:
    with pytest.raises(YouTubeError):
        artwork.prepare(b"not a picture")


def test_is_square() -> None:
    assert artwork.is_square(picture(544, 544))
    assert artwork.is_square(picture(544, 540))  # within 2%
    assert not artwork.is_square(picture(1280, 720))  # a video frame
    assert not artwork.is_square(None)
    assert not artwork.is_square(b"junk")


def test_sized_thumbnail() -> None:
    base = "https://lh3.googleusercontent.com/abc"
    assert youtube.sized_thumbnail(f"{base}=w544-h544-l90-rj", 1200) == f"{base}=w1200-h1200"
    assert youtube.sized_thumbnail(f"{base}=s0", 1200) == f"{base}=w1200-h1200"
    assert youtube.sized_thumbnail(base, 1200) == f"{base}=w1200-h1200"
    assert youtube.sized_thumbnail("https://example.com/a.jpg", 1200) == "https://example.com/a.jpg"


def test_the_album_cover_is_asked_for_at_1200_px_once_per_album(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thumbs = (("https://lh3.googleusercontent.com/x=w60-h60", 60, 60),
              ("https://lh3.googleusercontent.com/x=w544-h544-l90-rj", 544, 544))  # fmt: skip
    album = Album("MPREb_1", "Tunes", ("Band",), "2020", 9, False, (), thumbs)
    fetched: list[str] = []
    monkeypatch.setattr(youtube, "get_album", lambda browse_id, **kw: album)

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return picture(1200, 1200)

    monkeypatch.setattr(youtube, "fetch_image", fetch)
    first = artwork.album_art("MPREb_1")
    again = artwork.album_art("MPREb_1")
    assert first is not None and first is again
    assert fetched == ["https://lh3.googleusercontent.com/x=w1200-h1200"]


def test_an_album_without_a_cover(monkeypatch: pytest.MonkeyPatch) -> None:
    album = Album("MPREb_2", "Tunes", ("Band",), None, None, None, ())
    monkeypatch.setattr(youtube, "get_album", lambda browse_id, **kw: album)
    assert artwork.album_art("MPREb_2") is None


def test_art_url_a_picture_or_a_page(monkeypatch: pytest.MonkeyPatch) -> None:
    pictures = {"https://img.example/cover.jpg": picture(800, 800),
                "https://i1.sndcdn.com/artworks-x-t500x500.jpg": picture(500, 500)}  # fmt: skip

    def fetch(url: str) -> bytes:
        if url not in pictures:
            raise YouTubeError(f"{url} isn't a picture (text/html).")
        return pictures[url]

    monkeypatch.setattr(youtube, "fetch_image", fetch)
    monkeypatch.setattr(youtube, "page_thumbnail", lambda url: {
        "https://soundcloud.com/band/song": "https://i1.sndcdn.com/artworks-x-t500x500.jpg"
    }.get(url))  # fmt: skip
    assert size_of(artwork.art_from_url("https://img.example/cover.jpg").data) == (800, 800)  # type: ignore[union-attr]
    page = artwork.art_from_url("https://soundcloud.com/band/song")
    assert page is not None and size_of(page.data) == (500, 500)
    assert artwork.art_from_url("https://example.com/nothing-here") is None


def test_fetch_image_only_https() -> None:
    with pytest.raises(YouTubeError, match="https"):
        youtube.fetch_image("http://example.com/a.jpg")
