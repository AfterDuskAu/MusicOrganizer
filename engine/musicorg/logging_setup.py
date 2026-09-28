"""Logging: a rotating file in the log folder, plus stderr.

Nothing here writes to stdout. In `serve` mode stdout carries the JSON-RPC protocol.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from musicorg import config
from musicorg.errors import ConfigError

LOG_FILE_NAME = "musicorg.log"
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 4  # the current file plus 4 old ones: 5 files of up to 2 MB

_ROOT_LOGGER = "musicorg"
_OURS = "_musicorg_handler"
_log_file: Path | None = None


class _ConsoleFilter(logging.Filter):
    """Lets a record opt out of the console with extra={"console": False}."""

    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "console", True)


class _ConsoleFormatter(logging.Formatter):
    """Short lines, and never a raw traceback. Those go to the log file only."""

    def format(self, record: logging.LogRecord) -> str:
        if record.exc_info or record.exc_text or record.stack_info:
            record = logging.makeLogRecord(
                {**record.__dict__, "exc_info": None, "exc_text": None, "stack_info": None}
            )
        return super().format(record)


def setup_logging(verbose: bool = False) -> Path | None:
    """Set up the engine's logger. Returns the log file path, or None if it can't be written.

    Safe to call again: handlers from an earlier call are closed and replaced.
    """
    global _log_file
    shutdown_logging()
    logger = logging.getLogger(_ROOT_LOGGER)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    console.setFormatter(_ConsoleFormatter("%(levelname)s: %(message)s"))
    console.addFilter(_ConsoleFilter())
    _add(logger, console)

    log_dir = config.app_dirs().logs
    try:
        config.ensure_app_dir(log_dir)
        path = log_dir / LOG_FILE_NAME
        handler = RotatingFileHandler(
            path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
        )
    except (ConfigError, OSError) as exc:
        _log_file = None
        logger.warning(
            "Can't write the log file in %s (%s); logging to the screen only.", log_dir, exc
        )
        return None
    handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(process)d %(levelname)s %(name)s: %(message)s")
    )
    _add(logger, handler)
    _log_file = path
    return path


def log_file() -> Path | None:
    """The current log file, if file logging is working."""
    return _log_file


def shutdown_logging() -> None:
    """Close the handlers setup_logging added, so the log file isn't held open."""
    global _log_file
    logger = logging.getLogger(_ROOT_LOGGER)
    for handler in list(logger.handlers):
        if getattr(handler, _OURS, False):
            logger.removeHandler(handler)
            handler.close()
    _log_file = None


def _add(logger: logging.Logger, handler: logging.Handler) -> None:
    setattr(handler, _OURS, True)
    logger.addHandler(handler)
