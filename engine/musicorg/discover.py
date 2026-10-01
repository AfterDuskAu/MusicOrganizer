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
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from musicorg import listening, naming, queue, state, youtube
from musicorg.errors import NotFoundError, UserError, YouTubeError
from musicorg.index import Index
from musicorg.library import Library
from musicorg.normalize import SOFT_VERSION_KINDS, compare_key, parse_filename, parse_title
from musicorg.youtube import Candidate

log = logging.getLogger(__name__)

SEED_KINDS = ("library", "most_played", "top_artist", "playlist", "artist", "genre")
MAX_COUNT = 500
MAX_SEEDS = 8
MIN_RADIOS = 4  # fewer starting points and "found on several radios" means nothing
MAX_RADIOS = 24  # about 36 seconds of asking at the limiter's pace
SONGS_PER_RADIO = 25  # new songs a radio usually adds, for saying how far along it is
POOL = 3  # stop asking once there are this many times the songs wanted to choose from
MAX_LOOKUPS = 8  # searches for starting songs whose YouTube id isn't known
ARTIST_BOOST_SONGS = 10  # owning more of an artist than this adds nothing further

# Genre tags that mean the same thing, as `compare_key` spells them.
SAME_GENRE = (
    frozenset({"hip hop", "rap"}),
    frozenset({"r and b", "r b", "rnb"}),
    frozenset({"electronic", "electronica"}),
)

Progress = Callable[[int, int], None]


@dataclass(frozen=True)
class Seed:
    """Where picks start from. `value` is a playlist's id, an artist's name or a genre;
    the other kinds have none."""

    kind: str
    value: str | None = None

    @classmethod
    def from_dict(cls, data: object) -> Seed:
        if not isinstance(data, dict) or data.get("kind") not in SEED_KINDS:
            raise UserError(f"A seed's kind should be one of: {', '.join(SEED_KINDS)}.")
        kind = data["kind"]
        value = data.get("playlist_id") if kind == "playlist" else data.get("name")
        if kind in ("playlist", "artist", "genre"):
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


@dataclass
class _Source:
    """One seed, worked out: its name for the "why" lines and the starts still to try."""

    kind: str
    label: str
    starts: list[_Start] = field(default_factory=list)
    used: int = 0


@dataclass
class _Found:
    candidate: Candidate
    order: int  # the order it first turned up in: the last tie-break
    hits: dict[int, tuple[_Source, _Start, int]] = field(default_factory=dict)  # by start

    @property
    def best_position(self) -> int:
        return min(position for _, _, position in self.hits.values())


class _Owned:
    """What the owner has, for "is this pick already mine?" and "how much of this artist
    do I have?"."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.ids: set[str] = set()
        self.songs: dict[str, list[tuple[frozenset[str], str]]] = {}
        self.credits: list[str] = []
        self._counts: dict[str, int] = {}
        for row in rows:
            if isinstance(row.get("source_id"), str) and row["source_id"]:
                self.ids.add(row["source_id"])
            if naming.is_video_path(row["rel_path"]):
                continue  # a saved video isn't the song
            credit = f" {youtube.artist_key(row.get('artist') or '')} "
            self.credits.append(credit)
            self._add(row.get("title") or "", credit, ())
            # A copy not identified yet can carry the plain title while the rip it came
            # from is named "Song R" (a remix) or "Artist - Song": its name counts too.
            origin = row.get("origin_path")
            if isinstance(origin, str) and origin:
                name = re.split(r"[\\/]", origin)[-1]
                parsed = parse_filename(name.rsplit(".", 1)[0] if "." in name else name)
                if parsed.title:
                    by = f" {youtube.artist_key(parsed.artist or '')} "
                    self._add(parsed.title, credit if by.strip() == "" else by,
                              parsed.version_tokens)  # fmt: skip

    def _add(self, title: str, credit: str, versions: tuple[str, ...]) -> None:
        parsed = parse_title(title)
        key = compare_key(parsed.title)
        if key:
            hard = _hard(parsed.version_tokens + tuple(versions))
            self.songs.setdefault(key, []).append((hard, credit))

    def has(self, candidate: Candidate) -> bool:
        if candidate.video_id in self.ids:
            return True
        parsed = parse_title(candidate.title)
        versions = _hard(parsed.version_tokens)
        names = [youtube.artist_key(name) for name in candidate.artists]
        for hard, credit in self.songs.get(compare_key(parsed.title), []):
            if hard == versions and any(name and f" {name} " in credit for name in names):
                return True
        return False

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
    progress: Progress | None = None,
) -> dict[str, Any]:
    """`count` picks from these seeds, best first: `{picks, wanted, radios, seeds,
    note}` (docs/ENGINE_API.md → `discover.suggest`). `index` must be writable: YouTube
    Music's answers are kept in it."""
    if not seeds:
        raise UserError("Choose where to start from: your library, a playlist, an artist…")
    if len(seeds) > MAX_SEEDS:
        raise UserError(f"That's too many starting points at once (the limit is {MAX_SEEDS}).")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_COUNT:
        raise UserError(f"How many songs should be 1 to {MAX_COUNT}.")

    rows = [row for row in index.library_tracks() if _is_there(lib, row)]
    owned = _Owned(rows)
    heard = listening.get(lib)
    skip_ids = set().union(*state.rejected(lib.load_state().data).values())
    skip_ids |= {
        row["video_id"] for row in queue.downloads(lib.paths) if isinstance(row["video_id"], str)
    }
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
                if lookups >= MAX_LOOKUPS:
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
            {**entry.candidate.to_dict(), "why": _why(entry, owned), "hits": len(entry.hits)}
            for entry in picks
        ],
        "wanted": count,
        "radios": radios,
        "seeds": [{"kind": s.kind, "label": s.label, "radios": s.used} for s in sources],
        "note": " ".join(notes) or None,
    }


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
    return _genre(seed.value or "", songs, weight, index, rng, notes)


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
    if len(mine) >= MIN_RADIOS:
        return _Source("genre", name, _starts(_shuffled(mine, weight, rng)))
    try:
        playlist = youtube.genre_playlist(name, cache=index)
    except _skippable() as exc:
        log.warning("discover: the genre %r: %s", name, exc)
        playlist = None
    if playlist is None or not playlist.tracks:
        if not mine:
            notes.append(f"YouTube Music has no playlist of its own for “{name}”.")
            return None
        return _Source("genre", name, _starts(_shuffled(mine, weight, rng)))
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
    return _Source("genre", name, _starts(_shuffled(mine, weight, rng)) + listed)


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
            title=row.get("title") or "", artist=row.get("artist") or "", video_id=_video_id(row)
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


def _is_there(lib: Library, row: dict[str, Any]) -> bool:
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


def _score(entry: _Found, owned: _Owned) -> float:
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


def _why(entry: _Found, owned: _Owned) -> str:
    hits = list(entry.hits.values())
    source, start, _ = min(hits, key=lambda hit: hit[2])
    artist = entry.candidate.artists[0] if entry.candidate.artists else ""
    if source.kind == "artist" and len({s.label for s, _, _ in hits}) == 1:
        own = youtube.artist_key(source.label) in {
            youtube.artist_key(name) for name in entry.candidate.artists
        }
        return f"By {source.label}" if own else f"Similar to {source.label}"
    if len(hits) > 1:
        if len({s.label for s, _, _ in hits}) > 1:
            return f"On the radio for {len(hits)} of your starting points"
        if source.kind == "library":
            return f"On the radio for {len(hits)} of your songs"
        if source.kind == "most_played":
            return f"On the radio for {len(hits)} of your most played songs"
        if source.kind == "playlist":
            return f"On the radio for {len(hits)} songs in {source.label}"
        return f"On the radio for {len(hits)} {source.label} songs"
    if start.listed_on and entry.candidate.video_id == start.video_id:
        return f"On YouTube Music's “{start.listed_on}”"
    have = owned.songs_by(artist)
    if have >= 5:
        return f"You have {have} {artist} songs"
    by = f" by {start.artist}" if start.artist else ""
    return f"Like “{start.title}”{by}"
