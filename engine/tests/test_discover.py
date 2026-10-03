"""discover (v0.4): picks the owner doesn't have, found from the songs they do have.

YouTube Music's answers are recordings (fixtures/ytm/radio, artists, artist-radio, genre,
playlist), so nothing here reaches the network.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from test_rpc import Capture, code, opened, out, result, root, server  # noqa: F401  (fixtures)

from musicorg import discover, listening, pipeline, rpc, state, youtube
from musicorg.discover import Seed
from musicorg.errors import NotFoundError, UserError
from musicorg.index import open_index
from musicorg.library import Library
from musicorg.youtube import OFFICIAL_AUDIO, Candidate

# Four songs whose radios are recorded.
NUMB = ("Numb", "Linkin Park", "5qZQEq_C3vc")
IN_THE_END = ("In the End", "Linkin Park", "BLZWkjBXfN8")
BRING_ME = ("Bring Me To Life", "Evanescence", "-eGM0IJc70Y")
TEEN_SPIRIT = ("Smells Like Teen Spirit", "Nirvana", "ljUtuoFt-8c")
FOUR = (NUMB, IN_THE_END, BRING_ME, TEEN_SPIRIT)


def own(lib: Library, *songs: tuple[str, str, str | None], genre: str | None = None) -> list[str]:
    """Put songs in the library's index (and an empty file for each). Returns their ids."""
    with open_index(lib.paths, write=True) as index:
        start = len(index.library_tracks())
        rows = []
        for n, (title, artist, video_id) in enumerate(songs, start):
            rel = f"Music/{artist}/{title}.m4a"
            path = lib.root.joinpath(*rel.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
            rows.append({
                "rel_path": rel, "musicorg_id": f"t_{n}", "size": 0, "mtime_ns": 0,
                "title": title, "artist": artist, "album": None, "duration_s": 200.0,
                "source": "youtube_music" if video_id else "rip_copy", "source_id": video_id,
                "only_copy": 0, "origin_path": None,
                "details_json": json.dumps({"genre": genre}), "match": None,
            })  # fmt: skip
        index.put_library_tracks(rows)
    return [row["musicorg_id"] for row in rows]


def suggest(lib: Library, *seeds: Seed, count: int = 10, shuffle: str = "t") -> dict[str, Any]:
    with open_index(lib.paths, write=True) as index:
        return discover.suggest(lib, index, list(seeds), count, shuffle=shuffle)


def ids(found: dict[str, Any]) -> list[str]:
    return [pick["video_id"] for pick in found["picks"]]


# ---- from the owner's songs ----------------------------------------------------------------


def test_picks_from_the_whole_library(lib: Library) -> None:
    own(lib, *FOUR)
    found = suggest(lib, Seed("library"))
    picks = found["picks"]
    assert len(picks) == 10 and found["wanted"] == 10 and found["note"] is None
    assert found["radios"] == 4
    assert found["seeds"] == [{"kind": "library", "label": "your library", "radios": 4}]
    assert not {song[2] for song in FOUR} & set(ids(found))  # nothing the owner has
    assert all(pick["is_official_audio"] for pick in picks)
    assert len(set(ids(found))) == 10
    # A song on several of the radios comes first, and says so.
    assert [pick["hits"] for pick in picks] == sorted((p["hits"] for p in picks), reverse=True)
    assert picks[0]["hits"] >= 2
    assert picks[0]["why"] == f"On the radio for {picks[0]['hits']} of your songs"
    # Every pick has what a card shows.
    for pick in picks:
        assert pick["title"] and pick["artists"] and pick["why"] and pick["duration_s"]
    assert suggest(lib, Seed("library")) == found  # the same again: the answers are kept


def test_show_more_never_picks_what_is_already_shown(lib: Library) -> None:
    own(lib, *FOUR)
    first = suggest(lib, Seed("library"), count=10)
    with open_index(lib.paths, write=True) as index:
        more = discover.suggest(lib, index, [Seed("library")], 10, shuffle="t", exclude=ids(first))
    assert more["picks"] and not set(ids(more)) & set(ids(first))
    assert len(set(ids(more))) == len(ids(more))


def test_what_is_never_suggested(lib: Library) -> None:
    own(lib, *FOUR)
    with open_index(lib.paths, write=True) as index:
        before = discover.suggest(lib, index, [Seed("library")], 30)["picks"]
        mine, turned_down, waiting = before[0], before[1], before[2]
        # 1. A song the owner has under its name alone (a rip not identified yet).
        index.put_library_tracks([{
            "rel_path": "Music/Rips/one.mp3", "musicorg_id": "t_rip", "size": 0, "mtime_ns": 0,
            "title": mine["title"], "artist": mine["artists"][0], "album": None,
            "duration_s": 200.0, "source": "rip_copy", "source_id": None, "only_copy": 0,
            "origin_path": None, "details_json": None, "match": "unconfirmed",
        }])  # fmt: skip
        rip = lib.root / "Music" / "Rips" / "one.mp3"
        rip.parent.mkdir(parents=True)
        rip.write_bytes(b"")
        # 2. A candidate the owner rejected in review.
        with state.edit(lib.paths.state_file) as st:
            st.data["rejected"] = {"i_0123456789abcdef": [turned_down["video_id"]]}
        # 3. A song already waiting in the download queue.
        plan = pipeline.plan_download(lib, index, [waiting["video_id"]], known=[waiting])
        pipeline.apply(lib, index, plan.plan_id)
        after = discover.suggest(lib, index, [Seed("library")], 30)
    left_out = {mine["video_id"], turned_down["video_id"], waiting["video_id"]}
    assert not left_out & set(ids(after))
    assert left_out <= {pick["video_id"] for pick in before}


def test_a_song_whose_file_has_gone_is_not_the_owners(lib: Library) -> None:
    own(lib, *FOUR)
    before = suggest(lib, Seed("library"), count=40)
    assert "Numb" not in [pick["title"] for pick in before["picks"]]
    (lib.root / "Music" / "Linkin Park" / "Numb.m4a").unlink()
    after = suggest(lib, Seed("library"), count=40)
    assert after["radios"] == 3  # nothing starts from it any more
    assert "Numb" in [pick["title"] for pick in after["picks"]]  # it's on the other radios


def test_a_starting_song_known_only_by_name_is_looked_up(lib: Library) -> None:
    own(lib, ("Numb", "Linkin Park", None))
    found = suggest(lib, Seed("library"), count=5)
    assert found["radios"] == 1 and len(found["picks"]) == 5
    assert "Numb" not in [pick["title"] for pick in found["picks"]]
    assert found["picks"][0]["why"].startswith(("Like “Numb” by Linkin Park", "You have"))


def test_most_played(lib: Library) -> None:
    numb, in_the_end, bring_me, _ = own(lib, *FOUR)
    with pytest.raises(UserError, match="Nothing has been played yet"):
        suggest(lib, Seed("most_played"))
    for track_id, times in ((bring_me, 3), (numb, 1)):
        for _ in range(times):
            listening.played(lib, track_id)
    found = suggest(lib, Seed("most_played"), count=5)
    assert found["seeds"] == [{"kind": "most_played", "label": "your most played", "radios": 2}]
    whys = {pick["why"] for pick in found["picks"]}
    assert "On the radio for 2 of your most played songs" in whys
    assert in_the_end  # never played, so nothing starts from it
    assert not [w for w in whys if "In the End" in w]


def test_a_playlist(lib: Library) -> None:
    track_ids = own(lib, *FOUR)
    (made,) = listening.create_playlist(lib, "Road Trip")
    with pytest.raises(UserError, match="“Road Trip” has no songs yet"):
        suggest(lib, Seed("playlist", made["id"]))
    listening.set_playlist_tracks(lib, made["id"], [track_ids[0], track_ids[2], "t_gone"])
    found = suggest(lib, Seed("playlist", made["id"]), count=5)
    assert found["seeds"] == [{"kind": "playlist", "label": "Road Trip", "radios": 2}]
    assert "On the radio for 2 songs in Road Trip" in {pick["why"] for pick in found["picks"]}
    with pytest.raises(NotFoundError):
        suggest(lib, Seed("playlist", "pl_nothing"))


# ---- several starting points together -------------------------------------------------------


def test_starting_points_together_share_the_radios(
    lib: Library, radios: dict[str, list[Candidate]]
) -> None:
    track_ids = own(lib, ("A", "One", "startAAAAAA"), ("B", "Two", "startBBBBBB"))
    own(lib, ("C", "Three", "startCCCCCC"), ("D", "Four", "startDDDDDD"), genre="Rock")
    own(lib, ("E", "Five", "startEEEEEE"), ("F", "Six", "startFFFFFF"), genre="Rock")
    (made,) = listening.create_playlist(lib, "Road Trip")
    listening.set_playlist_tracks(lib, made["id"], track_ids)
    both, mine, theirs = song(1, "On Both"), song(2, "Playlist Only", "X"), song(3, "Rock", "Y")
    radios["startAAAAAA"] = radios["startBBBBBB"] = [both, mine]
    for start in ("startCCCCCC", "startDDDDDD", "startEEEEEE", "startFFFFFF"):
        radios[start] = [theirs, both]
    found = suggest(lib, Seed("playlist", made["id"]), Seed("genre", "rock"), count=10)
    assert [(s["kind"], s["label"]) for s in found["seeds"]] == [
        ("playlist", "Road Trip"), ("genre", "rock"),
    ]  # fmt: skip
    assert sum(s["radios"] for s in found["seeds"]) == 6
    whys = {pick["title"]: pick["why"] for pick in found["picks"]}
    # A song on the radios of both comes first, and says so.
    assert found["picks"][0]["title"] == "On Both"
    assert whys["On Both"] == "On the radio for both Road Trip and rock songs"
    assert whys["Playlist Only"] == "On the radio for 2 songs in Road Trip"
    assert whys["Rock"] == "On the radio for 4 rock songs"
    # The same starting point twice is one.
    again = suggest(lib, Seed("genre", "rock"), Seed("genre", "rock"), count=10)
    assert [s["kind"] for s in again["seeds"]] == ["genre"]


# ---- an artist -----------------------------------------------------------------------------


def test_an_artist_and_bands_like_them(lib: Library) -> None:
    found = suggest(lib, Seed("artist", "linkin park"), count=12)
    assert found["seeds"] == [{"kind": "artist", "label": "Linkin Park", "radios": 4}]
    picks = found["picks"]
    assert len(picks) == 12
    assert {pick["why"] for pick in picks} == {"By Linkin Park", "Similar to Linkin Park"}
    own_songs = [pick for pick in picks if "Linkin Park" in pick["artists"]]
    assert all(pick["why"] == "By Linkin Park" for pick in own_songs)
    assert 2 <= len(own_songs) < 12  # their songs, and other bands'


def test_the_owners_top_artist(lib: Library) -> None:
    numb, _, bring_me, _ = own(lib, *FOUR)
    # Nothing played yet: the artist with the most songs.
    assert suggest(lib, Seed("top_artist"), count=5)["seeds"][0]["label"] == "Linkin Park"
    assert discover._top_artist(
        [{"artist": "Evanescence", "musicorg_id": bring_me}, {"artist": "Linkin Park",
         "musicorg_id": numb}, {"artist": "linkin park", "musicorg_id": "t_x"}],
        {bring_me: {"count": 4}, numb: {"count": 1}},
    ) == "Evanescence"  # fmt: skip


def test_an_artist_youtube_music_does_not_know(lib: Library) -> None:
    with pytest.raises(UserError, match="doesn't know an artist called “Zzyzx Qwfp Band”"):
        suggest(lib, Seed("artist", "Zzyzx Qwfp Band"))
    # With another seed beside it, the picks still come and the note says what's missing.
    own(lib, *FOUR)
    found = suggest(lib, Seed("artist", "Zzyzx Qwfp Band"), Seed("library"), count=5)
    assert len(found["picks"]) == 5
    assert "Zzyzx Qwfp Band" in found["note"]


# ---- a genre -------------------------------------------------------------------------------


def test_a_genre_starts_from_the_owners_songs_of_it(lib: Library) -> None:
    own(lib, NUMB, IN_THE_END, genre="Alternative Rock")
    own(lib, BRING_ME, genre="Rock/Pop")
    own(lib, TEEN_SPIRIT, genre="rock")
    own(lib, ("Something", "Somebody", "abcdefghijk"), genre="Jazz")
    # No playlist is asked for (there's no recording of one for "rock": it would fail).
    found = suggest(lib, Seed("genre", "Rock"), count=8)
    assert found["seeds"] == [{"kind": "genre", "label": "Rock", "radios": 4}]
    assert found["picks"][0]["why"].startswith("On the radio for ")
    assert found["picks"][0]["why"].endswith(" Rock songs")


def test_a_genre_the_owner_has_none_of_comes_from_youtube_music(lib: Library) -> None:
    found = suggest(lib, Seed("genre", "jazz"), count=8)
    assert found["seeds"] == [{"kind": "genre", "label": "jazz", "radios": 4}]
    assert len(found["picks"]) == 8
    assert all(pick["is_official_audio"] for pick in found["picks"])
    whys = [pick["why"] for pick in found["picks"]]
    assert all(
        why.startswith(("On the radio for ", "Like “", "On YouTube Music's “Cozy Jazz”"))
        for why in whys
    )


# ---- the genre a pick is found under ----------------------------------------------------------


def test_a_pick_carries_the_genre_it_was_found_under(lib: Library) -> None:
    own(lib, NUMB, IN_THE_END, genre="Alternative Rock")
    own(lib, BRING_ME, genre="Rock/Pop")
    own(lib, TEEN_SPIRIT, genre="rock")
    # From the owner's songs: the tag of the song whose radio the pick was nearest the top of.
    found = suggest(lib, Seed("library"), count=30)
    genres = {pick["genre"] for pick in found["picks"]}
    assert genres <= {"Alternative Rock", "Rock/Pop", "rock"} and "Alternative Rock" in genres
    # A genre that was asked for: every pick is filed under it, spelled as the owner's
    # own files spell it (the commonest of their tags that mean it).
    asked = suggest(lib, Seed("genre", "rock"), count=10)
    assert {pick["genre"] for pick in asked["picks"]} == {"Alternative Rock"}
    # A genre the owner has none of: as typed, with capitals.
    assert {pick["genre"] for pick in suggest(lib, Seed("genre", "jazz"), count=5)["picks"]} == {
        "Jazz"
    }
    assert discover._spelled("r&b", []) == "R&B" and discover._spelled("EDM", []) == "EDM"
    # An artist says nothing about genre.
    by_artist = suggest(lib, Seed("artist", "linkin park"), count=5)
    assert {pick["genre"] for pick in by_artist["picks"]} == {None}


# ---- whatever the owner typed (the guided mode) ----------------------------------------------


def test_typed_words_are_worked_out_to_be_a_genre_or_an_artist(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A kind of music by name: a genre, without asking whether an artist is called that.
    assert suggest(lib, Seed("typed", "Jazz"), count=5)["seeds"] == [
        {"kind": "genre", "label": "Jazz", "radios": 4}
    ]
    # An artist YouTube Music has by exactly that name.
    found = suggest(lib, Seed("typed", "linkin park"), count=5)
    assert found["seeds"] == [{"kind": "artist", "label": "Linkin Park", "radios": 4}]
    assert {pick["why"] for pick in found["picks"]} <= {"By Linkin Park", "Similar to Linkin Park"}
    # Neither: it says so, and what to try.
    monkeypatch.setattr(youtube, "genre_playlist", lambda name, cache=None: None)
    with pytest.raises(UserError, match="no artist and no playlist of its own called"):
        suggest(lib, Seed("typed", "Zzyzx Qwfp Band"))
    assert Seed.from_dict({"kind": "typed", "name": " hip hop "}) == Seed("typed", "hip hop")
    with pytest.raises(UserError):
        Seed.from_dict({"kind": "typed"})


def test_typed_words_the_owner_has_songs_tagged_with_are_a_genre(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    # "Alternative Rock" isn't on the list of names genres go by, but it's the owner's
    # own tag: their songs of it are where it starts, and nothing is searched for.
    own(lib, *FOUR, genre="Alternative Rock")
    monkeypatch.setattr(youtube, "artist_radio", lambda *a, **kw: pytest.fail("not an artist"))
    found = suggest(lib, Seed("typed", "alternative rock"), count=5)
    assert found["seeds"] == [{"kind": "genre", "label": "alternative rock", "radios": 4}]


@pytest.mark.parametrize(
    ("tag", "wanted", "same"),
    [
        ("Hip-Hop/Rap", "hip hop", True),
        ("Rap", "Hip-Hop", True),
        ("Gangsta Rap", "hip hop", True),
        ("R&B", "rnb", True),
        ("R & B", "R&B", True),
        ("Electronica/Dance", "electronic", True),
        ("Alternative & Punk", "punk", True),
        ("Pop", "hip hop", False),
        ("Poprock", "pop", False),
        ("", "pop", False),
        ("Pop", "", False),
    ],
)
def test_genre_tags(tag: str, wanted: str, same: bool) -> None:
    assert discover._is_genre(tag, wanted) is same


# ---- ranking, with made-up radios ------------------------------------------------------------


def song(n: int, title: str | None = None, artist: str = "Band", **more: Any) -> Candidate:
    kind = more.pop("video_type", OFFICIAL_AUDIO)
    return Candidate(video_id=f"vid{n:08d}", title=title or f"Song {n}", artists=(artist,),
                     duration_s=200, video_type=kind, **more)  # fmt: skip


@pytest.fixture
def radios(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Candidate]]:
    """Made-up radios by starting id, in place of YouTube Music's."""
    made: dict[str, list[Candidate]] = {}
    monkeypatch.setattr(youtube, "radio", lambda video_id, cache=None: made[video_id])
    return made


def test_ranking(lib: Library, radios: dict[str, list[Candidate]]) -> None:
    own(
        lib,
        ("A", "Owned One", "startAAAAAA"),
        ("B", "Owned Two", "startBBBBBB"),
        ("C", "Mine", "startCCCCCC"),
        ("D", "Mine", "startDDDDDD"),
        ("E", "Mine", None),
    )
    twice = song(1, "On Both")
    first, second = song(2, "First Of A", "Band Two"), song(3, "Second Of A", "Band Three")
    liked = song(4, "By An Artist I Have", "Mine")
    live = song(5, "A (Live)", "Owned One")
    radios["startAAAAAA"] = [first, second, twice, live, song(6, "A", "Owned One")]
    radios["startBBBBBB"] = [song(7, "On Both (Remastered 2011)"), liked,
                             song(8, "A Video", video_type=youtube.OFFICIAL_VIDEO)]  # fmt: skip
    radios["startCCCCCC"] = radios["startDDDDDD"] = []
    found = suggest(lib, Seed("library"), count=10)
    assert [pick["title"] for pick in found["picks"]] == [
        "On Both",  # on two radios; its remaster is the same song, once
        "By An Artist I Have",  # the owner has three by them
        "First Of A",
        "Second Of A",
        "A (Live)",  # a different recording from the "A" the owner has
    ]
    assert found["picks"][0]["hits"] == 2
    assert found["picks"][0]["video_id"] == twice.video_id
    assert found["note"] == (
        "Found 5 new songs, not 10: that's all these radios had that you don't have already."
    )
    whys = {pick["title"]: pick["why"] for pick in found["picks"]}
    assert whys["First Of A"] == "Like “A” by Owned One"
    assert whys["By An Artist I Have"] == "Like “B” by Owned Two"  # three isn't "a lot"


def test_one_artist_does_not_fill_the_page() -> None:
    def entry(n: int, artist: str) -> discover._Found:
        return discover._Found(song(n, artist=artist), n)

    ranked = [entry(n, "Prolific") for n in range(6)] + [entry(9, "Other"), entry(10, "The Other")]
    picks = discover._spread(ranked, 4, set())
    assert [p.candidate.artists[0] for p in picks] == ["Prolific", "Prolific", "Other", "The Other"]
    # With room left, the rest of theirs come in after all.
    assert len(discover._spread(ranked, 7, set())) == 7
    # An artist asked for by name isn't held back.
    named = discover._spread(ranked, 4, {"prolific"})
    assert [p.candidate.artists[0] for p in named] == ["Prolific"] * 4


def test_a_radio_that_fails_is_left_out(lib: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    own(lib, ("A", "One", "startAAAAAA"), ("B", "Two", "startBBBBBB"))

    def radio(video_id: str, cache: object = None) -> list[Candidate]:
        if video_id == "startAAAAAA":
            raise KeyError("navigationEndpoint")  # ytmusicapi couldn't read the answer
        return [song(1), song(2)]

    monkeypatch.setattr(youtube, "radio", radio)
    found = suggest(lib, Seed("library"), count=2)
    assert found["radios"] == 1 and len(found["picks"]) == 2

    def refused(video_id: str, cache: object = None) -> list[Candidate]:
        raise youtube.paused_error(datetime.now(UTC))

    monkeypatch.setattr(youtube, "radio", refused)
    with pytest.raises(youtube.YouTubePausedError):  # a slow-down stops the whole request
        suggest(lib, Seed("library"), count=2)


def test_progress_is_reported(lib: Library, radios: dict[str, list[Candidate]]) -> None:
    own(lib, ("A", "One", "startAAAAAA"), ("B", "Two", "startBBBBBB"))
    radios["startAAAAAA"] = radios["startBBBBBB"] = [song(1)]
    steps: list[tuple[int, int]] = []
    with open_index(lib.paths, write=True) as index:
        discover.suggest(
            lib, index, [Seed("library")], 5, progress=lambda a, b: steps.append((a, b))
        )
    assert steps == [(1, 4), (2, 4)]


# ---- what's asked for -----------------------------------------------------------------------


def test_what_can_be_asked_for(lib: Library) -> None:
    with pytest.raises(UserError, match="Choose where to start from"):
        suggest(lib)
    with pytest.raises(UserError, match="no songs yet"):
        suggest(lib, Seed("library"))
    own(lib, *FOUR)
    for count in (0, 501, True, "10"):
        with pytest.raises(UserError, match="1 to 500"):
            suggest(lib, Seed("library"), count=count)  # type: ignore[arg-type]
    with pytest.raises(UserError, match="too many starting points"):
        suggest(lib, *[Seed("artist", f"Band {n}") for n in range(9)])
    assert Seed.from_dict({"kind": "artist", "name": " Linkin Park "}) == Seed(
        "artist", "Linkin Park"
    )
    assert Seed.from_dict({"kind": "playlist", "playlist_id": "pl_1"}) == Seed("playlist", "pl_1")
    assert Seed.from_dict({"kind": "library", "name": "ignored"}) == Seed("library")
    for bad in ({"kind": "mood"}, {"kind": "artist"}, {"kind": "genre", "name": " "},
                {"kind": "playlist", "name": "Road Trip"}, "library", None):  # fmt: skip
        with pytest.raises(UserError):
            Seed.from_dict(bad)


# ---- over JSON-RPC --------------------------------------------------------------------------


def test_rpc_suggest(opened: rpc.Server, out: Capture) -> None:  # noqa: F811
    assert opened.lib is not None
    own(opened.lib, *FOUR)
    found = result(opened, "discover.suggest", seeds=[{"kind": "library"}], count=6,
                   shuffle="t", token="page-1")  # fmt: skip
    assert len(found["picks"]) == 6 and found["radios"] == 4
    assert set(found["picks"][0]) >= {"video_id", "title", "artists", "album", "duration_s",
                                      "thumbnail", "why", "hits"}  # fmt: skip
    assert out.notes("discover.progress") == [
        {"token": "page-1", "done": n, "of": 4} for n in (1, 2, 3, 4)
    ]
    assert "discover.suggest" in rpc.SLOW_METHODS

    assert code(opened, "discover.suggest", count=5) == rpc.INVALID_PARAMS
    assert code(opened, "discover.suggest", seeds=[{"kind": "mood"}]) == rpc.USER_ERROR
    assert code(opened, "discover.suggest", seeds=[{"kind": "library"}], count=0) == rpc.USER_ERROR
    assert code(opened, "discover.suggest", seeds=[{"kind": "playlist", "playlist_id": "pl_x"}]) \
        == rpc.NOT_FOUND  # fmt: skip


def test_rpc_a_plan_for_picks_asks_youtube_nothing(opened: rpc.Server) -> None:  # noqa: F811
    assert opened.lib is not None
    own(opened.lib, *FOUR)
    picks = result(opened, "discover.suggest", seeds=[{"kind": "library"}], count=3)["picks"]
    wanted = [pick["video_id"] for pick in picks]
    # There are no recordings of these three as single tracks: a lookup would fail.
    plan = result(opened, "plan.create", kind="download",
                  options={"video_ids": wanted, "candidates": picks})  # fmt: skip
    assert plan["summary"]["downloads"] == 3 and plan["summary"]["est_minutes"] >= 1
    ops = result(opened, "plan.get", plan_id=plan["plan_id"])["plan"]["operations"]
    assert [op["params"]["video_id"] for op in ops] == wanted
    assert ops[0]["params"]["candidate"]["title"] == picks[0]["title"]
    assert all("genre" not in op["params"] for op in ops)  # these songs have no genre tag
    # A pick's genre goes into the plan (and from there into the song's tag).
    tagged = [{**picks[0], "genre": "  Hip  Hop "}, {**picks[1], "genre": 7},
              {**picks[2], "genre": "x" * 61}]  # fmt: skip
    plan = result(opened, "plan.create", kind="download",
                  options={"video_ids": wanted, "candidates": tagged})  # fmt: skip
    ops = result(opened, "plan.get", plan_id=plan["plan_id"])["plan"]["operations"]
    assert [op["params"].get("genre") for op in ops] == ["Hip Hop", None, None]
    bad = {"video_ids": wanted, "candidates": ["nonsense"]}
    assert code(opened, "plan.create", kind="download", options=bad) == rpc.USER_ERROR


def test_which_of_the_owners_songs_a_track_is(lib: Library) -> None:
    own(lib, NUMB, ("Their Own Rip", "Band", None))
    with open_index(lib.paths, write=False) as index:
        owned = discover.Owned(index.library_tracks())
    by_id = Candidate(NUMB[2], "Numb (Official Video)", ("Somebody Else",))
    by_name = Candidate("anotherIDxx", "Their Own Rip", ("Band",))
    remix = Candidate("remixIDxxxx", "Their Own Rip (Club Remix)", ("Band",))
    assert [owned.track_id(c) for c in (by_id, by_name, remix)] == ["t_0", "t_1", None]
    assert [owned.has(c) for c in (by_id, by_name, remix)] == [True, True, False]
