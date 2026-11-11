"""TMDB (The Movie Database), with the owner's own key (2026-11-05): what the add-ons
don't say about a film or a series. It's the only module that talks to TMDB, and it only
asks questions:

- **facts**: a title's original language (`original_language`) and its Australian rating
  (the owner chose Australia: a film's certification in `release_dates`, a series' rating
  in `content_ratings`; where Australia has none, the UK's, then the US's, counted as the
  Australian class each is closest to: 2026-11-10), for Movie Finder's and Series Finder's
  language filter and for a child's profile's ratings;
- **lists**: TMDB's Popular, Top Rated, Upcoming and Trending films and series, for the
  Finders' list menus (`catalog`, in the shape of `addon.catalog`), by TMDB's own genres
  (`genres`) and the original language chosen;
- **details**: a title's page (its name, pictures, summary, genres, length, who made it and
  its trailer), its cast with their photos, and a film's budget and revenue;
- **episodes**: a series' seasons and episodes, for one no plugin has episodes of;
- **search**: films or series by name;

TMDB is **the standard plugin, first in the list** (the owner, 2026-11-11: "lets add standard
plugin, TMDB. It wont work if not Connected to API, it will be number 1 on list. Anything it
cant get will come from other plugins"): the app shows TMDB's lists first, a title's page from
TMDB first with the plugins' for whatever it hasn't, and TMDB's finds first in a search. A
series' episodes stay the plugins' whenever they have any: the ways to play, what's watched
and the next episode go by their numbering ("tt0944947:1:2"), which TMDB's differs from for
some series. It's never a plugin of the add-on protocol, and never asked for a way to play.
- **people**: an actor's photo, biography and every film and series they're in;
- **the calendar**: the month's films released in Australia and new series, by popularity.

A title is known to the add-ons by its IMDb id ("tt0111161"), and asked of TMDB by it: a
film's rating in one question, `/movie/<IMDb id>` with its releases appended (2026-11-09: TMDB
takes a film's IMDb id there, checked with the owner's key; until then `/find`, then the
releases, two questions in a row), and a series by `/find`, then its ratings; TMDB's own ids
("tmdb:550") are asked directly. What TMDB lists by its own id
is given as "tmdb:<number>", and `imdb_id` turns one into the IMDb id its page is opened by.

**The key** is the computer's, not a person's (every profile, a child's too, is served by
it): `accounts.json` in the app's settings folder, under the computer's own keys, readable
by this user only, never in the repo or the library. TMDB's "API Read Access Token" goes in
a header; the shorter "API Key" can only go in the address, so `_http` never passes on the
words of a network error, which hold the address. Neither is ever logged, given out over
RPC, or put in an error. **The one place it goes** (the owner, 2026-11-05: "pass over to
any synced apps ... They all connect to D's inputted api key"): a device paired with this
computer for sharing, of any profile, may ask for it on the home network (`for_devices`,
`sharing`'s `GET /sync/v1/tmdb`), so its own TMDB features use the same key.

What TMDB says of a title's facts (its TMDB id, original language and Australian rating) is
kept in the app's cache folder, `tmdb/facts.json`, written only by `fileops.write_cached`, so a
title is asked about once, not again each time the app opens (the owner's yes, 2026-11-07, after
a child's Movie Finder was "extremely slow to find movies"; until then it was kept in memory
only). TMDB having no rating for a title, or not knowing it, is believed for `UNRATED_FOR_S` and
then asked again: a new film is often rated in Australia a few weeks after it's listed. The rest
(lists, a title's page, people, the calendar) is kept in memory while the engine runs. At most
`TOGETHER` questions are asked at once, whoever asks (`_gate`), over connections kept open
(`_session`, 2026-11-09: a question on a new connection took about 600 ms from the owner's
Mac, 230 ms on one kept open). What's learned is written at most every `WRITE_EVERY_S` while
answers come, and that long after the last (`_write_soon`): the app asks steadily, a title a
question.
In replay mode (`MUSICORG_REPLAY_DIR`, every test) the network is never reached.

Checked against TMDB's documentation on 2026-11-05 (v3: `authentication`, `find`,
`movie/{id}` with `append_to_response`, `release_dates`, `tv/{id}/content_ratings`,
`movie/popular`, `top_rated`, `upcoming`, `discover/movie`, `discover/tv`, `person/{id}`
with `combined_credits`, `external_ids`) and its error answer, which was seen for real
without a key: `{"status_code": 7, "status_message": "Invalid API key: …", "success": false}`
with HTTP 401. No answer with a key has been recorded: the owner's key is theirs to type.
"""

from __future__ import annotations

import calendar as month_days
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

from musicorg import config, fileops
from musicorg.errors import MusicOrgError, ReplayMissError, UserError

log = logging.getLogger(__name__)

SERVICE = "tmdb"
API = "https://api.themoviedb.org/3"
IMAGES = "https://image.tmdb.org/t/p/"
USER_AGENT = "MusicOrganizer (a personal media library app)"
TIMEOUT_S = 15
TOGETHER = 8  # questions asked at once (four until 2026-11-07, the owner's yes)
UNRATED_FOR_S = 14 * 24 * 3600  # "no Australian rating", or "not known", believed this long
WRITE_EVERY_S = 2.0  # what's learned is written at most this often, and this long after
FACTS_FOLDER = "tmdb"  # in the app's cache folder
FACTS_FILE = "facts.json"
FACTS_FORMAT = 1
REGION = "AU"  # the owner's choice, 2026-11-05: Australian ratings and release dates
REPLAY_ENV = "MUSICORG_REPLAY_DIR"
# For a check only: TMDB's address in place of the real one (a stand-in on this computer),
# so screens can be pictured without anyone's key.
API_ENV = "MUSICORG_TMDB_API"
WHERE = "Settings → Plugins → TMDB"
API_KEY = re.compile(r"[0-9a-f]{32}")
TOKEN_PART = re.compile(r"[A-Za-z0-9_\-]+")
IMDB_ID = re.compile(r"tt\d{1,12}")
TMDB_ID = re.compile(r"tmdb:(\d{1,12})")
PAGE_SIZE = 20  # TMDB's pages
MOST_PAGES = 25  # of one list: 500 titles
MOST_FACTS = 200  # titles in one `facts` question
CAST = 20  # people shown on a title's page
DIRECTORS = 4  # who made a title, on its page
SEASONS_AT_ONCE = 20  # a series' seasons asked in one question (TMDB's own limit)
MOST_GENRES = 2  # a list asked by every one of these genres at once
CREDITS = 80  # a person's films and series shown
CALENDAR_FILM_PAGES = 3
CALENDAR_SERIES_PAGES = 2
# The kinds of release a film counts on the calendar by (TMDB's numbers): limited
# theatrical, theatrical, digital.
RELEASE_KINDS = (2, 3, 4)

# The Finders' TMDB lists (docs/ENGINE_API.md → "TMDB list"). Trending (2026-11-11) is the
# week's, and can't be asked by genre or language: TMDB's trending takes neither.
LISTS = ("popular", "top_rated", "upcoming", "trending")
LIST_NAMES = {
    "popular": "TMDB – Popular",
    "top_rated": "TMDB – Top Rated",
    "upcoming": "TMDB – Upcoming",
    "trending": "TMDB – Trending",
}
LANGUAGE_CODE = re.compile(r"[a-z]{2}")  # an original language, as TMDB has it ("en", "cn")

# Australian ratings as the app offers them to tick (docs/ENGINE_API.md → "Australian
# rating"): G, PG, M, MA15+, R18+. A series' P and C (for children) count as G, and its
# AV15+ as MA15+. Anything else TMDB gives (E for exempt, RC, X18+, CTC) is kept as it is,
# and is never one of those the app lets be ticked.
RATINGS = ("G", "PG", "M", "MA15+", "R18+")
SAME_AS = {"P": "G", "C": "G", "AV15+": "MA15+", "MA 15+": "MA15+", "R 18+": "R18+"}
NOT_A_RATING = {"", "E", "CTC", "NR"}

# Where Australia has no rating for a title, the UK's, and where the UK has none either, the
# US's (the owner, 2026-11-10: "Australia primary, UK secondary, US tertiary"), each counted
# as the Australian class it's closest to. A film's and a series' (TV) ratings are both here.
# Anything else these two give (E, TBC, NR) is no rating, and the next country is asked.
FALLBACK = ("GB", "US")
AS_AUSTRALIAN = {
    "GB": {
        "U": "G", "UC": "G", "PG": "PG", "12A": "M", "12": "M", "15": "MA15+",
        "18": "R18+", "R18": "R18+",
    },
    "US": {
        "G": "G", "TV-Y": "G", "TV-G": "G",
        "PG": "PG", "TV-Y7": "PG", "TV-Y7-FV": "PG", "TV-PG": "PG",
        "PG-13": "M", "TV-14": "M", "R": "MA15+", "TV-MA": "MA15+", "NC-17": "R18+",
    },
}  # fmt: skip
# Which rule the facts file's "no rating" were found by: one found by an older rule (before
# the UK and the US were asked) is forgotten, so the title is asked about again.
RATING_RULE = 2


class TmdbError(UserError):
    """TMDB said no, or couldn't be reached. The message is for the owner."""


class _NotFound(TmdbError):
    """TMDB has no such title or person."""


# ---- the one place the network is reached ------------------------------------------------


def _http(path: str, params: dict[str, Any], key: str) -> tuple[int, Any]:
    """One question to TMDB: (status, the JSON body or None)."""
    if os.environ.get(REPLAY_ENV):
        raise ReplayMissError("Replay mode: TMDB is never reached. Stand in for tmdb._http.")
    import requests

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    asked = dict(params)
    if is_token(key):
        headers["Authorization"] = f"Bearer {key}"
    else:
        asked["api_key"] = key
    address = (os.environ.get(API_ENV) or API) + path
    try:
        try:
            response = _session().get(address, params=asked, headers=headers, timeout=TIMEOUT_S)
        except requests.exceptions.ConnectionError:
            # A connection kept open that TMDB has since closed: asked once more, afresh.
            response = _session().get(address, params=asked, headers=headers, timeout=TIMEOUT_S)
    except requests.exceptions.RequestException:
        # Not the exception's own words: they hold the address, and the key may be in it.
        raise TmdbError(
            "TMDB couldn't be reached. Check the internet connection and try again."
        ) from None
    try:
        body = response.json()
    except ValueError:
        body = None
    return response.status_code, body


_sessions_lock = threading.Lock()
_kept_session: Any = None
# However many ask at once (the app's steady questions, a list, a page), `TOGETHER` at most.
_gate = threading.BoundedSemaphore(TOGETHER)


def _session() -> Any:
    """The connections to TMDB, kept open between questions (made the first time)."""
    global _kept_session
    with _sessions_lock:
        if _kept_session is None:
            import requests
            from requests.adapters import HTTPAdapter

            made = requests.Session()
            made.mount("https://", HTTPAdapter(pool_connections=2, pool_maxsize=TOGETHER * 2))
            made.mount("http://", HTTPAdapter(pool_connections=2, pool_maxsize=TOGETHER * 2))
            _kept_session = made
        return _kept_session


def _ask(path: str, key: str | None = None, **params: Any) -> dict[str, Any]:
    """A question with the key saved (or `key`), its answer read, or a TmdbError."""
    with _gate:
        status_code, body = _http(path, params, key or _saved())
    if status_code == 200 and isinstance(body, dict):
        return body
    said = body.get("status_message") if isinstance(body, dict) else None
    if status_code == 401:
        raise TmdbError(
            "TMDB didn't accept the key. Copy the API Read Access Token (or the API Key) "
            f"again from themoviedb.org → Settings → API, and paste it in {WHERE}."
        )
    if status_code == 404:
        raise _NotFound("TMDB has no such title.")
    if status_code == 429:
        raise TmdbError("TMDB asked us to slow down. Try again in a moment.")
    words = f": “{said}”" if isinstance(said, str) and said and "key" not in said.lower() else ""
    raise TmdbError(f"TMDB answered with an error (HTTP {status_code}){words}.")


# ---- the key ------------------------------------------------------------------------------


def is_token(key: str) -> bool:
    """Whether a key is TMDB's API Read Access Token (three dotted parts, long), which goes
    in a header, rather than the 32-character API Key, which goes in the address."""
    parts = key.split(".")
    return len(parts) == 3 and len(key) > 60 and all(TOKEN_PART.fullmatch(part) for part in parts)


def tidy(pasted: str) -> str:
    """The key as pasted, made one TMDB takes, or a TmdbError saying what's wanted."""
    key = pasted.strip()
    if API_KEY.fullmatch(key.lower()):
        return key.lower()
    if is_token(key):
        return key
    raise TmdbError(
        "That doesn't look like a TMDB key. At themoviedb.org → Settings → API, copy the API "
        "Read Access Token (the long one) or the API Key (32 letters and digits)."
    )


def _saved() -> str:
    key = config.load_computer_account(SERVICE).get("key")
    if not isinstance(key, str) or not key:
        raise TmdbError(f"TMDB isn't set up yet. Paste your TMDB key in {WHERE}.")
    return key


def for_devices() -> str | None:
    """The key, for a paired device that asks for it (`sharing`), or None when there's
    none. Never logged."""
    key = config.load_computer_account(SERVICE).get("key")
    return key if isinstance(key, str) and key else None


def status() -> dict[str, Any]:
    """Whether a key is saved, for the app. Never the key."""
    key = config.load_computer_account(SERVICE).get("key")
    return {"has_key": isinstance(key, str) and bool(key)}


def connect(pasted: str) -> dict[str, Any]:
    """Save the owner's TMDB key, once TMDB has accepted it."""
    key = tidy(pasted)
    _ask("/authentication", key)
    config.save_computer_account(SERVICE, {"key": key})
    log.info("TMDB: a key was saved for this computer.")
    return status()


def forget() -> dict[str, Any]:
    config.save_computer_account(SERVICE, None)
    _forget_answers()
    return status()


# ---- what's kept while the engine runs ------------------------------------------------------

_lock = threading.Lock()
# By IMDb id: (TMDB's id, "movie" or "tv", original language), or None when TMDB hasn't it.
_found: dict[str, tuple[int, str, str | None] | None] = {}
# By (TMDB's id, "movie" or "tv"): the original language, the Australian rating, the IMDb id.
_languages: dict[tuple[int, str], str | None] = {}
_ratings: dict[tuple[int, str], str | None] = {}
_imdb: dict[tuple[int, str], str | None] = {}
_au_dates: dict[int, list[tuple[str, int]]] = {}  # a film's Australian (day, kind) releases
_genres: dict[str, dict[int, str]] = {}
# When TMDB was found not to know an IMDb id, or to have no Australian rating for a title.
_missing_at: dict[str, float] = {}
_unrated_at: dict[tuple[int, str], float] = {}
# The facts file: read once, and written after a question that learned something, at most
# every WRITE_EVERY_S (`_write_soon`).
_facts_read = False
_facts_changed = False
_written_at = 0.0  # time.monotonic() of the last writing
_write_timer: threading.Timer | None = None


def _forget_answers() -> None:
    global _facts_read, _facts_changed, _write_timer, _written_at
    with _lock:
        if _write_timer is not None:
            _write_timer.cancel()
            _write_timer = None
        _written_at = 0.0
        for kept in (_found, _languages, _ratings, _imdb, _au_dates, _genres):
            kept.clear()
        _missing_at.clear()
        _unrated_at.clear()
        _facts_read = _facts_changed = False


def _facts_path() -> tuple[Path, Path]:
    """(the facts file, the app's cache folder)."""
    cache = config.app_dirs().cache
    return cache / FACTS_FOLDER / FACTS_FILE, cache


def _fresh(when: float | None) -> bool:
    return when is not None and time.time() - when < UNRATED_FOR_S


def _read_facts() -> None:
    """The facts kept from before, once, the first time any is wanted (the lock is held).
    A file that can't be read is as none: it's only what TMDB would say again."""
    global _facts_read
    if _facts_read:
        return
    _facts_read = True
    path, _ = _facts_path()
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(kept, dict) or kept.get("format") != FACTS_FORMAT:
        return

    def title(key: Any) -> tuple[int, str] | None:
        media, _, number = str(key).partition(":")
        return (int(number), media) if media in ("movie", "tv") and number.isdigit() else None

    for imdb_id, found in (kept.get("found") or {}).items():
        if not IMDB_ID.fullmatch(str(imdb_id)):
            continue
        whole = isinstance(found, list) and len(found) == 3
        if whole and isinstance(found[0], int) and found[1] in ("movie", "tv"):
            language = found[2] if isinstance(found[2], str) else None
            _found.setdefault(imdb_id, (found[0], found[1], language))
            _imdb.setdefault((found[0], found[1]), imdb_id)
    for imdb_id, when in (kept.get("missing") or {}).items():
        if IMDB_ID.fullmatch(str(imdb_id)) and isinstance(when, (int, float)) and _fresh(when):
            _found.setdefault(imdb_id, None)
            _missing_at.setdefault(imdb_id, float(when))
    for key, language in (kept.get("languages") or {}).items():
        made = title(key)
        if made and (language is None or isinstance(language, str)):
            _languages.setdefault(made, language)
    for key, said in (kept.get("ratings") or {}).items():
        made = title(key)
        if made and isinstance(said, str):
            _ratings.setdefault(made, said)
    older_rule = kept.get("rule") != RATING_RULE
    for key, when in ({} if older_rule else kept.get("unrated") or {}).items():
        made = title(key)
        if made and isinstance(when, (int, float)) and _fresh(when):
            _ratings.setdefault(made, None)
            _unrated_at.setdefault(made, float(when))


def _write_soon() -> None:
    """What was learned written now, or `WRITE_EVERY_S` after it last was when that's later:
    at most that often while answers come, and never put off for ever (the tablet app's
    lesson, 2026-11-07: put off at every answer, it was never written)."""
    global _write_timer
    with _lock:
        if not _facts_changed or _write_timer is not None:
            return
        wait = _written_at + WRITE_EVERY_S - time.monotonic()
        if wait > 0:
            _write_timer = threading.Timer(wait, _timed_write)
            _write_timer.daemon = True
            _write_timer.start()
            return
    _write_facts()


def _timed_write() -> None:
    global _write_timer
    with _lock:
        _write_timer = None
    _write_facts()


def flush() -> None:
    """Whatever was learned and not yet written, written now (the engine stopping)."""
    global _write_timer
    with _lock:
        if _write_timer is not None:
            _write_timer.cancel()
            _write_timer = None
    _write_facts()


def _write_facts() -> None:
    """The facts kept, if anything was learned since they last were. Never fails a question:
    a file that can't be written is only asked about again."""
    global _facts_changed, _written_at
    with _lock:
        if not _facts_changed:
            return
        _facts_changed = False
        _written_at = time.monotonic()
        kept = {
            "format": FACTS_FORMAT,
            "rule": RATING_RULE,
            "found": {k: [v[0], v[1], v[2]] for k, v in _found.items() if v is not None},
            "missing": {k: when for k, when in _missing_at.items() if _found.get(k, 0) is None},
            "languages": {f"{m}:{n}": lang for (n, m), lang in _languages.items()},
            "ratings": {f"{m}:{n}": said for (n, m), said in _ratings.items() if said is not None},
            "unrated": {
                f"{m}:{n}": when
                for (n, m), when in _unrated_at.items()
                if _ratings.get((n, m), "") is None
            },
        }
    path, cache = _facts_path()
    try:
        fileops.make_cached_folder(path.parent, cache=cache)
        written = json.dumps(kept, separators=(",", ":")).encode("utf-8")
        fileops.write_cached(path, written, cache=cache)
    except (MusicOrgError, OSError, ValueError) as exc:
        log.warning("TMDB: what it said couldn't be kept: %s", exc)


def _each(work: Callable[[Any], Any], things: Iterable[Any]) -> list[Any]:
    """`work` done for each thing, `TOGETHER` at a time, in their order. A TmdbError for
    one is that one's answer (None); the key being refused stops them all."""
    listed = list(things)
    if not listed:
        return []

    def guarded(thing: Any) -> Any:
        try:
            return work(thing)
        except _NotFound:
            return None
        except TmdbError as exc:
            if _refused(exc):
                raise
            return None

    with ThreadPoolExecutor(max_workers=TOGETHER) as pool:
        return list(pool.map(guarded, listed))


def _media(kind: str) -> str:
    """TMDB's word for a kind: "tv" for a series, "movie" for anything else."""
    return "tv" if kind == "series" else "movie"


def _find(imdb_id: str, kind: str) -> tuple[int, str, str | None] | None:
    """A title by its IMDb id: (TMDB's id, its kind there, its original language)."""
    global _facts_changed
    with _lock:
        _read_facts()
        if imdb_id in _found:
            known = _found[imdb_id]
            if known is not None or _fresh(_missing_at.get(imdb_id)):
                return known
    body = _ask(f"/find/{imdb_id}", external_source="imdb_id")
    order = ["tv_results", "movie_results"] if kind == "series" else ["movie_results", "tv_results"]
    found: tuple[int, str, str | None] | None = None
    for name in order:
        results = body.get(name)
        if isinstance(results, list) and results and isinstance(results[0], dict):
            first = results[0]
            if isinstance(first.get("id"), int):
                language = _language(first.get("original_language"))
                found = (first["id"], "tv" if name == "tv_results" else "movie", language)
                break
    with _lock:
        _found[imdb_id] = found
        if found:
            _languages[(found[0], found[1])] = found[2]
            _imdb[(found[0], found[1])] = imdb_id
            _missing_at.pop(imdb_id, None)
        else:
            _missing_at[imdb_id] = time.time()
        _facts_changed = True
    return found


def _tmdb_of(title_id: str, kind: str) -> tuple[int, str] | None:
    """TMDB's id and kind for an add-on's id ("tt…") or one of its own ("tmdb:550")."""
    own = TMDB_ID.fullmatch(title_id)
    if own:
        return int(own.group(1)), _media(kind)
    if IMDB_ID.fullmatch(title_id):
        found = _find(title_id, kind)
        return (found[0], found[1]) if found else None
    return None


def _language(value: Any) -> str | None:
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


def rating(value: Any) -> str | None:
    """An Australian rating as the app knows it ("MA15+"), or None for none known."""
    if not isinstance(value, str):
        return None
    said = value.strip().upper()
    if said in NOT_A_RATING:
        return None
    return SAME_AS.get(said, said)


def _as_australian(country: str, value: Any) -> str | None:
    """A country's rating as an Australian class: Australia's own, tidied, or the UK's or
    the US's counted as the class it's closest to (`AS_AUSTRALIAN`); None for none."""
    if country == REGION:
        return rating(value)
    if not isinstance(value, str):
        return None
    return AS_AUSTRALIAN.get(country, {}).get(value.strip().upper())


def _strictest(classes: Iterable[str]) -> str:
    """The strictest of some Australian classes (one the app doesn't offer, such as X18+,
    stricter than all)."""
    return max(classes, key=lambda one: RATINGS.index(one) if one in RATINGS else len(RATINGS))


def _first_country(said: dict[str, list[tuple[bool, str]]]) -> str | None:
    """A title's rating by the owner's order, Australia's, else the UK's, else the US's:
    `said` is each country's classes, (in cinemas, class). Within a country its cinema
    releases count first, and of those that count, the strictest."""
    for country in (REGION, *FALLBACK):
        classes = said.get(country) or []
        counted = [one for cinema, one in classes if cinema] or [one for _, one in classes]
        if counted:
            return _strictest(counted)
    return None


# ---- facts: original language and Australian rating -------------------------------------


def facts(titles: list[dict[str, Any]], want: Iterable[str]) -> dict[str, dict[str, str | None]]:
    """Each title's original language and Australian rating (as `want` asks), by its id:
    `{id: {"language", "rating"}}`, a value None when TMDB doesn't say. A title TMDB
    can't be asked about (an id of another kind) has both None. A title TMDB couldn't be
    asked about just now (no connection, TMDB busy or failing) is left out, to be asked
    again (2026-11-09: given as None, it was hidden from a child as "not rated" until the
    app was opened again)."""
    wanted = set(want) & {"language", "rating"}
    _saved()  # not set up: said once, not for every title
    asked = [
        (t["id"], t.get("type") or "movie")
        for t in titles[:MOST_FACTS]
        if isinstance(t, dict) and isinstance(t.get("id"), str)
    ]

    def one(pair: tuple[str, str]) -> dict[str, str | None] | None:
        title_id, kind = pair
        said: dict[str, str | None] = {"language": None, "rating": None}
        try:
            if "rating" in wanted and kind != "series" and IMDB_ID.fullmatch(title_id):
                _film_at_once(title_id)
            found = _tmdb_of(title_id, kind)
            if not found:
                return said
            if "language" in wanted:
                said["language"] = _language_of(*found)
            if "rating" in wanted:
                said["rating"] = _rating_of(*found)
        except _NotFound:
            return {"language": None, "rating": None}
        except TmdbError as exc:
            if _refused(exc):
                raise
            return None  # not known now: left out, and asked again
        return said

    try:
        answers = _each(one, asked)
    finally:
        _write_soon()
    return {
        pair[0]: answer for pair, answer in zip(asked, answers, strict=True) if answer is not None
    }


def _refused(exc: TmdbError) -> bool:
    """The key itself was turned away: every question stops."""
    return "didn't accept the key" in str(exc)


def _film_at_once(imdb_id: str) -> None:
    """A film not known yet, asked by its IMDb id with its releases in the same answer: its
    TMDB id, original language and Australian rating in one question, kept as `_find` and
    `_film_releases` keep them. Nothing is asked of one known already, or known not to be
    known; one TMDB has no film for (a series, or nothing) is left to `_find`."""
    global _facts_changed
    with _lock:
        _read_facts()
        if imdb_id in _found:
            known = _found[imdb_id]
            if known is not None or _fresh(_missing_at.get(imdb_id)):
                return
    try:
        body = _ask(f"/movie/{imdb_id}", append_to_response="release_dates")
    except _NotFound:
        return
    tmdb_id = body.get("id")
    if not isinstance(tmdb_id, int):
        return
    language = _language(body.get("original_language"))
    with _lock:
        _found[imdb_id] = (tmdb_id, "movie", language)
        _languages[(tmdb_id, "movie")] = language
        _imdb[(tmdb_id, "movie")] = imdb_id
        _missing_at.pop(imdb_id, None)
        _facts_changed = True
    releases = body.get("release_dates")
    if isinstance(releases, dict):
        _keep_releases(tmdb_id, releases)
        with _lock:
            _note_rating((tmdb_id, "movie"), _ratings.get((tmdb_id, "movie")))


def _language_of(tmdb_id: int, media: str) -> str | None:
    global _facts_changed
    with _lock:
        _read_facts()
        if (tmdb_id, media) in _languages:
            return _languages[(tmdb_id, media)]
    body = _ask(f"/{media}/{tmdb_id}")
    language = _language(body.get("original_language"))
    with _lock:
        _languages[(tmdb_id, media)] = language
        _facts_changed = True
    return language


def _rating_of(tmdb_id: int, media: str) -> str | None:
    global _facts_changed
    with _lock:
        _read_facts()
        if (tmdb_id, media) in _ratings:
            said = _ratings[(tmdb_id, media)]
            if said is not None or _fresh(_unrated_at.get((tmdb_id, media))):
                return said
            # None for a fortnight: asked again, with the film's releases.
            _au_dates.pop(tmdb_id, None)
    if media == "movie":
        _film_releases(tmdb_id)
        with _lock:
            said = _ratings.get((tmdb_id, media))
            _note_rating((tmdb_id, media), said)
            _facts_changed = True
            return said
    body = _ask(f"/tv/{tmdb_id}/content_ratings")
    said: dict[str, list[tuple[bool, str]]] = {}
    for entry in body.get("results") or []:
        if not isinstance(entry, dict):
            continue
        country = entry.get("iso_3166_1")
        one = _as_australian(country, entry.get("rating")) if isinstance(country, str) else None
        if one:
            said.setdefault(country, []).append((True, one))
    found = _first_country(said)
    with _lock:
        _ratings[(tmdb_id, media)] = found
        _note_rating((tmdb_id, media), found)
        _facts_changed = True
    return found


def _note_rating(key: tuple[int, str], said: str | None) -> None:
    """When TMDB had no rating for a title, noted (the lock is held)."""
    if said is None:
        _unrated_at[key] = time.time()
    else:
        _unrated_at.pop(key, None)


def _film_releases(tmdb_id: int) -> list[tuple[str, int]]:
    """A film's Australian releases, (day, kind), and its Australian rating kept (the
    theatrical release's, else the first given)."""
    with _lock:
        if tmdb_id in _au_dates:
            return _au_dates[tmdb_id]
    return _keep_releases(tmdb_id, _ask(f"/movie/{tmdb_id}/release_dates"))


def _keep_releases(tmdb_id: int, body: dict[str, Any]) -> list[tuple[str, int]]:
    """A film's `release_dates` answer read: its Australian releases kept, and its rating
    (Australia's, else the UK's, else the US's: `_first_country`)."""
    releases: list[tuple[str, int]] = []
    said: dict[str, list[tuple[bool, str]]] = {}
    for country in body.get("results") or []:
        where = country.get("iso_3166_1") if isinstance(country, dict) else None
        if where not in (REGION, *FALLBACK):
            continue
        for one in country.get("release_dates") or []:
            if not isinstance(one, dict):
                continue
            kind = one.get("type") if isinstance(one.get("type"), int) else 0
            day = one.get("release_date")
            if where == REGION and isinstance(day, str) and len(day) >= 10:
                releases.append((day[:10], kind))
            rated = _as_australian(where, one.get("certification"))
            if rated:
                said.setdefault(where, []).append((kind == 3, rated))
    found = _first_country(said)
    with _lock:
        _au_dates[tmdb_id] = releases
        _ratings[(tmdb_id, "movie")] = found
    return releases


# ---- the Finders' lists ---------------------------------------------------------------------


def catalog(
    kind: str,
    list_id: str,
    skip: int = 0,
    genres: list[str] | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """One page of TMDB's Popular, Top Rated, Upcoming or Trending films ("movie") or
    series ("series"), in the shape of `addon.catalog`: `{items, more, next_skip}`. Each
    title is given by its IMDb id, so it opens on its page as any other does; one TMDB has
    no IMDb id for is left out.

    By `genres` (TMDB's genre names, `genres()`: every one of them, so two genres are one
    question, 2026-11-11) or an original `language` ("en"), TMDB's discover is asked, sorted
    as the list is. A genre TMDB hasn't (one chosen in another plugin's list) has nothing:
    an empty page. Trending takes neither."""
    if kind not in ("movie", "series"):
        raise TmdbError("TMDB's lists are of films or series.")
    if list_id not in LISTS:
        raise TmdbError(f"A TMDB list is one of: {', '.join(LISTS)}.")
    if language is not None and not LANGUAGE_CODE.fullmatch(language):
        raise TmdbError("An original language is two letters, such as “en”.")
    page = max(1, skip // PAGE_SIZE + 1)
    media = _media(kind)
    wanted = [name for name in genres or [] if name.strip()]
    params: dict[str, Any] = {"page": page, "language": "en-US"}
    if list_id == "trending":
        path = f"/trending/{media}/week"
    elif wanted or language:
        numbers = _genre_ids(wanted, media)
        if numbers is None:
            return {"items": [], "more": False, "next_skip": skip}
        path = f"/discover/{media}"
        params.update(_discover_order(list_id, media))
        if numbers:
            params["with_genres"] = ",".join(str(n) for n in numbers)  # TMDB's comma is "and"
        if language:
            params["with_original_language"] = language
    elif media == "movie":
        path = f"/movie/{list_id}"
        params["region"] = REGION
    elif list_id == "upcoming":
        # TMDB has no list of series to come: the ones whose first episode is still ahead,
        # the most talked about first.
        path = "/discover/tv"
        params.update(_discover_order(list_id, media))
    else:
        path = f"/tv/{list_id}"
    body = _ask(path, **params)
    items = _items(body.get("results"), kind)
    pages = body.get("total_pages") if isinstance(body.get("total_pages"), int) else page
    return {"items": items, "more": page < min(pages, MOST_PAGES), "next_skip": page * PAGE_SIZE}


def _discover_order(list_id: str, media: str) -> dict[str, Any]:
    """What TMDB's discover is asked with to give a list's titles in the list's own order:
    the most talked about first, the best scored (by enough people, as TMDB's own Top Rated
    is), or those still to come out (a film in Australia, in cinemas)."""
    if list_id == "top_rated":
        return {"sort_by": "vote_average.desc", "vote_count.gte": 200 if media == "tv" else 300}
    if list_id == "upcoming":
        today = date.today().isoformat()
        if media == "tv":
            return {"first_air_date.gte": today, "sort_by": "popularity.desc"}
        return {
            "primary_release_date.gte": today, "region": REGION,
            "with_release_type": "2|3", "sort_by": "popularity.desc",
        }  # fmt: skip
    return {"sort_by": "popularity.desc"}


def _genre_ids(names: list[str], media: str) -> list[int] | None:
    """TMDB's numbers for some of its genre names (any case), or None when it hasn't one of
    them."""
    if not names:
        return []
    by_name = {name.casefold(): number for number, name in _genre_names(media).items()}
    numbers = [by_name.get(name.strip().casefold()) for name in names]
    return None if None in numbers else [n for n in numbers if n is not None]


def genres(kind: str) -> list[str]:
    """TMDB's genres for films ("movie") or series ("series"), by name, A to Z: what its
    lists can be asked by (the app's genre menus, 2026-11-11)."""
    if kind not in ("movie", "series"):
        raise TmdbError("TMDB's genres are of films or series.")
    return sorted(_genre_names(_media(kind)).values(), key=str.casefold)


def search(kind: str, query: str) -> dict[str, Any]:
    """Films or series TMDB finds for some words (its first page: its best twenty), as
    `{items}` in the shape of `addon.search`'s: by their IMDb ids, as `catalog`'s are. Only
    those with a poster: the rest are TMDB's obscure ones, blank cards in a search."""
    if kind not in ("movie", "series"):
        raise TmdbError("TMDB searches films or series.")
    words = query.strip()
    if not words:
        return {"items": []}
    body = _ask(
        f"/search/{_media(kind)}", query=words, include_adult="false", language="en-US", page=1
    )
    results = [r for r in body.get("results") or [] if isinstance(r, dict)]
    with_poster = [r for r in results if _picture(r.get("poster_path"), "w342")]
    return {"items": _items(with_poster, kind)}


def _items(results: Any, kind: str) -> list[dict[str, Any]]:
    """Titles from one of TMDB's answers (a list's, a search's) as an add-on's `Item`s, by
    their IMDb ids; one TMDB has no IMDb id for is left out. Their languages are noted."""
    listed = [r for r in results or [] if isinstance(r, dict) and isinstance(r.get("id"), int)]
    if not listed:
        return []
    media = _media(kind)
    names = _genre_names(media)
    ids = _each(lambda r: imdb_id(kind, f"tmdb:{r['id']}"), listed)
    items = []
    for result, found in zip(listed, ids, strict=True):
        if not found:
            continue
        language = _language(result.get("original_language"))
        with _lock:
            # Known by its IMDb id too, so the language filter asks nothing more of it.
            _languages[(result["id"], media)] = language
            _found[found] = (result["id"], media, language)
        items.append(_item(result, kind, found, names))
    return items


def _item(
    result: dict[str, Any], kind: str, title_id: str, names: dict[int, str]
) -> dict[str, Any]:
    """A title from TMDB in the shape of an add-on's `Item`."""
    day = result.get("release_date") or result.get("first_air_date")
    score = result.get("vote_average")
    return {
        "id": title_id,
        "type": kind,
        "name": result.get("title") or result.get("name") or "",
        "poster": _picture(result.get("poster_path"), "w342"),
        "poster_shape": "poster",
        "year": day[:4] if isinstance(day, str) and len(day) >= 4 else None,
        "rating": round(float(score), 1) if isinstance(score, int | float) and score > 0 else None,
        "genres": [names[g] for g in result.get("genre_ids") or [] if g in names],
    }


def _picture(path: Any, size: str) -> str | None:
    return f"{IMAGES}{size}{path}" if isinstance(path, str) and path.startswith("/") else None


def _genre_names(media: str) -> dict[int, str]:
    with _lock:
        if media in _genres:
            return _genres[media]
    try:
        body = _ask(f"/genre/{media}/list", language="en-US")
    except _NotFound:
        body = {}
    names = {
        g["id"]: g["name"]
        for g in body.get("genres") or []
        if isinstance(g, dict) and isinstance(g.get("id"), int) and isinstance(g.get("name"), str)
    }
    with _lock:
        _genres[media] = names
    return names


def imdb_id(kind: str, title_id: str) -> str | None:
    """The IMDb id a title's page is opened by: as it is for one, or TMDB's for one of its
    own ("tmdb:550"). None when TMDB has none for it."""
    if IMDB_ID.fullmatch(title_id):
        return title_id
    own = TMDB_ID.fullmatch(title_id)
    if not own:
        return None
    key = (int(own.group(1)), _media(kind))
    with _lock:
        if key in _imdb:
            return _imdb[key]
    body = _ask(f"/{key[1]}/{key[0]}/external_ids")
    found = body.get("imdb_id")
    found = found if isinstance(found, str) and IMDB_ID.fullmatch(found) else None
    with _lock:
        _imdb[key] = found
    return found


# ---- a title's page, its cast, and a film's money ---------------------------------------------

# What a title's page has from TMDB, when TMDB can't be asked about it.
NO_DETAILS: dict[str, Any] = {
    "tmdb_id": None, "cast": [], "budget": None, "revenue": None,
    "name": None, "poster": None, "background": None, "year": None, "rating": None,
    "genres": [], "description": None, "runtime_min": None, "directors": [],
    "trailer_video_id": None,
}  # fmt: skip


def details(kind: str, title_id: str) -> dict[str, Any]:
    """A title's page as TMDB has it (the standard plugin, 2026-11-11), from one question:
    its name, poster, background, year, score, genres, summary (`description`), length
    (`runtime_min`), who made it (`directors`: a film's directors, else a series' creators)
    and its trailer (`trailer_video_id`, TMDB's own on the video service, the official
    first), named as an add-on's `Details` names them, for the app to put first and fill in
    from the plugins; and its cast with their photos (`CAST` at most, in TMDB's order), and
    for a film its budget and revenue in US dollars (None when TMDB doesn't know: it says
    0). No episodes: a series' are the plugins' (`episodes` for one they haven't)."""
    found = _tmdb_of(title_id, kind)
    if not found:
        return dict(NO_DETAILS)
    tmdb_id, media = found
    body = _ask(
        f"/{media}/{tmdb_id}", append_to_response="credits,videos,external_ids", language="en-US"
    )
    imdb = _external_imdb(body)
    with _lock:
        _languages[found] = _language(body.get("original_language"))
        if imdb:
            _imdb[found] = imdb
    credits = body.get("credits") if isinstance(body.get("credits"), dict) else {}
    cast = []
    for person in credits.get("cast") or []:
        if not isinstance(person, dict) or not isinstance(person.get("id"), int):
            continue
        cast.append({
            "person_id": person["id"],
            "name": person.get("name") or "",
            "character": person.get("character") or None,
            "photo": _picture(person.get("profile_path"), "w185"),
        })  # fmt: skip
        if len(cast) >= CAST:
            break

    def money(value: Any) -> int | None:
        return value if isinstance(value, int) and value > 0 else None

    def text(value: Any) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    day = text(body.get("release_date")) or text(body.get("first_air_date"))
    score = body.get("vote_average")
    runtime = body.get("runtime")
    if not isinstance(runtime, int):
        each = body.get("episode_run_time")
        runtime = each[0] if isinstance(each, list) and each and isinstance(each[0], int) else None
    return {
        "tmdb_id": tmdb_id,
        "cast": cast,
        "budget": money(body.get("budget")) if media == "movie" else None,
        "revenue": money(body.get("revenue")) if media == "movie" else None,
        "name": text(body.get("title")) or text(body.get("name")),
        "poster": _picture(body.get("poster_path"), "w500"),
        "background": _picture(body.get("backdrop_path"), "w1280"),
        "year": day[:4] if day and len(day) >= 4 else None,
        "rating": round(float(score), 1) if isinstance(score, int | float) and score > 0 else None,
        "genres": [
            g["name"]
            for g in body.get("genres") or []
            if isinstance(g, dict) and text(g.get("name"))
        ],
        "description": text(body.get("overview")),
        "runtime_min": runtime if isinstance(runtime, int) and runtime > 0 else None,
        "directors": _makers(body, credits, media),
        "trailer_video_id": _trailer(body),
    }


def _external_imdb(body: dict[str, Any]) -> str | None:
    """The IMDb id in a title's appended `external_ids`, if it has one."""
    external = body.get("external_ids")
    found = external.get("imdb_id") if isinstance(external, dict) else None
    return found if isinstance(found, str) and IMDB_ID.fullmatch(found) else None


def _makers(body: dict[str, Any], credits: dict[str, Any], media: str) -> list[str]:
    """Who made a title, for its page: a film's directors (its crew's "Director"), else a
    series' creators; each once, `DIRECTORS` at most."""
    named = [
        person.get("name")
        for person in credits.get("crew") or []
        if isinstance(person, dict) and person.get("job") == "Director"
    ]
    if not named and media == "tv":
        named = [p.get("name") for p in body.get("created_by") or [] if isinstance(p, dict)]
    makers: list[str] = []
    for name in named:
        if isinstance(name, str) and name.strip() and name.strip() not in makers:
            makers.append(name.strip())
    return makers[:DIRECTORS]


def _trailer(body: dict[str, Any]) -> str | None:
    """A title's trailer on the video service, by its id there: TMDB's official one first."""
    videos = body.get("videos")
    results = videos.get("results") if isinstance(videos, dict) else None
    trailers = [
        v
        for v in results or []
        if isinstance(v, dict)
        and v.get("site") == "YouTube"
        and v.get("type") == "Trailer"
        and isinstance(v.get("key"), str)
        and v["key"]
    ]
    official = [v for v in trailers if v.get("official") is True]
    chosen = (official or trailers)[:1]
    return chosen[0]["key"] if chosen else None


def episodes(kind: str, title_id: str) -> dict[str, Any]:
    """A series' seasons and episodes as TMDB has them (2026-11-11), for a series no plugin
    has episodes of: `{videos}`, each by the series' IMDb id with its season and number
    ("tt0944947:1:2"), as the plugins give theirs, so its ways to play are asked of them as
    any episode's are. Specials (season 0) too. A series' seasons are asked `SEASONS_AT_ONCE`
    to a question. Nothing for a film, or a title by any other id: its episodes' ids have
    to be the IMDb id's."""
    if kind != "series" or not IMDB_ID.fullmatch(title_id):
        return {"videos": []}
    found = _tmdb_of(title_id, kind)
    if not found or found[1] != "tv":
        return {"videos": []}
    tmdb_id = found[0]
    body = _ask(f"/tv/{tmdb_id}", language="en-US")
    seasons = sorted({
        s["season_number"]
        for s in body.get("seasons") or []
        if isinstance(s, dict) and isinstance(s.get("season_number"), int)
        and s["season_number"] >= 0
    })  # fmt: skip
    videos: list[dict[str, Any]] = []
    for start in range(0, len(seasons), SEASONS_AT_ONCE):
        batch = seasons[start : start + SEASONS_AT_ONCE]
        answer = _ask(
            f"/tv/{tmdb_id}",
            append_to_response=",".join(f"season/{n}" for n in batch),
            language="en-US",
        )
        for number in batch:
            season = answer.get(f"season/{number}")
            if not isinstance(season, dict):
                continue
            for raw in season.get("episodes") or []:
                video = _episode(raw, title_id)
                if video:
                    videos.append(video)
    videos.sort(key=lambda v: (v["season"], v["episode"]))
    return {"videos": videos}


def _episode(raw: Any, imdb: str) -> dict[str, Any] | None:
    """One of TMDB's episodes in the shape of an add-on's `Video`."""
    if not isinstance(raw, dict):
        return None
    season, number = raw.get("season_number"), raw.get("episode_number")
    if not isinstance(season, int) or not isinstance(number, int):
        return None
    name = raw.get("name")
    aired = raw.get("air_date")
    overview = raw.get("overview")
    return {
        "id": f"{imdb}:{season}:{number}",
        "title": name.strip() if isinstance(name, str) and name.strip() else f"Episode {number}",
        "thumbnail": _picture(raw.get("still_path"), "w300"),
        "released": aired if isinstance(aired, str) and aired else None,
        "video_id": None,
        "season": season,
        "episode": number,
        "overview": overview.strip() if isinstance(overview, str) and overview.strip() else None,
    }


def person(person_id: int) -> dict[str, Any]:
    """Someone from a cast: their name, photo and biography, and the films and series
    they're in (`CREDITS` at most, the most talked about first), each as "tmdb:<id>"
    with its kind ("movie" or "series"), to be opened by `imdb_id`."""
    body = _ask(f"/person/{person_id}", append_to_response="combined_credits", language="en-US")
    combined = (
        body.get("combined_credits") if isinstance(body.get("combined_credits"), dict) else {}
    )
    seen: set[tuple[str, int]] = set()
    credits = []
    entries = [c for c in combined.get("cast") or [] if isinstance(c, dict)]
    entries.sort(
        key=lambda c: (
            -(c.get("popularity") or 0) if isinstance(c.get("popularity"), int | float) else 0
        )
    )
    for entry in entries:
        media = entry.get("media_type")
        if media not in ("movie", "tv") or not isinstance(entry.get("id"), int):
            continue
        if (media, entry["id"]) in seen:
            continue
        seen.add((media, entry["id"]))
        kind = "series" if media == "tv" else "movie"
        day = entry.get("release_date") or entry.get("first_air_date")
        credits.append({
            "id": f"tmdb:{entry['id']}",
            "type": kind,
            "name": entry.get("title") or entry.get("name") or "",
            "poster": _picture(entry.get("poster_path"), "w342"),
            "year": day[:4] if isinstance(day, str) and len(day) >= 4 else None,
            "character": entry.get("character") or None,
        })  # fmt: skip
        if len(credits) >= CREDITS:
            break
    return {
        "id": person_id,
        "name": body.get("name") or "",
        "photo": _picture(body.get("profile_path"), "h632"),
        "biography": body.get("biography") or None,
        "credits": credits,
    }


# ---- the calendar's General --------------------------------------------------------------


def releases(year: int, month: int) -> dict[str, Any]:
    """The month's releases, for the calendar's General (the owner, 2026-11-05): films
    released in Australia in cinemas or online, the most talked about first, each on its
    Australian day; and series whose first episode comes out that month. Each as
    "tmdb:<id>" with its kind, name, poster and day ("2026-11-05")."""
    if not (1 <= month <= 12) or not (1900 <= year <= 2200):
        raise TmdbError("That isn't a month.")
    first = date(year, month, 1).isoformat()
    last = date(year, month, month_days.monthrange(year, month)[1]).isoformat()
    films: list[dict[str, Any]] = []
    for page in range(1, CALENDAR_FILM_PAGES + 1):
        body = _ask(
            "/discover/movie", page=page, region=REGION, sort_by="popularity.desc",
            with_release_type="|".join(str(k) for k in RELEASE_KINDS),
            **{"release_date.gte": first, "release_date.lte": last}, language="en-US",
        )  # fmt: skip
        films += [
            r
            for r in body.get("results") or []
            if isinstance(r, dict) and isinstance(r.get("id"), int)
        ]
        if page >= (body.get("total_pages") or 1):
            break
    days = _each(lambda r: _australian_day(r["id"], first, last), films)
    shown: list[dict[str, Any]] = []
    for film, found in zip(films, days, strict=True):
        # Its day in Australia that month; a film with Australian days but none this
        # month comes out here another month; one with none known, on the day TMDB lists.
        has_days, day = found if found else (False, None)
        if not has_days:
            day = film.get("release_date")
        if isinstance(day, str) and first <= day[:10] <= last:
            shown.append(_release(film, "movie", day[:10]))
    for page in range(1, CALENDAR_SERIES_PAGES + 1):
        body = _ask(
            "/discover/tv", page=page, sort_by="popularity.desc", language="en-US",
            **{"first_air_date.gte": first, "first_air_date.lte": last},
        )  # fmt: skip
        for result in body.get("results") or []:
            day = result.get("first_air_date") if isinstance(result, dict) else None
            if (
                isinstance(result.get("id"), int)
                and isinstance(day, str)
                and first <= day[:10] <= last
            ):
                shown.append(_release(result, "series", day[:10]))
        if page >= (body.get("total_pages") or 1):
            break
    return {"releases": shown}


def _australian_day(tmdb_id: int, first: str, last: str) -> tuple[bool, str | None]:
    """Whether TMDB knows a film's days in Australia (in cinemas or online), and the first
    of them that month, if one is."""
    known = [day for day, kind in _film_releases(tmdb_id) if kind in RELEASE_KINDS]
    days = [day for day in known if first <= day <= last]
    return bool(known), min(days) if days else None


def _release(result: dict[str, Any], kind: str, day: str) -> dict[str, Any]:
    return {
        "id": f"tmdb:{result['id']}",
        "type": kind,
        "name": result.get("title") or result.get("name") or "",
        "poster": _picture(result.get("poster_path"), "w185"),
        "day": day,
    }
