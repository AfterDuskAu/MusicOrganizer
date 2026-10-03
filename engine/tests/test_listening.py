"""listening (v0.2): favourites, play counts and playlists in state.json."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_rpc import code, out, result, server  # noqa: F401  (out and server are fixtures)

from musicorg import library, listening, rpc, state
from musicorg.errors import NotFoundError, UserError
from musicorg.library import Library


def test_nothing_yet(lib: Library) -> None:
    assert listening.get(lib) == {"favourites": [], "plays": {}, "playlists": [], "library": [],
                                  "heard": []}  # fmt: skip


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
    listening.move(lib, ["t_1", "t_2"], to_library=True)
    listening.forget(lib, ["t_1", "t_9"])
    found = listening.get(lib)
    assert found["favourites"] == ["t_2"] and list(found["plays"]) == ["t_2"]
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
            "playlists": [
                {"id": "pl_1", "name": "Kept", "track_ids": [1, "t_1"]},
                {"name": "no id"},
                "junk",
            ],
        }
    found = listening.get(lib)
    assert found["favourites"] == [] and found["plays"] == {} and found["library"] == []
    assert found["playlists"] == [
        {"id": "pl_1", "name": "Kept", "created_at": None, "track_ids": ["t_1"]}
    ]
    listening.played(lib, "t_1")
    assert state.decisions(lib.load_state().data) == {"i_1": {"decision": "skip"}}


def test_rpc_methods(server: rpc.Server, lib: Library) -> None:  # noqa: F811
    result(server, "engine.hello", client="pytest", client_version="1")
    lib.close()
    result(server, "library.open", root=str(lib.root))
    assert result(server, "listening.favourite", track_id="t_1", on=True) == {"favourites": ["t_1"]}
    assert result(server, "listening.played", track_id="t_1")["count"] == 1
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
