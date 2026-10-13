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
        # (A path that's already whole, a test's own made-up answer, is read as it is.)
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
    # Its size as a figure, from the "💾 859.37 MB" the add-on writes: the film's file
    # is that many bytes to within the rounding of what was written.
    assert abs(stream["size"] - 901_115_781) < 6_000
    # Everything a way to play carries, and no more: nothing says how many have it.
    assert set(stream) == {"kind", "name", "title", "quality", "size", "url", "video_id",
                           "info_hash", "file_index", "trackers"}  # fmt: skip


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


def test_what_a_stream_says_its_size_is() -> None:
    size = addons.size_of
    mb, gb = 1024**2, 1024**3
    assert size(None, "1080p", "💾 859.37 MB") == round(859.37 * mb)
    assert size({}, "Film 2160p HDR 14.2 GB") == round(14.2 * gb)
    assert size(None, "1,4 GB") == round(1.4 * gb)  # a comma for the point
    assert size(None, "700MiB") == 700 * mb and size(None, "512 kb") == 512 * 1024
    assert size(None, "2 TB") == 2 * 1024**4
    # The add-on's own figure, in the protocol's field for it, comes before its words.
    assert size({"videoSize": 901_115_781}, "💾 859.37 MB") == 901_115_781
    for odd in (0, -5, True, "901115781", 1.5, None, [1], 1024**5 + 1):
        assert size({"videoSize": odd}, "💾 2 GB") == 2 * gb
    assert size("not hints", "💾 2 GB") == 2 * gb
    # What isn't a size isn't read as one.
    for words in ("1080p", "5 Mbps", "DD5.1", "10bit x265", "v1.2GB", "", None, "GB", "0 MB"):
        assert size(None, words) is None, words
    assert size(None, "DD5.1 2.3 GB") == round(2.3 * gb)
    assert size(None, "9999999 TB") is None  # nobody's film
    # Every kind of stream carries it, or None when nothing says.
    read = addons._stream
    assert read({"url": "https://v.example/a.mp4", "name": "720p 350 MB"})["size"] == 350 * mb
    assert read({"ytId": "O5WXokyub-4"})["size"] is None
    hinted = read({"infoHash": "ab" * 20, "behaviorHints": {"videoSize": 123_456_789}})
    assert hinted["size"] == 123_456_789


def test_an_answer_in_another_shape_isnt_read(
    asked: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A way to play is what the protocol says it is. A list of torrents under another
    # name, with counts of who has each, isn't an add-on's answer: nothing is made of it.
    films = addons.load(f"{FILMS}/manifest.json")
    other = {"torrents": [{"hash": "ab" * 20, "quality": "1080p", "size": "1.4 GB",
                           "seeds": 12, "peers": 3}]}  # fmt: skip
    monkeypatch.setattr(addons, "_http", lambda url: other)
    assert addons.streams([films], "movie", "tt0012349") == {"sources": [], "problems": []}
    assert addons._stream(other["torrents"][0]) is None


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


def test_one_add_on_set_up_two_ways_is_kept_as_two(monkeypatch: pytest.MonkeyPatch) -> None:
    # A made-up add-on that keeps its settings in its address: the same id and the same
    # name at every address, and each address answers in its own way.
    home = "https://addon.example"
    made = {"id": "org.example.films", "name": "Example", "version": "1.0.0",
            "resources": ["stream"], "types": ["movie"], "idPrefixes": ["tt"]}  # fmt: skip
    answers: dict[str, Any] = {
        f"{home}/size=1080p/manifest.json": made,
        f"{home}/size=720p/manifest.json": {**made, "version": "1.0.1"},
        f"{home}/size=480p/manifest.json": made,
        f"{home}/size=1080p/stream/movie/tt1.json": {
            "streams": [{"infoHash": "ab" * 20, "name": "1080p"}]},
        f"{home}/size=720p/stream/movie/tt1.json": {
            "streams": [{"infoHash": "cd" * 20, "name": "720p"}]},
        f"{home}/size=480p/stream/movie/tt1.json": {"streams": []},
    }  # fmt: skip

    def answer(url: str) -> Any:
        url = url.replace("ADDON.example", "addon.example")  # a name is found in any capitals
        if url not in answers:
            raise addons.AddonError(f"Not made up: {url}")
        return answers[url]

    monkeypatch.setattr(addons, "_http", answer)
    config.save_addons([], offered=list(addons.STARTING))  # an empty list of the owner's own

    first = addons.add(f"{home}/size=1080p/manifest.json")
    both = addons.add(f"{home}/size=720p/manifest.json")
    # Both are there: the second didn't take the first's place.
    assert [one["base"] for one in both] == [f"{home}/size=1080p", f"{home}/size=720p"]
    assert [one["name"] for one in both] == ["Example", "Example (2)"]
    one_id, two_id = (one["id"] for one in both)
    assert one_id == first[0]["id"] == "org.example.films"  # the first keeps the manifest's id
    assert two_id.startswith("org.example.films@") and two_id != one_id

    # Each is asked at its own address, and says which it is.
    found = addons.streams(both, "movie", "tt1")
    assert [(s["addon_id"], s["addon"], s["streams"][0]["quality"]) for s in found["sources"]] == [
        (one_id, "Example", "1080p"), (two_id, "Example (2)", "720p"),
    ]  # fmt: skip

    # The same address again is read afresh in its place: not a third, and what the list
    # knows it by doesn't change. However it's written.
    again = addons.add("stremio://ADDON.example/size=720p/manifest.json")
    assert [(one["id"], one["name"]) for one in again] == [
        (one_id, "Example"),
        (two_id, "Example (2)"),
    ]
    assert again[1]["version"] == "1.0.1"
    assert addons.add(f"{home}/size=1080p/manifest.json")[0]["id"] == one_id
    assert len(addons.listed()) == 2
    # A third is the third.
    third = addons.add(f"{home}/size=480p/manifest.json")
    assert [one["name"] for one in third] == ["Example", "Example (2)", "Example (3)"]
    assert len({one["id"] for one in third}) == 3

    # Each is its own to mark, move and remove.
    addons.mark_adult(two_id, True)
    assert [addons.is_adult(one) for one in addons.listed()] == [False, True, False]
    kept = addons.add(f"{home}/size=720p/manifest.json")  # read afresh, the mark stays its own
    assert [addons.is_adult(one) for one in kept] == [False, True, False]
    order = [third[2]["id"], two_id, one_id]
    assert [one["id"] for one in addons.reorder(order)] == order
    left = addons.remove(one_id)
    assert [one["name"] for one in left] == ["Example (3)", "Example (2)"]
    # Taken out and added again, the second has the id it had: it's made from its address.
    addons.remove(two_id)
    back = addons.add(f"{home}/size=1080p/manifest.json")  # the manifest's own id is free again…
    assert back[-1]["id"] == "org.example.films" and back[-1]["name"] == "Example"
    assert (
        addons.add(f"{home}/size=720p/manifest.json")[-1]["id"] == two_id
    )  # …and this one's is its own


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


# ---- a search of every add-on (the owner, 2026-10-13) ---------------------------------------


def searchable(name: str, base: str, *, pages: bool = False) -> dict[str, Any]:
    extra = [{"name": "search", "required": False, "options": []}]
    if pages:
        extra.append({"name": "skip", "required": False, "options": []})
    return {
        "name": name, "base": base, "types": ["movie"],
        "resources": [{"name": "catalog", "types": ["movie"], "id_prefixes": []}],
        "catalogs": [
            # A list that must be asked by genre can't be searched, and isn't asked.
            {"type": "movie", "id": "year", "name": None,
             "extra": [{"name": "genre", "required": True, "options": []}]},
            {"type": "movie", "id": "top", "name": None, "extra": extra},
        ],
    }  # fmt: skip


def titles(*named: tuple[str, str]) -> dict[str, Any]:
    return {"metas": [{"id": i, "type": "movie", "name": name} for i, name in named]}


def test_a_search_asks_every_add_on_and_puts_the_answers_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    answers = {
        "https://one.invalid/catalog/movie/top/search=avengers.json": titles(
            ("tt1", "The Avengers"), ("tt2", "Avengers: Endgame"), ("tt9", "Thunderbolts*")
        ),
        # Asked again more widely: a title there was no room for, and ones unlike it.
        "https://one.invalid/catalog/movie/top/search=the%20avengers.json": titles(
            ("tt1", "The Avengers"), ("tt3", "Ultimate Avengers"), ("tt8", "The Avenger")
        ),
        "https://two.invalid/catalog/movie/top/search=avengers.json": titles(
            ("tt2", "Avengers: Endgame"), ("tt4", "Next Avengers")
        ),
        "https://two.invalid/catalog/movie/top/search=the%20avengers.json": titles(),
    }

    def http(url: str) -> Any:
        seen.append(url)
        if url not in answers:
            raise addons.AddonError("It didn't answer.")
        return answers[url]

    monkeypatch.setattr(addons, "_http", http)
    owned = [searchable("One", "https://one.invalid"), searchable("Two", "https://two.invalid"),
             searchable("Three", "https://three.invalid")]  # fmt: skip
    found = addons.search(owned, "movie", "  avengers ")
    # Each title once, in the add-ons' order; what an add-on's own answer holds is kept
    # as it is (it knows why), and of the wider asking only titles with the word in.
    assert [(item["id"], item["name"]) for item in found["items"]] == [
        ("tt1", "The Avengers"), ("tt2", "Avengers: Endgame"), ("tt9", "Thunderbolts*"),
        ("tt3", "Ultimate Avengers"), ("tt4", "Next Avengers"),
    ]  # fmt: skip
    # One that failed is named, and the others still answered.
    assert found["problems"] == [{"addon": "Three", "message": "It didn't answer."}]
    assert not any("/year" in url for url in seen)
    with pytest.raises(addons.AddonError):
        addons.search(owned, "movie", "   ")
    # Nothing that offers this kind: nothing is asked, and nothing is found.
    seen.clear()
    assert addons.search(owned, "series", "avengers") == {"items": [], "problems": []}
    assert seen == []


def test_a_searchs_pages_are_read_until_nothing_new_comes(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def http(url: str) -> Any:
        seen.append(url.rsplit("/", 1)[1])
        if "skip=" in url:  # this add-on takes no notice of skip: the same again
            return {**titles(("tt1", "Avengers One"), ("tt2", "Avengers Two")), "hasMore": True}
        if "the%20" in url:
            return titles()
        return {**titles(("tt1", "Avengers One"), ("tt2", "Avengers Two")), "hasMore": True}

    monkeypatch.setattr(addons, "_http", http)
    found = addons.search(
        [searchable("One", "https://one.invalid", pages=True)], "movie", "avengers"
    )
    assert [item["id"] for item in found["items"]] == ["tt1", "tt2"]
    # The second page brought nothing new, so there was no third.
    assert seen == [
        "search=avengers.json",
        "search=avengers&skip=2.json",
        "search=the%20avengers.json",
    ]
    # A search that starts with "the" isn't asked a second way.
    seen.clear()
    addons.search([searchable("One", "https://one.invalid")], "movie", "The Avengers")
    assert seen == ["search=The%20Avengers.json"]


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


# ---- add-ons for adults only (the owner, 2026-10-08) ---------------------------------------

GROWN = "https://adults.example.invalid/x"


def grown_manifest(**more: Any) -> dict[str, Any]:
    """A made-up add-on that says it's for adults only. No real one is tested against."""
    return {
        "id": "invalid.example.grown", "name": "Grown-ups", "version": "1.0.0",
        "resources": ["catalog", "meta", "stream"], "types": ["movie"],
        "catalogs": [{"type": "movie", "id": "new", "name": "New",
                      "extra": [{"name": "genre", "options": ["Ecchi", "Other"]}]}],
        "behaviorHints": {"adult": True}, **more,
    }  # fmt: skip


@pytest.fixture
def grown(asked: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    path = tmp_path / "grown-manifest.json"
    path.write_text(json.dumps(grown_manifest()), encoding="utf-8")
    monkeypatch.setitem(ANSWERS, f"{GROWN}/manifest.json", str(path))
    return asked


def test_an_adults_only_add_on_says_so_and_keeps_its_own_genres(grown: list[str]) -> None:
    added = addons.add(f"{GROWN}/manifest.json")[-1]
    assert added["adult"] is True
    # Its lists are its own business: the genres taken off other add-ons' lists stay.
    assert added["catalogs"][0]["extra"][0]["options"] == ["Ecchi", "Other"]
    # The app's own aren't, and nor is an add-on kept before the flag existed.
    assert [one["adult"] for one in addons.listed()[:4]] == [False] * 4
    assert addons.is_adult({"id": "old"}) is False


def test_a_childs_profile_never_sees_an_adults_only_add_on(grown: list[str]) -> None:
    addons.add(f"{GROWN}/manifest.json")
    addons.set_for_child(True)
    names = [one["name"] for one in addons.listed()]
    assert "Grown-ups" not in names and len(names) == 4
    # Nothing can be asked of it there: it isn't in the list at all.
    with pytest.raises(addons.AddonError):
        addons.named("invalid.example.grown")
    assert all(
        s["addon"] != "Grown-ups" for s in addons.streams(addons.listed(), "movie", "x1")["sources"]
    )
    # Nor added, nor its mark changed.
    with pytest.raises(addons.AddonError, match="adults only"):
        addons.add(f"{GROWN}/manifest.json")
    with pytest.raises(addons.AddonError):
        addons.mark_adult("com.linvo.cinemeta", True)

    # Changes made in the child's profile leave it where it was, unseen.
    addons.remove("invalid.example.grown")  # can't be seen, so can't be removed
    order = [one["id"] for one in addons.listed()][::-1]
    assert [one["id"] for one in addons.reorder(order)] == order
    addons.remove("com.linvo.cinemeta")
    assert len(addons.restore()) == 4
    addons.set_for_child(False)
    assert [one["id"] for one in addons.listed()][:3] == order[:3]
    assert "invalid.example.grown" in [one["id"] for one in addons.listed()]


def test_the_owner_can_mark_an_add_on_as_adults_only(grown: list[str]) -> None:
    marked = addons.mark_adult("org.stremio.pubdomainmovies", True)
    assert [one["adult"] for one in marked] == [False, False, True, False]
    # Read afresh, it keeps the owner's mark, though it doesn't say so itself.
    again = addons.add(f"{FILMS}/manifest.json")
    assert next(o for o in again if o["id"] == "org.stremio.pubdomainmovies")["adult"] is True
    assert not any(o["adult"] for o in addons.mark_adult("org.stremio.pubdomainmovies", False))
    with pytest.raises(addons.AddonError):
        addons.mark_adult("gone", True)


def test_each_side_of_the_fence_answers_only_its_own_pages(
    grown: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    addons.add(f"{GROWN}/manifest.json")
    for name, answer in (
        ("stream/movie/tt0012349.json", {"streams": [{"url": "https://adults.example.invalid/v.mp4"}]}),
        ("meta/movie/tt0012349.json",
         {"meta": {"id": "tt0012349", "type": "movie", "name": "Not The Kid"}}),
    ):  # fmt: skip
        path = tmp_path / name.replace("/", "-")
        path.write_text(json.dumps(answer), encoding="utf-8")
        monkeypatch.setitem(ANSWERS, f"{GROWN}/{name}", str(path))
    # An ordinary film's page: never anything from the add-on for adults, though it
    # says it has ways to play that film.
    usual = addons.streams(addons.kept_to(False), "movie", "tt0012349")
    assert [s["addon"] for s in usual["sources"]] == ["Public Domain Movies"]
    assert addons.details("movie", "tt0012349")["name"] == "The Kid"
    # The Finder for adults: only from it.
    theirs = addons.streams(addons.kept_to(True), "movie", "tt0012349")
    assert [s["addon"] for s in theirs["sources"]] == ["Grown-ups"]
    assert addons.details("movie", "tt0012349", adult=True)["name"] == "Not The Kid"
    # An add-on named from the wrong side isn't asked.
    with pytest.raises(addons.AddonError):
        addons.details("movie", "tt0012349", "invalid.example.grown")
    with pytest.raises(addons.AddonError):
        addons.details("movie", "tt0012349", "com.linvo.cinemeta", adult=True)
    # In a child's profile that side is empty.
    addons.set_for_child(True)
    assert addons.kept_to(True) == []
