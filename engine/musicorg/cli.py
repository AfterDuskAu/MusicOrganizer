"""The `musicorg` command. Argument parsing and printing only; the work happens in the
other musicorg modules, which the JSON-RPC server (step 11) calls too.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn

from musicorg import __version__, doctor, logging_setup, status
from musicorg.config import Config
from musicorg.errors import (
    EXIT_INTERNAL,
    EXIT_OK,
    EXIT_TOOL_MISSING,
    EXIT_USER_ERROR,
    ConfigError,
    MusicOrgError,
    NotImplementedYetError,
    UserError,
)

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

    p = add(commands, "init", "Create a new library in an empty folder.", _not_yet("init", "03a"))
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
    p = add(
        sources, "add", "Register a folder as a read-only source.", _not_yet("sources add", "05")
    )
    p.add_argument("path", type=_path)
    add(sources, "list", "List registered sources.", _not_yet("sources list", "05"))
    p = add(
        sources,
        "remove",
        "Forget a source. Its files are never touched.",
        _not_yet("sources remove", "05"),
    )
    p.add_argument("source_id")

    p = add(commands, "scan", "Index sources, read-only.", _not_yet("scan", "05"))
    p.add_argument("source_ids", nargs="*", metavar="SOURCE_ID")

    index = group("index", "The library's search index.")
    add(
        index,
        "rebuild",
        "Rebuild the index from the files and state.json.",
        _not_yet("index rebuild", "05"),
    )

    p = add(
        commands,
        "match",
        "Search YouTube Music for new items and score the candidates.",
        _not_yet("match", "06"),
    )
    p.add_argument("--limit", type=int, metavar="N")
    p.add_argument(
        "--rescan",
        action="store_true",
        help="Match review and not-found items again, skipping the search cache.",
    )

    p = add(
        commands,
        "report",
        "Write the decision report (markdown and CSV).",
        _not_yet("report", "07"),
    )
    p.add_argument("--out", type=_path, metavar="DIR")

    review = group("review", "Review uncertain matches in a spreadsheet.")
    p = add(review, "export", "Write the review CSV.", _not_yet("review export", "07"))
    p.add_argument("csv", type=_path)
    p.add_argument("--include-auto", action="store_true")
    p = add(
        review, "import", "Apply the decisions from a review CSV.", _not_yet("review import", "07")
    )
    p.add_argument("csv", type=_path)

    journal = group("journal", "The log of every change made to the library.")
    add(journal, "list", "Recent batches of changes.", _not_yet("journal list", "03b"))

    p = add(commands, "undo", "Reverse a batch of changes.", _not_yet("undo", "03b"))
    p.add_argument("batch_id")
    p.add_argument("--dry-run", action="store_true")

    plan = group("plan", "Make a dry-run plan of changes.")
    p = add(
        plan,
        "replace",
        "Plan replacing rips with official downloads.",
        _not_yet("plan replace", "09b"),
    )
    p.add_argument("--only", choices=["auto", "accepted", "all-eligible"])
    p.add_argument("--limit", type=int, metavar="N")
    p.add_argument("--stage-only", action="store_true")
    p = add(
        plan,
        "adopt",
        "Plan copying only-copy rips into the library.",
        _not_yet("plan adopt", "09b"),
    )
    p.add_argument("--include-not-found", action="store_true")
    p = add(plan, "show", "Show a plan's operations and summary.", _not_yet("plan show", "09b"))
    p.add_argument("plan_id")

    p = add(commands, "apply", "Check a plan and queue its jobs.", _not_yet("apply", "09b"))
    p.add_argument("plan_id")

    queue = group("queue", "The throttled download queue.")
    add(
        queue,
        "run",
        "Work through the queue until it's empty or paused.",
        _not_yet("queue run", "09a"),
    )
    add(queue, "status", "Queue state and counts.", _not_yet("queue status", "09a"))
    add(queue, "pause", "Pause the queue after the current job.", _not_yet("queue pause", "09a"))
    add(queue, "resume", "Resume the queue.", _not_yet("queue resume", "09a"))

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


def _cmd_status(args: argparse.Namespace) -> int:
    library = args.library if args.library is not None else Config.load().last_library
    result = status.get_status(library)
    if args.json:
        _print_json(result)
        return EXIT_OK

    print(f"Music Organizer engine {result['engine_version']}")
    if library is None:
        print(
            "Library: none chosen yet. Pass --library <folder>, "
            "or create one with `musicorg init <folder>` (step 03a)."
        )
    elif not result["library_exists"]:
        print(f"Library: {library} (this folder doesn't exist)")
    elif not result["is_library"]:
        print(f"Library: {library} (this folder isn't a Music Organizer library yet)")
    else:
        print(f"Library: {library}")
    print("Item counts arrive in step 05, and the queue state in step 09a.")
    return EXIT_OK


def _cmd_doctor(args: argparse.Namespace) -> int:
    if args.update_ytdlp or args.rollback_ytdlp:
        flag = "--update-ytdlp" if args.update_ytdlp else "--rollback-ytdlp"
        raise NotImplementedYetError(f"doctor {flag}", "09a")

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
