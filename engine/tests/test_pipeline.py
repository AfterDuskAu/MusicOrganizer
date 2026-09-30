"""Replace and adopt (step 09b), end to end through the queue, with a stand-in for yt-dlp
that "downloads" the step 02 audio: a full replace, a fingerprint mismatch, two rips of
one video, a video already in the library, adopts, a rip changed after the plan, undo,
and calibration mode."""

from __future__ import annotations

import csv
import json
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from conftest import AudioFixtures, require_tool
from index_support import add_candidates, add_item, add_source, candidate

from musicorg import cli, fileops, library, match, pipeline, queue, review, state, tags, youtube
from musicorg.config import Config
from musicorg.errors import PlanOutOfDateError, UserError
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
    monkeypatch.setattr(youtube, "get_album", lambda browse_id: album)
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
    clock = queue.Clock(sleep=lambda s: None, uniform=lambda a, b: a)
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

    def extras(c: youtube.Candidate, album: pipeline.AlbumInfo) -> pipeline.Extras:
        seen.append(c.video_id)
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
