"""A child's profile: only clean songs are looked up (the owner, 2026-10-07).

"If both a clean/explicit version show up, show only the clean. If there is no clean
version, say that there is no clean version in the search." And a switch in Settings
lets a song through as it is when no clean version of it was found.

This is a filter over what a lookup found, nothing more: it reads YouTube Music's own
explicit mark (`is_explicit`) and asks for nothing. A song is clean only when that mark
says so; one with no mark is never assumed clean. Read-only: it writes nothing."""

from __future__ import annotations

from typing import Any

from .normalize import compare_key, parse_title

NAMED_MOST = 5  # how many songs a note names before it says "and N more"


def is_clean(song: dict[str, Any]) -> bool:
    """Marked not explicit. No mark at all isn't clean: nobody has said so."""
    return song.get("is_explicit") is False


def _same_song(song: dict[str, Any]) -> tuple[str, str]:
    """What makes two results the same song: its title without the version words
    ("(Clean)", "[Explicit]", "feat. …") and its first artist."""
    title = str(song.get("title") or "")
    artists = song.get("artists") or []
    plain = parse_title(title).title or title
    return compare_key(plain), compare_key(str(artists[0]) if artists else "")


def _name(song: dict[str, Any]) -> str:
    artists = ", ".join(str(a) for a in song.get("artists") or [])
    title = str(song.get("title") or "")
    return f"{title} ({artists})" if artists else title


def _listed(names: list[str]) -> str:
    shown = "; ".join(names[:NAMED_MOST])
    more = len(names) - NAMED_MOST
    return shown if more <= 0 else f"{shown}; and {more} more"


def clean_only(
    songs: list[dict[str, Any]], *, allow_explicit: bool
) -> tuple[list[dict[str, Any]], str | None]:
    """`songs` as a child's profile shows them, in the same order, and a sentence about
    what that changed (None when nothing).

    - A song found both clean and explicit: only the clean one.
    - A song with no clean version: left out and named in the sentence; or, with
      `allow_explicit`, kept as it is and marked `"only_explicit": true`.
    - A song with no mark either way is treated like one with no clean version, but
      the sentence doesn't call it explicit, because nobody said it is."""
    has_clean = {_same_song(song) for song in songs if is_clean(song)}
    shown: list[dict[str, Any]] = []
    explicit: list[str] = []
    unmarked = 0
    for song in songs:
        if is_clean(song):
            shown.append(song)
        elif _same_song(song) in has_clean:
            continue  # its clean version is in the list
        elif allow_explicit:
            shown.append({**song, "only_explicit": True})
        elif song.get("is_explicit") is True:
            name = _name(song)
            if name not in explicit:
                explicit.append(name)
        else:
            unmarked += 1
    notes: list[str] = []
    if explicit:
        notes.append(f"No clean version was found for: {_listed(explicit)}.")
    if unmarked:
        notes.append(
            "1 song was left out because it isn't marked clean or explicit."
            if unmarked == 1
            else f"{unmarked} songs were left out because they aren't marked clean or explicit."
        )
    return shown, " ".join(notes) or None


def clean_found(found: dict[str, Any], *, allow_explicit: bool) -> dict[str, Any]:
    """One answer of `import.find` for a child's profile: a song that was found, but
    not clean, counts as not found, and says why."""
    candidate = found.get("candidate")
    if found.get("state") not in ("found", "unsure") or not isinstance(candidate, dict):
        return found
    if is_clean(candidate):
        return found
    if allow_explicit:
        return {**found, "candidate": {**candidate, "only_explicit": True}}
    return {"state": "not_found", "why": "No clean version of this song was found."}
