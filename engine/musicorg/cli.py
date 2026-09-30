"""The `musicorg` command. Argument parsing and printing only; the work happens in the
other musicorg modules, which the JSON-RPC server (step 11) calls too.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import traceback
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

from musicorg import (
    __version__,
    doctor,
    fileops,
    library,
    logging_setup,
    match,
    pipeline,
    queue,
    report,
    review,
    scan,
    status,
)
from musicorg.config import Config
from musicorg.errors import (
    EXIT_INTERNAL,
    EXIT_OK,
    EXIT_TOOL_MISSING,
    EXIT_USER_ERROR,
    EXIT_YOUTUBE_BLOCKED,
    ConfigError,
    MusicOrgError,
    NotImplementedYetError,
    UserError,
)
from musicorg.index import open_index

log = logging.getLogger(__name__)

Handler = Callable[[argparse.Namespace], int]


class _Parser(argparse.ArgumentParser):
    """argparse normally exits with code 2 on bad arguments, but 2 means "library locked"
    here, so argument mistakes become an ordinary user error (exit code 1)."""

    def error(self, message: str) -> NoReturn:
        raise UserError(f"{message}\nRun `{self.prog} --help` to see what it accepts.")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `musicorg` command. Returns the exit code."""
    _use_utf8_output()
    arg_list = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        args = parser.parse_args(arg_list)
    except SystemExit as exc:  # --help and --version
        return exc.code if isinstance(exc.code, int) else EXIT_OK
    except MusicOrgError as exc:
        return _report_error(exc, json_mode="--json" in arg_list)

    if getattr(args, "handler", None) is None:
        parser.print_help(sys.stderr)
        return EXIT_USER_ERROR

    logging_setup.setup_logging(verbose=args.verbose)
    try:
        log.info("musicorg %s: %s", __version__, " ".join(arg_list))
        return args.handler(args)
    except MusicOrgError as exc:
        log.info("%s: %s", type(exc).__name__, exc.message)
        return _report_error(exc, json_mode=args.json)
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return EXIT_USER_ERROR
    except Exception as exc:
        return _report_internal_error(exc, json_mode=args.json)
    finally:
        logging_setup.shutdown_logging()


def build_parser() -> argparse.ArgumentParser:
    common = _global_options(for_subcommand=True)
    parser = _Parser(
        prog="musicorg",
        description="Music Organizer engine: scans, matches, downloads, tags and protects "
        "your music library.",
        parents=[_global_options(for_subcommand=False)],
    )
    parser.add_argument("--version", action="version", version=f"musicorg {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", parser_class=_Parser)

    def add(group: Any, name: str, help_text: str, handler: Handler) -> argparse.ArgumentParser:
        p = group.add_parser(name, help=help_text, description=help_text, parents=[common])
        p.set_defaults(handler=handler)
        return p

    def group(name: str, help_text: str) -> Any:
        p = commands.add_parser(name, help=help_text, description=help_text, parents=[common])
        return p.add_subparsers(
            dest=f"{name}_command", metavar="COMMAND", required=True, parser_class=_Parser
        )

    p = add(
        commands,
        "init",
        "Create a new library, in a new folder or one with no music in it.",
        _cmd_init,
    )
    p.add_argument("root", type=_path)

    add(commands, "status", "Show the library, item counts, queue state and warnings.", _cmd_status)

    p = add(
        commands,
        "doctor",
        "Check that the tools and folders the engine needs are in place.",
        _cmd_doctor,
    )
    p.add_argument(
        "--update-ytdlp",
        action="store_true",
        help="Update yt-dlp, remembering the current version for --rollback-ytdlp.",
    )
    p.add_argument(
        "--rollback-ytdlp",
        action="store_true",
        help="Go back to the yt-dlp version from before the last update.",
    )

    sources = group("sources", "Manage read-only source folders (your existing rips).")
    p = add(sources, "add", "Register a folder as a read-only source.", _cmd_sources_add)
    p.add_argument("path", type=_path)
    add(sources, "list", "List registered sources.", _cmd_sources_list)
    p = add(sources, "remove", "Forget a source. Its files are never touched.", _cmd_sources_remove)
    p.add_argument("source_id")

    p = add(commands, "scan", "Index sources, read-only.", _cmd_scan)
    p.add_argument("source_ids", nargs="*", metavar="SOURCE_ID")

    index = group("index", "The library's search index.")
    add(index, "rebuild", "Rebuild the index from the files and state.json.", _cmd_index_rebuild)

    p = add(
        commands,
        "match",
        "Search YouTube Music for new items and score the candidates.",
        _cmd_match,
    )
    p.add_argument("--limit", type=int, metavar="N", help="Match at most N items.")
    p.add_argument(
        "--rescan",
        action="store_true",
        help="Match review and not-found items again, skipping the search cache.",
    )
    p.add_argument(
        "--recheck",
        action="store_true",
        help="Classify review and not-found items again from the results already found "
        "(no searching), e.g. after the rules improve.",
    )

    p = add(
        commands,
        "report",
        "Write the decision report (markdown and CSV).",
        _cmd_report,
    )
    p.add_argument(
        "--out", type=_path, metavar="DIR", help="Folder for the report (default: Reports/)."
    )

    review = group("review", "Review uncertain matches in a spreadsheet.")
    p = add(review, "export", "Write the review CSV.", _cmd_review_export)
    p.add_argument("csv", type=_path)
    p.add_argument("--include-auto", action="store_true", help="Also list the automatic matches.")
    p = add(review, "import", "Apply the decisions from a review CSV.", _cmd_review_import)
    p.add_argument("csv", type=_path)
    p = add(
        review,
        "serve",
        "Open the review page in your browser: hear each rip and its matches, click to decide.",
        _cmd_review_serve,
    )
    p.add_argument("--port", type=int, default=0, metavar="N", help="Port (default: any free one).")
    p.add_argument("--no-open", action="store_true", help="Don't open the browser.")

    journal = group("journal", "The log of every change made to the library.")
    p = add(journal, "list", "Recent batches of changes.", _cmd_journal_list)
    p.add_argument(
        "--limit", type=int, default=20, metavar="N", help="How many batches (default 20)."
    )

    p = add(commands, "undo", "Reverse a batch of changes.", _cmd_undo)
    p.add_argument("batch_id")
    p.add_argument(
        "--dry-run", action="store_true", help="Only show what would be undone; change nothing."
    )

    plan = group("plan", "Make a dry-run plan of changes.")
    p = add(plan, "replace", "Plan replacing rips with official downloads.", _cmd_plan_replace)
    p.add_argument(
        "--only",
        choices=list(pipeline.ONLY),
        default="all-eligible",
        help="auto: AUTO matches; accepted: your review choices; all-eligible: both (default).",
    )
    p.add_argument("--limit", type=int, metavar="N", help="At most N videos.")
    p.add_argument(
        "--stage-only",
        action="store_true",
        help="Calibration: download and compare, keep the downloads in _Staging/calibration, "
        "and change nothing else.",
    )
    p = add(plan, "adopt", "Plan copying only-copy rips into the library.", _cmd_plan_adopt)
    p.add_argument(
        "--include-not-found", action="store_true", help="Also copy in every not-found rip."
    )
    p = add(plan, "show", "Show a plan's operations and summary.", _cmd_plan_show)
    p.add_argument("plan_id")
    p = add(
        plan,
        "calibration",
        "Write Reports/calibration-pairs.csv from the --stage-only downloads.",
        _cmd_plan_calibration,
    )
    p.add_argument("--out", type=_path, metavar="DIR", help="Folder (default: Reports/).")

    p = add(commands, "apply", "Check a plan and queue its jobs.", _cmd_apply)
    p.add_argument("plan_id")

    queue_group = group("queue", "The throttled download queue.")
    add(
        queue_group,
        "run",
        "Work through the queue until it's empty or paused.",
        _cmd_queue_run,
    )
    add(queue_group, "status", "Queue state and counts.", _cmd_queue_status)
    add(queue_group, "pause", "Pause the queue after the current job.", _cmd_queue_pause)
    add(queue_group, "resume", "Resume the queue.", _cmd_queue_resume)

    p = add(commands, "lyrics", "Plan adding lyrics.", _not_yet("lyrics", "10"))
    p.add_argument("--missing", action="store_true")
    p = add(commands, "artwork", "Plan adding cover art.", _not_yet("artwork", "10"))
    p.add_argument("--missing", action="store_true")

    add(
        commands,
        "serve",
        "Run the JSON-RPC server on stdin/stdout for the app.",
        _not_yet("serve", "11"),
    )
    return parser


# ---- command handlers ----------------------------------------------------------------

# What each top-level folder is for, as `init` shows it.
_LAYOUT = (
    ("Music/", "your organised, tagged music"),
    ("_Replaced/", "files replaced by upgrades or undos; never deleted automatically"),
    ("_Staging/", "downloads and tag changes in progress"),
    ("Reports/", "reports and spreadsheets"),
    (".musicorg/", "the engine's own records (hidden)"),
)


def _cmd_init(args: argparse.Namespace) -> int:
    result = library.init(args.root)
    if args.json:
        _print_json(result.to_dict())
        return EXIT_OK

    if result.already_library:
        print(f"{result.root} is already a Music Organizer library.")
        if result.created:
            print("Added the missing folders:")
            for folder in result.created:
                print(f"  {folder}")
        else:
            print("Nothing needed creating.")
    else:
        print(f"Created a new library at {result.root}")
        print()
        width = max(len(name) for name, _ in _LAYOUT) + 2
        for name, purpose in _LAYOUT:
            print(f"  {name:<{width}}{purpose}")
    _print_warnings(result.warnings)
    return EXIT_OK


def _cmd_status(args: argparse.Namespace) -> int:
    root = args.library if args.library is not None else Config.load().last_library
    result = status.get_status(root)
    if args.json:
        _print_json(result)
        return EXIT_OK

    print(f"Music Organizer engine {result['engine_version']}")
    if root is None:
        print(
            "Library: none chosen yet. Pass --library <folder>, "
            "or create one with `musicorg init <folder>`."
        )
    elif not result["library_exists"]:
        print(f"Library: {root} (this folder doesn't exist)")
    elif not result["is_library"]:
        print(f"Library: {root} (this folder isn't a Music Organizer library yet)")
    else:
        print(f"Library: {root}")
        _print_counts(result)
        if result["queue"] is not None:
            _print_queue_status(result["queue"], json_mode=False)
    _print_warnings(result["warnings"])
    return EXIT_OK


def _print_counts(result: dict[str, Any]) -> None:
    items, sources = result["items"], result["sources"]
    print(f"Sources: {sources}. Library tracks: {result['tracks']:,}.")
    if not items:
        hint = "run `musicorg scan`" if sources else "add one with `musicorg sources add`"
        print(f"Items: none indexed yet ({hint}).")
        return
    print(f"Items: {items:,}")
    for name, count in result["items_by_state"].items():
        print(f"  {name:<20}{count:>8,}  {count / items:6.1%}")
    low = result["low_confidence"]
    print(f"Low parse confidence (below 0.5): {low:,} ({low / items:.1%})")


def _cmd_doctor(args: argparse.Namespace) -> int:
    if args.update_ytdlp and args.rollback_ytdlp:
        raise UserError("Choose one: --update-ytdlp or --rollback-ytdlp.")
    if args.update_ytdlp or args.rollback_ytdlp:
        return _update_ytdlp(args)

    cfg: Config | None
    try:
        cfg, config_problem = Config.load(), None
    except ConfigError as exc:
        cfg, config_problem = None, exc.message
    checks = doctor.run_checks(cfg, config_problem)
    failed = [c for c in checks if not c.ok]
    if any(c.kind == "tool" for c in failed):
        code = EXIT_TOOL_MISSING
    elif failed:
        code = EXIT_USER_ERROR
    else:
        code = EXIT_OK

    if args.json:
        _print_json(
            {
                "engine_version": __version__,
                "platform": doctor.describe_platform(),
                "ok": not failed,
                "checks": [c.to_dict() for c in checks],
            }
        )
        return code

    print(f"Music Organizer doctor (engine {__version__}, {doctor.describe_platform()})")
    print()
    for c in checks:
        mark = "✓" if c.ok else "✗"
        print(f"  {mark} {c.name}: {c.detail}")
        if c.fix:
            print(f"      Fix: {c.fix}")
    print()
    if failed:
        problems = "1 problem" if len(failed) == 1 else f"{len(failed)} problems"
        print(f"{problems} found. The fixes are listed above.")
    else:
        print("Everything is in place.")
    return code


def _update_ytdlp(args: argparse.Namespace) -> int:
    cfg = Config.load()
    root = args.library if args.library is not None else cfg.last_library
    if args.update_ytdlp:
        print("Updating yt-dlp (this can take a minute)...", file=sys.stderr, flush=True)
        result = doctor.update_ytdlp(root)
    else:
        print("Going back to the earlier yt-dlp...", file=sys.stderr, flush=True)
        result = doctor.rollback_ytdlp(cfg, root)
    if args.json:
        _print_json(result.to_dict())
        return EXIT_OK
    for dist in doctor.YTDLP_PACKAGES:
        old, new = result.before.get(dist), result.after.get(dist)
        change = f"{old} → {new}" if old != new else f"{new} (unchanged)"
        print(f"  {dist}: {change}")
    if args.update_ytdlp:
        print("If downloads stop working, `musicorg doctor --rollback-ytdlp` goes back.")
    return EXIT_OK


def _cmd_sources_add(args: argparse.Namespace) -> int:
    root = _library_root(args)
    with library.open(root, write=True) as lib, open_index(lib.paths, write=True) as index:
        source = scan.add_source(lib, index, args.path)
    if args.json:
        _print_json(source)
    else:
        print(f"Added the source {source['id']}: {source['path']}")
        print("Its files are only ever read. Index them with `musicorg scan`.")
    return EXIT_OK


def _cmd_sources_list(args: argparse.Namespace) -> int:
    with library.open(_library_root(args), write=False) as lib:
        with open_index(lib.paths, write=False) as index:
            sources = scan.list_sources(lib, index)
    if args.json:
        _print_json({"sources": sources})
        return EXIT_OK
    if not sources:
        print("No sources yet. Add your rips folder with `musicorg sources add <folder>`.")
    for source in sources:
        scanned = _local_time(source["scanned_at"]) if source["scanned_at"] else "never"
        missing = "" if source["available"] else "  (not there: is its drive connected?)"
        print(f"{source['id']}  {source['path']}{missing}")
        print(f"    {source['items']:,} items, last scanned {scanned}")
    return EXIT_OK


def _cmd_sources_remove(args: argparse.Namespace) -> int:
    with library.open(_library_root(args), write=True) as lib:
        with open_index(lib.paths, write=True) as index:
            removed = scan.remove_source(lib, index, args.source_id)
    if args.json:
        _print_json(removed)
    else:
        print(f"Forgot the source {removed['id']} ({removed['path']}).")
        print(f"{removed['items_forgotten']:,} items left the index. No files were touched.")
    return EXIT_OK


def _cmd_scan(args: argparse.Namespace) -> int:
    progress = None if args.json else _Progress("files read")
    with library.open(_library_root(args), write=True) as lib:
        with open_index(lib.paths, write=True) as index:
            result = scan.scan(lib, index, source_ids=args.source_ids or None, progress=progress)
    if args.json:
        _print_json(result.to_dict())
        return EXIT_OK
    _print_scan(result)
    return EXIT_OK


def _cmd_index_rebuild(args: argparse.Namespace) -> int:
    progress = None if args.json else _Progress("files read")
    with library.open(_library_root(args), write=True) as lib:
        with open_index(lib.paths, write=True) as index:
            result = scan.rebuild(lib, index, progress=progress)
    if args.json:
        _print_json(result.to_dict())
        return EXIT_OK
    print(f"Rebuilt the index: {result.library_tracks:,} library tracks.")
    _print_scan(result.scan)
    return EXIT_OK


def _print_scan(result: scan.ScanResult) -> None:
    print(
        f"Scanned {result.files:,} audio files in {len(result.sources)} source(s) "
        f"in {result.seconds:.0f} s: {result.new:,} new, {result.changed:,} changed, "
        f"{result.unchanged:,} unchanged, {result.gone:,} gone."
    )
    if result.unreadable:
        print(f"  {result.unreadable:,} files had audio that couldn't be read (flagged).")
    if result.skipped_links:
        print(f"  {result.skipped_links:,} links were skipped (never followed).")
    for folder in result.skipped_libraries:
        print(f"  Skipped a library inside a source: {folder}")
    for folder in result.unreadable_folders:
        print(f"  ! Couldn't read the folder {folder or '(the source itself)'}; see the log.")
    for source_id in result.missing_sources:
        print(f"  ! The source {source_id} isn't there (is its drive connected?). Its items stay.")


class _Progress:
    """Progress on stderr (stdout stays clean), at most every 2 seconds."""

    def __init__(self, what: str) -> None:
        self.what = what
        self.last = 0.0

    def __call__(self, done: int, total: int, current: str) -> None:
        now = time.monotonic()
        if done != total and now - self.last < 2:
            return
        self.last = now
        print(f"  {done:,} of {total:,} {self.what}", file=sys.stderr, flush=True)


def _cmd_match(args: argparse.Namespace) -> int:
    if args.recheck:
        if args.rescan or args.limit is not None:
            raise UserError("--recheck doesn't search, so it takes neither --rescan nor --limit.")
        with library.open(_library_root(args), write=True) as lib:
            with open_index(lib.paths, write=True) as index:
                checked = match.recheck(lib, index)
        if args.json:
            _print_json(checked.to_dict())
            return EXIT_OK
        print(f"Re-checked {checked.items:,} review and not-found items (no searching).")
        for change, n in sorted(checked.changed.items()):
            print(f"  {n:>6,}  {change}")
        if not checked.changed:
            print("  Nothing changed.")
        return EXIT_OK
    if args.limit is not None and args.limit < 1:
        raise UserError("--limit must be 1 or more.")
    progress = None if args.json else _MatchProgress()
    with library.open(_library_root(args), write=True) as lib:
        with open_index(lib.paths, write=True) as index:
            result = match.run(lib, index, limit=args.limit, rescan=args.rescan, progress=progress)
    if args.json:
        _print_json(result.to_dict())
        return EXIT_OK
    if not result.items:
        what = "new, review or not-found" if args.rescan else "new"
        print(f"No {what} items to match. Scan a source first with `musicorg scan`.")
    else:
        minutes = result.seconds / 60
        took = f"{minutes:.0f} min" if minutes >= 1 else f"{result.seconds:.0f} s"
        print(
            f"Matched {result.items:,} items in {took}: {result.matched_auto:,} automatic, "
            f"{result.review:,} to review, {result.not_found:,} not found "
            f"({result.searches:,} searches; the rest came from the cache)."
        )
    if result.left:
        print(f"{result.left:,} more items are waiting; run `musicorg match` again to carry on.")
    if result.sample is not None:
        print(f"Listen to {result.sample_size} of the automatic matches: {result.sample}")
    return EXIT_OK


def _cmd_report(args: argparse.Namespace) -> int:
    with library.open(_library_root(args), write=False) as lib:
        with open_index(lib.paths, write=False) as index:
            files = report.write(lib, index, out_dir=args.out)
    if args.json:
        _print_json({"markdown": str(files.markdown), "csv": str(files.csv),
                     **files.report.to_dict()})  # fmt: skip
        return EXIT_OK
    data = files.report
    print(f"Report on {data.total:,} items:")
    for name in report.ITEM_STATES:
        n = data.states.get(name, 0)
        if n:
            print(f"  {name:<20}{n:>8,}  {data.share(n):6.1%}")
    print()
    for line in data.recommendations:
        print(line)
    print()
    print(f"Written: {files.markdown}")
    print(f"         {files.csv}")
    return EXIT_OK


def _cmd_review_export(args: argparse.Namespace) -> int:
    with library.open(_library_root(args), write=False) as lib:
        with open_index(lib.paths, write=False) as index:
            result = review.export(lib, index, args.csv, include_auto=args.include_auto)
    if args.json:
        _print_json(result.to_dict())
        return EXIT_OK
    print(f"Wrote {result.rows:,} items to review: {result.path}")
    print(
        "Fill in the decision column (accept, cand:2, cand:3, url, only_copy, skip or "
        "reject:1–3), save as CSV UTF-8, then run `musicorg review import` on it."
    )
    return EXIT_OK


def _cmd_review_import(args: argparse.Namespace) -> int:
    with library.open(_library_root(args), write=True) as lib:
        with open_index(lib.paths, write=True) as index:
            result = review.import_csv(lib, index, args.csv)
    if args.json:
        _print_json(result.to_dict())
        return EXIT_OK
    applied = sum(result.applied.values())
    kinds = ", ".join(f"{n:,} {kind}" for kind, n in sorted(result.applied.items()))
    print(f"Applied {applied:,} decisions" + (f": {kinds}." if kinds else "."))
    if result.unchanged:
        print(f"{result.unchanged:,} were already decided that way (nothing changed).")
    if result.blank:
        print(f"{result.blank:,} rows had no decision and were left as they are.")
    for warning in result.warnings:
        print(f"  ! {warning}")
    return EXIT_OK


def _cmd_review_serve(args: argparse.Namespace) -> int:
    from musicorg import review_web

    def ready(url: str) -> None:
        print("The review page is open at:")
        print(f"  {url}")
        print("Keep this window open while you review; press Ctrl-C here to finish.", flush=True)

    with library.open(_library_root(args), write=True, command="review serve") as lib:
        try:
            review_web.serve(lib, port=args.port, open_browser=not args.no_open, ready=ready)
        except KeyboardInterrupt:
            pass
        except OSError as exc:
            raise UserError(f"Couldn't start the review page: {exc.strerror or exc}.") from exc
    print("Review page closed. Your decisions are saved.")
    return EXIT_OK


class _MatchProgress:
    """Items matched, with an estimate of the time left, on stderr every 5 seconds."""

    def __init__(self) -> None:
        self.last = 0.0

    def __call__(self, done: int, total: int, seconds_left: float | None) -> None:
        now = time.monotonic()
        if done != total and now - self.last < 5:
            return
        self.last = now
        left = ""
        if seconds_left and done != total:
            left = f", about {max(1, round(seconds_left / 60))} min left"
        print(f"  {done:,} of {total:,} items{left}", file=sys.stderr, flush=True)


def _cmd_queue_run(args: argparse.Namespace) -> int:
    def say(message: str) -> None:
        print(f"  {message}", file=sys.stderr, flush=True)

    with library.open(_library_root(args), write=True, command="queue run") as lib:
        print("Working through the queue. Ctrl-C stops after the current job.", file=sys.stderr)
        with queue.graceful_ctrl_c() as should_stop:
            result = queue.run(lib, should_stop=should_stop, report=say)
    if args.json:
        _print_json(result.to_dict())
    else:
        ended = ", ".join(f"{n} {state.replace('_', ' ')}" for state, n in result.counts.items())
        if ended:
            print(f"Jobs this run: {ended}.")
        print(result.message)
    return EXIT_YOUTUBE_BLOCKED if result.stopped == "paused_by_youtube" else EXIT_OK


def _cmd_queue_status(args: argparse.Namespace) -> int:
    with library.open(_library_root(args), write=False) as lib:
        result = queue.status(lib.paths)
    _print_queue_status(result, args.json)
    return EXIT_OK


def _cmd_queue_pause(args: argparse.Namespace) -> int:
    with library.open(_library_root(args), write=False) as lib:
        result = queue.pause(lib.paths)
    if not args.json:
        print("Paused. A running `queue run` stops after its current job.")
    _print_queue_status(result, args.json)
    return EXIT_OK


def _cmd_queue_resume(args: argparse.Namespace) -> int:
    with library.open(_library_root(args), write=False) as lib:
        result = queue.resume(lib.paths)
    if not args.json:
        print("Resumed. `musicorg queue run` works through what's queued.")
    _print_queue_status(result, args.json)
    return EXIT_OK


def _print_queue_status(result: dict[str, Any], json_mode: bool) -> None:
    if json_mode:
        _print_json(result)
        return
    words = {
        "running": "running",
        "idle": "idle",
        "paused": "paused (by you)",
        "paused_by_youtube": "paused by YouTube",
    }
    line = f"Queue: {words.get(result['state'], result['state'])}"
    if result.get("resume_at"):
        line += f" until {_local_time(result['resume_at'])}"
    print(line + (f". {result['reason']}" if result["state"] == "paused_by_youtube" else "."))
    counts = ", ".join(f"{result[s]:,} {s.replace('_', ' ')}" for s in queue.JOB_STATES)
    print(f"Jobs: {counts}.")
    print(f"Downloads in the last 24 hours: {result['daily_count']} of {result['daily_cap']}.")


# How `journal list` describes each operation (docs/ENGINE_API.md → Journal operation).
_OPERATION_WORDS = {
    "commit": "added",
    "copy_in": "copied in",
    "supersede": "moved to _Replaced",
    "restore": "restored",
    "move": "moved",
    "trash": "sent to the Trash",
    "write_tags": "retagged",
    "write_sidecar": "sidecars written",
}


def _cmd_journal_list(args: argparse.Namespace) -> int:
    with library.open(_library_root(args), write=False) as lib:
        batches = fileops.list_batches(lib, limit=args.limit)
    if args.json:
        _print_json({"batches": [b.to_dict() for b in batches]})
        return EXIT_OK
    if not batches:
        print("No changes have been made to this library yet.")
        return EXIT_OK

    rows = [("BATCH", "STARTED", "KIND", "STATUS", "CHANGES")]
    for b in batches:
        changes = [f"{n} {_OPERATION_WORDS.get(op, op)}" for op, n in b.operations.items()]
        if b.failed:
            changes.append(f"{b.failed} failed")
        if b.pending:
            changes.append(f"{b.pending} interrupted")
        status = b.status
        if b.undo_of:
            status += f", undo of {b.undo_of}"
        if b.undone_by:
            status += f", undone by {', '.join(b.undone_by)}"
        rows.append(
            (b.batch_id, _local_time(b.started_at), b.kind, status, ", ".join(changes) or "nothing")
        )
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    for row in rows:
        print(
            "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=False))
            + "  "
            + row[4]
        )
    return EXIT_OK


def _cmd_plan_replace(args: argparse.Namespace) -> int:
    with library.open(_library_root(args), write=True, command="plan replace") as lib:
        with open_index(lib.paths, write=False) as index:
            plan = pipeline.plan_replace(
                lib, index, only=args.only, limit=args.limit, stage_only=args.stage_only
            )
    return _print_new_plan(plan, args.json)


def _cmd_plan_adopt(args: argparse.Namespace) -> int:
    with library.open(_library_root(args), write=True, command="plan adopt") as lib:
        with open_index(lib.paths, write=False) as index:
            plan = pipeline.plan_adopt(lib, index, include_not_found=args.include_not_found)
    return _print_new_plan(plan, args.json)


def _cmd_plan_show(args: argparse.Namespace) -> int:
    with library.open(_library_root(args), write=False) as lib:
        plan = pipeline.show(lib, args.plan_id)
    if args.json:
        _print_json(plan.to_dict())
        return EXIT_OK
    print(f"Plan {plan.plan_id} ({plan.kind}), made {_local_time(plan.created_at)}:")
    for line in pipeline.describe(plan):
        print(line)
    _print_plan_summary(plan)
    return EXIT_OK


def _cmd_plan_calibration(args: argparse.Namespace) -> int:
    with library.open(_library_root(args), write=False) as lib:
        with open_index(lib.paths, write=False) as index:
            path = pipeline.calibration_pairs(lib, index, args.out)
    if args.json:
        _print_json({"path": str(path)})
    else:
        print(f"Saved {path}. Fill in `same` (yes or no) after listening, add the")
        print("different-version pairs, then run scripts/calibrate_fp.py on it.")
    return EXIT_OK


def _cmd_apply(args: argparse.Namespace) -> int:
    with library.open(_library_root(args), write=True, command=f"apply {args.plan_id}") as lib:
        with open_index(lib.paths, write=False) as index:
            result = pipeline.apply(lib, index, args.plan_id)
    if args.json:
        _print_json(result.to_dict())
    else:
        print(f"Queued {result.jobs:,} job(s) as batch {result.batch_id}.")
        print("Run `musicorg queue run` to work through them. To take the batch back later:")
        print(f"  musicorg undo {result.batch_id}")
    return EXIT_OK


def _print_new_plan(plan: fileops.Plan, json_mode: bool) -> int:
    if json_mode:
        _print_json({"plan_id": plan.plan_id, "summary": plan.summary})
        return EXIT_OK
    print(f"Made plan {plan.plan_id} ({plan.kind}). Nothing has been changed yet.")
    _print_plan_summary(plan)
    if plan.operations:
        print(f"See each step with `musicorg plan show {plan.plan_id}`, then run it with")
        print(f"  musicorg apply {plan.plan_id}")
    return EXIT_OK


def _print_plan_summary(plan: fileops.Plan) -> None:
    s = plan.summary
    if plan.kind == "replace":
        print(
            f"  {s.get('operations', 0):,} rip(s) to replace, {s.get('downloads', 0):,} download(s)"
        )
        if s.get("in_library"):
            print(f"  {s['in_library']:,} video(s) already in the library: linked, not downloaded")
        if s.get("stage_only"):
            print("  Calibration only (--stage-only): nothing will be committed or replaced")
    else:
        print(f"  {s.get('adopts', 0):,} rip(s) to copy in")
        if s.get("low_confidence_adopts"):
            print(f"  {s['low_confidence_adopts']:,} of them keep the rip's own names "
                  "(the file name was hard to read)")  # fmt: skip
        if s.get("unsupported_format"):
            print(f"  {s['unsupported_format']:,} in a format not adopted in v0.1 (WebM, AAC, WAV)")
    minutes = s.get("est_minutes") or 0
    took = f"about {minutes} min" if minutes < 90 else f"about {minutes / 60:.1f} hours"
    days = s.get("days") or 0
    if days > 1:
        took += f", spread over {days} days by the daily download limit"
    print(f"  Time: {took}; disk space: about {s.get('disk_mb', 0):,} MB")
    for why, n in sorted((s.get("skipped") or {}).items()):
        print(f"  Left out: {n:,} ({why.replace('_', ' ')})")


def _cmd_undo(args: argparse.Namespace) -> int:
    command = f"undo {args.batch_id}" + (" --dry-run" if args.dry_run else "")
    with library.open(_library_root(args), write=True, command=command) as lib:
        if not args.dry_run:
            queue.requeue_interrupted(lib)  # no queue runs while this holds the lock
        result = pipeline.undo(lib, args.batch_id, dry_run=args.dry_run)
    if args.json:
        _print_json(result.to_dict())
        return EXIT_OK

    if args.dry_run:
        print(f"Undoing batch {result.batch_id} would do this (nothing has been changed yet):")
    else:
        print(f"Undoing batch {result.batch_id}:")
    marks = {"planned": "↩", "done": "↩", "skipped": "-", "manual": "!"}
    for step in result.steps:
        prefix = "Skipped: " if step.status == "skipped" else ""
        print(f"  {marks.get(step.status, '?')} {prefix}{step.note}")
    if not result.steps:
        print("  Nothing: the batch didn't change any files.")
    done = sum(step.status == "done" for step in result.steps)
    if result.cancelled_jobs:
        print(f"Cancelled {result.cancelled_jobs} queued job(s) of that batch.")
    if result.undo_batch_id:
        noun = "change" if done == 1 else "changes"
        print(f"Undid {done} {noun}. The undo itself is batch {result.undo_batch_id}.")
    elif not args.dry_run:
        print("Nothing needed undoing.")
    return EXIT_OK


def _library_root(args: argparse.Namespace) -> Path:
    root = args.library if args.library is not None else Config.load().last_library
    if root is None:
        raise UserError(
            "No library chosen yet. Pass --library <folder>, "
            "or create one with `musicorg init <folder>`."
        )
    return root


def _local_time(stamp: str) -> str:
    try:
        return f"{datetime.fromisoformat(stamp).astimezone():%Y-%m-%d %H:%M}"
    except ValueError:
        return stamp


def _not_yet(command: str, step: str) -> Handler:
    def handler(args: argparse.Namespace) -> int:
        raise NotImplementedYetError(command, step)

    return handler


# ---- plumbing --------------------------------------------------------------------------


def _global_options(for_subcommand: bool) -> argparse.ArgumentParser:
    """Options accepted both before and after the command name.

    The copy attached to each command uses SUPPRESS defaults, so an option given before
    the command isn't reset by the command's parser.
    """

    def default(value: Any) -> Any:
        return argparse.SUPPRESS if for_subcommand else value

    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "--library",
        type=_path,
        metavar="ROOT",
        default=default(None),
        help="Library folder. Defaults to the last library opened.",
    )
    p.add_argument(
        "--json", action="store_true", default=default(False), help="Machine-readable output."
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        default=default(False),
        help="Show more detail on screen and in the log.",
    )
    return p


def _path(text: str) -> Path:
    """Path arguments. Expands a quoted "~/..." the shell left alone."""
    return Path(text).expanduser()


def _print_warnings(warnings: list[str]) -> None:
    if not warnings:
        return
    print()
    print("Warnings:")
    for warning in warnings:
        print(f"  ! {warning}")


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _report_error(exc: MusicOrgError, json_mode: bool) -> int:
    if json_mode:
        _print_json({"ok": False, "error": {"exit_code": exc.exit_code, "message": exc.message}})
    else:
        print(exc.message, file=sys.stderr)
    return exc.exit_code


def _report_internal_error(exc: Exception, json_mode: bool) -> int:
    log.error("Unexpected error", exc_info=True, extra={"console": False})
    path = logging_setup.log_file()
    message = f"Something unexpected went wrong ({type(exc).__name__}: {exc})."
    if path is not None:
        message += f" The details are in the log: {path}"
    if json_mode:
        _print_json({"ok": False, "error": {"exit_code": EXIT_INTERNAL, "message": message}})
    else:
        print(message, file=sys.stderr)
    if path is None:
        print("The log file couldn't be written, so here are the details:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
    return EXIT_INTERNAL


def _use_utf8_output() -> None:
    """Print ✓ and non-English names safely, even when output goes to a pipe on Windows."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding == "utf8":
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass
