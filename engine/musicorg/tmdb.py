"""TMDB (The Movie Database), with the owner's own key (2026-11-05): what the add-ons
don't say about a film or a series. It's the only module that talks to TMDB, and it only
asks questions:

- **facts**: a title's original language (`original_language`) and its Australian rating
  (the owner chose Australia: a film's certification in `release_dates`, a series' rating
  in `content_ratings`), for Movie Finder's and Series Finder's language filter and for a
  child's profile's ratings;
- **lists**: TMDB's Popular, Top Rated and Upcoming films and series, for the Finders'
  list menus (`catalog`, in the shape of `addon.catalog`);
- **details**: a title's cast with their photos, and a film's budget and revenue;
- **people**: an actor's photo, biography and every film and series they're in;
- **the calendar**: the month's films released in Australia and new series, by popularity.

A title is known to the add-ons by its IMDb id ("tt0111161"), and asked of TMDB by it
(`/find`); TMDB's own ids ("tmdb:550") are asked directly. What TMDB lists by its own id
is given as "tmdb:<number>", and `imdb_id` turns one into the IMDb id its page is opened by.

**The key** is the computer's, not a person's (every profile, a child's too, is served by
it): `accounts.json` in the app's settings folder, under the computer's own keys, readable
by this user only, never in the repo or the library. TMDB's "API Read Access Token" goes in
a header; the shorter "API Key" can only go in the address, so `_http` never passes on the
words of a network error, which hold the address. Neither is ever logged, given out over
RPC, or put in an error.

What TMDB answers is kept in memory while the engine runs (a film's original language and
rating don't change); nothing is written. At most `TOGETHER` questions are asked at once.
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
import logging
import os
import re
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

from musicorg import config
from musicorg.errors import ReplayMissError, UserError

log = logging.getLogger(__name__)

SERVICE = "tmdb"
API = "https://api.themoviedb.org/3"
IMAGES = "https://image.tmdb.org/t/p/"
USER_AGENT = "MusicOrganizer (a personal media library app)"
TIMEOUT_S = 15
TOGETHER = 4  # questions asked at once
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
CREDITS = 80  # a person's films and series shown
CALENDAR_FILM_PAGES = 3
CALENDAR_SERIES_PAGES = 2
# The kinds of release a film counts on the calendar by (TMDB's numbers): limited
# theatrical, theatrical, digital.
RELEASE_KINDS = (2, 3, 4)

# The Finders' TMDB lists (docs/ENGINE_API.md → "TMDB list").
LISTS = ("popular", "top_rated", "upcoming")
LIST_NAMES = {
    "popular": "TMDB – Popular",
    "top_rated": "TMDB – Top Rated",
    "upcoming": "TMDB – Upcoming",
}

# Australian ratings as the app offers them to tick (docs/ENGINE_API.md → "Australian
# rating"): G, PG, M, MA15+, R18+. A series' P and C (for children) count as G, and its
# AV15+ as MA15+. Anything else TMDB gives (E for exempt, RC, X18+, CTC) is kept as it is,
# and is never one of those the app lets be ticked.
RATINGS = ("G", "PG", "M", "MA15+", "R18+")
SAME_AS = {"P": "G", "C": "G", "AV15+": "MA15+", "MA 15+": "MA15+", "R 18+": "R18+"}
NOT_A_RATING = {"", "E", "CTC", "NR"}


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
    try:
        address = (os.environ.get(API_ENV) or API) + path
        response = requests.get(address, params=asked, headers=headers, timeout=TIMEOUT_S)
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


def _ask(path: str, key: str | None = None, **params: Any) -> dict[str, Any]:
    """A question with the key saved (or `key`), its answer read, or a TmdbError."""
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


def _forget_answers() -> None:
    with _lock:
        for kept in (_found, _languages, _ratings, _imdb, _au_dates, _genres):
            kept.clear()


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
            if "didn't accept the key" in str(exc):
                raise
            return None

    with ThreadPoolExecutor(max_workers=TOGETHER) as pool:
        return list(pool.map(guarded, listed))


def _media(kind: str) -> str:
    """TMDB's word for a kind: "tv" for a series, "movie" for anything else."""
    return "tv" if kind == "series" else "movie"


def _find(imdb_id: str, kind: str) -> tuple[int, str, str | None] | None:
    """A title by its IMDb id: (TMDB's id, its kind there, its original language)."""
    with _lock:
        if imdb_id in _found:
            return _found[imdb_id]
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


# ---- facts: original language and Australian rating -------------------------------------


def facts(titles: list[dict[str, Any]], want: Iterable[str]) -> dict[str, dict[str, str | None]]:
    """Each title's original language and Australian rating (as `want` asks), by its id:
    `{id: {"language", "rating"}}`, a value None when TMDB doesn't say. A title TMDB
    can't be asked about (an id of another kind) has both None."""
    wanted = set(want) & {"language", "rating"}
    _saved()  # not set up: said once, not for every title
    asked = [
        (t["id"], t.get("type") or "movie")
        for t in titles[:MOST_FACTS]
        if isinstance(t, dict) and isinstance(t.get("id"), str)
    ]

    def one(pair: tuple[str, str]) -> dict[str, str | None]:
        title_id, kind = pair
        found = _tmdb_of(title_id, kind)
        if not found:
            return {"language": None, "rating": None}
        said: dict[str, str | None] = {"language": None, "rating": None}
        if "language" in wanted:
            said["language"] = _language_of(*found)
        if "rating" in wanted:
            said["rating"] = _rating_of(*found)
        return said

    answers = _each(one, asked)
    return {
        pair[0]: answer if answer else {"language": None, "rating": None}
        for pair, answer in zip(asked, answers, strict=True)
    }


def _language_of(tmdb_id: int, media: str) -> str | None:
    with _lock:
        if (tmdb_id, media) in _languages:
            return _languages[(tmdb_id, media)]
    body = _ask(f"/{media}/{tmdb_id}")
    language = _language(body.get("original_language"))
    with _lock:
        _languages[(tmdb_id, media)] = language
    return language


def _rating_of(tmdb_id: int, media: str) -> str | None:
    with _lock:
        if (tmdb_id, media) in _ratings:
            return _ratings[(tmdb_id, media)]
    if media == "movie":
        _film_releases(tmdb_id)
        with _lock:
            return _ratings.get((tmdb_id, media))
    body = _ask(f"/tv/{tmdb_id}/content_ratings")
    found: str | None = None
    for entry in body.get("results") or []:
        if isinstance(entry, dict) and entry.get("iso_3166_1") == REGION:
            found = rating(entry.get("rating"))
            break
    with _lock:
        _ratings[(tmdb_id, media)] = found
    return found


def _film_releases(tmdb_id: int) -> list[tuple[str, int]]:
    """A film's Australian releases, (day, kind), and its Australian rating kept (the
    theatrical release's, else the first given)."""
    with _lock:
        if tmdb_id in _au_dates:
            return _au_dates[tmdb_id]
    body = _ask(f"/movie/{tmdb_id}/release_dates")
    releases: list[tuple[str, int]] = []
    ratings: list[tuple[int, str]] = []
    for country in body.get("results") or []:
        if not isinstance(country, dict) or country.get("iso_3166_1") != REGION:
            continue
        for one in country.get("release_dates") or []:
            if not isinstance(one, dict):
                continue
            kind = one.get("type") if isinstance(one.get("type"), int) else 0
            day = one.get("release_date")
            if isinstance(day, str) and len(day) >= 10:
                releases.append((day[:10], kind))
            said = rating(one.get("certification"))
            if said:
                ratings.append((0 if kind == 3 else 1, said))
    found = min(ratings)[1] if ratings else None
    with _lock:
        _au_dates[tmdb_id] = releases
        _ratings[(tmdb_id, "movie")] = found
    return releases


# ---- the Finders' lists ---------------------------------------------------------------------


def catalog(kind: str, list_id: str, skip: int = 0) -> dict[str, Any]:
    """One page of TMDB's Popular, Top Rated or Upcoming films ("movie") or series
    ("series"), in the shape of `addon.catalog`: `{items, more, next_skip}`. Each title is
    given by its IMDb id, so it opens on its page as any other does; one TMDB has no IMDb
    id for is left out."""
    if kind not in ("movie", "series"):
        raise TmdbError("TMDB's lists are of films or series.")
    if list_id not in LISTS:
        raise TmdbError(f"A TMDB list is one of: {', '.join(LISTS)}.")
    page = max(1, skip // PAGE_SIZE + 1)
    media = _media(kind)
    params: dict[str, Any] = {"page": page, "language": "en-US"}
    if media == "movie":
        path = f"/movie/{list_id}"
        params["region"] = REGION
    elif list_id == "upcoming":
        # TMDB has no list of series to come: the ones whose first episode is still ahead,
        # the most talked about first.
        path = "/discover/tv"
        params.update(
            {"first_air_date.gte": date.today().isoformat(), "sort_by": "popularity.desc"}
        )
    else:
        path = f"/tv/{list_id}"
    body = _ask(path, **params)
    results = [
        r for r in body.get("results") or [] if isinstance(r, dict) and isinstance(r.get("id"), int)
    ]
    names = _genre_names(media)
    ids = _each(lambda r: imdb_id(kind, f"tmdb:{r['id']}"), results)
    items = []
    for result, found in zip(results, ids, strict=True):
        if not found:
            continue
        language = _language(result.get("original_language"))
        with _lock:
            # Known by its IMDb id too, so the language filter asks nothing more of it.
            _languages[(result["id"], media)] = language
            _found[found] = (result["id"], media, language)
        items.append(_item(result, kind, found, names))
    pages = body.get("total_pages") if isinstance(body.get("total_pages"), int) else page
    return {"items": items, "more": page < min(pages, MOST_PAGES), "next_skip": page * PAGE_SIZE}


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


# ---- a title's page: its cast, and a film's money --------------------------------------------


def details(kind: str, title_id: str) -> dict[str, Any]:
    """A title's cast with their photos (`CAST` at most, in TMDB's order), and for a film
    its budget and revenue in US dollars (None when TMDB doesn't know: it says 0)."""
    found = _tmdb_of(title_id, kind)
    if not found:
        return {"tmdb_id": None, "cast": [], "budget": None, "revenue": None}
    tmdb_id, media = found
    body = _ask(f"/{media}/{tmdb_id}", append_to_response="credits")
    with _lock:
        _languages[found] = _language(body.get("original_language"))
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

    return {
        "tmdb_id": tmdb_id,
        "cast": cast,
        "budget": money(body.get("budget")) if media == "movie" else None,
        "revenue": money(body.get("revenue")) if media == "movie" else None,
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
