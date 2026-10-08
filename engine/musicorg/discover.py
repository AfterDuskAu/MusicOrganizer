"""Discover (v0.4): songs the owner doesn't have yet, found from songs they do have.

`suggest()` takes one or more **seeds** (where to start from) and a count, and returns
that many picks, best first, each with a one-line "why". Nothing is downloaded and
nothing in the library changes: the picks are for the app to show, play and, when the
owner asks, download through the usual plan and queue (`plan.create` kind `download`).

Where the picks come from (all through `musicorg.youtube` and its rate limiter, rule 8):

- a song's **radio** on YouTube Music: about 50 songs like it. A seed made of the
  owner's songs (the library, most played, a playlist) starts a radio from several of
  them.
- an artist's radio: the artist's own songs and other artists' that their listeners
  play. More is wanted than one radio holds → radios of the artist's songs follow.
- a genre: the owner's own songs tagged with it are where the radios start; with too
  few of those, YouTube Music's own playlist for the genre gives the starting songs.
- words the owner typed (the guided "What music would you like today?"): worked out
  to be a genre or an artist (`_typed`), then as above.
- the owner's most played songs on Last.fm (`musicorg.lastfm`, once it's set up): the
  radios start from those, so what they've listened to elsewhere counts too. A song
  of those the owner doesn't have is itself a pick.

How they're ranked:

- a song found on the radios of several starting songs comes before one found once;
- then songs by artists the owner already has a lot of;
- then the order YouTube Music gave them in.

Never suggested: a song already in the library (by its YouTube id, or by title, version
and artist: not by title alone), a candidate the owner rejected in review, a song
waiting in the download queue, the same song twice, and anything that isn't YouTube
Music's official audio (a music video is a different cut, and downloads keep format 140
of the official track).

Answers from YouTube Music are kept in the index's search cache (a radio for a week), so
asking again is quick and gives the same picks. `shuffle` decides which of the owner's
songs the radios start from: the same word gives the same starting songs.
"""

from __future__ import annotations

import json
import logging
import math
import random
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from musicorg import browse, lastfm, listening, naming, queue, state, youtube
from musicorg.errors import NotFoundError, UserError, YouTubeError
from musicorg.index import Index
from musicorg.library import Library
from musicorg.normalize import SOFT_VERSION_KINDS, compare_key, parse_filename, parse_title
from musicorg.youtube import Candidate

log = logging.getLogger(__name__)

SEED_KINDS = (
    "library", "most_played", "top_artist", "playlist", "artist", "genre", "typed", "lastfm",
)  # fmt: skip
MAX_COUNT = 500
MAX_SEEDS = 8
MIN_RADIOS = 4  # fewer starting points and "found on several radios" means nothing
MAX_RADIOS = 24  # about 36 seconds of asking at the limiter's pace
SONGS_PER_RADIO = 25  # new songs a radio usually adds, for saying how far along it is
POOL = 3  # stop asking once there are this many times the songs wanted to choose from
MAX_LOOKUPS = 8  # searches for starting songs whose YouTube id isn't known
# A Last.fm seed's songs are all known by name only, so it may search for more of them.
MAX_LOOKUPS_LASTFM = 16
LASTFM_PERIOD = "6month"  # "most played" means lately; with too little there, of all time
LASTFM_SONGS = 50
ARTIST_BOOST_SONGS = 10  # owning more of an artist than this adds nothing further

# Genre tags that mean the same thing, as `compare_key` spells them.
SAME_GENRE = (
    frozenset({"hip hop", "rap"}),
    frozenset({"r and b", "r b", "rnb"}),
    frozenset({"electronic", "electronica"}),
)

# What kinds of music are called, as `compare_key` spells them: typed words that are one
# of these are a genre without asking (there are artists called "Jazz" and "Pop").
GENRE_NAMES = frozenset(
    compare_key(name)
    for name in (
        "hip hop", "hiphop", "rap", "trap", "drill", "grime", "r&b", "rnb", "soul", "funk",
        "pop", "k-pop", "kpop", "j-pop", "rock", "hard rock", "classic rock", "indie",
        "indie rock", "alternative", "alt rock", "punk", "emo", "grunge", "metal",
        "heavy metal", "electronic", "electronica", "dance", "edm", "house", "techno",
        "trance", "dubstep", "drum and bass", "ambient", "lo-fi", "lofi", "chill",
        "reggae", "dancehall", "ska", "latin", "reggaeton", "afrobeats", "african",
        "folk", "acoustic", "country", "americana", "bluegrass", "jazz", "blues", "gospel",
        "christian", "classical", "opera", "soundtrack", "soundtracks", "disco", "bollywood",
    )
)  # fmt: skip

Progress = Callable[[int, int], None]


@dataclass(frozen=True)
class Seed:
    """Where picks start from. `value` is a playlist's id, an artist's name, a genre, or
    (`typed`) whatever the owner typed when asked what music they'd like; the other
    kinds have none."""

    kind: str
    value: str | None = None

    @classmethod
    def from_dict(cls, data: object) -> Seed:
        if not isinstance(data, dict) or data.get("kind") not in SEED_KINDS:
            raise UserError(f"A seed's kind should be one of: {', '.join(SEED_KINDS)}.")
        kind = data["kind"]
        value = data.get("playlist_id") if kind == "playlist" else data.get("name")
        if kind in ("playlist", "artist", "genre", "typed"):
            if not isinstance(value, str) or not value.strip():
                what = "a playlist_id" if kind == "playlist" else "a name"
                raise UserError(f"A {kind} seed needs {what}.")
            return cls(kind, value.strip())
        return cls(kind)


@dataclass
class _Start:
    """One place a radio starts from: a song (its id, or a title and artist to find it
    by) or an artist."""

    title: str = ""
    artist: str = ""
    video_id: str | None = None
    artist_radio: bool = False
    listed_on: str | None = None  # the YouTube Music playlist it was taken from
    genre: str | None = None  # the owner's genre tag on the song a radio starts from


@dataclass
class _Source:
    """One seed, worked out: its name for the "why" lines and the starts still to try."""

    kind: str
    label: str
    starts: list[_Start] = field(default_factory=list)
    used: int = 0
    genre: str | None = None  # the genre that was asked for, as the owner spells it


@dataclass
class _Found:
    candidate: Candidate
    order: int  # the order it first turned up in: the last tie-break
    hits: dict[int, tuple[_Source, _Start, int]] = field(default_factory=dict)  # by start

    @property
    def best_position(self) -> int:
        return min(position for _, _, position in self.hits.values())


class Owned:
    """What the owner has, for "is this pick already mine?" and "how much of this artist
    do I have?"."""

    def __init__(
        self,
        rows: list[dict[str, Any]],
        typed: Callable[[], dict[str, set[str]]] | None = None,
    ) -> None:
        """`rows` are the index's library tracks. `typed` gives the titles the owner
        typed in Edit Details (`browse.typed_titles`); it's asked at most once, and only
        if a copy would otherwise be read by its rip's name for a version."""
        self.ids: set[str] = set()
        typed_titles: dict[str, set[str]] | None = None
        # By title: the version, the artists credited, and the song's own id.
        self.songs: dict[str, list[tuple[frozenset[str], str, str | None]]] = {}
        self.credits: list[str] = []
        self._counts: dict[str, int] = {}
        self._by_source: dict[str, str | None] = {}
        for row in rows:
            track_id = row.get("musicorg_id") if isinstance(row.get("musicorg_id"), str) else None
            if isinstance(row.get("source_id"), str) and row["source_id"]:
                self.ids.add(row["source_id"])
            if naming.is_video_path(row["rel_path"]):
                continue  # a saved video isn't the song
            if isinstance(row.get("source_id"), str) and row["source_id"]:
                self._by_source[row["source_id"]] = track_id
            credit = f" {youtube.artist_key(row.get('artist') or '')} "
            self.credits.append(credit)
            # A title the owner named is read with their mark for a remix: owning
            # "Melody R" is owning a remix of "Melody", not "Melody" itself.
            match = browse.row_match(row)
            owner = browse.owner_named(row.get("source"), match)
            named = self._add(row.get("title") or "", credit, (), track_id, owner=owner)
            # A copy not identified yet, whose title names no version, can carry a plain
            # title while the rip it came from is named "Song R" (a remix) or "Artist -
            # Song": its name counts too. (A title that names no version has no version
            # tag either: the tag is written from the title.) Once the copy is found or
            # the owner has named it, the rip's old name says nothing more about it. That
            # goes for a title the owner typed in Edit Details too: taking the R off by
            # hand says the song isn't a remix.
            origin = row.get("origin_path")
            if match == browse.UNCONFIRMED and not named and isinstance(origin, str) and origin:
                parsed = parse_filename(browse.rip_stem(origin))
                theirs = False  # the title is one the owner typed
                if parsed.version_tokens and typed is not None:
                    if typed_titles is None:
                        typed_titles = typed()
                    theirs = browse.typed_by_owner(typed_titles, track_id, row.get("title"))
                if parsed.title and not theirs:
                    by = f" {youtube.artist_key(parsed.artist or '')} "
                    self._add(parsed.title, credit if by.strip() == "" else by,
                              parsed.version_tokens, track_id)  # fmt: skip

    def _add(
        self,
        title: str,
        credit: str,
        versions: tuple[str, ...],
        track_id: str | None,
        *,
        owner: bool = False,
    ) -> tuple[str, ...]:
        """Note one of the owner's songs. Returns the versions its title names."""
        parsed = browse.read_title(title, owner=owner)
        key = compare_key(parsed.title)
        if key:
            hard = _hard(parsed.version_tokens + tuple(versions))
            self.songs.setdefault(key, []).append((hard, credit, track_id))
        return parsed.version_tokens

    def has(self, candidate: Candidate) -> bool:
        return candidate.video_id in self.ids or self._song(candidate) is not None

    def track_id(self, candidate: Candidate) -> str | None:
        """The owner's own copy of this song (its `MUSICORG_ID`), if they have the song
        itself: by its YouTube id, or by title, version and artist. A saved video of it
        doesn't count, and nor does a copy with no id."""
        if candidate.video_id in self._by_source:
            return self._by_source[candidate.video_id]
        found = self._song(candidate)
        return found[2] if found else None

    def _song(self, candidate: Candidate) -> tuple[frozenset[str], str, str | None] | None:
        parsed = parse_title(candidate.title)
        versions = _hard(parsed.version_tokens)
        names = [youtube.artist_key(name) for name in candidate.artists]
        for entry in self.songs.get(compare_key(parsed.title), []):
            hard, credit, _ = entry
            if hard == versions and any(name and f" {name} " in credit for name in names):
                return entry
        return None

    def songs_by(self, artist: str) -> int:
        """How many of the owner's songs credit this artist."""
        key = youtube.artist_key(artist)
        if not key:
            return 0
        if key not in self._counts:
            self._counts[key] = sum(f" {key} " in credit for credit in self.credits)
        return self._counts[key]


def suggest(
    lib: Library,
    index: Index,
    seeds: list[Seed],
    count: int,
    *,
    shuffle: str = "",
    exclude: Iterable[str] = (),
    progress: Progress | None = None,
) -> dict[str, Any]:
    """`count` picks from these seeds, best first: `{picks, wanted, radios, seeds,
    note}` (docs/ENGINE_API.md → `discover.suggest`). `index` must be writable: YouTube
    Music's answers are kept in it.

    `exclude`: YouTube ids already on the owner's screen (the app's Show More): none of
    them is picked again, so the radios are walked further for songs that are new."""
    if not seeds:
        raise UserError("Choose where to start from: your library, a playlist, an artist…")
    if len(seeds) > MAX_SEEDS:
        raise UserError(f"That's too many starting points at once (the limit is {MAX_SEEDS}).")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_COUNT:
        raise UserError(f"How many songs should be 1 to {MAX_COUNT}.")

    rows = [row for row in index.library_tracks() if is_there(lib, row)]
    owned = Owned(rows, lambda: browse.typed_titles(lib))
    heard = listening.get(lib)
    skip_ids = set().union(*state.rejected(lib.load_state().data).values())
    skip_ids |= {
        row["video_id"] for row in queue.downloads(lib.paths) if isinstance(row["video_id"], str)
    }
    skip_ids |= {video_id for video_id in exclude if isinstance(video_id, str)}
    rng = random.Random(f"discover {shuffle}")
    notes: list[str] = []
    sources = []
    for seed in dict.fromkeys(seeds):
        source = _source(seed, rows, heard, index, rng, notes)
        if source is not None:
            sources.append(source)
    if not any(source.starts for source in sources):
        raise UserError(notes[0] if notes else "There's nothing to start from yet.")

    wanted_radios = min(MAX_RADIOS, max(MIN_RADIOS, math.ceil(count * POOL / SONGS_PER_RADIO)))
    found: dict[tuple[str, frozenset[str], str], _Found] = {}
    radios = lookups = 0
    while radios < MAX_RADIOS and any(source.starts for source in sources):
        if radios >= MIN_RADIOS and len(found) >= count * POOL:
            break
        for source in sources:  # in turn, so several seeds share the radios
            if not source.starts or radios >= MAX_RADIOS:
                continue
            start = source.starts.pop(0)
            if not start.artist_radio and start.video_id is None:
                if lookups >= (MAX_LOOKUPS_LASTFM if source.kind == "lastfm" else MAX_LOOKUPS):
                    continue
                lookups += 1
                start.video_id = _look_up(start, index)
                if start.video_id is None:
                    continue
            tracks = _radio(start, source, index, notes)
            if tracks is None:
                continue
            radios += 1
            source.used += 1
            if progress is not None:
                progress(radios, max(wanted_radios, radios))
            for position, candidate in enumerate(tracks):
                if (
                    not candidate.is_official_audio
                    or candidate.video_id in skip_ids
                    or owned.has(candidate)
                ):
                    continue
                key = _song_key(candidate)
                entry = found.setdefault(key, _Found(candidate, len(found)))
                entry.hits.setdefault(id(start), (source, start, position))

    if radios == 0:
        raise UserError(
            " ".join(notes) or "None of the starting songs could be found on YouTube Music."
        )
    ranked = sorted(found.values(), key=lambda entry: (-_score(entry, owned), entry.order))
    named = {youtube.artist_key(s.label) for s in sources if s.kind == "artist"}
    picks = _spread(ranked, count, named)
    if len(picks) < count:
        notes.append(
            f"Found {len(picks)} new {'song' if len(picks) == 1 else 'songs'}, not {count}: "
            "that's all these radios had that you don't have already."
        )
    return {
        "picks": [
            {
                **entry.candidate.to_dict(),
                "why": _why(entry, owned),
                "hits": len(entry.hits),
                "genre": _pick_genre(entry),
            }
            for entry in picks
        ],
        "wanted": count,
        "radios": radios,
        "seeds": [{"kind": s.kind, "label": s.label, "radios": s.used} for s in sources],
        "note": " ".join(notes) or None,
    }


# ---- covers and remixes of the owner's songs, and playlists around them (2026-10-08) --------

REMIX_KINDS = frozenset({"remix", "bootleg", "flip", "vip"})
COVER_KINDS = frozenset({"cover"})
MAX_REMIX_SEARCHES = 16  # two a song: its remixes, then its covers
MAX_PLAYLIST_SEARCHES = 6
PLAYLISTS_A_SONG = 4  # so one song's playlists don't fill the page
MAX_PLAYLISTS = 48


def _listened(
    rows: list[dict[str, Any]], heard: dict[str, Any], rng: random.Random
) -> list[dict[str, Any]]:
    """The owner's songs to start from, in a shuffled order that leans towards what
    they've been playing: the last few played most of all, then the most played and
    favourites. One song from each artist before a second from any."""
    songs = [r for r in rows if not naming.is_video_path(r["rel_path"]) and r.get("title")]
    plays = heard["plays"]
    favourites = set(heard["favourites"])
    lately = sorted(
        (t for t, p in plays.items() if isinstance(p.get("last_played"), str)),
        key=lambda t: plays[t]["last_played"],
        reverse=True,
    )[:20]

    def weight(row: dict[str, Any]) -> float:
        track_id = row.get("musicorg_id")
        return (
            1.0
            + (6.0 if track_id in lately else 0.0)
            + (2.0 if track_id in favourites else 0.0)
            + min(plays.get(track_id, {}).get("count", 0), 5)
        )

    return _shuffled(songs, weight, rng)


def remixes(
    lib: Library,
    index: Index,
    count: int,
    *,
    shuffle: str = "",
    exclude: Iterable[str] = (),
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Remixes and covers of songs the owner has, that they don't have: the answer is
    shaped as `suggest`'s (`{picks, wanted, radios, seeds, note}`; `radios` counts the
    searches made).

    For each starting song YouTube Music's song search is asked for "<title> <artist>
    remix" and "<title> cover". A result is taken only if its title is the same song's
    and names a remix (or bootleg, flip, VIP) or a cover: the same title by someone else
    with no such word may be a different song altogether, and is left. Only official
    audio, as with every pick. `shuffle` decides which songs are started from, leaning
    to what's been played lately; the searches are kept in the index for 30 days."""
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_COUNT:
        raise UserError(f"How many songs should be 1 to {MAX_COUNT}.")
    rows = [row for row in index.library_tracks() if is_there(lib, row)]
    owned = Owned(rows, lambda: browse.typed_titles(lib))
    heard = listening.get(lib)
    skip_ids = set().union(*state.rejected(lib.load_state().data).values())
    skip_ids |= {
        row["video_id"] for row in queue.downloads(lib.paths) if isinstance(row["video_id"], str)
    }
    skip_ids |= {video_id for video_id in exclude if isinstance(video_id, str)}
    starts = _listened(rows, heard, random.Random(f"remixes {shuffle}"))
    if not starts:
        raise UserError("The library has no songs yet, so there's nothing to find remixes of.")

    picks: list[dict[str, Any]] = []
    seen: set[tuple[str, frozenset[str], str]] = set()
    done: set[str] = set()
    searches = 0
    notes: list[str] = []
    for row in starts:
        if len(picks) >= count or searches >= MAX_REMIX_SEARCHES:
            break
        title = browse.read_title(row["title"], owner=False).title or row["title"]
        key = compare_key(title)
        artist = row.get("artist") or ""
        if not key or key in done:
            continue
        done.add(key)
        for words, kinds, said in (
            (f"{title} {artist} remix".strip(), REMIX_KINDS, "A remix of"),
            (f"{title} cover", COVER_KINDS, "A cover of"),
        ):
            if searches >= MAX_REMIX_SEARCHES:
                break
            try:
                found = youtube.search_songs(words, limit=20, cache=index)
            except _skippable() as exc:
                log.info("remixes: the search for %r failed: %s", words, exc)
                notes.append("Some searches didn't answer, so there may be more to find.")
                continue
            finally:
                searches += 1
                if progress is not None:
                    progress(searches, MAX_REMIX_SEARCHES)
            for candidate in found:
                parsed = parse_title(candidate.title)
                named = {t.partition(":")[0] for t in parsed.version_tokens}
                if compare_key(parsed.title) != key or not named & kinds:
                    continue
                song = _song_key(candidate)
                again = song in seen
                seen.add(song)  # one already on the page counts: no second upload of it
                if (
                    again
                    or not candidate.is_official_audio
                    or candidate.video_id in skip_ids
                    or owned.has(candidate)
                ):
                    continue
                by = f" by {artist}" if artist else ""
                picks.append(
                    {
                        **candidate.to_dict(),
                        "why": f"{said} “{title}”{by}",
                        "hits": 1,
                        "genre": _genre_tag(row) or None,
                    }
                )
    if searches and not picks:
        notes.append("No remixes or covers were found for these songs. Try Different Songs.")
    return {
        "picks": picks[:count],
        "wanted": count,
        "radios": searches,
        "seeds": [],
        "note": " ".join(dict.fromkeys(notes)) or None,
    }


def playlists(
    lib: Library,
    index: Index,
    count: int = 24,
    *,
    shuffle: str = "",
    exclude: Iterable[str] = (),
) -> dict[str, Any]:
    """Playlists on YouTube Music around what the owner has been playing: `{playlists:
    [{playlist_id, title, author, thumbnail, why}], note}`.

    YouTube Music has no way to ask "which playlists hold this song", so each starting
    song's artist and title are searched for among playlists: what comes back is about
    them, and usually has the song, but that isn't checked (reading every playlist would
    be a request each). A few from each song, in turn, so the page is a mix. `shuffle`
    decides the starting songs, leaning to the ones played lately, so the page moves on
    as the owner listens; `exclude` leaves out playlists already shown."""
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_PLAYLISTS:
        raise UserError(f"How many playlists should be 1 to {MAX_PLAYLISTS}.")
    rows = [row for row in index.library_tracks() if is_there(lib, row)]
    starts = _listened(rows, listening.get(lib), random.Random(f"playlists {shuffle}"))
    if not starts:
        raise UserError("The library has no songs yet, so there's nothing to find playlists for.")
    shown = {p for p in exclude if isinstance(p, str)}
    per_song: list[list[dict[str, Any]]] = []
    notes: list[str] = []
    for row in starts[:MAX_PLAYLIST_SEARCHES]:
        title = browse.read_title(row["title"], owner=False).title or row["title"]
        artist = row.get("artist") or ""
        try:
            found = youtube.search_playlists(f"{artist} {title}".strip(), cache=index)
        except _skippable() as exc:
            log.info("playlists: the search for %r failed: %s", title, exc)
            notes.append("Some searches didn't answer, so there may be more to find.")
            continue
        by = f" by {artist}" if artist else ""
        mine = []
        for entry in found:
            if entry["playlist_id"] in shown or len(mine) >= PLAYLISTS_A_SONG:
                continue
            shown.add(entry["playlist_id"])
            mine.append({**entry, "why": f"For “{title}”{by}"})
        per_song.append(mine)
    mixed = []
    while any(per_song) and len(mixed) < count:  # one from each song in turn
        for mine in per_song:
            if mine and len(mixed) < count:
                mixed.append(mine.pop(0))
    if not mixed:
        notes.append("No playlists were found for these songs. Try Different Playlists.")
    return {"playlists": mixed, "note": " ".join(dict.fromkeys(notes)) or None}


# ---- seeds → where the radios start ------------------------------------------------------


def _source(
    seed: Seed,
    rows: list[dict[str, Any]],
    heard: dict[str, Any],
    index: Index,
    rng: random.Random,
    notes: list[str],
) -> _Source | None:
    songs = [row for row in rows if not naming.is_video_path(row["rel_path"])]
    by_id = {row["musicorg_id"]: row for row in songs if row.get("musicorg_id")}
    plays = heard["plays"]
    favourites = set(heard["favourites"])

    def weight(row: dict[str, Any]) -> float:
        track_id = row.get("musicorg_id")
        return (
            1.0
            + (2.0 if track_id in favourites else 0.0)
            + min(plays.get(track_id, {}).get("count", 0), 5)
        )

    if seed.kind == "library":
        if not songs:
            notes.append("The library has no songs yet, so there's nothing to start from.")
            return None
        known = [row for row in songs if _video_id(row)]
        # Songs YouTube Music is already known to have need no search first.
        pool = known if len(known) >= MIN_RADIOS else songs
        return _Source("library", "your library", _starts(_shuffled(pool, weight, rng)))
    if seed.kind == "most_played":
        played = [row for row in songs if plays.get(row.get("musicorg_id"), {}).get("count", 0) > 0]
        # The most played first; of two played as often, the one heard more recently.
        played.sort(
            key=lambda row: plays[row["musicorg_id"]].get("last_played") or "", reverse=True
        )
        played.sort(key=lambda row: plays[row["musicorg_id"]]["count"], reverse=True)
        if not played:
            notes.append("Nothing has been played yet, so there's no most played to go on.")
            return None
        return _Source("most_played", "your most played", _starts(_one_each(played)))
    if seed.kind == "playlist":
        playlist = next((p for p in heard["playlists"] if p["id"] == seed.value), None)
        if playlist is None:
            raise NotFoundError("That playlist doesn't exist any more.")
        inside = [by_id[track_id] for track_id in playlist["track_ids"] if track_id in by_id]
        if not inside:
            notes.append(f"“{playlist['name']}” has no songs yet, so there's nothing to go on.")
            return None
        inside = list({row["rel_path"]: row for row in inside}.values())
        return _Source("playlist", playlist["name"], _starts(_shuffled(inside, weight, rng)))
    if seed.kind == "top_artist":
        name = _top_artist(songs, plays)
        if name is None:
            notes.append("The library has no songs yet, so there's no top artist.")
            return None
        return _Source("artist", name, [_Start(artist=name, artist_radio=True)])
    if seed.kind == "artist":
        name = seed.value or ""
        return _Source("artist", name, [_Start(artist=name, artist_radio=True)])
    if seed.kind == "typed":
        return _typed(seed.value or "", songs, weight, index, rng, notes)
    if seed.kind == "lastfm":
        return _lastfm(songs, rng, notes)
    return _genre(seed.value or "", songs, weight, index, rng, notes)


def _lastfm(songs: list[dict[str, Any]], rng: random.Random, notes: list[str]) -> _Source | None:
    """The owner's most played songs on Last.fm, over the last six months (of all time,
    if that's too few to go on). One the owner has, with a known YouTube id, needs no
    search before its radio."""
    try:
        played, _ = lastfm.top_tracks(LASTFM_PERIOD, LASTFM_SONGS)
        if len(played) < MIN_RADIOS:
            played, _ = lastfm.top_tracks("overall", LASTFM_SONGS)
    except UserError as exc:  # not set up, or Last.fm said no: the other seeds still count
        notes.append(exc.message)
        return None
    if not played:
        notes.append("Last.fm has no plays for you yet, so there's nothing to go on there.")
        return None
    # The owner's songs YouTube Music is known to have, by title: version, credit, song.
    mine: dict[str, list[tuple[frozenset[str], str, dict[str, Any]]]] = {}
    for row in songs:
        if _video_id(row):
            parsed = parse_title(row.get("title") or "")
            credit = f" {youtube.artist_key(row.get('artist') or '')} "
            mine.setdefault(compare_key(parsed.title), []).append(
                (_hard(parsed.version_tokens), credit, row)
            )

    def own(title: str, artist: str) -> dict[str, Any] | None:
        parsed, name = parse_title(title), youtube.artist_key(artist)
        for versions, credit, row in mine.get(compare_key(parsed.title), []):
            if versions == _hard(parsed.version_tokens) and name and f" {name} " in credit:
                return row
        return None

    # The most played likelier to come early; the same word gives the same order.
    order = sorted(
        played, key=lambda t: rng.random() ** (1.0 / (1.0 + min(t.get("plays") or 0, 50))),
        reverse=True,
    )  # fmt: skip
    starts = []
    for track in order:
        artist = track["artists"][0] if track["artists"] else ""
        have = own(track["title"], artist)
        starts.append(
            _Start(
                title=track["title"],
                artist=artist,
                video_id=_video_id(have) if have else None,
                genre=(_genre_tag(have) or None) if have else None,
            )
        )
    return _Source("lastfm", "your Last.fm", starts)


def _typed(
    words: str,
    songs: list[dict[str, Any]],
    weight: Callable[[dict[str, Any]], float],
    index: Index,
    rng: random.Random,
    notes: list[str],
) -> _Source | None:
    """What the owner typed when asked "What music would you like today?": a genre or an
    artist, and they needn't say which.

    It's a genre if the owner has songs tagged with it, or it's one of the names genres
    go by. Otherwise it's an artist if YouTube Music has one of exactly that name
    (which costs one request, kept for 30 days). Failing both, YouTube Music's own
    playlists are searched for it as for any genre."""
    is_genre = compare_key(words) in GENRE_NAMES or (
        sum(1 for row in songs if _video_id(row) and _is_genre(_genre_tag(row), words))
        >= MIN_RADIOS
    )
    if not is_genre:
        try:
            artist = youtube.artist_radio(words, cache=index)
        except _skippable() as exc:
            log.warning("discover: looking for an artist called %r: %s", words, exc)
            artist = None
        if artist is not None:
            return _Source("artist", artist.name, [_Start(artist=artist.name, artist_radio=True)])
    quiet: list[str] = []
    found = _genre(words, songs, weight, index, rng, quiet)
    if found is None:
        notes.append(
            f"YouTube Music has no artist and no playlist of its own called “{words}”. "
            "Try an artist's name, or a kind of music such as rock or hip hop."
        )
    return found


def _genre(
    name: str,
    songs: list[dict[str, Any]],
    weight: Callable[[dict[str, Any]], float],
    index: Index,
    rng: random.Random,
    notes: list[str],
) -> _Source | None:
    """The owner's songs of this genre that YouTube Music is known to have; with too few
    of those to go on, YouTube Music's own playlist for the genre."""
    mine = [row for row in songs if _video_id(row) and _is_genre(_genre_tag(row), name)]
    spelled = _spelled(name, songs)
    if len(mine) >= MIN_RADIOS:
        return _Source("genre", name, _starts(_shuffled(mine, weight, rng)), genre=spelled)
    try:
        playlist = youtube.genre_playlist(name, cache=index)
    except _skippable() as exc:
        log.warning("discover: the genre %r: %s", name, exc)
        playlist = None
    if playlist is None or not playlist.tracks:
        if not mine:
            notes.append(f"YouTube Music has no playlist of its own for “{name}”.")
            return None
        return _Source("genre", name, _starts(_shuffled(mine, weight, rng)), genre=spelled)
    # Official audio first: its radio is official audio too. A music video has to be
    # looked up as a song before a radio can start from it.
    tracks = list(playlist.tracks)
    rng.shuffle(tracks)
    tracks.sort(key=lambda c: not c.is_official_audio)
    listed = [
        _Start(
            title=parse_title(c.title).title or c.title,
            artist=", ".join(c.artists),
            video_id=c.video_id if c.is_official_audio else None,
            listed_on=playlist.title,
        )
        for c in tracks
    ]
    return _Source("genre", name, _starts(_shuffled(mine, weight, rng)) + listed, genre=spelled)


def _spelled(name: str, songs: list[dict[str, Any]]) -> str:
    """A genre as the owner's files spell it ("Hip-Hop/Rap" for "hip hop"): the commonest
    tag of theirs that means it. With none, the words as typed, capitals added if there
    were none."""
    tags_used = Counter(
        tag for tag in (_genre_tag(row) for row in songs) if tag and _is_genre(tag, name)
    )
    if tags_used:
        return max(tags_used, key=lambda tag: (tags_used[tag], tag))
    name = " ".join(name.split())
    return name if name != name.lower() else name.title()


def _genre_tag(row: dict[str, Any]) -> str:
    try:
        details = json.loads(row.get("details_json") or "{}")
    except ValueError:
        return ""
    genre = details.get("genre") if isinstance(details, dict) else None
    return genre if isinstance(genre, str) else ""


def _is_genre(tag: str, wanted: str) -> bool:
    """Whether a file's genre tag ("Hip-Hop/Rap") is the genre asked for ("hip hop")."""
    tag_key, wanted_key = f" {compare_key(tag)} ", compare_key(wanted)
    if not wanted_key or not tag_key.strip():
        return False
    names = next((group for group in SAME_GENRE if wanted_key in group), {wanted_key})
    return any(f" {name} " in tag_key for name in names)


def _starts(rows: list[dict[str, Any]]) -> list[_Start]:
    return [
        _Start(
            title=row.get("title") or "",
            artist=row.get("artist") or "",
            video_id=_video_id(row),
            genre=_genre_tag(row) or None,
        )
        for row in rows
        if row.get("title")
    ]


def _shuffled(
    rows: list[dict[str, Any]], weight: Callable[[dict[str, Any]], float], rng: random.Random
) -> list[dict[str, Any]]:
    """The songs in a random order, favourites and often-played ones likelier to come
    early, and one song from each artist before a second from any."""
    keyed = sorted(rows, key=lambda row: rng.random() ** (1.0 / weight(row)), reverse=True)
    return _one_each(keyed)


def _one_each(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same order, but an artist's second song waits until every artist has had one."""
    first, later, seen = [], [], set()
    for row in rows:
        artist = compare_key(row.get("artist"))
        (later if artist in seen else first).append(row)
        seen.add(artist)
    return first + later


def _top_artist(songs: list[dict[str, Any]], plays: dict[str, Any]) -> str | None:
    """The artist played most; with nothing played yet, the one with the most songs."""
    played: Counter[str] = Counter()
    owned: Counter[str] = Counter()
    spelling: dict[str, str] = {}
    for row in songs:
        name = row.get("artist")
        key = youtube.artist_key(name or "")
        if not key:
            continue
        spelling.setdefault(key, name)
        owned[key] += 1
        played[key] += plays.get(row.get("musicorg_id"), {}).get("count", 0)
    if not owned:
        return None
    best = max(owned, key=lambda key: (played[key], owned[key], key))
    return spelling[best]


def _video_id(row: dict[str, Any]) -> str | None:
    found = row.get("source_id")
    return found if isinstance(found, str) and youtube.VIDEO_ID.fullmatch(found) else None


def is_there(lib: Library, row: dict[str, Any]) -> bool:
    """The index is a cache: a song whose file has gone isn't the owner's any more."""
    return lib.root.joinpath(*row["rel_path"].split("/")).is_file()


# ---- asking YouTube Music ----------------------------------------------------------------


def _skippable() -> tuple[type[BaseException], ...]:
    """One radio that can't be read (a song that's gone, an answer ytmusicapi can't
    parse) is left out; the others still count. A slow-down isn't one of these: the
    limiter turns it into a pause, which stops the whole request."""
    from ytmusicapi.exceptions import YTMusicError

    return (YTMusicError, YouTubeError, KeyError, IndexError, TypeError)


def _look_up(start: _Start, index: Index) -> str | None:
    """The YouTube Music id of a starting song known only by name."""
    query = f"{start.artist} {start.title}".strip()
    try:
        results = youtube.search_songs(query, 5, cache=index)
    except _skippable() as exc:
        log.warning("discover: looking up %r: %s", query, exc)
        return None
    for candidate in results:
        if candidate.is_official_audio and youtube.same_song(start.title, start.artist, candidate):
            return candidate.video_id
    return None


def _radio(
    start: _Start, source: _Source, index: Index, notes: list[str]
) -> list[Candidate] | None:
    try:
        if not start.artist_radio:
            return youtube.radio(str(start.video_id), cache=index)
        found = youtube.artist_radio(start.artist, cache=index)
    except _skippable() as exc:
        log.warning("discover: the radio for %s %r: %s", start.artist, start.title, exc)
        return None
    if found is None:
        notes.append(f"YouTube Music doesn't know an artist called “{start.artist}”.")
        return None
    # The artist's own songs on their radio are where more radios can start from.
    source.label = found.name
    mine = youtube.artist_key(found.name)
    source.starts += [
        _Start(title=c.title, artist=", ".join(c.artists), video_id=c.video_id)
        for c in found.tracks
        if c.is_official_audio and mine in {youtube.artist_key(a) for a in c.artists}
    ]
    start.artist = found.name
    return list(found.tracks)


# ---- ranking and saying why --------------------------------------------------------------


def _hard(tokens: tuple[str, ...]) -> frozenset[str]:
    """The version words that make it a different recording (a remix isn't the original)."""
    return frozenset(t for t in tokens if t.partition(":")[0] not in SOFT_VERSION_KINDS)


def _song_key(candidate: Candidate) -> tuple[str, frozenset[str], str]:
    """What makes two uploads the same song: title, version and main artist."""
    parsed = parse_title(candidate.title)
    artist = youtube.artist_key(candidate.artists[0]) if candidate.artists else ""
    return (compare_key(parsed.title) or candidate.video_id, _hard(parsed.version_tokens), artist)


def _score(entry: _Found, owned: Owned) -> float:
    artist = entry.candidate.artists[0] if entry.candidate.artists else ""
    boost = min(owned.songs_by(artist), ARTIST_BOOST_SONGS) * 0.8  # at most 16 places up
    return len(entry.hits) * 100 + boost - entry.best_position * 0.5


def _spread(ranked: list[_Found], count: int, exempt: set[str]) -> list[_Found]:
    """The best `count`, without one artist filling the page: each gets a share, and the
    rest of theirs come in only if there's room left. An artist asked for by name
    (`exempt`) isn't held back."""
    share = max(2, math.ceil(count / 8))
    taken: Counter[str] = Counter()
    picks, held = [], []
    for entry in ranked:
        artist = youtube.artist_key(entry.candidate.artists[0]) if entry.candidate.artists else ""
        if artist in exempt or taken[artist] < share:
            taken[artist] += 1
            picks.append(entry)
        else:
            held.append(entry)
        if len(picks) == count:
            return picks
    return picks + held[: count - len(picks)]


def _pick_genre(entry: _Found) -> str | None:
    """The genre a pick was found under, if that's known: the genre that was asked for,
    or the owner's tag on the song whose radio it was nearest the top of. It goes with
    the pick into a download, so the downloaded song can be filed under it."""
    source, start, _ = min(entry.hits.values(), key=lambda hit: hit[2])
    return source.genre or start.genre


def _named(source: _Source) -> str:
    """A starting point as a "why" line names it: "Road Trip", "Linkin Park", "rock
    songs", "your library"."""
    return f"{source.label} songs" if source.kind == "genre" else source.label


def _why(entry: _Found, owned: Owned) -> str:
    hits = list(entry.hits.values())
    source, start, _ = min(hits, key=lambda hit: hit[2])
    artist = entry.candidate.artists[0] if entry.candidate.artists else ""
    if source.kind == "artist" and len({s.label for s, _, _ in hits}) == 1:
        own = youtube.artist_key(source.label) in {
            youtube.artist_key(name) for name in entry.candidate.artists
        }
        return f"By {source.label}" if own else f"Similar to {source.label}"
    if source.kind == "lastfm" and entry.candidate.video_id == start.video_id:
        return "One of your most played on Last.fm"
    if len(hits) > 1:
        labels = list(dict.fromkeys(_named(s) for s, _, _ in hits))
        if len(labels) == 2:  # found from both of two starting points: the best kind of pick
            return f"On the radio for both {labels[0]} and {labels[1]}"
        if len(labels) > 2:
            return f"On the radio for {len(labels)} of your starting points"
        if source.kind == "library":
            return f"On the radio for {len(hits)} of your songs"
        if source.kind == "most_played":
            return f"On the radio for {len(hits)} of your most played songs"
        if source.kind == "playlist":
            return f"On the radio for {len(hits)} songs in {source.label}"
        if source.kind == "lastfm":
            return f"On the radio for {len(hits)} of your most played on Last.fm"
        return f"On the radio for {len(hits)} {source.label} songs"
    if start.listed_on and entry.candidate.video_id == start.video_id:
        return f"On YouTube Music's “{start.listed_on}”"
    have = owned.songs_by(artist)
    if have >= 5:
        return f"You have {have} {artist} songs"
    by = f" by {start.artist}" if start.artist else ""
    return f"Like “{start.title}”{by}"
