"""Playlists brought in from elsewhere (v0.3, started 2026-10-02 at the owner's request).

A playlist is read from where it lives, each of its songs is found on YouTube Music,
and the app then asks for the ones the owner doesn't have through the usual download
plan and queue (`plan.create` kind `download`, with `playlist_id`: each song joins the
owner's playlist of the same name as it arrives). Nothing here downloads anything or
changes the library.

Sources (docs/ENGINE_API.md → "Import source"):

- `youtube`: a YouTube or YouTube Music playlist, by its link. No sign-in: the playlist
  has to be public or unlisted. Its entries already are YouTube tracks, so a song
  (official audio) needs no search; a music video or someone's upload is searched for,
  to get the song itself.

- `spotify`: one of the owner's own Spotify playlists, or their Liked Songs, read
  through `musicorg.spotify` once they've signed in. Its songs arrive as a title,
  artists and a length, and are searched for.

- `deezer`: a public Deezer playlist or album, by its link (`musicorg.deezer`). No
  sign-in. Its songs arrive as a title, the main artist and a length, and are searched
  for.

- `lastfm`: the owner's Loved Tracks or most played on Last.fm (`musicorg.lastfm`),
  once their username and API key are saved. A title and an artist, searched for.

- `file`: a playlist saved as a file (`musicorg.playlistfile`): the way in for Amazon
  Music, which can only be exported through a service such as TuneMyMusic. A CSV, a
  text file of "Artist - Title" lines, or an M3U playlist, opened read-only.

Apple Music comes next, through the same `find()`.

How a song is found: `match.match_item`, the rule the owner's rips are matched by. Only
its confident answer (the same artist, title and version, official audio, the length
within 2 seconds) is `found`; a likely one is `unsure` and is only downloaded if the
owner ticks it. A music video's length says nothing about the song's, so for one of
those the same artist, title and version is enough. The same goes for a song whose
playlist gives no length at all (a text file, a table with no length column, Last.fm):
there's nothing to compare, and without this every such song would be `unsure`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from musicorg import (
    browse,
    deezer,
    discover,
    lastfm,
    match,
    playlistfile,
    queue,
    spotify,
    youtube,
)
from musicorg.errors import UserError
from musicorg.index import Index
from musicorg.library import Library
from musicorg.normalize import parse_tags
from musicorg.tags import TrackTags
from musicorg.youtube import Candidate

log = logging.getLogger(__name__)

SOURCES = ("youtube", "spotify", "deezer", "lastfm", "file")
STATES = ("owned", "queued", "found", "unsure", "not_found")
FIND_AT_ONCE = 100  # songs in one `find()`: each may cost a search or two
MUSIC_VIDEO = "MUSIC_VIDEO_TYPE_OMV"
YOUTUBE_HOSTS = frozenset(
    {"music.youtube.com", "www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be"}
)
# Lists YouTube keeps per account, which nobody else can open.
PRIVATE_LISTS = frozenset({"LM", "LL", "WL"})

Progress = Callable[[int, int], None]  # songs looked at so far, and how many there are


# ---- reading a playlist ------------------------------------------------------------------


def youtube_playlist_id(link: str) -> str:
    """The playlist a link points at: "https://music.youtube.com/playlist?list=PL…", the
    same on youtube.com, a song's link that has `&list=…` in it, or the id by itself."""
    text = link.strip()
    if not text:
        raise UserError("Paste a playlist's link first.")
    if "/" in text or "?" in text:
        url = urlsplit(text if "://" in text else "https://" + text)
        if (url.hostname or "").lower() not in YOUTUBE_HOSTS:
            raise UserError("That isn't a YouTube or YouTube Music link.")
        ids = parse_qs(url.query).get("list") or []
        if not ids and "/browse/" in url.path:
            ids = [url.path.rsplit("/", 1)[-1]]
        if not ids:
            raise UserError(
                "That link is to a song or a page, not a playlist. Open the playlist, then "
                "Share → Copy link."
            )
        text = ids[0]
    text = text.removeprefix("VL")  # the id as YouTube Music's own pages write it
    if text in PRIVATE_LISTS:
        raise UserError(
            "That's one of your account's own lists (Liked Music, Watch Later), which only "
            "you can open, signed in. Put its songs in a playlist, set that to Unlisted, "
            "and paste its link."
        )
    if not youtube.PLAYLIST_ID.fullmatch(text):
        raise UserError("That doesn't look like a playlist's link.")
    return text


def from_youtube(link: str) -> dict[str, Any]:
    """A YouTube or YouTube Music playlist as an import: its name and its songs, each
    carrying the YouTube track it is. One request for every hundred songs."""
    found = youtube.playlist(youtube_playlist_id(link))
    return {
        "source": "youtube",
        "name": found.title,
        "tracks": [
            {
                "title": track.title,
                "artists": list(track.artists),
                "album": track.album,
                "duration_s": track.duration_s,
                "is_explicit": track.is_explicit,
                "candidate": track.to_dict(),
            }
            for track in found.tracks
        ],
    }


def spotify_playlists() -> list[dict[str, Any]]:
    """The owner's Spotify playlists, Liked Songs first, for the app to choose from."""
    return spotify.playlists()


def from_spotify(playlist_id: str) -> dict[str, Any]:
    """One of the owner's Spotify playlists (or `liked`) as an import: its name and its
    songs as Spotify names them. A request for every fifty songs."""
    found = spotify.playlist(playlist_id)
    return {
        "source": "spotify",
        "name": found["name"],
        "tracks": found["tracks"],
        "more": found["more"],
    }


def from_deezer(link: str) -> dict[str, Any]:
    """A public Deezer playlist or album as an import, by its link. A request for its
    name and one for every hundred songs; nothing is signed in to."""
    found = deezer.playlist(link)
    return {"source": "deezer", **found}


def from_lastfm(list_id: str) -> dict[str, Any]:
    """The owner's Loved Tracks or most played on Last.fm as an import (`lastfm.LISTS`).
    A request for every two hundred songs, up to a thousand."""
    found = lastfm.playlist(list_id)
    tracks = [{k: v for k, v in track.items() if k != "plays"} for track in found["tracks"]]
    return {"source": "lastfm", "name": found["name"], "tracks": tracks, "more": found["more"]}


def from_file(path: str, playlist: str | None = None) -> dict[str, Any]:
    """A playlist saved as a file (an export from Amazon Music, say) as an import. The
    file is only read. `playlists` names the playlists in a file that holds several;
    `playlist` chooses one of them."""
    if not path.strip() or not Path(path).is_absolute():
        raise UserError("Choose the playlist's file first.")
    found = playlistfile.read(Path(path), playlist)
    return {"source": "file", **found}


# ---- finding the songs -------------------------------------------------------------------


def find(
    lib: Library,
    index: Index,
    tracks: list[dict[str, Any]],
    *,
    progress: Progress | None = None,
) -> list[dict[str, Any]]:
    """What each imported song is to the owner, in the order given. Each answer has a
    `state` (docs/ENGINE_API.md → "Import track state") and, by state:

    - `owned`: `track_id`, the owner's own copy (None for a copy with no id yet)
    - `queued`: `candidate`, already waiting in the download queue
    - `found`: `candidate`, the song on YouTube Music, to download
    - `unsure`: `candidate`, the likeliest song, and `why` it isn't certain
    - `not_found`: nothing

    A song the owner has costs no search, and nor does a YouTube playlist's entry that
    is official audio. Searches go through `youtube` and its limiter, and are kept in
    the index's search cache, so asking again is quick."""
    if len(tracks) > FIND_AT_ONCE:
        raise UserError(f"That's too many songs at once (the limit is {FIND_AT_ONCE}).")
    rows = [row for row in index.library_tracks() if discover.is_there(lib, row)]
    owned = discover.Owned(rows, lambda: browse.typed_titles(lib))
    waiting = {
        row["video_id"] for row in queue.downloads(lib.paths) if isinstance(row["video_id"], str)
    }
    answers = []
    for done, raw in enumerate(tracks):
        answers.append(_find_one(_track(raw), owned, waiting, index))
        if progress is not None:
            progress(done + 1, len(tracks))
    return answers


def _track(raw: Any) -> dict[str, Any]:
    """One imported song, checked: a title, artists, and what else is known."""
    if not isinstance(raw, dict) or not isinstance(raw.get("title"), str) or not raw["title"]:
        raise UserError("An imported song needs at least a title.")
    artists = raw.get("artists")
    if not isinstance(artists, list) or not all(isinstance(name, str) for name in artists):
        raise UserError("An imported song's artists should be a list of names.")
    duration = raw.get("duration_s")
    explicit = raw.get("is_explicit")
    candidate = None
    if raw.get("candidate") is not None:
        try:
            candidate = Candidate.from_dict(raw["candidate"])
        except (KeyError, TypeError, AttributeError):
            raise UserError("A candidate should be a track as the engine gave it.") from None
        if not isinstance(candidate.video_id, str) or not isinstance(candidate.title, str):
            raise UserError("A candidate should be a track as the engine gave it.")
    return {
        "title": raw["title"],
        "artists": tuple(name for name in artists if name),
        "duration_s": duration
        if isinstance(duration, (int, float)) and not isinstance(duration, bool)
        else None,
        "is_explicit": explicit if isinstance(explicit, bool) else None,
        "candidate": candidate,
    }


def _find_one(
    track: dict[str, Any], owned: discover.Owned, waiting: set[str], index: Index
) -> dict[str, Any]:
    given: Candidate | None = track["candidate"]
    # The song as named where it came from: enough to know whether the owner has it.
    named = given or Candidate(video_id="", title=track["title"], artists=track["artists"])
    if owned.has(named):
        return {"state": "owned", "track_id": owned.track_id(named)}
    if given is not None and given.is_official_audio:
        return _placed(given, waiting, "found")

    video = given is not None and given.video_type == MUSIC_VIDEO
    # No length to go by: the video's is another cut's, or the playlist gave none.
    no_length = video or track["duration_s"] is None
    rip = match.Rip(
        parse_tags(TrackTags(title=track["title"], artist=", ".join(track["artists"]) or None)),
        # A video is a different cut: its length would only mislead.
        None if video else track["duration_s"],
        track["is_explicit"],
    )
    outcome = match.match_item(rip, cache=index)
    best = outcome.top[0] if outcome.top else None
    if best is None or outcome.state == "not_found":
        return {"state": "not_found"}
    if owned.has(best.candidate):  # under another name than the playlist's
        return {"state": "owned", "track_id": owned.track_id(best.candidate)}
    if outcome.state == "matched_auto" or (no_length and best.same_song):
        return _placed(best.candidate, waiting, "found")
    answer = _placed(best.candidate, waiting, "unsure")
    if answer["state"] == "unsure":
        answer["why"] = _why(best, no_length)
    return answer


def _placed(candidate: Candidate, waiting: set[str], state: str) -> dict[str, Any]:
    if candidate.video_id in waiting:
        state = "queued"
    return {"state": state, "candidate": candidate.to_dict()}


_WHY = {
    "artist_mismatch": "the artist is named differently",
    "title_fuzzy": "the title isn't quite the same",
    "version_mismatch": "it may be another version",
    "duration_mismatch": "the length is different",
    "not_official_audio": "it isn't YouTube Music's official audio",
}


def _why(scored: match.Scored, no_length: bool = False) -> str:
    """One plain line on why the likeliest song isn't a certain one. With no length to
    go by, the length isn't the reason."""
    reasons = [
        _WHY[code]
        for code in scored.codes
        if code in _WHY and not (no_length and code == "duration_mismatch")
    ]
    return (reasons[0] if reasons else "it isn't a certain match").capitalize() + "."
