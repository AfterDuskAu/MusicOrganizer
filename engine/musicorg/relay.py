"""Playlists for long videos, handed to the app's player from this computer (2026-10-07).

Apple's player can't start one of YouTube's ordinary files until it has read all of it,
and YouTube sends a large file slowly when it's asked for whole (measured: an hour of
sound at about 32 KB/s). A song arrives in a moment; an hour-long video never starts.
YouTube also offers every video cut into six-second segments (HLS), which the player
starts in a second or two. But the list YouTube gives of those includes pictures the
player can't show (VP9), and it stops on them; and it won't play a picture's segments
without a list that says what they are.

So for a long video the engine writes that list itself: one picture size (H.264) and
the sound in its original language, each pointing at YouTube's own segments. The player
needs an address to read the list from, and this is it.

- **This computer only:** 127.0.0.1, with a key made afresh each time the engine starts.
- **A few lines of text and nothing else:** no sound or picture passes through here; the
  player fetches those from YouTube itself, as it always has. Nothing is written to disk.
- **Nothing more is asked of YouTube:** the addresses in a list are ones `youtube` found.
"""

from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MOST = 256  # lists remembered; the oldest is forgotten first
PLAYLIST_TYPE = "application/vnd.apple.mpegurl"
SOUND_KBPS = 130  # format 234, for the list's estimate of what a second takes


def master_playlist(
    picture_url: str,
    sound_url: str,
    *,
    picture_codec: str,
    sound_codec: str,
    kbps: float | None,
    width: int | None,
    height: int,
    fps: int,
) -> str:
    """The list for one picture size with its sound, as HLS wants it (RFC 8216)."""
    for address in (picture_url, sound_url):
        if not address.startswith("https://") or '"' in address or "\n" in address:
            raise ValueError("a playlist's addresses are YouTube's https ones")
    rate = int(((kbps or 0) + SOUND_KBPS) * 1000) or 1
    size = f",RESOLUTION={width}x{height}" if width else ""
    return (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="sound",NAME="Original",DEFAULT=YES,'
        f'AUTOSELECT=YES,URI="{sound_url}"\n'
        f'#EXT-X-STREAM-INF:BANDWIDTH={rate},CODECS="{picture_codec},{sound_codec}"'
        f'{size},FRAME-RATE={fps},AUDIO="sound"\n'
        f"{picture_url}\n"
    )


class Relay:
    """The address on this computer that a long video's lists are read from."""

    def __init__(self) -> None:
        self._key = secrets.token_urlsafe(24)
        self._texts: OrderedDict[str, bytes] = OrderedDict()
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    def address_of(self, text: str) -> str:
        """An address the app's player can read this list from, until the engine stops
        (or `MOST` newer ones have been made)."""
        with self._lock:
            if self._server is None:
                self._start()
            assert self._server is not None
            name = secrets.token_urlsafe(12)
            self._texts[name] = text.encode()
            while len(self._texts) > MOST:
                self._texts.popitem(last=False)
            port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}/{self._key}/{name}.m3u8"

    def stop(self) -> None:
        with self._lock:
            server, self._server = self._server, None
            self._texts.clear()
        if server is not None:
            server.shutdown()
            server.server_close()

    def _start(self) -> None:
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass  # an address holds the key: nothing of a request is logged

            def do_GET(self) -> None:
                relay._answer(self, head=False)

            def do_HEAD(self) -> None:
                relay._answer(self, head=True)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def _answer(self, handler: BaseHTTPRequestHandler, *, head: bool) -> None:
        parts = handler.path.split("?", 1)[0].strip("/").split("/")
        name = parts[1].removesuffix(".m3u8") if len(parts) == 2 else ""
        with self._lock:
            text = self._texts.get(name)
        if text is None or not secrets.compare_digest(parts[0], self._key):
            handler.send_error(404)
            return
        handler.send_response(200)
        handler.send_header("Content-Type", PLAYLIST_TYPE)
        handler.send_header("Content-Length", str(len(text)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        if not head:
            handler.wfile.write(text)
