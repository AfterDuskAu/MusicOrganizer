"""The review spreadsheet (step 07): decisions made in Numbers or Excel before the app.

- `export(...)` (`review export <csv>`): one row per `review` and `not_found` item (and
  `matched_auto` ones if asked), with its top 3 candidates and empty decision columns.
  Written through `fileops.write_export`, so it never overwrites. UTF-8 with a BOM.
- `import_csv(...)` (`review import <csv>`): checks every row first and refuses the whole
  file if any is wrong, listing them all. Then the decisions go to state.json (one
  atomic save) and the index.

Decisions (docs/ENGINE_API.md → CSV decision): `accept` (candidate 1), `cand:<n>`, `url`
(a pasted link, fetched and scored: below 0.6 it stays in review as `url_low_score`),
`only_copy` (with the `*_fix` columns), `skip`, and `reject:<n>` (never proposed again).

`official` / `official:<n>` (2026-10-08) takes candidate n like `accept` / `cand:<n>`, and
adds the owner's word that this official track is to replace the rip's copy even if the
fingerprint gate is unsure of it (`uncertain`): the decision carries `override`. A track
the gate calls `different` is never taken (contract rule 7; `pipeline.replace_job`).

A pasted link that scored low isn't accepted, but it is the owner's own suggestion, so
its track becomes the item's candidate 1 whatever its score (`index.PASTED`), on the
export, the review page and over RPC. `accept` then takes it like any candidate 1: the
owner's decision, with no score check. It stays first until the owner decides the item,
rejects it, or pastes another link; the matcher leaves the item alone meanwhile
(`match.link_waiting`).

A pasted link is scored as the matcher scores a candidate, with the artist names the
owner confirmed. Pasting a link the owner rejected for the rip before takes that
rejection back: theirs is the newer word. And a low-scoring link that is already the
item's decision (the sheet with the link, imported again after the accept) changes
nothing.

Candidates are identified by the videoId in the row's own `candN_url`, never by position
in the index, so a spreadsheet exported before `index rebuild` still imports correctly
afterwards, and importing the same file twice changes nothing the second time.
"""

from __future__ import annotations

import csv
import io
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from musicorg import fileops, match, scan, state, youtube
from musicorg.errors import UserError
from musicorg.index import PASTED, Index
from musicorg.library import Library
from musicorg.normalize import compare_key, render_versions
from musicorg.report import minutes
from musicorg.youtube import Candidate

CANDIDATES = 3
COLUMNS = [
    "item_id", "source_path", "duration", "parsed_artist", "parsed_title", "parsed_version",
    "reasons",
    *(f"cand{n}_{part}" for n in range(1, CANDIDATES + 1)
      for part in ("title", "artists", "version", "duration", "score", "url")),
    "fingerprint", "decision", "url", "artist_fix", "title_fix", "album_fix", "art_url",
]  # fmt: skip
REQUIRED = ["item_id", "source_path", "decision", "url", "artist_fix", "title_fix", "album_fix",
            *(f"cand{n}_url" for n in range(1, CANDIDATES + 1))]  # fmt: skip
_LINK = re.compile(
    r"(?:https?://)?(?:(?:www\.|m\.|music\.)?youtube\.com/watch\?(?:[^#\s]*&)?v="
    r"|youtu\.be/)(?P<id>[A-Za-z0-9_-]{11})(?:[&?#]\S*)?",
    re.IGNORECASE,
)
_DECISION = re.compile(
    r"accept|url|only_copy|skip|official(?:\s*:\s*[1-3])?|(?:cand|reject)\s*:\s*[1-3]",
    re.IGNORECASE,
)


# ---- export ----------------------------------------------------------------------------


@dataclass
class ExportResult:
    path: Path
    rows: int

    def to_dict(self) -> dict[str, Any]:
        return {"path": str(self.path), "rows": self.rows}


def export(lib: Library, index: Index, path: Path, *, include_auto: bool = False) -> ExportResult:
    candidates = index.all_candidates()
    folders = scan.source_folders(lib, index)
    gate = state.gate(lib.load_state().data)

    def best_score(item: dict[str, Any]) -> float:
        found = candidates.get(item["id"])
        return found[0]["score"] if found else 0.0

    # Review first, most likely matches first; then not found; then AUTO if asked.
    rows = sorted(index.items_in_states(["review"]), key=lambda i: -best_score(i))
    rows += index.items_in_states(["not_found"])
    if include_auto:
        rows += index.items_in_states(["matched_auto"])

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    for item in rows:
        cells = [
            item["id"], scan.item_path(folders, item), minutes(item.get("duration_s")),
            item.get("parsed_artist") or "", item.get("parsed_title") or "",
            "; ".join(item.get("parsed_version_json") or []),
            "; ".join(item.get("reasons_json") or []),
        ]  # fmt: skip
        found = candidates.get(item["id"], [])[:CANDIDATES]
        for n in range(CANDIDATES):
            if n < len(found):
                payload = found[n]["payload"]
                cells += [
                    payload.get("title", ""), ", ".join(payload.get("artists") or []),
                    render_versions(payload.get("version_tokens") or []),
                    minutes(payload.get("duration_s")), f"{found[n]['score']:.3f}",
                    Candidate.from_dict(payload).link,
                ]  # fmt: skip
            else:
                cells += [""] * 6
        cells.append(_fingerprint_cell(gate.get(item["id"], {}), found))
        cells += [""] * 6  # decision, url, *_fix, art_url
        writer.writerow(cells)
    data = out.getvalue().encode("utf-8-sig")
    written = fileops.write_export(lib, path, data, sources=folders.values())
    return ExportResult(written, len(rows))


def _fingerprint_cell(results: dict[str, dict[str, Any]], found: list[dict[str, Any]]) -> str:
    """The fingerprint gate's verdict on candidate 1 (step 09b), e.g. "uncertain (BER
    0.21)"; empty if it was never downloaded."""
    if not found:
        return ""
    result = results.get(found[0]["video_id"])
    if not result or not result.get("verdict"):
        return ""
    ber = result.get("ber")
    return f"{result['verdict']} (BER {ber:.2f})" if isinstance(ber, float) else result["verdict"]


# ---- import ----------------------------------------------------------------------------


@dataclass
class Planned:
    """One row's decision, checked but not yet applied."""

    row: int
    item: dict[str, Any]
    kind: str  # accept, candidate, url, only_copy, skip or reject
    number: int | None = None  # the candidate, for accept/cand/reject
    override: bool = False  # `official`: the owner's word over an unsure fingerprint gate
    video_id: str | None = None
    link: str | None = None  # a pasted url
    row_candidate: dict[str, str] = field(default_factory=dict)  # the row's candN columns
    fixes: dict[str, str] = field(default_factory=dict)


@dataclass
class ImportResult:
    applied: dict[str, int] = field(default_factory=dict)  # by decision kind
    unchanged: int = 0  # already decided that way
    kept_in_review: int = 0  # pasted links that scored low or aren't available
    blank: int = 0  # rows without a decision
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class ReviewImportError(UserError):
    """A review spreadsheet was refused; nothing was imported. `problems` lists each
    row's problem."""

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        super().__init__(message)
        self.problems = problems or []


def read_rows(path: Path) -> list[dict[str, str]]:
    """The spreadsheet's rows as dicts, read strictly as UTF-8."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise UserError(f"Couldn't read {path}: {exc.strerror or exc}.") from exc
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ReviewImportError(
            f"{Path(path).name} isn't saved as UTF-8 text, so names with accents or other "
            "scripts would come out wrong. Save it as 'CSV UTF-8' in Numbers or Excel and "
            "import again."
        ) from None
    reader = csv.reader(io.StringIO(text, newline=""))
    header = [h.strip() for h in next(reader, [])]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise ReviewImportError(
            f"{Path(path).name} doesn't have the review columns ({', '.join(missing)} "
            "missing). Import a file made by `musicorg review export`, saved as CSV."
        )
    return [dict(zip(header, [c.strip() for c in row], strict=False)) for row in reader]


def import_csv(lib: Library, index: Index, path: Path) -> ImportResult:
    rows = read_rows(path)
    result = ImportResult()
    folders = scan.source_folders(lib, index)
    planned: list[Planned] = []
    problems: list[str] = []
    seen: dict[str, int] = {}
    for number, row in enumerate(rows, start=2):  # row 1 is the header
        if not any(row.values()):
            continue
        plan, problem = _check_row(number, row, index, folders, seen)
        if problem:
            problems.append(f"row {number}: {problem}")
        elif plan is None:
            result.blank += 1
        else:
            planned.append(plan)
    if problems:
        raise ReviewImportError(
            f"Nothing was imported from {Path(path).name}. Fix these rows and import again:\n"
            + "\n".join(f"  {p}" for p in problems),
            problems,
        )

    # Pasted links are fetched before anything is saved, so a YouTube pause leaves the
    # library exactly as it was.
    tracks = {p.video_id: youtube.get_track(p.video_id) for p in planned
              if p.kind == "url" and p.video_id}  # fmt: skip
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    outcomes: list[tuple[Planned, _Outcome]] = []
    with state.edit(lib.paths.state_file) as st:
        decisions = st.data.setdefault("decisions", {})
        rejected = st.data.setdefault("rejected", {})
        aliases = state.aliases(st.data)
        for plan in planned:
            outcome = _decide(plan, decisions, rejected, tracks.get(plan.video_id), now, aliases)
            outcomes.append((plan, outcome))
            if outcome.warning:
                result.warnings.append(f"row {plan.row}: {outcome.warning}")
                result.kept_in_review += 1
            elif outcome.changed:
                result.applied[plan.kind] = result.applied.get(plan.kind, 0) + 1
            else:
                result.unchanged += 1
        turned_down = state.rejected(st.data)
    # state.json is saved; now the index, which can always be rebuilt from it.
    for plan, outcome in outcomes:
        _update_index(index, plan, outcome, turned_down.get(plan.item["id"], set()))
    return result


# ---- one decision at a time (the review page) ------------------------------------------

PAGE_DECISIONS = ("use", "reject", "url", "only_copy", "skip")


@dataclass
class OneResult:
    changed: bool
    warning: str | None = None
    # The chosen track's artist isn't the rip's under any spelling, nor featured: ask
    # whether they're the same artist. {"from", "to", "others"}.
    alias_offer: dict[str, Any] | None = None


def decide_one(
    lib: Library,
    index: Index,
    item_id: str,
    decision: str,
    *,
    video_id: str | None = None,
    link: str | None = None,
    fixes: dict[str, str] | None = None,
) -> OneResult:
    """One decision from the review page, with the same checks and effects as a row of
    `review import`. `decision` is one of PAGE_DECISIONS: "use" or "reject" a candidate
    (by `video_id`), "url" (`link`), "only_copy" (optional `fixes`) or "skip"."""
    item = index.item(item_id)
    if item is None:
        raise UserError(f"There's no item {item_id!r} in the index.")
    if item["state"] in ("superseded", "adopted"):
        raise UserError(f"This rip was already {item['state']}; there's nothing left to decide.")
    if decision not in PAGE_DECISIONS:
        raise UserError(f"{decision!r} isn't a decision.")
    plan = Planned(row=0, item=item, kind=decision)
    if decision in ("use", "reject"):
        options = index.candidates(item_id)
        number = next((n for n, c in enumerate(options, start=1) if c["video_id"] == video_id), 0)
        if not number:
            raise UserError(f"{video_id!r} isn't one of this item's candidates.")
        payload = options[number - 1]["payload"]
        plan.number, plan.video_id = number, video_id
        if decision == "use":
            plan.kind = "accept" if number == 1 else "candidate"
        plan.row_candidate = {
            "title": payload.get("title", ""),
            "artists": ", ".join(payload.get("artists") or []),
            "duration": minutes(payload.get("duration_s")),
            "score": str(options[number - 1]["score"]),
        }
    elif decision == "url":
        found = _LINK.fullmatch((link or "").strip())
        if not found:
            raise UserError(
                "That isn't a YouTube link (music.youtube.com/watch?v=…, youtube.com/watch?v=… "
                "or youtu.be/…)."
            )
        plan.video_id, plan.link = found.group("id"), (link or "").strip()
    elif decision == "only_copy":
        plan.fixes = {k: v.strip() for k, v in (fixes or {}).items()
                      if k in ("artist_fix", "title_fix", "album_fix") and v.strip()}  # fmt: skip
        art_url = ((fixes or {}).get("art_url") or "").strip()
        if art_url:
            if not art_url.lower().startswith("https://"):
                raise UserError(f"{art_url!r} isn't an https:// link to a picture or a page "
                                "with one.")  # fmt: skip
            plan.fixes["art_url"] = art_url

    track = youtube.get_track(plan.video_id) if plan.kind == "url" and plan.video_id else None
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with state.edit(lib.paths.state_file) as st:
        decisions = st.data.setdefault("decisions", {})
        rejected = st.data.setdefault("rejected", {})
        outcome = _decide(plan, decisions, rejected, track, now, state.aliases(st.data))
        turned_down = state.rejected(st.data).get(item_id, set())
    _update_index(index, plan, outcome, turned_down)
    result = OneResult(outcome.changed, outcome.warning)
    if plan.kind in ("accept", "candidate", "url") and not outcome.warning:
        result.alias_offer = _alias_offer(lib, index, item, plan, track)
    return result


def _alias_offer(
    lib: Library, index: Index, item: dict[str, Any], plan: Planned, track: Candidate | None
) -> dict[str, Any] | None:
    if track is None:
        chosen = next((c for c in index.candidates(item["id"]) if c["video_id"] == plan.video_id),
                      None)  # fmt: skip
        track = Candidate.from_dict(chosen["payload"]) if chosen else None
    rip = match.rip_of(item, state.aliases(lib.load_state().data))
    name = match.alias_offer(rip, track) if track is not None else None
    if name is None or rip.aliases:
        return None
    artist = rip.parsed.artist or rip.parsed.credit or ""
    others = [i for i in _by_artist(index, artist) if i["id"] != item["id"]]
    return {"from": artist, "to": name, "others": len(others)}


def confirm_alias(lib: Library, index: Index, name_in_rips: str, name: str) -> match.RecheckResult:
    """The owner confirmed that `name_in_rips` (as the rips spell it) is `name`: remember
    it in state.json, and classify that artist's undecided items again from the
    candidates already found."""
    key = compare_key(name_in_rips)
    if not key or not name.strip():
        raise UserError("Both names are needed.")
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with state.edit(lib.paths.state_file) as st:
        aliases = st.data.setdefault("aliases", {})
        aliases[key] = {"name": name.strip(), "from": name_in_rips.strip(), "decided_at": now}
    ids = [i["id"] for i in _by_artist(index, name_in_rips)]
    return match.recheck(lib, index, item_ids=ids)


def _by_artist(index: Index, artist: str) -> list[dict[str, Any]]:
    """Undecided items whose artist, as the rips name it, is `artist`."""
    key = compare_key(artist)
    found = []
    for item in index.items_in_states(["review", "not_found"]):
        parsed = match.rip_of(item).parsed
        if key and key in (compare_key(parsed.artist), compare_key(parsed.credit)):
            found.append(item)
    return found


def _check_row(
    number: int,
    row: dict[str, str],
    index: Index,
    folders: dict[str, Path],
    seen: dict[str, int],
) -> tuple[Planned | None, str | None]:
    """The row's decision, or what's wrong with the row. (None, None): no decision."""
    item_id = row.get("item_id", "")
    item = index.item(item_id) if item_id else None
    if item is None:
        return None, f"unknown item_id {item_id!r}"
    if item_id in seen:
        return None, f"{item_id} is also on row {seen[item_id]}; keep one row per item"
    seen[item_id] = number
    expected = scan.item_path(folders, item)
    if not _same_path(row.get("source_path", ""), expected):
        return None, (f"source_path doesn't match this item (expected {expected}). "
                      "Rows may have been moved or edited; export again.")  # fmt: skip
    link = row.get("url", "")
    found = _LINK.fullmatch(link) if link else None
    if link and not found:
        return None, (f"{link!r} isn't a YouTube link (music.youtube.com/watch?v=…, "
                      "youtube.com/watch?v=… or youtu.be/…)")  # fmt: skip
    text = row.get("decision", "")
    if not text:
        return None, None
    if not _DECISION.fullmatch(text):
        return None, (f"{text!r} isn't a decision; use accept, cand:2, cand:3, url, "
                      "only_copy, skip, reject:1–3 or official")  # fmt: skip
    if item["state"] in ("superseded", "adopted"):
        return None, f"this rip was already {item['state']}; there's nothing left to decide"
    word, _, digit = text.lower().replace(" ", "").partition(":")
    plan = Planned(row=number, item=item, kind=word)
    if word == "official":
        word, plan.override = "cand", True
    if word in ("accept", "cand", "reject"):
        plan.number = int(digit) if digit else 1
        plan.kind = "reject" if word == "reject" else "accept" if plan.number == 1 else "candidate"
        cell = row.get(f"cand{plan.number}_url", "")
        chosen = _LINK.fullmatch(cell) if cell else None
        if chosen is None:
            return None, f"{text} but the row has no candidate {plan.number}"
        plan.video_id = chosen.group("id")
        plan.row_candidate = {k: row.get(f"cand{plan.number}_{k}", "")
                              for k in ("title", "artists", "duration", "score")}  # fmt: skip
    elif word == "url":
        if found is None:
            return None, "the decision is url, but the url column is empty"
        plan.video_id, plan.link = found.group("id"), link
    elif word == "only_copy":
        plan.fixes = {k: row[k] for k in ("artist_fix", "title_fix", "album_fix") if row.get(k)}
    art_url = (row.get("art_url") or "").strip()
    if art_url:
        if not art_url.lower().startswith("https://"):
            return None, (f"art_url {art_url!r} isn't an https:// link to a picture or a page "
                          "with one (SoundCloud, Bandcamp, YouTube…)")  # fmt: skip
        if plan.kind != "only_copy":
            return None, "art_url is for only_copy rows; matched songs get their album's cover"
        plan.fixes["art_url"] = art_url
    return plan, None


@dataclass
class _Outcome:
    changed: bool  # state.json changed
    state: str | None = None  # the item's new state (None: a rejection)
    reasons: list[str] = field(default_factory=list)
    chosen: dict[str, Any] | None = None  # the candidate to put first
    warning: str | None = None  # kept in review, and why
    pasted: bool = False  # `chosen` is a pasted link that scored low: first until decided


def _decide(
    plan: Planned,
    decisions: dict[str, Any],
    rejected: dict[str, Any],
    track: Candidate | None,
    now: str,
    aliases: dict[str, str],
) -> _Outcome:
    """Record one decision in state.json's data, and say what the index should become.
    `aliases`: the artist names the owner confirmed (`state.aliases`), so a pasted link
    is scored as the matcher would score it."""
    item_id = plan.item["id"]
    if plan.kind == "reject":
        ids = rejected.setdefault(item_id, [])
        changed = plan.video_id not in ids
        if changed:
            ids.append(plan.video_id)
        if decisions.get(item_id, {}).get("video_id") == plan.video_id:
            del decisions[item_id]  # it was the one chosen before; that choice goes too
            changed = True
        return _Outcome(changed)

    entry: dict[str, Any] = {"decision": plan.kind}
    chosen: dict[str, Any] | None = None
    took_back = False  # a pasted link the owner had rejected before: that rejection goes
    if plan.kind in ("accept", "candidate"):
        chosen = _row_candidate(plan)
        entry.update(video_id=plan.video_id, candidate_id=chosen["id"],
                     title=chosen["payload"]["title"])  # fmt: skip
        if plan.override:
            entry["override"] = True
    elif plan.kind == "url":
        if track is None:
            return _Outcome(False, "review", ["video_unavailable"],
                            warning=f"{plan.link} isn't on YouTube Music; the item stays in "
                            "review.")  # fmt: skip
        took_back = _take_back_rejection(rejected, item_id, plan.video_id)
        scored = match.assess(match.rip_of(plan.item, aliases), track)
        chosen = _candidate_row(item_id, scored)
        if scored.score < match.REVIEW_SCORE:
            decided = decisions.get(item_id, {})
            if (decided.get("video_id") == plan.video_id
                    and decided.get("decision") in scan.DECISION_STATES):  # fmt: skip
                # The owner already took this very track for the rip (the sheet with its
                # link, imported again after the accept): that decision stands.
                return _Outcome(took_back, scan.DECISION_STATES[decided["decision"]], [], chosen)
            return _Outcome(
                took_back, "review", ["url_low_score"], chosen,
                warning=f"{track.title} — {', '.join(track.artists)} scores {scored.score:.2f} "
                "against this rip (below 0.60), so it stays in review; it's candidate 1 on "
                "the next export.",
                pasted=True,
            )  # fmt: skip
        entry.update(url=plan.link, video_id=plan.video_id, candidate_id=chosen["id"],
                     title=track.title, score=round(scored.score, 3))  # fmt: skip
    elif plan.kind == "only_copy":
        entry.update(plan.fixes)

    new_state = scan.DECISION_STATES[plan.kind]
    before = {k: v for k, v in decisions.get(item_id, {}).items() if k != "decided_at"}
    if before == entry:
        return _Outcome(took_back, new_state, [], chosen)
    decisions[item_id] = {**entry, "decided_at": now}
    return _Outcome(True, new_state, [], chosen)


def _take_back_rejection(rejected: dict[str, Any], item_id: str, video_id: str | None) -> bool:
    """The owner pasted this track's link for the rip themselves, so if they rejected it
    for the rip before, that rejection no longer stands: it comes off state.json's
    `rejected` list, and the track is an ordinary candidate again. True if there was one."""
    ids = rejected.get(item_id)
    if not isinstance(ids, list) or video_id not in ids:
        return False
    ids.remove(video_id)
    if not ids:
        del rejected[item_id]
    return True


def _update_index(index: Index, plan: Planned, outcome: _Outcome, turned_down: set[str]) -> None:
    item_id = plan.item["id"]
    current = [c for c in index.candidates(item_id) if c["video_id"] not in turned_down]
    if outcome.state is None:
        # A rejection: an undecided item is classified again from what's left, without
        # searching; a decided one keeps its state. So does one whose pasted link is still
        # waiting for the owner (another candidate was the one rejected): it stays first.
        waiting = any(c["payload"].get(PASTED) for c in current)
        if waiting or plan.item["state"] in set(scan.DECISION_STATES.values()):
            index.set_match(item_id, plan.item["state"], plan.item.get("reasons_json") or [],
                            current)  # fmt: skip
            return
        left = [Candidate.from_dict(c["payload"]) for c in current]
        classified = match.classify(match.rip_of(plan.item), left, rejected=turned_down)
        rows = [_candidate_row(item_id, s) for s in classified.top]
        index.set_match(item_id, classified.state, classified.reasons, rows)
        return
    chosen = outcome.chosen
    if outcome.state != "review" or outcome.pasted:
        # The owner decided the item, or pasted another link: an earlier pasted link's
        # turn at the front is over. (A link that isn't on YouTube Music changes nothing.)
        current = [_marked(c, False) for c in current]
    if chosen is not None:
        # The index's own copy has the album and versions; the row's copy is the fallback.
        chosen = next((c for c in current if c["video_id"] == chosen["video_id"]), chosen)
        # A pasted link that scored low is marked: candidate 1, whatever its score.
        chosen = _marked(chosen, outcome.pasted)
        current = [chosen, *(c for c in current if c["video_id"] != chosen["video_id"])]
    index.set_match(item_id, outcome.state, outcome.reasons, current)


def _marked(row: dict[str, Any], pasted: bool) -> dict[str, Any]:
    """A stored candidate with the pasted-link mark (`index.PASTED`) put on or taken off."""
    payload = {k: v for k, v in row["payload"].items() if k != PASTED}
    if pasted:
        payload[PASTED] = True
    return {**row, "payload": payload}


def _row_candidate(plan: Planned) -> dict[str, Any]:
    """The chosen candidate as the index stores it, from the row's own columns (the
    index may no longer hold it, e.g. after a rebuild)."""
    cells = plan.row_candidate
    assert plan.video_id is not None
    try:
        score = float(cells.get("score") or 0)
    except ValueError:
        score = 0.0
    found = Candidate(
        video_id=plan.video_id,
        title=cells.get("title", ""),
        artists=tuple(a.strip() for a in cells.get("artists", "").split(",") if a.strip()),
        duration_s=_seconds(cells.get("duration", "")),
    )
    candidate_id = match.candidate_id(plan.item["id"], plan.video_id)
    reasons = ["chosen in review"]
    payload = {**found.to_dict(), "candidate_id": candidate_id, "score": score,
               "version_tokens": [], "reasons": reasons, "link": found.link}  # fmt: skip
    return {"id": candidate_id, "video_id": plan.video_id, "score": score,
            "reasons": reasons, "payload": payload}  # fmt: skip


def _candidate_row(item_id: str, scored: match.Scored) -> dict[str, Any]:
    return {"id": scored.candidate_id(item_id), "video_id": scored.candidate.video_id,
            "score": scored.score, "reasons": list(scored.reasons),
            "payload": scored.payload(item_id)}  # fmt: skip


def _seconds(text: str) -> int | None:
    """ "3:07" → 187."""
    parts = text.split(":")
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def _same_path(a: str, b: str) -> bool:
    """The same path as text: NFC, and ignoring case where the system does (Windows)."""

    def norm(text: str) -> str:
        return os.path.normcase(unicodedata.normalize("NFC", text.strip()))

    return norm(a) == norm(b)
