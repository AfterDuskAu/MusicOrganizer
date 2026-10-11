"""listening (v0.2): favourites, play counts and playlists in state.json."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_rpc import code, out, result, server  # noqa: F401  (out and server are fixtures)

from musicorg import library, listening, rpc, state
from musicorg.errors import NotFoundError, UserError
from musicorg.library import Library


def test_nothing_yet(lib: Library) -> None:
    assert listening.get(lib) == {"favourites": [], "plays": {}, "listened": {}, "playlists": [],
                                  "library": [], "heard": []}  # fmt: skip


def test_favourites_most_recent_first(lib: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter(["2026-10-01T00:00:01Z", "2026-10-01T00:00:02Z"])
    monkeypatch.setattr(listening, "_now", lambda: next(times))
    assert listening.set_favourite(lib, "t_1", True) == ["t_1"]
    assert listening.set_favourite(lib, "t_2", True) == ["t_2", "t_1"]
    assert listening.set_favourite(lib, "t_1", True) == ["t_2", "t_1"]  # already one
    assert listening.set_favourite(lib, "t_2", False) == ["t_1"]
    assert listening.set_favourite(lib, "t_9", False) == ["t_1"]
    assert listening.get(lib)["favourites"] == ["t_1"]


def test_plays_are_counted(lib: Library) -> None:
    assert listening.played(lib, "t_1")["count"] == 1
    again = listening.played(lib, "t_1")
    assert again["count"] == 2 and again["last_played"].endswith("Z")
    assert listening.get(lib)["plays"]["t_1"] == again
    with pytest.raises(UserError):
        listening.played(lib, "")


def test_time_spent_listening_adds_up(lib: Library) -> None:
    assert listening.listened(lib, "t_1", 30.5) == 30.5
    assert listening.listened(lib, "t_1", 12) == 42.5
    assert listening.listened(lib, "t_2", listening.MAX_SECONDS) == 86400.0
    assert listening.get(lib)["listened"] == {"t_1": 42.5, "t_2": 86400.0}
    assert listening.get(lib)["plays"] == {}  # time isn't a play: that's a song heard to its end
    for bad in (0, -1, listening.MAX_SECONDS + 1, float("nan"), float("inf"), True, "5", None):
        with pytest.raises(UserError):
            listening.listened(lib, "t_1", bad)  # type: ignore[arg-type]
    with pytest.raises(UserError):
        listening.listened(lib, "", 5)
    assert listening.get(lib)["listened"]["t_1"] == 42.5


def change(kind: str, mark: str, **about: object) -> listening.DeviceChange:
    return listening.DeviceChange(mark, kind, **about)  # type: ignore[arg-type]


def came(**of_it: object) -> dict[str, object]:
    """What `from_devices` says came of a device's changes: nothing, but for these."""
    nothing = {"playlists": 0, "songs": 0, "seconds": 0.0, "hearts": 0, "plays": 0,
               "removed": 0, "renamed": 0, "ordered": 0, "deleted": 0}  # fmt: skip
    return {**nothing, **of_it}


def test_a_heart_and_a_play_come_back_from_a_device(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(listening, "_now", lambda: "2026-10-11T03:00:00Z")
    listening.set_favourite(lib, "t_1", True)  # hearted here at three o'clock
    listening.set_favourite(lib, "t_2", True)
    listening.played(lib, "t_1")
    sent = [
        change("favourite", "m1", track_id="t_3", on=True),
        change("favourite", "m2", track_id="t_1", on=True),  # it has one already
        # Taken off on the device at four: after it was put on here, so off it comes.
        change("favourite", "m3", track_id="t_2", on=False, at="2026-10-11T04:00:00Z"),
        # Taken off on the device at two, an hour before it was put on here: it stays.
        change("favourite", "m4", track_id="t_1", on=False, at="2026-10-11T02:00:00Z"),
        change("favourite", "m5", track_id="t_9", on=False),  # it had none
        change("played", "m6", track_id="t_1", at="2026-10-11T02:30:00Z"),
        change("played", "m7", track_id="t_4", at="2026-10-11T01:00:00Z"),
        change("played", "m8", track_id="t_4", at="not a time"),
    ]
    assert listening.from_devices(lib, sent) == came(hearts=2, plays=3)
    found = listening.get(lib)
    assert set(found["favourites"]) == {"t_1", "t_3"}
    # A play on the device is one more play. The last time played is the later of what
    # was kept and when the device says; a time that can't be read counts as now.
    assert found["plays"]["t_1"] == {"count": 2, "last_played": "2026-10-11T03:00:00Z"}
    assert found["plays"]["t_4"]["count"] == 2
    assert found["plays"]["t_4"]["last_played"] > "2026-10-11T03:00:00Z"
    assert set(found["plays"]) == {"t_1", "t_4"}
    # The same again changes nothing, and a heart taken off with no time is taken off.
    assert listening.from_devices(lib, sent) == came()
    late = [
        change("favourite", "m9", track_id="t_1", on=False),
        # A time still to come is no later than now: it can't set a play in the future.
        change("played", "m10", track_id="t_5", at="2099-01-01T00:00:00Z"),
    ]
    assert listening.from_devices(lib, late) == came(hearts=1, plays=1)
    after = listening.get(lib)
    assert after["favourites"] == ["t_3"]
    assert after["plays"]["t_5"]["last_played"] < "2027"


def test_a_devices_changes_are_taken_once(lib: Library) -> None:
    (mix,) = listening.create_playlist(lib, "Mix")
    sent = [
        change("playlist_new", "m1", theirs="theirs-1", name="  Driving  home "),
        change("playlist_add", "m2", track_id="t_1", theirs="theirs-1"),
        change("playlist_add", "m3", track_id="t_2", playlist_id=mix["id"]),
        change("listened", "m4", track_id="t_1", seconds=215.5),
    ]
    assert listening.from_devices(lib, sent) == came(playlists=1, songs=2, seconds=215.5)
    found = listening.get(lib)
    assert [(p["name"], p["track_ids"]) for p in found["playlists"]] == [
        ("Mix", ["t_2"]), ("Driving home", ["t_1"]),
    ]  # fmt: skip
    assert found["listened"] == {"t_1": 215.5}
    # The same again (the answer was lost on the way), with one new change among them.
    more = [*sent, change("listened", "m5", track_id="t_1", seconds=4.5)]
    assert listening.from_devices(lib, more) == came(seconds=4.5)
    again = listening.get(lib)
    assert again["playlists"] == found["playlists"] and again["listened"] == {"t_1": 220.0}
    assert listening.from_devices(lib, []) == came()


def test_a_playlist_made_on_a_device_uses_the_one_of_that_name(lib: Library) -> None:
    listening.create_playlist(lib, "Driving")
    lower = listening.create_playlist(lib, "driving")[-1]
    done = listening.from_devices(lib, [
        change("playlist_new", "m1", theirs="a", name="driving"),  # the same spelling first
        change("playlist_add", "m2", track_id="t_1", theirs="a"),
        change("playlist_new", "m3", theirs="b", name="DRIVING"),  # else the first of that name
        change("playlist_add", "m4", track_id="t_2", theirs="b"),
        change("playlist_add", "m5", track_id="t_2", theirs="b"),  # it has the song already
        change("playlist_new", "m6", theirs="a", name="Something Else"),  # made already
        change("playlist_new", "m7", theirs="c", name="   "),  # nothing to call it
        change("playlist_add", "m8", track_id="t_3", theirs="c"),
        change("playlist_add", "m9", track_id="t_3", theirs="never-made"),
        change("playlist_add", "m10", track_id="t_3", playlist_id="pl_gone"),
    ])  # fmt: skip
    assert done == came(songs=2)
    first, second = listening.get(lib)["playlists"]
    assert (first["name"], first["track_ids"]) == ("Driving", ["t_2"])
    assert (second["id"], second["track_ids"]) == (lower["id"], ["t_1"])
    # The playlist is deleted on the computer: a song put in it on a device afterwards
    # has nowhere to go, and the note of whose playlist it was goes too.
    listening.delete_playlist(lib, lower["id"])
    done = listening.from_devices(lib, [change("playlist_add", "m11", track_id="t_9", theirs="a")])
    assert done["songs"] == 0
    kept = lib.load_state().data["listening"]["devices"]
    assert kept["made"] == [{"theirs": "b", "playlist": first["id"]}]
    assert kept["taken"] == [f"m{n}" for n in range(1, 12)]


def songs_of(lib: Library) -> list[tuple[str, list[str]]]:
    return [(p["name"], p["track_ids"]) for p in listening.get(lib)["playlists"]]


def test_a_playlist_is_changed_on_a_device(lib: Library) -> None:
    mix = listening.create_playlist(lib, "Mix")[0]["id"]
    listening.set_playlist_tracks(lib, mix, ["t_1", "t_2", "t_1", "t_3", "t_4"])
    sent = [
        change("playlist_rename", "m1", playlist_id=mix, name="  Road   trip "),
        change("playlist_remove", "m2", playlist_id=mix, track_id="t_3"),
        change("playlist_remove", "m3", playlist_id=mix, track_id="t_1"),  # its last copy goes
        change("playlist_remove", "m4", playlist_id=mix, track_id="t_9"),  # it isn't in it
        change("playlist_rename", "m5", playlist_id=mix, name="Road trip"),  # its name already
        change("playlist_rename", "m6", playlist_id=mix, name="   "),  # nothing to call it
        change("playlist_rename", "m7", playlist_id="pl_gone", name="Nowhere"),
        change("playlist_remove", "m8", playlist_id="pl_gone", track_id="t_1"),
    ]
    assert listening.from_devices(lib, sent) == came(renamed=1, removed=2)
    assert songs_of(lib) == [("Road trip", ["t_1", "t_2", "t_4"])]
    # Sent again, nothing more comes of them: a second copy of a song isn't taken out.
    listening.set_playlist_tracks(lib, mix, ["t_1", "t_2", "t_4", "t_1"])
    assert listening.from_devices(lib, sent) == came()
    assert songs_of(lib) == [("Road trip", ["t_1", "t_2", "t_4", "t_1"])]
    # A name longer than the library allows is cut, as a new playlist's is.
    long = [change("playlist_rename", "m9", playlist_id=mix, name="y" * 5000)]
    assert listening.from_devices(lib, long) == came(renamed=1)
    assert listening.get(lib)["playlists"][0]["name"] == "y" * listening.MAX_NAME


def test_a_new_order_never_puts_a_song_in_or_takes_one_out(lib: Library) -> None:
    mix = listening.create_playlist(lib, "Mix")[0]["id"]
    # t_5 was put in on the computer since the device last synced, and so was the
    # second t_1: the device's order names neither.
    listening.set_playlist_tracks(lib, mix, ["t_1", "t_2", "t_3", "t_5", "t_1"])
    order = change("playlist_order", "m1", playlist_id=mix, tracks=("t_3", "t_9", "t_1", "t_2"))
    assert listening.from_devices(lib, [order]) == came(ordered=1)
    # The device's order first; what it didn't name after, as it was. t_9 isn't put in.
    assert songs_of(lib) == [("Mix", ["t_3", "t_1", "t_2", "t_5", "t_1"])]
    same = [
        change("playlist_order", "m2", playlist_id=mix, tracks=("t_3", "t_1", "t_2", "t_5", "t_1")),
        change("playlist_order", "m3", playlist_id=mix, tracks=()),  # nothing named: as it was
        change("playlist_order", "m4", playlist_id=mix, tracks=("t_3", "t_3", "t_3")),
        change("playlist_order", "m5", playlist_id="pl_gone", tracks=("t_1",)),
    ]
    assert listening.from_devices(lib, same) == came()
    assert songs_of(lib) == [("Mix", ["t_3", "t_1", "t_2", "t_5", "t_1"])]
    # Both copies of a song can be placed.
    both = change("playlist_order", "m6", playlist_id=mix, tracks=("t_1", "t_1", "t_5"))
    assert listening.from_devices(lib, [both]) == came(ordered=1)
    assert songs_of(lib) == [("Mix", ["t_1", "t_1", "t_5", "t_3", "t_2"])]


def test_a_playlist_deleted_on_a_device_stays_if_it_was_changed_here_since(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(listening, "_now", lambda: "2026-10-11T03:00:00Z")
    old, used, plain = (p["id"] for p in [listening.create_playlist(lib, n)[-1] for n in "abc"])
    listening.set_playlist_tracks(lib, old, ["t_1", "t_2"])
    with state.edit(lib.paths.state_file) as st:  # one from before changes were noted
        del st.data["listening"]["playlists"][0]["changed_at"]
        st.data["listening"]["playlists"][0]["created_at"] = None
    listening.add_to_playlist(lib, used, "t_1")  # changed here at three o'clock
    assert [p.get("changed_at") for p in listening.get(lib)["playlists"]] == [
        None, "2026-10-11T03:00:00Z", None,
    ]  # fmt: skip
    early = [  # deleted on the device at two: the computer's word is the newer
        change("playlist_delete", "m1", playlist_id=used, at="2026-10-11T02:00:00Z"),
        change("playlist_delete", "m2", playlist_id=plain, at="2026-10-11T02:00:00Z"),  # made at 3
        change("playlist_delete", "m3", playlist_id="pl_gone", at="2026-10-11T04:00:00Z"),
    ]
    assert listening.from_devices(lib, early) == came()
    assert [name for name, _ in songs_of(lib)] == ["a", "b", "c"]
    late = [
        change("playlist_delete", "m4", playlist_id=used, at="2026-10-11T04:00:00Z"),
        change("playlist_delete", "m5", playlist_id=plain, at="not a time"),  # counts as now
        change("playlist_delete", "m6", playlist_id=old, at="2020-01-01T00:00:00Z"),  # no note
    ]
    assert listening.from_devices(lib, late) == came(deleted=3)
    assert listening.get(lib)["playlists"] == []
    # Only the lists went: nothing else the library keeps about a song is touched.
    assert listening.get(lib)["favourites"] == [] and listening.get(lib)["plays"] == {}


def test_what_a_device_did_to_a_playlist_counts_as_when_it_says(lib: Library) -> None:
    mix = listening.create_playlist(lib, "Mix")[0]["id"]
    at = {"at": "2026-10-10T10:00:00Z"}
    listening.from_devices(
        lib, [change("playlist_add", "m1", playlist_id=mix, track_id="t_1", **at)]
    )
    assert listening.get(lib)["playlists"][0]["changed_at"] == "2026-10-10T10:00:00Z"
    # A second device deleted it five minutes before that: the song put in is the newer word.
    sooner = change("playlist_delete", "m2", playlist_id=mix, at="2026-10-10T09:55:00Z")
    assert listening.from_devices(lib, [sooner]) == came()
    # A time in the future isn't believed: it counts as now, and no later.
    ahead = change(
        "playlist_remove", "m3", playlist_id=mix, track_id="t_1", at="2999-01-01T00:00:00Z"
    )
    assert listening.from_devices(lib, [ahead]) == came(removed=1)
    assert listening.get(lib)["playlists"][0]["changed_at"] < "2999"


def test_by_an_id_of_its_own_a_device_changes_only_a_playlist_made_for_it(lib: Library) -> None:
    driving = listening.create_playlist(lib, "Driving")[0]["id"]  # made here, never seen there
    listening.set_playlist_tracks(lib, driving, ["t_1", "t_2", "t_3"])
    sent = [
        # The device made a "driving" too. It lands in the one there is, and from there
        # on the device may only put songs in it: it has never seen what's in it.
        change("playlist_new", "m1", theirs="a", name="driving"),
        change("playlist_add", "m2", theirs="a", track_id="t_9"),
        change("playlist_remove", "m3", theirs="a", track_id="t_1"),
        change("playlist_order", "m4", theirs="a", tracks=("t_9", "t_3")),
        change("playlist_rename", "m5", theirs="a", name="Night drive"),
        change("playlist_delete", "m6", theirs="a", at="2999-01-01T00:00:00Z"),
        # One that was made here for the device is the device's to change, in the same
        # request or a later one.
        change("playlist_new", "m7", theirs="b", name="Running"),
        change("playlist_add", "m8", theirs="b", track_id="t_1"),
        change("playlist_add", "m9", theirs="b", track_id="t_2"),
        change("playlist_add", "m10", theirs="b", track_id="t_3"),
        change("playlist_remove", "m11", theirs="b", track_id="t_2"),
        change("playlist_order", "m12", theirs="b", tracks=("t_3", "t_1")),
        change("playlist_rename", "m13", theirs="b", name="Run"),
        change("playlist_delete", "m14", theirs="never-made"),
    ]
    assert listening.from_devices(lib, sent) == came(
        playlists=1, songs=4, removed=1, ordered=1, renamed=1
    )
    assert songs_of(lib) == [("Driving", ["t_1", "t_2", "t_3", "t_9"]), ("Run", ["t_3", "t_1"])]
    kept = lib.load_state().data["listening"]["devices"]["made"]
    assert kept == [
        {"theirs": "a", "playlist": driving},
        {"theirs": "b", "playlist": listening.get(lib)["playlists"][1]["id"], "new": True},
    ]
    # Deleted on the device afterwards: gone, and a song put in it later has nowhere to go.
    later = [
        change("playlist_delete", "m15", theirs="b"),
        change("playlist_add", "m16", theirs="b", track_id="t_5"),
    ]
    assert listening.from_devices(lib, later) == came(deleted=1)
    assert songs_of(lib) == [("Driving", ["t_1", "t_2", "t_3", "t_9"])]
    assert lib.load_state().data["listening"]["devices"]["made"] == [
        {"theirs": "a", "playlist": driving}
    ]


def test_a_device_cant_fill_the_librarys_records(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(listening, "TAKEN_KEPT", 4)
    monkeypatch.setattr(listening, "MADE_KEPT", 2)
    monkeypatch.setattr(listening, "MAX_PLAYLISTS", 3)
    monkeypatch.setattr(listening, "MAX_PLAYLIST_SONGS", 2)
    sent = [change("playlist_new", f"n{n}", theirs=f"p{n}", name=f"List {n}") for n in range(5)]
    sent += [change("playlist_add", f"a{n}", track_id=f"t_{n}", theirs="p0") for n in range(4)]
    sent.append(change("playlist_new", "long", theirs="p9", name="x" * 5000))
    sent.append(change("listened", "late", track_id="t_1", seconds=listening.MAX_SECONDS + 1))
    assert listening.from_devices(lib, sent) == came(playlists=3, songs=2)
    found = listening.get(lib)
    assert [p["name"] for p in found["playlists"]] == ["List 0", "List 1", "List 2"]
    assert found["playlists"][0]["track_ids"] == ["t_0", "t_1"]
    assert found["listened"] == {}
    kept = lib.load_state().data["listening"]["devices"]
    assert kept["taken"] == ["a2", "a3", "long", "late"]  # the newest
    assert [m["theirs"] for m in kept["made"]] == ["p1", "p2"]
    # A name longer than the library allows is cut, not refused.
    listening.delete_playlist(lib, found["playlists"][2]["id"])
    listening.from_devices(lib, [change("playlist_new", "long2", theirs="p10", name="y" * 5000)])
    assert listening.get(lib)["playlists"][-1]["name"] == "y" * listening.MAX_NAME


def test_playlists(lib: Library) -> None:
    (made,) = listening.create_playlist(lib, "  Road   trip ")
    assert made["name"] == "Road trip" and made["id"].startswith("pl_")
    assert made["track_ids"] == []
    listening.create_playlist(lib, "Second")
    listening.set_playlist_tracks(lib, made["id"], ["t_2", "t_1", "t_2"])
    listening.rename_playlist(lib, made["id"], "Drive")
    first, second = listening.get(lib)["playlists"]
    assert (first["name"], first["track_ids"]) == ("Drive", ["t_2", "t_1", "t_2"])
    assert [p["name"] for p in listening.delete_playlist(lib, second["id"])] == ["Drive"]
    for bad in ("", "   ", "x" * 201):
        with pytest.raises(UserError):
            listening.create_playlist(lib, bad)
    with pytest.raises(NotFoundError):
        listening.rename_playlist(lib, "pl_nothing", "Name")
    with pytest.raises(NotFoundError):
        listening.set_playlist_tracks(lib, second["id"], [])


def test_a_song_that_left_the_library_is_forgotten(lib: Library) -> None:
    (made,) = listening.create_playlist(lib, "Mix")
    listening.set_playlist_tracks(lib, made["id"], ["t_1", "t_2", "t_1", "t_3"])
    for track_id in ("t_1", "t_2"):
        listening.set_favourite(lib, track_id, True)
        listening.played(lib, track_id)
        listening.listened(lib, track_id, 60)
    listening.move(lib, ["t_1", "t_2"], to_library=True)
    listening.forget(lib, ["t_1", "t_9"])
    found = listening.get(lib)
    assert found["favourites"] == ["t_2"] and list(found["plays"]) == ["t_2"]
    assert found["listened"] == {"t_2": 60.0}
    assert found["library"] == ["t_2"]
    assert found["playlists"][0]["track_ids"] == ["t_2", "t_3"]
    listening.forget(lib, [])  # nothing to do


def test_downloads_moved_into_the_main_library_and_back(lib: Library) -> None:
    assert listening.move(lib, ["t_2", "t_1"], to_library=True) == ["t_1", "t_2"]
    assert listening.move(lib, ["t_1"], to_library=True) == ["t_1", "t_2"]  # already there
    assert listening.get(lib)["library"] == ["t_1", "t_2"]
    assert listening.move(lib, ["t_2", "t_9"], to_library=False) == ["t_1"]
    with pytest.raises(UserError):
        listening.move(lib, ["t_3", ""], to_library=True)
    assert listening.get(lib)["library"] == ["t_1"]  # nothing half done
    listening.set_favourite(lib, "t_1", True)  # the other lists are kept beside it
    assert listening.get(lib)["library"] == ["t_1"]


def test_the_rest_of_state_json_is_untouched_and_damage_is_dropped(lib: Library) -> None:
    with state.edit(lib.paths.state_file) as st:
        st.data["decisions"] = {"i_1": {"decision": "skip"}}
        st.data["listening"] = {
            "favourites": "nonsense",
            "library": ["t_1"],
            "plays": {"t_1": {"count": "x"}},
            "listened": {"t_1": "x", "t_2": -5, "t_3": 12, "": 5, "t_4": True, "t_5": 1e400,
                         "t_6": [1], "t_7": 10**400},
            "playlists": [
                {"id": "pl_1", "name": "Kept", "track_ids": [1, "t_1"]},
                {"name": "no id"},
                "junk",
            ],
            "devices": {
                "taken": ["m1", 2, None, ["m3"]],
                "made": [{"theirs": "a", "playlist": "pl_1"}, {"theirs": "b", "playlist": "pl_x"},
                         {"theirs": 1, "playlist": "pl_1"}, {"theirs": "c", "playlist": ["pl_1"]},
                         "junk"],
            },
        }  # fmt: skip
    found = listening.get(lib)
    assert found["favourites"] == [] and found["plays"] == {} and found["library"] == []
    assert found["listened"] == {"t_3": 12.0}
    assert found["playlists"] == [
        {"id": "pl_1", "name": "Kept", "created_at": None, "track_ids": ["t_1"]}
    ]
    listening.played(lib, "t_1")
    assert lib.load_state().data["listening"]["devices"] == {
        "taken": ["m1"], "made": [{"theirs": "a", "playlist": "pl_1"}],
    }  # fmt: skip
    # Damage where the notes about devices should be doesn't stop a device's changes.
    with state.edit(lib.paths.state_file) as st:
        st.data["listening"]["devices"] = "nonsense"
    listening.from_devices(lib, [listening.DeviceChange("m2", "listened", "t_3", seconds=3)])
    assert listening.get(lib)["listened"] == {"t_3": 15.0}
    assert state.decisions(lib.load_state().data) == {"i_1": {"decision": "skip"}}


def test_rpc_methods(server: rpc.Server, lib: Library) -> None:  # noqa: F811
    result(server, "engine.hello", client="pytest", client_version="1")
    lib.close()
    result(server, "library.open", root=str(lib.root))
    assert result(server, "listening.favourite", track_id="t_1", on=True) == {"favourites": ["t_1"]}
    assert result(server, "listening.played", track_id="t_1")["count"] == 1
    assert result(server, "listening.listened", track_id="t_1", seconds=200.5) == {"seconds": 200.5}
    assert result(server, "listening.listened", track_id="t_1", seconds=3) == {"seconds": 203.5}
    assert result(server, "listening.get")["listened"] == {"t_1": 203.5}
    assert code(server, "listening.listened", track_id="t_1") == rpc.INVALID_PARAMS
    assert code(server, "listening.listened", track_id="t_1", seconds="3") == rpc.INVALID_PARAMS
    assert code(server, "listening.listened", track_id="t_1", seconds=True) == rpc.INVALID_PARAMS
    assert code(server, "listening.listened", track_id="t_1", seconds=-3) == rpc.USER_ERROR
    (made,) = result(server, "playlist.create", name="Mix")["playlists"]
    result(server, "playlist.set_tracks", playlist_id=made["id"], track_ids=["t_1"])
    result(server, "playlist.rename", playlist_id=made["id"], name="Mix 2")
    found = result(server, "listening.get")
    assert found["playlists"][0]["name"] == "Mix 2" and found["playlists"][0]["track_ids"] == [
        "t_1"
    ]
    assert result(server, "playlist.delete", playlist_id=made["id"]) == {"playlists": []}
    assert code(server, "playlist.delete", playlist_id=made["id"]) == rpc.NOT_FOUND
    assert code(server, "playlist.create", name=" ") == rpc.USER_ERROR
    assert code(server, "playlist.set_tracks", playlist_id="x", track_ids=[1]) == rpc.INVALID_PARAMS
    assert code(server, "listening.favourite", track_id="t_1") == rpc.INVALID_PARAMS
    assert result(server, "listening.move", track_ids=["t_1"], to="library") == {"library": ["t_1"]}
    assert result(server, "listening.get")["library"] == ["t_1"]
    assert result(server, "listening.move", track_ids=["t_1"], to="downloads") == {"library": []}
    assert code(server, "listening.move", track_ids=["t_1"], to="elsewhere") == rpc.INVALID_PARAMS


def test_a_song_joins_a_playlist_once(lib: Library) -> None:
    (mix,) = listening.create_playlist(lib, "Mix")
    assert listening.add_to_playlist(lib, mix["id"], "t_one")
    assert listening.add_to_playlist(lib, mix["id"], "t_two")
    assert listening.add_to_playlist(lib, mix["id"], "t_one")  # already in it: left as it is
    assert listening.get(lib)["playlists"][0]["track_ids"] == ["t_one", "t_two"]
    # A playlist that's gone: nothing changes, and it says so.
    assert not listening.add_to_playlist(lib, "pl_gone", "t_one")
    with pytest.raises(UserError):
        listening.add_to_playlist(lib, mix["id"], "")


def test_a_song_heard_to_the_end_from_youtube_is_remembered(lib: Library) -> None:
    assert listening.get(lib)["heard"] == []
    assert listening.heard(lib, "DuQGokwsWF8")["count"] == 1
    again = listening.heard(lib, "DuQGokwsWF8")
    assert again["count"] == 2 and again["last_heard"]
    listening.heard(lib, "abc-def_123")
    assert listening.get(lib)["heard"] == ["DuQGokwsWF8", "abc-def_123"]
    for bad in ("", "short", "../../etc/pw", "x" * 12):
        with pytest.raises(UserError):
            listening.heard(lib, bad)
    # Kept in state.json, beside the play counts, and it survives a damaged entry.
    with state.edit(lib.paths.state_file) as st:
        st.data["listening"]["heard"]["broken0000"] = "not a dict"
    assert listening.get(lib)["heard"] == ["DuQGokwsWF8", "abc-def_123"]


def test_heard_over_rpc(server: rpc.Server, tmp_path: Path) -> None:  # noqa: F811
    root = tmp_path / "Library"
    library.init(root)
    result(server, "engine.hello", client="pytest", client_version="1")
    assert result(server, "library.open", root=str(root)) == {"status": "open"}
    assert result(server, "listening.heard", video_id="DuQGokwsWF8")["count"] == 1
    assert result(server, "listening.get")["heard"] == ["DuQGokwsWF8"]
    assert code(server, "listening.heard", video_id="nope") == rpc.USER_ERROR
