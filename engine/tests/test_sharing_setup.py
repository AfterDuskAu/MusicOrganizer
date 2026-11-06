"""sharing (2026-11-06): a video app set up with every profile by one code.

The owner: "Is there a way to automatically pair the accounts to any new devices? So that i
dont have to pair every new device to each account, and it grabs the plugins, etc from that
account?" Asked, they chose that it still takes a code, and that every plugin is copied, one
set up by its own address too. `POST /sync/v1/setup` with the set-up code tells a device the
profiles and the plugins and gives it a pass; each account then pairs with its profile by
that pass (`POST /sync/v1/pair`), one key each, as by hand.

Every share here listens on this computer's own loopback address only (conftest), and is
asked over real HTTP. No code, pass or key is written in this file: each is made while the
test runs. The plugins are made up, at addresses that are never asked.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest
from conftest import LOOPBACK
from test_rpc import Capture, code, opened, out, result, root, server  # noqa: F401  (fixtures)
from test_sharing import ask, paired, wrong  # noqa: F401  (fixtures)
from test_sharing import filled as filled  # noqa: F401  (a fixture)
from test_sharing import share as share  # noqa: F401  (a fixture)

from musicorg import config, rpc, sharing
from musicorg.library import Library

OWNER, OTHER, KID = "default", "c-profile", "kid-profile"
PROFILES = [
    {"id": OWNER, "name": "D", "child": False},
    {"id": OTHER, "name": "  C  ", "child": False, "ratings": ["G"]},  # not a child's: no ratings
    {"id": KID, "name": "Made-up Kid", "child": True, "ratings": ["PG", "G", "X18", "M"]},
]
# A made-up setting a plugin's address may carry, built while the test runs.
SET_UP = "token=" + "a1B2" * 6
PLAIN = {
    "id": "made.up.plain",
    "name": "Made-up Plain",
    "address": "https://addons.example/plain/manifest.json",
}
OWN = {"id": "made.up.own", "name": "Made-up Own Set-up",
       "address": f"https://addons.example/{SET_UP}/manifest.json"}  # fmt: skip
ADULT = {"id": "made.up.adult", "name": "Made-up Adults Only", "adult": True,
         "address": "https://adults.example/manifest.json"}  # fmt: skip
ODD = {"id": "made.up.odd", "name": "Made-up Odd", "address": "file:///nowhere/manifest.json"}


def code_for(share: sharing.Share, profiles: list[dict[str, Any]] = PROFILES) -> str:
    return share.new_setup_code(sharing.setup_profiles(profiles))["code"]


def set_up(share: sharing.Share, shown: str, device: str = "Android") -> tuple[int, Any]:
    return ask(share, "/sync/v1/setup", body={"code": shown, "device": device})


def by_pass(share: sharing.Share, given: str, profile: str, **sent: Any) -> tuple[int, Any]:
    key = sent.pop("key", None)
    body = {"setup": given, "profile": profile, "device": "Android", **sent}
    return ask(share, "/sync/v1/pair", key=key, body=body)


def lines(profile: str) -> list[dict[str, Any]]:
    return sharing._saved(profile)


def test_one_code_tells_a_device_every_profile_and_the_plugins(share: sharing.Share) -> None:
    config.save_addons([PLAIN, ADULT, OWN, ODD])
    status, answer = set_up(share, code_for(share))
    assert status == 200, answer
    assert set(answer) == {"format", "setup", "seconds", "profiles", "plugins"}
    assert answer["format"] == 1 and answer["seconds"] == sharing.SETUP_PASS_S
    assert len(answer["setup"]) >= 40  # a long random pass, not a code
    # Every profile, in the app's order: its name, its child mark, and a child's ratings
    # (in their own order, words that aren't ratings left out). Nothing else of a profile.
    assert [{k: v for k, v in one.items() if k != "profile"} for one in answer["profiles"]] == [
        {"name": "D", "child": False},
        {"name": "C", "child": False},
        {"name": "Made-up Kid", "child": True, "ratings": ["G", "PG", "M"]},
    ]
    named = [one["profile"] for one in answer["profiles"]]
    assert named == [sharing.profile_name(p) for p in (OWNER, OTHER, KID)]
    assert all(n.startswith("pr-") and len(n) == 19 for n in named)
    assert OWNER not in str(named) and KID not in str(named)  # a profile's own id isn't sent
    # The plugins in the owner's order, a set-up address too (the owner's choice); never
    # one for adults only, nor one that isn't read over the web.
    assert answer["plugins"] == [
        {"name": PLAIN["name"], "address": PLAIN["address"]},
        {"name": OWN["name"], "address": OWN["address"]},
    ]
    # Nothing is paired by it.
    assert all(lines(p) == [] for p in (OWNER, OTHER, KID))


def test_each_account_pairs_with_its_profile_by_the_pass(share: sharing.Share) -> None:
    answer = set_up(share, code_for(share))[1]
    keys = {}
    for profile in answer["profiles"]:
        status, paired_now = by_pass(
            share, answer["setup"], profile["profile"], id=str(uuid.uuid4())
        )
        assert status == 200, paired_now
        keys[profile["name"]] = paired_now["key"]
    assert len(set(keys.values())) == 3  # a key each
    # A line in each profile's list, as if paired by hand there.
    for profile in (OWNER, OTHER, KID):
        (line,) = lines(profile)
        assert line["device"] == "Android"
    # Each key is its own profile's: the open profile's for the music, and every one for
    # its own watching (which the app hasn't given yet).
    assert ask(share, "/sync/v1/library", key=keys["D"])[0] == 200
    assert ask(share, "/sync/v1/library", key=keys["C"])[0] == 401  # another profile's
    for key in keys.values():
        assert ask(share, "/sync/v1/hello", key=key)[1]["known"] is True
        assert ask(share, "/sync/v1/watching", key=key)[1]["error"] == "unavailable"
    assert sharing._any_device_with("Bearer " + keys["Made-up Kid"])[0] == KID
    # Each profile once: the pass doesn't pair a profile twice.
    status, again = by_pass(share, answer["setup"], answer["profiles"][0]["profile"])
    assert (status, again["error"]) == (403, "wrong_code")
    assert len(lines(OWNER)) == 1


def test_the_pass_is_for_its_profiles_and_its_five_minutes(filled: Library) -> None:
    now = [1000.0]
    share = sharing.Share(filled, clock=lambda: now[0])
    share.start()
    try:
        answer = set_up(share, code_for(share))[1]
        given, first = answer["setup"], answer["profiles"][0]["profile"]
        for wrong_one in (given + "x", ""):
            status, refused = by_pass(share, wrong_one, first)
            assert (status, refused["error"]) == (403, "wrong_code")
        # A profile it didn't name, or something that isn't one.
        for odd in (sharing.profile_name("someone-else"), None):
            status, refused = by_pass(share, given, odd)  # type: ignore[arg-type]
            assert (status, refused["error"]) == (403, "wrong_code")
        assert by_pass(share, given, first)[0] == 200  # those didn't use it up
        for odd in (7, ["pr-"], first):  # (the first profile is paired now)
            status, refused = by_pass(share, given, odd)  # type: ignore[arg-type]
            assert (status, refused["error"]) == (403, "wrong_code")
        now[0] += sharing.SETUP_PASS_S + 1
        status, refused = by_pass(share, given, answer["profiles"][1]["profile"])
        assert (status, refused["error"]) == (403, "wrong_code")
        assert "Add a Device" in refused["message"]
    finally:
        share.stop()


def test_wrong_passes_count_with_wrong_codes(filled: Library) -> None:
    share = sharing.Share(filled)
    share.start()
    try:
        answer = set_up(share, code_for(share))[1]
        for _ in range(sharing.WRONG_TRIES):
            assert by_pass(share, answer["setup"] + "x", answer["profiles"][0]["profile"])[0] == 403
        # Every code, and the pass itself, is refused for a minute.
        status, refused = by_pass(share, answer["setup"], answer["profiles"][0]["profile"])
        assert (status, refused["error"]) == (429, "too_many_tries")
        shown = share.new_code()["code"]
        status, refused = ask(share, "/sync/v1/pair", body={"code": shown, "device": "iPad"})
        assert (status, refused["error"]) == (429, "too_many_tries")
        assert set_up(share, code_for(share))[0] == 429
    finally:
        share.stop()


def test_the_code_works_once_and_runs_out(filled: Library) -> None:
    now = [1000.0]
    share = sharing.Share(filled, clock=lambda: now[0])
    share.start()
    try:
        status, refused = set_up(share, "123456")
        assert (status, refused["error"]) == (403, "wrong_code")  # none showing
        assert "Add a Device" in refused["message"]
        shown = code_for(share)
        assert share.status()["setting_up"] is True
        assert share.status()["setting_up_seconds_left"] == sharing.CODE_LIFE_S
        status, refused = set_up(share, wrong(shown))
        assert (status, refused["error"]) == (403, "wrong_code")
        assert set_up(share, shown)[0] == 200
        assert share.status()["setting_up"] is False
        assert set_up(share, shown)[0] == 403  # once
        shown = code_for(share)
        now[0] += sharing.CODE_LIFE_S + 1
        assert set_up(share, shown)[0] == 403  # run out
        # A new code: the one before stops working, and so does a pass not used up.
        given = set_up(share, code_for(share))[1]["setup"]
        old = code_for(share)
        newer = code_for(share)
        assert old == newer or set_up(share, old)[0] == 403
        share.stop_pairing()
        assert share.status()["setting_up"] is False
        assert set_up(share, newer)[0] == 403
        status, refused = by_pass(share, given, sharing.profile_name(OWNER))
        assert (status, refused["error"]) == (403, "wrong_code")
    finally:
        share.stop()


def test_a_set_up_code_and_a_pairing_code_are_apart(share: sharing.Share) -> None:
    setting_up, pairing = code_for(share), share.new_code()["code"]
    if setting_up == pairing:  # one chance in a million: make them differ
        setting_up = code_for(share)
    # Each request takes only its own kind, and says so of the other.
    status, refused = ask(share, "/sync/v1/pair", body={"code": setting_up, "device": "iPad"})
    assert (status, refused["error"]) == (403, "wrong_code")
    assert "Pair a Device" in refused["message"]
    status, refused = set_up(share, pairing)
    assert (status, refused["error"]) == (403, "wrong_code")
    assert "Add a Device" in refused["message"]
    # Both still work, each for its own: asking for one didn't take the other away.
    assert ask(share, "/sync/v1/pair", body={"code": pairing, "device": "iPad"})[0] == 200
    assert set_up(share, setting_up)[0] == 200


def test_set_up_again_each_account_takes_its_old_place(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The device's first account was paired with D by hand before.
    mine = str(uuid.uuid4())
    shown = share.new_code()["code"]
    old_key = ask(share, "/sync/v1/pair", body={"code": shown, "device": "iPad", "id": mine})[1][
        "key"
    ]
    theirs = str(uuid.uuid4())
    first = set_up(share, code_for(share))[1]
    other_key = by_pass(share, first["setup"], sharing.profile_name(OTHER), id=theirs)[1]["key"]
    before = {p: [d["id"] for d in lines(p)] for p in (OWNER, OTHER)}

    # Set up again: known by its key, or by its own id, an account keeps its line.
    again = set_up(share, code_for(share))[1]
    new_mine = by_pass(share, again["setup"], sharing.profile_name(OWNER), key=old_key, id=mine)
    new_theirs = by_pass(share, again["setup"], sharing.profile_name(OTHER), id=theirs)
    assert new_mine[0] == 200 and new_theirs[0] == 200
    assert {p: [d["id"] for d in lines(p)] for p in (OWNER, OTHER)} == before
    # The old keys stop working; the new ones take their place.
    assert ask(share, "/sync/v1/hello", key=old_key)[1]["known"] is False
    assert ask(share, "/sync/v1/hello", key=other_key)[1]["known"] is False
    assert ask(share, "/sync/v1/hello", key=new_mine[1]["key"])[1]["paired"] is True
    # A key of another profile's doesn't move a line between profiles.
    third = set_up(share, code_for(share))[1]
    assert (
        by_pass(share, third["setup"], sharing.profile_name(KID), key=new_theirs[1]["key"])[0]
        == 200
    )
    assert len(lines(KID)) == 1 and len(lines(OTHER)) == 1
    assert ask(share, "/sync/v1/hello", key=new_theirs[1]["key"])[1]["known"] is True


def test_nothing_of_it_is_logged_or_kept(
    share: sharing.Share, caplog: pytest.LogCaptureFixture
) -> None:
    config.save_addons([OWN])
    with caplog.at_level(logging.DEBUG):
        shown = code_for(share)
        answer = set_up(share, shown)[1]
        key = by_pass(share, answer["setup"], answer["profiles"][2]["profile"])[1]["key"]
    assert "being set up (Android, 3 profiles)" in caplog.text
    assert "paired by a set-up" in caplog.text
    for secret in (shown, answer["setup"], key, SET_UP, "Made-up Kid", LOOPBACK):
        assert secret not in caplog.text
    # The profiles were only in memory: only the paired line is kept, as by hand.
    saved = config.devices_path().read_text(encoding="utf-8")
    assert "Made-up Kid" not in saved and answer["setup"] not in saved and key not in saved
    assert len(lines(KID)) == 1


def test_profiles_that_cant_be_read_are_refused() -> None:
    too_many = [{"id": f"p{n}", "name": "D"} for n in range(sharing.MOST_PROFILES + 1)]
    for odd in (None, [], "D", [{"id": OWNER}], [{"name": "D"}], [{"id": "no spaces", "name": "D"}],
                [{"id": OWNER, "name": "D"}, {"id": OWNER, "name": "E"}],
                [{"id": OWNER, "name": "D", "child": "yes"}], ["D"], too_many):  # fmt: skip
        with pytest.raises(ValueError):
            sharing.setup_profiles(odd)
    (only,) = sharing.setup_profiles([{"id": OWNER, "name": " Two   words \u0007", "child": True}])
    assert only == (
        OWNER,
        {"profile": sharing.profile_name(OWNER), "name": "Two words", "child": True},
    )
    (long,) = sharing.setup_profiles([{"id": OWNER, "name": "x" * 500}])
    assert len(long[1]["name"]) == sharing.MOST_PROFILE_NAME


def test_a_set_up_code_needs_sharing_on(filled: Library) -> None:
    share = sharing.Share(filled)
    with pytest.raises(sharing.UserError):
        share.new_setup_code(sharing.setup_profiles(PROFILES))


def test_the_app_asks_for_a_set_up_code(
    opened: rpc.Server,  # noqa: F811
    out: Capture,  # noqa: F811
) -> None:
    assert code(opened, "sharing.setup", profiles=PROFILES) == rpc.USER_ERROR  # sharing off
    result(opened, "sharing.set", on=True)
    share = opened._share
    assert share is not None
    for odd in ({}, {"profiles": []}, {"profiles": [{"id": "x y", "name": "D"}]}):
        assert code(opened, "sharing.setup", **odd) == rpc.INVALID_PARAMS
    shown = result(opened, "sharing.setup", profiles=PROFILES)
    assert set(shown) == {"code", "seconds"} and shown["seconds"] == sharing.CODE_LIFE_S
    status = result(opened, "sharing.status")
    assert (status["setting_up"], status["pairing"]) == (True, False)
    answer = set_up(share, shown["code"])[1]
    assert out.notes("sharing.changed") == [{}]  # the code was used: the app closes it
    assert result(opened, "sharing.status")["setting_up"] is False
    key = by_pass(share, answer["setup"], answer["profiles"][0]["profile"], id=str(uuid.uuid4()))[
        1
    ]["key"]
    (device,) = result(opened, "sharing.status")["devices"]  # the open profile's own line
    assert device["device"] == "Android"
    assert len(out.notes("sharing.changed")) == 2
    # The code, the pass and the key reach only the device: nothing sent to the app has them.
    sent = out.data.decode("utf-8")
    assert answer["setup"] not in sent and key not in sent
    assert result(opened, "sharing.setup", profiles=PROFILES)["code"] != ""
    assert result(opened, "sharing.stop_pairing")["setting_up"] is False


def test_hello_says_a_device_can_be_set_up(share: sharing.Share) -> None:
    assert ask(share, "/sync/v1/hello")[1]["accepts"][-2:] == ["setup", "forget"]
    # Asked, not told: a GET there is answered as any other without a key.
    assert ask(share, "/sync/v1/setup")[0] == 401
