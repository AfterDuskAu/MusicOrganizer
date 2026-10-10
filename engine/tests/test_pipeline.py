"""Replace and adopt (step 09b), end to end through the queue, with a stand-in for yt-dlp
that "downloads" the step 02 audio: a full replace, a fingerprint mismatch, two rips of
one video, a video already in the library, adopts, a rip changed after the plan, undo,
and calibration mode."""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import AudioFixtures, require_tool
from index_support import add_candidates, add_item, add_source, candidate

from musicorg import (
    artwork,
    browse,
    cli,
    discover,
    fileops,
    library,
    listening,
    lyrics,
    match,
    naming,
    pipeline,
    queue,
    review,
    scan,
    state,
    tags,
    youtube,
)
from musicorg.config import Config
from musicorg.errors import (
    FileOperationError,
    NotFoundError,
    OutsideLibraryError,
    PlanOutOfDateError,
    UndoError,
    UserError,
)
from musicorg.index import Index, open_index, open_queue
from musicorg.library import Library
from musicorg.youtube import Album, AlbumTrack

VIDEO_A = "melodyAAAAA"
VIDEO_B = "melodyBBBBB"
SECONDS = 20  # the step 02 melodies


@dataclass
class FakeDownloads:
    """youtube.download_audio's stand-in: copies a fixture into the job's folder."""

    files: dict[str, Path]
    calls: list[str] = field(default_factory=list)
    format_id: str = "140"

    def __call__(
        self, video_id: str, dest: Path, *, progress: Any = None
    ) -> tuple[Path, dict[str, Any]]:
        self.calls.append(video_id)
        target = Path(dest) / f"{video_id}.m4a"
        shutil.copyfile(self.files[video_id], target)
        return target, {"id": video_id, "format_id": self.format_id}


@pytest.fixture
def fpcalc() -> Path:
    return require_tool("fpcalc")


@pytest.fixture
def rips(tmp_path: Path) -> Path:
    folder = tmp_path / "rips"
    folder.mkdir()
    return folder


@pytest.fixture
def index(lib: Library, rips: Path) -> Iterator[Index]:
    with open_index(lib.paths, write=True) as opened:
        add_source(opened, rips)
        yield opened


@pytest.fixture
def downloads(monkeypatch: pytest.MonkeyPatch, audio: AudioFixtures, fpcalc: Path) -> FakeDownloads:
    fake = FakeDownloads({VIDEO_A: audio.melody_a_m4a, VIDEO_B: audio.melody_b_m4a})
    monkeypatch.setattr(youtube, "download_audio", fake)
    album = Album(
        browse_id="MPREb_1", title="Tunes", artists=("Band",), year="2020", track_count=9,
        is_explicit=False,
        tracks=(AlbumTrack(VIDEO_A, "Melody", 3, SECONDS, False, None),
                AlbumTrack(VIDEO_B, "Melody", 4, SECONDS, False, None)),
    )  # fmt: skip
    monkeypatch.setattr(youtube, "get_album", lambda browse_id, **kw: album)
    return fake


def rip(
    index: Index,
    rips: Path,
    source: Path,
    name: str = "Band - Melody",
    *,
    state_: str = "matched_auto",
    video_id: str = VIDEO_A,
) -> str:
    """A rip file (a copy of `source`) indexed as matched to `video_id`."""
    shutil.copyfile(source, rips / f"{name}{source.suffix}")
    iid = add_item(index, name, state=state_, seconds=SECONDS, ext=source.suffix)
    add_candidates(index, iid, [candidate(video_id, "Melody", ("Band",), SECONDS, album="Tunes")])
    return iid


def settings() -> Config:
    cfg = Config(None, Path("unused-config.json"))
    cfg.data["throttle"] = {"pause_min_s": 0, "pause_max_s": 0, "quiet_start_min_s": 0,
                            "quiet_start_max_s": 0}  # fmt: skip
    return cfg


def run_queue(lib: Library) -> queue.RunResult:
    """Run the queue with a clock that moves on when the queue waits, so a job waiting
    to retry never makes a test spin until real time catches up."""
    now = [datetime.now(UTC)]

    def sleep(seconds: float) -> None:
        now[0] += timedelta(seconds=seconds)

    clock = queue.Clock(now=lambda: now[0], sleep=sleep, uniform=lambda a, b: a)
    return queue.run(lib, clock=clock, config=settings())


def plan_and_run(lib: Library, index: Index, **kw: Any) -> tuple[fileops.Plan, str]:
    plan = pipeline.plan_replace(lib, index, **kw)
    applied = pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    return plan, applied.batch_id


def music_files(lib: Library) -> list[str]:
    return sorted(
        p.relative_to(lib.paths.music).as_posix() for p in lib.paths.music.rglob("*") if p.is_file()
    )


def jobs(lib: Library) -> list[dict[str, Any]]:
    with open_queue(lib.paths, write=False) as store:
        return store.jobs()


def item_state(index: Index, iid: str) -> tuple[str, list[str]]:
    item = index.item(iid)
    assert item is not None
    return item["state"], item["reasons_json"]


# ---- replace -------------------------------------------------------------------------


def test_a_full_replace(
    lib: Library,
    index: Index,
    rips: Path,
    audio: AudioFixtures,
    downloads: FakeDownloads,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    iid = rip(index, rips, audio.melody_a_mp3)
    before = (rips / "Band - Melody.mp3").read_bytes()
    seen: list[str] = []

    def extras(query: pipeline.ExtrasQuery) -> pipeline.Extras:
        seen.append(query.candidate.video_id)
        return pipeline.Extras(lyrics="la la", synced_lyrics="[00:01.00]la la\n")

    monkeypatch.setattr(pipeline, "EXTRAS", [extras])
    # Something already has the file's name, so the commit is " (2)" and the sidecar
    # must follow the committed name.
    taken = lib.paths.music / "Band" / "Tunes (2020)" / "03 Melody.m4a"
    taken.parent.mkdir(parents=True)
    taken.write_bytes(b"not ours")

    plan, batch_id = plan_and_run(lib, index)

    assert plan.summary["downloads"] == 1
    assert downloads.calls == [VIDEO_A]
    assert seen == [VIDEO_A]
    assert music_files(lib) == [
        "Band/Tunes (2020)/03 Melody (2).lrc",
        "Band/Tunes (2020)/03 Melody (2).m4a",
        "Band/Tunes (2020)/03 Melody.m4a",
    ]
    new = lib.paths.music / "Band" / "Tunes (2020)" / "03 Melody (2).m4a"
    written = tags.read_tags(new)
    assert (written.title, written.artist, written.album_artist, written.album) == (
        "Melody", "Band", "Band", "Tunes",
    )  # fmt: skip
    assert (written.year, written.track, written.track_total) == (2020, 3, 9)
    assert written.lyrics == "la la"
    assert (written.source, written.source_id, written.source_format) == (
        "youtube_music", VIDEO_A, "140",
    )  # fmt: skip
    assert isinstance(written.source_bitrate, int) and written.source_bitrate >= 100
    assert written.match == "auto_exact"
    assert written.origin_path == str(rips / "Band - Melody.mp3")
    assert isinstance(written.musicorg_id, str) and written.acquired
    assert written.schema == tags.SCHEMA_VERSION

    assert item_state(index, iid) == ("superseded", [])
    links = state.superseded(lib.load_state().data)
    assert links == {state.normalise_path(rips / "Band - Melody.mp3"): written.musicorg_id}
    assert state.gate(lib.load_state().data)[iid][VIDEO_A]["verdict"] == "match"
    assert (rips / "Band - Melody.mp3").read_bytes() == before  # the rip is untouched
    assert [j["state"] for j in jobs(lib)] == ["done"]
    assert fileops.read_journal(lib)[batch_id].end is not None  # closed with its last job
    assert [t["source_id"] for t in index.library_tracks()] == [VIDEO_A]


def test_a_fingerprint_mismatch_goes_back_to_review(
    lib: Library, index: Index, rips: Path, audio: AudioFixtures, downloads: FakeDownloads
) -> None:
    iid = rip(index, rips, audio.melody_a_mp3, video_id=VIDEO_B)

    _, batch_id = plan_and_run(lib, index)

    assert item_state(index, iid) == ("review", ["fingerprint_mismatch"])
    assert music_files(lib) == []
    assert state.gate(lib.load_state().data)[iid][VIDEO_B]["verdict"] == "different"
    assert state.superseded(lib.load_state().data) == {}
    (job,) = jobs(lib)
    assert (job["state"], job["reason"]) == ("needs_review", "fingerprint_mismatch")
    kept = lib.paths.staging / batch_id / pipeline.KEPT_DIR / f"{VIDEO_B}.m4a"
    assert kept.is_file()  # kept for 24 hours

    # It's never planned again, and the matcher never proposes that video for this rip.
    again = pipeline.plan_replace(lib, index)
    assert again.operations == []
    assert state.turned_down(lib.load_state().data)[iid] == {VIDEO_B}
    with pytest.raises(UserError, match="nothing to do"):
        pipeline.apply(lib, index, again.plan_id)


def test_an_uncertain_verdict_never_goes_auto_again(lib: Library, index: Index) -> None:
    iid = add_item(index, "Band - Melody", state="review", reasons=["duration_mismatch"])
    add_candidates(index, iid, [candidate(VIDEO_A, "Melody", ("Band",), 200)])
    assert match.recheck(lib, index).changed == {"review → matched_auto": 1}
    # The gate found the download uncertain and put the rip back in review; a recheck
    # must not make it AUTO again.
    with state.edit(lib.paths.state_file) as st:
        st.data["gate"] = {iid: {VIDEO_A: {"verdict": "uncertain", "ber": 0.2}}}
    index.set_state(iid, "review", ["fingerprint_uncertain"])
    assert match.recheck(lib, index).changed == {}
    assert item_state(index, iid) == ("review", ["fingerprint_uncertain"])
    rows = review.export(lib, index, lib.paths.reports / "r.csv")
    with open(rows.path, encoding="utf-8-sig", newline="") as f:
        (row,) = list(csv.DictReader(f))
    assert row["fingerprint"] == "uncertain (BER 0.20)"


def test_two_rips_of_one_video_share_one_download(
    lib: Library, index: Index, rips: Path, audio: AudioFixtures, downloads: FakeDownloads
) -> None:
    first = rip(index, rips, audio.melody_a_mp3, "Band - Melody")
    second = rip(index, rips, audio.melody_a_m4a, "Band - Melody (copy)", state_="matched_user")

    plan, _ = plan_and_run(lib, index)

    assert (plan.summary["operations"], plan.summary["downloads"]) == (2, 1)
    assert downloads.calls == [VIDEO_A]
    assert [f for f in music_files(lib) if f.endswith(".m4a")] == [
        "Band/Tunes (2020)/03 Melody.m4a"
    ]
    links = state.superseded(lib.load_state().data)
    assert len(links) == 2 and len(set(links.values())) == 1
    assert item_state(index, first)[0] == item_state(index, second)[0] == "superseded"

    # A third rip of the same video: already in the library, so linked without a download.
    third = rip(index, rips, audio.melody_a_mp3, "Band - Melody (again)")
    plan, _ = plan_and_run(lib, index)
    assert (plan.summary["downloads"], plan.summary["in_library"]) == (0, 1)
    assert downloads.calls == [VIDEO_A]
    assert item_state(index, third)[0] == "superseded"
    assert len(set(state.superseded(lib.load_state().data).values())) == 1


def test_a_download_of_the_wrong_length_goes_to_review(
    lib: Library, index: Index, rips: Path, audio: AudioFixtures, downloads: FakeDownloads
) -> None:
    shutil.copyfile(audio.melody_a_mp3, rips / "Band - Melody.mp3")
    iid = add_item(index, "Band - Melody", state="matched_auto", seconds=SECONDS)
    add_candidates(index, iid, [candidate(VIDEO_A, "Melody", ("Band",), SECONDS + 30)])

    plan_and_run(lib, index)

    assert item_state(index, iid) == ("review", ["duration_mismatch"])
    assert music_files(lib) == []


def test_a_rip_changed_after_the_plan(
    lib: Library, index: Index, rips: Path, audio: AudioFixtures, downloads: FakeDownloads
) -> None:
    iid = rip(index, rips, audio.melody_a_mp3)
    plan = pipeline.plan_replace(lib, index)
    pipeline.apply(lib, index, plan.plan_id)
    with open(rips / "Band - Melody.mp3", "ab") as f:
        f.write(b"changed")

    run_queue(lib)

    (job,) = jobs(lib)
    assert (job["state"], job["reason"]) == ("needs_review", "file_changed")
    assert item_state(index, iid) == ("review", ["file_changed"])
    assert downloads.calls == []
    assert music_files(lib) == []


def test_apply_refuses_a_plan_twice_or_out_of_date(
    lib: Library, index: Index, rips: Path, audio: AudioFixtures, downloads: FakeDownloads
) -> None:
    iid = rip(index, rips, audio.melody_a_mp3)
    stale = pipeline.plan_replace(lib, index)
    index.set_state(iid, "review", ["duration_mismatch"])
    with pytest.raises(PlanOutOfDateError, match="out of date"):
        pipeline.apply(lib, index, stale.plan_id)
    assert jobs(lib) == []

    index.set_state(iid, "matched_auto", [])
    plan = pipeline.plan_replace(lib, index)
    pipeline.apply(lib, index, plan.plan_id)
    with pytest.raises(PlanOutOfDateError, match="applied already"):
        pipeline.apply(lib, index, plan.plan_id)
    # A queued item isn't planned again.
    assert pipeline.plan_replace(lib, index).summary["skipped"] == {"already_queued": 1}


def test_undo_of_a_replace(
    lib: Library, index: Index, rips: Path, audio: AudioFixtures, downloads: FakeDownloads
) -> None:
    iid = rip(index, rips, audio.melody_a_mp3)
    _, batch_id = plan_and_run(lib, index)
    assert item_state(index, iid)[0] == "superseded"

    pipeline.undo(lib, batch_id)

    assert music_files(lib) == []
    assert item_state(index, iid) == ("matched_auto", [])
    assert state.superseded(lib.load_state().data) == {}
    assert index.library_tracks() == []
    replaced = sorted(p.name for p in lib.paths.replaced.rglob("*") if p.is_file())
    assert replaced == ["03 Melody.m4a"]
    # The item can be planned again.
    assert pipeline.plan_replace(lib, index).summary["downloads"] == 1


def test_stage_only_commits_nothing(
    lib: Library, index: Index, rips: Path, audio: AudioFixtures, downloads: FakeDownloads
) -> None:
    iid = rip(index, rips, audio.melody_a_mp3)

    plan, batch_id = plan_and_run(lib, index, only="auto", limit=25, stage_only=True)

    assert plan.summary["stage_only"] is True
    assert music_files(lib) == []
    assert item_state(index, iid) == ("matched_auto", [])
    assert state.gate(lib.load_state().data) == {}
    kept = lib.paths.calibration / batch_id / f"{VIDEO_A}.m4a"
    assert kept.is_file()
    note = json.loads(kept.with_name(kept.name + ".json").read_text(encoding="utf-8"))
    assert note["comparisons"][0]["verdict"] == "match"
    fileops.clean_staging(lib, older_than_hours=0)
    assert kept.is_file()  # calibration downloads are never cleaned

    out = lib.paths.root.parent / "out"
    out.mkdir()
    pairs = pipeline.calibration_pairs(lib, index, out)
    with open(pairs, encoding="utf-8", newline="") as f:
        (row,) = list(csv.DictReader(f))
    assert (row["a_path"], row["b_path"], row["same"], row["verdict"]) == (
        str(rips / "Band - Melody.mp3"), str(kept), "", "match",
    )  # fmt: skip


def test_only_chooses_which_matches(
    lib: Library, index: Index, rips: Path, audio: AudioFixtures, downloads: FakeDownloads
) -> None:
    rip(index, rips, audio.melody_a_mp3, "Band - One")
    rip(index, rips, audio.melody_a_mp3, "Band - Two", state_="matched_user", video_id=VIDEO_B)
    assert pipeline.plan_replace(lib, index, only="auto").summary["operations"] == 1
    assert pipeline.plan_replace(lib, index, only="accepted").summary["operations"] == 1
    assert pipeline.plan_replace(lib, index).summary["operations"] == 2
    assert pipeline.plan_replace(lib, index, limit=1).summary["skipped"] == {"over_limit": 1}


# ---- adopt ---------------------------------------------------------------------------


def adopt_item(
    index: Index,
    rips: Path,
    source: Path,
    name: str,
    *,
    confidence: float | None = None,
    own: tags.TrackTags | None = None,
) -> str:
    target = rips / f"{name}{source.suffix}"
    shutil.copyfile(source, target)
    if own is not None:
        tags.write_tags(target, own)
    iid = add_item(index, name, state="only_copy", seconds=3, ext=source.suffix)
    item = index.item(iid)
    assert item is not None
    item["raw_tags_json"] = {"tags": tags.to_record(tags.read_tags(target))}
    if confidence is not None:
        item["parse_confidence"] = confidence
    index.put_items([item])
    return iid


def test_adopt_with_a_trusted_name(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    iid = adopt_item(index, rips, samples["mp3"], "Band - Rare Song", confidence=0.95)
    before = (rips / "Band - Rare Song.mp3").read_bytes()

    plan = pipeline.plan_adopt(lib, index)
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)

    assert music_files(lib) == ["Band/Unsorted/Rare Song.mp3"]
    written = tags.read_tags(lib.paths.music / "Band" / "Unsorted" / "Rare Song.mp3")
    assert (written.artist, written.title) == ("Band", "Rare Song")
    assert (written.source, written.only_copy, written.source_format) == ("rip_copy", True, "mp3")
    assert written.origin_path == str(rips / "Band - Rare Song.mp3")
    assert written.match is None
    assert item_state(index, iid) == ("adopted", [])
    assert (rips / "Band - Rare Song.mp3").read_bytes() == before


def test_a_low_confidence_adopt_keeps_the_rips_own_names(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    own = tags.TrackTags(title="Real Title", artist="Real Artist")
    adopt_item(index, rips, samples["mp3"], "zz_unclear_42", confidence=0.3, own=own)
    before = (rips / "zz_unclear_42.mp3").read_bytes()

    plan = pipeline.plan_adopt(lib, index)
    assert plan.summary["low_confidence_adopts"] == 1
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)

    (path,) = music_files(lib)
    written = tags.read_tags(lib.paths.music / path)
    assert (written.artist, written.title) == ("Real Artist", "Real Title")
    assert written.only_copy is True
    assert (rips / "zz_unclear_42.mp3").read_bytes() == before


def test_the_owners_fixes_are_used(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    iid = adopt_item(index, rips, samples["m4a"], "zz_unclear_43", confidence=0.3)
    with state.edit(lib.paths.state_file) as st:
        st.data["decisions"] = {iid: {"decision": "only_copy", "artist_fix": "Fixed Band",
                                      "title_fix": "Fixed Song"}}  # fmt: skip
    plan = pipeline.plan_adopt(lib, index)
    assert plan.summary["low_confidence_adopts"] == 0
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)

    assert music_files(lib) == ["Fixed Band/Unsorted/Fixed Song.m4a"]
    written = tags.read_tags(lib.paths.music / "Fixed Band" / "Unsorted" / "Fixed Song.m4a")
    assert written.match == "manual"


def test_a_webm_is_not_adopted(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    iid = adopt_item(index, rips, samples["webm"], "Band - Video Rip")

    plan = pipeline.plan_adopt(lib, index)
    assert plan.summary["unsupported_format"] == 1
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)

    assert item_state(index, iid) == ("unsupported_format", [])
    assert music_files(lib) == []


def test_two_adopts_with_one_name_both_land(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    (rips / "a").mkdir()
    shutil.copyfile(samples["mp3"], rips / "a" / "Band - Song.mp3")
    first = add_item(index, "a/Band - Song", state="only_copy", seconds=3)
    second = adopt_item(index, rips, samples["mp3"], "Band - Song", confidence=0.95)
    item = index.item(first)
    assert item is not None
    index.put_items([{**item, "parsed_artist": "Band", "parsed_title": "Song",
                      "parse_confidence": 0.95}])  # fmt: skip

    plan = pipeline.plan_adopt(lib, index)
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)

    assert music_files(lib) == ["Band/Unsorted/Song (2).mp3", "Band/Unsorted/Song.mp3"]
    assert item_state(index, first)[0] == item_state(index, second)[0] == "adopted"


def test_undo_of_an_adopt(lib: Library, index: Index, rips: Path, samples: dict[str, Path]) -> None:
    iid = adopt_item(index, rips, samples["mp3"], "Band - Rare Song", confidence=0.95)
    plan = pipeline.plan_adopt(lib, index)
    batch_id = pipeline.apply(lib, index, plan.plan_id).batch_id
    run_queue(lib)

    pipeline.undo(lib, batch_id)

    assert music_files(lib) == []
    assert item_state(index, iid) == ("only_copy", [])
    assert index.library_tracks() == []


def test_the_plan_summary_estimates_time(lib: Library) -> None:
    cfg = Config(None, Path("unused-config.json"))
    # 20 quiet-start downloads at 30 s, 10 more at 16.5 s, plus 10 s of work each: 1,065 s.
    assert pipeline.estimate_minutes(30, 0, cfg) == 18
    assert pipeline.download_days(250, cfg) == 1
    assert pipeline.download_days(500, cfg) == 2
    assert pipeline.download_days(0, cfg) == 0


# ---- the commands --------------------------------------------------------------------


def test_the_commands(
    tmp_path: Path, rips: Path, samples: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "Library"
    library.init(root)
    with library.open(root, write=True) as opened, open_index(opened.paths, write=True) as ix:
        add_source(ix, rips)
        adopt_item(ix, rips, samples["mp3"], "Band - Rare Song", confidence=0.95)

    def run(*args: str) -> str:
        code = cli.main(["--library", str(root), *args])
        out, err = capsys.readouterr()
        assert code == 0, err
        return out

    plan_id = json.loads(run("--json", "plan", "adopt"))["plan_id"]
    shown = run("plan", "show", plan_id)
    assert "adopt    Band - Rare Song.mp3  →  Music/Band/Unsorted/Rare Song.mp3" in shown
    assert "1 rip(s) to copy in" in shown
    batch_id = json.loads(run("--json", "apply", plan_id))["batch_id"]
    assert "Jobs this run: 1 done." in run("queue", "run")
    assert (root / "Music" / "Band" / "Unsorted" / "Rare Song.mp3").is_file()
    undone = run("undo", batch_id)
    assert "Skipped" not in undone  # the tag write in staging isn't shown
    assert "Undid 1 change." in undone
    assert not (root / "Music" / "Band" / "Unsorted" / "Rare Song.mp3").exists()


# ---- keep your own audio (step 09c) --------------------------------------------------


@pytest.fixture
def album_lookups(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """YouTube Music's album answer, counted; and any download fails the test."""
    calls: list[tuple[str, str]] = []
    raw = {
        "title": "Tunes", "artists": [{"name": "Band", "id": "UC1"}], "year": "2020",
        "trackCount": 9, "isExplicit": False, "thumbnails": [], "description": "long text",
        "tracks": [
            {"videoId": VIDEO_A, "title": "Melody", "trackNumber": 3, "duration_seconds": 20},
            {"videoId": VIDEO_B, "title": "Other Melody", "trackNumber": 4,
             "duration_seconds": 20},
        ],
    }  # fmt: skip

    def fetch(kind: str, key: str, live: Any) -> Any:
        calls.append((kind, key))
        return raw

    def no_download(*args: Any, **kw: Any) -> Any:
        raise AssertionError("step 09c never downloads")

    monkeypatch.setattr(youtube, "_fetch", fetch)
    monkeypatch.setattr(youtube, "download_audio", no_download)
    return calls


def own_rip(
    index: Index,
    rips: Path,
    source: Path,
    name: str,
    *,
    video_id: str = VIDEO_A,
    title: str = "Melody",
    state_: str = "matched_auto",
    own: tags.TrackTags | None = None,
    kbps: int = 128,
) -> str:
    target = rips / f"{name}{source.suffix}"
    shutil.copyfile(source, target)
    if own is not None:
        tags.write_tags(target, own)
    iid = add_item(index, name, state=state_, seconds=3, ext=source.suffix, kbps=kbps)
    add_candidates(index, iid, [candidate(video_id, title, ("Band",), 3, album="Tunes")])
    return iid


def test_keep_your_own_audio(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    album_lookups: list[tuple[str, str]],
) -> None:
    own = tags.TrackTags(title="melody (official video)", artist="band", track=7,
                         disc=1, disc_total=2, genre="Rock")  # fmt: skip
    auto = own_rip(index, rips, samples["mp3"], "Band - Melody", own=own)
    chosen = own_rip(index, rips, samples["m4a"], "Band - Other", video_id=VIDEO_B,
                     title="Other Melody", state_="matched_user")  # fmt: skip
    before = (rips / "Band - Melody.mp3").read_bytes()

    plan = pipeline.plan_adopt(lib, index, matched=True)
    assert (plan.summary["with_details"], plan.summary["adopts"], plan.summary["downloads"]) == (
        2, 0, 0,
    )  # fmt: skip
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)

    assert music_files(lib) == ["Band/Tunes (2020)/03 Melody.mp3",
                                "Band/Tunes (2020)/04 Other Melody.m4a"]  # fmt: skip
    written = tags.read_tags(lib.paths.music / "Band" / "Tunes (2020)" / "03 Melody.mp3")
    assert (written.title, written.artist, written.album_artist, written.album) == (
        "Melody", "Band", "Band", "Tunes",
    )  # fmt: skip
    assert (written.year, written.track, written.track_total) == (2020, 3, 9)
    assert (written.disc, written.disc_total) == (None, None)  # the rip's disc was another CD's
    assert written.genre == "Rock"  # what YouTube Music doesn't give is kept
    assert (written.source, written.source_id, written.source_format) == (
        "rip_copy", VIDEO_A, "mp3",
    )  # fmt: skip
    assert written.match == "auto_details" and written.only_copy is None
    assert written.origin_path == str(rips / "Band - Melody.mp3")
    other = tags.read_tags(lib.paths.music / "Band" / "Tunes (2020)" / "04 Other Melody.m4a")
    assert other.match == "user_details"
    assert item_state(index, auto) == item_state(index, chosen) == ("adopted", [])
    assert (rips / "Band - Melody.mp3").read_bytes() == before  # the rip is untouched
    assert album_lookups == [("album", "MPREb_1")]  # two songs, one album: asked once

    # Undo puts both back as they were matched.
    batch_id = jobs(lib)[0]["batch_id"]
    pipeline.undo(lib, batch_id)
    assert item_state(index, auto) == ("matched_auto", [])
    assert item_state(index, chosen) == ("matched_user", [])
    assert music_files(lib) == []


def test_keep_your_own_audio_leaves_out_what_it_cant_trust(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    album_lookups: list[tuple[str, str]],
) -> None:
    turned_down = own_rip(index, rips, samples["mp3"], "Band - One")
    with state.edit(lib.paths.state_file) as st:
        st.data["gate"] = {turned_down: {VIDEO_A: {"verdict": "different", "ber": 0.4}}}
    own_rip(index, rips, samples["webm"], "Band - Two")
    iid = add_item(index, "Band - Three", state="review", reasons=["duration_mismatch"])
    add_candidates(index, iid, [candidate(VIDEO_A, "Melody", ("Band",), 3)])

    plan = pipeline.plan_adopt(lib, index, matched=True)

    assert plan.operations == []
    assert plan.summary["skipped"] == {"fingerprint_turned_down": 1, "format_needs_a_download": 1}
    # Without --matched, matched rips aren't touched at all.
    assert pipeline.plan_adopt(lib, index).summary["skipped"] == {}


def test_an_unknown_track_number_is_removed_not_kept(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    album_lookups: list[tuple[str, str]],
) -> None:
    own = tags.TrackTags(track=7, track_total=12)
    own_rip(index, rips, samples["mp3"], "Band - Bonus", video_id="notOnAlbum1",
            title="Bonus Song", own=own)  # fmt: skip
    plan = pipeline.plan_adopt(lib, index, matched=True)
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    assert music_files(lib) == ["Band/Tunes (2020)/Bonus Song.mp3"]
    written = tags.read_tags(lib.paths.music / "Band" / "Tunes (2020)" / "Bonus Song.mp3")
    assert (written.track, written.track_total) == (None, None)


# ---- lyrics and covers for the library (step 10) ---------------------------------------

PLAIN = "First made-up line\nSecond made-up line"
SYNCED = "[00:01.00]First made-up line\n[00:02.00]Second made-up line\n"


def cover(width: int = 1200) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, width), (10, 120, 200)).save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture
def adopted(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    album_lookups: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """One matched rip in the library with its official details, and no extras yet."""
    monkeypatch.setattr(pipeline, "EXTRAS", [])
    own_rip(index, rips, samples["mp3"], "Band - Melody")
    plan = pipeline.plan_adopt(lib, index, matched=True)
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    return lib.paths.music / "Band" / "Tunes (2020)" / "03 Melody.mp3"


def test_lyrics_for_the_library(
    lib: Library, index: Index, adopted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[lyrics.Query] = []

    def find(query: lyrics.Query, *, cache: Any = None) -> lyrics.Found:
        asked.append(query)
        return lyrics.Found("synced", plain=PLAIN, synced=SYNCED, source="LRCLIB")

    monkeypatch.setattr(lyrics, "find", find)
    plan = pipeline.plan_lyrics(lib, index, missing=True)
    assert plan.summary["operations"] == 1
    batch_id = pipeline.apply(lib, index, plan.plan_id).batch_id
    run_queue(lib)

    (query,) = asked
    assert (query.title, query.artist, query.album, query.video_id, query.official_s) == (
        "Melody", "Band", "Tunes", VIDEO_A, 3.0,
    )  # fmt: skip
    assert tags.read_tags(adopted).lyrics == PLAIN
    assert adopted.with_suffix(".lrc").read_text(encoding="utf-8") == SYNCED
    assert [j["last_error"] for j in jobs(lib) if j["kind"] == "lyrics"] == ["synced from LRCLIB"]
    assert pipeline.plan_lyrics(lib, index, missing=True).summary["skipped"] == {"has_lyrics": 1}

    pipeline.undo(lib, batch_id)
    assert tags.read_tags(adopted).lyrics is None
    assert not adopted.with_suffix(".lrc").exists()


def test_covers_for_the_library(
    lib: Library, index: Index, adopted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    def album_art(browse_id: str, *, cache: Any = None) -> artwork.Art:
        asked.append(browse_id)
        return artwork.Art(cover(), 1200, 1200)

    monkeypatch.setattr(artwork, "album_art", album_art)
    plan = pipeline.plan_artwork(lib, index, missing=True)
    assert (plan.summary["operations"], plan.summary["albums"]) == (1, 1)
    batch_id = pipeline.apply(lib, index, plan.plan_id).batch_id
    run_queue(lib)

    assert asked == ["MPREb_1"]
    assert tags.read_tags(adopted).cover == cover()
    assert (adopted.parent / "cover.jpg").read_bytes() == cover()
    assert pipeline.plan_artwork(lib, index, missing=True).summary["skipped"] == {"has_cover": 1}

    pipeline.undo(lib, batch_id)
    assert tags.read_tags(adopted).cover is None
    assert not (adopted.parent / "cover.jpg").exists()


def test_a_video_frame_cover_counts_as_missing(lib: Library, index: Index, adopted: Path) -> None:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (1280, 720)).save(buffer, "JPEG")
    with fileops.batch(lib, "demo") as b:
        fileops.write_tags(b, adopted, tags.TrackTags(cover=buffer.getvalue()))
    assert pipeline.plan_artwork(lib, index, missing=True).summary["operations"] == 1


def test_an_only_copy_song_gets_a_cover_only_from_the_owners_art_url(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_url = adopt_item(index, rips, samples["mp3"], "Band - Rare Song", confidence=0.95)
    adopt_item(index, rips, samples["m4a"], "Band - Other Rare Song", confidence=0.95)
    with state.edit(lib.paths.state_file) as st:
        chosen = {"decision": "only_copy", "art_url": "https://soundcloud.com/band/rare"}
        st.data["decisions"] = {with_url: chosen}
    plan = pipeline.plan_adopt(lib, index)
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    urls: list[str] = []

    def from_url(url: str) -> artwork.Art:
        urls.append(url)
        return artwork.Art(cover(600), 600, 600)

    monkeypatch.setattr(artwork, "art_from_url", from_url)
    plan = pipeline.plan_artwork(lib, index)
    assert plan.summary["skipped"] == {"no_official_cover": 1}
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    assert urls == ["https://soundcloud.com/band/rare"]
    assert tags.read_tags(lib.paths.music / "Band" / "Unsorted" / "Rare Song.mp3").cover == cover(
        600
    )


def test_a_file_gone_since_the_plan(lib: Library, index: Index, adopted: Path) -> None:
    plan = pipeline.plan_lyrics(lib, index)
    pipeline.apply(lib, index, plan.plan_id)
    adopted.unlink()
    run_queue(lib)
    (job,) = [j for j in jobs(lib) if j["kind"] == "lyrics"]
    assert (job["state"], job["reason"]) == ("needs_review", "file_changed")


def test_new_adopts_get_lyrics_and_a_cover(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    album_lookups: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lyrics, "find", lambda query, cache=None: lyrics.Found(
        "synced", plain=PLAIN, synced=SYNCED, source="LRCLIB"))  # fmt: skip
    monkeypatch.setattr(artwork, "album_art", lambda browse_id, cache=None: artwork.Art(
        cover(), 1200, 1200))  # fmt: skip
    own_rip(index, rips, samples["mp3"], "Band - Melody")
    plan = pipeline.plan_adopt(lib, index, matched=True)
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    song = lib.paths.music / "Band" / "Tunes (2020)" / "03 Melody.mp3"
    written = tags.read_tags(song)
    assert (written.lyrics, written.cover) == (PLAIN, cover())
    assert song.with_suffix(".lrc").read_text(encoding="utf-8") == SYNCED
    assert (song.parent / "cover.jpg").is_file()


# ---- preferred names and duplicates (step 09d) ------------------------------------------


@pytest.fixture
def two_mp3s(ffmpeg_path: Path, samples: dict[str, Path], tmp_path: Path) -> tuple[Path, Path]:
    """The same 3 s of melody as a 96 kbps and a 192 kbps MP3."""
    made = []
    for kbps in (96, 192):
        out = tmp_path / f"melody-{kbps}.mp3"
        subprocess.run([str(ffmpeg_path), "-v", "error", "-i", str(samples["m4a"]), "-c:a",
                        "libmp3lame", "-b:a", f"{kbps}k", str(out)], check=True)  # fmt: skip
        made.append(out)
    return made[0], made[1]


def test_preferred_names_rename_the_library(lib: Library, index: Index, adopted: Path) -> None:
    with fileops.batch(lib, "demo") as b:
        fileops.write_sidecar(b, adopted, ".lrc", SYNCED.encode())
        fileops.write_sidecar(b, adopted, naming.COVER_NAME, cover())
    assert pipeline.plan_tidy(lib, index).operations == []  # nothing to do yet
    pipeline.set_name(lib, "Band", "The Band")

    plan = pipeline.plan_tidy(lib, index)
    assert (plan.summary["renames"], plan.summary["duplicates"]) == (1, 0)
    batch_id = pipeline.apply(lib, index, plan.plan_id).batch_id
    run_queue(lib)

    moved = lib.paths.music / "The Band" / "Tunes (2020)" / "03 Melody.mp3"
    assert music_files(lib) == ["The Band/Tunes (2020)/03 Melody.lrc",
                                "The Band/Tunes (2020)/03 Melody.mp3",
                                "The Band/Tunes (2020)/cover.jpg"]  # fmt: skip
    written = tags.read_tags(moved)
    assert (written.artist, written.album_artist, written.title) == (
        "The Band",
        "The Band",
        "Melody",
    )
    assert [t["rel_path"] for t in index.library_tracks()] == [
        "Music/The Band/Tunes (2020)/03 Melody.mp3"
    ]
    assert pipeline.plan_tidy(lib, index).operations == []  # done
    assert not (lib.paths.music / "Band").exists()  # the emptied folders went

    pipeline.undo(lib, batch_id)
    assert not (lib.paths.music / "The Band").exists()
    assert music_files(lib) == ["Band/Tunes (2020)/03 Melody.lrc",
                                "Band/Tunes (2020)/03 Melody.mp3",
                                "Band/Tunes (2020)/cover.jpg"]  # fmt: skip
    assert tags.read_tags(adopted).artist == "Band"
    assert [t["rel_path"] for t in index.library_tracks()] == [
        "Music/Band/Tunes (2020)/03 Melody.mp3"
    ]


def test_new_songs_use_preferred_names(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    album_lookups: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "EXTRAS", [])
    pipeline.set_name(lib, "Band", "The Band")
    own_rip(index, rips, samples["mp3"], "Band - Melody")
    plan = pipeline.plan_adopt(lib, index, matched=True)
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    assert music_files(lib) == ["The Band/Tunes (2020)/03 Melody.mp3"]
    written = tags.read_tags(lib.paths.music / "The Band" / "Tunes (2020)" / "03 Melody.mp3")
    assert (written.artist, written.album_artist) == ("The Band", "The Band")


def test_only_the_best_copy_of_a_song_is_adopted(
    lib: Library,
    index: Index,
    rips: Path,
    two_mp3s: tuple[Path, Path],
    album_lookups: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "EXTRAS", [])
    low, high = two_mp3s
    worse = own_rip(index, rips, low, "Band - Melody", kbps=96)
    better = own_rip(index, rips, high, "Band - Melody (another site)", kbps=192)

    plan = pipeline.plan_adopt(lib, index, matched=True)
    assert (plan.summary["with_details"], plan.summary["duplicates"]) == (1, 1)
    batch_id = pipeline.apply(lib, index, plan.plan_id).batch_id
    run_queue(lib)

    assert music_files(lib) == ["Band/Tunes (2020)/03 Melody.mp3"]
    kept = tags.read_tags(lib.paths.music / "Band" / "Tunes (2020)" / "03 Melody.mp3")
    assert kept.origin_path == str(rips / "Band - Melody (another site).mp3")
    assert item_state(index, better)[0] == "adopted"
    assert item_state(index, worse)[0] == "superseded"
    links = state.superseded(lib.load_state().data)
    assert links == {state.normalise_path(rips / "Band - Melody.mp3"): kept.musicorg_id}

    pipeline.undo(lib, batch_id)
    assert item_state(index, worse)[0] == item_state(index, better)[0] == "matched_auto"
    assert state.superseded(lib.load_state().data) == {}


def test_a_duplicate_in_the_library_is_set_aside_and_the_number_dropped(
    lib: Library,
    index: Index,
    rips: Path,
    two_mp3s: tuple[Path, Path],
    album_lookups: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "EXTRAS", [])
    low, high = two_mp3s
    worse = own_rip(index, rips, low, "Band - Melody", kbps=96)
    pipeline.apply(lib, index, pipeline.plan_adopt(lib, index, matched=True).plan_id)
    run_queue(lib)
    # Before step 09d, a second rip of the song came in too, as " (2)".
    shutil.copyfile(high, rips / "Band - Melody (2).mp3")
    better = add_item(index, "Band - Melody (2)", state="adopted", seconds=3)
    with fileops.batch(lib, "demo") as b:
        staged = fileops.stage_copy(b, rips / "Band - Melody (2).mp3")
        fileops.write_tags(b, staged, tags.TrackTags(
            title="Melody", artist="Band", album_artist="Band", album="Tunes", year=2020,
            track=3, musicorg_id=tags.new_track_id(), source="rip_copy", source_id=VIDEO_A,
            origin_path=str(rips / "Band - Melody (2).mp3"), schema=1))  # fmt: skip
        second = fileops.commit(b, staged, Path("Band", "Tunes (2020)", "03 Melody.mp3"))
    assert second.name == "03 Melody (2).mp3"
    index.put_library_tracks([pipeline._track_row(lib, second, tags.read_tags(second), 3.0)])

    plan = pipeline.plan_tidy(lib, index)
    # One copy set aside, and the kept one loses a " (2)" it no longer needs. That isn't
    # a preferred name, and is counted apart from those.
    assert (plan.summary["duplicates"], plan.summary["numbers_dropped"]) == (1, 1)
    assert plan.summary["renames"] == 0
    batch_id = pipeline.apply(lib, index, plan.plan_id).batch_id
    run_queue(lib)

    assert music_files(lib) == ["Band/Tunes (2020)/03 Melody.mp3"]
    kept = tags.read_tags(lib.paths.music / "Band" / "Tunes (2020)" / "03 Melody.mp3")
    assert kept.origin_path == str(rips / "Band - Melody (2).mp3")  # the 192 kbps copy
    assert sorted(p.name for p in lib.paths.replaced.rglob("*.mp3")) == ["03 Melody.mp3"]
    assert item_state(index, worse)[0] == "superseded"
    assert item_state(index, better)[0] == "adopted"
    assert len(index.library_tracks()) == 1

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == ["Band/Tunes (2020)/03 Melody (2).mp3",
                                "Band/Tunes (2020)/03 Melody.mp3"]  # fmt: skip
    assert item_state(index, worse)[0] == "adopted"
    assert len(index.library_tracks()) == 2


def test_the_names_commands(lib: Library, capsys: pytest.CaptureFixture[str]) -> None:
    root = str(lib.root)
    lib.close()
    assert cli.main(["--library", root, "names", "set", "JAŸ-Z", "Jay Z"]) == 0
    assert cli.main(["--library", root, "--json", "names", "list"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out[out.index("{") :])["names"] == {"JAŸ-Z": "Jay Z"}
    assert cli.main(["--library", root, "names", "remove", "JAŸ-Z"]) == 0
    assert cli.main(["--library", root, "names", "remove", "JAŸ-Z"]) == 1
    assert pipeline.prefer("JAŸ-Z, Kanye West", {"JAŸ-Z": "Jay Z"}) == "Jay Z, Kanye West"
    once = pipeline.prefer("Band & Bandit", {"Band": "The Band"})
    assert once == "The Band & Bandit"
    assert pipeline.prefer(once, {"Band": "The Band"}) == once  # doing it twice changes nothing


def test_a_cd_rip_beats_a_youtube_conversion_whatever_the_bitrate(tmp_path: Path) -> None:
    """Found on the owner's library: an iTunes CD rip at 121 kbps and a converter site's
    128 kbps MP3 of the same song. The CD rip is the better copy."""
    source = fileops.FileCheck("x", 5_000_000, 0, "")
    cd = {"codec": "aac", "bitrate_kbps": 121, "rel_path": "Band/Album/04 Song.m4a",
          "raw_tags_json": {"extra": {"encoder": "iTunes v4.6, QuickTime 6.5.1"}}}  # fmt: skip
    converted = {"codec": "mp3", "bitrate_kbps": 128, "rel_path": "Band/Song.mp3",
                 "raw_tags_json": {"extra": {"encoder": "Lavf53.32.100"}}}  # fmt: skip
    site = {"codec": "mp3", "bitrate_kbps": 320, "rel_path": "yt5s.io - Band - Song.mp3",
            "raw_tags_json": {"extra": {}}}  # fmt: skip
    flac = {"codec": "flac", "bitrate_kbps": 900, "rel_path": "Band/Song.flac",
            "raw_tags_json": {"extra": {"encoder": "Lavf60"}}}  # fmt: skip
    ranked = sorted([converted, site, cd, flac], key=lambda i: pipeline._rip_quality(i, source),
                    reverse=True)  # fmt: skip
    assert [i["rel_path"] for i in ranked] == [
        "Band/Song.flac",  # lossless first
        "Band/Album/04 Song.m4a",  # then a rip that isn't a conversion
        "yt5s.io - Band - Song.mp3",  # then the higher bitrate
        "Band/Song.mp3",
    ]


def test_a_lyrics_file_both_copies_share_stays(
    lib: Library,
    index: Index,
    rips: Path,
    two_mp3s: tuple[Path, Path],
    samples: dict[str, Path],
    album_lookups: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Found on the owner's library: "04 Song.m4a" and "04 Song.mp3" share "04 Song.lrc",
    which must stay when the lesser copy is set aside."""
    monkeypatch.setattr(pipeline, "EXTRAS", [])
    own_rip(index, rips, two_mp3s[0], "Band - Melody", kbps=96)
    pipeline.apply(lib, index, pipeline.plan_adopt(lib, index, matched=True).plan_id)
    run_queue(lib)
    mp3 = lib.paths.music / "Band" / "Tunes (2020)" / "03 Melody.mp3"
    shutil.copyfile(samples["m4a"], rips / "Band - Melody.m4a")
    add_item(index, "Band - Melody", state="adopted", seconds=3, ext=".m4a")
    with fileops.batch(lib, "demo") as b:
        staged = fileops.stage_copy(b, rips / "Band - Melody.m4a")
        fileops.write_tags(b, staged, tags.TrackTags(
            title="Melody", artist="Band", album_artist="Band", album="Tunes", year=2020,
            track=3, musicorg_id=tags.new_track_id(), source="rip_copy", source_id=VIDEO_A,
            origin_path=str(rips / "Band - Melody.m4a"), schema=1))  # fmt: skip
        m4a = fileops.commit(b, staged, Path("Band", "Tunes (2020)", "03 Melody.m4a"))
        fileops.write_sidecar(b, mp3, ".lrc", SYNCED.encode())
    index.put_library_tracks([pipeline._track_row(lib, m4a, tags.read_tags(m4a), 3.0)])

    plan = pipeline.plan_tidy(lib, index)
    assert plan.summary["duplicates"] == 1
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    assert len([f for f in music_files(lib) if not f.endswith(".lrc")]) == 1
    assert (lib.paths.music / "Band" / "Tunes (2020)" / "03 Melody.lrc").is_file()


def test_empty_folders_are_cleared(lib: Library, index: Index, adopted: Path) -> None:
    leftover = lib.paths.music / "Old Name" / "Album (2001)"
    leftover.mkdir(parents=True)
    (leftover / ".DS_Store").write_bytes(b"junk")
    plan = pipeline.plan_tidy(lib, index)
    assert plan.summary["empty_folders"] == 1
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    assert not (lib.paths.music / "Old Name").exists()
    assert adopted.is_file()  # a folder with a song in it is never touched


# ---- unconfirmed copies (v0.2): play it now, review it later -------------------------


def waiting_item(index: Index, iid: str, state_: str, confidence: float = 0.95) -> None:
    item = index.item(iid)
    assert item is not None
    item["state"], item["parse_confidence"] = state_, confidence
    index.put_items([item])


def adopt_unconfirmed(lib: Library, index: Index) -> str:
    plan = pipeline.plan_adopt(lib, index, unconfirmed=True)
    batch_id = pipeline.apply(lib, index, plan.plan_id).batch_id
    run_queue(lib)
    return batch_id


def test_unreviewed_rips_come_in_unconfirmed_and_stay_in_review(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    in_review = own_rip(index, rips, samples["mp3"], "Band - Melody", state_="review")
    lost = adopt_item(index, rips, samples["m4a"], "Band - Rare Song")
    waiting_item(index, lost, "not_found")
    webm = adopt_item(index, rips, samples["mp3"], "Band - Video")
    waiting_item(index, webm, "review")
    item = index.item(webm)
    assert item is not None
    item["ext"] = ".webm"
    index.put_items([item])

    plan = pipeline.plan_adopt(lib, index, unconfirmed=True)
    assert (plan.summary["unconfirmed"], plan.summary["adopts"]) == (2, 0)
    assert plan.summary["skipped"] == {"format_needs_a_download": 1}
    assert any("[unconfirmed: stays in review]" in line for line in pipeline.describe(plan))
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)

    assert music_files(lib) == ["Band/Unsorted/Melody.mp3", "Band/Unsorted/Rare Song.m4a"]
    written = tags.read_tags(lib.paths.music / "Band" / "Unsorted" / "Melody.mp3")
    assert (written.match, written.only_copy, written.source) == ("unconfirmed", None, "rip_copy")
    assert (written.artist, written.title, written.source_id) == ("Band", "Melody", None)
    assert written.origin_path == str(rips / "Band - Melody.mp3")
    # Still in the review queue, and a rescan must not call them adopted.
    assert item_state(index, in_review)[0] == "review"
    assert item_state(index, lost)[0] == "not_found"
    assert item_state(index, webm)[0] == "review"  # not marked unsupported: not decided
    derive = scan._state_deriver(lib, index)
    assert derive(in_review, rips / "Band - Melody.mp3") is None
    assert {t["match"] for t in index.library_tracks()} == {"unconfirmed"}

    # A second plan has nothing to do.
    again = pipeline.plan_adopt(lib, index, unconfirmed=True)
    assert again.summary["unconfirmed"] == 0
    assert again.summary["skipped"]["already_in_library"] == 2


def test_undo_of_unconfirmed_copies(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    iid = own_rip(index, rips, samples["mp3"], "Band - Melody", state_="review")
    batch_id = adopt_unconfirmed(lib, index)
    pipeline.undo(lib, batch_id)
    assert music_files(lib) == []
    assert index.library_tracks() == []
    assert item_state(index, iid)[0] == "review"


def test_a_match_chosen_later_upgrades_the_unconfirmed_copy_in_place(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    album_lookups: list[tuple[str, str]],
) -> None:
    iid = own_rip(index, rips, samples["mp3"], "Band - Melody", state_="review")
    adopt_unconfirmed(lib, index)
    first = tags.read_tags(lib.paths.music / "Band" / "Unsorted" / "Melody.mp3")

    index.set_state(iid, "matched_user", [])  # the owner picked the match
    plan = pipeline.plan_adopt(lib, index, matched=True)
    assert plan.summary["with_details"] == 1
    batch_id = pipeline.apply(lib, index, plan.plan_id).batch_id
    run_queue(lib)

    # One file, not two: the copy was retagged and moved, the rip wasn't copied again.
    assert [f for f in music_files(lib) if f.endswith(".mp3")] == [
        "Band/Tunes (2020)/03 Melody.mp3"
    ]
    assert not (lib.paths.music / "Band" / "Unsorted").exists()
    written = tags.read_tags(lib.paths.music / "Band" / "Tunes (2020)" / "03 Melody.mp3")
    assert (written.match, written.source_id, written.album) == ("user_details", VIDEO_A, "Tunes")
    assert written.musicorg_id == first.musicorg_id  # the same library track throughout
    assert item_state(index, iid) == ("adopted", [])
    assert [t["rel_path"] for t in index.library_tracks()] == [
        "Music/Band/Tunes (2020)/03 Melody.mp3"
    ]
    assert list(lib.paths.replaced.rglob("*.mp3")) == []

    # Undo: back to the unconfirmed copy, and back in the queue it came from.
    pipeline.undo(lib, batch_id)
    assert [f for f in music_files(lib) if f.endswith(".mp3")] == ["Band/Unsorted/Melody.mp3"]
    back = tags.read_tags(lib.paths.music / "Band" / "Unsorted" / "Melody.mp3")
    assert (back.match, back.source_id, back.album) == ("unconfirmed", None, first.album)
    assert item_state(index, iid) == ("matched_user", [])
    assert [t["match"] for t in index.library_tracks()] == ["unconfirmed"]


def test_only_copy_decided_later_upgrades_the_unconfirmed_copy(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    kept = adopt_item(index, rips, samples["mp3"], "Band - Rare Song")
    waiting_item(index, kept, "review")
    renamed = adopt_item(index, rips, samples["m4a"], "Band - Odd Name")
    waiting_item(index, renamed, "review")
    adopt_unconfirmed(lib, index)

    index.set_state(kept, "only_copy", [])
    index.set_state(renamed, "only_copy", [])
    with state.edit(lib.paths.state_file) as st:
        st.data.setdefault("decisions", {})[renamed] = {
            "decision": "only_copy", "title_fix": "Proper Name"}  # fmt: skip
    plan = pipeline.plan_adopt(lib, index)
    assert plan.summary["adopts"] == 2
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)

    assert music_files(lib) == ["Band/Unsorted/Proper Name.m4a", "Band/Unsorted/Rare Song.mp3"]
    same = tags.read_tags(lib.paths.music / "Band" / "Unsorted" / "Rare Song.mp3")
    assert (same.only_copy, same.match) == (True, None)
    fixed = tags.read_tags(lib.paths.music / "Band" / "Unsorted" / "Proper Name.m4a")
    assert (fixed.only_copy, fixed.match, fixed.title) == (True, "manual", "Proper Name")
    assert item_state(index, kept) == item_state(index, renamed) == ("adopted", [])
    assert len(index.library_tracks()) == 2


def test_an_unconfirmed_copy_that_turns_out_to_be_a_duplicate_is_set_aside(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    album_lookups: list[tuple[str, str]],
) -> None:
    good = own_rip(index, rips, samples["m4a"], "Band - Melody", kbps=256)
    worse = own_rip(index, rips, samples["mp3"], "band melody lyrics", state_="review", kbps=96)
    plan = pipeline.plan_adopt(lib, index, matched=True, unconfirmed=True)
    assert (plan.summary["with_details"], plan.summary["unconfirmed"]) == (1, 1)
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    assert len([f for f in music_files(lib) if f.endswith((".mp3", ".m4a"))]) == 2

    index.set_state(worse, "matched_user", [])  # the owner says it's the same song
    plan = pipeline.plan_adopt(lib, index, matched=True)
    assert plan.summary["duplicates"] == 1
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)

    assert [f for f in music_files(lib) if f.endswith((".mp3", ".m4a"))] == [
        "Band/Tunes (2020)/03 Melody.m4a"
    ]
    assert len(list(lib.paths.replaced.rglob("*.mp3"))) == 1  # kept, out of the way
    assert item_state(index, good)[0] == "adopted"
    assert item_state(index, worse)[0] == "superseded"
    assert len(index.library_tracks()) == 1


def test_tidy_sets_aside_the_copy_of_a_rip_a_download_has_replaced(
    lib: Library, index: Index, rips: Path, audio: AudioFixtures, downloads: FakeDownloads
) -> None:
    """The owner's library, 2026-10-07: 29 rips were replaced by downloads, and the copies
    made of them on 1 Oct, to play them meanwhile, stayed beside the downloads."""
    iid = rip(index, rips, audio.melody_a_mp3, state_="review")
    other = rip(
        index, rips, audio.melody_a_mp3, "Band - Another", state_="review", video_id=VIDEO_B
    )
    adopt_unconfirmed(lib, index)
    assert music_files(lib) == ["Band/Unsorted/Another.mp3", "Band/Unsorted/Melody.mp3"]

    index.set_state(iid, "matched_user", [])  # the owner chose the official track
    plan_and_run(lib, index, only="accepted")
    assert item_state(index, iid)[0] == "superseded"
    assert len([f for f in music_files(lib) if f.endswith("Melody.mp3")]) == 1  # still there

    plan = pipeline.plan_tidy(lib, index)
    assert (plan.summary["replaced_copies"], plan.summary["duplicates"]) == (1, 1)
    batch_id = pipeline.apply(lib, index, plan.plan_id).batch_id
    run_queue(lib)

    songs = [f for f in music_files(lib) if f.endswith((".mp3", ".m4a"))]
    assert songs == ["Band/Tunes (2020)/03 Melody.m4a", "Band/Unsorted/Another.mp3"]
    assert len(list(lib.paths.replaced.rglob("Melody.mp3"))) == 1  # kept, out of the way
    assert {t["match"] for t in index.library_tracks()} == {"user_confirmed", "unconfirmed"}
    assert item_state(index, iid)[0] == "superseded"
    assert item_state(index, other)[0] == "review"  # a rip still waiting keeps its copy
    assert (rips / "Band - Melody.mp3").is_file()  # the rip itself is never touched
    assert pipeline.plan_tidy(lib, index).summary["replaced_copies"] == 0

    pipeline.undo(lib, batch_id)
    assert len([f for f in music_files(lib) if f.endswith("Melody.mp3")]) == 1
    assert item_state(index, iid)[0] == "superseded"


# ---- what a copy is called: the version stays in its name (2026-10-04) ------------------
#
# The owner's rule: a song that has been found takes the found name; one that hasn't keeps
# the name they had on it. "R" is their own mark for a remix, and stays as they typed it.


def song(lib: Library, *parts: str) -> tags.TrackTags:
    return tags.read_tags(lib.paths.music.joinpath(*parts))


def apply_and_run(lib: Library, index: Index, plan: fileops.Plan) -> str:
    batch_id = pipeline.apply(lib, index, plan.plan_id).batch_id
    run_queue(lib)
    return batch_id


def forget_versions(index: Index, iid: str) -> None:
    """Store an item's candidates the way a review row or a search gives them: with no
    version tokens of their own, whatever their title says."""
    item = index.item(iid)
    assert item is not None
    rows = index.candidates(iid)
    for row in rows:
        row["payload"]["version_tokens"] = []
    index.set_match(iid, item["state"], item["reasons_json"], rows)


def old_copy(lib: Library, index: Index, rip: Path, title: str) -> Path:
    """An unconfirmed copy as the engine made them before 2026-10-04: titled with the
    parsed title, its version left out, and with no version tag."""
    with fileops.batch(lib, "demo") as b:
        staged = fileops.stage_copy(b, rip)
        fileops.write_tags(b, staged, tags.TrackTags(
            title=title, artist="Band", musicorg_id=tags.new_track_id(), source="rip_copy",
            source_format="mp3", match="unconfirmed", origin_path=str(rip), schema=1))  # fmt: skip
        copy = fileops.commit(b, staged, Path("Band", "Unsorted", f"{title}{rip.suffix}"))
    index.put_library_tracks([pipeline._track_row(lib, copy, tags.read_tags(copy), 3.0)])
    return copy


def test_a_remix_and_its_original_each_keep_their_name(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """The owner's report: the rip "Come As You Are R" was copied in as "Come As You
    Are", and the real original then became "Come As You Are (2)"."""
    remix = adopt_item(index, rips, samples["mp3"], "Band - Melody R",
                       own=tags.TrackTags(title="Melody R", artist="Band"))  # fmt: skip
    original = adopt_item(index, rips, samples["mp3"], "Band - Melody")
    waiting_item(index, remix, "review")
    waiting_item(index, original, "not_found")
    # As the owner's index is: scanned before the engine kept the words of a version.
    # Nothing needs scanning again for the names to come out right.
    for iid in (remix, original):
        item = index.item(iid)
        assert item is not None
        del item["parsed_json"]["version_words"]
        index.put_items([item])

    plan = pipeline.plan_adopt(lib, index, unconfirmed=True)
    assert [op.target for op in plan.operations] == [
        "Music/Band/Unsorted/Melody R.mp3",
        "Music/Band/Unsorted/Melody.mp3",
    ]
    batch_id = apply_and_run(lib, index, plan)

    assert music_files(lib) == ["Band/Unsorted/Melody R.mp3", "Band/Unsorted/Melody.mp3"]
    kept = song(lib, "Band", "Unsorted", "Melody R.mp3")
    assert (kept.title, kept.version, kept.match) == ("Melody R", ["remix"], "unconfirmed")
    assert kept.origin_path == str(rips / "Band - Melody R.mp3")
    plain = song(lib, "Band", "Unsorted", "Melody.mp3")
    assert (plain.title, plain.version, plain.match) == ("Melody", None, "unconfirmed")
    assert plain.origin_path == str(rips / "Band - Melody.mp3")
    assert {t["rel_path"]: t["title"] for t in index.library_tracks()} == {
        "Music/Band/Unsorted/Melody R.mp3": "Melody R",
        "Music/Band/Unsorted/Melody.mp3": "Melody",
    }
    assert item_state(index, remix)[0] == "review"  # still waiting to be found

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == [] and index.library_tracks() == []


def test_a_named_version_is_kept_in_the_rips_own_words(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    # The remixer named in the title tag, accent and all; the token is for comparing.
    adopt_item(index, rips, samples["mp3"], "Band - Howling", confidence=0.95,
               own=tags.TrackTags(title="Howling (Âme Remix)", artist="Band"))  # fmt: skip
    # The remixer named only in the file name.
    adopt_item(index, rips, samples["mp3"], "Band - Melody (Somebody Remix)", confidence=0.95)
    # Square brackets become round ones, and junk stays out of the name.
    adopt_item(index, rips, samples["m4a"], "Band - Still Here [Acoustic Version] (320 kbps)",
               confidence=0.95)  # fmt: skip
    # A soft version is part of the name too.
    adopt_item(index, rips, samples["mp3"], "Band - Old Song (2009 Remaster)", confidence=0.95)
    # What a file name can't hold (on Windows, a colon) is changed there only.
    club = tags.TrackTags(title="Club Night (Live @ 9:30 Club)", artist="Band")
    adopt_item(index, rips, samples["mp3"], "Band - Club Night", confidence=0.95, own=club)

    apply_and_run(lib, index, pipeline.plan_adopt(lib, index))

    assert [unicodedata.normalize("NFC", name) for name in music_files(lib)] == [
        "Band/Unsorted/Club Night (Live @ 9_30 Club).mp3",
        "Band/Unsorted/Howling (Âme Remix).mp3",
        "Band/Unsorted/Melody (Somebody Remix).mp3",
        "Band/Unsorted/Old Song (2009 Remaster).mp3",
        "Band/Unsorted/Still Here (Acoustic Version).m4a",
    ]
    found = {
        str(t.title): t.version
        for t in (tags.read_tags(lib.paths.music / name) for name in music_files(lib))
    }
    assert found == {
        "Club Night (Live @ 9:30 Club)": ["live:9 30 club"],
        "Howling (Âme Remix)": ["remix:ame"],
        "Melody (Somebody Remix)": ["remix:somebody"],
        "Old Song (2009 Remaster)": ["remaster:2009"],
        "Still Here (Acoustic Version)": ["acoustic"],
    }


def test_a_remix_named_in_the_tags_and_marked_in_the_file_name_is_one_remix(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """The title tag names the remixer and the file name carries the owner's R: the tag
    says what the title says, `remix:lucian`, not that and a second, nameless remix."""
    adopt_item(index, rips, samples["mp3"], "Band - Here R", confidence=0.95,
               own=tags.TrackTags(title="Here (Lucian Remix)", artist="Band"))  # fmt: skip
    apply_and_run(lib, index, pipeline.plan_adopt(lib, index))
    assert music_files(lib) == ["Band/Unsorted/Here (Lucian Remix).mp3"]
    written = song(lib, "Band", "Unsorted", "Here (Lucian Remix).mp3")
    assert (written.title, written.version) == ("Here (Lucian Remix)", ["remix:lucian"])


def test_a_parse_that_isnt_trusted_names_no_version(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    own = tags.TrackTags(title="Real Title (Somebody Remix)", artist="Real Artist")
    adopt_item(index, rips, samples["mp3"], "zz_unclear_42 R", confidence=0.3, own=own)

    plan = pipeline.plan_adopt(lib, index)
    assert plan.summary["low_confidence_adopts"] == 1
    assert plan.operations[0].params["names"]["version"] == []
    apply_and_run(lib, index, plan)

    # The rip's own title, untouched, and no guess at a version written into the file.
    assert music_files(lib) == ["Real Artist/Unsorted/Real Title (Somebody Remix).mp3"]
    written = song(lib, "Real Artist", "Unsorted", "Real Title (Somebody Remix).mp3")
    assert (written.title, written.version) == ("Real Title (Somebody Remix)", None)


def test_a_version_tag_the_rip_already_carries_is_kept(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    own = tags.TrackTags(title="Plain Song", artist="Band", version=["live:wembley"])
    adopt_item(index, rips, samples["mp3"], "Band - Plain Song", confidence=0.95, own=own)
    apply_and_run(lib, index, pipeline.plan_adopt(lib, index))
    written = song(lib, "Band", "Unsorted", "Plain Song.mp3")
    assert (written.title, written.version) == ("Plain Song", ["live:wembley"])


def test_two_rips_with_one_versioned_name_both_land(
    lib: Library, index: Index, rips: Path, two_mp3s: tuple[Path, Path]
) -> None:
    low, high = two_mp3s
    (rips / "a").mkdir()
    shutil.copyfile(low, rips / "a" / "Band - Melody R.mp3")
    first = add_item(index, "a/Band - Melody R", state="only_copy", seconds=3)
    item = index.item(first)
    assert item is not None
    index.put_items([{**item, "parsed_artist": "Band", "parsed_title": "Melody",
                      "parse_confidence": 0.95}])  # fmt: skip
    adopt_item(index, rips, high, "Band - Melody R", confidence=0.95)

    apply_and_run(lib, index, pipeline.plan_adopt(lib, index))

    # " (2)", never an overwrite: each file is still its own rip's audio.
    assert music_files(lib) == ["Band/Unsorted/Melody R (2).mp3", "Band/Unsorted/Melody R.mp3"]
    copies = [lib.paths.music / name for name in music_files(lib)]
    written = [tags.read_tags(path) for path in copies]
    assert [(t.title, t.version) for t in written] == [("Melody R", ["remix"])] * 2
    assert {t.origin_path for t in written} == {
        str(rips / "a" / "Band - Melody R.mp3"),
        str(rips / "Band - Melody R.mp3"),
    }
    for path, found in zip(copies, written, strict=True):
        assert tags.audio_hash(path) == tags.audio_hash(Path(str(found.origin_path)))


def test_only_copy_adopts_name_their_version(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    adopt_item(index, rips, samples["mp3"], "Band - Melody R", confidence=0.95)  # no fixes
    marked = adopt_item(index, rips, samples["mp3"], "zz_unclear_44", confidence=0.3)
    live = adopt_item(index, rips, samples["mp3"], "zz_unclear_45", confidence=0.3)
    with state.edit(lib.paths.state_file) as st:
        st.data["decisions"] = {
            marked: {"decision": "only_copy", "artist_fix": "Band", "title_fix": "Lost Boy R"},
            live: {"decision": "only_copy", "artist_fix": "Band", "title_fix": "Melody (Live)"},
        }

    apply_and_run(lib, index, pipeline.plan_adopt(lib, index))

    assert music_files(lib) == [
        "Band/Unsorted/Lost Boy R.mp3",
        "Band/Unsorted/Melody (Live).mp3",
        "Band/Unsorted/Melody R.mp3",
    ]
    plain = song(lib, "Band", "Unsorted", "Melody R.mp3")
    assert (plain.title, plain.version) == ("Melody R", ["remix"])
    assert (plain.only_copy, plain.match) == (True, None)
    # A title the owner typed is used exactly, and read for its version: their R too.
    fixed = song(lib, "Band", "Unsorted", "Lost Boy R.mp3")
    assert (fixed.title, fixed.version, fixed.match) == ("Lost Boy R", ["remix"], "manual")
    on_stage = song(lib, "Band", "Unsorted", "Melody (Live).mp3")
    assert (on_stage.title, on_stage.version, on_stage.match) == (
        "Melody (Live)", ["live"], "manual",
    )  # fmt: skip


def test_a_fix_to_another_field_doesnt_make_a_guessed_version_trusted(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """The owner fixed the artist of a rip whose name was hard to read. The names are
    used, as for any fix, but the version is still a guess, and a guess isn't written
    into the file's version tag."""
    iid = adopt_item(index, rips, samples["mp3"], "zz_unclear_46 (Somebody Remix)", confidence=0.3)
    with state.edit(lib.paths.state_file) as st:
        st.data["decisions"] = {iid: {"decision": "only_copy", "artist_fix": "Band"}}

    plan = pipeline.plan_adopt(lib, index)
    (op,) = plan.operations
    assert op.params["trusted"] is True and op.params["names"]["version"] == []
    apply_and_run(lib, index, plan)

    (name,) = music_files(lib)
    written = tags.read_tags(lib.paths.music / name)
    assert (written.artist, written.match, written.version) == ("Band", "manual", None)
    assert "(Somebody Remix)" in str(written.title)  # the name keeps what the rip says


def test_an_adopt_that_stops_before_its_commit_lands_once(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    iid = adopt_item(index, rips, samples["mp3"], "Band - Melody R", confidence=0.95)
    commit, stops = fileops.commit, [1]

    def stop_once(*args: Any, **kw: Any) -> Path:
        if stops:
            stops.pop()
            raise RuntimeError("the engine stopped here")
        return commit(*args, **kw)

    monkeypatch.setattr(fileops, "commit", stop_once)
    batch_id = apply_and_run(lib, index, pipeline.plan_adopt(lib, index))

    (job,) = jobs(lib)
    assert (job["state"], job["attempts"]) == ("done", 2)
    assert music_files(lib) == ["Band/Unsorted/Melody R.mp3"]
    assert song(lib, "Band", "Unsorted", "Melody R.mp3").version == ["remix"]
    assert item_state(index, iid) == ("adopted", [])
    assert len(index.library_tracks()) == 1

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == [] and index.library_tracks() == []
    assert item_state(index, iid) == ("only_copy", [])


def test_a_plan_made_before_versions_were_kept_is_refused(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    adopt_item(index, rips, samples["mp3"], "Band - Melody R", confidence=0.95)
    plan = pipeline.plan_adopt(lib, index)
    saved = lib.paths.plans / f"{plan.plan_id}.json"
    data = json.loads(saved.read_text(encoding="utf-8"))
    del data["operations"][0]["params"]["names"]["version"]  # as plans were saved before
    data["operations"][0]["params"]["names"]["title"] = "Melody"
    saved.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(PlanOutOfDateError, match="older version of the engine"):
        pipeline.apply(lib, index, plan.plan_id)
    assert jobs(lib) == [] and music_files(lib) == []

    # The same for the kind of plan the report came from: `plan adopt --unconfirmed`.
    waiting = adopt_item(index, rips, samples["mp3"], "Band - Other R", confidence=0.95)
    waiting_item(index, waiting, "review")
    plan = pipeline.plan_adopt(lib, index, unconfirmed=True)
    saved = lib.paths.plans / f"{plan.plan_id}.json"
    data = json.loads(saved.read_text(encoding="utf-8"))
    old = [op for op in data["operations"] if op["action"] == "adopt_unconfirmed"]
    assert len(old) == 1
    del old[0]["params"]["names"]["version"]
    data["operations"] = old
    saved.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PlanOutOfDateError, match="older version of the engine"):
        pipeline.apply(lib, index, plan.plan_id)
    assert jobs(lib) == [] and music_files(lib) == []


def test_a_found_song_takes_the_found_name(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    album_lookups: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The owner's example: "Lost Boy R" stays "Lost Boy R" until it is found, and then
    becomes the found title, "Lost Boy (Radio Remix)"."""
    asked: list[tuple[str, ...]] = []
    monkeypatch.setattr(pipeline, "EXTRAS", [lambda query: asked.append(query.versions)])
    iid = own_rip(index, rips, samples["mp3"], "Band - Melody R",
                  title="Melody (Radio Remix)", state_="review")  # fmt: skip
    adopt_unconfirmed(lib, index)
    waiting = song(lib, "Band", "Unsorted", "Melody R.mp3")
    assert (waiting.title, waiting.version) == ("Melody R", ["remix"])

    # The owner picks the match. A candidate kept from a review row or a search carries
    # no version tokens of its own: the tag must still come out of the official title.
    forget_versions(index, iid)
    index.set_state(iid, "matched_user", [])
    plan = pipeline.plan_adopt(lib, index, matched=True)
    assert (plan.summary["with_details"], plan.summary["version_not_in_title"]) == (1, 0)
    assert "this title names no version" not in pipeline.describe(plan)[0]
    batch_id = apply_and_run(lib, index, plan)

    assert music_files(lib) == ["Band/Tunes (2020)/03 Melody (Radio Remix).mp3"]
    found = song(lib, "Band", "Tunes (2020)", "03 Melody (Radio Remix).mp3")
    assert (found.title, found.version, found.match) == (
        "Melody (Radio Remix)", ["remix:radio"], "user_details",
    )  # fmt: skip
    assert found.musicorg_id == waiting.musicorg_id
    assert asked == [("remix:radio",)]  # and its lyrics are looked up as that remix's

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == ["Band/Unsorted/Melody R.mp3"]
    back = song(lib, "Band", "Unsorted", "Melody R.mp3")
    assert (back.title, back.version, back.match) == ("Melody R", ["remix"], "unconfirmed")
    assert item_state(index, iid) == ("matched_user", [])


def test_a_found_title_that_names_no_version_is_pointed_out(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    album_lookups: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(pipeline, "EXTRAS", [])
    iid = own_rip(index, rips, samples["mp3"], "Band - Melody R", state_="review")
    # Not pointed out: a rip that only says "explicit", which isn't another recording.
    own_rip(index, rips, samples["m4a"], "Band - Other (Explicit)", video_id=VIDEO_B,
            title="Other Melody")  # fmt: skip
    adopt_unconfirmed(lib, index)

    index.set_state(iid, "matched_user", [])  # the owner says it is the plain official track
    plan = pipeline.plan_adopt(lib, index, matched=True)
    assert (plan.summary["with_details"], plan.summary["version_not_in_title"]) == (2, 1)
    lines = pipeline.describe(plan)
    marked = [line for line in lines if "this title names no version" in line]
    assert len(lines) == 2 and len(marked) == 1
    assert "Band - Melody R.mp3" in marked[0]
    assert marked[0].endswith("[the rip says remix; this title names no version]")
    cli._print_plan_summary(plan)
    assert "1 of those: the rip's name says remix, live or another version" in (
        capsys.readouterr().out
    )
    batch_id = apply_and_run(lib, index, plan)

    # The decision stands: the found title, and the version tag the copy had is gone.
    assert [name for name in music_files(lib) if name.endswith(".mp3")] == [
        "Band/Tunes (2020)/03 Melody.mp3"
    ]
    found = song(lib, "Band", "Tunes (2020)", "03 Melody.mp3")
    assert (found.title, found.version, found.match) == ("Melody", None, "user_details")

    pipeline.undo(lib, batch_id)
    back = song(lib, "Band", "Unsorted", "Melody R.mp3")
    assert (back.title, back.version, back.match) == ("Melody R", ["remix"], "unconfirmed")


def test_an_official_title_that_only_names_a_remaster_is_pointed_out_too(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    album_lookups: list[tuple[str, str]],
) -> None:
    """The rip says remix; the official title chosen says "(2011 Remaster)". A remaster
    is the same recording, so the title still names no other version, and the plan says
    so. A rip that names only a remaster itself has nothing to point out."""
    remix = own_rip(index, rips, samples["mp3"], "Band - Melody R",
                    title="Melody (2011 Remaster)", state_="matched_user")  # fmt: skip
    own_rip(index, rips, samples["m4a"], "Band - Other (2009 Remaster)", video_id=VIDEO_B,
            title="Other Melody", state_="matched_user")  # fmt: skip

    plan = pipeline.plan_adopt(lib, index, matched=True)

    assert (plan.summary["with_details"], plan.summary["version_not_in_title"]) == (2, 1)
    marked = {op.item_id: op.params.get("version_not_in_title") for op in plan.operations}
    assert marked[remix] == ["remix"]
    assert [m for item, m in marked.items() if item != remix] == [None]


def test_a_plain_title_fix_takes_the_version_off_a_copy(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    iid = adopt_item(index, rips, samples["mp3"], "Band - Melody R")
    waiting_item(index, iid, "review")
    adopt_unconfirmed(lib, index)
    assert song(lib, "Band", "Unsorted", "Melody R.mp3").version == ["remix"]

    index.set_state(iid, "only_copy", [])  # the owner: it isn't a remix, and it's called this
    with state.edit(lib.paths.state_file) as st:
        st.data.setdefault("decisions", {})[iid] = {
            "decision": "only_copy", "title_fix": "Melody"}  # fmt: skip
    batch_id = apply_and_run(lib, index, pipeline.plan_adopt(lib, index))

    assert music_files(lib) == ["Band/Unsorted/Melody.mp3"]
    fixed = song(lib, "Band", "Unsorted", "Melody.mp3")
    assert (fixed.title, fixed.version, fixed.match) == ("Melody", None, "manual")

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == ["Band/Unsorted/Melody R.mp3"]
    back = song(lib, "Band", "Unsorted", "Melody R.mp3")
    assert (back.title, back.version, back.match) == ("Melody R", ["remix"], "unconfirmed")


def test_only_copy_decided_later_keeps_the_copys_version(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """Not found anywhere, the owner decides: it keeps the name they had on it."""
    iid = adopt_item(index, rips, samples["mp3"], "Band - Melody R")
    waiting_item(index, iid, "review")
    adopt_unconfirmed(lib, index)

    index.set_state(iid, "only_copy", [])
    plan = pipeline.plan_adopt(lib, index)
    assert plan.operations[0].target == "Music/Band/Unsorted/Melody R.mp3"
    batch_id = apply_and_run(lib, index, plan)

    assert music_files(lib) == ["Band/Unsorted/Melody R.mp3"]
    kept = song(lib, "Band", "Unsorted", "Melody R.mp3")
    assert (kept.title, kept.version) == ("Melody R", ["remix"])
    assert (kept.only_copy, kept.match) == (True, None)
    assert item_state(index, iid) == ("adopted", [])

    pipeline.undo(lib, batch_id)
    back = song(lib, "Band", "Unsorted", "Melody R.mp3")
    assert (back.title, back.version, back.match) == ("Melody R", ["remix"], "unconfirmed")
    assert item_state(index, iid) == ("only_copy", [])


def test_a_decision_without_fixes_keeps_the_title_the_owner_typed(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    iid = adopt_item(index, rips, samples["mp3"], "Band - Melody R")
    waiting_item(index, iid, "review")
    adopt_unconfirmed(lib, index)
    # The owner corrects the copy in Edit Details, then decides the rip is an only copy.
    edit(lib, index, "Music/Band/Unsorted/Melody R.mp3",
         changes={"title": "Melody (My Own Name)", "artist": "The Band"})  # fmt: skip
    mine = "The Band/Unsorted/Melody (My Own Name).mp3"
    assert music_files(lib) == [mine]
    before = tags.read_tags(lib.paths.music / mine)

    index.set_state(iid, "only_copy", [])
    plan = pipeline.plan_adopt(lib, index)
    (op,) = plan.operations
    assert op.target == f"Music/{mine}" and "retitle_from" not in op.params
    batch_id = apply_and_run(lib, index, plan)

    # Decided, and nothing else: not retitled, not moved, its version tag as it was.
    assert music_files(lib) == [mine]
    kept = tags.read_tags(lib.paths.music / mine)
    assert (kept.title, kept.artist, kept.version) == (
        "Melody (My Own Name)", "The Band", before.version,
    )  # fmt: skip
    assert (kept.only_copy, kept.match) == (True, None)
    assert item_state(index, iid) == ("adopted", [])
    assert [(t["rel_path"], t["match"]) for t in index.library_tracks()] == [
        (f"Music/{mine}", None)
    ]

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == [mine]
    back = tags.read_tags(lib.paths.music / mine)
    assert (back.title, back.only_copy, back.match) == (
        "Melody (My Own Name)", None, "unconfirmed",
    )  # fmt: skip
    assert item_state(index, iid) == ("only_copy", [])


def test_a_decision_without_fixes_gives_an_old_copy_its_version_back(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """A copy made before 2026-10-04 still has the title the engine wrote, with the
    version left out. The owner never typed that, so the decision puts it right."""
    iid = adopt_item(index, rips, samples["mp3"], "Band - Melody R", confidence=0.95)
    copy = old_copy(lib, index, rips / "Band - Melody R.mp3", "Melody")
    copy.with_suffix(".lrc").write_text(SYNCED, encoding="utf-8")
    first = tags.read_tags(copy)

    plan = pipeline.plan_adopt(lib, index)
    (op,) = plan.operations
    assert op.target == "Music/Band/Unsorted/Melody R.mp3"
    assert op.params["retitle_from"] == "Melody"
    batch_id = apply_and_run(lib, index, plan)

    assert music_files(lib) == ["Band/Unsorted/Melody R.lrc", "Band/Unsorted/Melody R.mp3"]
    fixed = song(lib, "Band", "Unsorted", "Melody R.mp3")
    assert (fixed.title, fixed.version) == ("Melody R", ["remix"])
    assert (fixed.only_copy, fixed.match, fixed.musicorg_id) == (True, None, first.musicorg_id)
    assert [(t["rel_path"], t["title"]) for t in index.library_tracks()] == [
        ("Music/Band/Unsorted/Melody R.mp3", "Melody R")
    ]
    assert item_state(index, iid) == ("adopted", [])

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == ["Band/Unsorted/Melody.lrc", "Band/Unsorted/Melody.mp3"]
    back = tags.read_tags(copy)
    assert (back.title, back.version, back.match) == ("Melody", None, "unconfirmed")
    assert [(t["rel_path"], t["match"]) for t in index.library_tracks()] == [
        ("Music/Band/Unsorted/Melody.mp3", "unconfirmed")
    ]


def test_an_upgrade_whose_move_fails_carries_on_when_tried_again(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tags are written, then the move is refused (on Windows: the file is open in
    another app). The job is tried again, and must finish the same copy: before, it
    copied the rip in a second time."""
    iid = adopt_item(index, rips, samples["mp3"], "Band - Melody R", confidence=0.95)
    copy = old_copy(lib, index, rips / "Band - Melody R.mp3", "Melody")
    refuse_one_move(monkeypatch)
    batch_id = apply_and_run(lib, index, pipeline.plan_adopt(lib, index))

    (job,) = jobs(lib)
    assert (job["state"], job["attempts"]) == ("done", 2)
    assert music_files(lib) == ["Band/Unsorted/Melody R.mp3"]  # one copy, renamed
    fixed = song(lib, "Band", "Unsorted", "Melody R.mp3")
    assert (fixed.title, fixed.version, fixed.only_copy) == ("Melody R", ["remix"], True)
    assert [t["rel_path"] for t in index.library_tracks()] == ["Music/Band/Unsorted/Melody R.mp3"]
    assert item_state(index, iid) == ("adopted", [])

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == ["Band/Unsorted/Melody.mp3"]
    back = tags.read_tags(copy)
    assert (back.title, back.version, back.match) == ("Melody", None, "unconfirmed")


def refuse_one_move(monkeypatch: pytest.MonkeyPatch) -> None:
    """The next `fileops.move` fails as a move does when the file is in use; the ones
    after it work."""
    move, refusals = fileops.move, [1]

    def refuse_once(*args: Any, **kw: Any) -> Path:
        if refusals:
            refusals.pop()
            raise FileOperationError("That file is open in another app.")
        return move(*args, **kw)

    monkeypatch.setattr(fileops, "move", refuse_once)


def test_a_found_song_whose_move_fails_is_finished_when_tried_again(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    album_lookups: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "EXTRAS", [])
    iid = own_rip(index, rips, samples["mp3"], "Band - Melody R",
                  title="Melody (Radio Remix)", state_="review")  # fmt: skip
    adopt_unconfirmed(lib, index)
    index.set_state(iid, "matched_user", [])
    refuse_one_move(monkeypatch)
    batch_id = apply_and_run(lib, index, pipeline.plan_adopt(lib, index, matched=True))

    (job,) = [j for j in jobs(lib) if j["batch_id"] == batch_id]
    assert (job["state"], job["attempts"]) == ("done", 2)
    assert music_files(lib) == ["Band/Tunes (2020)/03 Melody (Radio Remix).mp3"]
    found = song(lib, "Band", "Tunes (2020)", "03 Melody (Radio Remix).mp3")
    assert (found.version, found.match) == (["remix:radio"], "user_details")
    assert [t["rel_path"] for t in index.library_tracks()] == [
        "Music/Band/Tunes (2020)/03 Melody (Radio Remix).mp3"
    ]
    assert item_state(index, iid) == ("adopted", [])

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == ["Band/Unsorted/Melody R.mp3"]
    back = song(lib, "Band", "Unsorted", "Melody R.mp3")
    assert (back.title, back.version, back.match) == ("Melody R", ["remix"], "unconfirmed")


def stop_after_one_move(monkeypatch: pytest.MonkeyPatch) -> None:
    """The next `fileops.move` moves its file, and then the engine "stops": the journal
    has the move, and nothing that should follow it has happened."""
    move, stops = fileops.move, [1]

    def stop_after_one(*args: Any, **kw: Any) -> Path:
        landed = move(*args, **kw)
        if stops:
            stops.pop()
            raise RuntimeError("the engine stopped here")
        return landed

    monkeypatch.setattr(fileops, "move", stop_after_one)


def refuse_the_lyrics_move_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The song moves; the move of its `.lrc`, which follows, is refused the first time."""
    move, refusals = fileops.move, [1]

    def refuse_once(b: Any, lib_file: Any, rel_target: Any) -> Path:
        if refusals and Path(lib_file).suffix == ".lrc":
            refusals.pop()
            raise FileOperationError("That file is open in another app.")
        return move(b, lib_file, rel_target)

    monkeypatch.setattr(fileops, "move", refuse_once)


def stop_in_the_sidecar_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """The copy is moved and the index has its new row; the engine stops before the
    job's last steps (its sidecars, and marking the rip adopted)."""
    write, stops = pipeline._write_sidecars, [1]

    def stop_once(*args: Any, **kw: Any) -> None:
        if stops:
            stops.pop()
            raise RuntimeError("the engine stopped here")
        write(*args, **kw)

    monkeypatch.setattr(pipeline, "_write_sidecars", stop_once)


@pytest.mark.parametrize(
    "stop", [stop_after_one_move, refuse_the_lyrics_move_once, stop_in_the_sidecar_step]
)
def test_a_found_song_whose_upgrade_stops_after_its_move_is_finished_when_run_again(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    album_lookups: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    stop: Any,
) -> None:
    """The copy is retagged and moved, and the job stops somewhere after that. Run
    again, it must carry on with the file it moved: the journal says where that went.
    Before, it looked where the index said, found nothing, and copied the rip in a
    second time under a new id."""
    monkeypatch.setattr(pipeline, "EXTRAS", [lambda query: pipeline.Extras(synced_lyrics=SYNCED)])
    iid = own_rip(index, rips, samples["mp3"], "Band - Melody R",
                  title="Melody (Radio Remix)", state_="review")  # fmt: skip
    adopt_unconfirmed(lib, index)
    waiting = lib.paths.music / "Band" / "Unsorted" / "Melody R.mp3"
    waiting.with_suffix(".lrc").write_text(SYNCED, encoding="utf-8")
    track_id = tags.read_tags(waiting).musicorg_id
    index.set_state(iid, "matched_user", [])
    stop(monkeypatch)
    batch_id = apply_and_run(lib, index, pipeline.plan_adopt(lib, index, matched=True))

    (job,) = [j for j in jobs(lib) if j["batch_id"] == batch_id]
    assert (job["state"], job["attempts"]) == ("done", 2)
    assert music_files(lib) == [  # one copy, and its lyrics beside it
        "Band/Tunes (2020)/03 Melody (Radio Remix).lrc",
        "Band/Tunes (2020)/03 Melody (Radio Remix).mp3",
    ]
    found = song(lib, "Band", "Tunes (2020)", "03 Melody (Radio Remix).mp3")
    assert (found.musicorg_id, found.match, found.version) == (
        track_id, "user_details", ["remix:radio"],
    )  # fmt: skip
    assert [(t["rel_path"], t["musicorg_id"]) for t in index.library_tracks()] == [
        ("Music/Band/Tunes (2020)/03 Melody (Radio Remix).mp3", track_id)
    ]
    assert item_state(index, iid) == ("adopted", [])

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == ["Band/Unsorted/Melody R.lrc", "Band/Unsorted/Melody R.mp3"]
    back = tags.read_tags(waiting)
    assert (back.title, back.version, back.match, back.musicorg_id) == (
        "Melody R", ["remix"], "unconfirmed", track_id,
    )  # fmt: skip
    assert [(t["rel_path"], t["match"]) for t in index.library_tracks()] == [
        ("Music/Band/Unsorted/Melody R.mp3", "unconfirmed")
    ]
    assert item_state(index, iid) == ("matched_user", [])


@pytest.mark.parametrize("stop", [stop_after_one_move, refuse_the_lyrics_move_once])
def test_an_only_copy_upgrade_that_stops_after_its_move_is_finished_when_run_again(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    stop: Any,
) -> None:
    """The same for a rip decided as an only copy, whose old copy is being given its
    version back: before, the job run again ended `needs_review`, left the lyrics file
    behind, and the next decision copied the rip in a second time."""
    iid = adopt_item(index, rips, samples["mp3"], "Band - Melody R", confidence=0.95)
    copy = old_copy(lib, index, rips / "Band - Melody R.mp3", "Melody")
    copy.with_suffix(".lrc").write_text(SYNCED, encoding="utf-8")
    track_id = tags.read_tags(copy).musicorg_id
    stop(monkeypatch)
    batch_id = apply_and_run(lib, index, pipeline.plan_adopt(lib, index))

    (job,) = jobs(lib)
    assert (job["state"], job["attempts"]) == ("done", 2)
    assert music_files(lib) == ["Band/Unsorted/Melody R.lrc", "Band/Unsorted/Melody R.mp3"]
    fixed = song(lib, "Band", "Unsorted", "Melody R.mp3")
    assert (fixed.title, fixed.version, fixed.only_copy, fixed.musicorg_id) == (
        "Melody R", ["remix"], True, track_id,
    )  # fmt: skip
    assert [(t["rel_path"], t["match"]) for t in index.library_tracks()] == [
        ("Music/Band/Unsorted/Melody R.mp3", None)
    ]
    assert item_state(index, iid) == ("adopted", [])

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == ["Band/Unsorted/Melody.lrc", "Band/Unsorted/Melody.mp3"]
    back = tags.read_tags(copy)
    assert (back.title, back.version, back.match) == ("Melody", None, "unconfirmed")
    assert [(t["rel_path"], t["match"]) for t in index.library_tracks()] == [
        ("Music/Band/Unsorted/Melody.mp3", "unconfirmed")
    ]
    assert item_state(index, iid) == ("only_copy", [])


def test_the_index_knows_where_a_copy_is_the_moment_it_has_moved(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    album_lookups: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The song moves, and its `.lrc` can't follow however often the job is tried. The
    job isn't done, but the index already points at the copy's new place, so whatever
    looks for the rip's copy later finds it: there is one copy, not one per try."""
    monkeypatch.setattr(pipeline, "EXTRAS", [])
    iid = own_rip(index, rips, samples["mp3"], "Band - Melody R",
                  title="Melody (Radio Remix)", state_="review")  # fmt: skip
    adopt_unconfirmed(lib, index)
    waiting = lib.paths.music / "Band" / "Unsorted" / "Melody R.mp3"
    waiting.with_suffix(".lrc").write_text(SYNCED, encoding="utf-8")
    track_id = tags.read_tags(waiting).musicorg_id
    index.set_state(iid, "matched_user", [])
    move = fileops.move

    def never_the_lyrics(b: Any, lib_file: Any, rel_target: Any) -> Path:
        if Path(lib_file).suffix == ".lrc":
            raise FileOperationError("That file is open in another app.")
        return move(b, lib_file, rel_target)

    monkeypatch.setattr(fileops, "move", never_the_lyrics)
    batch_id = apply_and_run(lib, index, pipeline.plan_adopt(lib, index, matched=True))

    (job,) = [j for j in jobs(lib) if j["batch_id"] == batch_id]
    assert job["state"] == "queued" and job["attempts"] > 1  # tried again, and still waiting
    assert music_files(lib) == [
        "Band/Tunes (2020)/03 Melody (Radio Remix).mp3",
        "Band/Unsorted/Melody R.lrc",
    ]
    assert [(t["rel_path"], t["musicorg_id"]) for t in index.library_tracks()] == [
        ("Music/Band/Tunes (2020)/03 Melody (Radio Remix).mp3", track_id)
    ]

    monkeypatch.setattr(fileops, "move", move)
    pipeline.undo(lib, batch_id)  # cancels the waiting job, and takes back what it did
    assert music_files(lib) == ["Band/Unsorted/Melody R.lrc", "Band/Unsorted/Melody R.mp3"]
    back = tags.read_tags(waiting)
    assert (back.title, back.match, back.musicorg_id) == ("Melody R", "unconfirmed", track_id)
    assert [(t["rel_path"], t["match"]) for t in index.library_tracks()] == [
        ("Music/Band/Unsorted/Melody R.mp3", "unconfirmed")
    ]


def test_a_rip_whose_copy_was_upgraded_before_is_not_copied_in_again(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """An earlier job upgraded the rip's copy and never got to its last step (it was
    given up, or the engine stopped for good): the copy says it's decided, the rip
    doesn't say it's adopted. A new plan must carry on with the copy that is there."""
    iid = adopt_item(index, rips, samples["mp3"], "Band - Melody R", confidence=0.95)
    waiting_item(index, iid, "review")
    adopt_unconfirmed(lib, index)
    index.set_state(iid, "only_copy", [])
    apply_and_run(lib, index, pipeline.plan_adopt(lib, index))
    copy = lib.paths.music / "Band" / "Unsorted" / "Melody R.mp3"
    track_id = tags.read_tags(copy).musicorg_id
    assert tags.read_tags(copy).only_copy is True
    index.set_state(iid, "only_copy", [])  # as if the job's last step never happened

    plan = pipeline.plan_adopt(lib, index)
    assert plan.summary["adopts"] == 1
    batch_id = apply_and_run(lib, index, plan)

    assert music_files(lib) == ["Band/Unsorted/Melody R.mp3"]  # one copy, the same one
    again = tags.read_tags(copy)
    assert (again.musicorg_id, again.title, again.version, again.only_copy) == (
        track_id, "Melody R", ["remix"], True,
    )  # fmt: skip
    assert item_state(index, iid) == ("adopted", [])
    assert [t["musicorg_id"] for t in index.library_tracks()] == [track_id]

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == ["Band/Unsorted/Melody R.mp3"]
    assert item_state(index, iid) == ("only_copy", [])


def test_a_found_songs_lyrics_file_moves_with_it(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    album_lookups: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A copy's `.lrc` goes with it when it's found and renamed (before 2026-10-04 it
    was left behind under the old name). Undo brings both back."""
    monkeypatch.setattr(pipeline, "EXTRAS", [])
    iid = own_rip(index, rips, samples["mp3"], "Band - Melody R",
                  title="Melody (Radio Remix)", state_="review")  # fmt: skip
    adopt_unconfirmed(lib, index)
    waiting = lib.paths.music / "Band" / "Unsorted" / "Melody R.mp3"
    waiting.with_suffix(".lrc").write_text(SYNCED, encoding="utf-8")
    before = tags.to_record(tags.read_tags(waiting))
    index.set_state(iid, "matched_user", [])
    batch_id = apply_and_run(lib, index, pipeline.plan_adopt(lib, index, matched=True))

    found = lib.paths.music / "Band" / "Tunes (2020)" / "03 Melody (Radio Remix).mp3"
    assert music_files(lib) == [
        "Band/Tunes (2020)/03 Melody (Radio Remix).lrc",
        "Band/Tunes (2020)/03 Melody (Radio Remix).mp3",
    ]
    assert found.with_suffix(".lrc").read_text(encoding="utf-8") == SYNCED

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == ["Band/Unsorted/Melody R.lrc", "Band/Unsorted/Melody R.mp3"]
    assert tags.to_record(tags.read_tags(waiting)) == before  # its tags, exactly
    assert waiting.with_suffix(".lrc").read_text(encoding="utf-8") == SYNCED


def test_an_upgrade_onto_a_name_that_is_taken_gets_a_number(
    lib: Library, index: Index, rips: Path, two_mp3s: tuple[Path, Path]
) -> None:
    """A decision with no fixes gives an old copy its version back, and another song
    already has that name: " (2)", never an overwrite, and undo puts it back."""
    low, high = two_mp3s
    shutil.copyfile(low, rips / "Band - Melody R.mp3")
    iid = add_item(index, "Band - Melody R", state="only_copy", seconds=3)
    copy = old_copy(lib, index, rips / "Band - Melody R.mp3", "Melody")
    (rips / "other").mkdir()
    shutil.copyfile(high, rips / "other" / "Melody R.mp3")
    in_the_way = library_copy(lib, index, rips / "other" / "Melody R.mp3", "Melody R",
                              version=["remix"])  # fmt: skip
    taken = in_the_way.read_bytes()

    batch_id = apply_and_run(lib, index, pipeline.plan_adopt(lib, index))

    assert music_files(lib) == ["Band/Unsorted/Melody R (2).mp3", "Band/Unsorted/Melody R.mp3"]
    assert in_the_way.read_bytes() == taken
    landed = lib.paths.music / "Band" / "Unsorted" / "Melody R (2).mp3"
    assert tags.audio_hash(landed) == tags.audio_hash(low)
    fixed = tags.read_tags(landed)
    assert (fixed.title, fixed.version, fixed.only_copy) == ("Melody R", ["remix"], True)
    assert item_state(index, iid) == ("adopted", [])
    assert sorted(tracks_by_path(index)) == [
        f"{UNSORTED}/Melody R (2).mp3",
        f"{UNSORTED}/Melody R.mp3",
    ]

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == ["Band/Unsorted/Melody R.mp3", "Band/Unsorted/Melody.mp3"]
    assert in_the_way.read_bytes() == taken
    back = tags.read_tags(copy)
    assert (back.title, back.version, back.match) == ("Melody", None, "unconfirmed")


# ---- plan tidy: a version put back on a copy that lost it (2026-10-04) ------------------
#
# Before this, a copy named from its rip left the version out of its title. `plan tidy`
# gives each such copy its name back: title, file name and MUSICORG_VERSION.

UNSORTED = "Music/Band/Unsorted"


def library_copy(lib: Library, index: Index, rip: Path, title: str, **details: Any) -> Path:
    """A copy of a rip in the library, with the tags given: `old_copy` with a say in
    its details (its match, a version tag, where it says it came from)."""
    found = {"title": title, "artist": "Band", "musicorg_id": tags.new_track_id(),
             "source": "rip_copy", "source_format": "mp3", "match": "unconfirmed",
             "origin_path": str(rip), "schema": 1, **details}  # fmt: skip
    with fileops.batch(lib, "demo") as b:
        staged = fileops.stage_copy(b, rip)
        fileops.write_tags(b, staged, tags.TrackTags(**found))
        copy = fileops.commit(b, staged, Path("Band", "Unsorted", f"{title}{rip.suffix}"))
    index.put_library_tracks([pipeline._track_row(lib, copy, tags.read_tags(copy), 3.0)])
    return copy


def waiting_rip(index: Index, rips: Path, source: Path, name: str, confidence: float = 0.95) -> str:
    """A rip still waiting in review, in an index scanned before the words of a version
    were kept (as the owner's is)."""
    iid = adopt_item(index, rips, source, name)
    waiting_item(index, iid, "review", confidence)
    item = index.item(iid)
    assert item is not None
    del item["parsed_json"]["version_words"]
    index.put_items([item])
    return iid


def lost_pair(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> tuple[Path, Path]:
    """The library as the batch of 1 Oct left it: the rip "Melody R" copied in as
    "Melody", and the real "Melody" pushed to "Melody (2)"."""
    waiting_rip(index, rips, samples["mp3"], "Band - Melody R")
    waiting_rip(index, rips, samples["mp3"], "Band - Melody")
    remix = old_copy(lib, index, rips / "Band - Melody R.mp3", "Melody")
    original = old_copy(lib, index, rips / "Band - Melody.mp3", "Melody")
    assert (remix.name, original.name) == ("Melody.mp3", "Melody (2).mp3")
    return remix, original


def tracks_by_path(index: Index) -> dict[str, dict[str, Any]]:
    return {t["rel_path"]: t for t in index.library_tracks()}


def test_tidy_gives_a_copy_back_the_version_its_name_lost(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    remix, _ = lost_pair(lib, index, rips, samples)
    remix.with_suffix(".lrc").write_text(SYNCED, encoding="utf-8")
    first = tags.read_tags(remix)
    track_id = str(first.musicorg_id)
    listening.set_favourite(lib, track_id, True)
    (playlist,) = listening.create_playlist(lib, "Remixes")
    listening.set_playlist_tracks(lib, playlist["id"], [track_id])
    rip = rips / "Band - Melody R.mp3"
    rip_bytes = rip.read_bytes()

    plan = pipeline.plan_tidy(lib, index)
    assert (plan.summary["versions"], plan.summary["renames"], plan.summary["duplicates"]) == (
        1, 0, 0,
    )  # fmt: skip
    assert plan.summary["versions_skipped"] == {}
    (op,) = plan.operations  # the original keeps its " (2)" for now: its name isn't free yet
    assert (op.action, op.target) == ("rename", f"{UNSORTED}/Melody R.mp3")
    assert op.params["changes"] == {"title": "Melody R", "version": ["remix"]}
    assert (op.params["from_title"], op.params["from_version"]) == ("Melody", None)
    assert pipeline.describe(plan)[0].endswith(
        f"rename   {UNSORTED}/Melody.mp3  →  {UNSORTED}/Melody R.mp3"
        "  (title: Melody R, version: remix)"
    )
    cli._print_plan_summary(plan)
    printed = capsys.readouterr().out
    assert "1 song(s) to give back the version their name lost" in printed
    assert "0 song(s) to rename with your preferred names" in printed
    apply_and_run(lib, index, plan)

    assert music_files(lib) == [
        "Band/Unsorted/Melody (2).mp3",
        "Band/Unsorted/Melody R.lrc",  # its lyrics moved with it
        "Band/Unsorted/Melody R.mp3",
    ]
    fixed = song(lib, "Band", "Unsorted", "Melody R.mp3")
    assert (fixed.title, fixed.version, fixed.match) == ("Melody R", ["remix"], "unconfirmed")
    assert (fixed.musicorg_id, fixed.origin_path) == (track_id, str(rip))
    assert tags.audio_hash(lib.paths.music / "Band" / "Unsorted" / "Melody R.mp3") == (
        tags.audio_hash(rip)
    )
    rows = tracks_by_path(index)
    assert sorted(rows) == [f"{UNSORTED}/Melody (2).mp3", f"{UNSORTED}/Melody R.mp3"]
    row = rows[f"{UNSORTED}/Melody R.mp3"]
    assert (row["title"], row["musicorg_id"]) == ("Melody R", track_id)
    assert (row["match"], row["origin_path"]) == ("unconfirmed", str(rip))  # still its rip's copy
    # The same song throughout: the favourite and the playlist still find it.
    heard = listening.get(lib)
    assert heard["favourites"] == [track_id]
    assert heard["playlists"][0]["track_ids"] == [track_id]
    shown = {t["track_id"]: t for t in browse.tracks(lib, index)}[track_id]
    assert (shown["path"], shown["title"], shown["lyrics"]) == (
        f"{UNSORTED}/Melody R.mp3", "Melody R", "synced",
    )  # fmt: skip
    assert rip.read_bytes() == rip_bytes  # the rip is only ever read
    assert {state_ for state_, _ in (item_state(index, i["id"]) for i in index.items())} == {
        "review"
    }

    # A second tidy gives the original its plain name back, now that it's free.
    second = pipeline.plan_tidy(lib, index)
    assert (second.summary["versions"], second.summary["numbers_dropped"]) == (0, 1)
    assert second.summary["renames"] == 0  # not a preferred name, and not counted as one
    (op,) = second.operations
    assert op.source is not None
    assert (op.source.path, op.target) == (f"{UNSORTED}/Melody (2).mp3", f"{UNSORTED}/Melody.mp3")
    cli._print_plan_summary(second)
    printed = capsys.readouterr().out
    assert '1 song(s) to give their plain name back: the " (2)" on the file is no longer' in printed
    assert "0 song(s) to rename with your preferred names" in printed
    apply_and_run(lib, index, second)
    assert music_files(lib) == [
        "Band/Unsorted/Melody R.lrc",
        "Band/Unsorted/Melody R.mp3",
        "Band/Unsorted/Melody.mp3",
    ]
    plain = song(lib, "Band", "Unsorted", "Melody.mp3")
    assert (plain.title, plain.version) == ("Melody", None)
    assert plain.origin_path == str(rips / "Band - Melody.mp3")
    assert sorted(tracks_by_path(index)) == [f"{UNSORTED}/Melody R.mp3", f"{UNSORTED}/Melody.mp3"]

    # A third has nothing to do.
    third = pipeline.plan_tidy(lib, index)
    assert third.operations == []
    assert (third.summary["versions"], third.summary["versions_skipped"]) == (0, {})


def test_undo_of_a_version_put_back(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    remix, original = lost_pair(lib, index, rips, samples)
    remix.with_suffix(".lrc").write_text(SYNCED, encoding="utf-8")
    before = tags.to_record(tags.read_tags(remix))
    rows_before = tracks_by_path(index)
    batch_id = apply_and_run(lib, index, pipeline.plan_tidy(lib, index))
    assert not remix.exists()

    undone = pipeline.undo(lib, batch_id)

    assert all(step.status == "done" for step in undone.steps)
    assert music_files(lib) == [
        "Band/Unsorted/Melody (2).mp3",
        "Band/Unsorted/Melody.lrc",
        "Band/Unsorted/Melody.mp3",
    ]
    back = tags.read_tags(remix)
    assert tags.to_record(back) == before  # exactly: the title, and no version tag again
    assert (back.title, back.version) == ("Melody", None)
    assert remix.with_suffix(".lrc").read_text(encoding="utf-8") == SYNCED
    rows = tracks_by_path(index)
    assert sorted(rows) == sorted(rows_before)
    for rel, row in rows.items():
        assert (row["title"], row["match"], row["origin_path"], row["musicorg_id"]) == (
            "Melody", "unconfirmed", rows_before[rel]["origin_path"],
            rows_before[rel]["musicorg_id"],
        )  # fmt: skip
    # And tidy would do the same again.
    assert pipeline.plan_tidy(lib, index).summary["versions"] == 1


def test_what_tidy_leaves_alone(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """Only a copy still named after its rip, whose file doesn't say which version it
    is, is put right. Everything is decided from the file's own tags."""
    for name in ("Lost Boy R", "Plain Day R", "Found R", "Chosen R", "Fixed R", "Tune R",
                 "Tagged R", "Kept R", "Just A Song"):  # fmt: skip
        waiting_rip(index, rips, samples["mp3"], f"Band - {name}")
    waiting_rip(index, rips, samples["mp3"], "zz unclear 7 (Somebody Remix)", confidence=0.3)

    def rip(name: str) -> Path:
        return rips / f"{name}.mp3"

    # The owner retitled this one by hand, and the title still shows the version.
    by_hand = library_copy(lib, index, rip("Band - Lost Boy R"), "Lost Boy R")
    # This one too, to a title that names none: they may have meant it.
    renamed = library_copy(lib, index, rip("Band - Plain Day R"), "A Different Name")
    # Found songs, and one the owner gave fixes for: their titles aren't the rip's.
    found = library_copy(lib, index, rip("Band - Found R"), "Found", match="auto_details")
    chosen = library_copy(lib, index, rip("Band - Chosen R"), "Chosen", match="user_details")
    fixed = library_copy(lib, index, rip("Band - Fixed R"), "Fixed", match="manual",
                         only_copy=True)  # fmt: skip
    # A download that replaced a rip.
    download = library_copy(lib, index, rip("Band - Tune R"), "Tune", match="auto_exact",
                            source="youtube_music", source_id=VIDEO_A)  # fmt: skip
    # It already says which version it is; and a song with no version at all.
    tagged = library_copy(lib, index, rip("Band - Tagged R"), "Tagged", version=["live"])
    plain = library_copy(lib, index, rip("Band - Just A Song"), "Just A Song")
    # Its rip is no longer in the index, so what the rip names can't be checked.
    orphan = library_copy(lib, index, rip("Band - Kept R"), "Gone",
                          origin_path=str(rips / "elsewhere" / "Band - Gone R.mp3"))  # fmt: skip
    # Its rip's name couldn't be read with confidence: a copy made today gets no tag.
    unclear = library_copy(lib, index, rip("zz unclear 7 (Somebody Remix)"),
                           "zz unclear 7 (Somebody Remix)")  # fmt: skip
    # The index's own note of a copy's match can be empty while its tags say it has
    # official details (the column came later): the tags decide.
    rows = tracks_by_path(index)
    index.put_library_tracks([{**rows[f"{UNSORTED}/Found.mp3"], "match": None},
                              {**rows[f"{UNSORTED}/Chosen.mp3"], "match": None}])  # fmt: skip
    untouched = [renamed, found, chosen, fixed, download, tagged, plain, orphan, unclear]
    before = {path: tags.to_record(tags.read_tags(path)) for path in untouched}
    files = music_files(lib)

    plan = pipeline.plan_tidy(lib, index)

    assert (plan.summary["versions"], plan.summary["renames"]) == (1, 0)
    assert plan.summary["versions_skipped"] == {
        "title_names_no_version": 1, "rip_not_indexed": 1, "names_not_trusted": 1,
    }  # fmt: skip
    (op,) = plan.operations
    assert op.source is not None
    assert op.source.path == op.target == f"{UNSORTED}/Lost Boy R.mp3"  # the tag only
    assert op.params["changes"] == {"version": ["remix"]}
    shown = pipeline.describe(plan)
    assert shown[0].endswith(f"retag    {UNSORTED}/Lost Boy R.mp3  (version: remix)")
    # Which copies were left alone, and why, so the owner can look at each.
    assert plan.summary["versions_left"] == [
        {"path": f"{UNSORTED}/A Different Name.mp3", "why": "title_names_no_version"},
        {"path": f"{UNSORTED}/Gone.mp3", "why": "rip_not_indexed"},
        {"path": f"{UNSORTED}/zz unclear 7 (Somebody Remix).mp3", "why": "names_not_trusted"},
    ]
    assert shown[1:] == [
        f"       left     {UNSORTED}/A Different Name.mp3  (its rip names a version; its "
        "title was changed by hand and names none)",
        f"       left     {UNSORTED}/Gone.mp3  (its rip names a version; the rip isn't in "
        "the index any more, so it can't be checked)",
        f"       left     {UNSORTED}/zz unclear 7 (Somebody Remix).mp3  (its rip names a "
        "version; the rip's name was too hard to read to be sure of it)",
    ]
    tag_batch = apply_and_run(lib, index, plan)

    assert music_files(lib) == files  # nothing renamed
    assert (tags.read_tags(by_hand).title, tags.read_tags(by_hand).version) == (
        "Lost Boy R", ["remix"],
    )  # fmt: skip
    for path, record in before.items():
        assert tags.to_record(tags.read_tags(path)) == record, path.name
    again = pipeline.plan_tidy(lib, index)
    assert again.operations == [] and again.summary["versions"] == 0

    # Undo of a repair that only wrote the tag: the tag goes, and nothing else changes.
    undone = pipeline.undo(lib, tag_batch)
    assert [(step.action, step.status) for step in undone.steps] == [("write_tags", "done")]
    assert (tags.read_tags(by_hand).title, tags.read_tags(by_hand).version) == ("Lost Boy R", None)
    assert music_files(lib) == files
    assert pipeline.plan_tidy(lib, index).summary["versions"] == 1


def test_a_title_changed_after_the_plan_is_left_alone(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    remix, _ = lost_pair(lib, index, rips, samples)
    plan = pipeline.plan_tidy(lib, index)
    pipeline.apply(lib, index, plan.plan_id)
    with fileops.batch(lib, "demo") as b:  # the owner types a title of their own meanwhile
        fileops.write_tags(b, remix, tags.TrackTags(title="Typed By Hand"))
    run_queue(lib)

    (job,) = jobs(lib)
    assert (job["state"], job["reason"]) == ("needs_review", "file_changed")
    assert "was changed after the plan was made" in job["last_error"]
    after = tags.read_tags(remix)
    assert (after.title, after.version) == ("Typed By Hand", None)  # nothing was written
    assert music_files(lib) == ["Band/Unsorted/Melody (2).mp3", "Band/Unsorted/Melody.mp3"]
    assert sorted(tracks_by_path(index)) == [f"{UNSORTED}/Melody (2).mp3", f"{UNSORTED}/Melody.mp3"]


def test_a_repair_that_stops_after_its_tag_write_is_finished_when_run_again(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The title is written, then the move doesn't happen (the engine stops, or on
    Windows the file is open in another app). Run again, the job must see its own new
    title as its own, and rename the file: no later tidy would pick it up."""
    remix, _ = lost_pair(lib, index, rips, samples)
    remix.with_suffix(".lrc").write_text(SYNCED, encoding="utf-8")
    refuse_one_move(monkeypatch)
    batch_id = apply_and_run(lib, index, pipeline.plan_tidy(lib, index))

    (job,) = jobs(lib)
    assert (job["state"], job["attempts"]) == ("done", 2)
    assert music_files(lib) == [
        "Band/Unsorted/Melody (2).mp3",
        "Band/Unsorted/Melody R.lrc",
        "Band/Unsorted/Melody R.mp3",
    ]
    fixed = song(lib, "Band", "Unsorted", "Melody R.mp3")
    assert (fixed.title, fixed.version) == ("Melody R", ["remix"])
    assert sorted(tracks_by_path(index)) == [
        f"{UNSORTED}/Melody (2).mp3",
        f"{UNSORTED}/Melody R.mp3",
    ]

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == [
        "Band/Unsorted/Melody (2).mp3",
        "Band/Unsorted/Melody.lrc",
        "Band/Unsorted/Melody.mp3",
    ]
    back = tags.read_tags(remix)
    assert (back.title, back.version) == ("Melody", None)


def test_a_repair_that_stops_after_its_move_is_finished_when_run_again(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The song is renamed, and the engine stops before its lyrics file is moved and
    the index is told. Run again, the job finds the file where its own batch put it
    (the journal says where) and finishes: the lyrics follow, and the index is right."""
    remix, _ = lost_pair(lib, index, rips, samples)
    remix.with_suffix(".lrc").write_text(SYNCED, encoding="utf-8")
    track_id = tags.read_tags(remix).musicorg_id
    move, stops = fileops.move, [1]

    def stop_after_one(*args: Any, **kw: Any) -> Path:
        landed = move(*args, **kw)
        if stops:
            stops.pop()
            raise RuntimeError("the engine stopped here")
        return landed

    monkeypatch.setattr(fileops, "move", stop_after_one)
    batch_id = apply_and_run(lib, index, pipeline.plan_tidy(lib, index))

    (job,) = jobs(lib)
    assert (job["state"], job["attempts"]) == ("done", 2)
    assert music_files(lib) == [
        "Band/Unsorted/Melody (2).mp3",
        "Band/Unsorted/Melody R.lrc",
        "Band/Unsorted/Melody R.mp3",
    ]
    fixed = song(lib, "Band", "Unsorted", "Melody R.mp3")
    assert (fixed.title, fixed.version, fixed.musicorg_id) == ("Melody R", ["remix"], track_id)
    rows = tracks_by_path(index)
    assert sorted(rows) == [f"{UNSORTED}/Melody (2).mp3", f"{UNSORTED}/Melody R.mp3"]
    assert rows[f"{UNSORTED}/Melody R.mp3"]["musicorg_id"] == track_id

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == [
        "Band/Unsorted/Melody (2).mp3",
        "Band/Unsorted/Melody.lrc",
        "Band/Unsorted/Melody.mp3",
    ]
    assert (tags.read_tags(remix).title, tags.read_tags(remix).version) == ("Melody", None)
    assert sorted(tracks_by_path(index)) == [f"{UNSORTED}/Melody (2).mp3", f"{UNSORTED}/Melody.mp3"]


def test_a_file_moved_by_something_else_is_not_taken_for_the_jobs_own(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """Only a move this job's own batch made is carried on from. A song another batch
    moved between the plan and the job is left to a new plan."""
    remix, _ = lost_pair(lib, index, rips, samples)
    plan = pipeline.plan_tidy(lib, index)
    pipeline.apply(lib, index, plan.plan_id)
    with fileops.batch(lib, "demo") as b:
        fileops.move(b, remix, Path("Band", "Unsorted", "Somewhere Else.mp3"))
    run_queue(lib)

    (job,) = jobs(lib)
    assert (job["state"], job["reason"]) == ("needs_review", "file_changed")
    moved = song(lib, "Band", "Unsorted", "Somewhere Else.mp3")
    assert (moved.title, moved.version) == ("Melody", None)


def test_a_repaired_name_that_is_taken_gets_a_number(
    lib: Library, index: Index, rips: Path, two_mp3s: tuple[Path, Path]
) -> None:
    low, high = two_mp3s
    waiting_rip(index, rips, low, "Band - Melody R")
    remix = old_copy(lib, index, rips / "Band - Melody R.mp3", "Melody")
    # Another song already has the name the remix is to get. It says which version it
    # is, so tidy has nothing to do with it.
    (rips / "other").mkdir()
    shutil.copyfile(high, rips / "other" / "Melody R.mp3")
    in_the_way = library_copy(lib, index, rips / "other" / "Melody R.mp3", "Melody R",
                              version=["remix"])  # fmt: skip
    taken = in_the_way.read_bytes()

    plan = pipeline.plan_tidy(lib, index)
    assert plan.summary["versions"] == 1
    # The dry run says where the file will really land.
    assert [op.target for op in plan.operations] == [f"{UNSORTED}/Melody R (2).mp3"]
    batch_id = apply_and_run(lib, index, plan)

    # " (2)", never an overwrite.
    assert music_files(lib) == ["Band/Unsorted/Melody R (2).mp3", "Band/Unsorted/Melody R.mp3"]
    assert in_the_way.read_bytes() == taken
    landed = lib.paths.music / "Band" / "Unsorted" / "Melody R (2).mp3"
    assert tags.audio_hash(landed) == tags.audio_hash(low)
    assert (tags.read_tags(landed).title, tags.read_tags(landed).version) == (
        "Melody R", ["remix"],
    )  # fmt: skip
    assert sorted(tracks_by_path(index)) == [
        f"{UNSORTED}/Melody R (2).mp3",
        f"{UNSORTED}/Melody R.mp3",
    ]
    assert pipeline.plan_tidy(lib, index).operations == []  # its number is needed: it stays

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == ["Band/Unsorted/Melody R.mp3", "Band/Unsorted/Melody.mp3"]
    assert in_the_way.read_bytes() == taken
    assert (tags.read_tags(remix).title, tags.read_tags(remix).version) == ("Melody", None)
    assert sorted(tracks_by_path(index)) == [f"{UNSORTED}/Melody R.mp3", f"{UNSORTED}/Melody.mp3"]


def test_two_repaired_copies_with_one_new_name_both_land(
    lib: Library, index: Index, rips: Path, two_mp3s: tuple[Path, Path]
) -> None:
    """Two different rips both called "Solo R" were copied in as "Solo" and "Solo (2)"."""
    low, high = two_mp3s
    (rips / "a").mkdir()
    shutil.copyfile(low, rips / "a" / "Band - Solo R.mp3")
    first = add_item(index, "a/Band - Solo R", state="review", seconds=3)
    item = index.item(first)
    assert item is not None
    index.put_items([{**item, "parsed_artist": "Band", "parsed_title": "Solo",
                      "parse_confidence": 0.95}])  # fmt: skip
    waiting_rip(index, rips, high, "Band - Solo R")
    one = old_copy(lib, index, rips / "a" / "Band - Solo R.mp3", "Solo")
    two = old_copy(lib, index, rips / "Band - Solo R.mp3", "Solo")
    assert (one.name, two.name) == ("Solo.mp3", "Solo (2).mp3")
    ids = {tags.read_tags(path).musicorg_id for path in (one, two)}

    plan = pipeline.plan_tidy(lib, index)
    assert plan.summary["versions"] == 2
    # Two lines of a dry run never show one name: the second is shown with its " (2)".
    planned = {str(op.source.path): op.target for op in plan.operations if op.source}
    assert planned == {
        f"{UNSORTED}/Solo (2).mp3": f"{UNSORTED}/Solo R.mp3",
        f"{UNSORTED}/Solo.mp3": f"{UNSORTED}/Solo R (2).mp3",
    }
    batch_id = apply_and_run(lib, index, plan)

    assert music_files(lib) == ["Band/Unsorted/Solo R (2).mp3", "Band/Unsorted/Solo R.mp3"]
    landed = {str(t.origin_path): name for name in music_files(lib)
              for t in [tags.read_tags(lib.paths.music / name)]}  # fmt: skip
    assert landed == {  # each where the plan said
        str(rips / "Band - Solo R.mp3"): "Band/Unsorted/Solo R.mp3",
        str(rips / "a" / "Band - Solo R.mp3"): "Band/Unsorted/Solo R (2).mp3",
    }
    copies = [lib.paths.music / name for name in music_files(lib)]
    written = [tags.read_tags(path) for path in copies]
    assert [(t.title, t.version) for t in written] == [("Solo R", ["remix"])] * 2
    assert {t.musicorg_id for t in written} == ids
    for path, found in zip(copies, written, strict=True):  # each is still its own rip's audio
        assert tags.audio_hash(path) == tags.audio_hash(Path(str(found.origin_path)))
    assert {t["musicorg_id"] for t in index.library_tracks()} == ids
    assert pipeline.plan_tidy(lib, index).operations == []

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == ["Band/Unsorted/Solo (2).mp3", "Band/Unsorted/Solo.mp3"]
    for path in (one, two):
        assert (tags.read_tags(path).title, tags.read_tags(path).version) == ("Solo", None)
    assert sorted(tracks_by_path(index)) == [f"{UNSORTED}/Solo (2).mp3", f"{UNSORTED}/Solo.mp3"]


def test_a_numbered_file_that_only_needs_its_tag_keeps_its_name(
    lib: Library, index: Index, rips: Path, two_mp3s: tuple[Path, Path]
) -> None:
    """Two songs the owner both titled "Lost Boy R" by hand: "Lost Boy R.mp3" and "Lost
    Boy R (2).mp3". Each gets its version tag, and neither moves: the second must not
    try for the plain name and land on " (3)"."""
    low, high = two_mp3s
    (rips / "a").mkdir()
    shutil.copyfile(low, rips / "a" / "Band - Lost Boy R.mp3")
    first = add_item(index, "a/Band - Lost Boy R", state="review", seconds=3)
    item = index.item(first)
    assert item is not None
    index.put_items([{**item, "parsed_artist": "Band", "parsed_title": "Lost Boy",
                      "parse_confidence": 0.95}])  # fmt: skip
    waiting_rip(index, rips, high, "Band - Lost Boy R")
    library_copy(lib, index, rips / "a" / "Band - Lost Boy R.mp3", "Lost Boy R")
    library_copy(lib, index, rips / "Band - Lost Boy R.mp3", "Lost Boy R")
    files = ["Band/Unsorted/Lost Boy R (2).mp3", "Band/Unsorted/Lost Boy R.mp3"]
    assert music_files(lib) == files

    plan = pipeline.plan_tidy(lib, index)
    assert plan.summary["versions"] == 2
    assert all(op.source is not None and op.source.path == op.target for op in plan.operations)
    apply_and_run(lib, index, plan)

    assert music_files(lib) == files
    assert [tags.read_tags(lib.paths.music / name).version for name in files] == [["remix"]] * 2
    assert pipeline.plan_tidy(lib, index).operations == []


def test_a_number_is_told_apart_from_a_name_whatever_the_folders_case() -> None:
    here = PurePosixPath("Music/Kid CuDi/Unsorted/Solo Dolo (2).mp3")
    assert pipeline._just_a_number_added(
        here, PurePosixPath("Music/Kid Cudi/Unsorted/Solo Dolo.mp3")
    )
    assert not pipeline._just_a_number_added(
        here, PurePosixPath("Music/Kid Cudi/Unsorted/Solo Dolo R.mp3")
    )
    assert not pipeline._just_a_number_added(
        here, PurePosixPath("Music/Other/Unsorted/Solo Dolo.mp3")
    )


def test_a_version_and_a_preferred_name_are_put_right_together(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    lost_pair(lib, index, rips, samples)
    pipeline.set_name(lib, "Band", "The Band")

    plan = pipeline.plan_tidy(lib, index)
    # The remix is counted once, as a version put back; the original as a rename.
    assert (plan.summary["versions"], plan.summary["renames"]) == (1, 1)
    repair = next(op for op in plan.operations if "from_title" in op.params)
    assert repair.params["changes"] == {
        "title": "Melody R", "artist": "The Band", "version": ["remix"],
    }  # fmt: skip
    batch_id = apply_and_run(lib, index, plan)
    assert music_files(lib) == ["The Band/Unsorted/Melody R.mp3", "The Band/Unsorted/Melody.mp3"]
    fixed = song(lib, "The Band", "Unsorted", "Melody R.mp3")
    assert (fixed.title, fixed.artist, fixed.version) == ("Melody R", "The Band", ["remix"])

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == ["Band/Unsorted/Melody (2).mp3", "Band/Unsorted/Melody.mp3"]


def test_an_only_copy_made_under_the_old_rule_is_put_right_too(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """Decided as an only copy with no fixes: no match tag at all, and the same stripped
    title. A named version comes back in the rip's own words."""
    iid = waiting_rip(index, rips, samples["mp3"], "Band - Here [Lucian Remix] (320 kbps)")
    index.set_state(iid, "adopted", [])
    copy = library_copy(lib, index, rips / "Band - Here [Lucian Remix] (320 kbps).mp3", "Here",
                        match=None, only_copy=True)  # fmt: skip
    assert tags.read_tags(copy).match is None

    plan = pipeline.plan_tidy(lib, index)
    assert plan.summary["versions"] == 1
    apply_and_run(lib, index, plan)

    assert music_files(lib) == ["Band/Unsorted/Here (Lucian Remix).mp3"]
    fixed = song(lib, "Band", "Unsorted", "Here (Lucian Remix).mp3")
    assert (fixed.title, fixed.version) == ("Here (Lucian Remix)", ["remix:lucian"])
    assert (fixed.only_copy, fixed.match) == (True, None)
    assert item_state(index, iid) == ("adopted", [])


def test_a_copy_repaired_by_tidy_is_still_upgraded_when_it_is_found(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    album_lookups: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tidy rewrites the index's row for the copy it renames. The row must still say
    whose copy it is, or a later decision would copy the rip in a second time."""
    monkeypatch.setattr(pipeline, "EXTRAS", [])
    iid = own_rip(index, rips, samples["mp3"], "Band - Melody R",
                  title="Melody (Radio Remix)", state_="review")  # fmt: skip
    copy = old_copy(lib, index, rips / "Band - Melody R.mp3", "Melody")
    track_id = tags.read_tags(copy).musicorg_id
    apply_and_run(lib, index, pipeline.plan_tidy(lib, index))
    assert music_files(lib) == ["Band/Unsorted/Melody R.mp3"]

    index.set_state(iid, "matched_user", [])  # the owner picks the match
    plan = pipeline.plan_adopt(lib, index, matched=True)
    assert plan.summary["with_details"] == 1
    batch_id = apply_and_run(lib, index, plan)

    # One file, under the found title: the copy was upgraded where it was.
    assert music_files(lib) == ["Band/Tunes (2020)/03 Melody (Radio Remix).mp3"]
    found = song(lib, "Band", "Tunes (2020)", "03 Melody (Radio Remix).mp3")
    assert (found.title, found.version, found.match) == (
        "Melody (Radio Remix)", ["remix:radio"], "user_details",
    )  # fmt: skip
    assert found.musicorg_id == track_id
    assert item_state(index, iid) == ("adopted", [])
    assert [t["rel_path"] for t in index.library_tracks()] == [
        "Music/Band/Tunes (2020)/03 Melody (Radio Remix).mp3"
    ]

    pipeline.undo(lib, batch_id)
    back = song(lib, "Band", "Unsorted", "Melody R.mp3")
    assert (back.title, back.version, back.match) == ("Melody R", ["remix"], "unconfirmed")


def test_a_copy_found_between_the_plan_and_the_job_keeps_its_official_title(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    album_lookups: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The owner chose the plain official track for the rip "Melody R", and YouTube
    Music names no album, so the found copy stays where it was, titled "Melody": the
    very title the tidy plan saw. The job must not take that for the old engine's
    title and write the R back onto a found song."""
    monkeypatch.setattr(pipeline, "EXTRAS", [])
    monkeypatch.setattr(pipeline, "_album", lambda candidate, cache=None: pipeline.AlbumInfo())
    iid = own_rip(index, rips, samples["mp3"], "Band - Melody R", state_="review")
    copy = old_copy(lib, index, rips / "Band - Melody R.mp3", "Melody")
    tidy = pipeline.plan_tidy(lib, index)  # planned while the copy is still waiting…
    assert tidy.summary["versions"] == 1
    index.set_state(iid, "matched_user", [])
    found_batch = pipeline.apply(
        lib, index, pipeline.plan_adopt(lib, index, matched=True).plan_id
    ).batch_id
    tidy_batch = pipeline.apply(lib, index, tidy.plan_id).batch_id
    run_queue(lib)  # …and the song is found before the tidy job runs

    by_batch = {job["batch_id"]: job for job in jobs(lib)}
    assert by_batch[found_batch]["state"] == "done"
    late = by_batch[tidy_batch]
    assert (late["state"], late["reason"]) == ("needs_review", "file_changed")
    assert "was found or decided after the plan was made" in late["last_error"]
    assert music_files(lib) == ["Band/Unsorted/Melody.mp3"]
    found = tags.read_tags(copy)
    assert (found.title, found.match, found.version) == ("Melody", "user_details", None)
    assert pipeline.plan_tidy(lib, index).operations == []  # and no later plan touches it


def test_tidy_leaves_out_a_copy_whose_rip_is_waiting_in_the_queue(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    album_lookups: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`plan adopt --matched` is applied and the queue hasn't run yet. A tidy plan made
    now would rename copies that are about to get their official names, so they're
    left out, and the plan says why."""
    monkeypatch.setattr(pipeline, "EXTRAS", [])
    iid = own_rip(index, rips, samples["mp3"], "Band - Melody R",
                  title="Melody (Radio Remix)", state_="review")  # fmt: skip
    old_copy(lib, index, rips / "Band - Melody R.mp3", "Melody")
    index.set_state(iid, "matched_user", [])
    pipeline.apply(lib, index, pipeline.plan_adopt(lib, index, matched=True).plan_id)

    plan = pipeline.plan_tidy(lib, index)

    assert plan.operations == [] and plan.summary["versions"] == 0
    assert plan.summary["versions_skipped"] == {"rip_in_queue": 1}
    assert plan.summary["versions_left"] == [
        {"path": f"{UNSORTED}/Melody.mp3", "why": "rip_in_queue"}
    ]
    assert pipeline.describe(plan) == [
        f"       left     {UNSORTED}/Melody.mp3  (its rip names a version; a job for its rip "
        "is waiting in the queue (run the queue, then plan again))"
    ]
    cli._print_plan_summary(plan)
    assert ("1 song(s) left as they are, though their rip names a version: a job for their "
            "rip is waiting in the queue") in capsys.readouterr().out  # fmt: skip

    run_queue(lib)  # the song is found, and takes the found name
    assert music_files(lib) == ["Band/Tunes (2020)/03 Melody (Radio Remix).mp3"]
    after = pipeline.plan_tidy(lib, index)
    assert after.operations == [] and after.summary["versions_skipped"] == {}


def item_of(index: Index, rip_name: str) -> str:
    (found,) = [item["id"] for item in index.items() if item["rel_path"] == rip_name]
    return str(found)


def test_a_version_taken_off_by_hand_stays_off(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """The R on a rip isn't always a remix. The owner takes it off in Edit Details, and
    that leaves exactly the title the old engine wrote, with no version tag. Only the
    journal can tell the two apart. The owner's title stays: tidy doesn't put the R
    back, a decision with no fixes doesn't, and nothing reads the rip's name past it."""
    remix, _ = lost_pair(lib, index, rips, samples)
    track_id = str(tags.read_tags(remix).musicorg_id)
    apply_and_run(lib, index, pipeline.plan_tidy(lib, index))
    assert browse.version_tokens(lib, f"{UNSORTED}/Melody R.mp3") == ("remix",)

    edit(lib, index, f"{UNSORTED}/Melody R.mp3", changes={"title": "Melody"})
    files = ["Band/Unsorted/Melody (2).mp3", "Band/Unsorted/Melody.mp3"]
    assert music_files(lib) == files
    plain = tags.read_tags(remix)
    assert (plain.title, plain.version, plain.musicorg_id) == ("Melody", None, track_id)
    assert browse.typed_titles(lib) == {track_id: {"Melody"}}

    plan = pipeline.plan_tidy(lib, index)
    assert plan.operations == [] and plan.summary["versions"] == 0
    assert plan.summary["versions_skipped"] == {"title_names_no_version": 1}
    assert plan.summary["versions_left"] == [
        {"path": f"{UNSORTED}/Melody.mp3", "why": "title_names_no_version"}
    ]
    # The video and lyrics lookups, and Discover, read it as the plain song.
    assert browse.version_tokens(lib, f"{UNSORTED}/Melody.mp3") == ()
    owned = discover.Owned(index.library_tracks(), lambda: browse.typed_titles(lib))
    assert {hard for hard, _, tid in owned.songs["melody"] if tid == track_id} == {frozenset()}
    # Without the journal's word the rip's name would still be read (a copy made before
    # 2026-10-04, not repaired yet, looks just the same).
    assert browse.versions_of(plain) == ("remix",)

    # The owner decides it's an only copy, and says nothing about names.
    index.set_state(item_of(index, "Band - Melody R.mp3"), "only_copy", [])
    plan = pipeline.plan_adopt(lib, index)
    (op,) = plan.operations
    assert op.target == f"{UNSORTED}/Melody.mp3" and "retitle_from" not in op.params
    apply_and_run(lib, index, plan)
    assert music_files(lib) == files
    kept = tags.read_tags(remix)
    assert (kept.title, kept.version, kept.only_copy) == ("Melody", None, True)
    assert pipeline.plan_tidy(lib, index).summary["versions"] == 0


def test_an_edit_that_was_undone_is_not_the_owners_title(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    remix, _ = lost_pair(lib, index, rips, samples)
    undone = edit(lib, index, f"{UNSORTED}/Melody.mp3", changes={"title": "Tune"})
    pipeline.undo(lib, undone)
    assert tags.read_tags(remix).title == "Melody"
    assert browse.typed_titles(lib) == {}
    assert pipeline.plan_tidy(lib, index).summary["versions"] == 1  # still the engine's title

    # Typed there and back by hand, "Melody" is the owner's own.
    edit(lib, index, f"{UNSORTED}/Melody.mp3", changes={"title": "Tune"})
    edit(lib, index, f"{UNSORTED}/Tune.mp3", changes={"title": "Melody"})
    assert tags.read_tags(remix).title == "Melody"
    plan = pipeline.plan_tidy(lib, index)
    assert plan.summary["versions"] == 0
    assert plan.summary["versions_skipped"] == {"title_names_no_version": 1}


def test_a_repair_that_got_as_far_as_the_tags_is_finished_by_the_next_tidy(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """A repair job writes the title and the version tag, and then its move fails for
    good (a file that stays open, a disk that won't take the name). The file is left
    as "Melody.mp3", titled "Melody R". The next `plan tidy` finishes the rename."""
    remix, _ = lost_pair(lib, index, rips, samples)
    remix.with_suffix(".lrc").write_text(SYNCED, encoding="utf-8")
    with fileops.batch(lib, "tidy") as b:  # what the job did before it gave up
        fileops.write_tags(b, remix, tags.TrackTags(title="Melody R", version=["remix"]))
    index.put_library_tracks([pipeline._track_row(lib, remix, tags.read_tags(remix), 3.0)])

    plan = pipeline.plan_tidy(lib, index)
    assert (plan.summary["versions"], plan.summary["renames"]) == (1, 0)
    (op,) = plan.operations
    assert op.source is not None
    assert (op.source.path, op.target) == (f"{UNSORTED}/Melody.mp3", f"{UNSORTED}/Melody R.mp3")
    assert op.params["changes"] == {}  # nothing to write: the name is all that's left
    assert (op.params["from_title"], op.params["from_version"]) == ("Melody R", ["remix"])
    batch_id = apply_and_run(lib, index, plan)

    assert music_files(lib) == [
        "Band/Unsorted/Melody (2).mp3",
        "Band/Unsorted/Melody R.lrc",
        "Band/Unsorted/Melody R.mp3",
    ]
    fixed = song(lib, "Band", "Unsorted", "Melody R.mp3")
    assert (fixed.title, fixed.version) == ("Melody R", ["remix"])
    assert sorted(tracks_by_path(index)) == [
        f"{UNSORTED}/Melody (2).mp3",
        f"{UNSORTED}/Melody R.mp3",
    ]
    # A name that doesn't follow the title for any other reason is left alone: a file
    # named for its full title has nothing to finish.
    assert pipeline.plan_tidy(lib, index).summary["versions"] == 0

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == [
        "Band/Unsorted/Melody (2).mp3",
        "Band/Unsorted/Melody.lrc",
        "Band/Unsorted/Melody.mp3",
    ]


def test_a_hand_title_that_names_another_kind_of_version_is_left_alone(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """The rip says remix; the owner retitled the copy "Song (Live)". That isn't the
    rip's version under another spelling, so no remix tag is written onto it."""
    waiting_rip(index, rips, samples["mp3"], "Band - Song R")
    copy = library_copy(lib, index, rips / "Band - Song R.mp3", "Song (Live)")
    plan = pipeline.plan_tidy(lib, index)
    assert plan.operations == []
    assert plan.summary["versions_skipped"] == {"title_names_no_version": 1}
    assert tags.read_tags(copy).version is None


def test_a_rip_the_parser_reads_differently_now_is_not_retitled(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """The repair knows the engine's old title by parsing the rip's names again. If the
    parser has changed its mind since the scan stored its answer, the title in the file
    can't be told from a hand edit: the copy is counted, and left as it is."""
    iid = waiting_rip(index, rips, samples["mp3"], "Band - Melody R")
    item = index.item(iid)
    assert item is not None
    index.put_items([{**item, "parsed_title": "Melody As It Was Parsed Then"}])
    copy = library_copy(lib, index, rips / "Band - Melody R.mp3", "Melody As It Was Parsed Then")

    plan = pipeline.plan_tidy(lib, index)

    assert plan.operations == [] and plan.summary["versions"] == 0
    assert plan.summary["versions_skipped"] == {"title_names_no_version": 1}
    assert tags.read_tags(copy).title == "Melody As It Was Parsed Then"


def test_a_file_another_job_of_the_batch_moved_is_not_taken_for_the_jobs_own(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """A job run again looks in its batch's journal for where its file went. The file
    found there must be the same song: a different song moved from the same place by
    the same batch isn't it."""
    remix, original = lost_pair(lib, index, rips, samples)
    plan = pipeline.plan_tidy(lib, index)
    (op,) = plan.operations
    batch = fileops.open_batch(lib, "tidy")
    ctx = queue.JobContext(SimpleNamespace(lib=lib), batch, {"id": 1, "payload": {}})  # type: ignore[arg-type]
    # The same batch moves the remix away, then moves the *original* from that place.
    fileops.move(batch, remix, Path("Band", "Unsorted", "Elsewhere.mp3"))
    moved = fileops.move(batch, original, Path("Band", "Unsorted", "Melody.mp3"))
    fileops.move(batch, moved, Path("Band", "Unsorted", "Other Place.mp3"))
    fileops.close_batch(lib, batch.batch_id)

    # The newest move from "Melody.mp3" is the original's: not this job's song.
    assert pipeline._moved_by_this_batch(ctx, op) == (
        lib.paths.music / "Band" / "Unsorted" / "Elsewhere.mp3"
    )
    other = replace(op, params={**op.params, "musicorg_id": "t_someone_else"})
    assert pipeline._moved_by_this_batch(ctx, other) is None


def test_names_that_meet_in_another_letter_case_dont_share_a_file(
    lib: Library, index: Index, rips: Path, two_mp3s: tuple[Path, Path]
) -> None:
    """A repaired name that differs from another song's only in letter case counts as
    taken, on every kind of disk: the copy gets " (2)", in the plan and on the disk."""
    low, high = two_mp3s
    waiting_rip(index, rips, low, "Band - Melody R")
    old_copy(lib, index, rips / "Band - Melody R.mp3", "Melody")
    (rips / "other").mkdir()
    shutil.copyfile(high, rips / "other" / "MELODY R.mp3")
    shouting = library_copy(lib, index, rips / "other" / "MELODY R.mp3", "MELODY R",
                            version=["remix"])  # fmt: skip
    taken = shouting.read_bytes()

    plan = pipeline.plan_tidy(lib, index)
    assert [op.target for op in plan.operations] == [f"{UNSORTED}/Melody R (2).mp3"]
    apply_and_run(lib, index, plan)

    assert music_files(lib) == ["Band/Unsorted/MELODY R.mp3", "Band/Unsorted/Melody R (2).mp3"]
    assert shouting.read_bytes() == taken
    landed = lib.paths.music / "Band" / "Unsorted" / "Melody R (2).mp3"
    assert tags.audio_hash(landed) == tags.audio_hash(low)


def test_a_long_title_and_its_version_still_land_apart(
    lib: Library, index: Index, rips: Path, two_mp3s: tuple[Path, Path]
) -> None:
    """A file name is cut from its end, so a very long title loses its version from the
    name first. The remix and its original then want one name: the second gets " (2)",
    nothing is overwritten, and the title and version tag still say which is which."""
    low, high = two_mp3s
    long = " ".join(["Word"] * 60)  # 299 characters: far past a name's 120
    adopt_item(index, rips, low, "Band - Long", confidence=0.95,
               own=tags.TrackTags(title=f"{long} (Somebody Remix)", artist="Band"))  # fmt: skip
    adopt_item(index, rips, high, "Band - Long Too", confidence=0.95,
               own=tags.TrackTags(title=long, artist="Band"))  # fmt: skip

    apply_and_run(lib, index, pipeline.plan_adopt(lib, index))

    numbered, plain = music_files(lib)
    # Cut to the limit (sooner on Windows, where the whole path counts), and the version
    # went with the cut.
    assert len(Path(plain).name) <= 120 and "Remix" not in plain
    assert Path(numbered).stem == Path(plain).stem + " (2)"
    written = {
        str(t.title): (t.version, tags.audio_hash(lib.paths.music / name))
        for name in (numbered, plain)
        for t in [tags.read_tags(lib.paths.music / name)]
    }
    assert written == {
        f"{long} (Somebody Remix)": (["remix:somebody"], tags.audio_hash(low)),
        long: (None, tags.audio_hash(high)),
    }


def test_version_words_a_file_name_cant_hold_are_changed_there_only(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """A slash can't be in a file name anywhere, nor a colon on Windows: the name gets
    "_", and the title keeps the rip's own words."""
    adopt_item(index, rips, samples["mp3"], "Band - Club Night", confidence=0.95,
               own=tags.TrackTags(title="Club Night (Mix 1/2)", artist="Band"))  # fmt: skip
    adopt_item(index, rips, samples["mp3"], "Band - Night", confidence=0.95,
               own=tags.TrackTags(title="Night (AC/DC Remix)", artist="Band"))  # fmt: skip
    apply_and_run(lib, index, pipeline.plan_adopt(lib, index))
    assert music_files(lib) == [
        "Band/Unsorted/Club Night (Mix 1_2).mp3",
        "Band/Unsorted/Night (AC_DC Remix).mp3",
    ]
    assert song(lib, "Band", "Unsorted", "Club Night (Mix 1_2).mp3").title == "Club Night (Mix 1/2)"
    night = song(lib, "Band", "Unsorted", "Night (AC_DC Remix).mp3")
    assert (night.title, night.version) == ("Night (AC/DC Remix)", ["remix:ac dc"])


def test_tidy_on_an_index_that_spells_a_folder_in_another_letter_case(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """Three of the owner's folders are spelled one way in the index and another on the
    disk ("KiD CuDi", "Kid Cudi"). On a disk that doesn't tell the two apart they are
    one folder: both tidy runs and both undos go through."""
    remix, original = lost_pair(lib, index, rips, samples)
    if not (lib.paths.music / "BAND").is_dir():
        pytest.skip("this disk tells letter case apart, so the index can't be spelled otherwise")
    rows = index.library_tracks()
    index.remove_library_tracks([row["rel_path"] for row in rows])
    index.put_library_tracks(
        [{**row, "rel_path": row["rel_path"].replace("/Band/", "/BAND/")} for row in rows]
    )
    start = music_files(lib)

    first = pipeline.plan_tidy(lib, index)
    assert (first.summary["versions"], first.summary["numbers_dropped"]) == (1, 0)
    first_batch = apply_and_run(lib, index, first)
    assert music_files(lib) == ["Band/Unsorted/Melody (2).mp3", "Band/Unsorted/Melody R.mp3"]
    second = pipeline.plan_tidy(lib, index)
    assert (second.summary["versions"], second.summary["numbers_dropped"]) == (0, 1)
    second_batch = apply_and_run(lib, index, second)
    assert music_files(lib) == ["Band/Unsorted/Melody R.mp3", "Band/Unsorted/Melody.mp3"]
    assert pipeline.plan_tidy(lib, index).operations == []
    assert {t["title"] for t in index.library_tracks()} == {"Melody R", "Melody"}

    pipeline.undo(lib, second_batch)
    pipeline.undo(lib, first_batch)
    assert music_files(lib) == start
    assert (tags.read_tags(remix).title, tags.read_tags(remix).version) == ("Melody", None)
    assert tags.read_tags(original).origin_path == str(rips / "Band - Melody.mp3")


def test_tidy_copes_with_a_rip_name_that_is_only_junk(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """A copy whose rip isn't indexed, and was called "(Official Video).mp3": there is
    nothing in that name to read, and the plan must say so quietly, not stop."""
    waiting_rip(index, rips, samples["mp3"], "Band - Kept")
    copy = library_copy(lib, index, rips / "Band - Kept.mp3", "Gone",
                        origin_path=str(rips / "elsewhere" / "(Official Video).mp3"))  # fmt: skip
    plan = pipeline.plan_tidy(lib, index)
    assert plan.operations == [] and plan.summary["versions_skipped"] == {}
    assert browse.versions_of(tags.read_tags(copy)) == ()


# ---- undo, when a later batch gave a file's old name away -----------------------------


def test_undoing_a_repair_whose_old_names_were_given_away_is_refused(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """Tidy twice: the remix gets its name, then the original gets "Melody" back. Undoing
    only the first would move the remix onto a name the original has now. It would land
    on " (2)", its title would stay as it is (undo looks for the tags under the old
    name), and the index would lose it. So the undo says what to do instead."""
    remix, original = lost_pair(lib, index, rips, samples)
    remix.with_suffix(".lrc").write_text(SYNCED, encoding="utf-8")
    start_files = music_files(lib)
    start_tags = {path: tags.to_record(tags.read_tags(path)) for path in (remix, original)}
    start_rows = {rel: row["musicorg_id"] for rel, row in tracks_by_path(index).items()}
    first = apply_and_run(lib, index, pipeline.plan_tidy(lib, index))
    second = apply_and_run(lib, index, pipeline.plan_tidy(lib, index))
    files = music_files(lib)
    assert files == [
        "Band/Unsorted/Melody R.lrc",
        "Band/Unsorted/Melody R.mp3",
        "Band/Unsorted/Melody.mp3",
    ]
    rows = tracks_by_path(index)
    journal_before = len(fileops.read_journal(lib))

    for dry_run in (True, False):
        with pytest.raises(UndoError) as refused:
            pipeline.undo(lib, first, dry_run=dry_run)
        message = refused.value.message
        assert f"Batch {first} can't be undone yet" in message
        assert "Music/Band/Unsorted/Melody.mp3" in message
        assert f"`musicorg undo {second}`" in message and "Nothing was changed" in message
    # Nothing was: not a file, not a tag, not a row, and no undo batch was opened.
    assert music_files(lib) == files
    kept = song(lib, "Band", "Unsorted", "Melody R.mp3")
    assert (kept.title, kept.version) == ("Melody R", ["remix"])
    assert tracks_by_path(index) == rows
    assert len(fileops.read_journal(lib)) == journal_before

    # In the order it says, everything goes back as it was.
    pipeline.undo(lib, second)
    pipeline.undo(lib, first)
    assert music_files(lib) == start_files
    assert "Band/Unsorted/Melody.lrc" in start_files  # the remix's lyrics, back beside it
    for path, record in start_tags.items():
        assert tags.to_record(tags.read_tags(path)) == record
    assert {rel: row["musicorg_id"] for rel, row in tracks_by_path(index).items()} == start_rows


def test_an_undo_within_one_batch_is_not_refused(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """One batch moved a file away and then put another under its old name. Undo takes
    them back newest first, so the name is free again by the time it's needed."""
    remix, original = lost_pair(lib, index, rips, samples)
    with fileops.batch(lib, "demo") as b:
        fileops.move(b, remix, Path("Band", "Unsorted", "Melody R.mp3"))
        fileops.move(b, original, Path("Band", "Unsorted", "Melody.mp3"))

    pipeline.undo(lib, b.batch_id)

    assert music_files(lib) == ["Band/Unsorted/Melody (2).mp3", "Band/Unsorted/Melody.mp3"]
    assert tags.read_tags(remix).origin_path == str(rips / "Band - Melody R.mp3")
    assert tags.read_tags(original).origin_path == str(rips / "Band - Melody.mp3")

    # A longer chain: one song leaves a name, a second takes it and moves on again.
    with fileops.batch(lib, "demo") as b:
        fileops.move(b, remix, Path("Band", "Unsorted", "First.mp3"))
        second = fileops.move(b, original, Path("Band", "Unsorted", "Melody.mp3"))
        fileops.move(b, second, Path("Band", "Unsorted", "Second.mp3"))
    assert music_files(lib) == ["Band/Unsorted/First.mp3", "Band/Unsorted/Second.mp3"]

    pipeline.undo(lib, b.batch_id)

    assert music_files(lib) == ["Band/Unsorted/Melody (2).mp3", "Band/Unsorted/Melody.mp3"]
    assert tags.read_tags(remix).origin_path == str(rips / "Band - Melody R.mp3")
    assert tags.read_tags(original).origin_path == str(rips / "Band - Melody.mp3")


def test_a_taken_name_nobodys_batch_gave_away_is_said_plainly(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    remix, _ = lost_pair(lib, index, rips, samples)
    batch_id = apply_and_run(lib, index, pipeline.plan_tidy(lib, index))
    shutil.copyfile(samples["mp3"], remix)  # a file put there by hand, outside the engine

    with pytest.raises(UndoError, match="Rename or move the file that has the name now"):
        pipeline.undo(lib, batch_id)
    assert song(lib, "Band", "Unsorted", "Melody R.mp3").version == ["remix"]

    remix.unlink()
    pipeline.undo(lib, batch_id)  # the name is free again: the undo goes through
    assert (tags.read_tags(remix).title, tags.read_tags(remix).version) == ("Melody", None)


def test_a_song_that_comes_back_under_another_name_is_still_in_the_index(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """Undo brings a set-aside song back from `_Replaced/`. If its name was taken
    meanwhile it comes back as " (2)", and the app must still list it."""
    waiting_rip(index, rips, samples["mp3"], "Band - Melody")
    waiting_rip(index, rips, samples["mp3"], "Band - Other")
    first = old_copy(lib, index, rips / "Band - Melody.mp3", "Melody")
    track_id = tags.read_tags(first).musicorg_id
    with fileops.batch(lib, "demo") as b:
        fileops.supersede(b, first)
    index.remove_library_tracks([f"{UNSORTED}/Melody.mp3"])
    old_copy(lib, index, rips / "Band - Other.mp3", "Melody")  # another song takes the name

    undone = pipeline.undo(lib, b.batch_id)

    assert [step.to for step in undone.steps] == [f"{UNSORTED}/Melody (2).mp3"]
    rows = tracks_by_path(index)
    assert sorted(rows) == [f"{UNSORTED}/Melody (2).mp3", f"{UNSORTED}/Melody.mp3"]
    assert rows[f"{UNSORTED}/Melody (2).mp3"]["musicorg_id"] == track_id


def test_a_dry_run_says_the_tags_of_a_renamed_file_will_come_back(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """Undoing a repair moves the file back and then restores its tags. A dry run looks
    at the disk as it is, where the file isn't under its old name yet: it used to say
    of every song that its tags "can't be restored", which the real undo then did."""
    remix, _ = lost_pair(lib, index, rips, samples)
    batch_id = apply_and_run(lib, index, pipeline.plan_tidy(lib, index))

    dry = pipeline.undo(lib, batch_id, dry_run=True)

    assert [(step.op, step.action, step.status) for step in dry.steps] == [
        ("move", "move", "planned"),
        ("write_tags", "write_tags", "planned"),
    ]
    assert dry.steps[1].note == (
        f"Restore the tags of {UNSORTED}/Melody.mp3 (once it is back under that name)"
    )
    assert not any("can't be restored" in step.note for step in dry.steps)
    assert song(lib, "Band", "Unsorted", "Melody R.mp3").version == ["remix"]  # nothing done
    # And the real undo does what the dry run said.
    done = pipeline.undo(lib, batch_id)
    assert [(step.action, step.status) for step in done.steps] == [
        ("move", "done"),
        ("write_tags", "done"),
    ]
    assert (tags.read_tags(remix).title, tags.read_tags(remix).version) == ("Melody", None)


def old_batch(lib: Library, index: Index, rips: Path, copies: list[tuple[str, str]]) -> str:
    """One batch that copied several rips in the way the engine did before 2026-10-04,
    as the batch of 1 Oct did: each `(rip's name, title)`, under its stripped title."""
    with fileops.batch(lib, "adopt") as b:
        made = []
        for rip, title in copies:
            staged = fileops.stage_copy(b, rips / rip)
            fileops.write_tags(b, staged, tags.TrackTags(
                title=title, artist="Band", musicorg_id=tags.new_track_id(), source="rip_copy",
                source_format="mp3", match="unconfirmed", origin_path=str(rips / rip),
                schema=1))  # fmt: skip
            made.append(fileops.commit(b, staged, Path("Band", "Unsorted", f"{title}.mp3")))
    for copy in made:
        index.put_library_tracks([pipeline._track_row(lib, copy, tags.read_tags(copy), 3.0)])
    return b.batch_id


def by_rip(lib: Library) -> dict[str, str]:
    """Each song in Music/, by the name of the rip it came from."""
    return {
        Path(str(tags.read_tags(lib.paths.music / name).origin_path)).name: name
        for name in music_files(lib)
        if name.endswith(".mp3")
    }


def test_undoing_the_batch_that_copied_the_rips_in_is_refused_after_the_repair(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """One batch copied in the remix (as "Melody") and the original (as "Melody (2)").
    Two tidy runs later the remix is "Melody R" and the original is "Melody". Undo works
    by path: undoing the first batch now would set the *original* aside in the remix's
    place and leave the remix in the library. It's refused, and says what to undo first."""
    for name in ("Band - Melody R", "Band - Melody", "Band - Other R"):
        waiting_rip(index, rips, samples["mp3"], name)
    brought_in = old_batch(lib, index, rips, [
        ("Band - Melody R.mp3", "Melody"), ("Band - Melody.mp3", "Melody"),
        ("Band - Other R.mp3", "Other"),
    ])  # fmt: skip
    assert by_rip(lib) == {
        "Band - Melody R.mp3": "Band/Unsorted/Melody.mp3",
        "Band - Melody.mp3": "Band/Unsorted/Melody (2).mp3",
        "Band - Other R.mp3": "Band/Unsorted/Other.mp3",
    }
    first = apply_and_run(lib, index, pipeline.plan_tidy(lib, index))

    # After the first run alone: no name has changed hands yet, but two of the batch's
    # files are no longer where it left them. Undo would leave them in the library.
    with pytest.raises(UndoError) as refused:
        pipeline.undo(lib, brought_in)
    assert "2 of its files were renamed or moved by a later batch" in refused.value.message
    assert f"`musicorg undo {first}`" in refused.value.message

    second = apply_and_run(lib, index, pipeline.plan_tidy(lib, index))
    repaired = {
        "Band - Melody R.mp3": "Band/Unsorted/Melody R.mp3",
        "Band - Melody.mp3": "Band/Unsorted/Melody.mp3",
        "Band - Other R.mp3": "Band/Unsorted/Other R.mp3",
    }
    assert by_rip(lib) == repaired
    rows = tracks_by_path(index)
    journal_before = len(fileops.read_journal(lib))

    for dry_run in (True, False):
        with pytest.raises(UndoError) as refused:
            pipeline.undo(lib, brought_in, dry_run=dry_run)
        message = refused.value.message
        assert f"Batch {brought_in} can't be undone yet" in message
        assert "3 of its files were renamed or moved by a later batch" in message
        assert f"`musicorg undo {second}`" in message and "Nothing was changed" in message
    # Nothing was: every song is where it was, and no undo batch was opened.
    assert by_rip(lib) == repaired and list(lib.paths.replaced.rglob("*.mp3")) == []
    assert tracks_by_path(index) == rows
    assert len(fileops.read_journal(lib)) == journal_before

    # Newest first, each undo goes through, and the first batch takes its own three files.
    pipeline.undo(lib, second)
    with pytest.raises(UndoError, match=f"musicorg undo {first}"):
        pipeline.undo(lib, brought_in)
    pipeline.undo(lib, first)
    undone = pipeline.undo(lib, brought_in)
    assert [step.status for step in undone.steps if step.op == "commit"] == ["done"] * 3
    assert music_files(lib) == [] and index.library_tracks() == []
    assert len(list(lib.paths.replaced.rglob("*.mp3"))) == 3


def test_a_file_set_aside_since_is_simply_no_longer_there(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """A later batch set one of the batch's files aside. It isn't in Music/ any more, so
    there's nothing for undo to mistake: it says so and goes on. Only when another file
    has taken the name since is the undo refused."""
    for name in ("Band - Melody", "Band - Other", "Band - Third"):
        waiting_rip(index, rips, samples["mp3"], name)
    brought_in = old_batch(lib, index, rips, [("Band - Melody.mp3", "Melody"),
                                              ("Band - Other.mp3", "Other")])  # fmt: skip
    melody = lib.paths.music / "Band" / "Unsorted" / "Melody.mp3"
    with fileops.batch(lib, "demo") as aside:
        fileops.supersede(aside, melody)
    index.remove_library_tracks([f"{UNSORTED}/Melody.mp3"])

    dry = pipeline.undo(lib, brought_in, dry_run=True)  # not refused
    skipped = [step.note for step in dry.steps if step.status == "skipped" and step.op == "commit"]
    assert skipped == [
        f"{UNSORTED}/Melody.mp3 is no longer there, so there's nothing to take back."
    ]

    # Another song is given that name: now undo would take the wrong one.
    newcomer = old_copy(lib, index, rips / "Band - Third.mp3", "Melody")
    with pytest.raises(UndoError) as refused:
        pipeline.undo(lib, brought_in)
    assert f"One of its files was renamed or moved by a later batch: {UNSORTED}/Melody.mp3" in (
        refused.value.message
    )
    assert f"`musicorg undo {aside.batch_id}`" in refused.value.message
    assert tags.read_tags(newcomer).origin_path == str(rips / "Band - Third.mp3")  # untouched
    assert music_files(lib) == ["Band/Unsorted/Melody.mp3", "Band/Unsorted/Other.mp3"]


def test_a_folder_or_another_letter_case_at_the_old_name_refuses_the_undo(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """A file moved back never overwrites, and it counts anything of its old name as in
    the way: a folder, or a file whose name differs only in letter case. Undo counts the
    same way, so it refuses instead of leaving the song as " (2)" with its tags unrestored."""
    remix, _ = lost_pair(lib, index, rips, samples)
    batch_id = apply_and_run(lib, index, pipeline.plan_tidy(lib, index))
    assert not remix.exists()

    remix.mkdir()  # a folder called "Melody.mp3", made by hand
    with pytest.raises(UndoError, match="would go back to a name that another file has now"):
        pipeline.undo(lib, batch_id)
    remix.rmdir()

    shouting = remix.with_name("MELODY.MP3")
    shutil.copyfile(samples["mp3"], shouting)
    with pytest.raises(UndoError, match="Rename or move the file that has the name now"):
        pipeline.undo(lib, batch_id)
    assert song(lib, "Band", "Unsorted", "Melody R.mp3").version == ["remix"]  # nothing done
    shouting.unlink()

    pipeline.undo(lib, batch_id)
    assert (tags.read_tags(remix).title, tags.read_tags(remix).version) == ("Melody", None)


@pytest.mark.parametrize("sidecar", ["Melody.lrc", "cover.jpg"])
def test_a_taken_name_of_a_lyrics_file_or_a_cover_refuses_the_undo_too(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path], sidecar: str
) -> None:
    """The refusal is the same for a file that goes with a song. A batch moved a `.lrc`
    (or an album's cover) and something else has its old name now: moved back, it would
    become " (2)", which nothing would ever read. So the undo is refused whole."""
    remix, _ = lost_pair(lib, index, rips, samples)
    with fileops.batch(lib, "demo") as b:
        fileops.write_sidecar(b, remix, ".lrc" if sidecar.endswith(".lrc") else sidecar, b"x" * 9)
    pipeline.set_name(lib, "Band", "The Band")  # every file moves to another folder
    batch_id = apply_and_run(lib, index, pipeline.plan_tidy(lib, index))
    old_folder = lib.paths.music / "Band" / "Unsorted"
    assert not old_folder.exists()
    # The lyrics file went with its song, which got its version back on the way.
    moved = "Melody R.lrc" if sidecar.endswith(".lrc") else sidecar
    assert f"The Band/Unsorted/{moved}" in music_files(lib)
    files = music_files(lib)

    old_folder.mkdir(parents=True)
    (old_folder / sidecar).write_bytes(b"put there by hand")
    with pytest.raises(UndoError) as refused:
        pipeline.undo(lib, batch_id)
    assert f"Music/Band/Unsorted/{sidecar}" in refused.value.message
    assert "Nothing was changed" in refused.value.message
    assert [name for name in music_files(lib) if name.startswith("The Band/")] == files

    (old_folder / sidecar).unlink()
    pipeline.undo(lib, batch_id)
    assert not (lib.paths.music / "The Band").exists()
    assert (old_folder / sidecar).read_bytes() == b"x" * 9


# ---- the version a song's lyrics are looked up as --------------------------------------


def test_lyrics_are_looked_up_as_the_version_the_title_names(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 1 Oct lyrics batch looked remixes up as the plain song, because their version
    was in neither the title nor the tag. The lookup is told every version the file
    shows, so a remix isn't given the original's timed lyrics."""
    for name in ("Melody R", "Typed R", "Old R", "Vitamin R", "Named"):
        waiting_rip(index, rips, samples["mp3"], f"Band - {name}")

    def rip(name: str) -> Path:
        return rips / f"Band - {name}.mp3"

    library_copy(lib, index, rip("Melody R"), "Melody R", version=["remix"])
    library_copy(lib, index, rip("Typed R"), "Typed R")  # the owner's title, no tag yet
    library_copy(lib, index, rip("Old R"), "Old")  # as copies were made before 2026-10-04
    # Official titles: one that ends in " R", and one whose tag went missing.
    library_copy(lib, index, rip("Vitamin R"), "Vitamin R", match="user_details")
    library_copy(lib, index, rip("Named"), "Named (Somebody Remix)", match="auto_details")
    asked: dict[str, tuple[str, ...]] = {}

    def find(query: lyrics.Query, *, cache: Any = None) -> lyrics.Found:
        asked[query.title] = query.versions
        return lyrics.Found("not_found")

    monkeypatch.setattr(lyrics, "find", find)
    apply_and_run(lib, index, pipeline.plan_lyrics(lib, index))

    assert asked == {
        "Melody R": ("remix",),
        "Typed R": ("remix",),
        "Old": ("remix",),
        "Vitamin R": (),
        "Named (Somebody Remix)": ("remix:somebody",),
    }


def lrclib_answer(folder: Path, path: str, response: Any, **params: Any) -> None:
    """Record what LRCLIB answers to one request (as tests/test_lyrics.py does)."""
    key = f"lrclib {path} " + "&".join(f"{k}={params[k]}" for k in sorted(params))
    recording = youtube.recording_path(folder, "lrclib", key)
    recording.parent.mkdir(parents=True, exist_ok=True)
    recording.write_text(json.dumps({"kind": "lrclib", "key": key, "response": response}), "utf-8")


def test_a_remix_doesnt_take_the_originals_timed_lyrics(
    lib: Library,
    index: Index,
    rips: Path,
    samples: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the real lookup, with recorded answers. A copy titled "Melody R" asks
    LRCLIB for "Melody R": a record called "Melody", of the very same length, isn't its
    record. A copy whose title names the remix takes the record of that name. (Before
    2026-10-04 both were titled "Melody", and each took the original's lyrics.)"""
    replay = tmp_path / "replay"
    monkeypatch.setenv(youtube.REPLAY_ENV, str(replay))
    for name in ("Melody R", "Melody (Somebody Remix)"):
        waiting_rip(index, rips, samples["mp3"], f"Band - {name}")
    marked = library_copy(lib, index, rips / "Band - Melody R.mp3", "Melody R",
                          version=["remix"])  # fmt: skip
    named = library_copy(lib, index, rips / "Band - Melody (Somebody Remix).mp3",
                         "Melody (Somebody Remix)", version=["remix:somebody"])  # fmt: skip
    seconds = int(round(tags.probe(marked).duration_s or 0))

    def record(title: str, **fields: Any) -> dict[str, Any]:
        return {"id": 1, "name": title, "trackName": title, "artistName": "Band",
                "albumName": "", "duration": float(seconds), "instrumental": False,
                "plainLyrics": PLAIN, "syncedLyrics": SYNCED, **fields}  # fmt: skip

    asks = {"artist_name": "Band"}
    lrclib_answer(replay, "get", None, track_name="Melody R", duration=seconds, **asks)
    lrclib_answer(replay, "search", [record("Melody")], track_name="Melody R", **asks)
    lrclib_answer(replay, "get", record("Melody (Somebody Remix)"), duration=seconds,
                  track_name="Melody (Somebody Remix)", **asks)  # fmt: skip

    apply_and_run(lib, index, pipeline.plan_lyrics(lib, index))

    found = {job["last_error"] for job in jobs(lib) if job["kind"] == "lyrics"}
    assert found == {"not_found", "synced from LRCLIB"}
    assert not marked.with_suffix(".lrc").exists() and tags.read_tags(marked).lyrics is None
    assert named.with_suffix(".lrc").read_text(encoding="utf-8") == SYNCED
    assert tags.read_tags(named).lyrics == PLAIN


# ---- Edit Details keeps the version tag in step with the title -------------------------


def test_editing_a_title_changes_the_version_with_it(
    lib: Library, index: Index, rips: Path, samples: dict[str, Path]
) -> None:
    """The owner's way to put a wrong version right: the R on a rip isn't always a
    remix. The tag says what the title they typed says."""
    iid = adopt_item(index, rips, samples["mp3"], "Band - Melody R")
    waiting_item(index, iid, "review")
    adopt_unconfirmed(lib, index)
    assert song(lib, "Band", "Unsorted", "Melody R.mp3").version == ["remix"]

    # Not a remix at all: the R goes from the title, and the tag goes with it.
    plan = pipeline.plan_edit(lib, index, f"{UNSORTED}/Melody R.mp3", changes={"title": "Melody"})
    assert plan.operations[0].params["version"] == []
    assert pipeline.describe(plan)[0].endswith("(title: Melody, version: (none))")
    batch_id = apply_and_run(lib, index, plan)
    assert music_files(lib) == ["Band/Unsorted/Melody.mp3"]
    plain = song(lib, "Band", "Unsorted", "Melody.mp3")
    assert (plain.title, plain.version) == ("Melody", None)

    pipeline.undo(lib, batch_id)
    back = song(lib, "Band", "Unsorted", "Melody R.mp3")
    assert (back.title, back.version) == ("Melody R", ["remix"])

    # The other way round: a plain title given the owner's R gets the tag.
    edit(lib, index, f"{UNSORTED}/Melody R.mp3", changes={"title": "Melody"})
    plan = pipeline.plan_edit(lib, index, f"{UNSORTED}/Melody.mp3", changes={"title": "Melody R"})
    assert plan.operations[0].params["version"] == ["remix"]
    assert pipeline.describe(plan)[0].endswith("(title: Melody R, version: remix)")
    apply_and_run(lib, index, plan)
    assert song(lib, "Band", "Unsorted", "Melody R.mp3").version == ["remix"]

    # It was a cover; then a live take at a named place.
    edit(lib, index, f"{UNSORTED}/Melody R.mp3", changes={"title": "Melody (Cover)"})
    assert song(lib, "Band", "Unsorted", "Melody (Cover).mp3").version == ["cover"]
    edit(lib, index, f"{UNSORTED}/Melody (Cover).mp3",
         changes={"title": "Melody (Live at Wembley Stadium)"})  # fmt: skip
    live = song(lib, "Band", "Unsorted", "Melody (Live at Wembley Stadium).mp3")
    assert live.version == ["live:wembley stadium"]

    # A new title that names the same version leaves the tag as it is, and so does an
    # edit that isn't to the title.
    rel = f"{UNSORTED}/Melody (Live at Wembley Stadium).mp3"
    plan = pipeline.plan_edit(lib, index, rel, changes={"title": "Tune (Live at Wembley Stadium)"})
    assert "version" not in plan.operations[0].params
    plan = pipeline.plan_edit(lib, index, rel, changes={"genre": "Rock"})
    assert "version" not in plan.operations[0].params
    apply_and_run(lib, index, plan)
    assert song(lib, "Band", "Unsorted", "Melody (Live at Wembley Stadium).mp3").version == [
        "live:wembley stadium"
    ]


def test_editing_the_title_of_a_found_song_changes_its_version_too(
    lib: Library, index: Index, adopted: Path
) -> None:
    rel = PurePosixPath(*adopted.relative_to(lib.root).parts).as_posix()
    assert tags.read_tags(adopted).version is None
    batch_id = edit(lib, index, rel, changes={"title": "Melody (Acoustic)"})
    renamed = adopted.with_name("03 Melody (Acoustic).mp3")
    assert tags.read_tags(renamed).version == ["acoustic"]
    pipeline.undo(lib, batch_id)
    assert (tags.read_tags(adopted).title, tags.read_tags(adopted).version) == ("Melody", None)


def test_the_tidy_commands(
    tmp_path: Path, rips: Path, samples: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "Library"
    library.init(root)
    with library.open(root, write=True) as opened, open_index(opened.paths, write=True) as ix:
        add_source(ix, rips)
        lost_pair(opened, ix, rips, samples)
        waiting_rip(ix, rips, samples["mp3"], "Band - Plain Day R")
        library_copy(opened, ix, rips / "Band - Plain Day R.mp3", "A Different Name")

    def run(*args: str, code: int = 0) -> str:
        assert cli.main(["--library", str(root), *args]) == code
        out, err = capsys.readouterr()
        return out + err

    made = json.loads(run("--json", "plan", "tidy"))
    assert made["summary"]["versions"] == 1
    assert made["summary"]["versions_skipped"] == {"title_names_no_version": 1}
    shown = run("plan", "show", made["plan_id"])
    assert "rename   Music/Band/Unsorted/Melody.mp3  →  Music/Band/Unsorted/Melody R.mp3" in shown
    assert "(title: Melody R, version: remix)" in shown
    assert "1 song(s) to give back the version their name lost (remix, live…)" in shown
    assert ("1 song(s) left as they are, though their rip names a version: their title was "
            "changed by hand and names none") in shown  # fmt: skip
    assert ("left     Music/Band/Unsorted/A Different Name.mp3  (its rip names a version; its "
            "title was changed by hand and names none)") in shown  # fmt: skip
    first = json.loads(run("--json", "apply", made["plan_id"]))["batch_id"]
    assert "Jobs this run: 1 done." in run("queue", "run")
    assert (root / "Music" / "Band" / "Unsorted" / "Melody R.mp3").is_file()
    would = run("undo", first, "--dry-run")
    assert "Restore the tags of Music/Band/Unsorted/Melody.mp3" in would
    assert "can't be restored" not in would and "Skipped" not in would

    again = json.loads(run("--json", "plan", "tidy"))
    assert (again["summary"]["versions"], again["summary"]["numbers_dropped"]) == (0, 1)
    assert again["summary"]["renames"] == 0
    second = json.loads(run("--json", "apply", again["plan_id"]))["batch_id"]
    run("queue", "run")
    refused = run("undo", first, code=1)
    assert f"Batch {first} can't be undone yet" in refused and f"musicorg undo {second}" in refused
    run("undo", second)
    assert "Undid 2 changes." in run("undo", first)
    assert (root / "Music" / "Band" / "Unsorted" / "Melody.mp3").is_file()
    assert (root / "Music" / "Band" / "Unsorted" / "Melody (2).mp3").is_file()


# ---- a download the owner asked for (v0.2) --------------------------------------------


def test_a_song_downloaded_on_request(
    lib: Library, index: Index, downloads: FakeDownloads, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = {VIDEO_A: candidate(VIDEO_A, "Melody", ("Band",), SECONDS, album="Tunes")}
    monkeypatch.setattr(youtube, "get_track", lambda video_id: found.get(video_id))

    plan = pipeline.plan_download(lib, index, [VIDEO_A, VIDEO_A, "goneGONEgon"])
    assert (plan.kind, plan.summary["downloads"]) == ("download", 1)
    assert plan.summary["skipped"] == {"not_on_youtube_music": 1}
    assert "download Band – Melody" in pipeline.describe(plan)[0]
    batch_id = pipeline.apply(lib, index, plan.plan_id).batch_id
    run_queue(lib)

    assert [f for f in music_files(lib) if f.endswith(".m4a")] == [
        "Band/Tunes (2020)/03 Melody.m4a"
    ]
    written = tags.read_tags(lib.paths.music / "Band" / "Tunes (2020)" / "03 Melody.m4a")
    assert (written.title, written.artist, written.album, written.track) == (
        "Melody", "Band", "Tunes", 3,
    )  # fmt: skip
    assert (written.source, written.source_id, written.source_format) == (
        "youtube_music", VIDEO_A, "140",
    )  # fmt: skip
    assert written.match is None and written.origin_path is None
    assert written.version is None  # its title names no version
    assert isinstance(written.musicorg_id, str)
    assert downloads.calls == [VIDEO_A]

    # Asked for again: it's in the library, so nothing is planned.
    again = pipeline.plan_download(lib, index, [VIDEO_A])
    assert again.operations == [] and again.summary["skipped"] == {"already_in_library": 1}
    with pytest.raises(UserError):
        pipeline.plan_download(lib, index, ["not a video id"])

    pipeline.undo(lib, batch_id)
    assert [f for f in music_files(lib) if f.endswith(".m4a")] == []
    assert index.library_tracks() == []


def test_a_download_that_isnt_what_was_asked_for_is_not_kept(
    lib: Library, index: Index, downloads: FakeDownloads, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrong_length = candidate(VIDEO_A, "Melody", ("Band",), SECONDS + 60, album="Tunes")
    monkeypatch.setattr(youtube, "get_track", lambda video_id: wrong_length)
    plan = pipeline.plan_download(lib, index, [VIDEO_A])
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    assert music_files(lib) == []
    (job,) = jobs(lib)
    assert (job["state"], job["reason"]) == ("needs_review", "duration_mismatch")


def test_a_downloaded_remix_says_which_version_it_is(
    lib: Library, index: Index, downloads: FakeDownloads, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MUSICORG_VERSION is written for a download too, from its official title, and the
    lyrics are looked up as that version's."""
    remix = candidate(VIDEO_A, "Melody (Somebody Remix)", ("Band",), SECONDS, album="Tunes")
    monkeypatch.setattr(youtube, "get_track", lambda video_id: remix)
    asked: list[tuple[str, ...]] = []
    monkeypatch.setattr(pipeline, "EXTRAS", [lambda query: asked.append(query.versions)])

    plan = pipeline.plan_download(lib, index, [VIDEO_A])
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)

    assert music_files(lib) == ["Band/Tunes (2020)/03 Melody (Somebody Remix).m4a"]
    written = tags.read_tags(
        lib.paths.music / "Band" / "Tunes (2020)" / "03 Melody (Somebody Remix).m4a"
    )
    assert (written.title, written.version) == ("Melody (Somebody Remix)", ["remix:somebody"])
    assert asked == [("remix:somebody",)]


def test_a_replacement_takes_its_version_from_the_official_title(
    lib: Library,
    index: Index,
    rips: Path,
    audio: AudioFixtures,
    downloads: FakeDownloads,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A match chosen from a review row or a search is stored with no version tokens of
    its own. The download's version tag used to come from those, so a remix got none."""
    asked: list[tuple[str, ...]] = []
    monkeypatch.setattr(pipeline, "EXTRAS", [lambda query: asked.append(query.versions)])
    shutil.copyfile(audio.melody_a_mp3, rips / "Band - Melody (Somebody Remix).mp3")
    iid = add_item(index, "Band - Melody (Somebody Remix)", state="matched_user", seconds=SECONDS)
    add_candidates(index, iid, [
        candidate(VIDEO_A, "Melody (Somebody Remix)", ("Band",), SECONDS, album="Tunes")
    ])  # fmt: skip
    rows = index.candidates(iid)
    for row in rows:
        row["payload"]["version_tokens"] = []
    index.set_match(iid, "matched_user", [], rows)

    plan_and_run(lib, index)

    assert music_files(lib) == ["Band/Tunes (2020)/03 Melody (Somebody Remix).m4a"]
    written = tags.read_tags(
        lib.paths.music / "Band" / "Tunes (2020)" / "03 Melody (Somebody Remix).m4a"
    )
    assert (written.version, written.match) == (["remix:somebody"], "user_confirmed")
    assert asked == [("remix:somebody",)]
    assert item_state(index, iid) == ("superseded", [])


# ---- a video saved whole (v0.2) ----------------------------------------------------------

VIDEO_V = "videoVVVVVV"


@dataclass
class FakeVideoDownloads:
    """youtube.download_video's stand-in: copies the video fixture into the job's folder."""

    source: Path
    calls: list[tuple[str, int, int | None]] = field(default_factory=list)
    format_id: str = "133+140"

    def __call__(
        self, video_id: str, dest: Path, *, height: int, fps: int | None = None,
        at_most: bool = False, progress: Any = None,
    ) -> tuple[Path, dict[str, Any]]:  # fmt: skip
        self.at_most = at_most
        self.calls.append((video_id, height, fps))
        target = Path(dest) / f"{video_id}.mp4"
        shutil.copyfile(self.source, target)
        return target, {"id": video_id, "format_id": self.format_id}


@pytest.fixture
def videos(monkeypatch: pytest.MonkeyPatch, video_mp4: Path, fpcalc: Path) -> FakeVideoDownloads:
    fake = FakeVideoDownloads(video_mp4)
    monkeypatch.setattr(youtube, "download_video", fake)
    found = candidate(VIDEO_V, "Melody", ("Band", "Guest"), SECONDS, album=None,
                      video_type=youtube.OFFICIAL_VIDEO)  # fmt: skip
    monkeypatch.setattr(
        youtube, "get_track", lambda video_id: found if video_id == VIDEO_V else None
    )
    return fake


def save_video(lib: Library, index: Index, height: int = 240) -> str:
    plan = pipeline.plan_download(lib, index, [], [{"video_id": VIDEO_V, "height": height}])
    batch_id = pipeline.apply(lib, index, plan.plan_id).batch_id
    run_queue(lib)
    return batch_id


def test_a_video_saved_on_request(
    lib: Library, index: Index, videos: FakeVideoDownloads, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fileops_support import image

    from musicorg import artwork

    monkeypatch.setattr(artwork, "art_from_url", lambda url: pytest.fail("no picture to fetch"))
    plan = pipeline.plan_download(
        lib, index, [],
        [{"video_id": VIDEO_V, "height": 240}, {"video_id": VIDEO_V, "height": 240},
         {"video_id": "goneGONEgon", "height": 240}],
    )  # fmt: skip
    assert (plan.kind, plan.summary["downloads"], plan.summary["videos"]) == ("download", 1, 1)
    assert plan.summary["skipped"] == {"not_on_youtube_music": 1}
    assert plan.summary["disk_mb"] > 0
    assert "video    Band, Guest – Melody (videoVVVVVV, 240p)" in pipeline.describe(plan)[0]
    batch_id = pipeline.apply(lib, index, plan.plan_id).batch_id
    result = run_queue(lib)

    # In its own folder, under the first artist, with no album, year or number.
    assert music_files(lib) == ["Videos/Band/Melody.mp4"]
    saved = lib.paths.music / "Videos" / "Band" / "Melody.mp4"
    written = tags.read_tags(saved)
    assert (written.title, written.artist, written.album_artist, written.album) == (
        "Melody", "Band, Guest", "Band", None,
    )  # fmt: skip
    assert (written.source, written.source_id, written.source_format) == (
        "youtube_music", VIDEO_V, "133+140",
    )  # fmt: skip
    assert written.match is None and isinstance(written.musicorg_id, str)
    assert written.source_bitrate is not None and 90 <= written.source_bitrate <= 160
    # Exactly what was delivered: tagging changed neither the sound nor the picture.
    assert tags.audio_hash(saved) == tags.audio_hash(videos.source)
    assert videos.calls == [(VIDEO_V, 240, None)]
    (row,) = index.library_tracks()
    assert row["rel_path"] == "Music/Videos/Band/Melody.mp4" and row["source_id"] == VIDEO_V
    # It counted as one download toward the day's limit, like a song.
    assert result.counts == {"done": 1}
    assert queue.status(lib.paths)["daily_count"] == 1

    again = pipeline.plan_download(lib, index, [], [{"video_id": VIDEO_V, "height": 240}])
    assert again.operations == [] and again.summary["skipped"] == {"already_in_library": 1}

    pipeline.undo(lib, batch_id)
    assert music_files(lib) == [] and index.library_tracks() == []

    # With a picture to fetch, the video's own frame becomes its cover.
    monkeypatch.setattr(
        artwork, "art_from_url", lambda url: artwork.Art(image("JPEG", size=32), 32, 32)
    )
    with_picture = candidate(VIDEO_V, "Melody", ("Band",), SECONDS, album=None)
    with_picture = replace(with_picture, thumbnail="https://example.invalid/frame.jpg")
    monkeypatch.setattr(youtube, "get_track", lambda video_id: with_picture)
    save_video(lib, index)
    assert isinstance(tags.read_tags(saved).cover, bytes)
    assert music_files(lib) == ["Videos/Band/Melody.mp4"]  # and no cover.jpg beside it


def test_a_saved_video_says_which_version_it_is(
    lib: Library, index: Index, videos: FakeVideoDownloads, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = candidate(VIDEO_V, "Melody (Live at Wembley)", ("Band",), SECONDS, album=None,
                     video_type=youtube.OFFICIAL_VIDEO)  # fmt: skip
    monkeypatch.setattr(youtube, "get_track", lambda video_id: live)
    save_video(lib, index)
    assert music_files(lib) == ["Videos/Band/Melody (Live at Wembley).mp4"]
    saved = song(lib, "Videos", "Band", "Melody (Live at Wembley).mp4")
    assert (saved.title, saved.version) == ("Melody (Live at Wembley)", ["live:wembley"])


def test_a_video_plan_refuses_what_it_cant_do(
    lib: Library, index: Index, videos: FakeVideoDownloads
) -> None:
    for wrong in ({"video_id": VIDEO_V}, {"video_id": VIDEO_V, "height": 700},
                  {"video_id": VIDEO_V, "height": "720"}, {"video_id": VIDEO_V, "height": True},
                  {"video_id": VIDEO_V, "height": 720, "fps": "60"}, "videoVVVVVV"):  # fmt: skip
        with pytest.raises(UserError, match="height|fps"):
            pipeline.plan_download(lib, index, [], [wrong])  # type: ignore[list-item]
    with pytest.raises(UserError, match="video id"):
        pipeline.plan_download(lib, index, [], [{"video_id": "not a video id", "height": 720}])


@pytest.mark.parametrize(
    ("height", "format_id", "reason"),
    [
        (480, "133+140", "video_format_unavailable"),  # the file is 240 lines, not 480
        (240, "133+251", "video_format_unavailable"),  # the sound isn't format 140
        (240, "18", "video_format_unavailable"),  # one combined stream, not what's kept
    ],
)
def test_a_video_that_isnt_what_was_asked_for_is_not_kept(
    lib: Library, index: Index, videos: FakeVideoDownloads, height: int, format_id: str,
    reason: str,
) -> None:  # fmt: skip
    videos.format_id = format_id
    save_video(lib, index, height)
    assert music_files(lib) == [] and index.library_tracks() == []
    (job,) = jobs(lib)
    assert (job["state"], job["reason"]) == ("needs_review", reason)


def test_a_video_of_the_wrong_length_is_not_kept(
    lib: Library, index: Index, videos: FakeVideoDownloads, monkeypatch: pytest.MonkeyPatch
) -> None:
    longer = candidate(VIDEO_V, "Melody", ("Band",), SECONDS + 60, album=None)
    monkeypatch.setattr(youtube, "get_track", lambda video_id: longer)
    save_video(lib, index)
    assert music_files(lib) == []
    (job,) = jobs(lib)
    assert (job["state"], job["reason"]) == ("needs_review", "duration_mismatch")


def test_a_saved_video_stays_a_video(
    lib: Library, index: Index, videos: FakeVideoDownloads
) -> None:
    """Tidying, lyrics, covers and edits by hand all leave it in Videos/, untouched by
    what's meant for songs."""
    save_video(lib, index)
    rel = "Music/Videos/Band/Melody.mp4"

    assert pipeline.plan_tidy(lib, index).operations == []  # not "renamed" into an album
    lyrics_plan = pipeline.plan_lyrics(lib, index, missing=True)
    assert lyrics_plan.operations == [] and lyrics_plan.summary["skipped"] == {"video": 1}
    art_plan = pipeline.plan_artwork(lib, index)
    assert art_plan.operations == [] and art_plan.summary["skipped"] == {"video": 1}

    # A new title renames it where it is; an album doesn't move it into Music/<Artist>/.
    edit(lib, index, rel, changes={"title": "New Name", "album": "Some Album", "track": 4})
    assert music_files(lib) == ["Videos/Band/New Name.mp4"]
    renamed = lib.paths.music / "Videos" / "Band" / "New Name.mp4"
    assert tags.read_tags(renamed).title == "New Name"
    assert tags.probe(renamed).height == 240


# ---- the owner's unfinished downloads (v0.2) ---------------------------------------------


def test_unfinished_downloads_are_listed_and_can_be_dismissed(
    lib: Library, index: Index, downloads: FakeDownloads, videos: FakeVideoDownloads,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    song = candidate(VIDEO_A, "Melody", ("Band",), SECONDS + 60, album="Tunes")  # wrong length
    video = candidate(VIDEO_V, "Melody", ("Band", "Guest"), SECONDS, album=None)
    monkeypatch.setattr(youtube, "get_track", lambda vid: song if vid == VIDEO_A else video)
    assert queue.downloads(lib.paths) == []

    plan = pipeline.plan_download(lib, index, [VIDEO_A], [{"video_id": VIDEO_V, "height": 240}])
    batch_id = pipeline.apply(lib, index, plan.plan_id).batch_id
    waiting = queue.downloads(lib.paths)
    assert [(d["video_id"], d["state"], d["video"], d["height"]) for d in waiting] == [
        (VIDEO_V, "queued", True, 240),
        (VIDEO_A, "queued", False, None),
    ]  # fmt: skip  (newest first)
    assert waiting[0]["title"] == "Melody" and waiting[0]["artists"] == ["Band", "Guest"]

    # The video is taken off the list before it starts: it's never downloaded.
    queue.dismiss_download(lib, waiting[0]["job_id"])
    run_queue(lib)
    assert videos.calls == [] and music_files(lib) == []
    (failed,) = queue.downloads(lib.paths)  # the song didn't pass its checks
    assert (failed["video_id"], failed["state"], failed["reason"]) == (
        VIDEO_A, "needs_review", "duration_mismatch",
    )  # fmt: skip
    assert "long" in failed["message"]

    # Dismissed, it's no longer shown; and it can't be dismissed twice.
    queue.dismiss_download(lib, failed["job_id"])
    assert queue.downloads(lib.paths) == []
    with pytest.raises(NotFoundError):
        queue.dismiss_download(lib, failed["job_id"])
    assert {job["state"] for job in jobs(lib)} == {"cancelled"}
    assert fileops.read_journal(lib)[batch_id].status == "closed"


def test_a_download_dismissed_while_waiting_closes_its_batch(
    lib: Library, index: Index, videos: FakeVideoDownloads
) -> None:
    plan = pipeline.plan_download(lib, index, [], [{"video_id": VIDEO_V, "height": 240}])
    batch_id = pipeline.apply(lib, index, plan.plan_id).batch_id
    (waiting,) = queue.downloads(lib.paths)
    queue.dismiss_download(lib, waiting["job_id"])
    assert fileops.read_journal(lib)[batch_id].status == "closed"
    # A job that's running can't be taken away from under itself.
    plan = pipeline.plan_download(lib, index, [], [{"video_id": VIDEO_V, "height": 240}])
    pipeline.apply(lib, index, plan.plan_id)
    (waiting,) = queue.downloads(lib.paths)
    with open_queue(lib.paths, write=True) as store:
        store.update_job(waiting["job_id"], "2026-10-01T00:00:00Z", state="running")
    with pytest.raises(UserError, match="downloading right now"):
        queue.dismiss_download(lib, waiting["job_id"])


def test_every_waiting_download_can_be_cancelled_at_once(
    lib: Library, index: Index, downloads: FakeDownloads, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = {
        VIDEO_A: candidate(VIDEO_A, "Melody", ("Band",), SECONDS, album="Tunes"),
        VIDEO_B: candidate(VIDEO_B, "Other", ("Band",), SECONDS, album="Tunes"),
        VIDEO_V: candidate(VIDEO_V, "Third", ("Band",), SECONDS, album="Tunes"),
    }
    monkeypatch.setattr(youtube, "get_track", lambda video_id: found.get(video_id))
    assert queue.dismiss_waiting(lib) == 0  # nothing waiting: nothing to do

    # Two asked for together, and one by itself that has already started.
    together = pipeline.apply(
        lib, index, pipeline.plan_download(lib, index, [VIDEO_A, VIDEO_B]).plan_id
    ).batch_id
    alone = pipeline.apply(lib, index, pipeline.plan_download(lib, index, [VIDEO_V]).plan_id)
    started = next(d for d in queue.downloads(lib.paths) if d["video_id"] == VIDEO_V)
    with open_queue(lib.paths, write=True) as store:
        store.update_job(started["job_id"], "2026-10-02T00:00:00Z", state="running")

    assert queue.dismiss_waiting(lib) == 2
    (left,) = queue.downloads(lib.paths)  # the one that's downloading carries on
    assert (left["video_id"], left["state"]) == (VIDEO_V, "running")
    journal = fileops.read_journal(lib)
    assert journal[together].status == "closed" and journal[alone.batch_id].status != "closed"
    assert queue.dismiss_waiting(lib) == 0


def test_a_download_for_a_playlist_joins_it_when_it_arrives(
    lib: Library, index: Index, downloads: FakeDownloads, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = {
        VIDEO_A: candidate(VIDEO_A, "Melody", ("Band",), SECONDS, album="Tunes"),
        VIDEO_B: candidate(VIDEO_B, "Other", ("Band",), SECONDS, album="Tunes"),
    }
    monkeypatch.setattr(youtube, "get_track", lambda video_id: found.get(video_id))
    (road_trip,) = listening.create_playlist(lib, "Road Trip")
    with pytest.raises(NotFoundError):
        pipeline.plan_download(lib, index, [VIDEO_A], playlist_id="pl_gone")

    plan = pipeline.plan_download(lib, index, [VIDEO_A, VIDEO_B], playlist_id=road_trip["id"])
    assert [op.params["playlist_id"] for op in plan.operations] == [road_trip["id"]] * 2
    pipeline.apply(lib, index, plan.plan_id)
    assert listening.get(lib)["playlists"][0]["track_ids"] == []  # nothing has arrived yet
    run_queue(lib)
    arrived = [
        row["musicorg_id"]
        for video_id in (VIDEO_A, VIDEO_B)  # in the order asked for
        for row in index.library_tracks_with_source_id(video_id)
    ]
    assert len(arrived) == 2 and all(arrived)
    assert listening.get(lib)["playlists"][0]["track_ids"] == arrived

    # A playlist deleted while its songs were on the way: they still arrive.
    listening.delete_playlist(lib, road_trip["id"])
    (gone,) = listening.create_playlist(lib, "Gone")
    for row in index.library_tracks_with_source_id(VIDEO_B):
        lib.root.joinpath(*row["rel_path"].split("/")).unlink()
    index.remove_library_tracks(
        [row["rel_path"] for row in index.library_tracks_with_source_id(VIDEO_B)]
    )
    plan = pipeline.plan_download(lib, index, [VIDEO_B], playlist_id=gone["id"])
    pipeline.apply(lib, index, plan.plan_id)
    listening.delete_playlist(lib, gone["id"])
    run_queue(lib)
    assert index.library_tracks_with_source_id(VIDEO_B)
    assert listening.get(lib)["playlists"] == []


def test_a_playlist_is_copied_in_from_another_profiles_library(
    lib: Library, index: Index, downloads: FakeDownloads, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    found = {
        VIDEO_A: candidate(VIDEO_A, "Melody", ("Band",), SECONDS, album="Tunes"),
        VIDEO_B: candidate(VIDEO_B, "Other", ("Band",), SECONDS, album="Tunes"),
    }
    monkeypatch.setattr(youtube, "get_track", lambda video_id: found.get(video_id))
    # Their library: two songs downloaded, one with lyrics beside it.
    theirs_root = tmp_path / "Their Library"
    library.init(theirs_root)
    theirs = library.open(theirs_root, write=True, command="pytest")
    try:
        with open_index(theirs.paths, write=True) as their_index:
            plan = pipeline.plan_download(theirs, their_index, [VIDEO_A, VIDEO_B])
            pipeline.apply(theirs, their_index, plan.plan_id)
            run_queue(theirs)
            rows = sorted(their_index.library_tracks(), key=lambda row: row["rel_path"])
        their_songs = [row["rel_path"] for row in rows]
        lyrics_file = theirs.root.joinpath(*their_songs[0].split("/")).with_suffix(".lrc")
        lyrics_file.write_text("[00:01.00] la la\n", encoding="utf-8")
    finally:
        theirs.close()
    before = {p: p.stat().st_mtime_ns for p in theirs_root.rglob("*") if p.is_file()}

    # Copied into this library, into a playlist here.
    (mix,) = listening.create_playlist(lib, "From Them")
    plan = pipeline.plan_share(lib, index, theirs_root, their_songs, playlist_id=mix["id"])
    assert plan.kind == "share" and plan.summary["copies"] == 2
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    mine = sorted(index.library_tracks(), key=lambda row: row["rel_path"])
    assert [row["rel_path"] for row in mine] == their_songs  # the same places under Music/
    assert [row["musicorg_id"] for row in mine] == [row["musicorg_id"] for row in rows]
    assert lib.root.joinpath(*their_songs[0].split("/")).with_suffix(".lrc").is_file()
    assert listening.get(lib)["playlists"][0]["track_ids"] == [r["musicorg_id"] for r in rows]
    # Their library was only read.
    assert {p: p.stat().st_mtime_ns for p in theirs_root.rglob("*") if p.is_file()} == before

    # Again: nothing is copied twice, and the songs join the playlist they're asked for.
    (again,) = [p for p in listening.create_playlist(lib, "Again") if p["name"] == "Again"]
    plan = pipeline.plan_share(lib, index, theirs_root, their_songs, playlist_id=again["id"])
    assert plan.summary["copies"] == 0 and plan.summary["already_here"] == 2
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    assert len(index.library_tracks()) == 2
    assert listening.get(lib)["playlists"][1]["track_ids"] == [r["musicorg_id"] for r in rows]


def test_a_song_this_library_has_under_its_own_copy_isnt_copied_in_again(
    lib: Library, index: Index, downloads: FakeDownloads, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    found = {
        VIDEO_A: candidate(VIDEO_A, "Melody", ("Band",), SECONDS, album="Tunes"),
        VIDEO_B: candidate(VIDEO_B, "Melody (Night Remix)", ("Band",), SECONDS, album="Tunes"),
    }
    monkeypatch.setattr(youtube, "get_track", lambda video_id: found.get(video_id))
    theirs_root = tmp_path / "Their Library"
    library.init(theirs_root)
    theirs = library.open(theirs_root, write=True, command="pytest")
    try:
        with open_index(theirs.paths, write=True) as their_index:
            plan = pipeline.plan_download(theirs, their_index, [VIDEO_A, VIDEO_B])
            pipeline.apply(theirs, their_index, plan.plan_id)
            run_queue(theirs)
            their_songs = sorted(row["rel_path"] for row in their_index.library_tracks())
    finally:
        theirs.close()

    # This library has "Melody" by Band already, as a copy of its own: another file,
    # another id, nothing of where it came from. And a song of that name by someone else.
    own_rows = []
    for number, (title, artist) in enumerate((("melody", "BAND"), ("Melody", "Somebody Else"))):
        rel = f"Music/{artist}/Mine/{title} {number}.m4a"
        path = lib.root.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        own_rows.append({
            "rel_path": rel, "musicorg_id": f"mine_{number}", "size": 0, "mtime_ns": 0,
            "title": title, "artist": artist, "album": None, "duration_s": 200.0,
            "source": "rip_copy", "source_id": None, "only_copy": 0, "origin_path": None,
            "details_json": "{}", "match": None,
        })  # fmt: skip
    index.put_library_tracks(own_rows)

    (mix,) = listening.create_playlist(lib, "From Them")
    plan = pipeline.plan_share(lib, index, theirs_root, their_songs, playlist_id=mix["id"])
    # "Melody" is here already (by its name and artist): only the remix is copied.
    assert (plan.summary["copies"], plan.summary["already_here"]) == (1, 1)
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    assert len(index.library_tracks()) == 3
    in_list = listening.get(lib)["playlists"][0]["track_ids"]
    assert "mine_0" in in_list and "mine_1" not in in_list and len(in_list) == 2

    # Two songs here that answer to the name: it isn't clear which, so it's copied.
    index.put_library_tracks([{**own_rows[0], "rel_path": "Music/BAND/Mine/melody again.m4a",
                               "musicorg_id": "mine_2"}])  # fmt: skip
    lib.root.joinpath("Music", "BAND", "Mine", "melody again.m4a").write_bytes(b"")
    index.remove_library_tracks(
        [r["rel_path"] for r in index.library_tracks() if r.get("source_id") == VIDEO_B]
    )
    plan = pipeline.plan_share(lib, index, theirs_root, their_songs)
    assert (plan.summary["copies"], plan.summary["already_here"]) == (2, 0)

    # Only songs inside their Music folder, and not this library itself.
    for bad in ("state.json", "Music/../state.json", "../Their Library/Music/x.m4a", 3):
        with pytest.raises(UserError):
            pipeline.plan_share(lib, index, theirs_root, [bad])  # type: ignore[list-item]
    with pytest.raises(UserError, match="this library already"):
        pipeline.plan_share(lib, index, lib.root, their_songs)
    with pytest.raises(UserError, match="isn't there"):
        pipeline.plan_share(lib, index, tmp_path / "Nowhere", their_songs)


def test_a_download_is_given_a_genre(
    lib: Library, index: Index, downloads: FakeDownloads, samples: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    found = {
        VIDEO_B: candidate(VIDEO_B, "Other", ("Band",), SECONDS, album="Tunes"),
        VIDEO_A: candidate(VIDEO_A, "Melody", ("Band",), SECONDS, album="Tunes"),
    }
    monkeypatch.setattr(youtube, "get_track", lambda video_id: found.get(video_id))

    def genre_of(video_id: str) -> object:
        (row,) = index.library_tracks_with_source_id(video_id)
        return tags.read_tags(lib.root.joinpath(*row["rel_path"].split("/"))).genre

    # Found by Discover under a genre: that's its genre.
    known = [{**found[VIDEO_B].to_dict(), "genre": "Hip Hop"}]
    plan = pipeline.plan_download(lib, index, [VIDEO_B], known=known)
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    assert genre_of(VIDEO_B) == "Hip Hop"

    # Downloaded by name: the genre the owner's own songs by the artist have. A
    # download's genre (the one above) doesn't count towards that.
    browse.tracks(lib, index)  # reads each file's details into the index, as the app does
    assert browse.artist_genre(index, ("Band",)) is None
    mine = lib.paths.music / "Band" / "Unsorted" / "Mine.mp3"  # one of the owner's own
    mine.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(samples["mp3"], mine)
    tags.write_tags(mine, tags.TrackTags(title="Mine", artist="Band & Friend", genre="Rock",
                                         musicorg_id="t_mine", source="rip_copy"))  # fmt: skip
    scan.scan_library(lib, index)
    browse.tracks(lib, index)
    assert browse.artist_genre(index, ("Somebody", "The Band")) == "Rock"
    assert browse.artist_genre(index, ("Friendly",)) is None
    assert browse.artist_genre(index, ()) is None
    plan = pipeline.plan_download(lib, index, [VIDEO_A])
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    assert genre_of(VIDEO_A) == "Rock"


# ---- how far along a download is (v0.2) ---------------------------------------------------


def test_a_running_download_says_how_far_along_it_is(
    lib: Library, index: Index, downloads: FakeDownloads, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = {VIDEO_A: candidate(VIDEO_A, "Melody", ("Band",), SECONDS, album="Tunes")}
    monkeypatch.setattr(youtube, "get_track", lambda video_id: found.get(video_id))
    seen: list[tuple[str, float | None]] = []

    def look() -> None:
        (row,) = queue.downloads(lib.paths)
        seen.append((row["state"], row["progress"]))

    def download(video_id: str, dest: Path, *, progress: Any = None) -> Any:
        look()  # started, and YouTube hasn't said how big it is
        progress(1000, 4000)
        look()
        progress(3000, 4000)
        look()
        progress(4000, 4000)
        return downloads(video_id, dest)

    monkeypatch.setattr(youtube, "download_audio", download)
    plan = pipeline.plan_download(lib, index, [VIDEO_A])
    pipeline.apply(lib, index, plan.plan_id)
    (waiting,) = queue.downloads(lib.paths)
    assert (waiting["state"], waiting["progress"]) == ("queued", None)
    run_queue(lib)
    assert seen == [("running", None), ("running", 0.25), ("running", 0.75)]
    assert queue.downloads(lib.paths) == []  # it arrived
    assert queue._progress == {}  # and nothing is remembered about it


def test_a_videos_two_files_count_as_one_download() -> None:
    # Its picture, then its sound: the second file's bytes follow on from the first's,
    # and the sound's size is guessed until it starts, so the share never jumps back.
    forwarded: list[tuple[int, int | None]] = []
    meter = queue._Meter(7, lambda done, total: forwarded.append((done, total)),
                         expected_extra=1000)  # fmt: skip
    try:
        assert queue.progress_of(7) is None  # not started
        meter(0, None)
        assert queue.progress_of(7) is None  # YouTube hasn't said how big it is
        meter(4500, 9000)
        assert queue.progress_of(7) == 0.45  # of 9000 + the 1000 guessed
        meter(9000, 9000)
        assert queue.progress_of(7) == 0.9
        meter(100, 1200)  # the sound has started, and is a little bigger than guessed
        assert queue.progress_of(7) == pytest.approx(9100 / 10200, abs=0.001)
        meter(1200, 1200)
        assert queue.progress_of(7) == 1.0
        meter.finished()
        assert queue.progress_of(7) == 1.0
        assert forwarded[-1] == (1200, 1200) and len(forwarded) == 5
    finally:
        queue._progress.pop(7, None)
    assert queue.progress_of(7) is None and queue.progress_of(12345) is None


# ---- deleting a download (v0.2) ---------------------------------------------------------


def test_a_download_can_be_deleted(
    lib: Library, index: Index, downloads: FakeDownloads, fake_trash: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    found = {VIDEO_A: candidate(VIDEO_A, "Melody", ("Band",), SECONDS, album="Tunes")}
    monkeypatch.setattr(youtube, "get_track", lambda video_id: found.get(video_id))
    plan = pipeline.plan_download(lib, index, [VIDEO_A])
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    song = lib.paths.music / "Band" / "Tunes (2020)" / "03 Melody.m4a"
    song.with_suffix(".lrc").write_text("[00:01.00]Made-up line\n", encoding="utf-8")
    (song.parent / "cover.jpg").write_bytes(b"\xff\xd8cover")
    rel = "Music/Band/Tunes (2020)/03 Melody.m4a"
    track_id = str(tags.read_tags(song).musicorg_id)
    listening.set_favourite(lib, track_id, True)

    plan = pipeline.plan_remove(lib, index, [rel, rel])
    assert (plan.kind, plan.summary["operations"]) == ("remove", 1)
    assert pipeline.describe(plan) == [f"    1  delete   {rel}  (to the Trash)"]
    batch_id = pipeline.apply(lib, index, plan.plan_id).batch_id
    run_queue(lib)

    # The song, its lyrics and (as the album's last song) its cover are in the Trash,
    # and the folders that held only them are gone.
    assert music_files(lib) == []
    assert not (lib.paths.music / "Band").exists()
    assert sorted(path.name for path in fake_trash.sent) == [
        "03 Melody.lrc", "03 Melody.m4a", "cover.jpg",
    ]  # fmt: skip
    assert index.library_tracks() == []
    assert listening.get(lib)["favourites"] == []  # it isn't a favourite any more
    record = fileops.read_journal(lib)[batch_id]
    assert (record.kind, record.status) == ("remove", "closed")
    assert [op.op for op in record.ops] == ["trash", "trash", "trash"]
    # It can be downloaded again: nothing says it's still here.
    assert pipeline.plan_download(lib, index, [VIDEO_A]).summary["downloads"] == 1
    # Undo can't reach into the Trash: it says to put the files back by hand.
    steps = pipeline.undo(lib, batch_id, dry_run=True).steps
    assert {step.status for step in steps} == {"manual"}


def test_a_download_can_be_taken_out_of_the_library_and_kept(
    lib: Library, index: Index, downloads: FakeDownloads, fake_trash: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    # The app's Delete from Library (the owner, 2026-10-10): out of the library, and
    # the file kept on the computer, set aside in _Replaced.
    found = {VIDEO_A: candidate(VIDEO_A, "Melody", ("Band",), SECONDS, album="Tunes")}
    monkeypatch.setattr(youtube, "get_track", lambda video_id: found.get(video_id))
    plan = pipeline.plan_download(lib, index, [VIDEO_A])
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    song = lib.paths.music / "Band" / "Tunes (2020)" / "03 Melody.m4a"
    sound = song.read_bytes()
    song.with_suffix(".lrc").write_text("[00:01.00]Made-up line\n", encoding="utf-8")
    (song.parent / "cover.jpg").write_bytes(b"\xff\xd8cover")
    rel = "Music/Band/Tunes (2020)/03 Melody.m4a"
    track_id = str(tags.read_tags(song).musicorg_id)
    listening.set_favourite(lib, track_id, True)

    plan = pipeline.plan_remove(lib, index, [rel], keep=True)
    assert (plan.kind, plan.summary["operations"], plan.summary["set_aside"]) == ("remove", 1, 1)
    assert pipeline.describe(plan) == [
        f"    1  delete   {rel}  (out of the library, kept in _Replaced)"
    ]
    batch_id = pipeline.apply(lib, index, plan.plan_id).batch_id
    run_queue(lib)

    # Nothing of it is left under Music, nothing went to the Trash, and the song, its
    # lyrics and (as the album's last song) its cover are in _Replaced, as they were.
    assert music_files(lib) == []
    assert not (lib.paths.music / "Band").exists()
    assert fake_trash.sent == []
    kept = lib.paths.replaced / "Band" / "Tunes (2020)"
    assert sorted(path.name for path in kept.iterdir()) == [
        "03 Melody.lrc", "03 Melody.m4a", "cover.jpg",
    ]  # fmt: skip
    assert (kept / "03 Melody.m4a").read_bytes() == sound
    assert index.library_tracks() == []
    assert listening.get(lib)["favourites"] == []
    record = fileops.read_journal(lib)[batch_id]
    assert (record.kind, record.status) == ("remove", "closed")
    assert [op.op for op in record.ops] == ["supersede", "supersede", "supersede"]
    # It can be downloaded again: nothing says it's still here.
    assert pipeline.plan_download(lib, index, [VIDEO_A]).summary["downloads"] == 1

    # Undo puts the files back where they were.
    pipeline.undo(lib, batch_id)
    assert sorted(music_files(lib)) == [
        "Band/Tunes (2020)/03 Melody.lrc", "Band/Tunes (2020)/03 Melody.m4a",
        "Band/Tunes (2020)/cover.jpg",
    ]  # fmt: skip
    assert song.read_bytes() == sound
    # And the library lists it again, as the app's list of songs is made.
    with open_index(lib.paths, write=True) as again:
        assert [t["path"] for t in browse.tracks(lib, again)] == [rel]


def test_a_plan_made_before_keeping_was_offered_still_goes_to_the_trash(
    lib: Library, index: Index, videos: FakeVideoDownloads, fake_trash: Any
) -> None:
    save_video(lib, index)
    (rel,) = [t["rel_path"] for t in index.library_tracks()]
    plan = pipeline.plan_remove(lib, index, [rel])
    assert "keep" not in plan.operations[0].params and plan.summary["set_aside"] == 0
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    assert [path.suffix for path in fake_trash.sent] == [".mp4"]
    assert not lib.paths.replaced.exists() or not any(lib.paths.replaced.rglob("*.mp4"))


def test_undo_doesnt_take_a_second_download_for_the_first(
    lib: Library, index: Index, downloads: FakeDownloads, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A song is downloaded, deleted, and downloaded again: the same name, another file.
    Undoing the *first* download would take the second one out. It's refused, and the
    message doesn't send the owner to undo a delete, which can't be undone."""
    found = {VIDEO_A: candidate(VIDEO_A, "Melody", ("Band",), SECONDS, album="Tunes")}
    monkeypatch.setattr(youtube, "get_track", lambda video_id: found.get(video_id))
    monkeypatch.setattr(pipeline, "EXTRAS", [])
    rel = "Music/Band/Tunes (2020)/03 Melody.m4a"

    def download() -> str:
        return apply_and_run(lib, index, pipeline.plan_download(lib, index, [VIDEO_A]))

    first = download()
    deleted = apply_and_run(lib, index, pipeline.plan_remove(lib, index, [rel]))
    # Gone, with nothing in its place: undo just says the file isn't there any more.
    steps = pipeline.undo(lib, first, dry_run=True).steps
    assert [step.status for step in steps if step.op == "commit"] == ["skipped"]

    second = download()
    second_id = tags.read_tags(lib.root / rel).musicorg_id
    with pytest.raises(UndoError) as refused:
        pipeline.undo(lib, first)
    message = refused.value.message
    assert f"Batch {deleted} sent a file to the Trash" in message
    assert "can no longer be undone as a whole" in message and "musicorg undo" not in message
    assert tags.read_tags(lib.root / rel).musicorg_id == second_id  # the second is untouched

    pipeline.undo(lib, second)  # its own batch takes it back as ever
    assert music_files(lib) == []


def test_the_album_cover_stays_while_another_song_is_there(
    lib: Library, index: Index, downloads: FakeDownloads, fake_trash: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    found = {
        VIDEO_A: candidate(VIDEO_A, "Melody", ("Band",), SECONDS, album="Tunes"),
        VIDEO_B: candidate(VIDEO_B, "Other", ("Band",), SECONDS, album="Tunes"),
    }
    monkeypatch.setattr(youtube, "get_track", lambda video_id: found.get(video_id))
    plan = pipeline.plan_download(lib, index, [VIDEO_A, VIDEO_B])
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    (first, second) = sorted(f for f in music_files(lib) if f.endswith(".m4a"))
    folder = (lib.paths.music / first).parent
    (folder / "cover.jpg").write_bytes(b"\xff\xd8cover")

    plan = pipeline.plan_remove(lib, index, [f"Music/{first}"])
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    assert sorted(music_files(lib)) == sorted([second, f"{PurePosixPath(second).parent}/cover.jpg"])
    assert [t["rel_path"] for t in index.library_tracks()] == [f"Music/{second}"]


def test_only_a_download_can_be_deleted(lib: Library, index: Index, adopted: Path) -> None:
    rel = PurePosixPath(*adopted.relative_to(lib.root).parts).as_posix()
    with pytest.raises(UserError, match="came from your own files"):
        pipeline.plan_remove(lib, index, [rel])  # the owner's own rip, copied in
    with pytest.raises(UserError, match="Choose the downloads"):
        pipeline.plan_remove(lib, index, [])
    with pytest.raises(NotFoundError):
        pipeline.plan_remove(lib, index, ["Music/Nobody/nothing.m4a"])
    with pytest.raises(OutsideLibraryError):
        pipeline.plan_remove(lib, index, ["../outside.m4a"])
    assert adopted.is_file()


def test_a_saved_video_can_be_deleted(
    lib: Library, index: Index, videos: FakeVideoDownloads, fake_trash: Any
) -> None:
    save_video(lib, index)
    (rel,) = [t["rel_path"] for t in index.library_tracks()]
    assert naming.is_video_path(rel)
    plan = pipeline.plan_remove(lib, index, [rel])
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    assert index.library_tracks() == []
    assert [path.suffix for path in fake_trash.sent] == [".mp4"]
    assert not (lib.paths.music / naming.VIDEOS_DIR).exists()


# ---- edits by hand (v0.2) --------------------------------------------------------------


def edit(lib: Library, index: Index, rel: str, **kw: Any) -> str:
    plan = pipeline.plan_edit(lib, index, rel, **kw)
    batch_id = pipeline.apply(lib, index, plan.plan_id).batch_id
    run_queue(lib)
    return batch_id


def test_editing_names_retags_and_moves_the_song(lib: Library, index: Index, adopted: Path) -> None:
    rel = PurePosixPath(*adopted.relative_to(lib.root).parts).as_posix()
    before = tags.read_tags(adopted)
    adopted.with_suffix(".lrc").write_text("[00:01.00]Made-up line\n", encoding="utf-8")

    plan = pipeline.plan_edit(lib, index, rel, changes={
        "title": "  New   Name ", "artist": "Other Band", "album_artist": "Other Band",
        "album": None, "year": 1999, "track": None, "genre": "Jazz"})  # fmt: skip
    assert "edit" in pipeline.describe(plan)[0]
    batch_id = pipeline.apply(lib, index, plan.plan_id).batch_id
    run_queue(lib)

    final = lib.paths.music / "Other Band" / "Unsorted" / "New Name.mp3"
    written = tags.read_tags(final)
    assert (written.title, written.artist, written.album, written.year, written.genre) == (
        "New Name", "Other Band", None, 1999, "Jazz",
    )  # fmt: skip
    assert written.musicorg_id == before.musicorg_id
    assert final.with_suffix(".lrc").is_file()  # its lyrics moved with it
    assert not adopted.exists()
    assert [t["rel_path"] for t in index.library_tracks()] == [
        "Music/Other Band/Unsorted/New Name.mp3"
    ]

    pipeline.undo(lib, batch_id)
    back = tags.read_tags(adopted)
    assert (back.title, back.artist, back.album, back.year) == (
        before.title, before.artist, before.album, before.year,
    )  # fmt: skip
    assert adopted.with_suffix(".lrc").is_file() and not final.exists()


def test_lyrics_put_in_by_hand(lib: Library, index: Index, adopted: Path) -> None:
    rel = PurePosixPath(*adopted.relative_to(lib.root).parts).as_posix()
    lrc = adopted.with_suffix(".lrc")

    edit(lib, index, rel, lyrics_text="[00:01.00]First made-up line\r\n[00:05.50]Second\n")
    assert lrc.read_text(encoding="utf-8") == "[00:01.00]First made-up line\n[00:05.50]Second"
    assert tags.read_tags(adopted).lyrics == "First made-up line\nSecond"

    # Plain words replace them, and the timed file (the wrong lyrics) is set aside.
    edit(lib, index, rel, lyrics_text="Just the words\nof the song")
    assert not lrc.exists()
    assert tags.read_tags(adopted).lyrics == "Just the words\nof the song"
    assert len(list(lib.paths.replaced.rglob("*.lrc"))) == 1

    edit(lib, index, rel, lyrics_text="")
    assert tags.read_tags(adopted).lyrics is None
    assert adopted.is_file()  # only lyrics changed: the song didn't move


def test_a_cover_chosen_by_hand(lib: Library, index: Index, adopted: Path, tmp_path: Path) -> None:
    rel = PurePosixPath(*adopted.relative_to(lib.root).parts).as_posix()
    picture = tmp_path / "front.jpg"
    picture.write_bytes(cover(900))
    (adopted.parent / "cover.jpg").write_bytes(cover(300))
    original = picture.read_bytes()

    batch_id = edit(lib, index, rel, cover_file=picture)
    embedded = tags.read_tags(adopted).cover
    assert isinstance(embedded, bytes) and embedded[:2] == b"\xff\xd8"
    assert (adopted.parent / "cover.jpg").read_bytes() == embedded
    assert len(list(lib.paths.replaced.rglob("cover.jpg"))) == 1  # the old one, kept aside
    assert picture.read_bytes() == original  # the owner's picture is only read

    pipeline.undo(lib, batch_id)
    assert (adopted.parent / "cover.jpg").read_bytes() == cover(300)

    not_a_picture = tmp_path / "notes.txt"
    not_a_picture.write_text("hello")
    with pytest.raises(UserError, match="isn't a picture"):
        pipeline.plan_edit(lib, index, rel, cover_file=not_a_picture)
    with pytest.raises(UserError, match="couldn't be read"):
        pipeline.plan_edit(lib, index, rel, cover_file=tmp_path / "missing.jpg")


def test_editing_the_explicit_mark(lib: Library, index: Index, adopted: Path) -> None:
    """The mark comes from the match, not from listening: the owner can put it right."""
    rel = PurePosixPath(*adopted.relative_to(lib.root).parts).as_posix()
    was = tags.read_tags(adopted).explicit
    for value in (not was, None):
        plan = pipeline.plan_edit(lib, index, rel, changes={"explicit": value})
        pipeline.apply(lib, index, plan.plan_id)
        run_queue(lib)
        assert adopted.exists()  # the mark isn't part of the name: the file stays put
        assert tags.read_tags(adopted).explicit is value
    for wrong in (1, "yes"):
        with pytest.raises(UserError, match="true or false"):
            pipeline.plan_edit(lib, index, rel, changes={"explicit": wrong})


def test_edits_that_are_refused(lib: Library, index: Index, adopted: Path) -> None:
    rel = PurePosixPath(*adopted.relative_to(lib.root).parts).as_posix()
    current = tags.read_tags(adopted)
    for changes in ({"title": ""}, {"title": None}, {"year": 3}, {"track": "4"},
                    {"musicorg_id": "t_x"}, {"artist": 5}):  # fmt: skip
        with pytest.raises(UserError):
            pipeline.plan_edit(lib, index, rel, changes=changes)
    with pytest.raises(UserError, match="Nothing would change"):
        pipeline.plan_edit(lib, index, rel, changes={"title": current.title})
    with pytest.raises(OutsideLibraryError):
        pipeline.plan_edit(lib, index, "../elsewhere.mp3", changes={"title": "X"})


# ---- a video that isn't music, kept outside the library (2026-10-07) ----------------------------

KEPT = {"video_id": VIDEO_V, "title": "Speedrun: World  Record", "channel": "Some Channel",
        "duration_s": SECONDS, "thumbnail": "https://example.invalid/frame.jpg",
        "height": 1080}  # fmt: skip


def media_files(home: Path) -> list[str]:
    folder = home / "Movies" / "Videos"
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in for the owner's home folder, with a Movies folder in it."""
    from musicorg import config

    found = tmp_path / "home"
    (found / "Movies").mkdir(parents=True)
    monkeypatch.setenv(config.HOME_ENV, str(found))
    return found


def test_a_video_that_isnt_music_is_kept_outside_the_library(
    lib: Library, index: Index, videos: FakeVideoDownloads, home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    monkeypatch.setattr(youtube, "get_track", lambda video_id: pytest.fail("not looked up"))
    plan = pipeline.plan_download(lib, index, [], media=[KEPT, dict(KEPT)])
    assert (plan.summary["downloads"], plan.summary["kept"], plan.summary["videos"]) == (1, 1, 0)
    (line,) = pipeline.describe(plan)
    assert "keep     Speedrun: World Record (videoVVVVVV, up to 1080p, into Downloads)" in line
    pipeline.apply(lib, index, plan.plan_id)

    # While it waits, the Downloads list shows it like any other, marked as kept.
    (row,) = queue.downloads(lib.paths)
    assert (row["title"], row["artists"], row["video"], row["kept"], row["height"]) == (
        "Speedrun: World Record", ["Some Channel"], True, True, 1080,
    )  # fmt: skip

    result = run_queue(lib)
    assert result.counts == {"done": 1}
    # The largest picture up to the size asked for, through the paced queue, counted.
    assert videos.calls == [(VIDEO_V, 1080, None)] and videos.at_most is True
    assert queue.status(lib.paths)["daily_count"] == 1
    # In Movies/Videos under its title (made a safe file name), exactly as delivered.
    (name,) = media_files(home)
    assert name.startswith("Speedrun") and name.endswith("World Record.mp4")
    kept = home / "Movies" / "Videos" / name
    assert kept.read_bytes() == videos.source.read_bytes()
    # Nothing went into the library, the index, or stayed behind in staging.
    assert music_files(lib) == [] and index.library_tracks() == []
    assert not any(p.is_file() for p in lib.paths.staging.rglob("*"))
    assert queue.downloads(lib.paths) == []

    # Kept again, the first one isn't overwritten.
    plan = pipeline.plan_download(lib, index, [], media=[KEPT])
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    assert len(media_files(home)) == 2 and any(" (2).mp4" in n for n in media_files(home))


def test_a_kept_video_that_isnt_what_was_asked_for_goes_to_review(
    lib: Library, index: Index, videos: FakeVideoDownloads, home: Path
) -> None:
    # The fixture's picture is 240 lines: more than the 144 asked for at most.
    plan = pipeline.plan_download(lib, index, [], media=[{**KEPT, "height": 144}])
    pipeline.apply(lib, index, plan.plan_id)
    assert run_queue(lib).counts == {"needs_review": 1}
    assert media_files(home) == []
    (row,) = queue.downloads(lib.paths)
    assert row["reason"] == "video_format_unavailable" and "up to 144" in row["message"]

    # Nor one whose length isn't the one the list gave.
    plan = pipeline.plan_download(lib, index, [], media=[{**KEPT, "duration_s": SECONDS + 60}])
    pipeline.apply(lib, index, plan.plan_id)
    run_queue(lib)
    assert media_files(home) == []
    assert queue.downloads(lib.paths)[0]["reason"] == "duration_mismatch"


def test_a_kept_video_with_no_usable_title_is_named_by_its_id(
    lib: Library, index: Index, videos: FakeVideoDownloads, home: Path
) -> None:
    plan = pipeline.plan_download(lib, index, [], media=[{**KEPT, "title": ".DS_Store"}])
    pipeline.apply(lib, index, plan.plan_id)
    assert run_queue(lib).counts == {"done": 1}
    assert media_files(home) == [f"{VIDEO_V}.mp4"]


@pytest.mark.parametrize(
    "wrong",
    [
        "a video",
        {**KEPT, "video_id": "nope"},
        {**KEPT, "title": "  "},
        {**KEPT, "title": "x" * 301},
        {**KEPT, "height": 1000},
        {**KEPT, "height": True},
    ],
)
def test_a_video_to_keep_is_checked_before_anything_is_planned(
    lib: Library, index: Index, wrong: object
) -> None:
    with pytest.raises(UserError):
        pipeline.plan_download(lib, index, [], media=[wrong])  # type: ignore[list-item]
