"""youtube: responses recorded from the installed ytmusicapi (tests/fixtures/ytm/, replayed),
the album lookup, the search cache and the rate limiter."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import requests
from ytmusicapi.exceptions import YTMusicServerError

from musicorg import youtube
from musicorg.errors import ReplayMissError, YouTubePausedError
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
    assert youtube._artist_key("The Notorious B.I.G.") == youtube._artist_key("Notorious B.I.G.")
    assert youtube._artist_key("The The") == "the the"
    assert youtube._same_song(  # a remaster is the same recording, and so is a second artist
        parse_title("Crave You - Adventure Club Remix (2019 Remaster)"),
        "Flight Facilities, Giselle",
        remix,
    )


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
