"""youtube: responses recorded from the installed ytmusicapi (tests/fixtures/ytm/, replayed),
the album lookup, the search cache and the rate limiter."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import requests
from conftest import YTM_FIXTURES
from ytmusicapi.exceptions import YTMusicServerError

from musicorg import youtube
from musicorg.errors import ReplayMissError, VideoUnavailableError, YouTubePausedError
from musicorg.youtube import Album, AlbumTrack, RateLimiter


def test_search_results_carry_the_fields_matching_needs() -> None:
    results = youtube.search_songs("The Chainsmokers Closer")
    assert len(results) == 10  # 20 came back for limit=10; the rest are dropped
    closer = next(c for c in results if c.video_id == "r7zTKRonHXM")
    assert closer.title == "Closer (feat. Halsey)"
    assert closer.artists == ("The Chainsmokers",)
    assert (closer.album, closer.duration_s, closer.is_explicit) == ("Collage", 246, False)
    assert closer.album_browse_id and closer.album_browse_id.startswith("MPRE")
    assert closer.video_type == youtube.OFFICIAL_AUDIO and closer.is_official_audio
    assert closer.thumbnail and closer.thumbnail.startswith("https://")
    assert closer.link == "https://music.youtube.com/watch?v=r7zTKRonHXM"
    assert youtube.Candidate.from_dict(closer.to_dict()) == closer


def test_a_search_that_was_never_recorded_fails_in_replay() -> None:
    with pytest.raises(ReplayMissError, match="record_ytm.py"):
        youtube.search_songs("a search nobody recorded")


class DictCache:
    def __init__(self) -> None:
        self.saved: dict[str, Any] = {}

    def cached_search(self, key: str, *, max_age_days: float) -> Any | None:
        assert max_age_days == 30
        return self.saved.get(key)

    def put_search(self, key: str, response: Any) -> None:
        self.saved[key] = response


def test_search_responses_are_cached_under_the_normalised_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = DictCache()
    first = youtube.search_songs("The Chainsmokers Closer", cache=cache)
    assert list(cache.saved) == ["the chainsmokers closer"]

    def offline(*args: object) -> None:
        raise AssertionError("should have come from the cache")

    monkeypatch.setattr(youtube, "read_recording", offline)
    assert youtube.search_songs("the  CHAINSMOKERS closer", cache=cache) == first
    with pytest.raises(AssertionError):
        youtube.search_songs("The Chainsmokers Closer", cache=cache, refresh=True)


def test_get_track() -> None:
    track = youtube.get_track("xjj_OVvVQFc")
    assert track is not None
    assert (track.title, track.artists, track.duration_s) == (
        "Crave You (feat. Giselle)", ("Flight Facilities",), 235)  # fmt: skip
    assert track.is_explicit is None  # the watch playlist doesn't say
    assert track.year == "2013"
    # A watch playlist's track calls its pictures `thumbnail`; the largest is kept.
    assert track.thumbnail is not None and track.thumbnail.startswith("https://")
    # A video that doesn't exist: ytmusicapi raises "No content returned by the server".
    assert youtube.get_track("zzzzzzzzzzz") is None


def test_get_track_refuses_a_different_track(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record(tmp_path, "watch", "aaaaaaaaaaa", {"tracks": [{"videoId": "bbbbbbbbbbb", "title": "X"}]})
    monkeypatch.setenv(youtube.REPLAY_ENV, str(tmp_path))
    assert youtube.get_track("aaaaaaaaaaa") is None


def test_a_pasted_links_names_arrive_as_plain_text() -> None:
    """The recorded answer for a link (`get_watch_playlist`, ytmusicapi 1.12.3) names the
    artist "Michael Franti & Spearhead": a plain "&", not the web entity "&amp;". So the
    Candidate made from a pasted link, and the tags written from it when it's accepted,
    carry the name as it is; there's nothing for the gate to decode."""
    track = youtube.get_track("SpTYh-uYgQs")
    assert track is not None
    assert (track.title, track.artists) == ("Bomb the World", ("Michael Franti & Spearhead",))
    assert track.to_dict()["artists"] == ["Michael Franti & Spearhead"]


def test_no_recorded_answer_carries_a_web_entity() -> None:
    """Every recorded YouTube Music answer (searches, links, radios, albums, videos,
    playlists, artist pages) gives its text plain: many names with "&" in them, and not
    one web entity ("&amp;", "&#39;"). If a new recording ever carries one, this fails:
    decode it then at the gate (`youtube._from_track` and `_names`), in that one place,
    before it can reach a message or a song's tags."""
    entity = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]*|#[0-9]+|#[xX][0-9A-Fa-f]+);")
    with_entity: list[str] = []
    with_ampersand = 0

    def look(value: Any, where: str) -> None:
        nonlocal with_ampersand
        if isinstance(value, dict):
            for inner in value.values():
                look(inner, where)
        elif isinstance(value, list):
            for inner in value:
                look(inner, where)
        elif isinstance(value, str) and not value.startswith("http"):
            with_ampersand += "&" in value
            if entity.search(value):
                with_entity.append(f"{where}: {value}")

    recordings = sorted(YTM_FIXTURES.rglob("*.json"))
    for path in recordings:
        look(json.loads(path.read_text(encoding="utf-8")).get("response"), path.name)
    assert len(recordings) > 50 and with_ampersand > 100  # there was something to look at
    assert with_entity == []


def test_find_video_takes_only_the_artists_official_video() -> None:
    """Recorded "videos" searches: the official video of the same song, or nothing."""
    found = youtube.find_video("Work Out", "J. Cole")
    assert found is not None
    assert (found.video_id, found.title, found.artists) == ("W5hSdGt2M8w", "Work Out", ("J. Cole",))
    assert found.video_type == youtube.OFFICIAL_VIDEO
    assert found.duration_s == 245  # the video runs longer than the song (236 s)
    # Only other people's uploads, and another artist's official video: nothing is taken.
    assert youtube.find_video("cLOUDs", "J. Cole") is None
    remix = youtube.find_video("Crave You (Adventure Club Remix)", "Flight Facilities")
    assert remix is not None and remix.title.startswith("Crave You (Adventure Club Remix)")


def test_a_video_of_another_version_or_artist_isnt_the_songs() -> None:
    from musicorg.normalize import parse_title

    remix = youtube.find_video("Crave You (Adventure Club Remix)", "Flight Facilities")
    assert remix is not None
    assert not youtube._same_song(parse_title("Crave You"), "Flight Facilities", remix)
    assert not youtube._same_song(parse_title(remix.title), "Somebody Else", remix)
    assert not youtube._same_song(parse_title(remix.title), "", remix)
    # "The" in an artist's name doesn't count: libraries and YouTube Music differ on it.
    assert youtube._same_song(
        parse_title("Crave You (Adventure Club Remix)"), "The Flight Facilities", remix
    )
    assert youtube.artist_key("The Notorious B.I.G.") == youtube.artist_key("Notorious B.I.G.")
    assert youtube.artist_key("The The") == "the the"
    assert youtube._same_song(  # a remaster is the same recording, and so is a second artist
        parse_title("Crave You - Adventure Club Remix (2019 Remaster)"),
        "Flight Facilities, Giselle",
        remix,
    )


def test_a_version_in_both_the_title_and_the_tag_is_searched_for_once() -> None:
    """A library title names its version ("Crave You (Adventure Club Remix)") and its
    version tag says the same. The search words are the title's alone: with the remix
    named a second time the search would be another one, and no recording answers it."""
    title = "Crave You (Adventure Club Remix)"
    plain = youtube.find_video(title, "Flight Facilities")
    tagged = youtube.find_video(title, "Flight Facilities", versions=("remix:adventure club",))
    assert plain is not None and tagged == plain


def test_a_remix_never_gets_the_originals_video() -> None:
    """A library copy can be titled plainly while the rip it came from was "Song R" or
    "Song (Somebody Remix)": the version comes with the request, not from the title."""
    fixtures = Path(__file__).parent / "fixtures" / "ytm"
    recorded = youtube.read_recording(fixtures, "videos", "j cole work out")

    class Cache:
        def cached_search(self, key: str, *, max_age_days: float) -> Any | None:
            # The search for "... remix" finds what the plain search finds: the original.
            return recorded if key == "videos j cole work out remix" else None

        def put_search(self, key: str, response: Any) -> None:
            pytest.fail("nothing new to keep")

    assert youtube.find_video("Work Out", "J. Cole", versions=("remix",), cache=Cache()) is None
    # A remaster or an explicit mark is still the same recording.
    same = youtube.find_video("Work Out", "J. Cole", versions=("remaster:2011", "explicit"))
    assert same is not None and same.video_id == "W5hSdGt2M8w"
    # Which remix is known: its own official video is found, by the right words.
    remix = youtube.find_video("Crave You", "Flight Facilities", versions=("remix:adventure club",))
    assert remix is not None and "Adventure Club Remix" in remix.title
    assert youtube.find_video("Crave You (Adventure Club Remix)", "Flight Facilities",
                              versions=("remix:adventure club",)) == remix  # fmt: skip


def test_a_title_with_the_owners_remix_mark_gets_no_video() -> None:
    """A copy named after the owner's rip is titled "Work Out R" and tagged `remix`
    (2026-10-04). Which remix it is isn't known, so no video is its video: least of all
    the original's, which is what such a search finds."""
    fixtures = Path(__file__).parent / "fixtures" / "ytm"
    recorded = youtube.read_recording(fixtures, "videos", "j cole work out")
    asked: list[str] = []

    class Cache:
        def cached_search(self, key: str, *, max_age_days: float) -> Any | None:
            asked.append(key)
            return recorded  # the original's official video

        def put_search(self, key: str, response: Any) -> None:
            pytest.fail("nothing new to keep")

    assert youtube.find_video("Work Out R", "J. Cole", versions=("remix",), cache=Cache()) is None
    assert asked == ["videos j cole work out r remix"]  # the title as it stands, said once
    # The plain song, asked the same way, does get that video: it's the R that stops it.
    assert youtube.find_video("Work Out", "J. Cole", cache=Cache()) is not None


def test_video_searches_are_cached_apart_from_song_searches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Cache:
        def __init__(self) -> None:
            self.kept: dict[str, Any] = {}

        def cached_search(self, key: str, *, max_age_days: float) -> Any | None:
            return self.kept.get(key)

        def put_search(self, key: str, response: Any) -> None:
            self.kept[key] = response

    cache = Cache()
    assert youtube.find_video("Work Out", "J. Cole", cache=cache) is not None
    assert list(cache.kept) == ["videos j cole work out"]
    monkeypatch.setattr(youtube, "_fetch", lambda *a: pytest.fail("asked YouTube again"))
    assert youtube.find_video("Work Out", "J. Cole", cache=cache) is not None


def test_get_album() -> None:
    album = youtube.get_album("MPREb_blrjqA5PY0E")
    assert (album.title, album.artists, album.year, album.track_count) == (
        "Number Ones", ("Michael Jackson",), "2003", 18)  # fmt: skip
    assert [t.track_number for t in album.tracks] == list(range(1, 19))


def test_find_track_by_video_id() -> None:
    album = youtube.get_album("MPREb_blrjqA5PY0E")
    found = youtube.find_track(album, video_id="-e-y-1VRZ3I", title="?", duration_s=None)
    assert found == (5, 18)


def test_find_track_by_title_and_length() -> None:
    # The album lists "Crave You" under a different videoId from the one search gives.
    album = youtube.get_album("MPREb_d8g28l4HU1r")
    assert not any(t.video_id == "xjj_OVvVQFc" for t in album.tracks)
    found = youtube.find_track(
        album, video_id="xjj_OVvVQFc", title="Crave You (feat. Giselle)", duration_s=235
    )
    assert found == (14, 14)
    # 3 s out is too far, and the reprise's title is different: no guess.
    assert youtube.find_track(
        album, video_id="xjj_OVvVQFc", title="Crave You (feat. Giselle)", duration_s=239
    ) == (None, None)


def test_find_track_never_guesses_between_two() -> None:
    twice = (
        AlbumTrack("a", "Song", 1, 200, None, None),
        AlbumTrack("b", "Song", 2, 201, None, None),
    )
    album = Album("MPREb_x", "A", ("B",), None, 2, None, twice)
    assert youtube.find_track(album, video_id="c", title="Song", duration_s=200) == (None, None)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("3:07", 187), ("0:59", 59), ("1:02:03", 3723), ("", None), ("3.07", None), (None, None)],
)
def test_parse_length(text: str | None, seconds: int | None) -> None:
    assert youtube.parse_length(text) == seconds


def test_recording_names_are_safe_everywhere(tmp_path: Path) -> None:
    path = youtube.recording_path(tmp_path, "search", "米津玄師 lemon / ac/dc: <live>")
    assert path.parent == tmp_path / "search"
    assert path.name.isascii() and not set(path.name) & set('<>:"/\\|?* ')
    assert path == youtube.recording_path(tmp_path, "search", "米津玄師 lemon / ac/dc: <live>")
    assert path != youtube.recording_path(tmp_path, "search", "米津玄師 lemon / ac dc: <live>")


def record(folder: Path, kind: str, key: str, response: Any) -> None:
    path = youtube.recording_path(folder, kind, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"kind": kind, "key": key, "response": response}), encoding="utf-8")


# ---- the rate limiter ------------------------------------------------------------------


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []
        self.wall = datetime(2026, 9, 29, 3, 0, tzinfo=UTC)

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 3))
        self.now += seconds


def limiter_with(clock: FakeTime, jitter: float = 0.0) -> RateLimiter:
    return RateLimiter(
        jitter=jitter,
        clock=clock.clock,
        sleep=clock.sleep,
        uniform=lambda low, high: high,  # the most jitter allowed
        now=lambda: clock.wall,
    )


def test_one_request_per_interval() -> None:
    clock = FakeTime()
    limiter = limiter_with(clock)
    for _ in range(3):
        limiter.call(lambda: None)
    assert clock.slept == [1.5, 1.5]
    assert limiter.requests == 3
    clock.now += 10  # a pause between requests counts
    limiter.call(lambda: None)
    assert clock.slept == [1.5, 1.5]


def test_a_request_someone_is_waiting_on_takes_the_next_turn() -> None:
    """A click to play goes ahead of the look-ups already in line, and no sooner than
    the gap allows."""
    import threading
    import time

    # The same gap as any other request.
    clock = FakeTime()
    limiter = limiter_with(clock)
    limiter.call(lambda: None)
    limiter.call(lambda: None, first=True)
    assert clock.slept == [1.5]

    # One request is waiting out its gap, two more are in line behind it, and then one
    # comes that someone is waiting on.
    gap_over = threading.Event()
    limiter = RateLimiter(1.0, 0, clock=lambda: 0.0, sleep=lambda s: gap_over.wait(5))
    limiter.wait()  # the first of all goes at once, and starts the gap
    order: list[str] = []
    noted = threading.Lock()

    def ask(name: str, first: bool) -> None:
        limiter.wait(first=first)
        with noted:
            order.append(name)

    threads = [threading.Thread(target=ask, args=(name, False)) for name in ("a", "b", "c")]
    for thread in threads:
        thread.start()
    time.sleep(0.1)  # all three have joined the line
    play = threading.Thread(target=ask, args=("play", True))
    play.start()
    for _ in range(200):
        if limiter._waiting_first == 1:
            break
        time.sleep(0.01)
    assert limiter._waiting_first == 1 and order == []
    gap_over.set()
    for thread in [*threads, play]:
        thread.join(5)
    # Whichever of the three had the turn finishes it; the click is next, then the rest.
    assert order[1] == "play" and sorted(order) == ["a", "b", "c", "play"]
    assert limiter.requests == 5


def test_what_someone_is_looking_at_takes_the_next_turn_too() -> None:
    """A search typed or a page opened: marked for the thread that answers it, and only
    for that thread, and its requests go ahead of the line like a click to play."""
    import threading
    import time

    assert not youtube.is_waited_on()
    with youtube.waited_on():
        with youtube.waited_on():
            assert youtube.is_waited_on()
        assert youtube.is_waited_on()
        elsewhere: list[bool] = []
        other = threading.Thread(target=lambda: elsewhere.append(youtube.is_waited_on()))
        other.start()
        other.join(5)
        assert elsewhere == [False]
    assert not youtube.is_waited_on()

    gap_over = threading.Event()
    limiter = RateLimiter(1.0, 0, clock=lambda: 0.0, sleep=lambda s: gap_over.wait(5))
    limiter.wait()
    order: list[str] = []

    def batch(name: str) -> None:
        limiter.wait()
        order.append(name)

    def search() -> None:
        with youtube.waited_on():
            limiter.wait()
        order.append("search")

    threads = [threading.Thread(target=batch, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    time.sleep(0.1)
    threads.append(threading.Thread(target=search))
    threads[-1].start()
    for _ in range(200):
        if limiter._waiting_first == 1:
            break
        time.sleep(0.01)
    gap_over.set()
    for thread in threads:
        thread.join(5)
    assert order[1] == "search" and sorted(order) == ["a", "b", "search"]


def test_jitter() -> None:
    clock = FakeTime()
    limiter = limiter_with(clock, jitter=0.5)
    limiter.call(lambda: None)
    limiter.call(lambda: None)
    assert clock.slept == [2.0]


def test_backoff_then_pause_after_three_failures() -> None:
    clock = FakeTime()
    limiter = limiter_with(clock)
    attempts: list[int] = []

    def refused() -> None:
        attempts.append(1)
        raise YTMusicServerError("Server returned HTTP 429: Too Many Requests.\nslow down")

    with pytest.raises(YouTubePausedError) as caught:
        limiter.call(refused)
    assert len(attempts) == 3
    assert [s for s in clock.slept if s != 1.5] == [5.0, 10.0]  # exponential backoff
    assert caught.value.resume_at == clock.wall + timedelta(minutes=30)
    assert caught.value.exit_code == 4
    assert "slowing us down" in caught.value.message
    # Until then, nothing more is sent.
    with pytest.raises(YouTubePausedError):
        limiter.call(lambda: attempts.append(1))
    assert len(attempts) == 3
    clock.wall += timedelta(minutes=31)
    limiter.call(lambda: None)


def test_a_success_resets_the_failure_count() -> None:
    clock = FakeTime()
    limiter = limiter_with(clock)
    results = iter([requests.ConnectionError("down"), requests.Timeout("slow"), "ok"])

    def flaky() -> str:
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    assert limiter.call(flaky) == "ok"
    assert limiter.failures == 0


def test_errors_that_are_not_a_slow_down_pass_straight_through() -> None:
    clock = FakeTime()
    limiter = limiter_with(clock)
    with pytest.raises(YTMusicServerError):
        limiter.call(lambda: (_ for _ in ()).throw(YTMusicServerError("No content returned")))
    assert limiter.failures == 0 and limiter.requests == 1


@pytest.mark.parametrize(
    ("exc", "slow"),
    [
        (requests.ConnectionError("down"), True),
        (requests.Timeout("slow"), True),
        (YTMusicServerError("Server returned HTTP 429: Too Many Requests."), True),
        (YTMusicServerError("Server returned HTTP 503: Service Unavailable."), True),
        (YTMusicServerError("Server returned HTTP 400: Bad Request."), False),
        (json.JSONDecodeError("Expecting value", "<html>", 0), True),
        (KeyError("tracks"), False),
    ],
)
def test_what_counts_as_a_slow_down(exc: Exception, slow: bool) -> None:
    assert youtube.is_slow_down(exc) is slow


def test_the_pause_message_names_a_time() -> None:
    error = youtube.paused_error(datetime(2026, 9, 29, 17, 10, tzinfo=UTC))
    assert "try again after" in error.message
    assert error.message.split("after ")[1][:8].rstrip(". ").endswith(("am", "pm"))


@pytest.mark.live
def test_live_search() -> None:
    results = youtube.search_songs("Flight Facilities Crave You")
    assert any(c.is_official_audio and "Flight Facilities" in c.artists for c in results)


# ---- songs like a song, an artist or a genre (Discover, v0.4) ----------------------------


class AgedCache:
    """A cache that remembers how old an answer each lookup would accept."""

    def __init__(self) -> None:
        self.kept: dict[str, Any] = {}
        self.ages: dict[str, float] = {}

    def cached_search(self, key: str, *, max_age_days: float) -> Any | None:
        self.ages[key] = max_age_days
        return self.kept.get(key)

    def put_search(self, key: str, response: Any) -> None:
        self.kept[key] = response


def test_a_songs_radio(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = AgedCache()
    tracks = youtube.radio("5qZQEq_C3vc", cache=cache)
    assert len(tracks) == 25  # the recording keeps the first 25 of about 50
    first = tracks[0]
    assert (first.video_id, first.title, first.artists) == ("5qZQEq_C3vc", "Numb", ("Linkin Park",))
    assert first.duration_s == 188 and first.is_official_audio  # from "3:08"
    assert first.album and first.album_browse_id and first.year == "2003"
    assert first.thumbnail and first.thumbnail.startswith("https://")
    assert first.is_explicit is None  # a watch playlist doesn't say
    assert len({t.video_id for t in tracks}) == 25
    # Kept for a week, cut down to what's read; asked for once.
    assert cache.ages == {"radio 5qZQEq_C3vc": 7}
    kept = cache.kept["radio 5qZQEq_C3vc"]["tracks"][0]
    assert set(kept) <= set(youtube.RADIO_TRACK_KEEP) and len(kept["thumbnail"]) == 1
    monkeypatch.setattr(youtube, "_fetch", lambda *a: pytest.fail("asked YouTube again"))
    assert youtube.radio("5qZQEq_C3vc", cache=cache) == tracks


def test_an_artists_radio() -> None:
    cache = AgedCache()
    found = youtube.artist_radio("linkin park", cache=cache)
    assert found is not None and found.name == "Linkin Park"
    assert len(found.tracks) == 30
    theirs = [t for t in found.tracks if t.artists == ("Linkin Park",)]
    assert 3 <= len(theirs) < len(found.tracks)  # their songs, and other bands'
    assert cache.ages["artists linkin park"] == 30
    assert [age for key, age in cache.ages.items() if key.startswith("artist radio ")] == [7]
    # Only an artist by that name: the search for one nobody has returns other artists.
    assert youtube.artist_radio("Zzyzx Qwfp Band") is None
    assert youtube.artist_radio("  ") is None


def test_a_genres_playlist() -> None:
    found = youtube.genre_playlist("Jazz")
    assert found is not None and found.title == "Cozy Jazz"
    assert len(found.tracks) == 12
    kinds = {t.video_type for t in found.tracks}
    assert kinds == {youtube.OFFICIAL_AUDIO, youtube.OFFICIAL_VIDEO}  # mostly not songs
    assert all(t.duration_s for t in found.tracks)


def test_which_playlist_is_the_genres() -> None:
    def listed(*titles: str, author: str = "YouTube Music") -> list[dict[str, str]]:
        return [{"title": t, "browseId": f"VL{n}", "author": author} for n, t in enumerate(titles)]

    def choice(found: list[dict[str, str]]) -> str | None:
        chosen = youtube._genre_choice(found, "hip hop")
        return chosen["title"] if chosen else None

    mixed = listed("Aussie Hip-Hop Golds", "Lofi Loft", "Hip Hop Hits 2024", "Hip Hop Hits 2021",
                   "Trending Hip-Hop", "Hip-Hop Christmas")  # fmt: skip
    assert choice(mixed) == "Hip Hop Hits 2024"  # named for the genre and its hits
    assert choice(mixed[:2] + mixed[4:]) == "Trending Hip-Hop"  # else the shortest name with it
    assert choice(listed("Lofi Loft", "Pega a Visão")) == "Lofi Loft"  # else the first
    assert choice(listed("Hip Hop Hits 2024", author="Somebody")) is None  # never a listener's
    assert choice([]) is None and choice("nonsense") is None  # type: ignore[arg-type]


def test_same_song() -> None:
    numb = youtube.radio("5qZQEq_C3vc")[0]
    assert youtube.same_song("Numb", "Linkin Park", numb)
    assert youtube.same_song("numb (Remastered)", "LINKIN PARK feat. Somebody", numb)
    assert not youtube.same_song("Numb (Live)", "Linkin Park", numb)
    assert not youtube.same_song("Numb", "Somebody Else", numb)


@pytest.mark.parametrize(
    ("said", "plain"),
    [
        ("ERROR: [youtube] DuQGokwsWF8: Sign in to confirm your age. This video may be "
         "inappropriate for some users. Use --cookies-from-browser or --cookies for the "
         "authentication. See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ", "age-restricted"),
        ("ERROR: [youtube] abcdefghijk: Private video. Sign in if you've been granted access",
         "private video"),
        ("ERROR: [youtube] abcdefghijk: Video unavailable", "isn't on YouTube any more"),
        ("ERROR: [youtube] abcdefghijk: The uploader has not made this video available in your "
         "country", "this country"),
        ("ERROR: [youtube] abcdefghijk: Join this channel to get access to members-only content",
         "paying members"),
    ],
)  # fmt: skip
def test_why_youtube_wont_give_a_video_is_said_plainly(said: str, plain: str) -> None:
    error = youtube.download_problem("abcdefghijk", Exception(said))
    assert isinstance(error, VideoUnavailableError)
    assert plain in error.message
    # None of yt-dlp's own words for people at a command line come through.
    for leftover in ("--cookies", "http", "wiki", "abcdefghijk", "DuQGokwsWF8"):
        assert leftover not in error.message


def test_a_search_result_says_how_often_it_has_been_played() -> None:
    found = youtube.search_songs("alessia cara here")
    assert found and all(c.plays for c in found[:3])
    assert found[0].plays == "497M"  # as YouTube Music writes it
    assert youtube.Candidate.from_dict(found[0].to_dict()) == found[0]
