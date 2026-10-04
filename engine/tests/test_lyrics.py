"""lyrics (step 10): LRCLIB then YouTube Music, the version and timing guards, LRC checks,
and the 30-day cache. Answers are recorded at test time with the shapes LRCLIB and
ytmusicapi really give (checked 2026-09-30), but every lyric is made up: real lyrics are
copyrighted, and the repository is public."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from musicorg import lyrics, youtube
from musicorg.index import open_index
from musicorg.library import Library

SYNCED = "[00:01.00]First made-up line\n[00:04.50]Second made-up line\n"
PLAIN = "First made-up line\nSecond made-up line"
QUERY = lyrics.Query(title="Test Song", artist="Test Band", album="Test Album", duration_s=200.0)


@pytest.fixture
def replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "replay"
    monkeypatch.setenv(youtube.REPLAY_ENV, str(folder))
    return folder


def record(folder: Path, kind: str, key: str, response: Any) -> None:
    path = youtube.recording_path(folder, kind, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"kind": kind, "key": key, "response": response}), "utf-8")


def lrclib_key(path: str, **params: Any) -> str:
    return f"lrclib {path} " + "&".join(f"{k}={params[k]}" for k in sorted(params))


def get_key(query: lyrics.Query = QUERY, **extra: Any) -> str:
    params = {"track_name": query.title, "artist_name": query.artist,
              "duration": int(round(query.duration_s or 0))}  # fmt: skip
    if query.album:
        params["album_name"] = query.album
    params.update(extra)
    return lrclib_key("get", **params)


def search_key(query: lyrics.Query = QUERY) -> str:
    return lrclib_key("search", track_name=query.title, artist_name=query.artist)


def lrclib_record(**fields: Any) -> dict[str, Any]:
    record = {"id": 1, "name": "Test Song", "trackName": "Test Song", "artistName": "Test Band",
              "albumName": "Test Album", "duration": 200.0, "instrumental": False,
              "hasWordSync": False, "plainLyrics": PLAIN, "syncedLyrics": SYNCED}  # fmt: skip
    return {**record, **fields}


def no_lrclib(folder: Path, query: lyrics.Query = QUERY) -> None:
    record(folder, "lrclib", get_key(query), None)  # a 404 is recorded as no answer
    record(folder, "lrclib", search_key(query), [])


def ytm_lyrics(folder: Path, video_id: str, found: dict[str, Any] | None) -> None:
    record(folder, "watch", video_id, {"tracks": [], "lyrics": "MPLYt_test" if found else None})
    if found:
        record(folder, "lyrics", "MPLYt_test", found)


# ---- LRCLIB ----------------------------------------------------------------------------


def test_synced_lyrics_from_lrclib(replay: Path) -> None:
    record(replay, "lrclib", get_key(), lrclib_record())
    found = lyrics.find(QUERY)
    assert (found.status, found.synced, found.plain, found.source) == (
        "synced", SYNCED, PLAIN, "LRCLIB",
    )  # fmt: skip


def test_plain_only(replay: Path) -> None:
    record(replay, "lrclib", get_key(), lrclib_record(syncedLyrics=None))
    found = lyrics.find(QUERY)
    assert (found.status, found.plain, found.synced) == ("plain", PLAIN, None)


def test_instrumental(replay: Path) -> None:
    record(replay, "lrclib", get_key(), lrclib_record(instrumental=True, plainLyrics=None,
                                                     syncedLyrics=None))  # fmt: skip
    assert lyrics.find(QUERY).status == "instrumental"


def test_not_found_anywhere(replay: Path) -> None:
    no_lrclib(replay)
    assert lyrics.find(QUERY).status == "not_found"


def test_the_album_is_left_out_when_unknown(replay: Path) -> None:
    query = lyrics.Query(title="Test Song", artist="Test Band", album="Unsorted", duration_s=200)
    record(replay, "lrclib", get_key(lyrics.Query("Test Song", "Test Band", None, 200)),
           lrclib_record())  # fmt: skip
    assert lyrics.find(query).status == "synced"


def test_a_search_hit_with_the_same_names_and_length(replay: Path) -> None:
    record(replay, "lrclib", get_key(), None)
    record(replay, "lrclib", search_key(), [
        lrclib_record(id=2, trackName="Test Song (Live)", duration=200.0),  # another title
        lrclib_record(id=3, duration=260.0),  # another length
        lrclib_record(id=4, artistName="test band", duration=201.0),
    ])  # fmt: skip
    found = lyrics.find(QUERY)
    assert found.status == "synced"


def test_a_search_with_no_close_result_finds_nothing(replay: Path) -> None:
    record(replay, "lrclib", get_key(), None)
    record(replay, "lrclib", search_key(), [lrclib_record(duration=230.0)])
    assert lyrics.find(QUERY).status == "not_found"


def test_a_remix_whose_record_isnt_the_remix_gets_plain_lyrics(replay: Path) -> None:
    query = lyrics.Query("Test Song", "Test Band", "Test Album", 200.0,
                         versions=("remix:someone",))  # fmt: skip
    record(replay, "lrclib", get_key(query), lrclib_record())
    found = lyrics.find(query)
    assert (found.status, found.synced, found.plain) == ("plain", None, PLAIN)
    assert found.note and found.note.startswith("version_uncertain")
    # The same remix, named as one, is fine.
    record(replay, "lrclib", get_key(query), lrclib_record(trackName="Test Song (Someone Remix)"))
    assert lyrics.find(query).status == "synced"


def test_a_title_with_the_owners_remix_mark_doesnt_take_the_plain_songs_record(
    replay: Path,
) -> None:
    """A copy named after the owner's rip is titled "Test Song R" (2026-10-04; before, it
    was titled "Test Song" and took that song's lyrics). LRCLIB is asked for the title
    as it stands, and a record called "Test Song" of the very same length isn't it."""
    query = lyrics.Query("Test Song R", "Test Band", None, 200.0, versions=("remix",))
    record(replay, "lrclib", get_key(query), None)
    record(replay, "lrclib", search_key(query), [lrclib_record()])
    found = lyrics.find(query)
    assert (found.status, found.synced, found.plain) == ("not_found", None, None)


def test_a_title_that_names_its_remix_takes_the_record_of_that_name(replay: Path) -> None:
    title = "Test Song (Someone Remix)"
    query = lyrics.Query(title, "Test Band", None, 200.0, versions=("remix:someone",))
    record(replay, "lrclib", get_key(query), None)
    record(replay, "lrclib", search_key(query), [
        lrclib_record(id=2),  # the plain song, the same length: not this track
        lrclib_record(id=3, trackName=title, syncedLyrics="[00:02.00]The remix's line\n"),
    ])  # fmt: skip
    found = lyrics.find(query)
    assert (found.status, found.synced) == ("synced", "[00:02.00]The remix's line\n")


def test_synced_lyrics_timed_for_another_length_are_left_out(replay: Path) -> None:
    query = lyrics.Query("Test Song", "Test Band", "Test Album", 230.0)  # a video intro
    record(replay, "lrclib", get_key(query), lrclib_record(duration=200.0))
    found = lyrics.find(query)
    assert (found.status, found.synced) == ("plain", None)
    assert found.note and found.note.startswith("timing")


def test_a_malformed_synced_text_is_rejected(replay: Path) -> None:
    record(replay, "lrclib", get_key(), lrclib_record(syncedLyrics="[00:05.00]b\n[00:01.00]a"))
    found = lyrics.find(QUERY)
    assert (found.status, found.synced) == ("plain", None)


# ---- YouTube Music ---------------------------------------------------------------------


def test_youtube_music_timed_lyrics_when_lrclib_has_none(replay: Path) -> None:
    query = lyrics.Query("Test Song", "Test Band", "Test Album", 200.0, video_id="vid00000001",
                         official_s=200.0)  # fmt: skip
    no_lrclib(replay, query)
    ytm_lyrics(replay, "vid00000001", {
        "hasTimestamps": True, "source": "Source: Musixmatch",
        "lyrics": [{"text": "First made-up line", "start_time": 1000, "end_time": 4000},
                   {"text": "Second made-up line", "start_time": 4500, "end_time": 8000}],
    })  # fmt: skip
    found = lyrics.find(query)
    assert found.status == "synced"
    assert found.synced == "[00:01.00]First made-up line\n[00:04.50]Second made-up line\n"
    assert found.source == "YouTube Music (Musixmatch)"
    assert found.plain == PLAIN


def test_youtube_music_plain_lyrics(replay: Path) -> None:
    query = lyrics.Query("Test Song", "Test Band", None, 200.0, video_id="vid00000002")
    no_lrclib(replay, query)
    ytm_lyrics(replay, "vid00000002", {"hasTimestamps": False, "source": "Source: LyricFind",
                                       "lyrics": PLAIN})  # fmt: skip
    found = lyrics.find(query)
    assert (found.status, found.plain, found.source) == (
        "plain", PLAIN, "YouTube Music (LyricFind)",
    )  # fmt: skip


def test_youtube_music_timing_is_checked_against_the_official_length(replay: Path) -> None:
    query = lyrics.Query("Test Song", "Test Band", None, 240.0, video_id="vid00000003",
                         official_s=200.0)  # fmt: skip
    no_lrclib(replay, query)
    ytm_lyrics(replay, "vid00000003", {"hasTimestamps": True, "source": "Source: Musixmatch",
               "lyrics": [{"text": "Made up", "start_time": 1000, "end_time": 2000}]})  # fmt: skip
    found = lyrics.find(query)
    assert (found.status, found.synced) == ("plain", None)


def test_answers_are_cached(replay: Path, lib: Library) -> None:
    record(replay, "lrclib", get_key(), lrclib_record())
    with open_index(lib.paths, write=True) as index:
        assert lyrics.find(QUERY, cache=index).status == "synced"
        for path in replay.rglob("*.json"):
            path.unlink()  # a second ask must not need the recordings
        assert lyrics.find(QUERY, cache=index).status == "synced"


def test_a_cached_not_found_isnt_asked_again(replay: Path, lib: Library) -> None:
    no_lrclib(replay)
    with open_index(lib.paths, write=True) as index:
        assert lyrics.find(QUERY, cache=index).status == "not_found"
        for path in replay.rglob("*.json"):
            path.unlink()
        assert lyrics.find(QUERY, cache=index).status == "not_found"


# ---- LRC -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        SYNCED,
        "[ar:Test Band]\n[ti:Test Song]\n[offset:+250]\n[00:01.00]a\n\n[00:01.00]b\n",
        "[00:01]a\n[01:02.345]b\n",
    ],
)
def test_valid_lrc(text: str) -> None:
    assert lyrics.check_lrc(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "",
        "just words",
        "[00:05.00]b\n[00:01.00]a\n",  # backwards
        "[00:61.00]a\n",  # no 61st second
        "[al:Test Album]\n[00:01.00]a\n",  # a header that isn't allowed
        "[ar:Test Band]\n",  # nothing timed
        None,
    ],
)
def test_invalid_lrc(text: str | None) -> None:
    assert lyrics.check_lrc(text) is None


def test_to_lrc() -> None:
    assert lyrics.to_lrc([(0, "a"), (61_234, "b"), (3_600_000, "c")]) == (
        "[00:00.00]a\n[01:01.23]b\n[60:00.00]c\n"
    )
