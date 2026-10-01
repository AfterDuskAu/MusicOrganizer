"""Lyrics timed to a song's video (v0.2).

Timed lyrics are timed to the song: the album track. A music video is rarely that track
second for second. It starts with a few seconds of picture before the music, or has a
scene in the middle, or loses a verse, so the song's lyrics are out of step with it
even when the two are nearly the same length (the owner, 2026-10-01: "21 Questions").

What's done about it: the video's sound is lined up with the song's, and each line of
the lyrics is moved to where it's sung in the video.

1. **A time map.** Both recordings are fingerprinted (Chromaprint, one item per 0.124 s)
   and `time_map()` finds where the song plays in the video: a list of `Segment`s, each
   "this stretch of the video is that stretch of the song". A plain intro is one
   segment; a scene in the middle, a verse cut out or an ending played twice make more.
2. **Re-timing.** `retime()` moves every line of the lyrics through the map. A line
   sung in a part of the song the video doesn't have is left out.

A second way, for when the sound can't be lined up (a video with a re-recorded take)
and as a check on the first: **the video's own captions**. Most official videos carry
the label's captions, one cue per sung line with the time it's sung in the video;
others have YouTube's automatic ones, a time per word. `caption_times()` finds each
lyric line's words in the captions and takes the caption's time for it. Measured on 13
official videos (2026-10-02): 11 had captions good enough, their cue times within
0.1 to 0.25 s of each other line to line, and about half a second ahead of hand-timed
lyrics. Two of the owner's four examples had none, which is why both ways are needed.

Nothing is saved in the library: the answer is for the app to show while the video
plays. The map and the video's fingerprint are kept in the index's cache.
"""

from __future__ import annotations

import logging
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from itertools import pairwise
from pathlib import Path
from typing import Any

from musicorg import browse, fingerprint, lyrics, youtube
from musicorg.errors import AudioError, MusicOrgError, YouTubeBlockedError, YouTubeError
from musicorg.index import Index
from musicorg.library import Library

log = logging.getLogger(__name__)

CACHE_DAYS = 30  # a video and its captions don't change
MISS_CACHE_DAYS = 1  # "couldn't be timed" is tried again the next day
AGREE_S = 1.0  # two methods agree on a line when they put it this close
AGREE_SHARE_MIN = 0.5  # fewer lines agreeing than this: they disagree altogether
DISAGREE_RUN = 5  # lines in a row that disagree: the song's lyrics are wrong there
CAPTION_LEAD_S = (-2.0, 1.0)  # captions come up a little before the line is sung
MIN_LINES_TIMED = 0.5  # of the lyric lines must land in the video for a method to count

_STAMP = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")

# Captions (measured 2026-10-02, see the module docstring).
MIN_CAPTION_WORDS = 50  # a track of "[Music]" and eight words times nothing
MIN_ANCHORS = 4  # lyric lines found in the captions, at least
MIN_ANCHOR_SHARE = 0.15  # … and at least this share of the lines
ANCHOR_WORDS = 0.6  # of a line's words must be found for the line to count
NEAREST_ANCHORS = 3  # a line takes the median shift of this many anchors around it
PAINT_ON_GAP_S = 0.25
_NOTE = "♪"
_DESCRIPTION = re.compile(r"\[[^\]]*\]|\(\s[^)]*\s\)")  # [Music], ( music playing )
_NOT_A_LETTER = re.compile(r"[^\w']", re.UNICODE)
_GAP = -0.6


@dataclass(frozen=True)
class Segment:
    """A stretch of the video in which the song plays, and which stretch of the song it
    is. All seconds. `speed` is song seconds per video second: 1.0 unless the video
    plays the song a touch fast or slow."""

    video_start_s: float
    video_end_s: float
    song_start_s: float
    song_end_s: float
    speed: float = 1.0
    ber: float = 0.0  # mean bit error rate of the matched items (0 = identical)

    def to_video(self, song_time: float) -> float:
        return self.video_start_s + (song_time - self.song_start_s) / self.speed

    def to_dict(self) -> dict[str, float]:
        return {
            "video_start_s": round(self.video_start_s, 3),
            "video_end_s": round(self.video_end_s, 3),
            "song_start_s": round(self.song_start_s, 3),
            "song_end_s": round(self.song_end_s, 3),
            "speed": round(self.speed, 6),
            "ber": round(self.ber, 4),
        }


@dataclass(frozen=True)
class TimeMap:
    """Where the song plays in the video. `verdict` is `aligned` (most of the song is
    there), `partly`, or `different` (not the same recording: nothing can be timed).
    `coverage` is the share of the song that plays somewhere in the video; `segments`
    are in the video's order."""

    verdict: str
    coverage: float
    segments: tuple[Segment, ...] = ()
    why: str = ""


def video_times(segments: Sequence[Segment], song_time: float) -> list[float]:
    """Every moment of the video where a moment of the song plays: usually one, two
    where the video plays a part of the song twice, none where it leaves it out."""
    return [
        max(0.0, segment.to_video(song_time))
        for segment in segments
        if segment.song_start_s <= song_time < segment.song_end_s
    ]


BLANK_AFTER_S = 3.0  # a stretch of video this long with none of the song clears the line
LINE_LEFT_S = 1.0  # a line a segment cuts into is shown if this much of it is left


def line_places(
    times: Sequence[float], segments: Sequence[Segment]
) -> tuple[list[list[float]], list[float]]:
    """Where the song's lines (their start times, in order) come up in the video: for
    each line the moments it's shown, and the moments from which no line should show.

    A line the video leaves out has no moment; a line it plays twice has two. The line
    being sung where a segment comes in is shown from the segment's start, if at least
    a second of it is left. Where three seconds or more of video follow a segment with
    none of the song (a scene, the credits), the line is cleared."""
    places: list[list[float]] = [[] for _ in times]
    blanks: list[float] = []
    for number, segment in enumerate(segments):
        before = [n for n, at in enumerate(times) if at < segment.song_start_s]
        if before:
            cut_into = before[-1]
            ends = times[cut_into + 1] if cut_into + 1 < len(times) else float("inf")
            if ends - segment.song_start_s >= LINE_LEFT_S:
                places[cut_into].append(segment.video_start_s)
        for n, at in enumerate(times):
            if segment.song_start_s <= at < segment.song_end_s:
                places[n].append(max(0.0, segment.to_video(at)))
        following = (
            segments[number + 1].video_start_s if number + 1 < len(segments) else float("inf")
        )
        if following - segment.video_end_s >= BLANK_AFTER_S:
            blanks.append(segment.video_end_s)
    return places, blanks


def placed(
    lines: Sequence[tuple[float, str]], places: Sequence[Sequence[float]], blanks: Sequence[float]
) -> list[tuple[float, str]]:
    """Lines at the moments worked out for them, in the video's order, with a wordless
    line (a pause) wherever no line should show. The same line twice running is one."""
    moved = [(there, n) for n, found in enumerate(places) for there in found]
    moved += [(there, -1) for there in blanks]
    moved.sort(key=lambda item: item[0])  # stable: lines at one moment keep their order
    out: list[tuple[float, str]] = []
    last = None
    for there, n in moved:
        if n == last or (n == -1 and not out):
            continue
        out.append((there, "" if n == -1 else lines[n][1]))
        last = n
    return out


def retime(
    lines: Sequence[tuple[float, str]], segments: Sequence[Segment]
) -> list[tuple[float, str]]:
    """Lyric lines moved from the song's time to the video's, in the video's order."""
    places, blanks = line_places([at for at, _ in lines], segments)
    return placed(lines, places, blanks)


# ---- lining two recordings up by their sound ----------------------------------------------
#
# Worked out and measured on 2026-10-02 against six songs and their official videos
# (among them the owner's four: 21 Questions, Lost Boy, Breezeblocks, Work Out). `d` is
# always an offset in items: video item j lines up with song item j - d.
#
# 1. Candidate offsets, by voting. Every pair with video[j] == song[i] exactly votes for
#    d = j - i. Values that occur more than COMMON_VALUE times in the song don't vote
#    (silence, drones). Votes are counted over the whole video and inside each block of
#    BLOCK items, so an offset that only holds for 15 s is still noticed.
# 2. The best path along the video's items (Viterbi). States: each candidate offset, and
#    "none". An item's cost in an offset state is its bit error rate against the song
#    item it lines up with; in "none" it's NONE_COST. Changing state costs: a nearby
#    offset P_SMALL (drift), a smaller offset P_JUMP (the video cut something out), a
#    larger one P_BACK (the video repeats: dear on purpose, since a looped beat matches
#    itself one loop away; at 10 the looped ending of "21 Questions" was taken for a
#    repeat, at 20 a real 20 s repeat is still found), into or out of "none" P_NONE.
# 3. Runs of the path are joined into chains while their offsets stay within NEAR items;
#    a chain with less than MIN_CHAIN_S of matched audio is dropped.
# 4. Each chain's offset is refined to a fraction of an item in blocks of FIT_BLOCK
#    items, and a line fitted through the blocks: a steady drift is a speed difference.
# 5. An item looks at FP_SPAN_S of audio and the path changes state part way into it, so
#    a boundary is put EDGE_ITEM_S after the item's start. A segment within LEAD_S of
#    the song's start or end is stretched to it when the video has room: an effect or a
#    fade laid over the song's first seconds is no reason to lose a line there.
#
# Measured: on five real pairs the map was within 6 ms of the waveforms at all 30 places
# checked; a cut point is known to about ±0.3 s; all 60 wrong pairings (a song against
# another song's video) came back "different". A video that plays the song 2 % or more
# fast or slow, or has its own mix of it, is not lined up.

ITEM_S = 1365 / 11025  # seconds per raw Chromaprint item (11025 Hz, a hop of 1365 samples)
FP_SPAN_S = (19 * 1365 + 4096) / 11025  # the 2.72 s of audio behind one item
EDGE_ITEM_S = 0.9  # measured on made-up cuts: the path changes state this far into an item
COMMON_VALUE = 20
BLOCK_HOP = 80  # voting blocks are 160 items (about 20 s), overlapping by half
PER_BLOCK = 3
GLOBAL_TOP = 8
MIN_VOTES = 6
MAX_CANDIDATES = 60
FILL = 4  # holes this small between two candidate offsets are filled in (a drifting offset)
NONE_COST = 0.33
P_JUMP, P_BACK, P_NONE, P_SMALL = 3.0, 20.0, 1.5, 0.3
NEAR = 2
MIN_CHAIN_S = 5.0
FIT_BLOCK = 80
DRIFT_MIN_S = 0.15
DRIFT_MIN_CHAIN_S = 40.0
LEAD_S = 4.0  # a segment this close to the song's start or end is stretched to it
MIN_COVER = 0.30
FULL_COVER = 0.85
GOOD_BER = 0.20
MIN_ITEMS = 40


def time_map(song: Sequence[int], video: Sequence[int]) -> TimeMap:
    """Where the song plays in the video, from the two raw fingerprints."""
    n, m = len(song), len(video)
    if n < MIN_ITEMS or m < MIN_ITEMS:
        return TimeMap("different", 0.0, why="Too little audio to line up.")
    offsets = _candidate_offsets(song, video)
    if not offsets:
        return TimeMap(
            "different", 0.0, why="No part of the song's audio was found in the video's."
        )
    song_s, video_s = n * ITEM_S + FP_SPAN_S, m * ITEM_S + FP_SPAN_S
    path = _best_path(song, video, offsets)

    chains: list[list[tuple[int, int, int]]] = []
    for d, first, last in _runs(path):
        if d is None:
            continue
        if chains and abs(chains[-1][-1][0] - d) <= NEAR:
            chains[-1].append((d, first, last))
        else:
            chains.append([(d, first, last)])
    chains = [c for c in chains if sum(j1 - j0 for _, j0, j1 in c) * ITEM_S >= MIN_CHAIN_S]

    found: list[Segment] = []
    for chain in chains:
        found.append(_segment(song, video, chain, song_s, video_s))
    segments: list[Segment] = []
    for number, segment in enumerate(found):  # no two segments share video time
        if number + 1 < len(found):
            end = min(segment.video_end_s, found[number + 1].video_start_s)
            segment = replace(
                segment,
                video_end_s=end,
                song_end_s=segment.song_start_s + segment.speed * (end - segment.video_start_s),
            )
        segments.append(segment)

    segments = _reach_song_ends(segments, song_s, video_s)

    covered = 0.0
    at = 0.0
    for low, high in sorted(
        (max(0.0, s.song_start_s), min(song_s, s.song_end_s)) for s in segments
    ):
        covered += max(0.0, high - max(at, low))
        at = max(at, high)
    coverage = covered / song_s
    if not any(s.ber <= GOOD_BER for s in segments) or coverage < MIN_COVER:
        return TimeMap(
            "different", round(coverage, 3), tuple(segments),
            "Too little of the song's audio was found in the video's.",
        )  # fmt: skip
    return TimeMap(
        "aligned" if coverage >= FULL_COVER else "partly", round(coverage, 3), tuple(segments),
        f"{len(segments)} segment(s); {coverage:.0%} of the song plays in the video.",
    )  # fmt: skip


def _reach_song_ends(segments: list[Segment], song_s: float, video_s: float) -> list[Segment]:
    """A segment that starts within LEAD_S of the song's start (or ends that close to its
    end) is stretched to it, when no other segment has that part of the song and the
    video has room."""
    out = list(segments)
    for k, segment in enumerate(out):
        others = [other for n, other in enumerate(out) if n != k]
        lead = segment.song_start_s
        if 0 < lead <= LEAD_S and not any(o.song_start_s < segment.song_start_s for o in others):
            room = segment.video_start_s - (out[k - 1].video_end_s if k else 0.0)
            if room >= lead / segment.speed - 0.05:
                segment = replace(
                    segment,
                    video_start_s=max(0.0, segment.video_start_s - lead / segment.speed),
                    song_start_s=0.0,
                )
        tail = song_s - segment.song_end_s
        if 0 < tail <= LEAD_S and not any(o.song_end_s > segment.song_end_s for o in others):
            following = out[k + 1].video_start_s if k + 1 < len(out) else video_s
            if following - segment.video_end_s >= tail / segment.speed - 0.05:
                segment = replace(
                    segment,
                    video_end_s=min(video_s, segment.video_end_s + tail / segment.speed),
                    song_end_s=song_s,
                )
        out[k] = segment
    return out


def _candidate_offsets(song: Sequence[int], video: Sequence[int]) -> list[int]:
    where: dict[int, list[int]] = {}
    for i, value in enumerate(song):
        where.setdefault(value, []).append(i)
    whole: Counter[int] = Counter()
    blocks: dict[int, Counter[int]] = defaultdict(Counter)
    for j, value in enumerate(video):
        places = where.get(value)
        if not places or len(places) > COMMON_VALUE:
            continue
        block = j // BLOCK_HOP
        for i in places:
            whole[j - i] += 1
            blocks[block][j - i] += 1
            if block > 0:
                blocks[block - 1][j - i] += 1  # blocks overlap by half

    def peaks(votes: Counter[int], take: int) -> list[int]:
        smooth = {d: n + votes.get(d - 1, 0) + votes.get(d + 1, 0) for d, n in votes.items()}
        chosen: list[int] = []
        for d, n in sorted(smooth.items(), key=lambda kv: (-kv[1], -votes[kv[0]], kv[0])):
            if n < MIN_VOTES or len(chosen) >= take:
                break
            best = max((d, d - 1, d + 1), key=lambda x: votes.get(x, 0))
            if all(abs(best - c) > 1 for c in chosen):
                chosen.append(best)
        return chosen

    scored: Counter[int] = Counter()
    for d in peaks(whole, GLOBAL_TOP):
        scored[d] += whole[d] + 1000
    for votes in blocks.values():
        for d in peaks(votes, PER_BLOCK):
            scored[d] += votes[d]
    chosen = sorted(d for d, _ in scored.most_common(MAX_CANDIDATES))
    filled = set(chosen)
    for low, high in pairwise(chosen):
        if high - low <= FILL:  # a drifting offset: every step in between is a state
            filled.update(range(low + 1, high))
    return sorted(filled)


def _best_path(song: Sequence[int], video: Sequence[int], offsets: list[int]) -> list[int | None]:
    """For each video item: the offset the song plays at there, or None."""
    n, m, k = len(song), len(video), len(offsets)
    cost = []
    for d in offsets:
        row = [1.0] * m
        for j in range(max(0, d), min(m, n + d)):
            row[j] = (video[j] ^ song[j - d]).bit_count() / 32
        cost.append(row)
    near = [
        [t for t in range(k) if t != s and abs(offsets[t] - offsets[s]) <= NEAR] for s in range(k)
    ]
    prev = [cost[s][0] for s in range(k)]
    prev_none = NONE_COST
    none_at = -(10**9)  # the song item the best path into "none" had reached
    back: list[list[int]] = []  # back[j - 1][s] = the state before (k is "none")
    order = sorted(range(k), key=offsets.__getitem__)
    for j in range(1, m):
        # The cheapest earlier state with a larger offset (coming down from it is a cut)
        # and with a smaller one (coming up from it is a repeat).
        cut_from = [(0.0, -1)] * k
        best, arg = float("inf"), -1
        for s in reversed(order):
            cut_from[s] = (best, arg)
            if prev[s] < best:
                best, arg = prev[s], s
        repeat_from = [(0.0, -1)] * k
        best, arg = float("inf"), -1
        for s in order:
            repeat_from[s] = (best, arg)
            if prev[s] < best:
                best, arg = prev[s], s
        cheapest = arg if best <= min(prev) else min(range(k), key=prev.__getitem__)
        row_back = [0] * (k + 1)
        current = [0.0] * k
        for s in range(k):
            value, source = prev[s], s
            other, where = cut_from[s]
            if where >= 0 and other + P_JUMP < value:
                value, source = other + P_JUMP, where
            other, where = repeat_from[s]
            if where >= 0 and other + P_BACK < value:
                value, source = other + P_BACK, where
            leave = P_NONE if j - offsets[s] >= none_at - NEAR else P_NONE + P_BACK - P_JUMP
            if prev_none + leave < value:
                value, source = prev_none + leave, k
            for t in near[s]:
                if prev[t] + P_SMALL < value:
                    value, source = prev[t] + P_SMALL, t
            current[s] = value + cost[s][j]
            row_back[s] = source
        if prev[cheapest] + P_NONE < prev_none:
            current_none, row_back[k] = prev[cheapest] + P_NONE + NONE_COST, cheapest
            none_at = j - 1 - offsets[cheapest]
        else:
            current_none, row_back[k] = prev_none + NONE_COST, k
        back.append(row_back)
        prev, prev_none = current, current_none
    state = min(range(k), key=prev.__getitem__)
    if prev_none <= prev[state]:
        state = k
    states = [state]
    for row_back in reversed(back):
        state = row_back[state]
        states.append(state)
    states.reverse()
    return [None if state == k else offsets[state] for state in states]


def _runs(path: list[int | None]) -> list[tuple[int | None, int, int]]:
    runs, start = [], 0
    for j in range(1, len(path) + 1):
        if j == len(path) or path[j] != path[start]:
            runs.append((path[start], start, j))
            start = j
    return runs


def _mean_ber(
    song: Sequence[int], video: Sequence[int], items: Sequence[int], offset: float,
    slope: float = 0.0, first: int = 0,
) -> float:  # fmt: skip
    total = count = 0
    for j in items:
        i = j - round(offset + slope * (j - first))
        if 0 <= i < len(song):
            total += (video[j] ^ song[i]).bit_count()
            count += 1
    return total / (32 * count) if count else 1.0


def _fine_offset(
    song: Sequence[int], video: Sequence[int], items: Sequence[int], centre: int
) -> float:
    """The offset near `centre` that fits these video items best, to a fraction of an
    item: the error rate rises about evenly on each side of the true offset (a V), and
    the V's tip through the three lowest points is taken."""
    bers = {d: _mean_ber(song, video, items, d) for d in range(centre - 3, centre + 4)}
    lowest = min(bers, key=lambda d: (bers[d], abs(d - centre)))
    for d in (lowest - 1, lowest + 1):
        if d not in bers:
            bers[d] = _mean_ber(song, video, items, d)
    left, middle, right = bers[lowest - 1], bers[lowest], bers[lowest + 1]
    rise = max(left, right) - middle
    part = 0.5 * (left - right) / rise if rise > 1e-9 else 0.0
    return lowest + max(-0.5, min(0.5, part))


def _segment(
    song: Sequence[int], video: Sequence[int], chain: list[tuple[int, int, int]],
    song_s: float, video_s: float,
) -> Segment:  # fmt: skip
    n, m = len(song), len(video)
    items = [j for _, j0, j1 in chain for j in range(j0, j1)]
    offset_of = {j: d for d, j0, j1 in chain for j in range(j0, j1)}
    first, last = items[0], items[-1] + 1
    points: list[tuple[float, float, int]] = []  # (centre item, offset in items, weight)
    for at in range(0, len(items), FIT_BLOCK):
        block = items[at : at + FIT_BLOCK]
        if len(block) < FIT_BLOCK // 2 and points:
            continue
        centre = round(statistics.median(offset_of[j] for j in block))
        points.append(
            (sum(block) / len(block), _fine_offset(song, video, block, centre), len(block))
        )
    offsets = [point[1] for point in points]
    const = statistics.median(offsets)
    spread = (sum((o - const) ** 2 for o in offsets) / len(offsets)) ** 0.5
    slope = 0.0
    if len(points) >= 4 and (last - first) * ITEM_S >= DRIFT_MIN_CHAIN_S:
        weight = sum(point[2] for point in points)
        mean_x = sum(point[0] * point[2] for point in points) / weight
        mean_y = sum(point[1] * point[2] for point in points) / weight
        sxx = sum(point[2] * (point[0] - mean_x) ** 2 for point in points)
        sxy = sum(point[2] * (point[0] - mean_x) * (point[1] - mean_y) for point in points)
        fit = sxy / sxx if sxx else 0.0
        fit_spread = (
            sum(point[2] * (point[1] - (mean_y + fit * (point[0] - mean_x))) ** 2
                for point in points) / weight
        ) ** 0.5  # fmt: skip
        # A real speed difference: enough drift, and the line explains the blocks.
        if abs(fit) * (last - first) * ITEM_S >= DRIFT_MIN_S and fit_spread <= 0.5 * spread:
            slope, const = fit, mean_y + fit * (first - mean_x)
    ber = _mean_ber(song, video, items, const, slope, first)
    video_start = 0.0 if first == 0 else first * ITEM_S + EDGE_ITEM_S
    video_end = video_s if last == m else last * ITEM_S + EDGE_ITEM_S
    if first - const <= 1:  # the song's own first item: reach back to the song's start
        video_start = max(0.0, const * ITEM_S)
    if last - (const + slope * (last - first)) >= n - 1:  # the song's last item
        video_end = min(video_s, last * ITEM_S + FP_SPAN_S)
    speed = 1.0 - slope
    song_start = (video_start - first * ITEM_S) * speed + (first - const) * ITEM_S
    return Segment(
        video_start_s=video_start, video_end_s=video_end, song_start_s=song_start,
        song_end_s=min(song_s, song_start + speed * (video_end - video_start)),
        speed=speed, ber=ber,
    )  # fmt: skip


def lines_of(lrc: str) -> list[tuple[float, str]]:
    """Timed lyrics as (seconds, words) lines, in order. A line with a time and no words
    is a pause (nothing is sung from there), and is kept as one."""
    found: list[tuple[float, str]] = []
    for raw in lrc.splitlines():
        line = raw.strip()
        times = []
        while (stamp := _STAMP.match(line)) is not None:
            fraction = stamp.group(3) or "0"
            times.append(
                int(stamp.group(1)) * 60 + int(stamp.group(2)) + int(fraction) / 10 ** len(fraction)
            )
            line = line[stamp.end() :]
        found += [(at, line.strip()) for at in times]
    found.sort(key=lambda item: item[0])
    return found if any(words for _, words in found) else []


def to_lrc(lines: Sequence[tuple[float, str]]) -> str:
    out = []
    for at, words in lines:
        centis = round(max(0.0, at) * 100)
        out.append(f"[{centis // 6000:02d}:{centis // 100 % 60:02d}.{centis % 100:02d}]{words}")
    return "\n".join(out) + "\n"


# ---- the video's captions ---------------------------------------------------------------


@dataclass(frozen=True)
class Cue:
    """One caption: when it comes up, its text, and (automatic captions only) the time
    of each of its words."""

    start_s: float
    text: str
    word_times: tuple[tuple[float, str], ...] | None = None

    @property
    def sung(self) -> bool:
        return _NOTE in self.text


@dataclass(frozen=True)
class _Word:
    time_s: float
    word: str
    exact: bool  # the time is this word's own, not just its cue's


def parse_captions(data: Any) -> list[Cue]:
    """YouTube's "json3" captions as cues. Checked on 13 tracks (yt-dlp 2026.08.19):
    `events[]` each carry `tStartMs`, `dDurationMs` and `segs[].utf8`; an automatic
    track also gives `segs[].tOffsetMs`, each word's delay within its event. A caption
    that's painted on letter by letter arrives as back-to-back events, each repeating
    the last and adding to it: those are folded into one cue at the first one's time."""
    events = data.get("events") if isinstance(data, dict) else None
    cues: list[Cue] = []
    last_end = 0.0
    for event in events if isinstance(events, list) else []:
        segs = event.get("segs") if isinstance(event, dict) else None
        if not isinstance(segs, list) or not segs:
            continue
        start = _ms(event.get("tStartMs"))
        end = start + _ms(event.get("dDurationMs"))
        parts = [seg.get("utf8") for seg in segs if isinstance(seg, dict)]
        text = "".join(part for part in parts if isinstance(part, str))
        if not text.strip():
            continue
        timed = any(isinstance(seg, dict) and "tOffsetMs" in seg for seg in segs)
        words = None
        if timed:
            words = tuple(
                (start + _ms(seg.get("tOffsetMs")), word)
                for seg in segs
                if isinstance(seg, dict) and isinstance(seg.get("utf8"), str)
                for word in words_of(_DESCRIPTION.sub(" ", seg["utf8"]))
            )
        if cues and not timed and cues[-1].word_times is None:
            before = cues[-1].text.strip()
            if (
                text.strip().startswith(before)
                and len(before) < len(text.strip())
                and start - last_end < PAINT_ON_GAP_S
            ):
                cues[-1] = Cue(cues[-1].start_s, text)
                last_end = end
                continue
        cues.append(Cue(start, text, words))
        last_end = end
    return cues


def _ms(value: Any) -> float:
    return value / 1000 if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def words_of(text: str) -> list[str]:
    """A line's words as they're compared: lower case, no punctuation, "singin'" and
    "singing" the same."""
    found = []
    for raw in re.split(r"[\s\-—–]+", text.casefold().replace("’", "'")):
        word = _NOT_A_LETTER.sub("", raw).replace("'", "").replace("_", "")
        if word.endswith("in") and len(word) > 4:
            word += "g"
        if word:
            found.append(word)
    return found


def _caption_words(cues: Sequence[Cue]) -> list[_Word]:
    found: list[_Word] = []
    for cue in cues:
        if cue.word_times is not None:
            found += [_Word(at, word, True) for at, word in cue.word_times]
            continue
        words = words_of(_DESCRIPTION.sub(" ", cue.text.replace(_NOTE, " ")))
        found += [_Word(cue.start_s, word, n == 0) for n, word in enumerate(words)]
    return found


def _alike(a: str, b: str, memo: dict[tuple[str, str], float]) -> float:
    if a == b:
        return 2.0
    key = (a, b)
    if key not in memo:
        if abs(len(a) - len(b)) > 2 or min(len(a), len(b)) < 3:
            memo[key] = -1.0
        else:
            close = SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.75
            memo[key] = 1.0 if close else -1.0
    return memo[key]


def _pair_words(lyric: Sequence[str], caption: Sequence[str]) -> dict[int, int]:
    """Which lyric word is which caption word: the two lists lined up in order, whole
    (the classic alignment), with caption words before and after the lyrics free."""
    n, m = len(lyric), len(caption)
    memo: dict[tuple[str, str], float] = {}
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [bytearray(m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0], back[i][0] = i * _GAP, 1
    for j in range(1, m + 1):
        back[0][j] = 2
    for i in range(1, n + 1):
        word, row, above, moves = lyric[i - 1], score[i], score[i - 1], back[i]
        for j in range(1, m + 1):
            diagonal = above[j - 1] + _alike(word, caption[j - 1], memo)
            up, left = above[j] + _GAP, row[j - 1] + _GAP
            if diagonal >= up and diagonal >= left:
                row[j], moves[j] = diagonal, 0
            elif up >= left:
                row[j], moves[j] = up, 1
            else:
                row[j], moves[j] = left, 2
    i, j = n, max(range(m + 1), key=lambda k: score[n][k])
    pairs: dict[int, int] = {}
    while i > 0:
        move = back[i][j]
        if move == 0 and j > 0:
            if _alike(lyric[i - 1], caption[j - 1], memo) > 0:
                pairs[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif move == 1 or j == 0:
            i -= 1
        else:
            j -= 1
    return pairs


def caption_times(
    lines: Sequence[tuple[float, str]], cues: Sequence[Cue]
) -> list[tuple[float, str]] | None:
    """The lyric lines with the times the video's captions give them, or None if the
    captions don't carry these words (too few, another language, "[Music]").

    A line whose words are found in the captions, first word included, is an *anchor*:
    the caption says when it's sung. Every line then moves by the median shift of the
    three anchors nearest it, so a line the captions garbled still lands with its
    neighbours, one mis-heard anchor can't throw a line, and a shift that changes part
    way through the video (a scene in the middle) is followed. Right at such a change,
    an anchor keeps its own caption's time."""
    caption = _caption_words(cues)
    if len(caption) < MIN_CAPTION_WORDS or not lines:
        return None
    flat: list[str] = []
    owner: list[tuple[int, int]] = []
    counts: list[int] = []
    for number, (_, text) in enumerate(lines):
        words = words_of(text)
        counts.append(len(words))
        flat += words
        owner += [(number, place) for place in range(len(words))]
    pairs = _pair_words(flat, [word.word for word in caption])
    found: dict[int, list[tuple[int, int]]] = {}
    for lyric_at, caption_at in pairs.items():
        number, place = owner[lyric_at]
        found.setdefault(number, []).append((place, caption_at))
    anchors: list[tuple[int, float]] = []
    for number, (at, _) in enumerate(lines):
        matched = sorted(found.get(number, []))
        if not matched or counts[number] == 0:
            continue
        place, caption_at = matched[0]
        first = caption[caption_at]
        if (
            place == 0
            and first.exact
            and len(matched) / counts[number] >= ANCHOR_WORDS
            and (len(matched) >= 2 or counts[number] == 1)
        ):
            anchors.append((number, first.time_s - at))
    if len(anchors) < max(MIN_ANCHORS, NEAREST_ANCHORS, MIN_ANCHOR_SHARE * len(lines)):
        return None
    own = dict(anchors)
    moved: list[tuple[float, str]] = []
    latest = 0.0
    for number, (at, text) in enumerate(lines):
        near = sorted(anchors, key=lambda anchor: abs(anchor[0] - number))
        shift = statistics.median(shift for _, shift in near[:NEAREST_ANCHORS])
        if number in own:
            # An anchor whose two neighbours agree with each other takes the three's
            # median, so one mis-heard line can't stray. Where they don't agree, the
            # shift is changing right here (a cut in the video, or lyrics badly timed
            # in themselves) and the line's own caption is the best there is.
            others = [shift for line, shift in near if line != number][:2]
            if len(others) == 2 and abs(others[0] - others[1]) > AGREE_S:
                shift = own[number]
        latest = max(latest, at + shift)
        moved.append((latest, text))  # never before the line above it
    return moved


def sung_lines(cues: Sequence[Cue]) -> list[tuple[float, str]] | None:
    """The captions themselves as timed lyrics, for a song with no timed lyrics of its
    own: the cues marked as sung (a music note), without the mark. None unless the
    track is the label's own captions with enough sung lines to be the song."""
    lines = []
    for cue in cues:
        if cue.word_times is None and cue.sung:
            words = " ".join(cue.text.replace(_NOTE, " ").split())
            if words:
                lines.append((cue.start_s, words))
    return lines if len(lines) >= 10 else None


# ---- the whole thing: lyrics timed to a video ---------------------------------------------


@dataclass(frozen=True)
class Timed:
    """Lyrics timed to a video. `how` says which way they were timed: `audio` (the two
    recordings lined up), `captions` (the song's lyrics pinned to the video's captions),
    `caption_text` (the captions themselves, for a song with no timed lyrics) or
    `lrclib` (a record timed to the video's cut). `synced` is None when none worked."""

    synced: str | None
    how: str | None = None
    source: str | None = None
    note: str | None = None
    settled: bool = True  # False: YouTube didn't answer something, so try again next time

    def to_dict(self) -> dict[str, Any]:
        return {"synced": self.synced, "plain": None, "how": self.how, "source": self.source,
                "note": self.note}  # fmt: skip


def for_video(
    lib: Library,
    index: Index,
    *,
    title: str,
    artist: str,
    video_id: str,
    video_duration_s: float | None = None,
    song_path: str | None = None,
    song_video_id: str | None = None,
    song_duration_s: float | None = None,
    video_path: str | None = None,
    full: bool = False,
) -> Timed:
    """The song's lyrics, timed to this video of it.

    **Without `full`, YouTube is asked nothing** (the owner, 2026-10-02: timing every
    video that's played would spend requests on videos nobody is reading the lyrics
    of). The answer is then what's already known: lyrics timed to this video on an
    earlier request, or else a record on LRCLIB of the video's length that passes the
    check in 4 below. With `full` (the app's Karaoke button) everything below is done.

    `song_path` is the library song
    that's playing (its own `.lrc` and its own sound are what's lined up); without it,
    `song_video_id` is the song on YouTube Music, and without that the song is looked
    for by name. `video_path` is the video itself when it's a saved one in the library:
    its sound is then read from the file. `index` must be writable: what's worked out
    is kept in it, so the same video is quick the next time.

    Tried, in this order of trust:

    1. **The sound.** The song and the video are lined up by their audio and the
       song's lyrics moved through the map. Exact (a twentieth of a second) wherever
       the video plays the same recording.
    2. **The captions.** The song's lyrics are pinned to the video's own captions. When
       both ways worked and agree, the sound's answer is used. Where a stretch of lines
       disagrees, the captions place those lines, and when the two disagree altogether
       the captions are used whole: they were made for the video, and the song's lyrics
       can be badly timed in themselves, which the sound can't know (`_merge`).
    3. With no timed lyrics for the song at all: the label's captions as the lyrics.
    4. A record on LRCLIB of the video's length, but only if its times differ from the
       song's own. Most records "of the video's length" are the album's lyrics under
       another length, which is how lyrics came to be 30 and 75 seconds out.

    When none works the answer has no lyrics, and the app shows the words untimed."""
    if not youtube.VIDEO_ID.fullmatch(video_id):
        raise YouTubeError(f"{video_id!r} isn't a YouTube video id.")
    song_file = browse.track_path(lib, song_path) if song_path else None
    video_file = browse.track_path(lib, video_path) if video_path else None
    stamp = ""
    if song_file is not None:
        lrc_file = song_file.with_suffix(".lrc")
        info = (lrc_file if lrc_file.is_file() else song_file).stat()
        stamp = f"{song_path}:{info.st_size}:{info.st_mtime_ns}"
    key = f"video lyrics 2 {video_id} {stamp or song_video_id or youtube.query_key(title)}"
    kept = index.cached_search(key, max_age_days=CACHE_DAYS)
    if isinstance(kept, dict) and (
        kept.get("synced") or index.cached_search(key, max_age_days=MISS_CACHE_DAYS)
    ):
        return Timed(kept.get("synced"), kept.get("how"), kept.get("source"), kept.get("note"))
    if not full:
        return _already_known(index, title, artist, video_duration_s, song_file, song_duration_s)

    found = _work_out(
        index, title=title, artist=artist, video_id=video_id,
        video_duration_s=video_duration_s, song_file=song_file, song_video_id=song_video_id,
        song_duration_s=song_duration_s, video_file=video_file,
    )  # fmt: skip
    if found.synced or found.settled:  # a miss because YouTube didn't answer isn't kept
        index.put_search(key, {"synced": found.synced, "how": found.how,
                               "source": found.source, "note": found.note})  # fmt: skip
    return found


def _already_known(
    index: Index,
    title: str,
    artist: str,
    video_duration_s: float | None,
    song_file: Path | None,
    song_duration_s: float | None,
) -> Timed:
    """What can be said without asking YouTube anything: a record on LRCLIB of the
    video's length whose times differ from the song's own. (LRCLIB is asked, at its own
    pace; YouTube Music's lyrics aren't, so a song whose only timed lyrics are there has
    nothing to check a record against, and gets none.)"""
    lrc = None
    if song_file is not None and song_file.with_suffix(".lrc").is_file():
        lrc = lyrics.check_lrc(
            song_file.with_suffix(".lrc").read_text(encoding="utf-8", errors="replace")
        )
    if lrc is None:
        lrc = lyrics.find(
            lyrics.Query(title=title, artist=artist, album=None, duration_s=song_duration_s),
            cache=index,
        ).synced
    cut = _video_cut_record(index, title, artist, video_duration_s, lines_of(lrc) if lrc else [])
    if cut is not None:
        return Timed(cut, "lrclib", "LRCLIB")
    return Timed(None, settled=False)  # not looked into yet: nothing to remember


def _work_out(
    index: Index,
    *,
    title: str,
    artist: str,
    video_id: str,
    video_duration_s: float | None,
    song_file: Path | None,
    song_video_id: str | None,
    song_duration_s: float | None,
    video_file: Path | None,
) -> Timed:
    if song_file is None and song_video_id is None:
        # A saved video, or a song known only by name: the official song it's a video of.
        song = _official_song(index, title, artist)
        if song is not None:
            song_video_id, song_duration_s = song.video_id, song.duration_s
    # The song's own timed lyrics: its .lrc, or what the usual sources have for it.
    lrc = None
    where = None
    if song_file is not None and song_file.with_suffix(".lrc").is_file():
        lrc = lyrics.check_lrc(
            song_file.with_suffix(".lrc").read_text(encoding="utf-8", errors="replace")
        )
        where = "your library"
    if lrc is None:
        looked = lyrics.find(
            lyrics.Query(title=title, artist=artist, album=None, duration_s=song_duration_s,
                         video_id=song_video_id, official_s=song_duration_s),
            cache=index,
        )  # fmt: skip
        lrc, where = looked.synced, looked.source
    lines = lines_of(lrc) if lrc else []

    try:
        sources = youtube.sources(video_id)
    except YouTubeBlockedError:
        raise
    except MusicOrgError as exc:
        log.info("video lyrics: %s can't be looked at: %s", video_id, exc)
        return Timed(None, note="YouTube didn't answer for that video.", settled=False)

    trouble: list[str] = []  # what YouTube didn't hand over this time
    by_sound = (
        _by_sound(index, lines, sources, song_file, song_video_id, video_file, trouble)
        if lines
        else None
    )
    cues = None
    by_captions = None
    caption_kind = None
    for track in sources.captions:
        try:
            cues = parse_captions(youtube.fetch_captions(track))
        except YouTubeBlockedError:
            raise
        except MusicOrgError as exc:
            log.info("video lyrics: the %s captions of %s: %s", track.kind, video_id, exc)
            trouble.append("captions")
            continue
        if not lines:
            break  # only the first (the label's own) can stand in for the lyrics
        by_captions = caption_times(lines, cues)
        if by_captions is not None:
            caption_kind = track.kind
            break

    if by_sound is not None and by_captions is not None:
        merged = _merge(lines, by_sound[0], by_captions, manual=caption_kind == "manual")
        if merged is None:
            note = "The video's captions and its sound disagreed; the captions were used."
            return Timed(to_lrc(by_captions), "captions", where, note)
        return Timed(to_lrc(placed(lines, merged[0], by_sound[1])), "audio", where, merged[1])
    if by_sound is not None:
        return Timed(to_lrc(placed(lines, *by_sound)), "audio", where)
    if by_captions is not None:
        return Timed(to_lrc(by_captions), "captions", where)
    if not lines and cues is not None and sources.captions[0].kind == "manual":
        sung = sung_lines(cues)
        if sung is not None:
            return Timed(to_lrc(sung), "caption_text", "YouTube captions")
    cut = _video_cut_record(index, title, artist, video_duration_s, lines)
    if cut is not None:
        return Timed(cut, "lrclib", "LRCLIB")
    if trouble:
        return Timed(None, note="YouTube didn't hand over the video's sound.", settled=False)
    return Timed(None, note="Neither the video's sound nor its captions could time the lyrics.")


def _by_sound(
    index: Index,
    lines: list[tuple[float, str]],
    sources: youtube.VideoSources,
    song_file: Path | None,
    song_video_id: str | None,
    video_file: Path | None,
    trouble: list[str],
) -> tuple[list[list[float]], list[float]] | None:
    """The lyrics moved through the time map: for each of the song's lines the moments
    of the video it comes up at, and the moments no line should show from. None if the
    two recordings can't be lined up, or too few lines land."""
    try:
        if song_file is not None:
            song = fingerprint.fingerprint(song_file, index=index).items
        elif song_video_id:
            song = _stream_items(index, song_video_id, None)
        else:
            return None
        if video_file is not None:
            video = fingerprint.fingerprint(video_file, index=index).items
        else:
            video = _stream_items(index, sources.video_id, sources.audio)
    except YouTubeBlockedError:
        raise
    except (MusicOrgError, AudioError) as exc:
        log.info("video lyrics: no fingerprint for %s: %s", sources.video_id, exc)
        trouble.append("sound")
        return None
    found = time_map(song, video)
    log.info("video lyrics: %s: %s %s", sources.video_id, found.verdict, found.why)
    if found.verdict == "different":
        return None
    places, blanks = line_places([at for at, _ in lines], found.segments)
    if sum(bool(place) for place in places) < MIN_LINES_TIMED * len(lines):
        return None
    return places, blanks


def _official_song(index: Index, title: str, artist: str) -> youtube.Candidate | None:
    try:
        results = youtube.search_songs(f"{artist} {title}".strip(), 5, cache=index)
    except YouTubeBlockedError:
        raise
    except MusicOrgError as exc:
        log.info("video lyrics: looking for the song %r: %s", title, exc)
        return None
    for candidate in results:
        if candidate.is_official_audio and youtube.same_song(title, artist, candidate):
            return candidate
    return None


def _stream_items(index: Index, video_id: str, stream: youtube.Stream | None) -> tuple[int, ...]:
    """A YouTube audio's raw fingerprint, fetched once and kept for 30 days."""
    key = f"fingerprint {video_id}"
    kept = index.cached_search(key, max_age_days=CACHE_DAYS)
    if isinstance(kept, list) and kept:
        return tuple(int(item) for item in kept)
    if stream is None:
        stream = youtube.sources(video_id).audio
        if stream is None:
            raise YouTubeError("YouTube doesn't offer that song in the format we read.")
    try:
        data = youtube.fetch_audio(stream)
    except YouTubeBlockedError:
        raise
    except MusicOrgError as exc:
        # Now and then YouTube refuses an address it has just given out (HTTP 403); a
        # fresh one works. Measured 2026-10-02: once in about fifteen fetches.
        log.info("video lyrics: fetching %s again with a fresh address: %s", video_id, exc)
        data = youtube.fetch_audio(youtube.stream(video_id))
    items = fingerprint.fingerprint_bytes(data, "that video's sound").items
    index.put_search(key, list(items))
    return items


def _merge(
    lines: list[tuple[float, str]],
    by_sound: list[list[float]],
    by_captions: list[tuple[float, str]],
    *,
    manual: bool,
) -> tuple[list[list[float]], str | None] | None:
    """The sound's answer (each line's moments in the video), checked line by line
    against the captions'. None when the two disagree altogether (then the captions are
    used whole).

    Normally they agree, the captions a steady half second ahead, and the sound's
    times stand: they're the exact ones. A *stretch* of lines that disagree (five or
    more in a row) is where the song's own lyrics are badly timed, which the sound
    can't know: one lyric file in circulation has its last forty lines crammed into
    fourteen seconds. Those lines take the captions' times instead, if the captions
    are the label's own. A line or two that disagree are left with the sound:
    captions mis-hear, and a repeated chorus can be pinned to the wrong repeat."""
    apart: list[float | None] = []
    for places, (caption_at, _) in zip(by_sound, by_captions, strict=True):
        apart.append(min((caption_at - place for place in places), key=abs) if places else None)
    known = [gap for gap in apart if gap is not None]
    if len(known) < MIN_ANCHORS:
        return [list(found) for found in by_sound], None  # too little to say it's wrong
    lead = statistics.median(known)
    agrees = [
        gap is None or not text or abs(gap - lead) <= AGREE_S
        for gap, (_, text) in zip(apart, lines, strict=True)
    ]
    share = sum(gap is not None and abs(gap - lead) <= AGREE_S for gap in apart) / len(known)
    if not CAPTION_LEAD_S[0] <= lead <= CAPTION_LEAD_S[1] or share < AGREE_SHARE_MIN:
        return None
    replaced = 0
    places = [list(found) for found in by_sound]
    if manual:
        run_start = None
        for number in range(len(lines) + 1):
            if number < len(lines) and not agrees[number]:
                run_start = number if run_start is None else run_start
                continue
            if run_start is not None and number - run_start >= DISAGREE_RUN:
                for line in range(run_start, number):
                    places[line] = [max(0.0, by_captions[line][0] - lead)]
                replaced += number - run_start
            run_start = None
    note = (
        f"{replaced} lines were badly timed in the song's own lyrics; the video's captions "
        "placed them."
        if replaced
        else None
    )
    return places, note


def _video_cut_record(
    index: Index,
    title: str,
    artist: str,
    video_duration_s: float | None,
    song_lines: list[tuple[float, str]],
) -> str | None:
    """LRCLIB's record of the video's length, if it's really timed to the video: its
    times must differ from the song's own. (Measured: of 9 videos that differ from their
    song, the record "of the video's length" was the album's timing on 5.)"""
    if video_duration_s is None or not song_lines:
        return None  # nothing to check it against, so it isn't trusted
    found = lyrics.find(
        lyrics.Query(title=title, artist=artist, album=None, duration_s=video_duration_s),
        cache=index,
    )
    theirs = lines_of(found.synced) if found.synced else []
    if not theirs:
        return None
    same = sum(
        abs(mine[0] - other[0]) <= 0.5 for mine, other in zip(song_lines, theirs, strict=False)
    )
    if same >= 0.5 * min(len(song_lines), len(theirs)):
        return None  # the album's lyrics under another length
    return found.synced
