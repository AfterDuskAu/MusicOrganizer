"""Long videos: the segmented formats chosen, and the playlist written for the player."""

from __future__ import annotations

from typing import Any
from urllib.request import urlopen

import pytest

from musicorg import relay, youtube

VIDEO = "abcdefghijk"


def hls(format_id: str, **more: Any) -> dict[str, Any]:
    return {"format_id": format_id, "protocol": "m3u8_native",
            "url": f"https://example.invalid/{format_id}.m3u8", **more}  # fmt: skip


def info(duration: int = 3479) -> dict[str, Any]:
    return {
        "duration": duration,
        "like_count": 12,
        "format_id": "140-1",
        "url": "https://example.invalid/whole.m4a",
        "http_headers": {"User-Agent": "x"},
        "formats": [
            hls("233-0", language="de", language_preference=-1),
            hls("233-1", language="en", language_preference=10),
            hls("234-0", language="de", language_preference=-1),
            hls("234-1", language="en", language_preference=10),
            hls("232", vcodec="avc1.64001F", height=720, fps=30.0, tbr=2638.6, width=1280),
            hls("270", vcodec="avc1.640028", height=1080, fps=30.0, tbr=5362.0),
            hls("614", vcodec="vp09.00.40.08", height=1080, fps=30.0, tbr=8357.0),
            {"format_id": "137", "protocol": "https", "vcodec": "avc1.640028", "height": 1080,
             "url": "https://example.invalid/137.mp4"},
        ],
    }  # fmt: skip


def test_a_long_video_is_played_from_its_segmented_formats() -> None:
    found = youtube._segmented(VIDEO, info())
    assert found is not None and found.segmented
    # The better sound, in the original language; only pictures the player can show.
    assert found.audio.url == "https://example.invalid/234-1.m3u8"
    assert (found.sound_codec, found.audio.duration_s, found.audio.likes) == (
        "mp4a.40.2", 3479.0, 12,
    )  # fmt: skip
    assert [(q.label, q.url.rsplit("/", 1)[1], q.codec, q.width) for q in found.qualities] == [
        ("1080p", "270.m3u8", "avc1.640028", None),
        ("720p", "232.m3u8", "avc1.64001F", 1280),
    ]


def test_a_short_video_and_one_with_no_segmented_sound_play_as_before() -> None:
    assert youtube._segmented(VIDEO, info(duration=youtube.LONG_S - 1)) is None
    plain = info()
    plain["formats"] = [f for f in plain["formats"] if not f["format_id"].startswith("23")]
    assert youtube._segmented(VIDEO, plain) is None
    # Only the smaller sound on offer: that one is used.
    small = info()
    small["formats"] = [f for f in small["formats"] if not f["format_id"].startswith("234")]
    found = youtube._segmented(VIDEO, small)
    assert found is not None and found.sound_codec == "mp4a.40.5"
    assert found.audio.url.endswith("/233-1.m3u8")


def playlist() -> str:
    return relay.master_playlist(
        "https://example.invalid/232.m3u8", "https://example.invalid/234-1.m3u8",
        picture_codec="avc1.64001F", sound_codec="mp4a.40.2", kbps=2638.6, width=1280,
        height=720, fps=30,
    )  # fmt: skip


def test_the_playlist_names_one_picture_and_its_sound() -> None:
    assert playlist().splitlines() == [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="sound",NAME="Original",DEFAULT=YES,AUTOSELECT=YES,'
        'URI="https://example.invalid/234-1.m3u8"',
        '#EXT-X-STREAM-INF:BANDWIDTH=2768600,CODECS="avc1.64001F,mp4a.40.2",'
        'RESOLUTION=1280x720,FRAME-RATE=30,AUDIO="sound"',
        "https://example.invalid/232.m3u8",
    ]
    for wrong in ("http://example.invalid/a", 'https://example.invalid/a"b', "file:///etc/x"):
        with pytest.raises(ValueError):
            relay.master_playlist(
                wrong, "https://example.invalid/s", picture_codec="avc1", sound_codec="mp4a",
                kbps=None, width=None, height=720, fps=30,
            )  # fmt: skip


def test_the_address_is_on_this_computer_and_needs_its_key() -> None:
    served = relay.Relay()
    try:
        address = served.address_of(playlist())
        assert address.startswith("http://127.0.0.1:") and address.endswith(".m3u8")
        with urlopen(address, timeout=5) as answer:  # noqa: S310
            assert answer.headers["Content-Type"] == relay.PLAYLIST_TYPE
            assert answer.read().decode() == playlist()
        start, key, name = address.rsplit("/", 2)
        for wrong in (f"{start}/{'x' * len(key)}/{name}", f"{start}/{key}/nothing.m3u8", start):
            with pytest.raises(OSError):
                urlopen(wrong, timeout=5)  # noqa: S310
        # It listens on this computer only.
        assert served._server is not None
        assert served._server.server_address[0] == "127.0.0.1"
    finally:
        served.stop()
    with pytest.raises(OSError):
        urlopen(address, timeout=2)  # noqa: S310
