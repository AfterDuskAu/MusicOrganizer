"""Move one profile's library folder into the Mac's Music folder (or anywhere else on the
same disk), and tell the app where it went.

Usage, with Music Organizer quit first (from the repo root, with the engine's virtual
environment):

    .venv/bin/python scripts/move_library.py "C"
    .venv/bin/python scripts/move_library.py "C" --dry-run
    .venv/bin/python scripts/move_library.py "Kids" --to "/some/other/folder/Music Kids"

Without `--to`, the library goes to `Music <profile's name>` in this Mac's Music folder
("Music C"), which is where a new profile's library is made.

A library is one folder that holds everything about its music: the songs, videos, lyrics
and covers, and its own records (`.musicorg`: the index, the queue, playlists, play
counts, the journal). So it moves whole, as a rename: nothing inside it is read or
changed, and on the same disk it's instant. Only this profile's library is touched;
every other profile's stays exactly where it is.

Then the three places that remember where it was are told the new place:

- the app's list of profiles (its saved settings), and its older single-library entry;
- playlists waiting to be copied from this profile to another (they name its folder);
- the engine's "last library", if it was this one.

It refuses, and changes nothing, if Music Organizer is running, if the library is open
in an engine, if the new folder already exists, or if the new place is on another disk
(that would be a slow copy, not a move: do it by hand, then choose the folder under
Settings → Downloads → Library folder). If the app's settings can't be updated after the
folder has moved, the folder is moved back.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

APP_DOMAIN = "org.musicorganizer.app"
APP_PROCESS = "MusicOrganizer"
RECORDS = ".musicorg"


class Refused(Exception):
    """Why nothing was moved, in plain words."""


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("profile", help="The profile's name, as Settings → Profile shows it.")
    parser.add_argument("--to", type=Path, help="The new folder (default: Music/Music <name>).")
    parser.add_argument("--dry-run", action="store_true", help="Say what would happen; do nothing.")
    parser.add_argument("--domain", default=APP_DOMAIN, help=argparse.SUPPRESS)  # for testing
    opts = parser.parse_args()
    if sys.platform != "darwin":
        print("This moves a library for the Mac app: it only runs on a Mac.")
        return 1
    try:
        return move(opts.profile, opts.to, dry_run=opts.dry_run, domain=opts.domain)
    except Refused as why:
        print(f"Nothing was moved. {why}")
        return 1


def move(name: str, to: Path | None, *, dry_run: bool, domain: str) -> int:
    settings = read_settings(domain)
    listed = saved_profiles(settings)
    wanted = [p for p in listed.get("profiles", []) if same_name(p.get("name"), name)]
    if not wanted:
        names = ", ".join(str(p.get("name")) for p in listed.get("profiles", [])) or "none"
        raise Refused(f"There's no profile called {name!r}. The profiles are: {names}.")
    profile = wanted[0]
    if not profile.get("libraryRoot"):
        raise Refused(f"{profile['name']} has no library folder yet, so there's nothing to move.")
    old = Path(profile["libraryRoot"])
    new = (to or Path.home() / "Music" / f"Music {profile['name']}").expanduser()
    if not new.is_absolute():
        raise Refused("Give the new folder as a full path.")

    if domain == APP_DOMAIN and app_is_running():
        raise Refused("Music Organizer is open. Quit it first (⌘Q), then run this again.")
    if not old.is_dir() or not (old / RECORDS).is_dir():
        raise Refused(f"{profile['name']}'s library isn't at {old}.")
    holder = lock_holder(old)
    if holder is not None:
        raise Refused(f"That library is open in an engine right now (process {holder}).")
    if same_folder(old, new):
        raise Refused(f"{profile['name']}'s library is already at {new}.")
    if new.exists():
        raise Refused(f"There's already something at {new}.")
    if not new.parent.is_dir():
        raise Refused(f"The folder {new.parent} isn't there.")
    if os.stat(old).st_dev != os.stat(new.parent).st_dev:
        raise Refused(
            f"{new.parent} is on another disk. That would be a copy, not a move: do it by "
            "hand, then choose the folder under Settings → Downloads → Library folder."
        )
    for other in listed.get("profiles", []):
        root = other.get("libraryRoot")
        if other is not profile and root and same_folder(Path(root), new):
            raise Refused(f"{new} is {other.get('name')}'s library.")

    print(f"{profile['name']}'s library: {old}")
    print(f"                 goes to: {new}")
    if dry_run:
        print("(A dry run: nothing was moved.)")
        return 0

    os.rename(old, new)
    try:
        update_settings(domain, settings, listed, profile, old, new)
    except Exception as exc:
        os.rename(new, old)  # the app would look for it where it was
        raise Refused(
            f"The app's settings couldn't be updated ({exc}), so the folder was put back."
        ) from exc
    update_engine(old, new)
    print("Moved. Open Music Organizer: everything is as it was, from the new folder.")
    return 0


# ---- what the app has saved --------------------------------------------------------------


def read_settings(domain: str) -> dict[str, Any]:
    found = subprocess.run(["defaults", "export", domain, "-"], capture_output=True, check=False)
    if found.returncode != 0 or not found.stdout:
        raise Refused("Music Organizer's saved settings couldn't be read. Has it been opened yet?")
    return plistlib.loads(found.stdout)


def saved_profiles(settings: dict[str, Any]) -> dict[str, Any]:
    raw = settings.get("profiles")
    if not isinstance(raw, bytes):
        raise Refused("Music Organizer has no profiles saved yet: open it once first.")
    listed = json.loads(raw)
    if not isinstance(listed, dict) or not isinstance(listed.get("profiles"), list):
        raise Refused("Music Organizer's list of profiles couldn't be read.")
    return listed


def update_settings(
    domain: str,
    settings: dict[str, Any],
    listed: dict[str, Any],
    profile: dict[str, Any],
    old: Path,
    new: Path,
) -> None:
    profile["libraryRoot"] = str(new)
    write_data(domain, "profiles", listed)
    # From before there were profiles: the one library the app opened.
    single = settings.get("libraryRoot")
    if isinstance(single, str) and same_folder(Path(single), old):
        subprocess.run(
            ["defaults", "write", domain, "libraryRoot", "-string", str(new)], check=True
        )
    # Playlists this profile sent to another, not yet copied: they name its folder.
    waiting = settings.get("pendingShares")
    if isinstance(waiting, bytes):
        shares = json.loads(waiting)
        changed = False
        for sent in shares.values() if isinstance(shares, dict) else []:
            for share in sent if isinstance(sent, list) else []:
                root = share.get("sourceRoot") if isinstance(share, dict) else None
                if isinstance(root, str) and same_folder(Path(root), old):
                    share["sourceRoot"] = str(new)
                    changed = True
        if changed:
            write_data(domain, "pendingShares", shares)


def write_data(domain: str, key: str, value: Any) -> None:
    """Save a value the app keeps as JSON data."""
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    subprocess.run(["defaults", "write", domain, key, "-data", encoded.hex()], check=True)


def update_engine(old: Path, new: Path) -> None:
    """The engine's "last library" (what a command uses when none is named)."""
    try:
        from musicorg.config import Config
    except ImportError:
        print("(The engine's own setting wasn't looked at: run this with .venv/bin/python.)")
        return
    config = Config.load()
    last = config.last_library
    if last is not None and same_folder(last, old):
        config.last_library = new
        config.save()


# ---- checks ------------------------------------------------------------------------------


def same_name(a: Any, b: str) -> bool:
    return isinstance(a, str) and a.strip().casefold() == b.strip().casefold()


def same_folder(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b)) or (
        str(a).casefold() == str(b).casefold()
    )


def app_is_running() -> bool:
    return (
        subprocess.run(["pgrep", "-x", APP_PROCESS], capture_output=True, check=False).returncode
        == 0
    )


def lock_holder(library: Path) -> int | None:
    """The engine process that has this library open, if one does."""
    try:
        info = json.loads((library / RECORDS / "lock.info").read_text(encoding="utf-8"))
        pid = int(info["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None  # a note left by an engine that has gone
    except PermissionError:
        return pid
    command = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=False
    ).stdout
    return pid if "musicorg" in command else None


if __name__ == "__main__":
    raise SystemExit(main())
