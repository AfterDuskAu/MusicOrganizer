"""The Artist page (Discover, started 2026-10-03 at the owner's request): who an artist
is, their best-known songs, their albums and singles, and the artists their listeners
also play, with which of the songs the owner already has.

Everything comes from YouTube Music's own page for the artist, through `musicorg.youtube`
and its rate limiter (rule 8), and is kept in the index's search cache for a week.
Nothing is downloaded and nothing in the library changes: the songs are for the app to
show, play and, when the owner asks, download through the usual plan and queue
(`plan.create` kind `download`, handing the songs back as `candidates`).

- `search()`: the artists YouTube Music finds for what was typed, with their pictures.
  One request, kept for 30 days.
- `info()`: the page. By the artist's name (found first: a search, kept for 30 days) or
  by their id (a related artist's, which needs no search). Two requests at most.
- `songs()`: every song of theirs, from the playlist the page points at. A request for
  every hundred songs, up to three hundred. Never cached, like any playlist.
- `album()`: one album's or single's songs. One request, kept for 30 days.

A song is the owner's (`owned`) by the rule Discover and imports use (`discover.Owned`):
its YouTube id, or the same title, version and artist. Not by title alone.

Concerts aren't here yet: they'd come from a ticket seller's API with a key of the
owner's own (docs/ROADMAP.md).
"""

from __future__ import annotations

import logging
from typing import Any

from musicorg import browse, discover, youtube
from musicorg.errors import UserError
from musicorg.index import Index
from musicorg.library import Library
from musicorg.youtube import Candidate

log = logging.getLogger(__name__)

SONGS_MOST = 300  # of an artist's songs read at once (three requests)


def _owned(lib: Library, index: Index) -> discover.Owned:
    rows = [row for row in index.library_tracks() if discover.is_there(lib, row)]
    return discover.Owned(rows, lambda: browse.typed_titles(lib))


def _songs(found: list[Candidate] | tuple[Candidate, ...], owned: discover.Owned) -> list[Any]:
    return [{**song.to_dict(), "owned": owned.has(song)} for song in found]


def search(index: Index, query: str) -> dict[str, Any]:
    """The artists YouTube Music finds for `query`, best first: `{artists: [{artist_id,
    name, monthly_audience, thumbnail}]}` (the search gives no audience, so that's
    null). Empty when it finds none. `index` must be writable: the answer is kept."""
    wanted = query.strip()
    if not wanted:
        raise UserError("Type an artist's name first.")
    return {"artists": [found.to_dict() for found in youtube.search_artists(wanted, cache=index)]}


def info(
    lib: Library, index: Index, *, name: str | None = None, artist_id: str | None = None
) -> dict[str, Any]:
    """An artist's page: `{found, name, artist_id, description, subscribers,
    monthly_audience, views, thumbnail, songs, songs_playlist_id, albums, singles,
    related, owned_songs}` (docs/ENGINE_API.md → `artist.info`), or `{found: false,
    name}` when YouTube Music finds no artist for the name. `index` must be writable:
    YouTube Music's answers are kept in it."""
    if artist_id is None:
        wanted = (name or "").strip()
        if not wanted:
            raise UserError("Type an artist's name first.")
        found = youtube.find_artist(wanted, cache=index)
        if found is None:
            return {"found": False, "name": wanted}
        artist_id = found[0]
    page = youtube.artist_page(artist_id, cache=index)
    owned = _owned(lib, index)
    return {
        "found": True,
        "artist_id": page.artist_id,
        "name": page.name,
        "description": page.description,
        "subscribers": page.subscribers,
        "monthly_audience": page.monthly_audience,
        "views": page.views,
        "thumbnail": page.thumbnail,
        "songs": _songs(page.songs, owned),
        "songs_playlist_id": page.songs_playlist_id,
        "albums": [release.to_dict() for release in page.albums],
        "singles": [release.to_dict() for release in page.singles],
        "related": [artist.to_dict() for artist in page.related],
        # How many songs credited to them the owner has, for "You have 31 of their songs".
        "owned_songs": owned.songs_by(page.name),
    }


def songs(lib: Library, index: Index, playlist_id: str) -> dict[str, Any]:
    """Every song of an artist's, from the playlist their page points at
    (`songs_playlist_id`): `{songs, more}`. `more` says there are more than were read."""
    found = youtube.playlist(playlist_id.removeprefix("VL"), limit=SONGS_MOST)
    listed = _songs(found.tracks[:SONGS_MOST], _owned(lib, index))
    return {"songs": listed, "more": len(found.tracks) >= SONGS_MOST}


def album(lib: Library, index: Index, browse_id: str) -> dict[str, Any]:
    """One album's, EP's or single's songs, in its order: `{browse_id, title, year,
    artists, thumbnail, songs}`. A track YouTube Music lists without an id (one that
    can't be played) is left out."""
    if not youtube.PLAYLIST_ID.fullmatch(browse_id):
        raise UserError("That isn't an album's id.")
    found = youtube.get_album(browse_id, cache=index)
    cover = found.thumbnails[-1][0] if found.thumbnails else None
    tracks = [
        Candidate(
            video_id=track.video_id,
            title=track.title,
            artists=found.artists,
            album=found.title,
            album_browse_id=browse_id,
            duration_s=track.duration_s,
            is_explicit=track.is_explicit,
            video_type=track.video_type,
            year=found.year,
            thumbnail=cover,
        )
        for track in found.tracks
        if track.video_id
    ]
    return {
        "browse_id": browse_id,
        "title": found.title,
        "year": found.year,
        "artists": list(found.artists),
        "thumbnail": cover,
        "songs": _songs(tracks, _owned(lib, index)),
    }
