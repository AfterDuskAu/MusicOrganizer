"""tmdb: original language, Australian ratings, TMDB's lists, cast, people and the
calendar's releases, with the owner's own key (2026-11-05).

TMDB is never reached: `tmdb._http` is stood in for by answers in the shapes TMDB's
documentation gives (checked 2026-11-05), and its 401 answer as it was really seen without
a key. The keys here are made up while the tests run, so the file holds none.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from test_rpc import Capture, code, out, result, server  # noqa: F401  (fixtures)

from musicorg import config, rpc, tmdb
from musicorg.errors import ReplayMissError

API_KEY = "0123456789abcdef" * 2  # a made-up key in the shape of TMDB's API Key: secrets-ok
TOKEN = ".".join(["a1B2c3" * 6, "d4E5f6" * 8, "g7H8" * 6])  # the shape of a read token
REFUSED = (
    401,
    {
        "status_code": 7,
        "status_message": "Invalid API key: You must be granted a valid key.",
        "success": False,
    },
)


class FakeTmdb:
    """Answers `tmdb._http` from a table of paths, and remembers what was asked."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, dict[str, Any], str]] = []
        self.answers: dict[str, Any] = {"/authentication": {"success": True, "status_code": 1}}

    def __call__(self, path: str, params: dict[str, Any], key: str) -> tuple[int, Any]:
        self.asked.append((path, dict(params), key))
        if key not in (API_KEY, TOKEN):
            return REFUSED
        found = self.answers.get(path)
        if callable(found):
            found = found(params)
        if found is None:
            return 404, {
                "status_code": 34,
                "status_message": "The resource you requested could not be found.",
            }
        return 200, found

    def paths(self) -> list[str]:
        return [path for path, _, _ in self.asked]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeTmdb:
    found = FakeTmdb()
    monkeypatch.setattr(tmdb, "_http", found)
    tmdb._forget_answers()
    return found


def set_up() -> None:
    config.save_computer_account("tmdb", {"key": API_KEY})


def film_found(tmdb_id: int, language: str) -> dict[str, Any]:
    return {
        "movie_results": [{"id": tmdb_id, "title": "A Film", "original_language": language}],
        "tv_results": [],
        "person_results": [],
    }


def au_releases(*entries: tuple[str, int, str]) -> dict[str, Any]:
    """`/movie/{id}/release_dates` with Australia's releases: (day, kind, certification)."""
    return {
        "id": 1,
        "results": [
            {
                "iso_3166_1": "US",
                "release_dates": [
                    {
                        "certification": "PG-13",
                        "release_date": "2026-01-01T00:00:00.000Z",
                        "type": 3,
                    }
                ],
            },
            {
                "iso_3166_1": "AU",
                "release_dates": [
                    {"certification": cert, "release_date": f"{day}T00:00:00.000Z", "type": kind}
                    for day, kind, cert in entries
                ],
            },
        ],
    }


# ---- the key ------------------------------------------------------------------------------


def test_a_pasted_key_is_known_by_its_shape() -> None:
    assert tmdb.tidy(f"  {API_KEY.upper()}\n") == API_KEY
    assert tmdb.tidy(TOKEN) == TOKEN
    assert tmdb.is_token(TOKEN) and not tmdb.is_token(API_KEY)
    for bad in ("", "not a key", "g" * 32, "a.b.c"):
        with pytest.raises(tmdb.TmdbError, match="doesn't look like a TMDB key"):
            tmdb.tidy(bad)


def test_a_key_is_saved_for_the_computer_once_tmdb_accepts_it(fake: FakeTmdb) -> None:
    assert tmdb.status() == {"has_key": False}
    config.save_account("lastfm", {"user": "Listener", "api_key": API_KEY})  # a profile's sign-in
    assert tmdb.connect(API_KEY) == {"has_key": True}
    assert fake.paths() == ["/authentication"]
    assert config.load_computer_account("tmdb") == {"key": API_KEY}
    # The profile's own sign-ins are as they were, and the key isn't one of them.
    assert config.load_accounts() == {"lastfm": {"user": "Listener", "api_key": API_KEY}}
    config.save_account("lastfm", None)
    assert config.load_computer_account("tmdb") == {"key": API_KEY}
    assert tmdb.forget() == {"has_key": False}


def test_a_key_tmdb_refuses_isnt_saved_and_isnt_repeated(fake: FakeTmdb) -> None:
    other = "fedcba9876543210" * 2
    with pytest.raises(tmdb.TmdbError) as refused:
        tmdb.connect(other)
    assert "didn't accept the key" in str(refused.value) and other not in str(refused.value)
    assert tmdb.status() == {"has_key": False}


def test_nothing_is_asked_without_a_key_and_tmdb_is_never_reached_in_tests() -> None:
    with pytest.raises(tmdb.TmdbError, match="isn't set up"):
        tmdb.facts([{"id": "tt1", "type": "movie"}], ["language"])
    set_up()
    with pytest.raises(ReplayMissError):
        tmdb._http("/authentication", {}, API_KEY)


# ---- facts --------------------------------------------------------------------------------


def test_original_language_and_australian_rating_by_imdb_id(fake: FakeTmdb) -> None:
    set_up()
    fake.answers["/find/tt6751668"] = film_found(496243, "ko")
    fake.answers["/movie/496243/release_dates"] = au_releases(
        ("2019-06-27", 3, "MA15+"), ("2019-10-01", 4, "")
    )
    fake.answers["/find/tt0944947"] = {
        "movie_results": [],
        "tv_results": [{"id": 1399, "name": "A Series", "original_language": "en"}],
    }
    fake.answers["/tv/1399/content_ratings"] = {
        "results": [
            {"iso_3166_1": "US", "rating": "TV-MA"},
            {"iso_3166_1": "AU", "rating": "AV15+"},
        ]
    }
    asked = [
        {"id": "tt6751668", "type": "movie"},
        {"id": "tt0944947", "type": "series"},
        {"id": "kitsu:1", "type": "series"},
        {"id": "tt404", "type": "movie"},
    ]
    found = tmdb.facts(asked, ["language", "rating"])
    assert found == {
        "tt6751668": {"language": "ko", "rating": "MA15+"},
        "tt0944947": {"language": "en", "rating": "MA15+"},  # AV15+ counts as MA15+
        "kitsu:1": {"language": None, "rating": None},
        "tt404": {"language": None, "rating": None},
    }
    # Asked again: nothing more of TMDB.
    before = len(fake.asked)
    assert tmdb.facts(asked[:2], ["language", "rating"])["tt6751668"]["language"] == "ko"
    assert len(fake.asked) == before
    # Only the language: no rating is asked for (what was kept forgotten too).
    tmdb._forget_answers()
    tmdb._facts_path()[0].unlink()
    fake.asked.clear()
    assert tmdb.facts(asked[:1], ["language"]) == {"tt6751668": {"language": "ko", "rating": None}}
    assert fake.paths() == ["/find/tt6751668"]


def test_facts_are_kept_in_the_cache_and_no_rating_is_asked_again_after_two_weeks(
    fake: FakeTmdb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-11-07, the owner's yes: what TMDB said of a title is kept (the app's cache folder,
    through fileops), so the engine started again asks nothing; "no rating" and "not known"
    are believed for two weeks, then asked again."""
    set_up()
    fake.answers["/find/tt0000001"] = film_found(101, "en")
    fake.answers["/movie/101/release_dates"] = au_releases(("2026-01-01", 3, "PG"))
    fake.answers["/find/tt0000002"] = film_found(102, "en")
    fake.answers["/movie/102/release_dates"] = {"results": []}  # no Australian rating yet
    asked = [{"id": "tt0000001", "type": "movie"}, {"id": "tt0000002", "type": "movie"}]
    now = 1_800_000_000.0
    monkeypatch.setattr(tmdb.time, "time", lambda: now)
    assert tmdb.facts(asked, ["language", "rating"]) == {
        "tt0000001": {"language": "en", "rating": "PG"},
        "tt0000002": {"language": "en", "rating": None},
    }
    assert tmdb._facts_path()[0].is_file()
    # The engine started again: nothing is asked of TMDB.
    tmdb._forget_answers()
    fake.asked.clear()
    assert tmdb.facts(asked, ["language", "rating"])["tt0000001"]["rating"] == "PG"
    assert fake.paths() == []
    # Two weeks on: the film with no rating is asked about again, and is rated now.
    now += 15 * 24 * 3600
    fake.answers["/movie/102/release_dates"] = au_releases(("2026-02-01", 3, "G"))
    tmdb._forget_answers()
    assert tmdb.facts(asked, ["language", "rating"])["tt0000002"]["rating"] == "G"
    assert fake.paths() == ["/movie/102/release_dates"]
    # A kept file that can't be read is as none: asked again, and kept anew.
    tmdb._facts_path()[0].write_text("not json")
    tmdb._forget_answers()
    fake.asked.clear()
    assert tmdb.facts(asked[:1], ["language"])["tt0000001"]["language"] == "en"
    assert fake.paths() == ["/find/tt0000001"]


def test_a_film_is_asked_by_its_imdb_id_in_one_question(fake: FakeTmdb) -> None:
    """2026-11-09: TMDB takes a film's IMDb id in `/movie/{id}`, with its releases appended,
    so a film's id, language and Australian rating come in one question, not two in a row."""
    set_up()

    def film(params: dict[str, Any]) -> dict[str, Any] | None:
        if params.get("append_to_response") != "release_dates":
            return None
        return {
            "id": 103,
            "original_language": "fr",
            "release_dates": au_releases(("2026-03-01", 3, "PG")),
        }

    fake.answers["/movie/tt0000003"] = film
    asked = [{"id": "tt0000003", "type": "movie"}]
    assert tmdb.facts(asked, ["language", "rating"]) == {
        "tt0000003": {"language": "fr", "rating": "PG"}
    }
    assert fake.paths() == ["/movie/tt0000003"]
    # Known now, the language and rating each by itself: nothing more is asked.
    assert tmdb.facts(asked, ["rating"])["tt0000003"]["rating"] == "PG"
    assert tmdb.facts(asked, ["language"])["tt0000003"]["language"] == "fr"
    assert fake.paths() == ["/movie/tt0000003"]
    # The calendar's Australian day for it was kept from the same answer.
    assert tmdb._film_releases(103) == [("2026-03-01", 3)]
    assert fake.paths() == ["/movie/tt0000003"]


def test_a_title_tmdb_couldnt_answer_just_now_is_left_out_and_asked_again(
    fake: FakeTmdb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-11-09: a question that failed (no connection, TMDB busy) isn't an answer. Given as
    "no rating", it hid the title from a child until the app was opened again."""
    set_up()
    fake.answers["/movie/tt0000004"] = {
        "id": 104,
        "original_language": "en",
        "release_dates": au_releases(("2026-04-01", 3, "G")),
    }
    asked = [{"id": "tt0000004", "type": "movie"}, {"id": "tt404", "type": "movie"}]
    answered = fake

    def busy(path: str, params: dict[str, Any], key: str) -> tuple[int, Any]:
        if path == "/movie/tt0000004":
            return 429, {"status_message": "Your request count is over the allowed limit."}
        return answered(path, params, key)

    monkeypatch.setattr(tmdb, "_http", busy)
    # tt404 isn't known to TMDB: that is an answer. tt0000004 couldn't be asked: left out.
    assert tmdb.facts(asked, ["rating"]) == {"tt404": {"language": None, "rating": None}}
    monkeypatch.setattr(tmdb, "_http", answered)
    assert tmdb.facts(asked, ["rating"])["tt0000004"] == {"language": None, "rating": "G"}


def test_whats_learned_is_written_at_most_every_two_seconds_and_never_put_off(
    fake: FakeTmdb, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_up()
    for number in (5, 6):
        fake.answers[f"/movie/tt000000{number}"] = {
            "id": 100 + number,
            "original_language": "en",
            "release_dates": au_releases(("2026-05-01", 3, "PG")),
        }
    timers: list[Any] = []

    class Timer:
        """Stands in for threading.Timer: started, but run only when the test says."""

        def __init__(self, wait: float, then: Any) -> None:
            self.wait, self.then, self.daemon = wait, then, False
            timers.append(self)

        def start(self) -> None:
            pass

        def cancel(self) -> None:
            timers.remove(self)

    monkeypatch.setattr(tmdb.threading, "Timer", Timer)
    path = tmdb._facts_path()[0]
    tmdb.facts([{"id": "tt0000005", "type": "movie"}], ["rating"])
    assert "tt0000005" in path.read_text()  # the first is written at once
    tmdb.facts([{"id": "tt0000006", "type": "movie"}], ["rating"])
    assert "tt0000006" not in path.read_text()  # the next waits its two seconds
    assert len(timers) == 1 and 0 < timers[0].wait <= tmdb.WRITE_EVERY_S
    tmdb.facts([{"id": "tt0000006", "type": "movie"}], ["rating"])
    assert len(timers) == 1  # and isn't put off by more answers
    timers.pop().then()
    assert "tt0000006" in path.read_text()
    # The engine stopping writes what's waiting at once.
    fake.answers["/movie/tt0000007"] = {"id": 107, "original_language": "en", "release_dates": {}}
    tmdb.facts([{"id": "tt0000007", "type": "movie"}], ["rating"])
    tmdb.flush()
    assert "tt0000007" in path.read_text() and not timers


def test_a_tmdb_id_is_asked_directly_and_ratings_are_tidied(fake: FakeTmdb) -> None:
    set_up()
    fake.answers["/movie/550"] = {"id": 550, "original_language": "en"}
    fake.answers["/movie/550/release_dates"] = au_releases(("1999-11-11", 3, "R18+"))
    assert tmdb.facts([{"id": "tmdb:550", "type": "movie"}], ["language", "rating"]) == {
        "tmdb:550": {"language": "en", "rating": "R18+"}
    }
    assert tmdb.rating("P") == "G" and tmdb.rating("C") == "G" and tmdb.rating(" m ") == "M"
    assert tmdb.rating("E") is None and tmdb.rating("") is None and tmdb.rating(None) is None
    assert tmdb.rating("X18+") == "X18+"  # kept, and never one the app lets be ticked


def releases(**countries: list[tuple[int, str]]) -> dict[str, Any]:
    """`/movie/{id}/release_dates` with each country's releases: (kind, certification)."""
    return {
        "results": [
            {
                "iso_3166_1": country,
                "release_dates": [
                    {"certification": cert, "release_date": "2026-01-01T00:00:00.000Z", "type": t}
                    for t, cert in entries
                ],
            }
            for country, entries in countries.items()
        ]
    }


def test_where_australia_has_no_rating_the_uks_then_the_uss_is_taken(fake: FakeTmdb) -> None:
    """2026-11-10, the owner: "Australia primary, UK secondary, US tertiary. If 1 doesnt have,
    move onto the second, and then the third if second doesnt have it." Each counted as the
    Australian class it's closest to; within a country, its cinema release first, and of
    those, the strictest."""
    set_up()
    films = {
        1: (releases(AU=[(3, "PG")], GB=[(3, "15")], US=[(3, "R")]), "PG"),  # Australia's
        2: (releases(AU=[(3, "E"), (4, "")], GB=[(3, "12A")], US=[(3, "PG")]), "M"),  # UK's
        3: (releases(GB=[(3, "")], US=[(3, "PG-13")]), "M"),  # the US's
        4: (releases(GB=[(3, "U")]), "G"),
        5: (releases(GB=[(3, "PG")]), "PG"),
        6: (releases(GB=[(3, "12")]), "M"),
        7: (releases(GB=[(3, "18")]), "R18+"),
        8: (releases(US=[(3, "G")]), "G"),
        9: (releases(US=[(3, "R")]), "MA15+"),
        10: (releases(US=[(3, "NC-17")]), "R18+"),
        11: (releases(FR=[(3, "TP")], DE=[(3, "6")]), None),  # none of the three
        12: (releases(GB=[(3, "TBC")], US=[(3, "NR")]), None),
        13: (releases(AU=[(3, "PG"), (4, "M")]), "PG"),  # the cinema release first
        14: (releases(AU=[(4, "PG"), (4, "M")]), "M"),  # the strictest
        15: (releases(AU=[(3, ""), (4, "G")]), "G"),  # a cinema release with none: the rest
    }
    for number, (answer, _) in films.items():
        fake.answers[f"/movie/{number}/release_dates"] = answer
    asked = [{"id": f"tmdb:{n}", "type": "movie"} for n in films]
    found = tmdb.facts(asked, ["rating"])
    assert {k: v["rating"] for k, v in found.items()} == {
        f"tmdb:{n}": rated for n, (_, rated) in films.items()
    }
    assert tmdb._film_releases(2) == [("2026-01-01", 3), ("2026-01-01", 4)]  # Australia's days
    assert tmdb._film_releases(3) == []

    # A series: its one rating in each country, by the same order.
    shows = {
        20: ([("US", "TV-MA"), ("GB", "12")], "M"),
        21: ([("US", "TV-Y7")], "PG"),
        22: ([("US", "TV-G")], "G"),
        23: ([("US", "TV-14")], "M"),
        24: ([("US", "TV-MA")], "MA15+"),
        25: ([("DE", "12"), ("US", "NR")], None),
        26: ([("GB", "15"), ("AU", "M")], "M"),
    }
    for number, (said, _) in shows.items():
        fake.answers[f"/tv/{number}/content_ratings"] = {
            "results": [{"iso_3166_1": c, "rating": r} for c, r in said]
        }
    asked = [{"id": f"tmdb:{n}", "type": "series"} for n in shows]
    found = tmdb.facts(asked, ["rating"])
    assert {k: v["rating"] for k, v in found.items()} == {
        f"tmdb:{n}": rated for n, (_, rated) in shows.items()
    }


def test_no_rating_found_by_the_rule_before_is_asked_again(fake: FakeTmdb) -> None:
    """A title kept as having no rating before the UK and the US were asked (a facts file
    with no `rule`) is asked about again; a rating kept is believed as it was."""
    set_up()
    path = tmdb._facts_path()[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    now = tmdb.time.time()
    kept = {
        "format": tmdb.FACTS_FORMAT,
        "found": {},
        "missing": {},
        "languages": {},
        "ratings": {"movie:31": "PG"},
        "unrated": {"movie:32": now},
    }
    path.write_text(json.dumps(kept), encoding="utf-8")
    fake.answers["/movie/32/release_dates"] = releases(GB=[(3, "PG")])
    asked = [{"id": "tmdb:31", "type": "movie"}, {"id": "tmdb:32", "type": "movie"}]
    found = tmdb.facts(asked, ["rating"])
    assert found == {
        "tmdb:31": {"language": None, "rating": "PG"},
        "tmdb:32": {"language": None, "rating": "PG"},
    }
    assert fake.paths() == ["/movie/32/release_dates"]
    assert json.loads(path.read_text(encoding="utf-8"))["rule"] == tmdb.RATING_RULE
    # Kept by this rule, "no rating" is believed for its two weeks, as before.
    path.write_text(json.dumps({**kept, "rule": tmdb.RATING_RULE}), encoding="utf-8")
    tmdb._forget_answers()
    fake.asked.clear()
    assert tmdb.facts(asked[1:], ["rating"]) == {"tmdb:32": {"language": None, "rating": None}}
    assert fake.paths() == []


def test_the_key_being_refused_stops_every_question(fake: FakeTmdb) -> None:
    config.save_computer_account("tmdb", {"key": "fedcba9876543210" * 2})
    with pytest.raises(tmdb.TmdbError, match="didn't accept the key"):
        tmdb.facts([{"id": "tt1", "type": "movie"}, {"id": "tt2", "type": "movie"}], ["language"])


def test_a_token_goes_in_a_header_and_an_api_key_in_the_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import requests

    seen: list[dict[str, Any]] = []

    class Answer:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {"success": True}

    dropped = [1]  # the first question meets a connection TMDB has closed

    class Kept:
        """Stands in for the connections kept open (`tmdb._session`)."""

        @staticmethod
        def get(url: str, **kwargs: Any) -> Answer:
            if dropped[0]:
                dropped[0] -= 1
                raise requests.exceptions.ConnectionError("closed by the other end")
            seen.append({"url": url, **kwargs})
            return Answer()

    monkeypatch.delenv("MUSICORG_REPLAY_DIR", raising=False)
    monkeypatch.setattr(tmdb, "_session", lambda: Kept)
    # Asked once more, afresh, after a kept connection had gone.
    assert tmdb._http("/authentication", {}, TOKEN)[0] == 200
    tmdb._http("/authentication", {}, API_KEY)
    assert (
        seen[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"
        and "api_key" not in seen[0]["params"]
    )
    assert seen[1]["params"]["api_key"] == API_KEY and "Authorization" not in seen[1]["headers"]
    assert seen[0]["url"] == "https://api.themoviedb.org/3/authentication"


# ---- the Finders' lists ---------------------------------------------------------------------


def test_tmdbs_lists_come_as_an_add_ons_items_by_imdb_id(fake: FakeTmdb) -> None:
    set_up()
    fake.answers["/genre/movie/list"] = {
        "genres": [{"id": 27, "name": "Horror"}, {"id": 53, "name": "Thriller"}]
    }
    fake.answers["/movie/upcoming"] = {
        "page": 1,
        "total_pages": 3,
        "results": [
            {
                "id": 11,
                "title": "Coming Soon",
                "poster_path": "/p.jpg",
                "release_date": "2026-12-01",
                "vote_average": 7.25,
                "genre_ids": [27, 53],
                "original_language": "en",
            },
            {
                "id": 12,
                "title": "No IMDb Id",
                "poster_path": None,
                "release_date": "",
                "vote_average": 0,
                "genre_ids": [],
            },
        ],
    }
    fake.answers["/movie/11/external_ids"] = {"imdb_id": "tt1100000"}
    fake.answers["/movie/12/external_ids"] = {"imdb_id": None}
    page = tmdb.catalog("movie", "upcoming")
    assert page == {
        "items": [
            {
                "id": "tt1100000",
                "type": "movie",
                "name": "Coming Soon",
                "poster": "https://image.tmdb.org/t/p/w342/p.jpg",
                "poster_shape": "poster",
                "year": "2026",
                "rating": 7.2,
                "genres": ["Horror", "Thriller"],
            }
        ],
        "more": True,
        "next_skip": 20,
    }
    upcoming = next(params for path, params, _ in fake.asked if path == "/movie/upcoming")
    assert upcoming["region"] == "AU" and upcoming["page"] == 1
    # Its language came with the list: a filter needs no more questions for it.
    before = len(fake.asked)
    assert (
        tmdb.facts([{"id": "tt1100000", "type": "movie"}], ["language"])["tt1100000"]["language"]
        == "en"
    )
    assert len(fake.asked) == before
    with pytest.raises(tmdb.TmdbError):
        tmdb.catalog("movie", "everything")


def test_series_to_come_are_the_ones_whose_first_episode_is_ahead(fake: FakeTmdb) -> None:
    set_up()
    fake.answers["/discover/tv"] = {"page": 2, "total_pages": 2, "results": []}
    fake.answers["/genre/tv/list"] = {"genres": []}
    assert tmdb.catalog("series", "upcoming", skip=20) == {
        "items": [],
        "more": False,
        "next_skip": 40,
    }
    params = next(params for path, params, _ in fake.asked if path == "/discover/tv")
    assert (
        params["page"] == 2
        and params["sort_by"] == "popularity.desc"
        and "first_air_date.gte" in params
    )


TV_GENRES = {
    "genres": [
        {"id": 80, "name": "Crime"},
        {"id": 99, "name": "Documentary"},
        {"id": 10759, "name": "Action & Adventure"},
    ]
}
MOVIE_GENRES = {
    "genres": [
        {"id": 27, "name": "Horror"},
        {"id": 35, "name": "Comedy"},
        {"id": 53, "name": "Thriller"},
    ]
}


def test_the_standard_plugins_lists_by_genre_two_in_one_question(fake: FakeTmdb) -> None:
    """2026-11-11, TMDB the standard plugin: its lists by its own genres, every one of them in
    one question (TMDB's discover, whose comma is "and"), and in the original language
    chosen; Trending takes neither. A genre TMDB hasn't has nothing."""
    set_up()
    today = tmdb.date.today().isoformat()
    fake.answers["/genre/tv/list"] = TV_GENRES
    fake.answers["/genre/movie/list"] = MOVIE_GENRES
    fake.answers["/discover/tv"] = {
        "page": 1,
        "total_pages": 4,
        "results": [{"id": 21, "name": "The Jinx", "genre_ids": [99, 80], "poster_path": "/j.jpg"}],
    }
    fake.answers["/tv/21/external_ids"] = {"imdb_id": "tt4299972"}
    page = tmdb.catalog("series", "popular", genres=["crime", "Documentary"])
    assert [(i["id"], i["genres"]) for i in page["items"]] == [
        ("tt4299972", ["Documentary", "Crime"])
    ]
    assert (page["more"], page["next_skip"]) == (True, 20)
    asked = [(path, params) for path, params, _ in fake.asked if path.startswith("/discover")]
    assert asked == [
        (
            "/discover/tv",
            {"page": 1, "language": "en-US", "sort_by": "popularity.desc", "with_genres": "80,99"},
        )
    ]
    fake.asked.clear()

    fake.answers["/discover/movie"] = {"page": 2, "total_pages": 2, "results": []}
    assert tmdb.catalog("movie", "top_rated", skip=20, genres=["Horror"], language="en") == {
        "items": [],
        "more": False,
        "next_skip": 40,
    }
    tmdb.catalog("movie", "upcoming", genres=["Comedy"])
    tmdb.catalog("series", "top_rated", language="ko")
    tmdb.catalog("series", "upcoming", genres=["Crime"])
    asked = [(path, params) for path, params, _ in fake.asked if path.startswith("/discover")]
    assert asked == [
        (
            "/discover/movie",
            {
                "page": 2, "language": "en-US", "sort_by": "vote_average.desc",
                "vote_count.gte": 300, "with_genres": "27", "with_original_language": "en",
            },
        ),
        (
            "/discover/movie",
            {
                "page": 1, "language": "en-US", "primary_release_date.gte": today, "region": "AU",
                "with_release_type": "2|3", "sort_by": "popularity.desc", "with_genres": "35",
            },
        ),
        (
            "/discover/tv",
            {
                "page": 1, "language": "en-US", "sort_by": "vote_average.desc",
                "vote_count.gte": 200, "with_original_language": "ko",
            },
        ),
        (
            "/discover/tv",
            {
                "page": 1, "language": "en-US", "first_air_date.gte": today,
                "sort_by": "popularity.desc", "with_genres": "80",
            },
        ),
    ]  # fmt: skip
    fake.asked.clear()

    # Trending: the week's, by no genre or language.
    fake.answers["/trending/tv/week"] = {"page": 2, "total_pages": 9, "results": []}
    tmdb.catalog("series", "trending", skip=20, genres=["Crime"], language="en")
    assert fake.asked[0][:2] == ("/trending/tv/week", {"page": 2, "language": "en-US"})
    fake.asked.clear()

    # A genre TMDB hasn't (Cinemeta's "Sci-Fi"): nothing has it, and nothing is asked for it.
    assert tmdb.catalog("series", "popular", skip=40, genres=["Crime", "Sci-Fi"]) == {
        "items": [],
        "more": False,
        "next_skip": 40,
    }
    assert [path for path in fake.paths() if not path.startswith("/genre")] == []
    # No genre and no language: TMDB's own list, as before.
    fake.answers["/tv/popular"] = {"page": 1, "total_pages": 1, "results": []}
    tmdb.catalog("series", "popular")
    assert fake.paths()[-1] == "/tv/popular"
    # The genres the app's menus offer, A to Z.
    assert tmdb.genres("series") == ["Action & Adventure", "Crime", "Documentary"]
    assert tmdb.genres("movie") == ["Comedy", "Horror", "Thriller"]
    with pytest.raises(tmdb.TmdbError):
        tmdb.genres("anime")
    with pytest.raises(tmdb.TmdbError, match="two letters"):
        tmdb.catalog("movie", "popular", language="english")


def test_a_search_finds_titles_with_a_poster_by_their_imdb_ids(fake: FakeTmdb) -> None:
    set_up()
    fake.answers["/genre/movie/list"] = MOVIE_GENRES
    fake.answers["/search/movie"] = {
        "page": 1,
        "results": [
            {
                "id": 550,
                "title": "Fight Club",
                "poster_path": "/f.jpg",
                "release_date": "1999-10-15",
                "vote_average": 8.4,
                "genre_ids": [53],
                "original_language": "en",
                "popularity": 61.2345,
            },
            {"id": 551, "title": "No Poster", "poster_path": None},
            {"id": 552, "title": "No IMDb Id", "poster_path": "/n.jpg"},
        ],
    }
    fake.answers["/movie/550/external_ids"] = {"imdb_id": "tt0137523"}
    fake.answers["/movie/552/external_ids"] = {"imdb_id": None}
    # `popularity` is by the ids the items were given: for a pasted name with no year that
    # several titles share, the app takes one far ahead of the rest as the one meant.
    assert tmdb.search("movie", "  fight club ") == {
        "items": [
            {
                "id": "tt0137523",
                "type": "movie",
                "name": "Fight Club",
                "poster": "https://image.tmdb.org/t/p/w342/f.jpg",
                "poster_shape": "poster",
                "year": "1999",
                "rating": 8.4,
                "genres": ["Thriller"],
            }
        ],
        "popularity": {"tt0137523": 61.234},
    }
    searched = next(params for path, params, _ in fake.asked if path == "/search/movie")
    assert searched == {
        "query": "fight club",
        "include_adult": "false",
        "language": "en-US",
        "page": 1,
    }
    assert "/movie/551/external_ids" not in fake.paths()  # a title with no poster isn't asked about
    # Its language came with the search: the language filter asks nothing more of it.
    before = len(fake.asked)
    assert tmdb.facts([{"id": "tt0137523", "type": "movie"}], ["language"])["tt0137523"] == {
        "language": "en",
        "rating": None,
    }
    assert len(fake.asked) == before
    assert tmdb.search("series", "   ") == {"items": [], "popularity": {}}
    assert len(fake.asked) == before
    with pytest.raises(tmdb.TmdbError):
        tmdb.search("channel", "anything")


# ---- a title's page, and people ---------------------------------------------------------------


def test_a_films_cast_with_photos_and_its_money(fake: FakeTmdb) -> None:
    set_up()
    fake.answers["/find/tt0137523"] = film_found(550, "en")
    fake.answers["/movie/550"] = {
        "id": 550,
        "original_language": "en",
        "budget": 63000000,
        "revenue": 0,
        "credits": {
            "cast": [
                {
                    "id": 819,
                    "name": "Edward Norton",
                    "character": "The Narrator",
                    "profile_path": "/n.jpg",
                },
                {"id": 287, "name": "Brad Pitt", "character": "", "profile_path": None},
            ]
        },
    }
    found = tmdb.details("movie", "tt0137523")
    assert {key: found[key] for key in ("tmdb_id", "cast", "budget", "revenue")} == {
        "tmdb_id": 550,
        "cast": [
            {
                "person_id": 819,
                "name": "Edward Norton",
                "character": "The Narrator",
                "photo": "https://image.tmdb.org/t/p/w185/n.jpg",
            },
            {"person_id": 287, "name": "Brad Pitt", "character": None, "photo": None},
        ],
        "budget": 63000000,
        "revenue": None,  # TMDB's 0 is not knowing
    }
    assert tmdb.details("movie", "kitsu:9") == {
        "tmdb_id": None,
        "cast": [],
        "budget": None,
        "revenue": None,
        "name": None,
        "poster": None,
        "background": None,
        "year": None,
        "rating": None,
        "genres": [],
        "description": None,
        "runtime_min": None,
        "directors": [],
        "trailer_video_id": None,
    }


def test_a_titles_page_from_tmdb_in_the_shape_of_an_add_ons_details(fake: FakeTmdb) -> None:
    """2026-11-11, TMDB the standard plugin: a title's page from TMDB first (the app fills in
    from the plugins what it hasn't), from the one question that also gives its cast."""
    set_up()
    fake.answers["/find/tt4299972"] = {
        "movie_results": [],
        "tv_results": [{"id": 1, "name": "The Jinx", "original_language": "en"}],
    }

    def series(params: dict[str, Any]) -> dict[str, Any] | None:
        if params.get("append_to_response") != "credits,videos,external_ids":
            return None
        return {
            "id": 1,
            "name": "The Jinx",
            "first_air_date": "2015-02-08",
            "vote_average": 8.04,
            "overview": "Robert Durst.",
            "poster_path": "/p.jpg",
            "backdrop_path": "/b.jpg",
            "episode_run_time": [45],
            "genres": [{"id": 99, "name": "Documentary"}, {"id": 80, "name": "Crime"}],
            "created_by": [{"name": "Andrew Jarecki"}],
            "credits": {"cast": [{"id": 5, "name": "Robert Durst"}], "crew": []},
            "videos": {
                "results": [
                    {"site": "YouTube", "type": "Teaser", "key": "t1"},
                    {"site": "YouTube", "type": "Trailer", "key": "x1", "official": False},
                    {"site": "Vimeo", "type": "Trailer", "key": "v1", "official": True},
                    {"site": "YouTube", "type": "Trailer", "key": "x2", "official": True},
                ]
            },
            "external_ids": {"imdb_id": "tt4299972"},
        }

    fake.answers["/tv/1"] = series
    found = tmdb.details("series", "tt4299972")
    assert {key: value for key, value in found.items() if key not in ("cast",)} == {
        "tmdb_id": 1,
        "budget": None,
        "revenue": None,
        "name": "The Jinx",
        "poster": "https://image.tmdb.org/t/p/w500/p.jpg",
        "background": "https://image.tmdb.org/t/p/w1280/b.jpg",
        "year": "2015",
        "rating": 8.0,
        "genres": ["Documentary", "Crime"],
        "description": "Robert Durst.",
        "runtime_min": 45,
        "directors": ["Andrew Jarecki"],  # a series' creators, with no director
        "trailer_video_id": "x2",  # the official trailer on the video service
    }
    assert [person["name"] for person in found["cast"]] == ["Robert Durst"]
    assert fake.paths() == ["/find/tt4299972", "/tv/1"]  # one question for the page

    # A film: its directors (each once, four at most), and what TMDB says 0 of, unknown.
    crew = [{"job": "Director", "name": name} for name in ("A", "B", "A", "C", "D", "E")]
    fake.answers["/movie/7"] = {
        "id": 7,
        "title": "A Film",
        "release_date": "",
        "vote_average": 0,
        "runtime": 0,
        "overview": "",
        "credits": {"cast": [], "crew": [{"job": "Producer", "name": "P"}, *crew]},
        "created_by": [{"name": "Not For A Film"}],
    }
    film = tmdb.details("movie", "tmdb:7")
    assert (film["name"], film["directors"], film["year"], film["rating"]) == (
        "A Film",
        ["A", "B", "C", "D"],
        None,
        None,
    )
    assert (film["runtime_min"], film["description"], film["trailer_video_id"]) == (
        None,
        None,
        None,
    )
    assert (film["poster"], film["background"], film["genres"]) == (None, None, [])


def test_a_series_no_plugin_has_episodes_of_gets_tmdbs_by_imdb_id(fake: FakeTmdb) -> None:
    """Its seasons from its details, then twenty seasons to a question, each episode by the
    series' IMDb id, season and number, as the plugins give theirs."""
    set_up()
    fake.answers["/find/tt4299972"] = {
        "movie_results": [],
        "tv_results": [{"id": 1, "name": "The Jinx", "original_language": "en"}],
    }

    def series(params: dict[str, Any]) -> dict[str, Any]:
        if "append_to_response" not in params:
            return {"id": 1, "seasons": [{"season_number": n} for n in (2, 0, 1)]}
        assert params["append_to_response"] == "season/0,season/1,season/2"
        return {
            "id": 1,
            "season/1": {
                "episodes": [
                    {"season_number": 1, "episode_number": 2, "name": ""},
                    {
                        "season_number": 1,
                        "episode_number": 1,
                        "name": "Chapter 1",
                        "air_date": "2015-02-08",
                        "still_path": "/s.jpg",
                        "overview": "First.",
                    },
                ]
            },
            "season/2": {
                "episodes": [{"season_number": 2, "episode_number": 1, "name": "Chapter 7"}]
            },
            "season/0": {
                "episodes": [{"season_number": 0, "episode_number": 1, "name": "Special"}]
            },
        }

    fake.answers["/tv/1"] = series
    videos = tmdb.episodes("series", "tt4299972")["videos"]
    assert [v["id"] for v in videos] == [
        "tt4299972:0:1",
        "tt4299972:1:1",
        "tt4299972:1:2",
        "tt4299972:2:1",
    ]
    assert videos[1] == {
        "id": "tt4299972:1:1",
        "title": "Chapter 1",
        "thumbnail": "https://image.tmdb.org/t/p/w300/s.jpg",
        "released": "2015-02-08",
        "video_id": None,
        "season": 1,
        "episode": 1,
        "overview": "First.",
    }
    assert videos[2]["title"] == "Episode 2" and videos[2]["released"] is None

    # A long series: twenty seasons to a question.
    fake.answers["/find/tt0000025"] = {"movie_results": [], "tv_results": [{"id": 25}]}
    asked: list[str] = []

    def long_series(params: dict[str, Any]) -> dict[str, Any]:
        if "append_to_response" not in params:
            return {"id": 25, "seasons": [{"season_number": n} for n in range(1, 26)]}
        asked.append(params["append_to_response"])
        return {"id": 25}

    fake.answers["/tv/25"] = long_series
    assert tmdb.episodes("series", "tt0000025") == {"videos": []}
    assert asked == [
        ",".join(f"season/{n}" for n in range(1, 21)),
        ",".join(f"season/{n}" for n in range(21, 26)),
    ]
    # Nothing for a film, or a series by an id its episodes can't be named by.
    fake.asked.clear()
    assert tmdb.episodes("movie", "tt4299972") == {"videos": []}
    assert tmdb.episodes("series", "tmdb:1") == {"videos": []}
    assert fake.paths() == []


def test_a_persons_films_and_series_the_most_talked_about_first(fake: FakeTmdb) -> None:
    set_up()
    fake.answers["/person/819"] = {
        "id": 819,
        "name": "Edward Norton",
        "profile_path": "/n.jpg",
        "biography": "An actor.",
        "combined_credits": {
            "cast": [
                {
                    "id": 550,
                    "media_type": "movie",
                    "title": "Fight Club",
                    "popularity": 60.5,
                    "release_date": "1999-10-15",
                    "character": "The Narrator",
                    "poster_path": "/f.jpg",
                },
                {"id": 550, "media_type": "movie", "title": "Fight Club", "popularity": 60.5},
                {
                    "id": 77,
                    "media_type": "tv",
                    "name": "A Series",
                    "popularity": 80,
                    "first_air_date": "2020-01-01",
                },
                {"id": 5, "media_type": "person", "popularity": 99},
            ]
        },
    }
    found = tmdb.person(819)
    assert (found["name"], found["photo"], found["biography"]) == (
        "Edward Norton",
        "https://image.tmdb.org/t/p/h632/n.jpg",
        "An actor.",
    )
    assert [(c["id"], c["type"], c["year"]) for c in found["credits"]] == [
        ("tmdb:77", "series", "2020"),
        ("tmdb:550", "movie", "1999"),
    ]
    fake.answers["/tv/77/external_ids"] = {"imdb_id": "tt7700000"}
    assert tmdb.imdb_id("series", "tmdb:77") == "tt7700000"
    assert tmdb.imdb_id("movie", "tt1") == "tt1"
    assert tmdb.imdb_id("movie", "kitsu:1") is None


# ---- the calendar's General -----------------------------------------------------------------


def test_the_months_films_on_their_australian_day_and_new_series(fake: FakeTmdb) -> None:
    set_up()
    fake.answers["/discover/movie"] = {
        "page": 1,
        "total_pages": 1,
        "results": [
            {"id": 1, "title": "Here Later", "release_date": "2026-10-20", "poster_path": "/1.jpg"},
            {"id": 2, "title": "Next Month Here", "release_date": "2026-11-02"},
            {"id": 3, "title": "No Australian Date", "release_date": "2026-11-09"},
        ],
    }
    fake.answers["/movie/1/release_dates"] = au_releases(
        ("2026-11-13", 3, "M"), ("2026-12-20", 4, "")
    )
    fake.answers["/movie/2/release_dates"] = au_releases(("2026-12-25", 3, "PG"))
    fake.answers["/movie/3/release_dates"] = {"results": []}
    fake.answers["/discover/tv"] = {
        "page": 1,
        "total_pages": 1,
        "results": [
            {"id": 9, "name": "New Show", "first_air_date": "2026-11-30", "poster_path": "/9.jpg"}
        ],
    }
    found = tmdb.releases(2026, 11)["releases"]
    assert [(r["id"], r["type"], r["day"]) for r in found] == [
        ("tmdb:1", "movie", "2026-11-13"),  # its Australian day, not its first anywhere
        ("tmdb:3", "movie", "2026-11-09"),  # none known in Australia: the day TMDB lists
        ("tmdb:9", "series", "2026-11-30"),
    ]
    discover = next(params for path, params, _ in fake.asked if path == "/discover/movie")
    assert discover["region"] == "AU" and discover["with_release_type"] == "2|3|4"
    assert (discover["release_date.gte"], discover["release_date.lte"]) == (
        "2026-11-01",
        "2026-11-30",
    )
    # The ratings came with the release dates: a child's profile's filter asks nothing more.
    before = len(fake.asked)
    assert tmdb.facts([{"id": "tmdb:1", "type": "movie"}], ["rating"])["tmdb:1"]["rating"] == "M"
    assert len(fake.asked) == before
    with pytest.raises(tmdb.TmdbError):
        tmdb.releases(2026, 13)


# ---- over RPC -------------------------------------------------------------------------------


def test_tmdb_over_rpc_never_gives_out_the_key(server: rpc.Server, fake: FakeTmdb) -> None:  # noqa: F811
    result(server, "engine.hello", client="pytest", client_version="1")
    assert result(server, "tmdb.status") == {"has_key": False}
    assert code(server, "tmdb.facts", titles=[{"id": "tt1", "type": "movie"}]) == rpc.USER_ERROR
    assert result(server, "tmdb.connect", key=API_KEY) == {"has_key": True}
    fake.answers["/find/tt6751668"] = film_found(496243, "ko")
    assert result(
        server, "tmdb.facts", titles=[{"id": "tt6751668", "type": "movie"}], want=["language"]
    ) == {"facts": [{"id": "tt6751668", "language": "ko", "rating": None}]}
    fake.answers["/movie/5/external_ids"] = {"imdb_id": "tt5"}
    assert result(server, "tmdb.imdb", type="movie", id="tmdb:5") == {"id": "tt5"}
    assert code(server, "tmdb.catalog", type="movie", list="popular", skip=-1) == rpc.INVALID_PARAMS
    assert result(server, "tmdb.forget") == {"has_key": False}


def test_the_standard_plugins_lists_genres_episodes_and_search_over_rpc(
    server: rpc.Server,  # noqa: F811
    fake: FakeTmdb,
) -> None:
    result(server, "engine.hello", client="pytest", client_version="1")
    assert code(server, "tmdb.genres") == rpc.USER_ERROR  # no key yet
    assert result(server, "tmdb.connect", key=API_KEY) == {"has_key": True}
    fake.answers["/genre/tv/list"] = TV_GENRES
    fake.answers["/genre/movie/list"] = MOVIE_GENRES
    assert result(server, "tmdb.genres") == {
        "genres": {
            "movie": ["Comedy", "Horror", "Thriller"],
            "series": ["Action & Adventure", "Crime", "Documentary"],
        }
    }
    list_page = {"type": "series", "list": "popular"}
    for bad in (
        {"genres": ["Crime", "Documentary", "Action & Adventure"]},  # two at most
        {"genres": [80]},
        {"genres": [" "]},
        {"genres": "Crime"},
        {"language": "eng"},
        {"language": "EN"},
    ):
        assert code(server, "tmdb.catalog", **list_page, **bad) == rpc.INVALID_PARAMS, bad
    fake.answers["/discover/tv"] = {"page": 1, "total_pages": 1, "results": []}
    assert result(
        server, "tmdb.catalog", **list_page, genres=["Crime", "Documentary"], language="en"
    ) == {"items": [], "more": False, "next_skip": 20}
    discover = next(params for path, params, _ in fake.asked if path == "/discover/tv")
    assert (discover["with_genres"], discover["with_original_language"]) == ("80,99", "en")
    fake.answers["/search/tv"] = {"page": 1, "results": []}
    assert result(server, "tmdb.search", type="series", query="the jinx") == {
        "items": [],
        "popularity": {},
    }
    assert code(server, "tmdb.search", type="series", query="x" * 201) == rpc.INVALID_PARAMS
    assert result(server, "tmdb.episodes", type="movie", id="tt1") == {"videos": []}
    assert {"tmdb.genres", "tmdb.episodes", "tmdb.search"} <= rpc.SLOW_METHODS
