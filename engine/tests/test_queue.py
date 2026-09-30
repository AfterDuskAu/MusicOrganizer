"""The throttled queue, with a fake clock and a stand-in for yt-dlp: the pace and the daily
cap (surviving a restart), a YouTube block pausing everything (surviving a restart), one
video's problem staying that job's, format 140 missing, retries, a pause from another
process, a real crash mid-job, undo cancelling queued jobs, and status/pause/resume
without the lock."""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yt_dlp

from musicorg import fileops, library, queue, tools, youtube
from musicorg.config import Config
from musicorg.index import open_queue
from musicorg.library import Library
from musicorg.queue import Kind, Outcome
from musicorg.youtube import RateLimiter

START = datetime(2026, 10, 1, 2, 0, tzinfo=UTC)
ENGINE = Path(__file__).resolve().parents[1]


@dataclass
class FakeClock:
    t: datetime = START
    slept: float = 0.0

    def now(self) -> datetime:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept += seconds
        self.t += timedelta(seconds=seconds)

    def clock(self) -> queue.Clock:
        # uniform() gives the low end of each range, so the pace is exact.
        return queue.Clock(now=self.now, sleep=self.sleep, uniform=lambda a, b: a)


@dataclass
class FakeYouTube:
    """yt-dlp stand-in: `errors[video_id]` is a list of messages to fail with, in turn."""

    clock: FakeClock
    errors: dict[str, list[str]] = field(default_factory=dict)
    downloads: list[tuple[str, datetime]] = field(default_factory=list)
    formats: list[str] = field(default_factory=list)

    def make(self, opts: dict[str, Any]) -> Any:
        fake = self

        class Ydl:
            def __enter__(self) -> Ydl:
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
                video_id = url.rsplit("v=", 1)[1]
                fake.formats.append(opts["format"])
                pending = fake.errors.get(video_id)
                if pending:
                    raise yt_dlp.utils.DownloadError(pending.pop(0))
                fake.downloads.append((video_id, fake.clock.t))
                path = Path(opts["paths"]["home"]) / f"{video_id}.m4a"
                path.write_bytes(b"\0" * 100)
                return {"id": video_id, "format_id": "140", "requested_downloads": [
                    {"filepath": str(path)}]}  # fmt: skip

        return Ydl()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def yt(clock: FakeClock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeYouTube:
    fake = FakeYouTube(clock)
    monkeypatch.setattr(youtube, "_make_ydl", fake.make)
    monkeypatch.setattr(youtube, "_LIMITER", RateLimiter(0, 0, sleep=lambda s: None))
    monkeypatch.setattr(tools, "require", lambda name, config=None: tmp_path / "bin" / name)
    return fake


def download_job(ctx: queue.JobContext) -> Outcome:
    path, info = ctx.download(ctx.payload["video_id"])
    assert path.parent == ctx.lib.paths.staging / ctx.job["batch_id"] / f"job-{ctx.job['id']}"
    return Outcome.done(f"format {info['format_id']}")


KINDS = {"replace": Kind(download_job, network=True)}


def settings(**throttle: int) -> Config:
    cfg = Config(None, Path("unused-config.json"))
    cfg.data["throttle"] = throttle
    return cfg


def vid(n: int) -> str:
    return f"video{n:06d}"


def enqueue(lib: Library, count: int, kind: str = "replace") -> tuple[str, list[int]]:
    b = fileops.open_batch(lib, "replace")
    jobs = [
        {"batch_id": b.batch_id, "plan_id": "p_test", "kind": kind, "item_id": f"i_{n}",
         "payload": {"video_id": vid(n)}}
        for n in range(1, count + 1)
    ]  # fmt: skip
    return b.batch_id, queue.add_jobs(lib, jobs)


def run(lib: Library, clock: FakeClock, cfg: Config | None = None, **kw: Any) -> queue.RunResult:
    return queue.run(lib, clock=clock.clock(), kinds=kw.pop("kinds", KINDS),
                     config=cfg or settings(), **kw)  # fmt: skip


def jobs(lib: Library) -> dict[int, dict[str, Any]]:
    with open_queue(lib.paths, write=False) as store:
        return {j["id"]: j for j in store.jobs()}


def batch_ended(lib: Library, batch_id: str) -> bool:
    return fileops.read_journal(lib)[batch_id].end is not None


# ---- the happy path, the pace, the cap -------------------------------------------------


def test_empty_queue(lib: Library, clock: FakeClock) -> None:
    result = run(lib, clock)
    assert result.stopped == "empty"
    assert result.message == "The queue is empty."


def test_works_through_jobs_and_closes_the_batch(
    lib: Library, clock: FakeClock, yt: FakeYouTube
) -> None:
    batch_id, ids = enqueue(lib, 3)
    result = run(lib, clock)
    assert result.stopped == "empty"
    assert result.counts == {"done": 3}
    assert [v for v, _ in yt.downloads] == [vid(1), vid(2), vid(3)]
    assert {j["state"] for j in jobs(lib).values()} == {"done"}
    assert all(j["attempts"] == 1 for j in jobs(lib).values())
    assert batch_ended(lib, batch_id)
    # Each job's staging folder is gone once it has ended.
    assert not any((lib.paths.staging / batch_id).glob("job-*"))


def test_the_pace_between_downloads(lib: Library, clock: FakeClock, yt: FakeYouTube) -> None:
    enqueue(lib, 5)
    cfg = settings(quiet_start_downloads=2, quiet_start_min_s=20, pause_min_s=8)
    run(lib, clock, cfg)
    times = [t for _, t in yt.downloads]
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:], strict=False)]
    assert times[0] == START  # no wait before the first
    assert gaps == [20, 8, 8, 8]  # quiet start for the first two, then the usual pace


def test_default_pace() -> None:
    t = Config(None, Path("x")).throttle()
    assert (t["pause_min_s"], t["pause_max_s"], t["daily_cap"]) == (8, 25, 300)
    assert (t["quiet_start_downloads"], t["quiet_start_min_s"], t["quiet_start_max_s"]) == (
        20,
        20,
        40,
    )
    assert t["youtube_pause_hours"] == 6


def test_daily_cap_survives_a_restart(lib: Library, clock: FakeClock, yt: FakeYouTube) -> None:
    enqueue(lib, 5)
    cfg = settings(daily_cap=3)
    first = run(lib, clock, cfg)
    assert first.stopped == "daily_cap"
    assert len(yt.downloads) == 3
    assert first.resume_at == START + timedelta(hours=24)
    assert "daily limit" in first.message

    again = run(lib, clock, cfg)  # a new run: the count comes from queue.sqlite
    assert again.stopped == "daily_cap"
    assert len(yt.downloads) == 3

    clock.t = START + timedelta(hours=24)  # the first download is now 24 hours old
    later = run(lib, clock, cfg)
    assert later.stopped == "daily_cap"
    assert len(yt.downloads) == 4
    assert queue.status(lib.paths, now=clock.t, config=cfg)["daily_count"] == 3


def test_jobs_without_downloads_skip_the_pace(lib: Library, clock: FakeClock) -> None:
    enqueue(lib, 3, kind="adopt")
    kinds = {"adopt": Kind(lambda ctx: Outcome.done(), network=False)}
    result = run(lib, clock, kinds=kinds)
    assert result.counts == {"done": 3}
    assert clock.slept == 0


# ---- YouTube refusing us ---------------------------------------------------------------


def test_not_a_bot_pauses_the_whole_queue_and_survives_a_restart(
    lib: Library, clock: FakeClock, yt: FakeYouTube
) -> None:
    enqueue(lib, 3)
    yt.errors[vid(2)] = ["ERROR: [youtube] x: Sign in to confirm you’re not a bot."]
    result = run(lib, clock)
    assert result.stopped == "paused_by_youtube"
    resume_at = result.resume_at
    assert resume_at is not None and resume_at - clock.t == timedelta(hours=6)
    assert "YouTube is slowing us down" in result.message
    state = jobs(lib)
    assert state[2]["state"] == "queued" and state[2]["attempts"] == 0  # not the job's fault
    assert state[3]["state"] == "queued"

    status = queue.status(lib.paths, now=clock.t)
    assert status["state"] == "paused_by_youtube"
    assert queue.resume(lib.paths)["state"] == "paused_by_youtube"  # resume doesn't lift it

    again = run(lib, clock)
    assert again.stopped == "paused_by_youtube"
    assert len(yt.downloads) == 1

    clock.t = resume_at
    done = run(lib, clock)
    assert done.stopped == "empty"
    assert [v for v, _ in yt.downloads] == [vid(1), vid(2), vid(3)]
    # After a pause the pace starts gently again (quiet start).
    times = [t for _, t in yt.downloads[1:]]
    assert (times[1] - times[0]).total_seconds() == 20


def test_three_network_failures_in_a_row_pause_the_queue(
    lib: Library, clock: FakeClock, yt: FakeYouTube
) -> None:
    enqueue(lib, 4)
    for n in (1, 2, 3):
        yt.errors[vid(n)] = ["ERROR: unable to download video data: HTTP Error 403: Forbidden"]
    result = run(lib, clock)
    assert result.stopped == "paused_by_youtube"
    assert "3 downloads in a row failed on the network" in result.message
    state = jobs(lib)
    assert [state[n]["state"] for n in (1, 2, 3, 4)] == ["queued"] * 4
    assert [state[n]["attempts"] for n in (1, 2, 3, 4)] == [1, 1, 1, 0]
    assert state[1]["next_attempt_at"] is not None


def test_a_success_resets_the_network_failure_count(
    lib: Library, clock: FakeClock, yt: FakeYouTube
) -> None:
    enqueue(lib, 5)
    for n in (1, 2, 4, 5):
        yt.errors[vid(n)] = ["ERROR: [youtube] x: Read timed out."]
    result = run(lib, clock)
    # 1, 2 fail; 3 works; 4, 5 fail; then the retries of 1, 2, 4, 5 all work.
    assert result.stopped == "empty"
    assert result.counts == {"done": 5, "retrying": 4}


# ---- one video's problems --------------------------------------------------------------


def test_age_restricted_video_is_only_that_job(
    lib: Library, clock: FakeClock, yt: FakeYouTube
) -> None:
    batch_id, _ = enqueue(lib, 3)
    yt.errors[vid(2)] = [
        "ERROR: [youtube] x: Sign in to confirm your age. This video may be inappropriate "
        "for some users."
    ]
    result = run(lib, clock)
    assert result.stopped == "empty"
    state = jobs(lib)
    assert state[2]["state"] == "needs_review"
    assert state[2]["reason"] == "video_unavailable"
    assert "age" in state[2]["last_error"]
    assert state[1]["state"] == state[3]["state"] == "done"
    assert queue.status(lib.paths, now=clock.t)["state"] == "idle"
    assert batch_ended(lib, batch_id)


def test_format_140_missing_goes_to_review_after_one_retry(
    lib: Library, clock: FakeClock, yt: FakeYouTube
) -> None:
    enqueue(lib, 2)
    missing = "ERROR: [youtube] x: Requested format is not available. Use --list-formats"
    yt.errors[vid(1)] = [missing, missing]
    result = run(lib, clock)
    assert result.stopped == "empty"
    state = jobs(lib)
    assert state[1]["state"] == "needs_review"
    assert state[1]["reason"] == "format_140_unavailable"
    assert state[1]["attempts"] == 2
    assert state[2]["state"] == "done"
    assert yt.formats == ["140"] * 3  # never another format
    assert [v for v, _ in yt.downloads] == [vid(2)]


def test_format_140_back_on_the_retry(lib: Library, clock: FakeClock, yt: FakeYouTube) -> None:
    """The research saw "format not available" for a video that had it minutes later."""
    enqueue(lib, 1)
    yt.errors[vid(1)] = ["ERROR: [youtube] x: Requested format is not available."]
    run(lib, clock)
    assert jobs(lib)[1]["state"] == "done"
    assert [v for v, _ in yt.downloads] == [vid(1)]


def test_retries_then_failed(lib: Library, clock: FakeClock, yt: FakeYouTube) -> None:
    enqueue(lib, 1)
    yt.errors[vid(1)] = ["ERROR: something odd"] * 5
    first = run(lib, clock)
    # Retries after 1, 5 and 30 minutes are waited for; the 2-hour one isn't.
    assert first.stopped == "waiting"
    assert jobs(lib)[1]["attempts"] == 4
    assert first.resume_at is not None
    assert first.resume_at - clock.t == timedelta(hours=2)

    clock.t = first.resume_at
    second = run(lib, clock)
    assert second.stopped == "empty"
    job = jobs(lib)[1]
    assert job["state"] == "failed"
    assert job["attempts"] == 5
    assert "something odd" in job["last_error"]


def test_unknown_kind_fails_at_once(lib: Library, clock: FakeClock) -> None:
    enqueue(lib, 1, kind="lyrics")
    result = run(lib, clock, kinds={})
    assert result.counts == {"failed": 1}
    assert "No handler" in jobs(lib)[1]["last_error"]


def test_a_job_whose_batch_ended_is_cancelled(
    lib: Library, clock: FakeClock, yt: FakeYouTube
) -> None:
    batch_id, _ = enqueue(lib, 1)
    fileops.close_batch(lib, batch_id)
    run(lib, clock)
    assert jobs(lib)[1]["state"] == "cancelled"
    assert yt.downloads == []


# ---- pausing, stopping, crashing -------------------------------------------------------


def test_pause_from_another_process_stops_after_the_current_job(
    lib: Library, clock: FakeClock, yt: FakeYouTube
) -> None:
    enqueue(lib, 3)

    def pause_meanwhile(ctx: queue.JobContext) -> Outcome:
        # A second process, without the lock (this one holds it).
        done = subprocess.run(
            [sys.executable, "-m", "musicorg", "--library", str(lib.root), "queue", "pause"],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )  # fmt: skip
        assert done.returncode == 0, done.stderr
        return download_job(ctx)

    result = run(lib, clock, kinds={"replace": Kind(pause_meanwhile, network=True)})
    assert result.stopped == "paused"
    assert [j["state"] for j in jobs(lib).values()] == ["done", "queued", "queued"]
    assert queue.status(lib.paths)["state"] == "paused"

    queue.resume(lib.paths)
    assert run(lib, clock).stopped == "empty"


def test_should_stop_is_checked_between_jobs(
    lib: Library, clock: FakeClock, yt: FakeYouTube
) -> None:
    enqueue(lib, 3)
    asked = []

    def stop_after_first(ctx: queue.JobContext) -> Outcome:
        asked.append(1)
        return download_job(ctx)

    result = run(lib, clock, kinds={"replace": Kind(stop_after_first, network=True)},
                 should_stop=lambda: bool(asked))  # fmt: skip
    assert result.stopped == "stopped"
    assert [j["state"] for j in jobs(lib).values()] == ["done", "queued", "queued"]


def test_a_crash_mid_job_requeues_it_and_discards_its_staging(tmp_path: Path) -> None:
    root = tmp_path / "Library"
    library.init(root)
    with library.open(root, write=True, command="test") as lib:
        batch_id, _ = enqueue(lib, 2)
    crash = tmp_path / "crash.py"
    crash.write_text(
        textwrap.dedent(
            """
            import os, sys
            from pathlib import Path
            from musicorg import library, queue

            def crash(ctx):
                folder = ctx.staging()
                (folder / "abc.m4a.part").write_bytes(b"half a download")
                os._exit(9)

            queue.register("replace", crash, network=True)
            with library.open(Path(sys.argv[1]), write=True, command="crash") as lib:
                queue.run(lib)
            """
        ),
        encoding="utf-8",
    )
    child = subprocess.run(
        [sys.executable, str(crash), str(root)], capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONPATH": str(ENGINE)},
    )  # fmt: skip
    assert child.returncode == 9, child.stderr
    leftover = root / "_Staging" / batch_id / "job-1" / "abc.m4a.part"
    assert leftover.exists()

    seen: list[list[Path]] = []

    def check(ctx: queue.JobContext) -> Outcome:
        seen.append(list(ctx.staging().iterdir()))
        return Outcome.done()

    reports: list[str] = []
    with library.open(root, write=True, command="test") as lib:
        assert jobs(lib)[1]["state"] == "running"
        result = queue.run(
            lib,
            clock=FakeClock().clock(),
            config=settings(),
            kinds={"replace": Kind(check, network=True)},
            report=reports.append,
        )
        assert result.counts == {"done": 2}
        assert jobs(lib)[1]["attempts"] == 2  # the crashed attempt counts
    assert not leftover.exists()
    assert seen == [[], []]
    assert any("interrupted" in r for r in reports)


# ---- undo, status, the CLI -------------------------------------------------------------


def test_undo_cancels_the_batch_s_queued_jobs(lib: Library) -> None:
    batch_id, _ = enqueue(lib, 2)
    result = fileops.undo(lib, batch_id, jobs=queue.BatchJobs(lib))
    assert result.cancelled_jobs == 2
    assert {j["state"] for j in jobs(lib).values()} == {"cancelled"}
    assert batch_ended(lib, batch_id)


def test_status_of_a_library_with_no_queue_yet(lib: Library) -> None:
    status = queue.status(lib.paths)
    assert status["state"] == "idle"
    assert status["queued"] == 0 and status["daily_count"] == 0
    assert set(status) >= {"state", "reason", "resume_at", "queued", "running", "done",
                           "failed", "needs_review", "daily_count", "daily_cap"}  # fmt: skip


def test_cli_queue_commands(lib: Library, capsys: pytest.CaptureFixture[str]) -> None:
    from musicorg import cli

    enqueue(lib, 2)
    root = str(lib.root)
    assert cli.main(["--library", root, "queue", "status"]) == 0
    out = capsys.readouterr().out
    assert "Queue: idle." in out and "2 queued" in out and "0 of 300" in out

    assert cli.main(["--library", root, "queue", "pause"]) == 0
    assert "Queue: paused (by you)." in capsys.readouterr().out
    assert cli.main(["--library", root, "queue", "resume", "--json"]) == 0
    assert '"state": "idle"' in capsys.readouterr().out

    lib.close()
    with open_queue(lib.paths, write=True) as store:
        store.set_meta(queue.YOUTUBE_PAUSED_UNTIL, "2999-01-01T00:00:00.000000Z")
    assert cli.main(["--library", root, "queue", "run"]) == 4
    captured = capsys.readouterr()
    assert "YouTube is slowing us down" in captured.out


# ---- the crash-loop guard and keeping awake (ideas from the Photonizer project) ---------


def test_a_job_that_keeps_stopping_the_engine_is_set_aside(
    lib: Library, clock: FakeClock, yt: FakeYouTube
) -> None:
    batch_id, (first, second) = enqueue(lib, 2)
    now = queue._iso(clock.t)
    with open_queue(lib.paths, write=True) as store:
        # The engine died during job 1 on every one of its tries, and once during job 2.
        store.update_job(first, now, state="running", attempts=queue.MAX_ATTEMPTS)
        store.update_job(second, now, state="running", attempts=1)

    run(lib, clock)

    found = jobs(lib)
    assert found[first]["state"] == "failed"
    assert "stopped in the middle of this job 5 times" in found[first]["last_error"]
    assert found[second]["state"] == "done"
    assert [v for v, _ in yt.downloads] == [vid(2)]


def test_the_queue_keeps_the_computer_awake(
    lib: Library, clock: FakeClock, yt: FakeYouTube, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    @contextlib.contextmanager
    def awake() -> Iterator[None]:
        events.append("awake")
        yield
        events.append("may sleep")

    monkeypatch.setattr(tools, "keep_awake", awake)
    enqueue(lib, 1)
    run(lib, clock)
    assert events == ["awake", "may sleep"]
