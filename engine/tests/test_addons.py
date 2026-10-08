"""Add-ons: reading a manifest, building addresses, and tidying answers. Real answers,
recorded on 2026-10-07 and trimmed, are replayed: the network is never reached."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from musicorg import addons, config
from musicorg.errors import ReplayMissError

FIXTURES = Path(__file__).parent / "fixtures" / "addons"
CHANNELS = "https://v3-channels.strem.io"
FILMS = "https://caching.stremio.net/publicdomainmovies.now.sh"
CINEMETA = "https://v3-cinemeta.strem.io"
KITSU = "https://anime-kitsu.strem.fun"

ANSWERS = {
    f"{CHANNELS}/manifest.json": "channels-manifest.json",
    f"{FILMS}/manifest.json": "publicdomain-manifest.json",
    f"{CINEMETA}/manifest.json": "cinemeta-manifest.json",
    f"{CHANNELS}/catalog/channel/top/genre=Gaming.json": "channels-top-gaming.json",
    f"{FILMS}/catalog/movie/publicdomainmovies.json": "publicdomain-catalog.json",
    f"{CHANNELS}/meta/channel/yt_id:UCX6OQ3DkcsbYNE6H8uQQuVA.json": "channels-meta.json",
    f"{FILMS}/stream/movie/tt0012349.json": "publicdomain-stream.json",
    f"{CINEMETA}/meta/movie/tt0012349.json": "cinemeta-meta.json",
    # Anime Kitsu, recorded 2026-10-08 and trimmed.
    f"{KITSU}/manifest.json": "kitsu-manifest.json",
    f"{KITSU}/catalog/anime/kitsu-anime-popular.json": "kitsu-popular.json",
    f"{KITSU}/meta/series/kitsu:1.json": "kitsu-meta.json",
}


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stand in for the network with the recorded answers; gives the addresses asked."""
    seen: list[str] = []

    def answer(url: str) -> Any:
        seen.append(url)
        if url not in ANSWERS:
            raise addons.AddonError(f"Not recorded: {url}")
        return json.loads((FIXTURES / ANSWERS[url]).read_text(encoding="utf-8"))

    monkeypatch.setattr(addons, "_http", answer)
    return seen


def test_the_network_is_never_reached_in_tests() -> None:
    with pytest.raises(ReplayMissError):
        addons.load(f"{CHANNELS}/manifest.json")


def test_an_address_as_the_owner_pastes_it() -> None:
    assert addons.manifest_address(" stremio://example.org/a/manifest.json ") == (
        "https://example.org/a/manifest.json"
    )
    for wrong in ("", "example.org/manifest.json", "ftp://example.org/manifest.json",
                  "https://example.org/", "https://example.org/manifest.json.txt"):  # fmt: skip
        with pytest.raises(addons.AddonError):
            addons.manifest_address(wrong)
    # Settings kept in the path stay: only the last /manifest.json is cut.
    assert addons.base_of("https://example.org/quality=hd|x/manifest.json") == (
        "https://example.org/quality=hd|x"
    )


def test_an_address_is_built_the_same_way_every_time() -> None:
    assert addons.build_url("https://a.example/", "catalog", "movie", "top") == (
        "https://a.example/catalog/movie/top.json"
    )
    assert (
        addons.build_url(
            "https://a.example", "catalog", "movie", "top", {"search": "tom & jerry", "skip": "20"}
        )
        == "https://a.example/catalog/movie/top/search=tom%20%26%20jerry&skip=20.json"
    )
    assert addons.build_url("https://a.example", "meta", "channel", "yt_id:AB/c") == (
        "https://a.example/meta/channel/yt_id:AB%2Fc.json"
    )


def test_a_manifest_is_read_into_one_shape(asked: list[str]) -> None:
    channels = addons.load(f"{CHANNELS}/manifest.json")
    assert channels["base"] == CHANNELS and channels["types"] == ["channel"]
    assert [r["name"] for r in channels["resources"]] == ["catalog", "meta"]
    assert channels["resources"][0]["id_prefixes"] == ["yt_id:"]
    top = channels["catalogs"][0]
    genre = next(e for e in top["extra"] if e["name"] == "genre")
    assert "Gaming" in genre["options"] and "Sports" in genre["options"]
    videos = channels["catalogs"][1]
    assert videos["extra"] == [{"name": "search", "required": True, "options": []}]


def test_resources_may_be_words_or_objects() -> None:
    raw = {
        "id": "x", "name": "X", "version": "1", "types": ["movie"], "idPrefixes": ["tt"],
        "resources": ["catalog", {"name": "stream", "types": ["series"], "idPrefixes": ["kitsu:"]}],
        "catalogs": [{"type": "movie", "id": "old", "extraSupported": ["search", "skip"],
                      "extraRequired": ["search"], "genres": ["Drama"]}],
    }  # fmt: skip
    addon = addons.read_manifest("https://x.example/manifest.json", raw)
    assert addons.supports(addon, "catalog", "movie")
    assert not addons.supports(addon, "stream", "movie", "tt1")
    assert addons.supports(addon, "stream", "series", "kitsu:5")
    assert not addons.supports(addon, "stream", "series", "tt5")
    assert not addons.supports(addon, "meta", "movie")
    assert addon["catalogs"][0]["extra"] == [
        {"name": "search", "required": True, "options": []},
        {"name": "skip", "required": False, "options": []},
        {"name": "genre", "required": False, "options": ["Drama"]},
    ]


@pytest.mark.parametrize("missing", ["id", "name", "version", "resources", "types"])
def test_a_manifest_without_what_it_must_have_is_refused(missing: str) -> None:
    raw = {"id": "x", "name": "X", "version": "1", "types": [], "resources": []}
    del raw[missing]
    with pytest.raises(addons.AddonError):
        addons.read_manifest("https://x.example/manifest.json", raw)


def test_a_list_of_channels_by_genre(asked: list[str]) -> None:
    channels = addons.load(f"{CHANNELS}/manifest.json")
    page = addons.catalog(channels, "channel", "top", genre="Gaming")
    assert asked[-1] == f"{CHANNELS}/catalog/channel/top/genre=Gaming.json"
    first = page["items"][0]
    assert first["id"].startswith("yt_id:") and first["type"] == "channel"
    assert first["poster_shape"] == "square" and first["poster"].startswith("https://")
    assert page["more"] is True  # the add-on said so


def test_only_what_a_list_takes_is_asked(asked: list[str]) -> None:
    channels = addons.load(f"{CHANNELS}/manifest.json")
    before = len(asked)
    with pytest.raises(addons.AddonError, match="genre"):
        addons.catalog(channels, "channel", "videos", search="x", genre="Gaming")  # no genres there
    with pytest.raises(addons.AddonError, match="needs a search"):
        addons.catalog(channels, "channel", "videos")
    with pytest.raises(addons.AddonError):
        addons.catalog(channels, "movie", "top")  # no lists of films there
    with pytest.raises(addons.AddonError):
        addons.catalog(channels, "channel", "nope")
    assert len(asked) == before  # nothing pointless was sent


def test_a_list_of_films(asked: list[str]) -> None:
    films = addons.load(f"{FILMS}/manifest.json")
    page = addons.catalog(films, "movie", "publicdomainmovies")
    kid = page["items"][0]
    assert (kid["id"], kid["name"]) == ("tt0012349", "The Kid")
    assert (kid["year"], kid["rating"]) == ("1921", 8.3)
    assert kid["genres"] == ["Comedy", "Drama", "Family"] and kid["poster_shape"] == "poster"
    assert page["more"] is True  # it has pages, and this one wasn't empty


def test_a_channels_details_and_its_videos(asked: list[str]) -> None:
    channels = addons.load(f"{CHANNELS}/manifest.json")
    found = addons.meta(channels, "channel", "yt_id:UCX6OQ3DkcsbYNE6H8uQQuVA")
    assert found["name"] == "MrBeast" and len(found["videos"]) == 3
    released = [v["released"] for v in found["videos"]]
    assert released == sorted(released, reverse=True)  # newest first
    for video in found["videos"]:
        assert video["id"].endswith(":" + video["video_id"]) and len(video["video_id"]) == 11
    with pytest.raises(addons.AddonError):
        addons.meta(channels, "channel", "tt0012349")  # not one of its ids: nothing asked


def test_a_films_details(asked: list[str]) -> None:
    cinemeta = addons.load(f"{CINEMETA}/manifest.json")
    kid = addons.meta(cinemeta, "movie", "tt0012349")
    assert (kid["name"], kid["year"]) == ("The Kid", "1921")
    assert (kid["runtime_min"], kid["rating"]) == (68, 8.2)
    assert kid["cast"][0] == "Charles Chaplin" and kid["directors"] == ["Charles Chaplin"]
    assert kid["trailer_video_id"] == "O5WXokyub-4" and kid["background"].startswith("https://")
    assert kid["videos"] == []


def test_streams_come_only_from_add_ons_that_offer_them(asked: list[str]) -> None:
    mine = [addons.load(f"{base}/manifest.json") for base in (CINEMETA, CHANNELS, FILMS)]
    before = len(asked)
    found = addons.streams(mine, "movie", "tt0012349")
    assert asked[before:] == [f"{FILMS}/stream/movie/tt0012349.json"]  # one question, not three
    assert found["problems"] == []
    (source,) = found["sources"]
    assert source["addon"] == "Public Domain Movies"
    (stream,) = source["streams"]
    assert stream["kind"] == "torrent" and stream["quality"] == "1080p"
    assert stream["info_hash"] == "5d640678eae57c72c0d096904fd7d7405ece6653"
    assert stream["file_index"] == 1 and stream["trackers"] == []


def test_an_add_on_that_fails_is_named_and_the_rest_still_answer(asked: list[str]) -> None:
    films = addons.load(f"{FILMS}/manifest.json")
    found = addons.streams([films], "movie", "tt9999999")  # not recorded: it "fails"
    assert found["sources"] == []
    assert found["problems"][0]["addon"] == "Public Domain Movies"


def test_each_kind_of_stream() -> None:
    read = addons._stream
    assert read({"url": "https://v.example/a.mp4", "name": "720p"})["kind"] == "url"
    special = {"url": "https://v.example/a.mkv", "behaviorHints": {"notWebReady": True}}
    assert read(special)["kind"] == "url_special"
    assert read({"ytId": "O5WXokyub-4"})["video_id"] == "O5WXokyub-4"
    torrent = read({"infoHash": "AB" * 20, "sources": ["tracker:udp://t.example:80", "dht:x"]})
    assert torrent["info_hash"] == "ab" * 20 and torrent["trackers"] == ["udp://t.example:80"]
    assert torrent["file_index"] is None
    for unknown in ({"externalUrl": "https://x.example"}, {"url": "magnet:?xt=1"},
                    {"infoHash": "short"}, {"ytId": "bad id"}, "text"):  # fmt: skip
        assert read(unknown) is None


def test_what_a_stream_says_its_picture_is() -> None:
    assert addons.quality_of("Torrentless", "Film 2160p HDR") == "4k"
    assert addons.quality_of("1080p WEB-DL") == "1080p"
    assert addons.quality_of("1080p TeleSync") == "cam"  # filmed off a screen, whatever its size
    assert addons.quality_of("HDCAM") == "cam"
    assert addons.quality_of("💾 859.37 MB") is None
    assert addons.quality_of(None, None) is None


def test_the_owners_list_starts_with_the_apps_own_and_is_kept(asked: list[str]) -> None:
    assert config.load_addons() is None
    first = addons.listed()
    assert [one["name"] for one in first] == [
        "Cinemeta", "YouTube", "Public Domain Movies", "Anime Kitsu"]  # fmt: skip
    before = len(asked)
    assert addons.listed() == first and len(asked) == before  # kept: not fetched again

    assert [one["id"] for one in addons.remove("com.linvo.cinemeta")] == [
        "com.linvo.stremiochannels", "org.stremio.pubdomainmovies",
        "community.anime.kitsu"]  # fmt: skip
    again = addons.add(f"stremio://{CINEMETA.removeprefix('https://')}/manifest.json")
    assert [one["name"] for one in again] == [
        "YouTube", "Public Domain Movies", "Anime Kitsu", "Cinemeta"]  # fmt: skip
    assert len(addons.add(f"{CINEMETA}/manifest.json")) == 4  # the same one isn't listed twice
    with pytest.raises(addons.AddonError):
        addons.named("gone")

    # Put in another order, and the order is kept.
    order = ["org.stremio.pubdomainmovies", "com.linvo.cinemeta", "com.linvo.stremiochannels",
             "community.anime.kitsu"]  # fmt: skip
    assert [one["id"] for one in addons.reorder(order)] == order
    assert [one["id"] for one in addons.listed()] == order
    for wrong in (order[:2], [*order, "another"], [order[0], order[0], order[1]]):
        with pytest.raises(addons.AddonError):
            addons.reorder(wrong)
    # One of the app's own that was removed is put back, after the others.
    addons.remove("com.linvo.cinemeta")
    assert [one["id"] for one in addons.restore()] == [order[0], order[2], order[3], order[1]]
    before = len(asked)
    assert len(addons.restore()) == 4 and len(asked) == before  # nothing missing: nothing asked

    # Details come from the first add-on in the list that has them for that id.
    assert addons.details("movie", "tt0012349")["name"] == "The Kid"
    with pytest.raises(addons.AddonError):
        addons.details("movie", "xx1")


def test_a_starting_add_on_that_cant_be_reached_is_tried_again_next_time(
    monkeypatch: pytest.MonkeyPatch, asked: list[str]
) -> None:
    monkeypatch.delitem(ANSWERS, f"{CHANNELS}/manifest.json")
    assert [one["name"] for one in addons.listed()] == [
        "Cinemeta", "Public Domain Movies", "Anime Kitsu"]  # fmt: skip
    assert config.load_addons() is None  # not kept short: the next time asks again


def test_a_list_by_two_genres_keeps_only_what_has_both(
    monkeypatch: pytest.MonkeyPatch, asked: list[str]
) -> None:
    def film(number: int, *genres: str) -> dict[str, object]:
        return {"id": f"tt{number}", "name": f"Film {number}", "genres": list(genres)}

    pages = {
        0: [film(1, "Documentary"), film(2, "Documentary", "Crime"), film(3, "Documentary")],
        3: [film(4, "Documentary", "crime", "History"), film(5, "Documentary", "War")],
        5: [],
    }
    seen: list[int] = []

    def http(url: str) -> dict[str, object]:
        skip = int(url.split("skip=")[1].split(".")[0].split("&")[0]) if "skip=" in url else 0
        assert "genre=Documentary" in url and "Crime" not in url  # asked for one genre only
        seen.append(skip)
        return {"metas": pages[skip]}

    addon = {
        "name": "Films", "base": "https://example.invalid", "types": ["movie"],
        "resources": [{"name": "catalog", "types": ["movie"], "id_prefixes": []}],
        "catalogs": [{"type": "movie", "id": "top", "name": None, "extra": [
            {"name": "genre", "required": False, "options": []},
            {"name": "skip", "required": False, "options": []}]}],
    }  # fmt: skip
    monkeypatch.setattr(addons, "_http", http)
    page = addons.catalog(addon, "movie", "top", genre="Documentary", also=["Crime"])
    assert [item["id"] for item in page["items"]] == ["tt2", "tt4"]
    assert (page["more"], page["next_skip"], seen) == (False, 5, [0, 3, 5])

    # One genre, as before: one page, and where the next begins.
    plain = addons.catalog(addon, "movie", "top", genre="Documentary")
    assert len(plain["items"]) == 3 and plain["next_skip"] == 3
    # A second genre needs a first.
    with pytest.raises(addons.AddonError):
        addons.catalog(addon, "movie", "top", also=["Crime"])
    # It stops reading after so many pages and says there's more.
    monkeypatch.setattr(addons, "_http", lambda url: {"metas": [film(9, "Documentary")]})
    long = addons.catalog(addon, "movie", "top", genre="Documentary", also=["Crime"])
    assert long == {"items": [], "more": True, "next_skip": addons.ALSO_PAGES}


# ---- anime (the owner, 2026-10-08) ---------------------------------------------------------


def test_anime_lists_and_details(asked: list[str]) -> None:
    kitsu = addons.named("community.anime.kitsu")
    lists = [c for c in kitsu["catalogs"] if c["type"] == "anime"]
    assert "kitsu-anime-popular" in [c["id"] for c in lists]
    # Its lists are by genre, without the adults-only ones (this is a family's app).
    genres = {g for c in lists for e in c["extra"] if e["name"] == "genre" for g in e["options"]}
    assert {"Action", "Comedy", "Drama"} <= genres
    assert not {g.lower() for g in genres} & addons.ADULT_GENRES
    # Nothing to play comes from it: lists and details only.
    assert not addons.supports(kitsu, "stream", "series", "kitsu:1")

    found = addons.catalog(kitsu, "anime", "kitsu-anime-popular")
    assert len(found["items"]) == 3
    first = found["items"][0]
    assert first["id"].startswith("kitsu:") and first["type"] == "series" and first["name"]
    # What's in the list is a series like any other: its details, with episodes, come
    # from this add-on (the film-details one doesn't know these ids).
    about = addons.details("series", "kitsu:1")
    assert about["name"] == "Cowboy Bebop"
    assert [(v["season"], v["episode"]) for v in about["videos"]] == [(1, 1), (1, 2)]


def test_a_list_kept_before_gets_the_new_starting_add_on_once(asked: list[str]) -> None:
    # A list from before Anime Kitsu was one of the app's own, with one of the first
    # three taken out by the owner.
    kept = [addons.load(f"{CHANNELS}/manifest.json"), addons.load(f"{FILMS}/manifest.json")]
    config.save_addons(kept)
    assert config.addons_offered() is None
    names = [one["name"] for one in addons.listed()]
    assert names == ["YouTube", "Public Domain Movies", "Anime Kitsu"]  # Cinemeta stays out
    assert config.addons_offered() == list(addons.STARTING)
    before = len(asked)
    assert [one["name"] for one in addons.listed()] == names and len(asked) == before
    # Taken out by the owner, it isn't put back by itself; other changes keep the note.
    addons.remove("community.anime.kitsu")
    addons.reorder(["org.stremio.pubdomainmovies", "com.linvo.stremiochannels"])
    assert [one["name"] for one in addons.listed()] == ["Public Domain Movies", "YouTube"]
    assert config.addons_offered() == list(addons.STARTING)


def test_a_new_starting_add_on_that_cant_be_reached_is_tried_next_time(
    monkeypatch: pytest.MonkeyPatch, asked: list[str]
) -> None:
    config.save_addons([addons.load(f"{CINEMETA}/manifest.json")])
    answer = ANSWERS[f"{KITSU}/manifest.json"]
    monkeypatch.delitem(ANSWERS, f"{KITSU}/manifest.json")
    assert [one["name"] for one in addons.listed()] == ["Cinemeta"]
    assert config.addons_offered() == list(addons.FIRST_STARTING)
    monkeypatch.setitem(ANSWERS, f"{KITSU}/manifest.json", answer)
    assert [one["name"] for one in addons.listed()] == ["Cinemeta", "Anime Kitsu"]
