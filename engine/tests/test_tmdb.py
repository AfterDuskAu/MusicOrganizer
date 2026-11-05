"""tmdb: original language, Australian ratings, TMDB's lists, cast, people and the
calendar's releases, with the owner's own key (2026-11-05).

TMDB is never reached: `tmdb._http` is stood in for by answers in the shapes TMDB's
documentation gives (checked 2026-11-05), and its 401 answer as it was really seen without
a key. The keys here are made up while the tests run, so the file holds none.
"""

from __future__ import annotations

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
    # Only the language: no rating is asked for.
    tmdb._forget_answers()
    fake.asked.clear()
    assert tmdb.facts(asked[:1], ["language"]) == {"tt6751668": {"language": "ko", "rating": None}}
    assert fake.paths() == ["/find/tt6751668"]


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

    def get(url: str, **kwargs: Any) -> Answer:
        seen.append({"url": url, **kwargs})
        return Answer()

    monkeypatch.delenv("MUSICORG_REPLAY_DIR", raising=False)
    monkeypatch.setattr(requests, "get", get)
    tmdb._http("/authentication", {}, TOKEN)
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
    assert tmdb.details("movie", "tt0137523") == {
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
    }


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
