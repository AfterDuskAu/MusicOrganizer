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
