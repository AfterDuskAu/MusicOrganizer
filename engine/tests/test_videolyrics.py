"""videolyrics (v0.2): a song's lyrics timed to its video.

The fingerprints here are made up (random items), and so are the words: nothing in this
file is a real song's audio or lyrics, and nothing reaches the network.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from test_rpc import code, opened, out, result, root, server  # noqa: F401  (fixtures)

from musicorg import fingerprint, lyrics, rpc, videolyrics, youtube
from musicorg.errors import DownloadError, YouTubePausedError
from musicorg.index import open_index
from musicorg.library import Library
from musicorg.videolyrics import ITEM_S, Cue, Segment

# ---- made-up recordings ---------------------------------------------------------------------


def recording(seed: int, items: int) -> list[int]:
    rng = random.Random(seed)
    return [rng.getrandbits(32) for _ in range(items)]


def as_encoded_again(items: list[int], seed: int = 99) -> list[int]:
    """The same recording from another encode: about one item in three has a bit or two
    different, as two YouTube encodes of one master do."""
    rng = random.Random(seed)
    again = []
    for item in items:
        if rng.random() < 0.35:
            item ^= 1 << rng.randrange(32)
        if rng.random() < 0.1:
            item ^= 1 << rng.randrange(32)
        again.append(item)
    return again


SONG = recording(1, 1800)  # about 3:43
OTHER = recording(2, 2400)


def seconds(items: int) -> float:
    return items * ITEM_S


# ---- the time map ---------------------------------------------------------------------------


def test_a_video_with_an_intro_is_one_segment() -> None:
    video = OTHER[:250] + as_encoded_again(SONG)  # 31 s of something else first
    found = videolyrics.time_map(SONG, video)
    assert found.verdict == "aligned" and found.coverage > 0.97
    (segment,) = found.segments
    assert segment.video_start_s - segment.song_start_s == pytest.approx(seconds(250), abs=0.1)
    assert segment.speed == 1.0 and segment.ber < 0.05
    # A line sung a minute into the song is sung 31 seconds later in the video.
    assert videolyrics.video_times(found.segments, 60.0) == [
        pytest.approx(60.0 + seconds(250), abs=0.1)
    ]


def test_the_same_recording_is_one_segment_with_no_shift() -> None:
    found = videolyrics.time_map(SONG, as_encoded_again(SONG))
    (segment,) = found.segments
    assert found.verdict == "aligned" and found.coverage == pytest.approx(1.0, abs=0.01)
    assert (segment.video_start_s, segment.song_start_s) == (pytest.approx(0, abs=0.1),) * 2
    assert videolyrics.video_times(found.segments, 100.0) == [pytest.approx(100.0, abs=0.1)]


def test_a_scene_in_the_middle_makes_two_segments() -> None:
    again = as_encoded_again(SONG)
    video = OTHER[:80] + again[:900] + OTHER[500:740] + again[900:]
    found = videolyrics.time_map(SONG, video)
    first, second = found.segments
    assert found.verdict == "aligned"
    assert first.video_start_s - first.song_start_s == pytest.approx(seconds(80), abs=0.15)
    assert second.video_start_s - second.song_start_s == pytest.approx(seconds(320), abs=0.15)
    # The cut is found to within a second or so of where it is.
    assert first.song_end_s == pytest.approx(seconds(900), abs=1.5)
    assert second.song_start_s == pytest.approx(seconds(900), abs=1.5)
    before, after = seconds(900) - 20, seconds(900) + 20
    assert videolyrics.video_times(found.segments, before)[0] == pytest.approx(
        before + seconds(80), abs=0.15
    )
    assert videolyrics.video_times(found.segments, after)[0] == pytest.approx(
        after + seconds(320), abs=0.15
    )


def test_a_verse_the_video_leaves_out_isnt_in_the_map() -> None:
    again = as_encoded_again(SONG)
    video = again[:600] + again[1000:]  # 50 seconds of the song aren't in the video
    found = videolyrics.time_map(SONG, video)
    assert len(found.segments) == 2
    assert found.coverage == pytest.approx(1400 / 1800, abs=0.03)
    assert found.verdict == "partly"
    assert videolyrics.video_times(found.segments, seconds(800)) == []  # left out
    assert videolyrics.video_times(found.segments, seconds(1200))[0] == pytest.approx(
        seconds(800), abs=0.15
    )


def test_an_ending_played_twice_puts_its_lines_there_twice() -> None:
    again = as_encoded_again(SONG)
    video = again + as_encoded_again(SONG[1500:], seed=5)  # the last 37 seconds again
    found = videolyrics.time_map(SONG, video)
    assert len(found.segments) == 2
    places = videolyrics.video_times(found.segments, seconds(1650))
    assert places == [
        pytest.approx(seconds(1650), abs=0.15),
        pytest.approx(seconds(1800 + 150), abs=0.15),
    ]


def test_a_looped_ending_is_not_taken_for_a_repeat() -> None:
    # The song ends on a beat that loops every ten seconds: the last loops match
    # themselves one loop earlier just as well. It's still one stretch of video.
    loop = recording(7, 83)
    song = SONG[:1400] + loop * 4
    video = OTHER[:250] + as_encoded_again(song)
    found = videolyrics.time_map(song, video)
    (segment,) = found.segments
    assert found.verdict == "aligned" and found.coverage > 0.97
    assert segment.video_start_s - segment.song_start_s == pytest.approx(seconds(250), abs=0.1)


def test_a_video_playing_the_song_a_touch_fast_keeps_time_to_the_end() -> None:
    # One item dropped in every hundred: the song runs 1 % fast in the video.
    again = as_encoded_again(SONG)
    video = [item for n, item in enumerate(again) if n % 100 != 99]
    found = videolyrics.time_map(SONG, video)
    (segment,) = found.segments
    assert segment.speed == pytest.approx(1.01, abs=0.002)
    late = seconds(1700)
    assert videolyrics.video_times(found.segments, late) == [pytest.approx(late / 1.0101, abs=0.3)]


def test_another_recording_is_not_lined_up() -> None:
    found = videolyrics.time_map(SONG, OTHER)
    assert found.verdict == "different" and found.segments == ()
    assert videolyrics.time_map(SONG[:10], SONG[:10]).verdict == "different"  # too short
    # A few seconds in common (a sample) isn't the song.
    assert videolyrics.time_map(SONG, OTHER[:900] + SONG[400:520] + OTHER[900:]).verdict == (
        "different"
    )


# ---- moving lines through it ----------------------------------------------------------------


def test_lines_are_moved_and_put_in_the_videos_order() -> None:
    segments = [
        Segment(video_start_s=30.0, video_end_s=130.0, song_start_s=0.0, song_end_s=100.0),
        Segment(video_start_s=130.0, video_end_s=150.0, song_start_s=80.0, song_end_s=100.0),
    ]
    lines = [(10.0, "one"), (90.0, "two"), (100.8, "past the end"), (140.0, "left out")]
    assert videolyrics.retime(lines, segments) == [
        (40.0, "one"),
        (120.0, "two"),
        (130.0, "one"),  # that part of the song plays again: the line being sung there
        (140.0, "two"),
        (150.0, ""),  # nothing is sung from here on: the line is cleared
    ]
    assert videolyrics.retime(lines, []) == []
    places, blanks = videolyrics.line_places([at for at, _ in lines], segments)
    assert places == [[40.0, 130.0], [120.0, 140.0], [], []] and blanks == [150.0]
    slow = [Segment(video_start_s=0.0, video_end_s=101.0, song_start_s=0.0, song_end_s=100.0,
                    speed=100 / 101)]  # fmt: skip
    assert videolyrics.video_times(slow, 50.0) == [pytest.approx(50.5)]


def test_a_scene_in_the_video_clears_the_line_and_a_cut_into_line_is_shown() -> None:
    segments = [
        Segment(video_start_s=0.0, video_end_s=60.0, song_start_s=0.0, song_end_s=60.0),
        Segment(video_start_s=80.0, video_end_s=140.0, song_start_s=62.0, song_end_s=122.0),
        Segment(video_start_s=141.0, video_end_s=200.0, song_start_s=122.5, song_end_s=181.5),
    ]
    lines = [(50.0, "a"), (58.0, "b"), (66.0, "c"), (61.5, "x"), (122.2, "d"), (130.0, "e")]
    lines.sort()
    assert videolyrics.retime(lines, segments) == [
        (50.0, "a"),
        (58.0, "b"),
        (60.0, ""),  # twenty seconds of video with none of the song
        (80.0, "x"),  # the line being sung where the song comes back in (4 s of it left)
        (84.0, "c"),
        (pytest.approx(141.0), "d"),  # cut into, less than a second gone: still shown
        (pytest.approx(148.5), "e"),
        (200.0, ""),
    ]


def test_lrc_text_in_and_out() -> None:
    text = "[ar:Someone]\n[00:01.50]First\n[00:05.00][01:05.25]Again\n[00:03]\n\nnot a line\n"
    assert videolyrics.lines_of(text) == [
        (1.5, "First"), (3.0, ""), (5.0, "Again"), (65.25, "Again"),  # a pause is kept
    ]  # fmt: skip
    assert videolyrics.lines_of("[00:03.00]\n[00:09.00]\n") == []  # no words at all
    assert videolyrics.to_lrc([(1.5, "First"), (65.254, "Again")]) == (
        "[00:01.50]First\n[01:05.25]Again\n"
    )
    assert lyrics.check_lrc(videolyrics.to_lrc([(0.0, "a"), (3599.99, "b")])) is not None


# ---- captions -------------------------------------------------------------------------------

WORDS = ("amber basket candle dancer ember feather garden harbor island jacket kettle lantern "
         "marble needle orange pebble quiver ribbon saddle timber umbrella velvet window yellow "
         "zipper anchor bottle copper dragon engine forest glacier hammer iron jungle kitten "
         "ladder mirror napkin ocean pillow quartz rocket silver tunnel uncle valley wagon "
         "yonder zebra").split()  # fmt: skip


def made_up_lines(count: int, seed: int = 3) -> list[tuple[float, str]]:
    rng = random.Random(seed)
    return [(5.0 + 4.0 * n, " ".join(rng.sample(WORDS, 5)).capitalize()) for n in range(count)]


def captions_for(lines: list[tuple[float, str]], shift: Any) -> dict[str, Any]:
    """The label's captions for these lines: a cue per line, `shift(n)` seconds later."""
    events = [{"tStartMs": 0, "dDurationMs": 4000, "segs": [{"utf8": "[Music]"}]}]
    for number, (at, text) in enumerate(lines):
        events.append({
            "tStartMs": round((at + shift(number)) * 1000), "dDurationMs": 3500,
            "segs": [{"utf8": f"♪ {text.upper()}, ♪"}],
        })  # fmt: skip
    return {"events": events}


def test_lines_take_the_captions_times() -> None:
    lines = made_up_lines(30)
    cues = videolyrics.parse_captions(captions_for(lines, lambda n: 30.0))
    assert len(cues) == 31 and cues[1].sung and not cues[0].sung
    timed = videolyrics.caption_times(lines, cues)
    assert timed is not None
    assert [text for _, text in timed] == [text for _, text in lines]
    assert all(
        there == pytest.approx(at + 30.0) for (there, _), (at, _) in zip(timed, lines, strict=True)
    )


def test_a_shift_that_changes_part_way_is_followed() -> None:
    lines = made_up_lines(40)
    cues = videolyrics.parse_captions(captions_for(lines, lambda n: 8.0 if n < 20 else 29.0))
    timed = videolyrics.caption_times(lines, cues)
    assert timed is not None
    shifts = [round(there - at, 1) for (there, _), (at, _) in zip(timed, lines, strict=True)]
    assert shifts == [8.0] * 20 + [29.0] * 20  # right up to the change
    assert [there for there, _ in timed] == sorted(there for there, _ in timed)


def test_lines_the_captions_garbled_land_with_their_neighbours() -> None:
    lines = made_up_lines(30)
    said = [(at, "something else entirely here now" if n % 3 == 0 else text)
            for n, (at, text) in enumerate(lines)]  # fmt: skip
    cues = videolyrics.parse_captions(captions_for(said, lambda n: 12.5))
    timed = videolyrics.caption_times(lines, cues)
    assert timed is not None and len(timed) == 30
    assert all(
        there == pytest.approx(at + 12.5) for (there, _), (at, _) in zip(timed, lines, strict=True)
    )


def test_captions_that_arent_these_words_time_nothing() -> None:
    lines = made_up_lines(30)
    other = videolyrics.parse_captions(captions_for(made_up_lines(30, seed=8), lambda n: 0.0))
    assert videolyrics.caption_times(lines, other) is None  # another song, another language
    few = videolyrics.parse_captions(captions_for(lines[:2], lambda n: 0.0))
    assert videolyrics.caption_times(lines, few) is None  # "[Music]" and eight words
    assert videolyrics.caption_times([], other) is None
    assert videolyrics.parse_captions({"events": "nonsense"}) == []
    assert videolyrics.parse_captions(None) == []


def test_automatic_captions_carry_a_time_for_each_word() -> None:
    lines = made_up_lines(20)
    events = []
    for at, text in lines:
        words = text.lower().split()
        # Two lines to a caption event, as speech recognition gives them: only word
        # times can say when the second line starts.
        events.append({
            "tStartMs": round((at - 2.0) * 1000), "dDurationMs": 6000,
            "segs": [{"utf8": "uh", "tOffsetMs": 0}]
            + [{"utf8": f" {word}", "tOffsetMs": 2000 + 300 * n} for n, word in enumerate(words)],
        })  # fmt: skip
    cues = videolyrics.parse_captions({"events": events})
    assert cues[0].word_times is not None and len(cues[0].word_times) == 6
    timed = videolyrics.caption_times(lines, cues)
    assert timed is not None
    assert all(there == pytest.approx(at) for (there, _), (at, _) in zip(timed, lines, strict=True))


def test_a_caption_painted_on_letter_by_letter_is_one_cue() -> None:
    events = [
        {"tStartMs": 1000, "dDurationMs": 100, "segs": [{"utf8": "♪ AM"}]},
        {"tStartMs": 1100, "dDurationMs": 100, "segs": [{"utf8": "♪ AMBER"}]},
        {"tStartMs": 1200, "dDurationMs": 2000, "segs": [{"utf8": "♪ AMBER BASKET ♪"}]},
        {"tStartMs": 5000, "dDurationMs": 2000, "segs": [{"utf8": "♪ CANDLE ♪"}]},
        {"tStartMs": 9000, "dDurationMs": 2000, "segs": [{"utf8": "   "}]},
    ]
    cues = videolyrics.parse_captions({"events": events})
    assert [(cue.start_s, cue.text) for cue in cues] == [
        (1.0, "♪ AMBER BASKET ♪"), (5.0, "♪ CANDLE ♪"),
    ]  # fmt: skip


def test_words_are_compared_without_their_dress() -> None:
    assert videolyrics.words_of("Singin’ in the RAIN, (oh-oh) — it's “fine”!") == [
        "singing", "in", "the", "rain", "oh", "oh", "its", "fine",
    ]  # fmt: skip
    assert videolyrics.words_of("♪ ♪") == []


def test_the_captions_themselves_can_be_the_lyrics() -> None:
    lines = made_up_lines(12)
    cues = videolyrics.parse_captions(captions_for(lines, lambda n: 3.0))
    sung = videolyrics.sung_lines(cues)
    assert sung is not None and len(sung) == 12
    assert sung[0] == (8.0, lines[0][1].upper() + ",")  # the note marks are gone
    assert videolyrics.sung_lines(cues[:6]) is None  # too few to be a song
    spoken = [Cue(float(n), f"line {n} of talking") for n in range(20)]
    assert videolyrics.sung_lines(spoken) is None  # nothing marked as sung


# ---- the whole thing, with YouTube made up ----------------------------------------------------


class FakeYouTube:
    """A song and its video: what `youtube.sources`, `fetch_audio` and `fetch_captions`
    would say, and how often they were asked."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.sounds: dict[str, list[int]] = {}
        self.captions: dict[str, list[tuple[str, Any]]] = {}
        self.asked: list[str] = []
        self.found = lyrics.Found("not_found")
        self.at_video_length = lyrics.Found("not_found")
        self.at_video_length_song = lyrics.Found("not_found")
        monkeypatch.setattr(youtube, "sources", self.sources)
        monkeypatch.setattr(youtube, "fetch_audio", self.fetch_audio)
        monkeypatch.setattr(youtube, "fetch_captions", self.fetch_captions)
        monkeypatch.setattr(fingerprint, "fingerprint_bytes", self.fingerprint_bytes)
        monkeypatch.setattr(lyrics, "find", self.find)

    def sources(self, video_id: str) -> youtube.VideoSources:
        self.asked.append(f"sources {video_id}")
        audio = youtube.Stream(f"https://example.invalid/{video_id}", {}, None)
        tracks = tuple(
            youtube.CaptionTrack(kind, "en", f"https://example.invalid/{video_id}/{n}")
            for n, (kind, _) in enumerate(self.captions.get(video_id, []))
        )
        return youtube.VideoSources(video_id, audio if video_id in self.sounds else None, tracks)

    def fetch_audio(self, stream: youtube.Stream) -> bytes:
        self.asked.append("audio " + stream.url.rsplit("/", 1)[1])
        return stream.url.rsplit("/", 1)[1].encode()

    def fingerprint_bytes(self, data: bytes, name: str = "") -> fingerprint.RawFP:
        return fingerprint.RawFP(0.0, tuple(self.sounds[data.decode()]))

    def fetch_captions(self, track: youtube.CaptionTrack) -> Any:
        video_id, number = track.url.rsplit("/", 2)[1:]
        self.asked.append(f"captions {video_id} {number}")
        return self.captions[video_id][int(number)][1]

    def find(self, query: lyrics.Query, *, cache: Any = None) -> lyrics.Found:
        if query.video_id:
            return self.found  # the song's lyrics, YouTube Music's included
        if query.duration_s == 260.0:
            return self.at_video_length  # LRCLIB's record of the video's length
        return self.at_video_length_song  # LRCLIB's record for the song (YouTube not asked)


VIDEO, SONG_ID = "videoVVVVVV", "songSSSSSSS"
LINES = made_up_lines(30)
LRC = videolyrics.to_lrc(LINES)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeYouTube:
    made = FakeYouTube(monkeypatch)
    made.sounds[SONG_ID] = SONG
    made.sounds[VIDEO] = OTHER[:250] + as_encoded_again(SONG)  # the song, 31 s in
    made.found = lyrics.Found("synced", synced=LRC, source="LRCLIB")
    return made


def timed(lib: Library, **kw: Any) -> videolyrics.Timed:
    """What the Karaoke button asks for (`full`), unless told otherwise."""
    kw.setdefault("song_video_id", SONG_ID)
    kw.setdefault("full", True)
    with open_index(lib.paths, write=True) as index:
        return videolyrics.for_video(lib, index, title="Song", artist="Band", video_id=VIDEO, **kw)


def sung(found: videolyrics.Timed) -> list[tuple[float, str]]:
    """The lines with words (a wordless line only clears the one before it)."""
    assert found.synced is not None
    return [(at, text) for at, text in videolyrics.lines_of(found.synced) if text]


def shift_of(found: videolyrics.Timed) -> list[float]:
    return [round(there - at, 1) for (there, _), (at, _) in zip(sung(found), LINES, strict=True)]


def test_lyrics_are_timed_by_the_sound(lib: Library, fake: FakeYouTube) -> None:
    found = timed(lib)
    assert (found.how, found.source, found.note) == ("audio", "LRCLIB", None)
    assert shift_of(found) == [pytest.approx(250 * ITEM_S, abs=0.11)] * 30
    assert lyrics.check_lrc(found.synced) is not None
    assert fake.asked == [f"sources {VIDEO}", f"sources {SONG_ID}", f"audio {SONG_ID}",
                          f"audio {VIDEO}"]  # fmt: skip
    # Asked again: nothing is fetched a second time.
    fake.asked.clear()
    assert timed(lib) == found and fake.asked == []


def test_youtube_is_asked_nothing_until_its_asked_for(lib: Library, fake: FakeYouTube) -> None:
    # A video that's simply playing: only what's known already is given.
    assert timed(lib, full=False, video_duration_s=260.0) == videolyrics.Timed(None, settled=False)
    assert fake.asked == []
    # A record on LRCLIB really timed to the video is known without YouTube…
    moved = videolyrics.to_lrc([(at + 31.0, text) for at, text in LINES])
    fake.at_video_length = lyrics.Found("synced", synced=moved, source="LRCLIB")
    fake.found, own = lyrics.Found("not_found"), fake.found
    assert timed(lib, full=False, video_duration_s=260.0).synced is None  # nothing to check it by
    fake.at_video_length_song = own  # LRCLIB's lyrics for the song itself
    found = timed(lib, full=False, video_duration_s=260.0)
    assert (found.how, found.synced) == ("lrclib", moved) and fake.asked == []
    # … and the album's lyrics filed under the video's length are still refused.
    fake.at_video_length = lyrics.Found("synced", synced=LRC, source="LRCLIB")
    assert timed(lib, full=False, video_duration_s=260.0).synced is None and fake.asked == []

    # Asked for (Karaoke): it's worked out, and from then on it's known.
    fake.found = own
    assert timed(lib).how == "audio" and fake.asked
    fake.asked.clear()
    assert timed(lib, full=False).how == "audio" and fake.asked == []


def test_captions_that_agree_leave_the_sounds_answer(lib: Library, fake: FakeYouTube) -> None:
    # The label's captions come up about half a second before each line is sung.
    fake.captions[VIDEO] = [("manual", captions_for(LINES, lambda n: 250 * ITEM_S - 0.5))]
    found = timed(lib)
    assert found.how == "audio" and found.note is None
    assert f"captions {VIDEO} 0" in fake.asked


def test_a_badly_timed_stretch_of_the_lyrics_is_placed_by_the_captions(
    lib: Library, fake: FakeYouTube
) -> None:
    # The song's own lyrics have their last ten lines crammed together, which the sound
    # can't know. The label's captions, made for the video, say where they really are.
    crammed = [
        (at if n < 20 else 85.0 + 0.3 * (n - 20), text) for n, (at, text) in enumerate(LINES)
    ]
    fake.found = lyrics.Found("synced", synced=videolyrics.to_lrc(crammed), source="LRCLIB")
    shift = 250 * ITEM_S
    fake.captions[VIDEO] = [("manual", captions_for(LINES, lambda n: shift - 0.5))]
    found = timed(lib)
    assert found.how == "audio"
    assert found.note is not None and "badly timed" in found.note
    there = [at for at, _ in sung(found)]
    # Every line is where it's really sung: the good ones by the sound, the crammed
    # ones by the captions (brought to the sound's clock: half a second later).
    assert there == [pytest.approx(at + shift, abs=0.6) for at, _ in LINES]
    assert there[:20] == [pytest.approx(at + shift, abs=0.11) for at, _ in LINES[:20]]
    # Speech recognition's captions aren't trusted with that: the sound's answer stands.
    fake.captions[VIDEO] = [("automatic", captions_for(LINES, lambda n: shift - 0.5))]
    with open_index(lib.paths, write=True) as index:
        index.put_search(f"video lyrics 2 {VIDEO} {SONG_ID}", {})  # forget the answer
    assert timed(lib).note is None


def test_one_or_two_lines_the_captions_put_elsewhere_stay_with_the_sound(
    lib: Library, fake: FakeYouTube
) -> None:
    shift = 250 * ITEM_S
    fake.captions[VIDEO] = [
        ("manual", captions_for(LINES, lambda n: shift - 0.5 + (6.0 if n in (7, 8, 19) else 0)))
    ]
    found = timed(lib)
    assert (found.how, found.note) == ("audio", None)
    assert shift_of(found) == [pytest.approx(shift, abs=0.11)] * 30


def test_captions_win_when_they_disagree_with_the_sound_altogether(
    lib: Library, fake: FakeYouTube
) -> None:
    # The song's lyrics are ten seconds early throughout (timed to another cut).
    early = videolyrics.to_lrc([(at - 4.0 if at > 4 else 0.0, text) for at, text in LINES])
    fake.found = lyrics.Found("synced", synced=early, source="LRCLIB")
    fake.captions[VIDEO] = [("manual", captions_for(LINES, lambda n: 30.5))]
    found = timed(lib)
    assert found.how == "captions" and found.note is not None
    there = [at for at, _ in sung(found)]
    assert there == [pytest.approx(at + 30.5) for at, _ in LINES]


def test_captions_time_a_video_whose_sound_is_another_take(lib: Library, fake: FakeYouTube) -> None:
    fake.sounds[VIDEO] = OTHER  # re-recorded for the video: nothing lines up
    fake.captions[VIDEO] = [
        ("manual", captions_for(made_up_lines(30, seed=8), lambda n: 0.0)),  # not these words
        ("automatic", captions_for(LINES, lambda n: 14.0)),
    ]
    found = timed(lib)
    assert (found.how, found.note) == ("captions", None)
    assert shift_of(found) == [14.0] * 30
    assert [a for a in fake.asked if a.startswith("captions")] == [
        f"captions {VIDEO} 0", f"captions {VIDEO} 1",
    ]  # fmt: skip


def test_a_song_with_no_timed_lyrics_gets_the_labels_captions(
    lib: Library, fake: FakeYouTube
) -> None:
    fake.found = lyrics.Found("plain", plain="Words only")
    fake.captions[VIDEO] = [("manual", captions_for(LINES, lambda n: 7.0))]
    found = timed(lib)
    assert (found.how, found.source) == ("caption_text", "YouTube captions")
    assert len(sung(found)) == 30
    assert not [a for a in fake.asked if a.startswith("audio")]  # nothing to line up
    # Speech recognition's guess isn't shown as the lyrics.
    fake.captions[VIDEO] = [("automatic", captions_for(LINES, lambda n: 7.0))]
    with open_index(lib.paths, write=True) as index:
        again = videolyrics._work_out(
            index, title="Song", artist="Band", video_id=VIDEO, video_duration_s=None,
            song_file=None, song_video_id=SONG_ID, song_duration_s=None, video_file=None,
        )  # fmt: skip
    assert again.synced is None


def test_a_record_of_the_videos_length_must_differ_from_the_songs(
    lib: Library, fake: FakeYouTube
) -> None:
    fake.sounds[VIDEO] = OTHER  # the sound can't be lined up, and there are no captions
    # The album's lyrics again, filed under the video's length: not trusted.
    fake.at_video_length = lyrics.Found("synced", synced=LRC, source="LRCLIB")
    missed = timed(lib, video_duration_s=260.0)
    assert missed.synced is None and missed.note is not None
    # A miss is remembered for the day, so the same video isn't worked out over and over.
    fake.asked.clear()
    assert timed(lib, video_duration_s=260.0) == missed and fake.asked == []


def test_a_record_really_timed_to_the_video_is_used(lib: Library, fake: FakeYouTube) -> None:
    fake.sounds[VIDEO] = OTHER
    moved = videolyrics.to_lrc([(at + 31.0, text) for at, text in LINES])
    fake.at_video_length = lyrics.Found("synced", synced=moved, source="LRCLIB")
    found = timed(lib, video_duration_s=260.0)
    assert (found.how, found.synced) == ("lrclib", moved)
    assert timed(lib).synced == moved  # kept, whatever length is given next time


def test_a_library_songs_own_lyrics_and_sound_are_what_is_lined_up(
    lib: Library, fake: FakeYouTube, samples: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    song = lib.paths.music / "Band" / "Album" / "01 Song.m4a"
    song.parent.mkdir(parents=True)
    shutil.copyfile(samples["m4a"], song)
    song.with_suffix(".lrc").write_text(LRC, encoding="utf-8")
    read: list[Path] = []

    def from_file(path: Path, length_s: int = 0, *, index: Any = None) -> fingerprint.RawFP:
        read.append(Path(path))
        return fingerprint.RawFP(0.0, tuple(SONG))

    monkeypatch.setattr(fingerprint, "fingerprint", from_file)
    fake.found = lyrics.Found("not_found")  # nothing is looked up: the song has its .lrc
    found = timed(lib, song_path="Music/Band/Album/01 Song.m4a", song_video_id=None)
    assert (found.how, found.source) == ("audio", "your library")
    assert read == [song] and fake.asked == [f"sources {VIDEO}", f"audio {VIDEO}"]
    # New lyrics for the song (Edit Details) are worked out afresh.
    song.with_suffix(".lrc").write_text(LRC + "[05:00.00]One more\n", encoding="utf-8")
    fake.asked.clear()
    assert timed(lib, song_path="Music/Band/Album/01 Song.m4a", song_video_id=None).how == "audio"
    assert fake.asked == [f"sources {VIDEO}"]  # the video's fingerprint was kept


def test_youtube_slowing_us_down_stops_the_request(
    lib: Library, fake: FakeYouTube, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(stream: youtube.Stream) -> bytes:
        raise youtube.paused_error(datetime.now(UTC))

    monkeypatch.setattr(youtube, "fetch_audio", refused)
    with pytest.raises(YouTubePausedError):
        timed(lib)


def test_a_refused_fetch_is_tried_again_and_a_passing_failure_isnt_remembered(
    lib: Library, fake: FakeYouTube, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetch = fake.fetch_audio
    refusals = [DownloadError("YouTube didn't hand over that audio (HTTP 403).")]
    fresh: list[str] = []

    def sometimes(stream: youtube.Stream) -> bytes:
        if refusals:
            raise refusals.pop()
        return fetch(stream)

    def stream(video_id: str) -> youtube.Stream:
        fresh.append(video_id)
        return youtube.Stream(f"https://example.invalid/{video_id}", {}, None)

    monkeypatch.setattr(youtube, "fetch_audio", sometimes)
    monkeypatch.setattr(youtube, "stream", stream)
    assert timed(lib).how == "audio" and fresh == [SONG_ID]  # asked again, afresh: it worked

    # Refused twice running: nothing can be timed now, and that isn't kept for the day.
    with open_index(lib.paths, write=True) as index:
        for key in (f"video lyrics 2 {VIDEO} {SONG_ID}", f"fingerprint {SONG_ID}"):
            index.put_search(key, {})
    refusals += [DownloadError("refused"), DownloadError("refused")]
    missed = timed(lib)
    assert missed.synced is None and not missed.settled
    assert missed.note == "YouTube didn't hand over the video's sound."
    assert timed(lib).how == "audio"  # the next time it's asked, it's worked out


def test_a_video_youtube_wont_show_times_nothing(
    lib: Library, fake: FakeYouTube, monkeypatch: pytest.MonkeyPatch
) -> None:
    def gone(video_id: str) -> youtube.VideoSources:
        raise DownloadError("That video isn't available.")

    monkeypatch.setattr(youtube, "sources", gone)
    found = timed(lib)
    assert found.synced is None and found.note == "YouTube didn't answer for that video."


# ---- over JSON-RPC --------------------------------------------------------------------------


def test_rpc_lyrics_for_video(opened: rpc.Server, fake: FakeYouTube) -> None:  # noqa: F811
    asked = dict(title="Song", artist="Band", video_id=VIDEO, song_video_id=SONG_ID,
                 video_duration_s=258, song_duration_s=224.5)  # fmt: skip
    # A video that's playing: nothing is asked of YouTube, and nothing is known yet.
    found = result(opened, "lyrics.for_video", **asked)
    assert set(found) == {"synced", "plain", "how", "source", "note"}
    assert found["synced"] is None and fake.asked == []
    # The Karaoke button: the whole thing.
    found = result(opened, "lyrics.for_video", **asked, full=True)
    assert found["how"] == "audio" and found["synced"].startswith("[00:")
    assert code(opened, "lyrics.for_video", **asked, full="yes") == rpc.INVALID_PARAMS
    assert "lyrics.for_video" in rpc.SLOW_METHODS
    assert code(opened, "lyrics.for_video", title="Song") == rpc.INVALID_PARAMS
    assert code(opened, "lyrics.for_video", title="Song", video_id="not an id") == rpc.USER_ERROR
    assert code(opened, "lyrics.for_video", title="Song", video_id=VIDEO,
                song_path="../outside.m4a") == rpc.OUTSIDE  # fmt: skip
