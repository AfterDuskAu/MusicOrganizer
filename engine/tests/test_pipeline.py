"""Replace and adopt (step 09b), end to end through the queue, with a stand-in for yt-dlp
that "downloads" the step 02 audio: a full replace, a fingerprint mismatch, two rips of
one video, a video already in the library, adopts, a rip changed after the plan, undo,
and calibration mode."""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from conftest import AudioFixtures, require_tool
from index_support import add_candidates, add_item, add_source, candidate

from musicorg import (
    artwork,
    cli,
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
from musicorg.errors import NotFoundError, OutsideLibraryError, PlanOutOfDateError, UserError
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
    assert (plan.summary["duplicates"], plan.summary["renames"]) == (1, 1)
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
        progress: Any = None,
    ) -> tuple[Path, dict[str, Any]]:  # fmt: skip
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
