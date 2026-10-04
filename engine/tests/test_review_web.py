"""The local review page (`musicorg review serve`): its safety checks, the lists it shows,
decisions made from it, and playing a rip."""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from index_support import add_candidates, add_item, add_source, candidate

from musicorg import cli, review, review_web
from musicorg.errors import EXIT_OK, UserError
from musicorg.index import Index, open_index
from musicorg.library import Library


@pytest.fixture
def index(lib: Library, tmp_path: Path) -> Iterator[Index]:
    with open_index(lib.paths, write=True) as opened:
        add_source(opened, tmp_path / "rips")
        yield opened


@pytest.fixture
def items(index: Index, tmp_path: Path) -> dict[str, str]:
    ids = {
        "doubt": add_item(index, "Band - Doubt", state="review", reasons=["title_fuzzy"]),
        "near": add_item(index, "Band - Near", state="review", reasons=["duration_mismatch"]),
        "far": add_item(index, "Band - Far", state="review", reasons=["duration_mismatch"]),
        "missing": add_item(index, "Band - Missing", state="not_found"),
        "auto": add_item(index, "Band - Auto", state="matched_auto"),
        "new": add_item(index, "Band - Unmatched"),
    }
    add_candidates(index, ids["doubt"], [
        candidate("Dou1xxxxxxx", "Doubt!", ("Band",)),
        candidate("Dou2xxxxxxx", "Doubt (Live)", ("Band",)),
        candidate("Dou3xxxxxxx", "Doubt", ("Other",), explicit=True),
    ])  # fmt: skip
    add_candidates(index, ids["near"], [candidate("Nea1xxxxxxx", "Near", ("Band",), 204)])
    add_candidates(index, ids["far"], [candidate("Far1xxxxxxx", "Far", ("Band",), 260)])
    rips = tmp_path / "rips"
    rips.mkdir()
    (rips / "Band - Doubt.mp3").write_bytes(bytes(range(256)) * 40)  # 10,240 bytes
    return ids


@pytest.fixture
def server(lib: Library, items: dict[str, str]) -> Iterator[review_web.ReviewServer]:
    running = review_web.ReviewServer(lib)
    thread = threading.Thread(target=running.serve_forever, daemon=True)
    thread.start()
    yield running
    running.shutdown()
    running.server_close()
    thread.join()


def call(
    server: review_web.ReviewServer,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    key: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    sent = {"X-Review-Key": server.key if key is None else key, **(headers or {})}
    if body is not None:
        sent.setdefault("Content-Type", "application/json")
        conn.request("POST", path, json.dumps(body), sent)
    else:
        conn.request("GET", path, headers=sent)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, dict(response.getheaders()), data


def api(server: review_web.ReviewServer, path: str, body: dict[str, Any] | None = None) -> Any:
    status, _, data = call(server, path, body)
    assert status == 200, data
    return json.loads(data)


# ---- safety ----------------------------------------------------------------------------


def test_it_only_listens_on_this_mac(server: review_web.ReviewServer) -> None:
    assert server.server_address[0] == "127.0.0.1"
    assert server.url.startswith(f"http://127.0.0.1:{server.server_port}/?key=")


def test_the_page_needs_the_key(server: review_web.ReviewServer) -> None:
    status, headers, page = call(server, f"/?key={server.key}", key="")
    assert status == 200 and b"Music Organizer" in page
    assert "Content-Security-Policy" in headers
    assert b"<script src" not in page  # nothing loaded from elsewhere
    assert call(server, "/", key="")[0] == 403
    assert call(server, "/api/summary", key="wrong")[0] == 403


def test_other_sites_cant_use_it(server: review_web.ReviewServer) -> None:
    # DNS rebinding: a request that reached this port under another name.
    assert call(server, "/api/summary", headers={"Host": "evil.example"})[0] == 403
    # A cross-site form or script.
    body = {"item_id": "x", "decision": "skip"}
    assert call(server, "/api/decide", body, headers={"Origin": "https://evil.example"})[0] == 403
    assert call(server, "/api/decide", body, headers={"Content-Type": "text/plain"})[0] == 415


# ---- the lists -------------------------------------------------------------------------


def test_summary_and_lists(server: review_web.ReviewServer, items: dict[str, str]) -> None:
    summary = api(server, "/api/summary")
    assert summary["groups"] == {"doubts": 1, "length": 2, "not_found": 1, "auto": 1,
                                 "decided": 0}  # fmt: skip
    length = api(server, "/api/items?group=length")["items"]
    assert [i["title"] for i in length] == ["Near", "Far"]  # smallest difference first
    doubt = api(server, "/api/items?group=doubts")["items"][0]
    assert (doubt["artist"], doubt["title"], doubt["duration"]) == ("Band", "Doubt", "3:20")
    assert doubt["reasons"] == [{"code": "title_fuzzy", "text": "title not exactly the same"}]
    assert [c["video_id"] for c in doubt["candidates"]][0] == "Dou1xxxxxxx"
    first = doubt["candidates"][0]
    assert first["link"] == "https://music.youtube.com/watch?v=Dou1xxxxxxx"
    assert first["delta_s"] == 0 and first["duration"] == "3:20"
    assert all(n not in review_web.REASON_MEANING for n in first["notes"])  # plain words only
    assert call(server, "/api/items?group=everything")[0] == 400


# ---- decisions -------------------------------------------------------------------------


def test_decisions(server: review_web.ReviewServer, items: dict[str, str], lib: Library) -> None:
    used = api(server, "/api/decide", {"item_id": items["doubt"], "decision": "use",
                                       "video_id": "Dou2xxxxxxx"})  # fmt: skip
    assert used["changed"] and used["item"]["state"] == "matched_user"
    assert used["item"]["decision"]["decision"] == "candidate"
    assert used["item"]["decision"]["video_id"] == "Dou2xxxxxxx"
    again = api(server, "/api/decide", {"item_id": items["doubt"], "decision": "use",
                                        "video_id": "Dou2xxxxxxx"})  # fmt: skip
    assert not again["changed"]

    kept = api(server, "/api/decide", {"item_id": items["missing"], "decision": "only_copy",
                                       "fixes": {"title_fix": "Missing (Demo)"}})  # fmt: skip
    assert kept["item"]["state"] == "only_copy"
    assert kept["item"]["decision"]["title_fix"] == "Missing (Demo)"
    skipped = api(server, "/api/decide", {"item_id": items["near"], "decision": "skip"})
    assert skipped["item"]["state"] == "skipped"
    summary = api(server, "/api/summary")["groups"]
    assert (summary["decided"], summary["doubts"], summary["not_found"]) == (3, 0, 0)
    decisions = json.loads(lib.paths.state_file.read_text(encoding="utf-8"))["decisions"]
    assert set(decisions) == {items["doubt"], items["missing"], items["near"]}


def test_rejecting(server: review_web.ReviewServer, items: dict[str, str], index: Index) -> None:
    one = api(server, "/api/decide", {"item_id": items["doubt"], "decision": "reject",
                                      "video_id": "Dou1xxxxxxx"})  # fmt: skip
    assert [c["video_id"] for c in one["item"]["candidates"]] == ["Dou2xxxxxxx", "Dou3xxxxxxx"]
    every = api(server, "/api/decide", {"item_id": items["doubt"], "decision": "reject",
                                        "video_ids": ["Dou2xxxxxxx", "Dou3xxxxxxx"]})  # fmt: skip
    assert every["item"]["candidates"] == []
    assert every["item"]["state"] == "not_found"


def test_a_pasted_link(server: review_web.ReviewServer, index: Index) -> None:
    """Replayed: xjj_OVvVQFc is Flight Facilities' Crave You (3:55)."""
    iid = add_item(index, "Flight Facilities - Crave You", state="not_found", seconds=235)
    done = api(server, "/api/decide", {"item_id": iid, "decision": "url",
                                       "link": "https://youtu.be/xjj_OVvVQFc"})  # fmt: skip
    assert done["item"]["state"] == "matched_user"
    assert done["item"]["decision"]["video_id"] == "xjj_OVvVQFc"


def test_a_low_scoring_pasted_link_is_first_on_the_page(
    server: review_web.ReviewServer, index: Index, lib: Library
) -> None:
    """Replayed: SpTYh-uYgQs is Michael Franti & Spearhead's Bomb the World (4:29). It
    scores 0.35 against this rip; the three candidates already there score more."""
    iid = add_item(index, "Kygo - Bomb the World R", state="review", seconds=240,
                   reasons=["version_mismatch"])  # fmt: skip
    add_candidates(index, iid, [
        candidate("Bom1xxxxxxx", "Bomb the World", ("Kygo",), 240),
        candidate("Bom2xxxxxxx", "Bomb the World (Live)", ("Kygo",), 300),
        candidate("Bom3xxxxxxx", "Bomb the World", ("Kygo",), 250),
    ])  # fmt: skip
    pasted = api(server, "/api/decide", {"item_id": iid, "decision": "url",
                                         "link": "https://youtu.be/SpTYh-uYgQs"})  # fmt: skip
    assert not pasted["changed"] and "below 0.60" in pasted["warnings"][0]
    assert pasted["item"]["state"] == "review" and pasted["item"]["decision"] is None
    assert pasted["item"]["reasons"][0]["code"] == "url_low_score"
    # First of the three the page shows, with its real score; then the best by score.
    shown = pasted["item"]["candidates"]
    assert [c["video_id"] for c in shown] == ["SpTYh-uYgQs", "Bom1xxxxxxx", "Bom3xxxxxxx"]
    assert (shown[0]["score"], shown[0]["artists"]) == (0.35, ["Michael Franti & Spearhead"])
    # The list the page loads says the same.
    listed = next(i for i in api(server, "/api/items?group=doubts")["items"] if i["id"] == iid)
    assert [c["video_id"] for c in listed["candidates"]] == [c["video_id"] for c in shown]

    used = api(server, "/api/decide", {"item_id": iid, "decision": "use",
                                       "video_id": "SpTYh-uYgQs"})  # fmt: skip
    assert used["changed"] and used["warnings"] == []
    assert used["item"]["state"] == "matched_user"
    assert used["item"]["decision"]["decision"] == "accept"  # it was candidate 1
    assert used["item"]["decision"]["video_id"] == "SpTYh-uYgQs"
    decisions = json.loads(lib.paths.state_file.read_text(encoding="utf-8"))["decisions"]
    assert decisions[iid]["video_id"] == "SpTYh-uYgQs"


def test_rejecting_a_pasted_link_on_the_page(server: review_web.ReviewServer, index: Index) -> None:
    iid = add_item(index, "Kygo - Bomb the World R", state="review", seconds=240,
                   reasons=["version_mismatch"])  # fmt: skip
    add_candidates(index, iid, [
        candidate("Bom1xxxxxxx", "Bomb the World", ("Kygo",), 240),
        candidate("Bom3xxxxxxx", "Bomb the World", ("Kygo",), 250),
    ])  # fmt: skip
    api(server, "/api/decide", {"item_id": iid, "decision": "url",
                                "link": "https://youtu.be/SpTYh-uYgQs"})  # fmt: skip
    gone = api(server, "/api/decide", {"item_id": iid, "decision": "reject",
                                       "video_id": "SpTYh-uYgQs"})  # fmt: skip
    assert [c["video_id"] for c in gone["item"]["candidates"]] == ["Bom1xxxxxxx", "Bom3xxxxxxx"]
    assert [r["code"] for r in gone["item"]["reasons"]] == ["version_mismatch"]


def test_bad_decisions_say_why(server: review_web.ReviewServer, items: dict[str, str]) -> None:
    cases = [
        ({"item_id": "i_0000000000000000", "decision": "skip"}, "There's no item"),
        ({"item_id": items["doubt"], "decision": "maybe"}, "isn't a decision"),
        ({"item_id": items["doubt"], "decision": "use", "video_id": "zzzzzzzzzzz"},
         "isn't one of this item's candidates"),
        ({"item_id": items["doubt"], "decision": "url", "link": "https://vimeo.com/1"},
         "isn't a YouTube link"),
    ]  # fmt: skip
    for body, message in cases:
        status, _, data = call(server, "/api/decide", body)
        assert status == 400
        assert message in json.loads(data)["error"]


def test_decide_one_refuses_a_rip_already_replaced(
    lib: Library, index: Index, items: dict[str, str]
) -> None:
    index.set_state(items["doubt"], "superseded")
    with pytest.raises(UserError, match="already superseded"):
        review.decide_one(lib, index, items["doubt"], "skip")


# ---- playing a rip ---------------------------------------------------------------------


def test_playing_a_rip(
    server: review_web.ReviewServer, items: dict[str, str], tmp_path: Path
) -> None:
    whole = (tmp_path / "rips" / "Band - Doubt.mp3").read_bytes()
    path = f"/api/audio/{items['doubt']}?key={server.key}"
    status, headers, data = call(
        server, path, key=""
    )  # the key in the address, as <audio> sends it
    assert (status, data) == (200, whole)
    assert headers["Content-Type"] == "audio/mpeg" and headers["Accept-Ranges"] == "bytes"
    status, headers, data = call(server, path, headers={"Range": "bytes=100-199"})
    assert (status, data, headers["Content-Range"]) == (206, whole[100:200], "bytes 100-199/10240")
    status, _, data = call(server, path, headers={"Range": "bytes=-10"})
    assert (status, data) == (206, whole[-10:])
    assert call(server, path, headers={"Range": "bytes=20000-"})[0] == 416
    assert call(server, f"/api/audio/{items['near']}")[0] == 404  # its file isn't there
    assert call(server, "/api/audio/i_0000000000000000")[0] == 404
    assert call(server, "/api/audio/../../etc/passwd")[0] == 404


# ---- the command -----------------------------------------------------------------------


def test_the_command(
    capsys: pytest.CaptureFixture[str], lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake_serve(opened: Library, **kw: Any) -> None:
        seen.update(writable=opened.writable, **kw)
        kw["ready"]("http://127.0.0.1:1234/?key=k")

    monkeypatch.setattr(review_web, "serve", fake_serve)
    lib.close()
    code = cli.main(["review", "serve", "--no-open", "--port", "1234"])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "http://127.0.0.1:1234/?key=k" in out
    assert "Review page closed. Your decisions are saved." in out
    assert seen["writable"] is True  # it holds the library's lock
    assert (seen["port"], seen["open_browser"]) == (1234, False)


# ---- confirming an artist's other name -------------------------------------------------


def test_a_choice_by_another_artist_offers_to_remember_the_name(
    server: review_web.ReviewServer, index: Index, lib: Library
) -> None:
    juicy = add_item(index, "Biggie Smalls - Juicy", state="review", reasons=["artist_mismatch"])
    add_candidates(index, juicy, [candidate("Jui1xxxxxxx", "Juicy", ("The Notorious B.I.G.",))])
    hypnotize = add_item(index, "Biggie Smalls - Hypnotize", state="review",
                         reasons=["artist_mismatch"])  # fmt: skip
    add_candidates(index, hypnotize,
                   [candidate("Hyp1xxxxxxx", "Hypnotize", ("The Notorious B.I.G.",))])  # fmt: skip

    used = api(server, "/api/decide", {"item_id": juicy, "decision": "use",
                                       "video_id": "Jui1xxxxxxx"})  # fmt: skip
    assert used["alias_offer"] == {"from": "Biggie Smalls", "to": "The Notorious B.I.G.",
                                   "others": 1}  # fmt: skip
    confirmed = api(server, "/api/alias", {"from": "Biggie Smalls", "to": "The Notorious B.I.G."})
    assert confirmed["changed"] == {"review → matched_auto": 1}
    assert index.item(hypnotize)["state"] == "matched_auto"  # type: ignore[index]
    saved = json.loads(lib.paths.state_file.read_text(encoding="utf-8"))["aliases"]
    assert saved["biggie smalls"]["name"] == "The Notorious B.I.G."
    assert saved["biggie smalls"]["from"] == "Biggie Smalls"


def test_confirming_a_name_leaves_a_song_with_a_pasted_link_as_it_is(
    server: review_web.ReviewServer, index: Index
) -> None:
    """Replayed links: Bomb the World by Michael Franti & Spearhead (SpTYh-uYgQs) and
    Daddy Issues by The Neighbourhood (lqSgsq4Bn2c). Both score low against these rips.
    The page asks about the artist's name right after the first is used; saying yes
    re-checks the artist's other songs, and the other pasted link must stay first."""
    bomb = add_item(index, "Kygo - Bomb the World R", state="review", seconds=240,
                    reasons=["version_mismatch"])  # fmt: skip
    add_candidates(index, bomb, [candidate("Bom1xxxxxxx", "Bomb the World", ("Kygo",), 240)])
    daddy = add_item(index, "Kygo - Daddy Issues R", state="review", seconds=261,
                     reasons=["version_mismatch"])  # fmt: skip
    add_candidates(index, daddy, [
        candidate("Dad1xxxxxxx", "Daddy Issues", ("Kygo",), 261),
        candidate("Dad2xxxxxxx", "Daddy Issues (Live)", ("Kygo",), 300),
        candidate("Dad3xxxxxxx", "Daddy Issues", ("Kygo",), 271),
    ])  # fmt: skip
    for iid, video_id in ((bomb, "SpTYh-uYgQs"), (daddy, "lqSgsq4Bn2c")):
        pasted = api(server, "/api/decide", {"item_id": iid, "decision": "url",
                                             "link": "https://youtu.be/" + video_id})  # fmt: skip
        assert "below 0.60" in pasted["warnings"][0]
    used = api(server, "/api/decide", {"item_id": bomb, "decision": "use",
                                       "video_id": "SpTYh-uYgQs"})  # fmt: skip
    assert used["alias_offer"] == {"from": "Kygo", "to": "Michael Franti & Spearhead",
                                   "others": 1}  # fmt: skip

    confirmed = api(server, "/api/alias", {"from": "Kygo", "to": "Michael Franti & Spearhead"})
    assert (confirmed["items"], confirmed["waiting"], confirmed["changed"]) == (0, 1, {})
    listed = next(i for i in api(server, "/api/items?group=doubts")["items"] if i["id"] == daddy)
    assert [c["video_id"] for c in listed["candidates"]] == [
        "lqSgsq4Bn2c", "Dad1xxxxxxx", "Dad3xxxxxxx"]  # fmt: skip
    assert [r["code"] for r in listed["reasons"]] == ["url_low_score"]
    # The page's message after a yes mentions the ones left as they are.
    assert "data.waiting" in review_web.PAGE.read_text(encoding="utf-8")


def test_no_offer_when_the_artist_is_the_same(
    server: review_web.ReviewServer, index: Index
) -> None:
    uptown = add_item(index, "Bruno Mars - Uptown Funk", state="review", seconds=260,
                      reasons=["duration_mismatch"])  # fmt: skip
    add_candidates(index, uptown, [candidate("Upt1xxxxxxx", "Uptown Funk (feat. Bruno Mars)",
                                             ("Mark Ronson",), 270)])  # fmt: skip
    used = api(server, "/api/decide", {"item_id": uptown, "decision": "use",
                                       "video_id": "Upt1xxxxxxx"})  # fmt: skip
    assert used["alias_offer"] is None  # he's featured on it: nothing to learn
