"""The decision report (step 07): how much of the library can be replaced cleanly, how
much needs the owner, and how much exists nowhere else.

`musicorg report` writes report-YYYY-MM-DD.md (to read) and report-YYYY-MM-DD.csv (every
item, for a spreadsheet) through `fileops.write_export`, into Reports/ unless another
folder is given. It only reads the index, so it needs no lock.

"unsupported_format" (docs/LIBRARY_CONTRACT.md section 3): a WebM, raw AAC or WAV rip
with no official match. It can't be adopted in v0.1, so the report counts it apart from
`not_found` and `only_copy`.
"""

from __future__ import annotations

import csv
import io
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from musicorg import fileops, scan, state
from musicorg.config import Config
from musicorg.index import Index
from musicorg.library import Library
from musicorg.normalize import SOFT_VERSION_KINDS, render_versions

# docs/ENGINE_API.md → Enums, in the order the report lists them.
ITEM_STATES = ("matched_auto", "matched_user", "review", "not_found", "only_copy", "skipped",
               "unsupported_format", "superseded", "adopted", "new")  # fmt: skip
REVIEW_REASONS = ("version_mismatch", "duration_mismatch", "artist_mismatch", "title_fuzzy",
                  "not_official_audio", "low_parse_confidence", "fingerprint_mismatch",
                  "fingerprint_uncertain", "format_140_unavailable", "video_unavailable",
                  "file_changed", "url_low_score")  # fmt: skip
STATE_MEANING = {
    "matched_auto": "official match found; safe to queue",
    "matched_user": "you chose the match",
    "review": "needs your decision",
    "not_found": "no official match",
    "only_copy": "you marked it as the only copy",
    "skipped": "you chose to leave it",
    "unsupported_format": "no match, and WebM/AAC/WAV can't be adopted yet",
    "superseded": "replaced by an official download",
    "adopted": "copied into the library",
    "new": "not matched yet",
}
REASON_MEANING = {
    "version_mismatch": "a different version (remix, live, clean/explicit…)",
    "duration_mismatch": "length more than 2 s off",
    "artist_mismatch": "a different artist",
    "title_fuzzy": "title not exactly the same",
    "not_official_audio": "not an official audio track",
    "low_parse_confidence": "the file's name was hard to read",
    "fingerprint_mismatch": "the audio differs",
    "fingerprint_uncertain": "the audio comparison was unsure",
    "format_140_unavailable": "the download format isn't offered",
    "video_unavailable": "the video is unavailable",
    "file_changed": "the file changed since the plan",
    "url_low_score": "a pasted link scored low",
}
NOT_FOUND_ADOPT = 0.30  # not_found share at which adopting matters as much as replacing
AUTO_REPLACE = 0.60  # matched_auto share at which replace-first cleans most of it
EXAMPLES = 30
DOWNLOAD_S = 10  # assumed time to fetch one track, on top of the pause
BITRATE_BANDS = ((0, 128, "under 128 kbps"), (128, 160, "128–159 kbps"),
                 (160, 192, "160–191 kbps"), (192, 256, "192–255 kbps"),
                 (256, 320, "256–319 kbps"), (320, 10**9, "320 kbps and over"))  # fmt: skip
LOSSLESS = frozenset({".wav", ".flac"})
VERSION_COLUMNS = ("matched_auto", "matched_user", "review", "not_found")
# How far off the length is, for review items where nothing else differs.
LENGTH_BANDS = ((5, "up to 5 s"), (10, "5–10 s"), (30, "10–30 s"), (10**9, "over 30 s"))


def report_state(item: dict[str, Any]) -> str:
    if "not_adoptable" in (item.get("flags_json") or []) and item["state"] in (
        "not_found", "only_copy"
    ):  # fmt: skip
        return "unsupported_format"
    return item["state"]


@dataclass
class Report:
    root: Path
    generated: datetime
    total: int = 0
    sources: int = 0
    states: Counter[str] = field(default_factory=Counter)
    review_reasons: Counter[str] = field(default_factory=Counter)
    length_only: Counter[str] = field(default_factory=Counter)  # band → n
    no_results: int = 0
    low_score: int = 0
    misses: list[dict[str, Any]] = field(default_factory=list)  # closest first
    versions: dict[str, Counter[str]] = field(default_factory=dict)  # kind → state → n
    bitrates: Counter[str] = field(default_factory=Counter)
    formats: Counter[str] = field(default_factory=Counter)
    suspect_upscale: int = 0
    unreadable: int = 0
    downloads: int = 0
    download_hours: float = 0.0
    download_days: int = 0
    throttle: dict[str, int] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)

    def share(self, n: int) -> float:
        return n / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "states": {s: self.states.get(s, 0) for s in ITEM_STATES},
            "review_reasons": dict(self.review_reasons),
            "not_found": {"no_results": self.no_results, "low_score": self.low_score},
            "suspect_upscale": self.suspect_upscale,
            "downloads": self.downloads,
            "download_days": self.download_days,
            "download_hours": round(self.download_hours, 1),
            "recommendations": self.recommendations,
        }


# ---- gathering -------------------------------------------------------------------------


def gather(lib: Library, index: Index, *, now: datetime | None = None) -> Report:
    items = index.items()
    candidates = index.all_candidates()
    report = Report(root=lib.root, generated=now or datetime.now().astimezone())
    report.total = len(items)
    report.sources = len(scan.source_folders(lib, index))
    report.states = Counter(report_state(item) for item in items)

    for item in items:
        state_name = report_state(item)
        if item["state"] == "review":
            report.review_reasons.update(list(dict.fromkeys(item.get("reasons_json") or [])))
            if item.get("reasons_json") == ["duration_mismatch"]:
                report.length_only[_length_band(item, candidates.get(item["id"]))] += 1
        _count_versions(report, item, state_name)
        _count_quality(report, item)

    missing = [i for i in items if report_state(i) == "not_found"]
    report.no_results = sum(1 for i in missing if not candidates.get(i["id"]))
    report.low_score = len(missing) - report.no_results
    misses = []
    for item in missing:
        best = candidates.get(item["id"], [None])[0]
        misses.append({
            "artist": item.get("parsed_artist") or "",
            "title": item.get("parsed_title") or "",
            "best": _describe(best["payload"]) if best else "",
            "score": best["score"] if best else None,
        })  # fmt: skip
    misses.sort(key=lambda m: -(m["score"] if m["score"] is not None else -1))
    report.misses = misses[:EXAMPLES]

    _estimate_downloads(report, Config.load().throttle())
    report.recommendations = recommend(report)
    return report


def _length_band(item: dict[str, Any], options: list[dict[str, Any]] | None) -> str:
    theirs = options[0]["payload"].get("duration_s") if options else None
    if item.get("duration_s") is None or theirs is None:
        return "unknown"
    delta = abs(item["duration_s"] - theirs)
    return next(name for limit, name in LENGTH_BANDS if delta <= limit)


def _count_versions(report: Report, item: dict[str, Any], state_name: str) -> None:
    kinds = list(dict.fromkeys(t.partition(":")[0] for t in item.get("parsed_version_json") or []))
    column = state_name if state_name in VERSION_COLUMNS else "other"
    for kind in kinds or ["original"]:
        counts = report.versions.setdefault(kind, Counter())
        counts["all"] += 1
        counts[column] += 1


def _count_quality(report: Report, item: dict[str, Any]) -> None:
    ext = (item.get("ext") or "").lower()
    report.formats[ext.lstrip(".").upper() or "?"] += 1
    kbps = item.get("bitrate_kbps")
    if ext in LOSSLESS:
        report.bitrates["lossless (WAV, FLAC)"] += 1
    elif not kbps:
        report.bitrates["unknown"] += 1
    else:
        band = next(name for low, high, name in BITRATE_BANDS if low <= kbps < high)
        report.bitrates[band] += 1
    flags = item.get("flags_json") or []
    report.suspect_upscale += "suspect_upscale" in flags
    report.unreadable += "unreadable" in flags


def _estimate_downloads(report: Report, throttle: dict[str, int]) -> None:
    """Replacing every matched_auto item at the queue's pace (step 09a): the daily cap
    sets the number of days; pauses and downloads set the hours of work."""
    n = report.states.get("matched_auto", 0)
    quiet = min(n, throttle["quiet_start_downloads"])
    pauses = quiet * (throttle["quiet_start_min_s"] + throttle["quiet_start_max_s"]) / 2
    pauses += (n - quiet) * (throttle["pause_min_s"] + throttle["pause_max_s"]) / 2
    report.downloads = n
    report.throttle = throttle
    report.download_hours = (pauses + n * DOWNLOAD_S) / 3600
    cap = throttle["daily_cap"]
    report.download_days = math.ceil(n / cap) if n and cap > 0 else 0


def recommend(report: Report) -> list[str]:
    """Suggestions from fixed thresholds; the owner decides."""
    auto = report.share(report.states.get("matched_auto", 0))
    missing = report.share(report.states.get("not_found", 0))
    found: list[str] = []
    if missing >= NOT_FOUND_ADOPT:
        found.append(
            f"{missing:.0%} of items have no official match. Adopting only-copy tracks "
            "matters as much as replacing. Prioritise `plan adopt`."
        )
    if auto >= AUTO_REPLACE:
        found.append(
            f"{auto:.0%} of items matched automatically. Replace-first will clean most of "
            "the library quickly."
        )
    if not found:
        review = report.share(report.states.get("review", 0))
        found.append(
            f"Neither threshold is met: {auto:.0%} matched automatically (the replace-first "
            f"mark is {AUTO_REPLACE:.0%}) and {missing:.0%} have no official match (the "
            f"adopt-first mark is {NOT_FOUND_ADOPT:.0%}). {review:.0%} need a decision, so "
            "the review spreadsheet (`musicorg review export`) decides how much gets "
            "replaced."
        )
    return found


def _describe(payload: dict[str, Any]) -> str:
    return f"{payload.get('title', '')} — {', '.join(payload.get('artists') or [])}"


# ---- writing ---------------------------------------------------------------------------


def render_markdown(report: Report) -> str:
    lines = [
        "# Music Organizer: decision report",
        "",
        f"Library: `{report.root}`  ",
        f"{report.generated:%Y-%m-%d %H:%M} · {report.total:,} items from "
        f"{report.sources} source{'s' if report.sources != 1 else ''}",
        "",
        "## Where the library stands",
        "",
        "| State | Meaning | Items | Share |",
        "|---|---|---:|---:|",
    ]
    for name in ITEM_STATES:
        n = report.states.get(name, 0)
        lines.append(f"| `{name}` | {STATE_MEANING[name]} | {n:,} | {report.share(n):.1%} |")
    lines += [f"| **Total** | | **{report.total:,}** | **100%** |", "", "## Recommendation", ""]
    lines += [" ".join(report.recommendations), "",
              "These come from fixed thresholds; the decision is yours.", ""]  # fmt: skip

    in_review = report.states.get("review", 0)
    lines += ["## Why items need review", ""]
    if in_review:
        lines += [f"{in_review:,} items. An item can have several reasons.", "",
                  "| Reason | Meaning | Items | Share of review |",
                  "|---|---|---:|---:|"]  # fmt: skip
        for code in REVIEW_REASONS:
            n = report.review_reasons.get(code, 0)
            if n:
                lines.append(f"| `{code}` | {REASON_MEANING[code]} | {n:,} | {n / in_review:.1%} |")
        only = sum(report.length_only.values())
        if only:
            bands = [name for _, name in LENGTH_BANDS] + ["unknown"]
            spread = " · ".join(f"{b}: {report.length_only[b]:,}" for b in bands
                                if report.length_only.get(b))  # fmt: skip
            lines += [
                "",
                f"**Only the length differs** for {only:,} of them ({only / in_review:.0%}): the "
                "same artist, exactly the same title and version, and official audio. By how "
                f"much: {spread}. A few seconds is usually silence or a fade at the ends; much "
                "more is usually another edit (radio, extended, a video's intro). Comparing the "
                "audio itself (step 08) can tell.",
            ]
    else:
        lines.append("None.")
    lines.append("")

    missing = report.states.get("not_found", 0)
    lines += ["## Why items weren't found", ""]
    if missing:
        lines += [
            f"- No results at all: {report.no_results:,}",
            f"- Results, but none close enough (best score under 0.60): {report.low_score:,}",
            "",
            f"Closest misses (up to {EXAMPLES}):",
            "",
            "| # | Artist | Title | Best result | Score |",
            "|---:|---|---|---|---:|",
        ]
        for n, miss in enumerate(report.misses, start=1):
            score = f"{miss['score']:.2f}" if miss["score"] is not None else "—"
            lines.append(f"| {n} | {_cell(miss['artist'])} | {_cell(miss['title'])} | "
                         f"{_cell(miss['best']) or 'no results'} | {score} |")  # fmt: skip
    else:
        lines.append("None.")
    lines.append("")

    lines += ["## Versions in the library", "",
              "From the files' names and tags. Soft versions (clean, explicit, remaster) "
              "don't change the recording.", "",
              "| Version | All | " + " | ".join(f"`{c}`" for c in VERSION_COLUMNS) + " | other |",
              "|---|" + "---:|" * (len(VERSION_COLUMNS) + 2)]  # fmt: skip
    ordered = sorted(report.versions.items(), key=lambda kv: (kv[0] != "original", -kv[1]["all"]))
    for kind, counts in ordered:
        label = "none (the original)" if kind == "original" else render_versions([kind])
        if kind in SOFT_VERSION_KINDS:
            label += " (soft)"
        cells = [counts.get(c, 0) for c in ("all", *VERSION_COLUMNS, "other")]
        lines.append(f"| {label} | " + " | ".join(f"{n:,}" for n in cells) + " |")
    lines.append("")

    lines += ["## Quality of the existing files", "", "| Bitrate | Files | Share |",
              "|---|---:|---:|"]  # fmt: skip
    bands = [name for _, _, name in BITRATE_BANDS] + ["lossless (WAV, FLAC)", "unknown"]
    for band in bands:
        n = report.bitrates.get(band, 0)
        if n:
            lines.append(f"| {band} | {n:,} | {report.share(n):.1%} |")
    formats = " · ".join(f"{name} {n:,}" for name, n in report.formats.most_common())
    lines += [
        "",
        f"Formats: {formats}.",
        "",
        f"`suspect_upscale`: {report.suspect_upscale:,} "
        f"({report.share(report.suspect_upscale):.1%}): MP3s of 256 kbps or more with signs "
        "of coming from YouTube, whose audio is about 128 kbps. A heuristic, not a "
        "measurement.",
    ]
    if report.unreadable:
        lines.append(f"Files whose audio couldn't be read: {report.unreadable:,}.")
    lines += [
        "",
        "YouTube Music's official audio is about 128 kbps AAC. A replacement is cleaner and "
        "correctly tagged, not a higher bitrate.",
        "",
        "## What replacing would take",
        "",
    ]
    if report.downloads:
        days = f"{report.download_days} day{'s' if report.download_days != 1 else ''}"
        pace = report.throttle
        lines.append(
            f"Replacing all {report.downloads:,} `matched_auto` items means {report.downloads:,} "
            f"downloads: **about {days}** at the queue's pace (one at a time, "
            f"{pace['pause_min_s']}–{pace['pause_max_s']} s pauses, at most "
            f"{pace['daily_cap']} a day), with about {report.download_hours:.1f} hours of "
            f"downloading in all (assuming {DOWNLOAD_S} s per download)."
        )
    else:
        lines.append("Nothing to download yet: no items matched automatically.")
    return "\n".join(lines) + "\n"


def render_csv(lib: Library, index: Index) -> bytes:
    """Every item: its state, reasons, file details, parse and best candidate."""
    folders = scan.source_folders(lib, index)
    decisions = state.decisions(lib.load_state().data)
    candidates = index.all_candidates()
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["item_id", "source_path", "state", "reasons", "flags", "format",
                     "bitrate_kbps", "duration", "parsed_artist", "parsed_title",
                     "parsed_version", "parse_confidence", "match_title", "match_artists",
                     "match_score", "match_url"])  # fmt: skip
    for item in index.items():
        chosen = decisions.get(item["id"], {}).get("video_id")
        options = candidates.get(item["id"], [])
        first = options[0] if options else None
        best = next((c for c in options if c["video_id"] == chosen), first)
        payload = best["payload"] if best else {}
        writer.writerow([
            item["id"], scan.item_path(folders, item), report_state(item),
            "; ".join(item.get("reasons_json") or []), "; ".join(item.get("flags_json") or []),
            (item.get("ext") or "").lstrip("."), item.get("bitrate_kbps") or "",
            minutes(item.get("duration_s")), item.get("parsed_artist") or "",
            item.get("parsed_title") or "", "; ".join(item.get("parsed_version_json") or []),
            f"{item.get('parse_confidence') or 0:.2f}", payload.get("title", ""),
            ", ".join(payload.get("artists") or []),
            f"{best['score']:.3f}" if best else "", payload.get("link", ""),
        ])  # fmt: skip
    return out.getvalue().encode("utf-8-sig")


@dataclass
class ReportFiles:
    markdown: Path
    csv: Path
    report: Report


def write(
    lib: Library, index: Index, *, out_dir: Path | None = None, now: datetime | None = None
) -> ReportFiles:
    report = gather(lib, index, now=now)
    folder = out_dir if out_dir is not None else lib.paths.reports
    stamp = f"{report.generated:%Y-%m-%d}"
    sources = scan.source_folders(lib, index).values()
    markdown = fileops.write_export(
        lib, folder / f"report-{stamp}.md", render_markdown(report).encode("utf-8"), sources=sources
    )
    table = fileops.write_export(lib, folder / f"report-{stamp}.csv", render_csv(lib, index),
                                 sources=sources)  # fmt: skip
    return ReportFiles(markdown, table, report)


def minutes(seconds: float | None) -> str:
    """ "3:07" for 187 s; empty when unknown."""
    if seconds is None:
        return ""
    whole = round(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def _cell(text: str) -> str:
    """Text that can't break a markdown table."""
    return text.replace("|", "\\|").replace("\n", " ")
