"""Artist, title and version parsing from messy rip names and tags (step 05).

Rips are named after YouTube video titles: "Artist - Title (Official Video) [HQ]",
"Title (Adventure Club Remix) [Monstercat Release]", "YOASOBI「夜に駆ける」Official Music
Video". This module turns them into what matching needs:

- `parse_filename(stem)` and `parse_tags(tags)` give a `Parsed`: the main artist, the
  clean title, version tokens, what was stripped as junk, and a confidence 0–1.
- `best_parse(stem, tags)` prefers the tags when they look real, otherwise the filename.
- `parse_title(title)` reads an official title (YouTube Music's, or real tags);
  `parse_owner_title(title)` reads a title the owner named, and knows their R mark.
- `full_title(parsed)`: the title a copy named from a rip is given: the clean title and
  every version the rip names, in the rip's own words.
- `compare_key(text)`: the form names are compared in.

Version tokens are `kind[:detail]`, e.g. `remix:adventure club`, `live:wembley`,
`slowed`. They are kept, never thrown away: a remix is not the original. Tokens are made
for comparing (lower case, no accents). The words the name itself uses are kept beside
them (`version_words`: "Âme Remix", "Acoustic Version", the owner's "R"), because a
song's title is written in those.

Confidence: a clean "Artist - Title" is at least 0.8; a single token, or a likely
reversed order, is below 0.5. "Low confidence" means below 0.5 everywhere.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from musicorg.tags import TrackTags

LOW_CONFIDENCE = 0.5
HIGH_CONFIDENCE = 0.8

# Confidence given to each kind of parse.
_TAGS = 0.95
_SPLIT = 0.9  # "Artist - Title"
_QUOTED = 0.85  # YOASOBI「夜に駆ける」, BTS 'Dynamite'
_TAG_ARTIST = 0.8  # an artist tag, and a file name that is only the title
_LOOSE_SPLIT = 0.75  # "Artist- Title": a dash with a space on one side only
_MANY_PARTS = 0.6  # "A - B - C" that couldn't be explained
_REMIXER_FIRST = 0.6  # "Remixer - Title (Remixer Remix)": the real artist is unknown
_PIPE = 0.45  # "Title | Artist" or "Artist | Title": can't tell
_REVERSED = 0.4  # "Title (Official Video) - Artist"
_TITLE_ONLY = 0.3
_NOTHING = 0.0

# Version kinds (docs: step 05). Soft ones don't change the recording enough to matter
# for matching (step 06).
VERSION_KINDS = frozenset({
    "remix", "edit", "radio edit", "extended", "vip", "bootleg", "flip", "live",
    "acoustic", "unplugged", "remaster", "instrumental", "sped up", "slowed",
    "nightcore", "cover", "demo", "clean", "explicit",
})  # fmt: skip
SOFT_VERSION_KINDS = frozenset({"clean", "explicit", "remaster"})


@dataclass(frozen=True)
class Parsed:
    """What a rip is, as far as its name or tags tell."""

    artist: str | None  # the main artist
    title: str | None  # without junk, featured artists or version tokens
    version_tokens: tuple[str, ...] = ()
    junk_removed: tuple[str, ...] = ()
    confidence: float = 0.0
    artists: tuple[str, ...] = ()  # everyone credited, main artist first, featured last
    credit: str | None = None  # the artist as credited, before splitting ("Simon & Garfunkel")
    artist_uncertain: bool = False  # e.g. a remixer named where the artist should be
    source: str = "filename"  # "filename" or "tags"
    notes: tuple[str, ...] = field(default=(), compare=False)
    # Each version as the name words it, in the name's order: "Lucian Remix", "Acoustic
    # Version", and the owner's mark as typed ("R", "(R)"). `full_title` writes a title
    # with them; comparing uses `version_tokens` only, so two parses that differ just in
    # these words are equal.
    version_words: tuple[str, ...] = field(default=(), compare=False)

    @property
    def band(self) -> str:
        return confidence_band(self.confidence)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in _TUPLE_FIELDS:
            data[key] = list(data[key])
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Parsed:
        """The inverse of `to_dict` (e.g. the index's `parsed_json`). Unknown keys are
        ignored, and missing ones are empty, so an index from an older or a newer engine
        still reads. A parse stored before `version_words` existed has none: to get them,
        parse the names again (`scan.parse_again`)."""
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in known}
        for key in _TUPLE_FIELDS:
            values[key] = tuple(values.get(key) or ())
        return cls(**values)


_TUPLE_FIELDS = ("version_tokens", "junk_removed", "artists", "notes", "version_words")


def confidence_band(confidence: float) -> str:
    """`high` (≥ 0.8), `mid`, or `low` (< 0.5)."""
    if confidence >= HIGH_CONFIDENCE:
        return "high"
    return "mid" if confidence >= LOW_CONFIDENCE else "low"


# ---- comparing -------------------------------------------------------------------------

_APOSTROPHES = re.compile(r"['’`´]")


def compare_key(text: str | None) -> str:
    """The form names are compared in: NFKC, case-folded, accents on Latin letters
    removed, `&` → `and`, punctuation and symbols dropped, spaces collapsed. Other
    scripts are kept as they are; nothing is transliterated."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = _fold_latin_accents(text)
    text = text.replace("&", " and ")
    text = _APOSTROPHES.sub("", text)
    chars = [" " if unicodedata.category(c)[0] in "PSZC" else c for c in text]
    return " ".join("".join(chars).split())


def _fold_latin_accents(text: str) -> str:
    """ "Beyoncé" → "beyonce", but "が" stays "が": marks are only dropped from Latin
    letters."""
    out = []
    for ch in unicodedata.normalize("NFD", text):
        if unicodedata.category(ch) == "Mn" and out and "a" <= out[-1] <= "z":
            continue
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))


def render_versions(tokens: tuple[str, ...] | list[str]) -> str:
    """Version tokens as words for a search: "remix:adventure club" → "adventure club
    remix"."""
    words = []
    for token in tokens:
        kind, _, detail = token.partition(":")
        if kind in ("remix", "bootleg", "flip", "cover") and detail:
            words.append(f"{detail} {kind}")
        elif detail:
            words.append(f"{kind} {detail}")
        else:
            words.append(kind)
    return " ".join(words)


# ---- junk ------------------------------------------------------------------------------

_JUNK_PHRASES = [
    r"(?:official\s+)?(?:hd\s+|4k\s+)?(?:music\s+|lyrics?\s+|explicit\s+)?(?:video|audio|clip|mv|m/v)"
    r"(?:\s+clip)?(?:\s+(?:hd|hq|4k))?",
    r"official",
    r"(?:official\s+)?(?:lyric|lyrics)(?:\s+video)?",
    r"with\s+lyrics",
    r"(?:official\s+)?visuali[sz]er",
    r"audio(?:\s+only)?",
    r"hq|hd|4k|uhd|\d{3,4}p(?:\s*hd)?",
    r"\d{2,3}\s*kbps|\d{3}k",
    r"(?:free\s+(?:download|dl))|download",
    r"out\s+now(?:\s+.*)?",
    r"premiere",
    r"full\s+(?:song|version|audio)",
    r"original\s+(?:mix|version)|album\s+version",
    r".*\brelease\b.*",  # [Monstercat Release], [NCS Release]
    r"mp3|m4a|ytmp3|y2mate(?:\.com)?|savefrom(?:\.net)?|mp3juices",
    r"new(?:\s+song)?(?:\s+\d{4})?",
    r"\d{4}\s+(?:new|hit)",
    r"(?:prod(?:\.|uced)?|dir(?:\.|ected)?|shot)\s+by\s+.+|prod\.?\s+.+",
    r"(?:official\s+)?(?:performance|music)\s+video|m\s*/\s*v|mv",
    r"supported\s+by\s+.+",  # [Supported by Timmy Trumpet]
]
_JUNK = re.compile(r"(?:" + "|".join(_JUNK_PHRASES) + r")", re.IGNORECASE)
# A group made only of these words is junk when one names the upload's form: "(Animated
# Video)", "[FULL-HD]", "[HD UPGRADE]", "(Video Oficial)", "(Very High Audio Quality)".
_JUNK_FORM_WORDS = frozenset({
    "video", "videoclip", "clip", "audio", "lyric", "lyrics", "letra", "visualizer",
    "visualiser", "mv", "hd", "hq", "uhd", "4k", "8k", "quality",
})  # fmt: skip
_JUNK_FILLER_WORDS = frozenset({
    "official", "oficial", "officiel", "music", "musical", "full", "upgrade", "upgraded",
    "animated", "very", "high", "best", "new", "with", "and", "the",
})  # fmt: skip
_RESOLUTION = re.compile(r"\d{3,4}p")
# "(From "Top Gun: Maverick")", "- From the Motion Picture ...": the same recording, so
# junk, but only in brackets or after a title's " - " ("From Me to You" is a title).
_SOUNDTRACK = re.compile(r"(?:music\s+)?from\s+\S.*|.*\b(?:soundtrack|ost)\b.*", re.IGNORECASE)
# Genre tags in square brackets on Monstercat/NCS-style uploads: [Dubstep], [Trap].
_GENRES = re.compile(
    r"(?:trap|dubstep|house|edm|dnb|drum\s*(?:and|&|n|'n')\s*bass|electro(?:nic)?|"
    r"future\s+bass|glitch\s+hop|drumstep|trance|melodic\s+dubstep|chill(?:step|out)?|"
    r"lo-?fi|hardstyle|bass\s+house|future\s+house|pop|hip[\s-]?hop|rap|rock|metal|indie|"
    r"nu\s+disco|electro\s+house|progressive\s+house|deep\s+house|tropical\s+house|"
    r"synthwave|future\s+funk|hybrid\s+trap|breaks|garage|bass|techno|ambient|"
    r"electronic|chillout|downtempo|j-?pop|k-?pop)",
    re.IGNORECASE,
)
# A web address, as download sites put their own in names: "Y2meta.app", "mp3convert.org".
# Only these endings, so "Mr.Kitty", "E.T." and "Will.i.am" aren't taken for one.
_DOMAIN = (
    r"(?:www\.)?[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)*"
    r"\.(?:com|net|org|app|to|io|cc|me|co|biz|info|tv|ws|site|online|pro|xyz|ru|fm)(?!\w)"
)
_DOMAIN_RE = re.compile(_DOMAIN, re.IGNORECASE)
# Download-site names at the start or the end of a name: "Y2meta.app - ", "_(mp3convert.org)".
_SITE_MARKERS = re.compile(
    rf"^\s*[\[(]?{_DOMAIN}[\])]?\s*[-–—~|:]\s*"
    rf"|(?:\s*[-–—~|_]\s*|\s+|(?=[\[(]))[\[(]?{_DOMAIN}[\])]?\s*$",
    re.IGNORECASE,
)
# onlymp3.to and others end names with "-<video id>-192k-<timestamp>".
_CONVERTER_SUFFIX = re.compile(r"-(?P<id>[A-Za-z0-9_-]{11})-\d{2,3}k-\d{10,13}$")
_LEADING_JUNK = re.compile(
    r"^\s*(?:premiere\s*[:|\-–]|\[?free(?:\s+(?:download|dl))?\]?\s*[:|\-–]|"
    r"out\s+now\s*[:|\-–]|new\s*[:|\-–])\s*",
    re.IGNORECASE,
)
_TRACK_NUMBER = re.compile(r"^\s*(\d{1,3})\s*(?:[.)_]|\s-\s|-(?=\s))\s*")
_ZERO_PADDED = re.compile(r"^\s*(0\d)\s+(?=\S)")
_BARE_COUNTER = re.compile(r"^\d{1,2}$")  # "(1)" added by a browser to a duplicate


def _is_junk(text: str, bracket: str = "(") -> bool:
    text = text.strip().strip(".!:-–|,").strip()
    if not text:
        return True
    if _JUNK.fullmatch(text):
        return True
    if bracket == "[" and _GENRES.fullmatch(text):
        return True
    if _DOMAIN_RE.fullmatch(text):
        return True
    # Several junk phrases together: "Official Video HD", "Audio + Lyrics".
    words = re.split(r"\s*(?:[/+&,|]|\s-\s)\s*|\s+", text)
    if len(words) > 1 and all(_JUNK.fullmatch(w) or not w for w in words):
        return True
    words = re.findall(r"[^\W_]+", text.casefold())
    form = [w for w in words if w in _JUNK_FORM_WORDS or _RESOLUTION.fullmatch(w)]
    return bool(form) and all(w in _JUNK_FILLER_WORDS or w in form for w in words)


def _strip_symbols(text: str, junk: list[str]) -> str:
    """Emoji and decorative symbols (★ ♪ 🔥) are dropped."""
    kept = []
    dropped = False
    for ch in text:
        code = ord(ch)
        if (
            unicodedata.category(ch) in ("So", "Cs")
            or 0xFE00 <= code <= 0xFE0F
            or code == 0x200D
            or 0x1F3FB <= code <= 0x1F3FF
        ):
            dropped = True
            kept.append(" ")
        else:
            kept.append(ch)
    if dropped:
        junk.append("emoji")
    return "".join(kept)


# ---- versions and featured artists ------------------------------------------------------

_WHO = r"(?P<who>.+?)"
# Each version kind and the phrasings that mean it, tried in order. `who` is the remixer,
# performer or venue; `year` the remaster's year.
_VERSION_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p, re.IGNORECASE), kind)
    for p, kind in [
        (r"slowed(?:\s+down)?(?:\s*(?:\+|&|and|n|'n'|x|/)?\s*reverb(?:ed)?)?"
         r"(?:\s+version)?", "slowed"),
        (r"(?:\+\s*)?reverb(?:ed)?", "slowed"),
        (r"sped\s*up(?:\s*(?:\+|&|and)\s*reverb)?(?:\s+version)?|speed\s*up", "sped up"),
        (r"nightcore(?:\s+(?:mix|remix|edit|version|ver\.?))?", "nightcore"),
        (r"radio\s+(?:edit|version|mix)", "radio edit"),
        (r"extended(?:\s+(?:mix|version|edit|remix))?", "extended"),
        (r"(?:" + _WHO + r"\s+)?vip(?:\s+(?:mix|remix|edit))?", "vip"),
        (r"bootleg\s+by\s+" + _WHO, "bootleg"),
        (r"(?:" + _WHO + r"\s+)?bootleg", "bootleg"),
        (r"flip\s+by\s+" + _WHO, "flip"),
        (r"(?:" + _WHO + r"\s+)?flip", "flip"),
        (r"remix(?:ed)?\s+by\s+" + _WHO, "remix"),
        (_WHO + r"(?:'s)?\s+(?:remix|rmx|re-?mix)(?:\s+edit)?", "remix"),
        (r"remix|rmx", "remix"),
        (r"(?!original\b|album\b|radio\b|extended\b)" + _WHO + r"\s+mix", "remix"),
        (r"(?:" + _WHO + r"\s+)?edit", "edit"),
        (r"live(?:\s+(?:at|from|in|@|on)\s+" + _WHO + r")?"
         r"(?:\s+(?:version|performance|session))?", "live"),
        (_WHO + r"\s+live(?:\s+session)?", "live"),
        (r"(?:mtv\s+)?unplugged", "unplugged"),
        (r"acoustic(?:\s+(?:version|session|mix))?", "acoustic"),
        (r"(?:(?P<year>\d{4})\s+)?remaster(?:ed)?(?:\s+(?P<year2>\d{4}))?"
         r"(?:\s+version)?", "remaster"),
        (r"instrumental(?:\s+version)?|karaoke(?:\s+version)?", "instrumental"),
        (r"cover\s+by\s+" + _WHO, "cover"),
        (r"(?:" + _WHO + r"\s+)?cover(?:\s+version)?", "cover"),
        (r"demo(?:\s+version)?", "demo"),
        (r"(?:super\s+)?clean(?:\s+(?:version|edit|radio\s+edit|mix))?|radio\s+clean"
         r"|edited(?:\s+version)?|censored(?:\s+version)?", "clean"),
        (r"explicit(?:\s+version)?|dirty(?:\s+version)?|uncensored", "explicit"),
    ]
]  # fmt: skip
_FEAT = re.compile(r"^(?:feat\.?|ft\.?|featuring|with)\s+(?P<who>.+)$", re.IGNORECASE)
_FEAT_INLINE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+", re.IGNORECASE)
_ARTIST_SPLIT = re.compile(
    r"\s+(?:feat\.?|ft\.?|featuring|with|vs\.?)\s+|\s+x\s+|\s+×\s+|\s+&\s+|\s*,\s*",
)
_ARTIST_SPLIT_CI = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|with|vs\.?)\s+", re.IGNORECASE)


def _version_tokens(text: str) -> list[str] | None:
    """The version tokens `text` consists of, or None if it isn't only versions.
    "Adventure Club Remix" → ["remix:adventure club"]; "Slowed + Reverb" → ["slowed"]."""
    text = text.strip().strip(".!:-–|").strip()
    if not text:
        return None
    for pattern, kind in _VERSION_RULES:
        match = pattern.fullmatch(text)
        if not match:
            continue
        groups = match.groupdict()
        detail = groups.get("who")
        if kind == "remaster":
            detail = groups.get("year") or groups.get("year2")
        if detail and (_is_junk(detail) or _version_tokens(detail) is not None):
            if kind in ("remix", "edit", "vip", "cover"):
                inner = _version_tokens(detail)
                if inner is not None and kind == "remix":
                    return [*inner, kind]
            detail = None
        return [_token(kind, detail)]
    # Two or more joined: "Sped Up + Reverb", "Extended Mix / Remastered".
    parts = re.split(r"\s*(?:\+|/|,|\||\s-\s)\s*", text)
    if len(parts) > 1:
        tokens: list[str] = []
        for part in parts:
            found = _version_tokens(part)
            if found is None:
                return None
            tokens += found
        return tokens
    return None


def _token(kind: str, detail: str | None) -> str:
    if not detail:
        return kind
    detail = re.sub(r"'s$", "", detail.strip(), flags=re.IGNORECASE)
    detail = compare_key(detail)
    return f"{kind}:{detail}" if detail else kind


def _featured(text: str) -> list[str] | None:
    match = _FEAT.fullmatch(text.strip())
    if not match:
        return None
    return _split_artists(match.group("who"))


def _split_artists(text: str) -> list[str]:
    """ "A feat. B & C" → ["A", "B", "C"]. " x " only splits in lower case, so "Lil Nas X"
    stays whole."""
    text = _ARTIST_SPLIT_CI.sub(" feat. ", text)
    names = [n.strip(" .,-") for n in _ARTIST_SPLIT.split(text)]
    return [n for n in names if n]


# ---- brackets --------------------------------------------------------------------------

_PAIRS = {"(": ")", "[": "]", "{": "}", "【": "】", "〔": "〕", "（": "）", "「": "」", "『": "』"}


@dataclass
class _Group:
    opener: str
    content: str


def _extract_groups(text: str) -> tuple[str, list[_Group]]:
    """Take the top-level bracketed groups out of `text`, leaving a numbered marker for
    each ("\\x00N\\x00"). Nested brackets stay inside their group. An unclosed bracket is
    left as text."""
    out: list[str] = []
    groups: list[_Group] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in _PAIRS:
            end = _closing(text, i)
            if end is not None:
                groups.append(_Group(ch, text[i + 1 : end]))
                out.append(f" \x00{len(groups) - 1}\x00 ")
                i = end + 1
                continue
        out.append(ch)
        i += 1
    return "".join(out), groups


def _closing(text: str, start: int) -> int | None:
    opener, closer = text[start], _PAIRS[text[start]]
    depth = 0
    for i in range(start, len(text)):
        if text[i] == opener:
            depth += 1
        elif text[i] == closer:
            depth -= 1
            if depth == 0:
                return i
    return None


_MARKER = re.compile(r"\x00(\d+)\x00")


@dataclass
class _Found:
    """What parsing collects on the way."""

    junk: list[str] = field(default_factory=list)
    versions: list[str] = field(default_factory=list)
    words: list[str] = field(default_factory=list)  # the versions, as the name words them
    featured: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    title_groups: int = 0  # groups a part had that weren't junk/version/featured


def _note_versions(found: _Found, tokens: list[str], words: str) -> None:
    """Record a version taken out of a name: its tokens, for comparing, and the words
    the name used for it ("Lucian Remix"), for writing a title (`full_title`)."""
    found.versions += tokens
    words = " ".join(_drop_unmatched(words).split())
    if tokens and words:
        found.words.append(words)


def _resolve(part: str, groups: list[_Group], found: _Found, *, last_part: bool) -> str:
    """Put a part's bracketed groups back as text, or take them out as junk, versions or
    featured artists."""
    markers = [int(m) for m in _MARKER.findall(part)]

    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        group = groups[index]
        is_last = last_part and index == markers[-1]
        kept = _classify(group, found, is_last=is_last)
        if kept is None:
            return " "
        found.title_groups += 1
        return f" {group.opener}{kept}{_PAIRS[group.opener]} "

    return _MARKER.sub(replace, part)


def _classify(group: _Group, found: _Found, *, is_last: bool) -> str | None:
    """None if the group was taken out; otherwise its content to keep in the text."""
    inner_text, inner_groups = _extract_groups(group.content)
    content = _resolve(inner_text, inner_groups, found, last_part=False)
    content = " ".join(content.split())
    if not content:
        return None
    if (
        _is_junk(content, group.opener)
        or _SOUNDTRACK.fullmatch(content)
        or (is_last and _BARE_COUNTER.fullmatch(content))
    ):
        found.junk.append(content.casefold())
        return None
    featured = _featured(content)
    if featured is not None:
        found.featured += featured
        return None
    versions = _version_tokens(content)
    if versions is not None:
        _note_versions(found, versions, content)
        return None
    # "(Adventure Club Remix Official Audio)": a version with junk after it.
    for cut in range(len(content.split()) - 1, 0, -1):
        words = content.split()
        head, tail = " ".join(words[:cut]), " ".join(words[cut:])
        versions = _version_tokens(head)
        if versions is not None and _is_junk(tail):
            _note_versions(found, versions, head)
            found.junk.append(tail.casefold())
            return None
    return content


# ---- free text at the ends -------------------------------------------------------------

_FREE_TRAILING_VERSION = re.compile(
    r"(?:^|\s+|\s*[-–]\s*)(?P<v>slowed(?:\s+down)?(?:\s*(?:\+|&|and|n)\s*reverb(?:ed)?)?"
    r"|sped\s*up(?:\s*(?:\+|&|and)\s*reverb)?|speed\s*up|nightcore)\s*$",
    re.IGNORECASE,
)
_FREE_TRAILING_JUNK = re.compile(
    r"(?:\s+|\s*[-–|]\s*)(?P<j>(?:official\s+)?(?:music\s+|lyrics?\s+)?(?:video|audio|visuali[sz]er)"
    r"|official|lyrics?|with\s+lyrics|hq|hd|4k|\d{3,4}p|\d{2,3}\s*kbps|free\s+download|out\s+now"
    r"|full\s+song|m/?v)\s*$",
    re.IGNORECASE,
)


def _strip_free_ends(text: str, found: _Found) -> str:
    while True:
        text = text.strip(" -–|:,")
        match = _FREE_TRAILING_JUNK.search(text)
        if match and match.start() > 0:
            found.junk.append(match.group("j").casefold())
            text = text[: match.start()]
            continue
        match = _FREE_TRAILING_VERSION.search(text)
        if match and match.start() > 0:
            _note_versions(found, _version_tokens(match.group("v")) or [], match.group("v"))
            text = text[: match.start()]
            continue
        return text


def _take_featured_inline(text: str, found: _Found) -> str:
    """ "Title feat. X" → "Title", with X featured."""
    parts = _FEAT_INLINE.split(text, maxsplit=1)
    if len(parts) == 2 and parts[0].strip():
        found.featured += _split_artists(parts[1])
        return parts[0]
    return text


# ---- parsing a file name ---------------------------------------------------------------

_SEPARATORS = re.compile(
    r"\s+[-–—~_]\s+|\s+\|\s+|\s*\|\s*(?=\S)|\s[-–—]{2}\s"
    # A dash or tilde with a space on one side only: "Artist- Title", "Artist -Title".
    # "Jay-Z" has none, so it stays whole.
    r"|(?<=\S)[-–—~]\s+|\s+[-–—~](?=\S)"
)
_UNDERSCORE_APOSTROPHE = re.compile(r"(?<=[A-Za-z])_(?=(?:t|s|ll|re|ve|m|d)(?![A-Za-z0-9]))")
# Dashes some names use that look like a hyphen or an en dash.
_ODD_DASHES = str.maketrans({"‐": "-", "‑": "-", "‒": "–", "―": "—", "−": "-"})
_NAMED_ENTITY = re.compile(r"&[a-z]+;", re.IGNORECASE)
_QUOTED_TITLE = re.compile(
    r"^(?P<artist>[^「『'‘\"“]+?)\s*(?P<open>[「『'‘\"“])(?P<title>.+?)[」』'’\"”]\s*(?P<rest>.*)$"
)
_CHANNEL_SUFFIX = re.compile(r"\s*(?:vevo|\s-\s?topic|official)$", re.IGNORECASE)


def _prepare(text: str) -> str:
    """NFC, web entities decoded ("can&#39t" → "can't", but "S&M" stays), odd dashes made
    plain, and spaces collapsed."""
    text = unicodedata.normalize("NFC", text)
    if "&#" in text or _NAMED_ENTITY.search(text):
        text = html.unescape(text)
    return " ".join(text.translate(_ODD_DASHES).split())


# The owner's own mark for a remix: a final "R" or "(R)" ("Stressed Out R", "Done Wrong
# (R)", "Black Out Days R(slowed)"). Upper case only, and never the whole title.
_OWNER_REMIX = re.compile(r"(?:(?<=\s)R|\(R\)|\[R\])$")
OWNER_REMIX_MARKS = ("R", "(R)", "[R]")  # the ways the mark is typed


def is_owner_mark(words: str) -> bool:
    """Whether a version's words (an entry of `Parsed.version_words`) are the owner's
    mark for a remix. It is the one version that isn't written in round brackets: it
    stays as the owner typed it."""
    return words in OWNER_REMIX_MARKS


def _owner_remix(parsed: Parsed) -> Parsed:
    title = (parsed.title or "").rstrip()
    match = _OWNER_REMIX.search(title)
    if not match or not title[: match.start()].strip():
        return parsed
    versions, words = parsed.version_tokens, parsed.version_words
    if not any(t.partition(":")[0] == "remix" for t in versions):
        versions = (*versions, "remix")
        words = (*words, match.group(0))  # as typed: it is the owner's name for the song
    return _replace(
        parsed,
        title=title[: match.start()].rstrip(" -–(["),
        version_tokens=versions,
        version_words=words,
        notes=(*parsed.notes, "R: the owner's mark for a remix"),
    )


def _in_name_order(parsed: Parsed, name: str | None) -> Parsed:
    """`parsed`, with its version words in the order `name` has them. Parsing finds
    them in another order (brackets, then loose words at the end, then the owner's
    mark): "Black Out Days R(slowed)" is found as slowed, R, and named R, slowed."""
    words = parsed.version_words
    if len(words) < 2 or not isinstance(name, str):
        return parsed
    text = " ".join(_prepare(name).replace("_", " ").split())
    ordered = tuple(sorted(words, key=lambda entry: _position(entry, text)))
    return parsed if ordered == words else _replace(parsed, version_words=ordered)


def _position(words: str, text: str) -> int:
    """Where a version's words sit in a name, counted from its start. Words that can't
    be found go last. The owner's mark, if it can't be found, goes first: it was read
    off the end of the title itself."""
    if is_owner_mark(words):
        pattern = r"(?<=\s)R(?!\w)" if words == "R" else re.escape(words)
        places = [match.start() for match in re.finditer(pattern, text)]
        return places[-1] if places else -1
    place = text.rfind(words)
    return place if place >= 0 else len(text)


def parse_filename(stem: str) -> Parsed:
    """Parse a rip's file name (without its extension)."""
    return _in_name_order(_owner_remix(_parse_filename(stem)), stem)


def _parse_filename(stem: str) -> Parsed:
    found = _Found()
    text = _prepare(stem)
    suffix = _CONVERTER_SUFFIX.search(text)
    if suffix and text[: suffix.start()].strip():
        found.junk.append("download site")
        found.notes.append(f"video id {suffix.group('id')}")
        text = text[: suffix.start()]
    text = _UNDERSCORE_APOSTROPHE.sub("'", text)  # "Ain_t" → "Ain't"
    if "_" in text and text.count(" ") < text.count("_"):
        text = text.replace("_", " ")
    else:
        text = re.sub(r"(?<!\s)_|_(?!\s)", " ", text)  # quotes and colons: "(From _Film_)"
    text = " ".join(text.split())
    marked = _SITE_MARKERS.sub(" ", text)
    if marked != text and marked.strip():
        found.junk.append("download site")
        text = " ".join(marked.split())
    if " " not in text and text.count("-") >= 3:
        text = text.replace("-", " ")  # "john-newman-love-me-again"
    text = _strip_symbols(text, found.junk)
    for pattern in (_LEADING_JUNK, _TRACK_NUMBER, _ZERO_PADDED):
        match = pattern.match(text)
        if match and text[match.end() :].strip():
            found.junk.append(match.group(0).strip(" .-_)").casefold() or "number")
            text = text[match.end() :]

    skeleton, groups = _extract_groups(text)
    # Junk tags at the very start ("[MV] Artist - Title", "【FREE】..."): taken out first, so
    # they aren't mistaken for a sign that the title comes first.
    while match := re.match(r"\s*\x00(\d+)\x00", skeleton):
        group = groups[int(match.group(1))]
        if not _is_junk(group.content, group.opener):
            break
        found.junk.append(group.content.strip().casefold())
        skeleton = skeleton[match.end() :]
    parts = [p for p in _SEPARATORS.split(skeleton)]
    separators = _SEPARATORS.findall(skeleton)
    piped = any("|" in s for s in separators)
    loose = any("|" not in s and not (s[0].isspace() and s[-1].isspace()) for s in separators)
    parts = [p for p in parts if p.strip()]

    if not parts:
        # Nothing is left to name the song: the name was empty, only symbols, or only
        # junk in brackets ("(Official Video)", "---", a lone emoji).
        return _result(None, None, found, _NOTHING)
    if len(parts) == 1:
        quoted = _QUOTED_TITLE.match(text)
        if quoted and quoted.group("artist").strip():
            return _quoted(quoted, found)
        title = _finish_title(parts[0], groups, found, last_part=True)
        return _result(None, title, found, _TITLE_ONLY if title else _NOTHING)

    # Parts that are only versions or junk ("- Adventure Club Remix", "- Official Video").
    kept: list[str] = []
    for index, part in enumerate(parts):
        if index > 0:
            trial = _Found()
            plain = " ".join(_resolve(part, groups, trial, last_part=False).split())
            if plain and trial.title_groups == 0:
                versions = _version_tokens(plain)
                if versions is not None or _is_junk(plain):
                    _merge(found, trial)
                    if versions is not None:
                        _note_versions(found, versions, plain)
                    else:
                        found.junk.append(plain.casefold())
                    continue
        kept.append(part)
    parts = kept

    if len(parts) == 1:
        title = _finish_title(parts[0], groups, found, last_part=True)
        return _result(None, title, found, _TITLE_ONLY if title else _NOTHING)

    artist_part, title_parts = parts[0], parts[1:]
    confidence = _SPLIT if len(title_parts) == 1 else _MANY_PARTS
    if loose:
        confidence = min(confidence, _LOOSE_SPLIT)
    # "Title (Official Video) - Artist": junk or a version on the left means it's the
    # title, so the order is reversed.
    left = _Found()
    _resolve(artist_part, groups, left, last_part=False)
    right = _Found()
    for part in title_parts:
        _resolve(part, groups, right, last_part=False)
    if (left.junk or left.versions) and not (right.junk or right.versions) and len(parts) == 2:
        artist_part, title_parts = parts[1], [parts[0]]
        confidence = _REVERSED
        found.notes.append("reversed")
    elif piped:
        confidence = min(confidence, _PIPE)

    credit, artists = _finish_artist(artist_part, groups, found)
    title = _finish_title(" - ".join(title_parts), groups, found, last_part=True)
    if not title:
        return _result(None, credit, found, _TITLE_ONLY)
    return _result(credit, title, found, confidence, artists=artists)


def _merge(found: _Found, more: _Found) -> None:
    found.junk += more.junk
    found.versions += more.versions
    found.words += more.words
    found.featured += more.featured
    found.notes += more.notes


def _quoted(match: re.Match[str], found: _Found) -> Parsed:
    """YOASOBI「夜に駆ける」Official Music Video; BTS 'Dynamite' Official MV."""
    skeleton, groups = _extract_groups(match.group("artist"))
    credit, artists = _finish_artist(skeleton, groups, found)
    rest_skeleton, rest_groups = _extract_groups(match.group("rest"))
    rest = " ".join(_resolve(rest_skeleton, rest_groups, found, last_part=True).split())
    if rest and not _is_junk(rest):
        rest = _strip_free_ends(" " + rest, found).strip()
    if rest and not _is_junk(rest):
        versions = _version_tokens(rest)
        if versions is None:
            found.notes.append(f"ignored: {rest}")
        else:
            _note_versions(found, versions, rest)
    elif rest:
        found.junk.append(rest.casefold())
    title_skeleton, title_groups = _extract_groups(match.group("title"))
    title = _finish_title(title_skeleton, title_groups, found, last_part=False)
    return _result(credit, title, found, _QUOTED if credit and title else _TITLE_ONLY,
                   artists=artists)  # fmt: skip


def _finish_title(part: str, groups: list[_Group], found: _Found, *, last_part: bool) -> str:
    text = _drop_unmatched(_resolve(part, groups, found, last_part=last_part))
    text = _take_featured_inline(text, found)
    text = _strip_free_ends(" " + text, found)
    text = " ".join(text.split()).strip(" -–|:,")
    if len(text) >= 2 and text[0] in "'‘\"“" and text[-1] in "'’\"”":
        text = text[1:-1].strip()
    return text


_CLOSERS = {closer: opener for opener, closer in _PAIRS.items()}


def _drop_unmatched(text: str) -> str:
    """Brackets without a partner are dropped: "Here (Lucian Remix))" leaves "Here )"."""
    open_at: list[int] = []
    drop: set[int] = set()
    for i, ch in enumerate(text):
        if ch in _PAIRS:
            open_at.append(i)
        elif ch in _CLOSERS:
            if open_at and text[open_at[-1]] == _CLOSERS[ch]:
                open_at.pop()
            else:
                drop.add(i)
    drop.update(open_at)
    return "".join(" " if i in drop else ch for i, ch in enumerate(text))


def _finish_artist(part: str, groups: list[_Group], found: _Found) -> tuple[str, list[str]]:
    """The credited artist text and the list of artists. Brackets in the artist part
    (an alias like "BTS (방탄소년단)", or "(Official)") are dropped."""

    def drop(match: re.Match[str]) -> str:
        group = groups[int(match.group(1))]
        featured = _featured(group.content)
        if featured is not None:
            found.featured += featured
        else:
            found.junk.append(group.content.casefold())
        return " "

    text = _MARKER.sub(drop, part)
    text = " ".join(text.split()).strip(" -–|:,")
    stripped = _CHANNEL_SUFFIX.sub("", text)
    if stripped != text and stripped:
        found.notes.append("channel name")
        text = stripped
    names = _split_artists(text)
    credit = _FEAT_INLINE.split(text, maxsplit=1)[0].strip()
    featured_in_credit = names[len(_split_artists(credit)) :]
    found.featured = [*featured_in_credit, *found.featured]
    return credit, _split_artists(credit)


def _result(
    credit: str | None,
    title: str | None,
    found: _Found,
    confidence: float,
    *,
    artists: list[str] | None = None,
) -> Parsed:
    title = title or None
    credit = credit or None
    names = list(artists or ([credit] if credit else []))
    uncertain = False
    versions = list(dict.fromkeys(found.versions))

    if credit and compare_key(credit) == "nightcore":
        # "Nightcore - Title": a channel, and a version, not the artist.
        if "nightcore" not in versions:
            found.words.append(credit)
        versions = list(dict.fromkeys([*versions, "nightcore"]))
        credit, names, confidence = None, [], min(confidence, _TITLE_ONLY)
        found.notes.append("channel name")
    if credit:
        people = {compare_key(credit), *(compare_key(n) for n in names)}
        remixers = {t.partition(":")[2] for t in versions if t.split(":")[0] in
                    ("remix", "bootleg", "flip", "vip", "edit")}  # fmt: skip
        if people & remixers:
            uncertain = True
            confidence = min(confidence, _REMIXER_FIRST)
            found.notes.append("remixer named as the artist")
    if "channel name" in found.notes and credit:
        confidence = min(confidence, 0.7)

    featured = [f for f in dict.fromkeys(found.featured) if f and compare_key(f) not in
                {compare_key(n) for n in names}]  # fmt: skip
    all_names = [*names, *featured]
    return Parsed(
        artist=names[0] if names else None,
        title=title,
        version_tokens=tuple(versions),
        junk_removed=tuple(dict.fromkeys(j for j in found.junk if j)),
        confidence=round(confidence if title else min(confidence, _NOTHING), 2),
        artists=tuple(all_names),
        credit=credit,
        artist_uncertain=uncertain,
        source="filename",
        notes=tuple(found.notes),
        version_words=_unique_words(found.words),
    )


# ---- parsing tags ----------------------------------------------------------------------

_JUNK_ARTISTS = frozenset({
    "unknown artist", "unknown", "artist", "various", "youtube", "y2mate com", "ytmp3 cc",
    "savefrom net", "none", "null", "untitled",
})  # fmt: skip
_PROMO_CHANNELS = frozenset({
    "trap nation", "proximity", "mrsuicidesheep", "monstercat", "nocopyrightsounds",
    "chill nation", "majestic casual", "bass nation", "xkito music", "selected",
    "the vibe guide", "cloudkid", "7clouds", "taz network", "lyrical lemonade",
    "trap city", "house nation", "wavemusic", "future classic",
})  # fmt: skip


def parse_tags(tags: TrackTags) -> Parsed:
    """Parse a rip's own tags. Rip converters often put the video title in the title
    and the channel in the artist; those are recognised and parsed like a file name."""
    return _in_name_order(_owner_remix(_parse_tags(tags)), tags.title)


def _parse_tags(tags: TrackTags) -> Parsed:
    title = _prepare(tags.title) if isinstance(tags.title, str) else None
    artist = _prepare(tags.artist) if isinstance(tags.artist, str) else None
    if not title:
        return Parsed(None, None, source="tags")
    artist_key = compare_key(artist)
    channel = bool(_CHANNEL_SUFFIX.search(artist or ""))  # "DrakeVEVO", "Drake - Topic"
    looks_like_name = bool(_SEPARATORS.search(title)) or bool(_QUOTED_TITLE.match(title))
    if (
        _not_an_artist(artist)
        or (channel and looks_like_name)
        or (looks_like_name and compare_key(title).startswith(artist_key))
    ):
        parsed = parse_filename(title)
        confidence = min(parsed.confidence, _SPLIT) if parsed.artist else parsed.confidence
        return _replace(parsed, source="tags", confidence=confidence)

    found = _Found()
    clean_title = _title_with_versions(title, found)
    artist_skeleton, artist_groups = _extract_groups(artist or "")
    credit, artists = _finish_artist(artist_skeleton, artist_groups, found)
    parsed = _result(credit, clean_title, found, _TAGS, artists=artists)
    return _replace(parsed, source="tags")


def _not_an_artist(artist: str | None) -> bool:
    """An artist tag that names no artist: empty, "Unknown Artist", a promo channel or a
    download site."""
    key = compare_key(artist)
    return (
        not artist
        or key in _JUNK_ARTISTS
        or key in _PROMO_CHANNELS
        or "://" in artist
        or bool(_DOMAIN_RE.fullmatch(artist.strip()))
    )


def parse_title(title: str) -> Parsed:
    """A title as YouTube Music or real tags give it, with no artist in it: "Crave You
    (Adventure Club Remix)", "Yesterday - Remastered 2009", "Hotline Bling (feat. X)".
    Version tokens and featured artists come out; nothing is taken as the artist.

    This is the reader for an official title. It doesn't know the owner's R mark, and
    mustn't: YouTube Music never writes it, and an official title that happens to end
    in " R" is not a remix. For a title the owner named, use `parse_owner_title`."""
    found = _Found()
    clean = _title_with_versions(_prepare(title), found)
    parsed = _replace(_result(None, clean, found, _TAGS), source="tags")
    return _in_name_order(parsed, title)


def parse_owner_title(title: str) -> Parsed:
    """A title the owner named, with no artist in it: the title of a copy still named
    after their rip, a title fix, a title typed in Edit Details. Read as `parse_title`
    reads it, and the owner's mark for a remix is understood too: "Lost Boy R" is the
    song "Lost Boy", version `remix`. It reads back what `full_title` writes."""
    return _in_name_order(_owner_remix(parse_title(title)), title)


def full_title(parsed: Parsed) -> str | None:
    """The title a copy named from a rip is given: the clean title, then every version
    the rip names, in the rip's own words and order. A named version goes in round
    brackets, whatever brackets the rip used: "Here (Lucian Remix)", "Still Here
    (Acoustic Version)". The owner's mark for a remix stays as they typed it, and is
    never spelled out or dropped: "Come As You Are R", "Done Wrong (R)", "Black Out Days
    R (slowed)". Junk ("(320 kbps)") is left out.

    None when there is no title. Also None when the parse names versions but not their
    words: a parse stored by an older engine, which must be parsed again first
    (`scan.parse_again`). Leaving the version out of the name is the mistake this
    function exists to stop."""
    if not parsed.title:
        return None
    if parsed.version_tokens and not parsed.version_words:
        return None
    versions = [
        words if is_owner_mark(words) else f"({words})"
        for words in _unique_words(parsed.version_words)
    ]
    return " ".join([parsed.title, *versions])


def _unique_words(words: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Version words with spaces collapsed, and one that only repeats an earlier one
    (in any letter case) left out: "(Live) (live)" names one version."""
    kept: dict[str, str] = {}
    for entry in words:
        entry = " ".join(entry.split())
        if entry:
            kept.setdefault(entry.casefold(), entry)
    return tuple(kept.values())


def _title_with_versions(title: str, found: _Found) -> str:
    """A clean title. Parts after " - " that are only versions or junk ("- Remastered
    2009", "- Live", "- Radio Edit") come out as such; any other part stays in the title
    ("Love Story - Taylor's Version")."""
    skeleton, groups = _extract_groups(title)
    parts = re.split(r"\s+[-–—]\s+", skeleton)
    kept = [parts[0]]
    for part in parts[1:]:
        trial = _Found()
        plain = " ".join(_resolve(part, groups, trial, last_part=False).split())
        if plain and trial.title_groups == 0:
            versions = _version_tokens(plain)
            if versions is not None or _is_junk(plain) or _SOUNDTRACK.fullmatch(plain):
                _merge(found, trial)
                if versions is not None:
                    _note_versions(found, versions, plain)
                else:
                    found.junk.append(plain.casefold())
                continue
        kept.append(part)
    return _finish_title(" - ".join(kept), groups, found, last_part=True)


def _replace(parsed: Parsed, **changes: Any) -> Parsed:
    return Parsed(**{**asdict(parsed), **changes})


def best_parse(stem: str, tags: TrackTags | None) -> Parsed:
    """The tags when they look real, otherwise the file name. When both name the same
    song, version tokens from either are kept: a remix named only in the file name is
    still a remix. The version's words are the tags', with the file name's for any kind
    of version the tags don't name."""
    from_name = parse_filename(stem)
    from_tags = parse_tags(tags) if tags is not None else None
    if from_tags is None or from_tags.title is None:
        return _with_tag_artist(from_name, tags)
    if from_tags.confidence < max(from_name.confidence, LOW_CONFIDENCE):
        return from_name
    if compare_key(from_tags.title) == compare_key(from_name.title):
        versions = tuple(dict.fromkeys([*from_tags.version_tokens, *from_name.version_tokens]))
        junk = tuple(dict.fromkeys([*from_tags.junk_removed, *from_name.junk_removed]))
        words = _merged_words(from_tags, from_name)
        return _replace(from_tags, version_tokens=versions, junk_removed=junk, version_words=words)
    return from_tags


def _merged_words(from_tags: Parsed, from_name: Parsed) -> tuple[str, ...]:
    """The tags' version words, then the file name's for each kind of version the tags
    don't name. A tag title "Song" with the file "Song R" gives "R"; a tag title "Here
    (Lucian Remix)" with the file "Here R" stays "Lucian Remix", so one remix isn't
    named twice."""
    have = {token.partition(":")[0] for token in from_tags.version_tokens}
    words = list(from_tags.version_words)
    for more in from_name.version_words:
        kinds = _kinds(more)
        if kinds and kinds <= have:
            continue
        words.append(more)
        have |= kinds
    return _unique_words(words)


def _kinds(words: str) -> set[str]:
    """The kinds of version some version words name: "Lucian Remix" → {"remix"}."""
    if is_owner_mark(words):
        return {"remix"}
    return {token.partition(":")[0] for token in _version_tokens(words) or []}


def _with_tag_artist(from_name: Parsed, tags: TrackTags | None) -> Parsed:
    """An artist tag but no title tag, and a file name that is only the title: Apple
    Music shows such a file under its artist, with the file name as its name. The
    artist comes from the tag and the title from the name, without the artist repeated
    at its start ("Portugal The Man Do You" → "Do You")."""
    artist = _prepare(tags.artist) if tags is not None and isinstance(tags.artist, str) else None
    if from_name.artist or not from_name.title or _not_an_artist(artist):
        return from_name
    found = _Found()
    skeleton, groups = _extract_groups(artist or "")
    credit, artists = _finish_artist(skeleton, groups, found)
    if not artists:
        return from_name
    main = {compare_key(a) for a in artists}
    featured = [a for a in (*found.featured, *from_name.artists) if compare_key(a) not in main]
    return _replace(
        from_name,
        artist=artists[0],
        credit=credit,
        artists=tuple(dict.fromkeys([*artists, *featured])),
        title=_without_leading(from_name.title, credit),
        confidence=0.7 if "channel name" in found.notes else _TAG_ARTIST,
        notes=(*from_name.notes, *found.notes, "artist from tags"),
    )


def _without_leading(title: str, artist: str) -> str:
    words = title.split()
    for n in range(1, len(words)):
        if compare_key(" ".join(words[:n])) == compare_key(artist):
            return " ".join(words[n:]).lstrip("-–—~:| ") or title
    return title
