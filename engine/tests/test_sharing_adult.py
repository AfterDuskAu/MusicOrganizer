"""sharing (2026-10-17): the films kept from the Finder for adults, for a phone player
that asks for them.

They're in the Movies folder's `18+` folder (kind `adult`). A phone player is told of
them only when its request for the list says `?adult=1`, only while the films switch is
on and the profile open on the computer isn't a child's; and a file of theirs is given
only to the device whose list named it. A video app never sees them, and no device can
send one. Every share here listens on this computer's own loopback address only.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import quote

import pytest
from test_rpc import Capture, code, opened, out, result, root, server  # noqa: F401  (fixtures)
from test_sharing import ask, fetch, filled, keep, kept, paired, the_list
from test_sharing_video import paired_with, start

from musicorg import config, naming, rpc, sharing
from musicorg.library import Library

_ = filled  # a fixture, used by `shared`


def adult_list(share: sharing.Share, key: str, query: str = "?adult=1") -> dict[str, Any]:
    status, found = ask(share, "/sync/v1/library" + query, key=key)
    assert status == 200, found
    return found


def by_title(found: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["title"]: m for m in found["movies"]}


def status_of(share: sharing.Share, key: str, file: dict[str, Any]) -> int:
    return ask(share, "/sync/v1/files/" + quote(file["id"], safe=""), key=key)[0]


@pytest.fixture
def shared(filled: Library) -> Iterator[Callable[..., sharing.Share]]:
    """A share of the test library with its films switched on, and a film and a film for
    adults kept; `child` says whether a child's profile is open on the computer."""
    kept("movie", "A Film", year="1921")
    adult = kept("adult", "A Film for Adults", year="2001")
    adult.write_bytes(b"made-up bytes of a film for adults")
    made: list[sharing.Share] = []

    def share(child: Callable[[], bool] = lambda: False, films: bool = True) -> sharing.Share:
        one = sharing.Share(filled, films=films, child=child)
        one.start()
        made.append(one)
        return one

    yield share
    for one in made:
        one.stop()


def test_films_for_adults_are_named_only_to_a_phone_that_asks(
    shared: Callable[..., sharing.Share],
) -> None:
    share = shared()
    key = paired(share)
    plain = the_list(share, key)
    assert set(by_title(plain)) == {"A Film (1921)"}
    asked = adult_list(share, key)
    films = by_title(asked)
    assert set(films) == {"A Film (1921)", "A Film for Adults (2001)"}
    adult = films["A Film for Adults (2001)"]
    # Its kind, and nothing more of where it's kept: no show, no folder.
    assert {k: v for k, v in adult.items() if k not in ("id", "added", "video")} == {
        "title": "A Film for Adults (2001)", "kind": "adult",
    }  # fmt: skip
    assert "18+" not in str(asked["movies"])
    assert fetch(share, key, adult["video"]) == b"made-up bytes of a film for adults"
    # The list's revision follows what's in it.
    assert asked["revision"] != plain["revision"]
    assert the_list(share, key)["revision"] == plain["revision"]
    # Only `adult=1` asks for them.
    for other in ("?adult=0", "?adult=true", "?adult=", "?adults=1", "?adult=1&adult=1"):
        assert set(by_title(adult_list(share, key, other))) == {"A Film (1921)"}, other


def test_a_film_for_adults_is_given_only_to_the_device_that_asked(
    shared: Callable[..., sharing.Share],
) -> None:
    share = shared()
    mine, theirs = paired(share), paired(share, "iPad")
    adult = by_title(adult_list(share, mine))["A Film for Adults (2001)"]["video"]
    assert status_of(share, theirs, adult) == 404  # it never asked
    assert status_of(share, mine, adult) == 200
    the_list(share, theirs)  # another device's list doesn't change what this one may have
    assert status_of(share, mine, adult) == 200
    # Its own list made without them: none of them is given to it any more.
    the_list(share, mine)
    share._built_at = None  # (and a list made again for a file it asks for has none either)
    assert status_of(share, mine, adult) == 404


def test_never_in_a_childs_profile(shared: Callable[..., sharing.Share]) -> None:
    open_now = {"child": True}
    share = shared(child=lambda: open_now["child"])
    key = paired(share)
    assert set(by_title(adult_list(share, key))) == {"A Film (1921)"}
    # Asked for while a grown-up's profile was open, then a child's profile is opened:
    # what was named isn't given out.
    open_now["child"] = False
    adult = by_title(adult_list(share, key))["A Film for Adults (2001)"]["video"]
    open_now["child"] = True
    assert status_of(share, key, adult) == 404
    assert set(by_title(adult_list(share, key))) == {"A Film (1921)"}


def test_never_while_the_films_switch_is_off(shared: Callable[..., sharing.Share]) -> None:
    share = shared(films=False)
    key = paired(share)
    assert adult_list(share, key)["movies"] == []
    share.films = True
    adult = by_title(adult_list(share, key))["A Film for Adults (2001)"]["video"]
    share.films = False
    assert status_of(share, key, adult) == 404


def test_a_share_nobody_told_takes_the_profile_for_a_childs(filled: Library) -> None:
    kept("adult", "A Film for Adults")
    share = sharing.Share(filled, films=True)
    share.start()
    try:
        assert adult_list(share, paired(share))["movies"] == []
    finally:
        share.stop()


def test_never_to_a_video_app(
    shared: Callable[..., sharing.Share], monkeypatch: pytest.MonkeyPatch
) -> None:
    share = shared()
    mine = paired(share)
    adult = by_title(adult_list(share, mine))["A Film for Adults (2001)"]["video"]
    theirs = paired_with(share, monkeypatch, "kid-profile")  # a video app, another profile's
    status, films = ask(share, "/sync/v1/films", key=theirs)
    assert status == 200 and [m["title"] for m in films["movies"]] == ["A Film (1921)"]
    assert status_of(share, theirs, adult) == 404
    # Nor to one of this profile's, by the films list.
    status, films = ask(share, "/sync/v1/films", key=mine)
    assert [m["kind"] for m in films["movies"]] == ["movie"]


def test_a_device_cant_send_a_film_for_adults(shared: Callable[..., sharing.Share]) -> None:
    share = shared()
    key = paired(share)
    status, answer = start(share, key, kind="adult")
    assert (status, answer["error"]) == (400, "bad_request")
    assert "movie, series or anime" in answer["message"]
    folder = config.media_folders()["movies"] / naming.ADULT_DIR
    assert sorted(p.name for p in folder.iterdir()) == ["A Film for Adults (2001).mp4"]


def test_the_engine_says_whether_a_childs_profile_is_open(opened: rpc.Server) -> None:  # noqa: F811
    keep(config.media_folders()["movies"] / naming.ADULT_DIR, "A Film.mp4")
    result(opened, "sharing.set", on=True, films=True)
    share = opened._share
    assert share is not None
    assert share.adult_allowed() is False  # the app hasn't said which profile this is yet
    result(opened, "kids.set", on=False)
    assert share.adult_allowed() is True
    result(opened, "kids.set", on=True)
    assert share.adult_allowed() is False
    result(opened, "kids.set", on=False)
    result(opened, "sharing.set", on=True, films=False)
    assert share.adult_allowed() is False
    result(opened, "sharing.set", on=False)
    assert code(opened, "sharing.set", on=True, films="yes") == rpc.INVALID_PARAMS
