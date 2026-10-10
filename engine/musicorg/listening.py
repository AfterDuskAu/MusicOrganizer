"""listening (v0.2): the owner's favourites, play counts, listening time and playlists.

None of this can live in a file's tags (a playlist isn't about one recording, and a play
count would mean rewriting the song every time it's heard), so it's kept in state.json
under "listening", which the contract (section 5) sets aside for exactly this. Songs are
named by their MUSICORG_ID, which stays the same when a file is renamed, moved or
upgraded, so a playlist survives a tidy.

    "listening": {
        "favourites": {track_id: {"since": ISO time}},
        "plays": {track_id: {"count": 3, "last_played": ISO time}},
        "listened": {track_id: 15324.5},                 # seconds spent listening to it
        "playlists": [{"id": "pl_…", "name": "…", "created_at": ISO time,
                       "track_ids": [track_id, …]}],     # in the owner's order
        "library": {track_id: {"since": ISO time}},      # downloads moved into the main library
        "devices": {"taken": ["9f2c…", …],               # changes taken from paired devices
                    "made": [{"theirs": "5b1e…", "playlist": "pl_…"}]}
    }

A download stays under Discover → Downloads until the owner moves it into the main
library (or sets the app to show all downloads there). That's the owner's sorting, not a
fact about the recording, and no file moves, so it's kept here too.

**Listening time (2026-10-10)** is how long a song has been listened to, in seconds: one
figure for a song, wherever it was heard. The app adds what its own player played
(`listened`), and a paired phone player adds what was played on it (`from_devices`). It
is time, not a count: a play is still a song heard to its end.

**Changes from a paired device (2026-10-10).** A phone player tells the computer what
was done on it: a playlist made, a song put in a playlist, time spent listening
(`sharing` checks what it sent and hands it over as `DeviceChange`s; docs/ENGINE_API.md,
section 3). What's kept so that each is taken once, under "devices":

- `taken`: a short hash of every change taken, the oldest first, `TAKEN_KEPT` at most.
  A change that comes again (its answer was lost on the way) is known by it.
- `made`: for a playlist made on a device, which of the library's playlists it is, by
  a hash of the id the device made up for it. A song the device puts in "its" playlist
  lands in ours, in the same request or a later one. Gone with the playlist.

Every function takes the open library (its lock must be held to change anything) and
returns plain data in the shapes docs/ENGINE_API.md gives.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from musicorg import state
from musicorg.errors import NotFoundError, UserError
from musicorg.library import Library

KEY = "listening"
MAX_NAME = 200
CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")
MAX_SECONDS = 24 * 3600  # more listening than this at once isn't believed
# What a paired device's changes may come to, whatever it sends.
TAKEN_KEPT = 5000  # the changes remembered as taken: the newest
MADE_KEPT = 500  # the playlists remembered as a device's own: the newest
MAX_PLAYLISTS = 1000  # a device can't make a playlist in a library that has this many
MAX_PLAYLIST_SONGS = 10_000  # nor make a playlist longer than this


@dataclass(frozen=True)
class DeviceChange:
    """One thing done on a paired device, in the library's own terms. `kind` is a Sync
    change kind (docs/ENGINE_API.md → Enums)."""

    mark: str  # a short hash of the change's own id: a change is taken once
    kind: str
    track_id: str = ""  # `playlist_add` and `listened`: the song
    playlist_id: str = ""  # `playlist_add`: one of the library's playlists…
    theirs: str = ""  # …or one the device made, by the hash of its id there
    name: str = ""  # `playlist_new`: what the device called it
    seconds: float = 0.0  # `listened`


def get(lib: Library) -> dict[str, Any]:
    """Everything, cleaned of anything malformed: `{favourites, plays, listened,
    playlists, library, heard}`. What's kept about paired devices isn't part of it."""
    return _shown(_read(lib.load_state().data))


def set_favourite(lib: Library, track_id: str, on: bool) -> list[str]:
    """Mark or unmark a favourite. Returns the favourites, most recent first."""
    _check_id(track_id)
    with state.edit(lib.paths.state_file) as st:
        data = _read(st.data)
        if on and track_id not in data["favourites"]:
            data["favourites"][track_id] = {"since": _now()}
        elif not on:
            data["favourites"].pop(track_id, None)
        st.data[KEY] = data
    return _shown(data)["favourites"]


def move(lib: Library, track_ids: list[str], *, to_library: bool) -> list[str]:
    """Move downloads into the main library's lists, or back under Downloads. Returns
    the ids that are in the main library now."""
    for track_id in track_ids:
        _check_id(track_id)
    with state.edit(lib.paths.state_file) as st:
        data = _read(st.data)
        for track_id in track_ids:
            if to_library:
                data["library"].setdefault(track_id, {"since": _now()})
            else:
                data["library"].pop(track_id, None)
        st.data[KEY] = data
    return _shown(data)["library"]


def forget(lib: Library, track_ids: list[str]) -> None:
    """Take songs that have left the library (deleted downloads) out of everything here:
    favourites, play counts, listening time, playlists and the moved-to-library list."""
    gone = set(track_ids)
    if not gone:
        return
    with state.edit(lib.paths.state_file) as st:
        data = _read(st.data)
        for name in ("favourites", "plays", "listened", "library"):
            for track_id in gone:
                data[name].pop(track_id, None)
        for playlist in data["playlists"]:
            playlist["track_ids"] = [t for t in playlist["track_ids"] if t not in gone]
        st.data[KEY] = data


def played(lib: Library, track_id: str) -> dict[str, Any]:
    """Count one play of a song. Returns its `{count, last_played}`."""
    _check_id(track_id)
    with state.edit(lib.paths.state_file) as st:
        data = _read(st.data)
        entry = data["plays"].setdefault(track_id, {"count": 0})
        entry["count"] = int(entry.get("count", 0)) + 1
        entry["last_played"] = _now()
        st.data[KEY] = data
    return dict(entry)


def listened(lib: Library, track_id: str, seconds: float) -> float:
    """Add time spent listening to a song: the time it was playing, however much of the
    song that was. Returns the song's total, in seconds."""
    _check_id(track_id)
    if not _believable(seconds):
        raise UserError("That isn't a length of time spent listening.")
    with state.edit(lib.paths.state_file) as st:
        data = _read(st.data)
        total = data["listened"][track_id] = _added(data["listened"].get(track_id), seconds)
        st.data[KEY] = data
    return total


def from_devices(lib: Library, changes: list[DeviceChange]) -> dict[str, Any]:
    """Take what was done on a paired device, all of it in one save: either every change
    here is in state.json with the note that it was taken, or none is.

    A change taken before is left alone. One that can't be done any more (its playlist
    has been deleted since) is dropped, and is noted as taken all the same. Returns what
    was done: `{playlists, songs, seconds}` (playlists made, songs put in playlists, and
    listening time added)."""
    done: dict[str, Any] = {"playlists": 0, "songs": 0, "seconds": 0.0}
    if not changes:
        return done
    with state.edit(lib.paths.state_file) as st:
        data = _read(st.data)
        taken: list[str] = data["devices"]["taken"]
        made: list[dict[str, str]] = data["devices"]["made"]
        seen = set(taken)
        for change in changes:
            if change.mark in seen:
                continue  # it came before, and its answer was lost on the way
            seen.add(change.mark)
            taken.append(change.mark)
            if change.kind == "playlist_new":
                done["playlists"] += _made_on_a_device(data, change)
            elif change.kind == "playlist_add":
                done["songs"] += _added_on_a_device(data, change)
            elif change.kind == "listened" and change.track_id and _believable(change.seconds):
                time = data["listened"]
                time[change.track_id] = _added(time.get(change.track_id), change.seconds)
                done["seconds"] = round(done["seconds"] + change.seconds, 1)
        del taken[:-TAKEN_KEPT]
        del made[:-MADE_KEPT]
        st.data[KEY] = data
    return done


def _made_on_a_device(data: dict[str, Any], change: DeviceChange) -> int:
    """A playlist made on a device. **If the library has a playlist of that name
    already, that one is used** and nothing is made: a device can't see a playlist made
    here since it last synced, and nobody could tell two of one name apart. The same
    spelling comes first, then the same name in other capitals. A name longer than the
    library allows is cut. Returns how many playlists were made (1 or 0)."""
    made = data["devices"]["made"]
    name = " ".join(change.name.split())[:MAX_NAME].strip()
    if not name or not change.theirs or any(m["theirs"] == change.theirs for m in made):
        return 0  # nothing to call it, nothing to know it by, or made already
    same = [p for p in data["playlists"] if p["name"] == name]
    same += [p for p in data["playlists"] if p["name"].casefold() == name.casefold()]
    if same:
        made.append({"theirs": change.theirs, "playlist": same[0]["id"]})
        return 0
    if len(data["playlists"]) >= MAX_PLAYLISTS:
        return 0
    new = {"id": "pl_" + secrets.token_hex(6), "name": name, "created_at": _now(), "track_ids": []}
    data["playlists"].append(new)
    made.append({"theirs": change.theirs, "playlist": new["id"]})
    return 1


def _added_on_a_device(data: dict[str, Any], change: DeviceChange) -> int:
    """A song put in a playlist on a device: it goes at the end, **unless the playlist
    has it already** (a device can't see what was put in here since it last synced, so a
    second copy would be one nobody asked for). Returns how many songs went in (1 or 0)."""
    wanted = change.playlist_id
    if not wanted:  # a playlist the device made: ours, by the note kept when it was made
        wanted = next(
            (m["playlist"] for m in data["devices"]["made"] if m["theirs"] == change.theirs), ""
        )
    for found in data["playlists"]:
        if found["id"] == wanted:
            songs = found["track_ids"]
            if not change.track_id or change.track_id in songs:
                return 0
            if len(songs) >= MAX_PLAYLIST_SONGS:
                return 0
            songs.append(change.track_id)
            return 1
    return 0  # the playlist has been deleted since


def _believable(seconds: object) -> bool:
    """Whether this can be time spent listening: a number above nothing, and no more
    than a day at once. (True and False are numbers to Python, and JSON has a "not a
    number": neither is a length of time.)"""
    return (
        isinstance(seconds, int | float)
        and not isinstance(seconds, bool)
        and 0 < seconds <= MAX_SECONDS
    )


def _added(before: object, seconds: float) -> float:
    return round((before if isinstance(before, int | float) else 0.0) + seconds, 1)


def heard(lib: Library, video_id: str) -> dict[str, Any]:
    """Note that a song from YouTube was played all the way through (it isn't the
    owner's: a pick or a search result). Kept for good, per library, so the app can mark
    what's been heard. Returns its `{count, last_heard}`."""
    if not isinstance(video_id, str) or not youtube_id(video_id):
        raise UserError("That isn't a YouTube video id.")
    with state.edit(lib.paths.state_file) as st:
        data = _read(st.data)
        entry = data["heard"].setdefault(video_id, {"count": 0})
        entry["count"] = int(entry.get("count", 0)) + 1
        entry["last_heard"] = _now()
        st.data[KEY] = data
    return dict(entry)


def followed(lib: Library) -> list[dict[str, Any]]:
    """The channels the owner follows, by name: `[{channel_id, name, thumbnail}]`."""
    return _channels(_read(lib.load_state().data))


def follow(
    lib: Library, channel_id: str, on: bool, *, name: str = "", thumbnail: str | None = None
) -> list[dict[str, Any]]:
    """Follow a channel, or stop. Kept with the library, like favourites: it's a note
    of whose videos to list, nothing more. Returns the channels followed."""
    if not isinstance(channel_id, str) or not CHANNEL_ID.fullmatch(channel_id):
        raise UserError("That isn't a channel's id.")
    with state.edit(lib.paths.state_file) as st:
        data = _read(st.data)
        if on:
            kept = data["channels"].get(channel_id, {})
            data["channels"][channel_id] = {
                "name": _name(name) if name.strip() else kept.get("name") or channel_id,
                "thumbnail": thumbnail or kept.get("thumbnail"),
                "since": kept.get("since") or _now(),
            }
        else:
            data["channels"].pop(channel_id, None)
        st.data[KEY] = data
    return _channels(data)


def _channels(data: dict[str, Any]) -> list[dict[str, Any]]:
    listed = [
        {"channel_id": channel_id, "name": kept["name"], "thumbnail": kept["thumbnail"]}
        for channel_id, kept in data["channels"].items()
    ]
    return sorted(listed, key=lambda channel: channel["name"].casefold())


def youtube_id(text: str) -> bool:
    return len(text) == 11 and all(c.isalnum() or c in "-_" for c in text)


def create_playlist(lib: Library, name: str) -> list[dict[str, Any]]:
    name = _name(name)
    with state.edit(lib.paths.state_file) as st:
        data = _read(st.data)
        data["playlists"].append(
            {"id": "pl_" + secrets.token_hex(6), "name": name, "created_at": _now(),
             "track_ids": []}
        )  # fmt: skip
        st.data[KEY] = data
    return data["playlists"]


def rename_playlist(lib: Library, playlist_id: str, name: str) -> list[dict[str, Any]]:
    name = _name(name)
    with state.edit(lib.paths.state_file) as st:
        data = _read(st.data)
        _find(data, playlist_id)["name"] = name
        st.data[KEY] = data
    return data["playlists"]


def delete_playlist(lib: Library, playlist_id: str) -> list[dict[str, Any]]:
    """Remove a playlist. Only the list goes: the songs in it are untouched."""
    with state.edit(lib.paths.state_file) as st:
        data = _read(st.data)
        data["playlists"].remove(_find(data, playlist_id))
        st.data[KEY] = data
    return data["playlists"]


def set_playlist_tracks(
    lib: Library, playlist_id: str, track_ids: list[str]
) -> list[dict[str, Any]]:
    """Replace a playlist's songs, in this order: adding, removing and reordering are
    all this one call. A song may be in a playlist more than once."""
    for track_id in track_ids:
        _check_id(track_id)
    with state.edit(lib.paths.state_file) as st:
        data = _read(st.data)
        _find(data, playlist_id)["track_ids"] = list(track_ids)
        st.data[KEY] = data
    return data["playlists"]


def add_to_playlist(lib: Library, playlist_id: str, track_id: str) -> bool:
    """Put one song at the end of a playlist, unless it's in it already (a download
    that was asked for as part of a playlist joins it when it arrives). False, and
    nothing changes, if the playlist has been deleted meanwhile."""
    _check_id(track_id)
    with state.edit(lib.paths.state_file) as st:
        data = _read(st.data)
        for found in data["playlists"]:
            if found["id"] == playlist_id:
                if track_id not in found["track_ids"]:
                    found["track_ids"].append(track_id)
                    st.data[KEY] = data
                return True
    return False


def _find(data: dict[str, Any], playlist_id: str) -> dict[str, Any]:
    for playlist in data["playlists"]:
        if playlist["id"] == playlist_id:
            return playlist
    raise NotFoundError("That playlist doesn't exist any more.")


def _name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise UserError("A playlist needs a name.")
    if len(name) > MAX_NAME:
        raise UserError(f"That name is too long (the limit is {MAX_NAME} characters).")
    return name


def _check_id(track_id: object) -> None:
    if not isinstance(track_id, str) or not track_id or len(track_id) > 100:
        raise UserError("That isn't a song in the library.")


def _read(data: dict[str, Any]) -> dict[str, Any]:
    """state.json's "listening", keeping only what's well formed (a hand-edited or
    damaged entry is dropped rather than crashing the app)."""
    raw = data.get(KEY)
    raw = raw if isinstance(raw, dict) else {}
    favourites = raw.get("favourites")
    plays = raw.get("plays")
    playlists = raw.get("playlists")
    moved = raw.get("library")
    heard_ = raw.get("heard")
    channels = raw.get("channels")
    time = raw.get("listened")
    kept = [
        {"id": p["id"], "name": p["name"], "created_at": p.get("created_at"),
         "track_ids": [t for t in p.get("track_ids") or [] if isinstance(t, str)]}
        for p in (playlists if isinstance(playlists, list) else [])
        if isinstance(p, dict)
        and isinstance(p.get("id"), str)
        and isinstance(p.get("name"), str)
    ]  # fmt: skip
    return {
        "devices": _devices(raw.get("devices"), {p["id"] for p in kept}),
        "listened": {
            k: round(float(v), 1)
            for k, v in (time.items() if isinstance(time, dict) else [])
            if isinstance(k, str) and k and _countable(v)
        },
        "channels": {
            k: {
                "name": v["name"],
                "thumbnail": v.get("thumbnail") if isinstance(v.get("thumbnail"), str) else None,
                "since": v.get("since"),
            }
            for k, v in (channels.items() if isinstance(channels, dict) else [])
            if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("name"), str)
        },
        "heard": {
            k: {"count": v["count"], "last_heard": v.get("last_heard")}
            for k, v in (heard_.items() if isinstance(heard_, dict) else [])
            if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("count"), int)
        },
        "library": {
            k: {"since": v.get("since") if isinstance(v, dict) else None}
            for k, v in (moved.items() if isinstance(moved, dict) else [])
            if isinstance(k, str) and k
        },
        "favourites": {
            k: {"since": v.get("since") if isinstance(v, dict) else None}
            for k, v in (favourites.items() if isinstance(favourites, dict) else [])
            if isinstance(k, str) and k
        },
        "plays": {
            k: {"count": v["count"], "last_played": v.get("last_played")}
            for k, v in (plays.items() if isinstance(plays, dict) else [])
            if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("count"), int)
        },
        "playlists": kept,
    }


def _countable(value: object) -> bool:
    """A total of listening time that can be added to: a real number, not below zero,
    and not more than anyone could have listened (three hundred years)."""
    return isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= 1e10


def _devices(raw: object, playlists: set[str]) -> dict[str, Any]:
    """What's kept about paired devices' changes, keeping only what's well formed. The
    note of a playlist a device made goes when the playlist does."""
    raw = raw if isinstance(raw, dict) else {}
    taken, made = raw.get("taken"), raw.get("made")
    return {
        "taken": [t for t in (taken if isinstance(taken, list) else []) if isinstance(t, str)],
        "made": [
            {"theirs": m["theirs"], "playlist": m["playlist"]}
            for m in (made if isinstance(made, list) else [])
            if isinstance(m, dict)
            and isinstance(m.get("theirs"), str)
            and isinstance(m.get("playlist"), str)
            and m["playlist"] in playlists
        ],
    }


def _shown(data: dict[str, Any]) -> dict[str, Any]:
    recent = sorted(data["favourites"].items(), key=lambda kv: kv[1]["since"] or "", reverse=True)
    return {
        "favourites": [track_id for track_id, _ in recent],
        "plays": data["plays"],
        "listened": data["listened"],
        "playlists": data["playlists"],
        "library": sorted(data["library"]),
        "heard": sorted(data["heard"]),
    }


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
