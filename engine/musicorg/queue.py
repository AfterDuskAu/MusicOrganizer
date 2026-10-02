"""The throttled download queue (step 09a), in `.musicorg/queue.sqlite`.

Bulk downloading is what gets a home connection blocked by YouTube, so there is exactly
one queue, it survives quitting, and it goes slowly:

- One job at a time. A random 8–25 s pause between downloads; 20–40 s for the first 20
  of a session ("quiet start"). At most 300 downloads in any 24 hours. All of these are
  in config.json under "throttle" (config.THROTTLE_DEFAULTS).
- **Retries:** after 1 min, 5 min, 30 min and 2 h. When the retry after the 2 h wait
  fails too, the job ends `failed` with its last error.
- **YouTube refusing us** ("confirm you're not a bot", HTTP 429), or 3 network-level
  failures in a row (HTTP 403, timeouts, dropped connections), pauses the whole queue for
  6 hours (`paused_by_youtube`). It's never retried in a tight loop and never worked
  around. The next session starts with the quiet-start pace again.
- **One video's problem is only that job's:** age-restricted, private, removed or
  region-blocked → `needs_review` with `video_unavailable`, and the queue carries on.
  ("Sign in to confirm your age" is one video; "confirm you're not a bot" is us.)
- **Format 140 not offered** → `needs_review` with `format_140_unavailable`, after one
  retry: the research saw YouTube answer "format not available" for a video and offer it
  minutes later. The retry asks for format 140 again; there's never a fallback format.
- `queue pause` / `resume` flip a flag in queue.sqlite without the library lock; `queue
  run` checks it between jobs. A YouTube pause isn't lifted by `resume`.
- On start, jobs left `running` by a crash go back to `queued`, and their staging
  folders are discarded.

Jobs belong to an open batch (made by `apply`, step 09b) and the batch is closed when its
last job ends. What a job does depends on its kind: step 09b registers the handlers
(`register`). A handler gets a `JobContext` and returns an `Outcome`; it downloads only
through `ctx.download()`, which applies the pace and the daily cap.
"""

from __future__ import annotations

import json
import logging
import random
import signal
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from musicorg import fileops, tools, youtube
from musicorg.config import Config, load_download_times, save_download_times
from musicorg.errors import (
    ConfigError,
    DownloadError,
    FormatUnavailableError,
    MusicOrgError,
    NotFoundError,
    UserError,
    VideoUnavailableError,
    YouTubePausedError,
    YouTubeRefusedError,
)
from musicorg.index import QueueStore, open_queue
from musicorg.library import Library
from musicorg.naming import LibraryPaths

log = logging.getLogger(__name__)

JOB_STATES = ("queued", "running", "done", "failed", "needs_review", "cancelled")
QUEUE_STATES = ("running", "idle", "paused", "paused_by_youtube")

BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2))
MAX_ATTEMPTS = 1 + len(BACKOFF)  # the first try, then a retry after each wait
FORMAT_TRIES = 2  # format 140 missing: one retry first (the research's deviation)
NETWORK_FAILURES_TO_PAUSE = 3
RETRY_WAIT_LIMIT = timedelta(minutes=30)  # `queue run` waits for a retry this soon; else stops
DAY = timedelta(hours=24)

# queue_meta keys
PAUSED = "paused"  # "1" while the owner has paused the queue
YOUTUBE_PAUSED_UNTIL = "youtube_paused_until"
YOUTUBE_PAUSE_REASON = "youtube_pause_reason"
DOWNLOADS = "downloads_24h"  # JSON list of the times of downloads in the last 24 hours
NETWORK_FAILURES = "network_failures_in_a_row"


# ---- what jobs return, and the kinds of job --------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """How a job ended: `done`, or `needs_review` with a review reason (ENGINE_API.md)."""

    state: str
    reason: str | None = None
    message: str | None = None

    @classmethod
    def done(cls, message: str | None = None) -> Outcome:
        return cls("done", None, message)

    @classmethod
    def needs_review(cls, reason: str, message: str | None = None) -> Outcome:
        return cls("needs_review", reason, message)


Handler = Callable[["JobContext"], Outcome]


@dataclass(frozen=True)
class Kind:
    handler: Handler
    network: bool  # downloads: waits for its turn and counts toward the daily cap


KINDS: dict[str, Kind] = {}


def register(kind: str, handler: Handler, *, network: bool) -> None:
    """Say what jobs of `kind` do (step 09b: replace and adopt)."""
    KINDS[kind] = Kind(handler, network)


@dataclass
class Clock:
    """Time, for the queue. Tests pass a fake one."""

    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    sleep: Callable[[float], None] = field(default=time.sleep)
    uniform: Callable[[float, float], float] = field(default=random.uniform)


# How far along the download that's running is, by job id: the bytes so far and the bytes
# in all (None while YouTube hasn't said). Kept in memory by the process that runs the
# queue, for `downloads()` to report. A queue run by another process (`musicorg queue
# run` beside the app) reports none.
_progress: dict[int, tuple[int, int | None]] = {}
_progress_lock = threading.Lock()
AUDIO_BYTES_PER_S = 16_200  # format 140, measured: for guessing a video's sound in advance


class _Meter:
    """Counts a download's bytes for `_progress`. A song is one file. A video is two,
    its picture and then its sound: the second file's bytes follow on from the first's,
    and until it starts its size is guessed (`expected_extra`), so the share shown
    doesn't jump back when it does."""

    def __init__(
        self, job_id: int, forward: youtube.ProgressHook | None, expected_extra: int = 0
    ) -> None:
        self.job_id, self.forward = job_id, forward
        self.before = 0  # the bytes of the files already finished
        self.last: tuple[int, int | None] = (0, None)
        self.extra = expected_extra
        with _progress_lock:
            _progress[job_id] = (0, None)

    def __call__(self, done: int, total: int | None) -> None:
        if done < self.last[0]:  # a new file has started
            self.before += self.last[1] or self.last[0]
            self.extra = 0
        self.last = (done, total)
        whole = self.before + total + self.extra if total else None
        with _progress_lock:
            _progress[self.job_id] = (self.before + done, whole)
        if self.forward is not None:
            self.forward(done, total)

    def finished(self) -> None:
        """All of it has arrived: the job goes on to check and tag it."""
        with _progress_lock:
            done, whole = _progress.get(self.job_id, (0, None))
            _progress[self.job_id] = (max(done, whole or 0), max(done, whole or 0) or None)


def progress_of(job_id: int) -> float | None:
    """The share of a running download that has arrived (0 to 1), or None if that isn't
    known: it hasn't started, YouTube hasn't said how big it is, or another process is
    running the queue."""
    with _progress_lock:
        done, whole = _progress.get(job_id, (0, None))
    return round(min(1.0, done / whole), 3) if whole else None


class _DailyCapReached(Exception):
    def __init__(self, resume_at: datetime) -> None:
        super().__init__("daily cap")
        self.resume_at = resume_at


class JobContext:
    """What a handler gets: the library, the job's open batch, the job itself, and
    `download()`."""

    def __init__(self, runner: _Runner, batch: fileops.Batch, job: dict[str, Any]) -> None:
        self._runner = runner
        self.lib = runner.lib
        self.batch = batch
        self.job = job
        self.downloaded = 0

    @property
    def payload(self) -> dict[str, Any]:
        return self.job["payload"]

    def staging(self) -> Path:
        """This job's own empty folder in `_Staging/<batch_id>/`."""
        return fileops.stage_dir(self.batch, staging_name(self.job["id"]))

    def download(
        self, video_id: str, progress: youtube.ProgressHook | None = None
    ) -> tuple[Path, dict[str, Any]]:
        """Download a video's audio (format 140) into this job's staging folder, keeping
        to the pace and the daily cap."""
        self._runner.wait_for_download_turn()
        self._runner.count_download()
        self.downloaded += 1
        meter = _Meter(self.job["id"], progress)
        try:
            found = youtube.download_audio(video_id, self.staging(), progress=meter)
            meter.finished()
            return found
        finally:
            self._runner.downloaded()

    def download_video(
        self,
        video_id: str,
        height: int,
        fps: int | None = None,
        progress: youtube.ProgressHook | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        """Download a video (its picture at `height`, joined to its sound) into this
        job's staging folder. One download, for the pace and the daily cap, like a song."""
        self._runner.wait_for_download_turn()
        self._runner.count_download()
        self.downloaded += 1
        meter = _Meter(self.job["id"], progress, expected_extra=self._sound_bytes())
        try:
            found = youtube.download_video(
                video_id, self.staging(), height=height, fps=fps, progress=meter
            )
            meter.finished()
            return found
        finally:
            self._runner.downloaded()

    def _sound_bytes(self) -> int:
        """About how big the video's sound will be, from the length the plan recorded."""
        ops = self.payload.get("ops")
        op = ops[0] if isinstance(ops, list) and ops and isinstance(ops[0], dict) else {}
        params = op.get("params") if isinstance(op.get("params"), dict) else {}
        candidate = params.get("candidate") if isinstance(params.get("candidate"), dict) else {}
        length = candidate.get("duration_s")
        return int(length * AUDIO_BYTES_PER_S) if isinstance(length, int | float) else 0


def staging_name(job_id: int) -> str:
    return f"job-{job_id}"


# ---- running the queue -----------------------------------------------------------------


@dataclass
class RunResult:
    """Why `queue run` stopped: `empty`, `paused`, `paused_by_youtube`, `daily_cap`,
    `waiting` (only retries left, not due soon) or `stopped` (Ctrl-C)."""

    stopped: str
    message: str
    resume_at: datetime | None = None
    counts: dict[str, int] = field(default_factory=dict)  # jobs ended this run, by state

    def to_dict(self) -> dict[str, Any]:
        return {
            "stopped": self.stopped,
            "message": self.message,
            "resume_at": _iso(self.resume_at) if self.resume_at else None,
            "jobs": dict(self.counts),
        }


Report = Callable[[str], None]


def run(
    lib: Library,
    *,
    clock: Clock | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    report: Report | None = None,
    kinds: dict[str, Kind] | None = None,
    config: Config | None = None,
) -> RunResult:
    """Work through the queue until it's empty, paused, or `should_stop()` says so (checked
    between jobs, so the current job always finishes). Needs the library open for
    writing."""
    if not lib.writable:
        raise RuntimeError("queue.run needs the library open for writing")
    cfg = config if config is not None else Config.load()
    with open_queue(lib.paths, write=True) as store, tools.keep_awake():
        runner = _Runner(
            lib, store, cfg.throttle(), clock or Clock(), KINDS if kinds is None else kinds, report
        )
        runner.requeue_interrupted()
        _clean_old_staging(lib)
        return runner.loop(should_stop)


def _clean_old_staging(lib: Library) -> None:
    """Downloads no rip matched are kept in `_Staging/` for 24 hours (step 09b), then
    cleaned up here. `_Staging/calibration/` is never cleaned."""
    try:
        fileops.clean_staging(lib)
    except (OSError, MusicOrgError) as exc:
        log.warning("Couldn't clean up old files in _Staging: %s", exc)


class _Runner:
    def __init__(
        self,
        lib: Library,
        store: QueueStore,
        throttle: dict[str, int],
        clock: Clock,
        kinds: dict[str, Kind],
        report: Report | None,
    ) -> None:
        self.lib, self.store, self.throttle, self.clock = lib, store, throttle, clock
        self.kinds = kinds
        self.report = report or (lambda message: None)
        self.session_downloads = 0
        self.next_download_at: datetime | None = None
        self.counts: dict[str, int] = {}
        self.should_stop: Callable[[], bool] = lambda: False

    # -- the loop --

    def loop(self, should_stop: Callable[[], bool]) -> RunResult:
        self.should_stop = should_stop
        while True:
            if should_stop():
                return self._result("stopped", "Stopped after the current job, as asked.")
            if self.store.meta(PAUSED) == "1":
                return self._result(
                    "paused", "The queue is paused. `musicorg queue resume` starts it again."
                )
            until = self.youtube_pause()
            if until is not None:
                return self._youtube_result(until)
            now = self.clock.now()
            job = self.store.next_ready(_iso(now))
            if job is None:
                retry = _parse(self.store.next_retry_at())
                if retry is None:
                    return self._result("empty", "The queue is empty.")
                if retry - now > RETRY_WAIT_LIMIT:
                    return self._result(
                        "waiting",
                        f"The jobs left are waiting to retry; the next one can go at "
                        f"{_clock(retry)}. Run `musicorg queue run` again then.",
                        resume_at=retry,
                    )
                self.report(f"Waiting until {_clock(retry)} to retry a job.")
                self._sleep_until(retry)  # stopped or paused meanwhile: the loop says which
                continue
            kind = self.kinds.get(job["kind"])
            if kind is None:
                self._end(job, "failed", error=f"No handler for jobs of kind {job['kind']!r}.")
                continue
            if kind.network:
                cap = self._cap_reached()
                if cap is not None:
                    return self._cap_result(cap)
                if not self._wait_for_turn_between_jobs():
                    continue  # stopped or paused while waiting: the loop says which
            try:
                self._run_job(job, kind)
            except _DailyCapReached as cap:
                return self._cap_result(cap.resume_at)
            except _YouTubePause as pause:
                return self._youtube_result(pause.until)

    def _run_job(self, job: dict[str, Any], kind: Kind) -> None:
        now = self.clock.now()
        try:
            batch = fileops.resume_batch(self.lib, job["batch_id"])
        except (NotFoundError, UserError) as exc:
            self._end(job, "cancelled", error=exc.message)
            return
        attempts = job["attempts"] + 1
        self.store.update_job(job["id"], _iso(now), state="running", attempts=attempts)
        self.report(f"Job {job['id']} ({job['kind']}): started, attempt {attempts}.")
        ctx = JobContext(self, batch, job)
        try:
            try:
                outcome = kind.handler(ctx)
            finally:
                with _progress_lock:
                    _progress.pop(job["id"], None)
        except _DailyCapReached:
            self._requeue(job, attempts=job["attempts"])
            raise
        except (YouTubeRefusedError, YouTubePausedError) as exc:
            self._requeue(job, attempts=job["attempts"], error=exc.message)
            raise _YouTubePause(self.pause_for_youtube(exc.message)) from exc
        except VideoUnavailableError as exc:
            self._network_ok()
            self._end(job, "needs_review", reason="video_unavailable", error=exc.message)
        except FormatUnavailableError as exc:
            self._network_ok()
            if attempts >= FORMAT_TRIES:
                self._end(job, "needs_review", reason="format_140_unavailable", error=exc.message)
            else:
                self._retry(job, attempts, exc.message)
        except DownloadError as exc:
            if exc.network:
                failures = self._network_failed()
                self._retry(job, attempts, exc.message)
                if failures >= NETWORK_FAILURES_TO_PAUSE:
                    raise _YouTubePause(
                        self.pause_for_youtube(
                            f"{failures} downloads in a row failed on the network."
                        )
                    ) from exc
            else:
                self._network_ok()
                self._retry(job, attempts, exc.message)
        except MusicOrgError as exc:
            self._retry(job, attempts, exc.message)
        except KeyboardInterrupt:
            self._requeue(job, attempts=job["attempts"], error="Stopped by Ctrl-C.")
            raise
        except Exception as exc:
            log.exception("Job %s failed unexpectedly", job["id"])
            self._retry(job, attempts, f"Something unexpected went wrong: {exc}")
        else:
            if ctx.downloaded:
                self._network_ok()
            if outcome.state not in ("done", "needs_review"):
                raise ValueError(f"a handler returned {outcome.state!r}")
            self._end(job, outcome.state, reason=outcome.reason, error=outcome.message)

    # -- ending jobs --

    def _end(
        self,
        job: dict[str, Any],
        state: str,
        *,
        reason: str | None = None,
        error: str | None = None,
    ) -> None:
        self.store.update_job(
            job["id"], _iso(self.clock.now()), state=state, reason=reason, last_error=error,
            next_attempt_at=None,
        )  # fmt: skip
        self.counts[state] = self.counts.get(state, 0) + 1
        why = f" ({reason})" if reason else ""
        self.report(f"Job {job['id']} ({job['kind']}): {state.replace('_', ' ')}{why}.")
        self._clean(job)
        self._close_batch_if_finished(job["batch_id"])

    def _retry(self, job: dict[str, Any], attempts: int, error: str) -> None:
        if attempts >= MAX_ATTEMPTS:
            self._end(job, "failed", error=error)
            return
        at = self.clock.now() + BACKOFF[attempts - 1]
        self.store.update_job(
            job["id"], _iso(self.clock.now()), state="queued", next_attempt_at=_iso(at),
            last_error=error,
        )  # fmt: skip
        self.counts["retrying"] = self.counts.get("retrying", 0) + 1
        self.report(f"Job {job['id']} ({job['kind']}): will retry at {_clock(at)}. {error}")
        self._clean(job)

    def _requeue(self, job: dict[str, Any], *, attempts: int, error: str | None = None) -> None:
        """Back to the queue, the attempt not counted: it wasn't the job's fault."""
        self.store.update_job(
            job["id"], _iso(self.clock.now()), state="queued", attempts=attempts,
            last_error=error or job["last_error"],
        )  # fmt: skip
        self._clean(job)

    def _clean(self, job: dict[str, Any]) -> None:
        folder = self.lib.paths.staging / job["batch_id"] / staging_name(job["id"])
        if folder.exists() or folder.is_symlink():
            try:
                fileops.discard_staged(self.lib, folder)
            except (OSError, MusicOrgError) as exc:
                log.warning("Couldn't clear %s: %s", folder, exc)

    def _close_batch_if_finished(self, batch_id: str) -> None:
        if self.store.open_jobs(batch_id) == 0:
            try:
                fileops.close_batch(self.lib, batch_id)
            except NotFoundError:
                pass

    def requeue_interrupted(self) -> int:
        """Jobs a crash left `running` go back to `queued`, their staging discarded. A
        job the engine stopped during as many times as it may be tried ends `failed`, so
        a job that crashes the engine can't block the front of the queue forever (a
        lesson from the Photonizer project's crash-loop guard)."""
        stuck = self.store.jobs(state="running")
        for job in stuck:
            if job["attempts"] >= MAX_ATTEMPTS:
                self._end(
                    job,
                    "failed",
                    error=f"The engine stopped in the middle of this job {job['attempts']} "
                    "times, so it's been set aside. Make a new plan to try it again.",
                )
                log.warning("Job %s was interrupted too often; it's failed", job["id"])
                continue
            self._requeue(job, attempts=job["attempts"])
            log.info("Job %s was interrupted; it's queued again", job["id"])
        if stuck:
            self.report(f"{len(stuck)} interrupted job(s) queued again.")
        return len(stuck)

    # -- the pace and the daily cap --

    def _gap(self) -> float:
        t = self.throttle
        if self.session_downloads < t["quiet_start_downloads"]:
            return self.clock.uniform(t["quiet_start_min_s"], t["quiet_start_max_s"])
        return self.clock.uniform(t["pause_min_s"], t["pause_max_s"])

    def _wait_for_turn_between_jobs(self) -> bool:
        """Wait for the pause between downloads. False if stopped or paused meanwhile."""
        if self.next_download_at is None:
            return True
        wait = (self.next_download_at - self.clock.now()).total_seconds()
        if wait > 0:
            self.report(f"Waiting {wait:.0f} s before the next download.")
        return self._sleep_until(self.next_download_at)

    def wait_for_download_turn(self) -> None:
        """Inside a job (a second download in the same job): wait, and check the cap."""
        if self.next_download_at is not None:
            self._sleep_until(self.next_download_at, interruptible=False)
        cap = self._cap_reached()
        if cap is not None:
            raise _DailyCapReached(cap)

    def count_download(self) -> None:
        now = self.clock.now()
        times = self._recent_downloads(now) + [now]
        self.store.set_meta(DOWNLOADS, json.dumps([_iso(t) for t in times]))
        try:  # and for the other libraries on this computer
            save_download_times([_iso(t) for t in times])
        except ConfigError as exc:
            log.warning("%s The daily limit is counted for this library alone.", exc.message)
        self.session_downloads += 1

    def downloaded(self) -> None:
        self.next_download_at = self.clock.now() + timedelta(seconds=self._gap())

    def _recent_downloads(self, now: datetime) -> list[datetime]:
        return recent_downloads(self.store, now)

    def _cap_reached(self) -> datetime | None:
        """When the next download may start, if the rolling 24-hour cap is reached."""
        now = self.clock.now()
        times = self._recent_downloads(now)
        if len(times) < self.throttle["daily_cap"]:
            return None
        return times[len(times) - self.throttle["daily_cap"]] + DAY

    def _sleep_until(self, when: datetime, *, interruptible: bool = True) -> bool:
        """Sleep in steps of at most a second. With `interruptible`, returns False as soon
        as Ctrl-C or `queue pause` asks the run to stop."""
        while True:
            left = (when - self.clock.now()).total_seconds()
            if left <= 0:
                return True
            if interruptible and (self.should_stop() or self.store.meta(PAUSED) == "1"):
                return False
            self.clock.sleep(min(1.0, left))

    # -- YouTube pauses --

    def youtube_pause(self) -> datetime | None:
        """When the YouTube pause ends, if one is on; an expired one is cleared."""
        until = _parse(self.store.meta(YOUTUBE_PAUSED_UNTIL))
        if until is None:
            return None
        if self.clock.now() >= until:
            self.store.set_meta(YOUTUBE_PAUSED_UNTIL, None)
            self.store.set_meta(YOUTUBE_PAUSE_REASON, None)
            self.store.set_meta(NETWORK_FAILURES, None)
            log.info("The YouTube pause is over")
            return None
        return until

    def pause_for_youtube(self, reason: str) -> datetime:
        until = self.clock.now() + timedelta(hours=self.throttle["youtube_pause_hours"])
        self.store.set_meta(YOUTUBE_PAUSED_UNTIL, _iso(until))
        self.store.set_meta(YOUTUBE_PAUSE_REASON, reason)
        self.store.set_meta(NETWORK_FAILURES, None)
        log.warning("Queue paused by YouTube until %s: %s", _iso(until), reason)
        return until

    def _network_failed(self) -> int:
        failures = int(self.store.meta(NETWORK_FAILURES) or 0) + 1
        self.store.set_meta(NETWORK_FAILURES, str(failures))
        return failures

    def _network_ok(self) -> None:
        if self.store.meta(NETWORK_FAILURES) is not None:
            self.store.set_meta(NETWORK_FAILURES, None)

    # -- results --

    def _result(self, stopped: str, message: str, resume_at: datetime | None = None) -> RunResult:
        return RunResult(stopped, message, resume_at, dict(self.counts))

    def _youtube_result(self, until: datetime) -> RunResult:
        reason = self.store.meta(YOUTUBE_PAUSE_REASON) or "YouTube asked us to slow down."
        return self._result(
            "paused_by_youtube",
            f"YouTube is slowing us down; resuming at {_clock(until)}. {reason} Nothing is "
            "lost: run `musicorg queue run` again after that.",
            resume_at=until,
        )

    def _cap_result(self, resume_at: datetime) -> RunResult:
        return self._result(
            "daily_cap",
            f"That's {self.throttle['daily_cap']} downloads in 24 hours, the daily limit. The "
            f"next can start at {_clock(resume_at)}; run `musicorg queue run` again then.",
            resume_at=resume_at,
        )


class _YouTubePause(Exception):
    def __init__(self, until: datetime) -> None:
        super().__init__("paused by YouTube")
        self.until = until


def recent_downloads(store: QueueStore, now: datetime) -> list[datetime]:
    """Download times in the 24 hours before `now`, oldest first: this library's, and
    every other library's on this computer (another profile's). YouTube counts the
    computer, so the daily limit is one count however many people share it."""
    try:
        raw = json.loads(store.meta(DOWNLOADS) or "[]")
    except ValueError:
        raw = []

    def times(values: Any) -> Counter[datetime]:
        listed = values if isinstance(values, list) else []
        found = (_parse(v) for v in listed if isinstance(v, str))
        return Counter(t for t in found if t is not None)

    # A download is in this library's list and in the computer's: counted once. Two that
    # started at the very same moment are two.
    merged = times(raw) | times(load_download_times())
    return sorted(t for t in merged.elements() if now - t < DAY)


# ---- status, pause, resume (no lock) ---------------------------------------------------


def status(
    paths: LibraryPaths, *, now: datetime | None = None, config: Config | None = None
) -> dict[str, Any]:
    """The queue's state and counts (ENGINE_API.md → `queue.status`). Needs no lock."""
    now = now or datetime.now(UTC)
    cap = (config if config is not None else Config.load()).throttle()["daily_cap"]
    with open_queue(paths, write=False) as store:
        counts = store.counts()
        paused = store.meta(PAUSED) == "1"
        until = _parse(store.meta(YOUTUBE_PAUSED_UNTIL))
        reason = store.meta(YOUTUBE_PAUSE_REASON)
        recent = recent_downloads(store, now)
    daily = len(recent)
    # At the daily limit the next download waits for the oldest of them to be a day old.
    daily_resume = recent[daily - cap] + DAY if cap > 0 and daily >= cap else None
    youtube_paused = until is not None and now < until
    if youtube_paused:
        state = "paused_by_youtube"
    elif paused:
        state = "paused"
    elif counts.get("running"):
        state = "running"
    else:
        state = "idle"
    result: dict[str, Any] = {
        "state": state,
        "reason": reason if youtube_paused else ("paused by you" if paused else None),
        "resume_at": _iso(until) if youtube_paused and until else None,
    }
    for name in JOB_STATES:
        result[name] = counts.get(name, 0)
    result.update(daily_count=daily, daily_cap=cap)
    result["daily_resume_at"] = _iso(daily_resume) if daily_resume else None
    return result


def pause(paths: LibraryPaths) -> dict[str, Any]:
    """Pause the queue after the current job. Needs no lock."""
    with open_queue(paths, write=True) as store:
        store.set_meta(PAUSED, "1")
    return status(paths)


def resume(paths: LibraryPaths) -> dict[str, Any]:
    """Lift the owner's pause. A pause by YouTube stays until it ends by itself."""
    with open_queue(paths, write=True) as store:
        store.set_meta(PAUSED, None)
    return status(paths)


def add_jobs(lib: Library, jobs: list[dict[str, Any]]) -> list[int]:
    """Queue jobs (step 09b's `apply`): dicts with batch_id, plan_id, kind, item_id and
    payload. Needs the library open for writing."""
    if not lib.writable:
        raise RuntimeError("adding jobs needs the library open for writing")
    for job in jobs:
        if job["kind"] not in fileops.BATCH_KINDS:
            raise ValueError(f"unknown job kind {job['kind']!r}")
    with open_queue(lib.paths, write=True) as store:
        return store.add_jobs(jobs, _iso(datetime.now(UTC)))


# ---- the owner's own downloads, for the app's Downloads page (v0.2) ---------------------

UNFINISHED = ("queued", "running", "failed", "needs_review")


def downloads(paths: LibraryPaths) -> list[dict[str, Any]]:
    """The downloads the owner asked for (plan kind `download`) that haven't arrived:
    waiting, downloading, or ended without the song. Newest first. Needs no lock."""
    with open_queue(paths, write=False) as store:
        jobs = [job for job in store.jobs(kind="download") if job["state"] in UNFINISHED]
    return [_download_row(job) for job in reversed(jobs)]


def _download_row(job: dict[str, Any]) -> dict[str, Any]:
    ops = job["payload"].get("ops") if isinstance(job.get("payload"), dict) else None
    op = ops[0] if isinstance(ops, list) and ops and isinstance(ops[0], dict) else {}
    params = op.get("params") if isinstance(op.get("params"), dict) else {}
    candidate = params.get("candidate") if isinstance(params.get("candidate"), dict) else {}
    video = op.get("action") == "download_video"
    return {
        "job_id": job["id"],
        "batch_id": job["batch_id"],
        "state": job["state"],
        "reason": job["reason"],
        "message": job["last_error"],
        "video_id": params.get("video_id"),
        "title": candidate.get("title"),
        "artists": [a for a in candidate.get("artists") or [] if isinstance(a, str)],
        "video": video,
        "height": params.get("height") if video else None,
        "fps": params.get("fps") if video else None,
        "thumbnail": candidate.get("thumbnail"),
        "progress": progress_of(job["id"]) if job["state"] == "running" else None,
    }


def dismiss_download(lib: Library, job_id: int) -> None:
    """Take one of the owner's downloads off the list: one still waiting is cancelled
    before it starts, one that ended without the song is just no longer shown. One
    that's downloading right now can't be stopped. Needs the library's lock."""
    if not lib.writable:
        raise RuntimeError("dismissing a download needs the library open for writing")
    with open_queue(lib.paths, write=True) as store:
        job = store.job(job_id)
        if job is None or job["kind"] != "download" or job["state"] not in UNFINISHED:
            raise NotFoundError("That download isn't on the list any more.")
        if not store.cancel_job(
            job_id, _iso(datetime.now(UTC)), states=("queued", "failed", "needs_review")
        ):
            raise UserError("That one is downloading right now, so it can't be removed.")
        finished = store.open_jobs(job["batch_id"]) == 0
    if finished:
        try:
            fileops.close_batch(lib, job["batch_id"])
        except NotFoundError:
            pass


def dismiss_waiting(lib: Library) -> int:
    """Cancel every one of the owner's downloads that's still waiting its turn (the
    app's Cancel Waiting, after hundreds were asked for at once). The one downloading
    right now carries on, and ones that ended without the song stay on the list. Needs
    the library's lock. Returns how many were cancelled."""
    if not lib.writable:
        raise RuntimeError("dismissing downloads needs the library open for writing")
    now = _iso(datetime.now(UTC))
    cancelled = 0
    with open_queue(lib.paths, write=True) as store:
        batches: dict[str, None] = {}  # in the order met
        for job in store.jobs(kind="download", state="queued"):
            if store.cancel_job(job["id"], now, states=("queued",)):  # not started meanwhile
                cancelled += 1
                batches[job["batch_id"]] = None
        finished = [batch_id for batch_id in batches if store.open_jobs(batch_id) == 0]
    for batch_id in finished:
        try:
            fileops.close_batch(lib, batch_id)
        except NotFoundError:
            pass
    return cancelled


class BatchJobs:
    """The queue's side of `fileops.undo`: cancel a batch's queued jobs, and count its
    running ones. Used while holding the library's lock."""

    def __init__(self, lib: Library) -> None:
        self.lib = lib

    def cancel_queued(self, batch_id: str) -> int:
        with open_queue(self.lib.paths, write=True) as store:
            return store.cancel_queued(batch_id, _iso(datetime.now(UTC)))

    def running(self, batch_id: str) -> int:
        with open_queue(self.lib.paths, write=False) as store:
            return sum(1 for _ in store.jobs(state="running", batch_id=batch_id))


def requeue_interrupted(lib: Library) -> int:
    """Put jobs a crash left `running` back in the queue (holding the lock, with no queue
    running in this process)."""
    with open_queue(lib.paths, write=True) as store:
        runner = _Runner(lib, store, Config.load().throttle(), Clock(), KINDS, None)
        return runner.requeue_interrupted()


@contextmanager
def graceful_ctrl_c() -> Iterator[Callable[[], bool]]:
    """The first Ctrl-C asks `queue run` to stop after the current job; a second one
    stops at once (the job is queued again next time). Yields `should_stop`."""
    asked = threading.Event()
    if threading.current_thread() is not threading.main_thread():
        yield asked.is_set
        return

    def handler(signum: int, frame: Any) -> None:
        if asked.is_set():
            raise KeyboardInterrupt
        asked.set()
        log.warning("Ctrl-C: stopping after the current job (press it again to stop now)")

    previous = signal.signal(signal.SIGINT, handler)
    try:
        yield asked.is_set
    finally:
        signal.signal(signal.SIGINT, previous)


# ---- times -----------------------------------------------------------------------------


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _clock(when: datetime) -> str:
    local = when.astimezone()
    today = datetime.now(UTC).astimezone().date()
    time_text = local.strftime("%I:%M%p").lstrip("0").lower()
    return time_text if local.date() == today else f"{time_text} on {local:%a %d %b}"
