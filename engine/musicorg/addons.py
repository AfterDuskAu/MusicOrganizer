"""Add-ons: where the app's movies and channels are listed (the owner, 2026-10-07).

An add-on is a small web service that follows the Stremio add-on protocol. It starts
from one address, its manifest, and from there answers three kinds of question:

- **catalog**: a list to browse (films, channels), sometimes with a search, a genre
  and more pages;
- **meta**: one film's or channel's details (and a channel's videos);
- **stream**: where a film can be played from.

Every address is built the same way:

    {base}/{resource}/{type}/{id}.json
    {base}/{resource}/{type}/{id}/{extra}.json      extra: key=value&key=value

where `base` is the manifest's address without its last `/manifest.json`.

This module only reads: it asks an add-on a question and tidies the answer. It plays
nothing, downloads nothing and writes nothing (the list of add-ons is kept by
`config`). It is the only module that talks to add-ons. Before asking, it checks the
manifest says the add-on answers that question, so nothing pointless is sent; and an
add-on that's slow or broken is skipped with a plain reason, because they often are.

Checked live on 2026-10-07 against the three add-ons the app starts with:

- Cinemeta (`v3-cinemeta.strem.io`): catalog and meta for `movie` and `series`, ids
  `tt…`. A meta has `name`, `poster`, `background`, `logo`, `description`, `runtime`
  ("68 min"), `releaseInfo` or `year`, `imdbRating` (text), `genres` (older: `genre`),
  `cast`, `director`, `trailerStreams` (`ytId`). No streams.
- the channels add-on (`v3-channels.strem.io`): catalog and meta for `channel`, ids
  `yt_id:<channel>`. Catalog `top` takes `genre` (Gaming, News & Politics, Sports, …)
  and `search`; its search answered HTTP 500 that day. A channel's meta has `videos`,
  each `{id: "yt_id:<channel>:<video>", title, thumbnail, released}`: the video's own
  id is the last part. No streams: a video is played by its id.
- Public Domain Movies (`caching.stremio.net/publicdomainmovies.now.sh`): catalog
  (with `skip` and `search`) and stream for `movie`. A stream there is a torrent:
  `{infoHash, fileIdx, name: "1080p", title: "💾 859.37 MB"}`.

In replay mode (`MUSICORG_REPLAY_DIR`, every test) the network is never reached:
`_http` refuses.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import quote, urlsplit

from musicorg.errors import ReplayMissError, UserError

log = logging.getLogger(__name__)

TIMEOUT_S = 8
REPLAY_ENV = "MUSICORG_REPLAY_DIR"
MANIFEST = "/manifest.json"
RESOURCES = ("catalog", "meta", "stream")
INFO_HASH = re.compile(r"[0-9a-fA-F]{40}")
YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")

# The add-ons the app starts with: the film details Stremio itself publishes, its
# channels list, and films whose copyright has run out. The owner adds others by
# their address.
STARTING = (
    "https://v3-cinemeta.strem.io/manifest.json",
    "https://v3-channels.strem.io/manifest.json",
    "https://caching.stremio.net/publicdomainmovies.now.sh/manifest.json",
)


class AddonError(UserError):
    """An add-on said no, gave something unreadable, or couldn't be reached. The
    message is for the owner."""


# ---- the only place the network is reached -----------------------------------------------


def _http(url: str) -> Any:
    """One question to an add-on: its JSON answer."""
    if os.environ.get(REPLAY_ENV):
        raise ReplayMissError("Replay mode: add-ons are never reached. Stand in for addons._http.")
    import requests

    host = urlsplit(url).hostname or "the add-on"
    try:
        response = requests.get(url, timeout=TIMEOUT_S)
    except requests.exceptions.Timeout:
        raise AddonError(f"{host} took too long to answer.") from None
    except requests.exceptions.RequestException:
        raise AddonError(f"{host} couldn't be reached.") from None
    if response.status_code != 200:
        raise AddonError(f"{host} answered with an error (HTTP {response.status_code}).")
    try:
        return response.json()
    except ValueError:
        raise AddonError(f"{host} gave an answer that can't be read.") from None


# ---- the manifest ------------------------------------------------------------------------


def manifest_address(text: str) -> str:
    """The address the owner pasted, as one that can be asked: `stremio://` becomes
    `https://`, and it has to end in `/manifest.json`."""
    address = text.strip()
    if not address:
        raise AddonError("Paste an add-on's address first.")
    if address.lower().startswith("stremio://"):
        address = "https://" + address[len("stremio://") :]
    url = urlsplit(address)
    if url.scheme not in ("http", "https") or not url.hostname:
        raise AddonError("An add-on's address starts with https:// (or stremio://).")
    if not url.path.endswith(MANIFEST):
        raise AddonError("An add-on's address ends in /manifest.json.")
    return address


def base_of(address: str) -> str:
    """Where an add-on's answers are: its manifest's address without the last
    `/manifest.json`. Some add-ons keep settings in the path, so nothing else is cut."""
    plain = address.split("?", 1)[0]
    return plain[: -len(MANIFEST)]


def _texts(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _resources(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """A manifest's resources in one shape. Each is a word ("stream") or an object
    (`{name, types, idPrefixes}`); a word takes the manifest's own types and prefixes."""
    types, prefixes = _texts(raw.get("types")), _texts(raw.get("idPrefixes"))
    found = []
    for entry in raw.get("resources") or []:
        if isinstance(entry, str):
            found.append({"name": entry, "types": types, "id_prefixes": prefixes})
        elif isinstance(entry, dict) and isinstance(entry.get("name"), str):
            found.append(
                {
                    "name": entry["name"],
                    "types": _texts(entry.get("types")) or types,
                    "id_prefixes": _texts(entry.get("idPrefixes")) or prefixes,
                }
            )
    return found


def _catalogs(raw: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    for entry in raw.get("catalogs") or []:
        if not isinstance(entry, dict):
            continue
        kind, catalog_id = entry.get("type"), entry.get("id")
        if not isinstance(kind, str) or not isinstance(catalog_id, str):
            continue
        extra = []
        for one in entry.get("extra") or []:
            if isinstance(one, dict) and isinstance(one.get("name"), str):
                extra.append(
                    {
                        "name": one["name"],
                        "required": one.get("isRequired") is True,
                        "options": _texts(one.get("options")),
                    }
                )
        # Older manifests name what a catalog takes in other fields.
        named = {one["name"] for one in extra}
        for name in _texts(entry.get("extraSupported")):
            if name not in named:
                required = name in _texts(entry.get("extraRequired"))
                extra.append({"name": name, "required": required, "options": []})
        if "genre" not in {one["name"] for one in extra} and _texts(entry.get("genres")):
            extra.append({"name": "genre", "required": False, "options": _texts(entry["genres"])})
        found.append(
            {
                "type": kind,
                "id": catalog_id,
                "name": entry.get("name") if isinstance(entry.get("name"), str) else None,
                "extra": extra,
            }
        )
    return found


def read_manifest(address: str, raw: Any) -> dict[str, Any]:
    """An add-on as the engine keeps it (docs/ENGINE_API.md → Addon), from its
    manifest. It must have an id, a name, a version, resources and types."""
    if not isinstance(raw, dict):
        raise AddonError("That address didn't give an add-on's manifest.")
    for key in ("id", "name", "version"):
        if not isinstance(raw.get(key), str) or not raw[key]:
            raise AddonError(f"That isn't an add-on's manifest: it has no {key}.")
    if not isinstance(raw.get("resources"), list) or not isinstance(raw.get("types"), list):
        raise AddonError("That isn't an add-on's manifest: it doesn't say what it offers.")
    description = raw.get("description")
    return {
        "id": raw["id"],
        "name": raw["name"],
        "version": raw["version"],
        "description": description if isinstance(description, str) else None,
        "address": address,
        "base": base_of(address),
        "types": _texts(raw["types"]),
        "resources": _resources(raw),
        "catalogs": _catalogs(raw),
    }


def load(text: str) -> dict[str, Any]:
    """The add-on at the address the owner pasted."""
    address = manifest_address(text)
    return read_manifest(address, _http(address))


# ---- what an add-on answers --------------------------------------------------------------


def supports(addon: dict[str, Any], resource: str, kind: str, item_id: str | None = None) -> bool:
    """Whether the manifest says this add-on answers `resource` for this type (and,
    given an id, for ids that start like it). Asked before every question."""
    for entry in addon.get("resources") or []:
        if entry.get("name") != resource or kind not in (entry.get("types") or []):
            continue
        prefixes = entry.get("id_prefixes") or []
        if item_id is None or not prefixes or any(item_id.startswith(p) for p in prefixes):
            return True
    return False


def _safe(part: str) -> str:
    return quote(part, safe="")


def build_url(
    base: str, resource: str, kind: str, item_id: str, extra: dict[str, str] | None = None
) -> str:
    """`{base}/{resource}/{type}/{id}[/{key=value&…}].json`, with each part made safe
    for an address."""
    parts = [base.rstrip("/"), _safe(resource), _safe(kind), quote(item_id, safe=":")]
    if extra:
        parts.append("&".join(f"{_safe(key)}={_safe(value)}" for key, value in extra.items()))
    return "/".join(parts) + ".json"


def _catalog_named(addon: dict[str, Any], kind: str, catalog_id: str) -> dict[str, Any]:
    for catalog in addon.get("catalogs") or []:
        if catalog.get("type") == kind and catalog.get("id") == catalog_id:
            return catalog
    raise AddonError(f"{addon.get('name', 'That add-on')} has no such list.")


ALSO_PAGES = 8  # pages of a genre's list read at most for one page of two genres
ALSO_ENOUGH = 24  # films with both genres that make a screenful


def catalog(
    addon: dict[str, Any],
    kind: str,
    catalog_id: str,
    *,
    search: str | None = None,
    genre: str | None = None,
    skip: int = 0,
    also: list[str] | None = None,
) -> dict[str, Any]:
    """One page of a list: `{items: [Item], more, next_skip}`. `search`, `genre` and
    `skip` are sent only when the catalog says it takes them; asking for one it doesn't
    take is the caller's mistake and is refused, not quietly dropped. `next_skip` is the
    `skip` that gives the page after this one.

    `also` (the owner, 2026-10-08: "documentary and crime") keeps only what's tagged
    with every one of those genres as well. An add-on can be asked for one genre at a
    time, so the list for `genre` is read a page after a page (up to `ALSO_PAGES`) and
    what doesn't have the others is left out, until there's a screenful."""
    wanted_too = [g.strip().casefold() for g in also or [] if isinstance(g, str) and g.strip()]
    if wanted_too:
        if not genre:
            raise AddonError("Choose a first genre before adding another.")
        found: list[dict[str, Any]] = []
        cursor, more = skip, True
        for _ in range(ALSO_PAGES):
            page = catalog(addon, kind, catalog_id, search=search, genre=genre, skip=cursor)
            cursor = page["next_skip"]
            found += [
                item
                for item in page["items"]
                if all(g in {have.casefold() for have in item["genres"]} for g in wanted_too)
            ]
            more = page["more"] and bool(page["items"])
            if not more or len(found) >= ALSO_ENOUGH:
                break
        return {"items": found, "more": more, "next_skip": cursor}
    if not supports(addon, "catalog", kind):
        raise AddonError(f"{addon.get('name', 'That add-on')} has no lists of that kind.")
    wanted = _catalog_named(addon, kind, catalog_id)
    takes = {one["name"]: one for one in wanted["extra"]}
    asked = {
        "search": (search or "").strip() or None,
        "genre": genre,
        "skip": str(skip) if skip else None,
    }
    extra = {}
    for name, value in asked.items():
        if value is None:
            if takes.get(name, {}).get("required"):
                raise AddonError(f"That list needs a {name}.")
            continue
        if name not in takes:
            raise AddonError(f"That list can't be asked by {name}.")
        extra[name] = value
    raw = _http(build_url(addon["base"], "catalog", kind, catalog_id, extra))
    metas = raw.get("metas") if isinstance(raw, dict) else None
    if not isinstance(metas, list):
        raise AddonError(f"{addon.get('name', 'The add-on')} gave a list that can't be read.")
    items = [item for item in (_item(meta, kind) for meta in metas) if item is not None]
    # An add-on that says so is believed; otherwise a page with something on it may
    # have another after it, if the list has pages at all.
    said = raw.get("hasMore")
    more = said if isinstance(said, bool) else bool(items) and "skip" in takes
    return {"items": items, "more": more, "next_skip": skip + len(metas)}


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _minutes(runtime: Any) -> int | None:
    found = re.match(r"\s*(\d{1,4})", runtime) if isinstance(runtime, str) else None
    return int(found.group(1)) if found else None


def _year(meta: dict[str, Any]) -> str | None:
    for key in ("releaseInfo", "year"):
        if isinstance(meta.get(key), (str, int)) and str(meta[key]).strip():
            return str(meta[key]).strip()
    released = _text(meta.get("released"))
    return released[:4] if released else None


def _rating(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        return None


def _item(meta: Any, kind: str) -> dict[str, Any] | None:
    """One film or channel as a list shows it (docs/ENGINE_API.md → Item)."""
    if not isinstance(meta, dict) or not _text(meta.get("id")) or not _text(meta.get("name")):
        return None
    return {
        "id": meta["id"],
        "type": _text(meta.get("type")) or kind,
        "name": meta["name"],
        "poster": _text(meta.get("poster")),
        "poster_shape": _text(meta.get("posterShape")) or "poster",
        "year": _year(meta),
        "rating": _rating(meta.get("imdbRating")),
        "genres": _texts(meta.get("genres")) or _texts(meta.get("genre")),
    }


def _video(raw: Any) -> dict[str, Any] | None:
    """One of a channel's videos (or a series' episodes). A channel video's id ends in
    the video's own id, which is what plays it."""
    if not isinstance(raw, dict) or not _text(raw.get("id")):
        return None
    last = raw["id"].rsplit(":", 1)[-1]
    is_channel_video = raw["id"].startswith("yt_id:") and YOUTUBE_ID.fullmatch(last)
    return {
        "id": raw["id"],
        "title": _text(raw.get("title")) or _text(raw.get("name")) or "",
        "thumbnail": _text(raw.get("thumbnailHigh")) or _text(raw.get("thumbnail")),
        "released": _text(raw.get("released")) or _text(raw.get("publishedAt")),
        "season": raw.get("season") if isinstance(raw.get("season"), int) else None,
        "episode": raw.get("episode") if isinstance(raw.get("episode"), int) else None,
        "video_id": last if is_channel_video else None,
    }


def meta(addon: dict[str, Any], kind: str, item_id: str) -> dict[str, Any]:
    """One film's or channel's details (docs/ENGINE_API.md → Details)."""
    if not supports(addon, "meta", kind, item_id):
        raise AddonError(f"{addon.get('name', 'That add-on')} has no details for that.")
    raw = _http(build_url(addon["base"], "meta", kind, item_id))
    found = raw.get("meta") if isinstance(raw, dict) else None
    item = _item(found, kind)
    if item is None or not isinstance(found, dict):
        raise AddonError(f"{addon.get('name', 'The add-on')} has no details for that.")
    trailers = [
        t["ytId"]
        for t in found.get("trailerStreams") or []
        if isinstance(t, dict)
        and isinstance(t.get("ytId"), str)
        and YOUTUBE_ID.fullmatch(t["ytId"])
    ]
    videos = [v for v in (_video(raw) for raw in found.get("videos") or []) if v is not None]
    # Newest first: a channel's videos come oldest first.
    videos.sort(key=lambda v: v["released"] or "", reverse=True)
    return {
        **item,
        "description": _text(found.get("description")),
        "background": _text(found.get("background")),
        "logo": _text(found.get("logo")),
        "runtime_min": _minutes(found.get("runtime")),
        "cast": _texts(found.get("cast")),
        "directors": _texts(found.get("director")),
        "trailer_video_id": trailers[0] if trailers else None,
        "videos": videos,
    }


# ---- streams -----------------------------------------------------------------------------

QUALITY = re.compile(r"\b(4k|2160p|1440p|1080p|720p|480p|360p)\b", re.I)
CAM = re.compile(r"\b(cam|camrip|hdcam|telesync|ts|hdts|telecine|tc)\b", re.I)


def quality_of(*texts: str | None) -> str | None:
    """What a stream says its picture is, from its name: "cam" for one filmed off a
    screen (it says so before any size), or else the size ("1080p", "4k")."""
    words = " ".join(t for t in texts if t)
    if CAM.search(words):
        return "cam"
    found = QUALITY.search(words)
    if found is None:
        return None
    size = found.group(1).lower()
    return "4k" if size == "2160p" else size


def _stream(raw: Any) -> dict[str, Any] | None:
    """One way to play something (docs/ENGINE_API.md → Stream). Which field it has says
    what kind it is; one with none the engine knows is left out."""
    if not isinstance(raw, dict):
        return None
    name, title = _text(raw.get("name")), _text(raw.get("title")) or _text(raw.get("description"))
    stream: dict[str, Any] = {
        "name": name,
        "title": title,
        "quality": quality_of(name, title),
        "url": None,
        "video_id": None,
        "info_hash": None,
        "file_index": None,
        "trackers": [],
    }
    hints = raw.get("behaviorHints") if isinstance(raw.get("behaviorHints"), dict) else {}
    url = _text(raw.get("url"))
    if url and urlsplit(url).scheme in ("http", "https"):
        # One that needs its own headers, or isn't a plain web video, can't be handed
        # to the player as it is.
        plain = not hints.get("notWebReady") and not hints.get("proxyHeaders")
        return {**stream, "kind": "url" if plain else "url_special", "url": url}
    if isinstance(raw.get("ytId"), str) and YOUTUBE_ID.fullmatch(raw["ytId"]):
        return {**stream, "kind": "youtube", "video_id": raw["ytId"]}
    if isinstance(raw.get("infoHash"), str) and INFO_HASH.fullmatch(raw["infoHash"]):
        trackers = [
            source[len("tracker:") :]
            for source in _texts(raw.get("sources"))
            if source.startswith("tracker:")
        ]
        index = raw.get("fileIdx")
        return {
            **stream,
            "kind": "torrent",
            "info_hash": raw["infoHash"].lower(),
            "file_index": index if isinstance(index, int) and not isinstance(index, bool) else None,
            "trackers": trackers,
        }
    return None


def streams(addons: list[dict[str, Any]], kind: str, item_id: str) -> dict[str, Any]:
    """Every way to play one film, from every add-on that offers streams for it:
    `{sources: [{addon_id, addon, streams: [Stream]}], problems: [{addon, message}]}`.
    An add-on that fails is named in `problems` and the rest still answer."""
    sources, problems = [], []
    for addon in addons:
        if not supports(addon, "stream", kind, item_id):
            continue  # it doesn't offer them: nothing is asked
        try:
            raw = _http(build_url(addon["base"], "stream", kind, item_id))
        except AddonError as exc:
            problems.append({"addon": addon["name"], "message": exc.message})
            continue
        given = raw.get("streams") if isinstance(raw, dict) else None
        found = [s for s in (_stream(one) for one in given or []) if s is not None]
        if found:
            sources.append({"addon_id": addon["id"], "addon": addon["name"], "streams": found})
    return {"sources": sources, "problems": problems}


# ---- the owner's list of add-ons ---------------------------------------------------------


def listed() -> list[dict[str, Any]]:
    """The owner's add-ons, in their order. The first time, the app's own few are
    fetched and kept; one of those that can't be reached is left out for now and tried
    again next time, until a list has been saved."""
    from musicorg import config

    saved = config.load_addons()
    if saved is not None:
        return saved
    found = []
    for address in STARTING:
        try:
            found.append(load(address))
        except AddonError as exc:
            log.warning("A starting add-on couldn't be read: %s", exc.message)
    if len(found) == len(STARTING):
        config.save_addons(found)
    return found


def add(text: str) -> list[dict[str, Any]]:
    """Add the add-on at this address to the owner's list (or read it afresh if it's
    there already), and give the list."""
    from musicorg import config

    fresh = load(text)
    current = listed()
    if any(one["id"] == fresh["id"] for one in current):
        current = [fresh if one["id"] == fresh["id"] else one for one in current]
    else:
        current.append(fresh)
    config.save_addons(current)
    return current


def remove(addon_id: str) -> list[dict[str, Any]]:
    from musicorg import config

    current = [one for one in listed() if one["id"] != addon_id]
    config.save_addons(current)
    return current


def reorder(addon_ids: list[str]) -> list[dict[str, Any]]:
    """Put the owner's add-ons in this order (the first one that has a film's details
    is the one asked for them). The ids must be exactly the ones in the list."""
    from musicorg import config

    current = {one["id"]: one for one in listed()}
    if sorted(addon_ids) != sorted(current):
        raise AddonError("The list of add-ons has changed. Look at it again and try once more.")
    ordered = [current[addon_id] for addon_id in addon_ids]
    config.save_addons(ordered)
    return ordered


def restore() -> list[dict[str, Any]]:
    """Put back whichever of the app's own starting add-ons isn't in the owner's list,
    after the ones that are. One that can't be reached says so."""
    from musicorg import config

    current = listed()
    have = {base_of(one["address"]) for one in current}
    missing = [address for address in STARTING if base_of(manifest_address(address)) not in have]
    if not missing:
        return current
    current = current + [load(address) for address in missing]
    config.save_addons(current)
    return current


def named(addon_id: str) -> dict[str, Any]:
    for addon in listed():
        if addon["id"] == addon_id:
            return addon
    raise AddonError("That add-on isn't in your list any more.")


def details(kind: str, item_id: str, addon_id: str | None = None) -> dict[str, Any]:
    """One film's or channel's details, from the add-on named or else the first in the
    list that has details for it."""
    if addon_id is not None:
        return meta(named(addon_id), kind, item_id)
    problem: AddonError | None = None
    for addon in listed():
        if supports(addon, "meta", kind, item_id):
            try:
                return meta(addon, kind, item_id)
            except AddonError as exc:
                problem = exc
    raise problem or AddonError("None of your add-ons has details for that.")
