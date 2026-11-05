"""sharing (2026-11-05): the computer's TMDB key, for a paired device that asks.

The owner: "make the TMDB api that was imputted into Bear Media, pass over to any synced
apps ... This will account for all accounts ... They all connect to D's inputed api key".
`GET /sync/v1/tmdb` answers a device paired with any profile on this computer; nobody
else, and the key is never in the log.

Every share here listens on this computer's own loopback address only (conftest), and is
asked over real HTTP. The key is made while the test runs, in TMDB's shape.
"""

from __future__ import annotations

import logging

import pytest
from test_sharing import ask, filled, paired  # noqa: F401  (fixtures)
from test_sharing import share as share  # noqa: F401  (a fixture)
from test_sharing_video import KID, paired_with

from musicorg import config, sharing

KEY = "".join(["0a1b2c3d", "4e5f6a7b"] * 2)  # a made-up 32-character API Key


def test_a_paired_device_is_given_the_key(share: sharing.Share) -> None:
    config.save_computer_account("tmdb", {"key": KEY})
    device = paired(share, "iPad")
    status, answer = ask(share, "/sync/v1/tmdb", key=device)
    assert status == 200
    assert answer == {"key": KEY}


def test_a_device_of_another_profile_is_given_it_too(
    share: sharing.Share, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The key is the computer's: every profile's devices (a child's too) use it.
    config.save_computer_account("tmdb", {"key": KEY})
    device = paired_with(share, monkeypatch, KID)
    assert ask(share, "/sync/v1/tmdb", key=device) == (200, {"key": KEY})


def test_with_no_key_saved_the_answer_says_so(share: sharing.Share) -> None:
    device = paired(share, "iPad")
    assert ask(share, "/sync/v1/tmdb", key=device) == (200, {"key": None})


def test_nobody_else_is_given_it(share: sharing.Share) -> None:
    config.save_computer_account("tmdb", {"key": KEY})
    paired(share, "iPad")
    for key in (None, "not-a-key-" + "x" * 30):
        status, answer = ask(share, "/sync/v1/tmdb", key=key)
        assert status == 401
        assert KEY not in str(answer)


def test_a_device_that_unpaired_itself_is_not_given_it(share: sharing.Share) -> None:
    config.save_computer_account("tmdb", {"key": KEY})
    device = paired(share, "iPad")
    assert ask(share, "/sync/v1/forget", key=device, body={})[0] == 200
    assert ask(share, "/sync/v1/tmdb", key=device)[0] == 401


def test_the_key_is_never_logged(share: sharing.Share, caplog: pytest.LogCaptureFixture) -> None:
    config.save_computer_account("tmdb", {"key": KEY})
    device = paired(share, "iPad")
    with caplog.at_level(logging.DEBUG):
        assert ask(share, "/sync/v1/tmdb", key=device)[0] == 200
    assert "TMDB key (given)" in caplog.text
    assert KEY not in caplog.text
