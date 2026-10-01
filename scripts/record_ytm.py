"""Record real YouTube Music responses for replay mode (step 06).

Usage (from the repo root, with the engine's virtual environment):

    .venv/bin/python scripts/record_ytm.py search "Flight Facilities Crave You" ...
    .venv/bin/python scripts/record_ytm.py videos "J. Cole Work Out" ...
    .venv/bin/python scripts/record_ytm.py watch -- xjj_OVvVQFc -e-y-1VRZ3I ...
    .venv/bin/python scripts/record_ytm.py album MPREb_d8g28l4HU1r ...
    .venv/bin/python scripts/record_ytm.py radio 5qZQEq_C3vc ...
    .venv/bin/python scripts/record_ytm.py artist "Linkin Park" ...
    .venv/bin/python scripts/record_ytm.py genre "jazz" ...
    .venv/bin/python scripts/record_ytm.py cases [engine/tests/data/match_cases.json]

`cases` records every search the matcher would make for each case in the evaluation
harness (all of them, not stopping at an AUTO hit). Responses go to
engine/tests/fixtures/ytm/ (or --out), one JSON file per request, named the way
`musicorg.youtube` looks them up. Existing recordings are kept unless --force. Put `--`
before ids that start with a dash. A request that fails (e.g. a video that doesn't
exist) is recorded as its error and replayed as the same error.

`artist` records two requests (the artist search, then that artist's radio) and `genre`
two as well (the playlist search, then the playlist chosen): what Discover asks for.
`--tracks N` keeps only the first N tracks of a radio or playlist.

Requests go through the engine's rate limiter. Opaque feedback tokens are removed, a
watch playlist keeps only its first track and an album drops its recommendations; a
radio or playlist keeps only its tracks and the fields the engine reads. The file says
what was trimmed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from musicorg import match, youtube
from musicorg.normalize import Parsed
from ytmusicapi.exceptions import YTMusicServerError

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "engine" / "tests" / "fixtures" / "ytm"
DEFAULT_CASES = REPO / "engine" / "tests" / "data" / "match_cases.json"
DROPPED_KEYS = {"feedbackTokens", "listenAgainFeedbackTokens"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "kind",
        choices=["search", "videos", "watch", "album", "radio", "artist", "genre", "cases"],
    )
    parser.add_argument("args", nargs="*")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--force", action="store_true", help="Record again even if recorded.")
    parser.add_argument("--tracks", type=int, help="Keep only this many tracks of a radio.")
    opts = parser.parse_args()
    if opts.kind in ("artist", "genre"):
        recorded = sum(record_pair(opts, arg) for arg in opts.args)
        print(f"Recorded {recorded} requests.")
        return 0

    if opts.kind == "cases":
        cases_file = Path(opts.args[0]) if opts.args else DEFAULT_CASES
        requests = [("search", q) for q in case_queries(cases_file)]
    else:
        requests = [(opts.kind, arg) for arg in opts.args]
    if not requests:
        parser.error("nothing to record")

    recorded = 0
    for kind, arg in requests:
        key = youtube.query_key(arg) if kind in ("search", "videos") else arg
        path = youtube.recording_path(opts.out, kind, key)
        if path.exists() and not opts.force:
            continue
        error = None
        try:
            response, trimmed = fetch(kind, arg)
        except YTMusicServerError as exc:
            if youtube.is_slow_down(exc):
                raise
            response, trimmed, error = None, [], str(exc)  # replayed as the same error
        if kind == "radio" and response is not None:
            response = cut(response, opts.tracks, trimmed)
        write(path, kind, key, arg, response, trimmed, error)
        recorded += 1
    kept = len(requests) - recorded
    print(f"Recorded {recorded} of {len(requests)} requests ({kept} already there).")
    return 0


def write(
    path: Path, kind: str, key: str, arg: str, response: Any, trimmed: list[str], error: str | None
) -> None:
    record = {
        "kind": kind,
        "key": key,
        "request": arg,
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ytmusicapi": version("ytmusicapi"),
        "trimmed": trimmed,
        "error": error,
        "response": response,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"  {kind} {arg!r} -> {path.relative_to(REPO)}", file=sys.stderr)


def cut(response: Any, tracks: int | None, trimmed: list[str]) -> Any:
    """A radio or playlist as the engine keeps it: its tracks, and only the fields read."""
    response = youtube.trim_tracks(response)
    trimmed.append("everything but the tracks' fields the engine reads")
    if tracks is not None and len(response["tracks"]) > tracks:
        response["tracks"] = response["tracks"][:tracks]
        trimmed.append(f"all but the first {tracks} tracks")
    return response


def record_pair(opts: argparse.Namespace, name: str) -> int:
    """An artist and their radio, or a genre's playlists and the one chosen: the two
    requests `youtube.artist_radio` and `youtube.genre_playlist` make."""
    key = youtube.query_key(name)
    recorded = 0
    if opts.kind == "artist":
        first, second, keep = "artists", "artist-radio", ("artist", "browseId", "radioId")
        flt = "artists"
    else:
        first, second, keep = "genre", "playlist", ("title", "browseId", "author")
        flt = "featured_playlists"
    path = youtube.recording_path(opts.out, first, key)
    if path.exists() and not opts.force:
        found = json.loads(path.read_text(encoding="utf-8"))["response"]
    else:
        limit = 5 if opts.kind == "artist" else 20
        raw = youtube.fetch_live(lambda client: client.search(name, filter=flt, limit=limit))
        found = [{k: r.get(k) for k in keep} for r in raw if isinstance(r, dict)]
        write(path, first, key, name, found, ["everything but " + ", ".join(keep)], None)
        recorded += 1
    if opts.kind == "artist":
        wanted = youtube.artist_key(name)
        chosen = next(
            (r for r in found if r.get("radioId") and youtube.artist_key(r["artist"]) == wanted),
            None,
        )
        second_key = chosen["radioId"] if chosen else None
    else:
        chosen = youtube._genre_choice(found, key)
        second_key = chosen["browseId"].removeprefix("VL") if chosen else None
    if second_key is None:
        print(f"  nothing on YouTube Music is called {name!r}", file=sys.stderr)
        return recorded
    path = youtube.recording_path(opts.out, second, second_key)
    if path.exists() and not opts.force:
        return recorded
    if opts.kind == "artist":
        raw = youtube.fetch_live(
            lambda client: client.get_watch_playlist(
                playlistId=second_key, limit=youtube.ARTIST_RADIO_SONGS
            )
        )
    else:
        raw = youtube.fetch_live(
            lambda client: client.get_playlist(second_key, limit=youtube.GENRE_PLAYLIST_SONGS)
        )
    trimmed: list[str] = []
    write(path, second, second_key, name, cut(raw, opts.tracks, trimmed), trimmed, None)
    return recorded + 1


def fetch(kind: str, arg: str) -> tuple[Any, list[str]]:
    trimmed: list[str] = []
    if kind == "search":
        response = youtube.fetch_live(lambda client: client.search(arg, filter="songs", limit=10))
    elif kind == "videos":
        response = youtube.fetch_live(lambda client: client.search(arg, filter="videos", limit=10))
    elif kind == "radio":
        response = youtube.fetch_live(
            lambda client: client.get_watch_playlist(
                videoId=arg, radio=True, limit=youtube.RADIO_SONGS
            )
        )
    elif kind == "watch":
        response = youtube.fetch_live(
            lambda client: client.get_watch_playlist(videoId=arg, limit=1)
        )
        response = {"tracks": response.get("tracks", [])[:1]}
        trimmed.append("all but the first track, lyrics, related")
    else:
        response = youtube.fetch_live(lambda client: client.get_album(arg))
        for key in ("related_recommendations", "description", "descriptionRuns"):
            if response.pop(key, None) is not None:
                trimmed.append(key)
    if drop_tokens(response):
        trimmed.append("feedback tokens")
    return response, trimmed


def drop_tokens(value: Any) -> bool:
    """Remove opaque feedback tokens everywhere; True if any were there."""
    found = False
    if isinstance(value, dict):
        for key in DROPPED_KEYS & value.keys():
            del value[key]
            found = True
        for child in value.values():
            found = drop_tokens(child) or found
    elif isinstance(value, list):
        for child in value:
            found = drop_tokens(child) or found
    return found


def case_queries(cases_file: Path) -> list[str]:
    cases = json.loads(cases_file.read_text(encoding="utf-8"))["cases"]
    queries: list[str] = []
    for case in cases:
        for query in match.queries(Parsed.from_dict(case["parsed"])):
            if query not in queries:
                queries.append(query)
    return queries


if __name__ == "__main__":
    sys.exit(main())
