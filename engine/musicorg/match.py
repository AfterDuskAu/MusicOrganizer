"""Matching scanned rips to official YouTube Music tracks (step 06). Nothing is ever
downloaded here.

For each rip, up to 3 searches (`queries`), then every candidate is scored (`score`)
and the item classified (`classify`):

- `matched_auto`: an official audio track with the same main artist, exactly the same
  title and version, the clean/explicit rule satisfied, and a length within 2 s. That
  means safe to *queue*; replacing a file still needs the fingerprint gate (step 08).

Clean or explicit: the owner always wants the explicit version, unless the rip's own
name says clean (or "edited", "censored"). A rip that says clean only matches a clean
track; one that says explicit, or neither, never matches a track marked clean, and when
both versions are listed it takes the explicit one (config `prefer_explicit`, default
true).
- `review`: the best candidate scores at least 0.60 but isn't AUTO. The top 3 are kept.
- `not_found`: nothing scores 0.60.

Wrong version is worse than no match: every AUTO condition is an exact check, and a
doubt of any kind sends the item to review.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import random
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from musicorg import fileops, state, youtube
from musicorg.config import Config
from musicorg.index import Index
from musicorg.library import Library
from musicorg.normalize import (
    LOW_CONFIDENCE,
    SOFT_VERSION_KINDS,
    Parsed,
    compare_key,
    parse_title,
    render_versions,
)
from musicorg.youtube import Candidate

log = logging.getLogger(__name__)

REVIEW_SCORE = 0.60
MAX_QUERIES = 3
TOP_CANDIDATES = 3
ARTIST_WEIGHT, TITLE_WEIGHT, VERSION_WEIGHT, DURATION_WEIGHT = 0.35, 0.35, 0.20, 0.10
DURATION_EXACT_S = 2  # full marks, and the AUTO limit
DURATION_ZERO_S = 15  # no marks
SAMPLE_SIZE = 20
SAMPLE_NAME = "auto-sample.csv"

Progress = Callable[[int, int, float | None], None]  # done, total, seconds left (estimate)


# ---- queries ---------------------------------------------------------------------------


def queries(parsed: Parsed) -> list[str]:
    """The searches for a rip, best first, at most 3: "<artist> <title>", then with
    its versions ("… Adventure Club Remix"), then for a low-confidence parse the title
    alone and the reversed order."""
    if not parsed.title:
        return []
    artist = parsed.credit or parsed.artist
    base = " ".join(x for x in (artist, parsed.title) if x)
    found = [base]
    hard = _hard(parsed.version_tokens)
    if hard:
        found.append(f"{base} {render_versions(hard)}")
    if parsed.confidence < LOW_CONFIDENCE:
        found.append(parsed.title)
        if artist:
            found.append(f"{parsed.title} {artist}")
    unique: dict[str, str] = {}
    for query in found:
        unique.setdefault(youtube.query_key(query), query)
    return list(unique.values())[:MAX_QUERIES]


# ---- scoring ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rip:
    """What matching knows about a rip."""

    parsed: Parsed
    duration_s: float | None
    explicit_tag: bool | None = None  # the file's own advisory tag

    @property
    def says(self) -> str | None:
        """ "explicit" or "clean" if the rip says which it is."""
        kinds = {t.partition(":")[0] for t in self.parsed.version_tokens}
        if "explicit" in kinds or self.explicit_tag is True:
            return "explicit"
        return "clean" if "clean" in kinds else None


@dataclass(frozen=True)
class Scored:
    """A candidate scored against a rip."""

    candidate: Candidate
    version_tokens: tuple[str, ...]
    score: float
    reasons: tuple[str, ...]  # short human strings, then the review-reason codes
    codes: tuple[str, ...]  # the review-reason codes alone (docs/ENGINE_API.md → Enums)
    same_song: bool  # official audio, same artist, title and version
    auto: bool  # and the length and the clean/explicit rule agree too

    def candidate_id(self, item_id: str) -> str:
        key = f"{item_id}/{self.candidate.video_id}".encode()
        return "c_" + hashlib.sha1(key, usedforsecurity=False).hexdigest()[:12]

    def payload(self, item_id: str) -> dict[str, Any]:
        """The Candidate shape in docs/ENGINE_API.md, plus a link."""
        data = self.candidate.to_dict()
        data.update(
            candidate_id=self.candidate_id(item_id),
            version_tokens=list(self.version_tokens),
            score=round(self.score, 3),
            reasons=list(self.reasons),
            link=self.candidate.link,
        )
        return data


def score(
    parsed: Parsed, file_duration: float | None, candidate: Candidate
) -> tuple[float, list[str]]:
    """How well `candidate` fits a rip, 0–1, and why."""
    scored = assess(Rip(parsed, file_duration), candidate)
    return scored.score, list(scored.reasons)


def assess(rip: Rip, candidate: Candidate, *, prefer_explicit: bool = True) -> Scored:
    parsed = rip.parsed
    theirs = parse_title(candidate.title)
    human: list[str] = []
    codes: list[str] = []

    # Artist (0.35): the best pairing of everyone credited on each side, featured artists
    # included (a mislabelled "Eminem & Rihanna - Run This Town" still finds JAY-Z's track
    # "feat. Rihanna"). AUTO needs the rip's main artist (or whole credit) itself.
    names = list(candidate.artists)
    if len(names) > 1:
        names.append(" & ".join(names))
    everyone = [*names, *theirs.artists]
    rip_title = parsed.title or ""
    main = [n for n in dict.fromkeys([parsed.credit, parsed.artist]) if n]
    mine = [n for n in dict.fromkeys([*main, *parsed.artists]) if n]
    if main:
        artist_sim = max((_ratio(a, b) for a in mine for b in everyone), default=0.0)
        artist_equal = any(_tight(a) == _tight(b) for a in main for b in names)
    else:
        # No artist in the rip's name: it may start the title ("Gigi D'Agostino Bla Bla Bla").
        artist_sim, rip_title = _artist_in_title(rip_title, candidate.artists)
        artist_equal = False
    human.append("artist exact" if artist_equal else f"artist {artist_sim:.0%}")
    if not artist_equal:
        codes.append("artist_mismatch")

    # Title (0.35), without versions or featured artists on either side.
    title_sim = _ratio(rip_title, theirs.title or "")
    title_equal = bool(compare_key(rip_title)) and compare_key(rip_title) == compare_key(
        theirs.title
    )
    human.append("title exact" if title_equal else f"title {title_sim:.0%}")
    if not title_equal:
        codes.append("title_fuzzy")

    # Version (0.20): the hard version tokens must be the same set.
    version_equal = set(_hard(parsed.version_tokens)) == set(_hard(theirs.version_tokens))
    if version_equal:
        human.append("version match")
    else:
        human.append(
            f"version: rip {_describe(parsed.version_tokens)}, "
            f"this {_describe(theirs.version_tokens)}"
        )
        codes.append("version_mismatch")

    # Duration (0.10).
    delta = (
        abs(rip.duration_s - candidate.duration_s)
        if rip.duration_s is not None and candidate.duration_s is not None
        else None
    )
    if delta is None:
        duration_score = 0.0
        human.append("duration unknown")
        codes.append("duration_mismatch")
    else:
        span = DURATION_ZERO_S - DURATION_EXACT_S
        duration_score = (
            1.0 if delta <= DURATION_EXACT_S else max(0.0, 1 - (delta - DURATION_EXACT_S) / span)
        )
        human.append(f"duration Δ{delta:.0f}s")
        if delta > DURATION_EXACT_S:
            codes.append("duration_mismatch")

    official = candidate.is_official_audio
    if not official:
        human.append("not official audio")
        codes.append("not_official_audio")

    # Clean/explicit: a rip that says which it is only matches the same kind. One that
    # says neither is taken as explicit (the owner's wish), so a track whose title says
    # clean ("(Clean)", "(Edited)") isn't AUTO for it.
    says = rip.says
    agrees = says is None or candidate.is_explicit is (says == "explicit")
    marked_clean = "clean" in {t.partition(":")[0] for t in theirs.version_tokens}
    if not agrees:
        kind = {True: "explicit", False: "clean", None: "not marked"}[candidate.is_explicit]
        human.append(f"rip is {says}, this is {kind}")
    elif marked_clean and says != "clean" and (says == "explicit" or prefer_explicit):
        agrees = False
        human.append("this is a clean version; the rip doesn't say clean")
    if not agrees and "version_mismatch" not in codes:
        codes.append("version_mismatch")

    total = (
        ARTIST_WEIGHT * artist_sim
        + TITLE_WEIGHT * title_sim
        + VERSION_WEIGHT * (1.0 if version_equal else 0.0)
        + DURATION_WEIGHT * duration_score
    )
    same_song = official and artist_equal and title_equal and version_equal
    auto = same_song and agrees and delta is not None and delta <= DURATION_EXACT_S
    return Scored(
        candidate=candidate,
        version_tokens=theirs.version_tokens,
        score=round(total, 4),
        reasons=(*human, *codes),
        codes=tuple(codes),
        same_song=same_song,
        auto=auto,
    )


def _ratio(a: str, b: str) -> float:
    """token_sort_ratio, 0–1, on compare keys. Not token_set_ratio, which scores
    "Closer" against "Closer (Tribute to …)" as a perfect match."""
    return fuzz.token_sort_ratio(compare_key(a), compare_key(b)) / 100


def _tight(name: str) -> str:
    """Artist names compared without spaces too: tags say "Cold Play", "Audio Slave"."""
    return compare_key(name).replace(" ", "")


def _artist_in_title(title: str, artists: Iterable[str]) -> tuple[float, str]:
    """A candidate artist's name at the start or end of a title-only rip counts as the
    artist, and the rest is compared as the title."""
    words = compare_key(title).split()
    for artist in artists:
        name = compare_key(artist).split()
        if not name or len(name) >= len(words):
            continue
        if words[: len(name)] == name:
            return 1.0, " ".join(words[len(name) :])
        if words[-len(name) :] == name:
            return 1.0, " ".join(words[: -len(name)])
    return 0.0, title


def _hard(tokens: Iterable[str]) -> list[str]:
    return [t for t in tokens if t.partition(":")[0] not in SOFT_VERSION_KINDS]


def _describe(tokens: Iterable[str]) -> str:
    return render_versions(_hard(tokens)) or "original"


# ---- classifying -----------------------------------------------------------------------


@dataclass
class Outcome:
    state: str  # matched_auto, review or not_found
    reasons: list[str] = field(default_factory=list)  # review-reason codes
    top: list[Scored] = field(default_factory=list)  # best first; for AUTO, the match


def classify(
    rip: Rip,
    candidates: Iterable[Candidate],
    *,
    rejected: Iterable[str] = (),
    prefer_explicit: bool = True,
) -> Outcome:
    turned_down = set(rejected)
    unique: dict[str, Candidate] = {}
    for c in candidates:
        if c.video_id not in turned_down:
            unique.setdefault(c.video_id, c)
    scored = sorted(
        (assess(rip, c, prefer_explicit=prefer_explicit) for c in unique.values()),
        key=lambda s: -s.score,
    )
    if not scored:
        return Outcome("not_found")

    pick: Scored | None = None
    same = [s for s in scored if s.same_song]
    kinds = {s.candidate.is_explicit for s in same}
    if rip.says is None and True in kinds and False in kinds:
        # Both an explicit and a clean official version, and the rip says neither.
        preferred = [s for s in same if s.candidate.is_explicit is prefer_explicit]
        note = "explicit/clean pair: chose " + ("explicit" if prefer_explicit else "clean")
        pick = replace(preferred[0], reasons=(*preferred[0].reasons, note))
        if not pick.auto:
            return _review_or_not_found(rip, [pick, *[s for s in scored if s is not preferred[0]]])
    else:
        autos = [s for s in scored if s.auto]
        pick = autos[0] if autos else None
    if pick is not None and pick.auto:
        rest = [s for s in scored if s.candidate.video_id != pick.candidate.video_id]
        return Outcome("matched_auto", [], [pick, *rest][:TOP_CANDIDATES])
    return _review_or_not_found(rip, scored)


def _review_or_not_found(rip: Rip, ranked: list[Scored]) -> Outcome:
    best = ranked[0]
    top = ranked[:TOP_CANDIDATES]
    if best.score < REVIEW_SCORE:
        return Outcome("not_found", [], top)
    reasons = list(best.codes)
    if rip.parsed.confidence < LOW_CONFIDENCE:
        reasons.append("low_parse_confidence")
    return Outcome("review", reasons, top)


# ---- matching items --------------------------------------------------------------------


def rip_of(item: dict[str, Any]) -> Rip:
    """A scanned item (a row from the index) as matching sees it."""
    data = item.get("parsed_json") or {}
    if data:
        parsed = Parsed.from_dict(data)
    else:
        parsed = Parsed(item.get("parsed_artist"), item.get("parsed_title"),
                        tuple(item.get("parsed_version_json") or ()),
                        confidence=item.get("parse_confidence") or 0.0)  # fmt: skip
    tags = (item.get("raw_tags_json") or {}).get("tags") or {}
    explicit = tags.get("explicit")
    return Rip(parsed, item.get("duration_s"), explicit if isinstance(explicit, bool) else None)


def match_item(
    rip: Rip,
    *,
    cache: youtube.SearchCache | None,
    rejected: Iterable[str] = (),
    prefer_explicit: bool = True,
    refresh: bool = False,
) -> Outcome:
    """Search for one rip and classify it, stopping early on an AUTO match."""
    found: list[Candidate] = []
    outcome = Outcome("not_found")
    for query in queries(rip.parsed):
        found += youtube.search_songs(query, cache=cache, refresh=refresh)
        outcome = classify(rip, found, rejected=rejected, prefer_explicit=prefer_explicit)
        if outcome.state == "matched_auto":
            break
    return outcome


@dataclass
class MatchResult:
    items: int = 0
    matched_auto: int = 0
    review: int = 0
    not_found: int = 0
    left: int = 0  # items still waiting (beyond --limit)
    searches: int = 0  # requests that reached YouTube Music
    seconds: float = 0.0
    sample: Path | None = None
    sample_size: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["sample"] = str(self.sample) if self.sample else None
        return data


def run(
    lib: Library,
    index: Index,
    *,
    limit: int | None = None,
    rescan: bool = False,
    progress: Progress | None = None,
    prefer_explicit: bool | None = None,
    rng: random.Random | None = None,
) -> MatchResult:
    """`musicorg match`: match `new` items (with `rescan`, `review` and `not_found`
    ones too, skipping the search cache). Each item's result is saved as soon as it's
    known, so an interrupted run carries on where it stopped."""
    started = time.monotonic()
    states = ("new", "review", "not_found") if rescan else ("new",)
    todo = index.items_in_states(states)
    result = MatchResult()
    if limit is not None:
        result.left = max(0, len(todo) - limit)
        todo = todo[:limit]
    data = lib.load_state().data
    rejected = state.rejected(data)
    prefer = Config.load().prefer_explicit if prefer_explicit is None else prefer_explicit
    limiter = youtube.limiter()
    requests_before = limiter.requests

    for done, item in enumerate(todo, start=1):
        outcome = match_item(
            rip_of(item),
            cache=index,
            rejected=rejected.get(item["id"], ()),
            prefer_explicit=prefer,
            refresh=rescan,
        )
        index.set_match(
            item["id"],
            outcome.state,
            outcome.reasons,
            [
                {
                    "id": s.candidate_id(item["id"]),
                    "video_id": s.candidate.video_id,
                    "score": s.score,
                    "reasons": list(s.reasons),
                    "payload": s.payload(item["id"]),
                }
                for s in outcome.top
            ],
        )
        result.items += 1
        setattr(result, outcome.state, getattr(result, outcome.state) + 1)
        result.searches = limiter.requests - requests_before
        if progress is not None:
            # The time items have taken so far, paced by the rate limiter (cached searches
            # are quicker), for the ones left.
            per_item = (time.monotonic() - started) / done
            progress(done, len(todo), (len(todo) - done) * per_item)

    result.seconds = time.monotonic() - started
    result.sample, result.sample_size = write_sample(lib, index, rng=rng)
    return result


def write_sample(
    lib: Library, index: Index, *, rng: random.Random | None = None
) -> tuple[Path | None, int]:
    """Reports/auto-sample.csv: 20 random AUTO matches with links, for listening to.
    Never overwrites (a ` (2)` suffix). None when there are no AUTO matches yet."""
    autos = index.items_in_states(["matched_auto"])
    if not autos:
        return None, 0
    chosen = (rng or random.Random()).sample(autos, min(SAMPLE_SIZE, len(autos)))
    # state.json is the record of sources; the index mirrors it.
    sources = {**index.sources(), **state.sources(lib.load_state().data)}
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["item_id", "rip", "rip_artist", "rip_title", "rip_version", "rip_duration_s",
                     "match_title", "match_artists", "match_album", "match_version",
                     "match_duration_s", "score", "link"])  # fmt: skip
    for item in chosen:
        best = index.candidates(item["id"])[:1]
        if not best:
            continue
        match = best[0]["payload"]
        rip = rip_of(item)
        folder = sources.get(item["source_id"], {}).get("path")
        writer.writerow([
            item["id"],
            str(Path(folder) / item["rel_path"]) if folder else item["rel_path"],
            rip.parsed.artist or "",
            rip.parsed.title or "",
            "; ".join(rip.parsed.version_tokens),
            f"{item['duration_s']:.0f}" if item.get("duration_s") is not None else "",
            match["title"],
            ", ".join(match["artists"]),
            match.get("album") or "",
            "; ".join(match.get("version_tokens") or []),
            match.get("duration_s") or "",
            f"{match['score']:.3f}",
            match["link"],
        ])  # fmt: skip
    # UTF-8 with a byte-order mark, so Excel and Numbers read the names correctly.
    data = out.getvalue().encode("utf-8-sig")
    folders = [Path(s["path"]) for s in sources.values() if isinstance(s.get("path"), str)]
    path = fileops.write_export(lib, lib.paths.reports / SAMPLE_NAME, data, sources=folders)
    return path, len(chosen)
