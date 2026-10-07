"""Videos of any kind, and the channels the owner follows. YouTube's answers, recorded
on 2026-10-07 and trimmed, are replayed: the network is never reached."""

from __future__ import annotations

from pathlib import Path

import pytest

from musicorg import library, listening, youtube
from musicorg.errors import ReplayMissError, UserError

NASA = "UCLA_DiR1FfKNvjuUpBHmylQ"


def test_a_search_for_videos_of_any_kind() -> None:
    found = youtube.search_videos("  nasa   artemis launch ", 8)  # spaces don't matter
    assert 1 <= len(found) <= 8
    first = found[0]
    assert len(first.video_id) == 11 and first.title
    assert first.channel == "NASA" and first.channel_id == NASA
    assert isinstance(first.duration_s, int) and isinstance(first.views, int)
    assert first.published is None  # a search gives no date
    assert first.thumbnail is not None and first.thumbnail.startswith("https://")
    assert set(first.to_dict()) == {
        "video_id", "title", "channel", "channel_id", "duration_s", "views", "published",
        "thumbnail",
    }  # fmt: skip


def test_a_search_that_wasnt_recorded_never_goes_online() -> None:
    with pytest.raises(ReplayMissError):
        youtube.search_videos("something nobody recorded", 8)
    with pytest.raises(youtube.YouTubeError):
        youtube.search_videos("   ")


def test_a_channel_and_its_newest_videos() -> None:
    channel = youtube.channel_videos(NASA, 6)
    assert channel.name == "NASA" and channel.channel_id == NASA
    assert isinstance(channel.followers, int) and channel.followers > 1_000_000
    assert channel.thumbnail is not None  # its own square picture, not a banner
    assert 1 <= len(channel.videos) <= 6
    days = [video.published for video in channel.videos]
    assert all(day and len(day) == 10 for day in days)
    assert days == sorted(days, reverse=True)  # newest first, as YouTube lists them
    # A channel's list doesn't say whose each video is: it's the channel's.
    assert {(v.channel, v.channel_id) for v in channel.videos} == {("NASA", NASA)}
    with pytest.raises(youtube.YouTubeError):
        youtube.channel_videos("not-a-channel")


def test_only_videos_are_kept_from_a_listing() -> None:
    read = youtube._video_result
    assert read({"id": NASA, "title": "A channel in the results"}) is None
    assert read({"id": "abcdefghijk", "title": ""}) is None
    assert read("text") is None
    plain = read({"id": "abcdefghijk", "title": "T", "duration": 61.6, "view_count": True})
    assert plain is not None and plain.duration_s == 62 and plain.views is None
    assert plain.thumbnail is None and plain.channel is None


def test_following_a_channel_is_kept_with_the_library(tmp_path: Path) -> None:
    library.init(tmp_path / "lib")
    lib = library.open(tmp_path / "lib", write=True)
    assert listening.followed(lib) == []
    listening.follow(lib, NASA, True, name="NASA", thumbnail="https://p.example/n.jpg")
    other = "UC" + "b" * 22
    assert [c["name"] for c in listening.follow(lib, other, True, name="an early bird")] == [
        "an early bird", "NASA"]  # fmt: skip
    # Followed again with nothing new said: its name and picture are kept.
    again = listening.follow(lib, NASA, True)
    assert again[1] == {"channel_id": NASA, "name": "NASA", "thumbnail": "https://p.example/n.jpg"}
    # Something else kept with the library doesn't lose them.
    listening.set_favourite(lib, "some-track", True)
    assert len(listening.followed(lib)) == 2
    assert [c["channel_id"] for c in listening.follow(lib, other, False)] == [NASA]
    assert listening.follow(lib, other, False) == listening.followed(lib)  # twice is fine
    with pytest.raises(UserError):
        listening.follow(lib, "nasa", True)
