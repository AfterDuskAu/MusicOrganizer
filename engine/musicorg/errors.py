"""Exception types.

Every message is plain English and can be shown to the user as it is. Each type carries
the CLI exit code from docs/ENGINE_API.md.
"""

from __future__ import annotations

from datetime import datetime

EXIT_OK = 0
EXIT_USER_ERROR = 1
EXIT_LOCKED = 2
EXIT_TOOL_MISSING = 3
EXIT_YOUTUBE_BLOCKED = 4
EXIT_INTERNAL = 10


class MusicOrgError(Exception):
    """Base class for errors the engine expects and can explain."""

    exit_code = EXIT_INTERNAL

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UserError(MusicOrgError):
    """Something the user can fix: a bad argument, a missing folder, a damaged setting."""

    exit_code = EXIT_USER_ERROR


class ConfigError(UserError):
    """config.json can't be read or saved."""


class NotImplementedYetError(UserError):
    """A command that a later step of the build brief will implement."""

    def __init__(self, command: str, step: str) -> None:
        super().__init__(f"musicorg {command}: not implemented yet (step {step})")
        self.command = command
        self.step = step


class StateError(UserError):
    """The library's state.json can't be read or saved."""


class PathTooLongError(UserError):
    """A library path can't be made to fit Windows' path length limit."""


class LibraryLockedError(MusicOrgError):
    """Another engine process holds the library's single-writer lock.

    `holder` is what the holder wrote to lock.info (pid, command, started_at, host), or
    None if that couldn't be read.
    """

    exit_code = EXIT_LOCKED

    def __init__(self, message: str, holder: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.holder = holder


class ToolMissingError(MusicOrgError):
    """ffmpeg, ffprobe, fpcalc or deno is missing or unusable."""

    exit_code = EXIT_TOOL_MISSING

    def __init__(self, tool: str, message: str) -> None:
        super().__init__(message)
        self.tool = tool


class YouTubeBlockedError(MusicOrgError):
    """YouTube is slowing us down, or the queue is paused because of it (step 09a)."""

    exit_code = EXIT_YOUTUBE_BLOCKED


class YouTubePausedError(YouTubeBlockedError):
    """YouTube refused or dropped several requests in a row, so everything that talks to
    it waits until `resume_at` (an aware datetime)."""

    def __init__(self, message: str, resume_at: datetime) -> None:
        super().__init__(message)
        self.resume_at = resume_at


class YouTubeRefusedError(YouTubeBlockedError):
    """YouTube refused a download from us ("confirm you're not a bot", HTTP 429). The whole
    queue pauses (step 09a). Never retried in a loop, and never worked around."""


class YouTubeError(UserError):
    """YouTube Music gave an answer the engine couldn't use (not a slow-down)."""


# ---- downloads (step 09a) --------------------------------------------------------------


class DownloadError(UserError):
    """A download didn't work. `network` is True for network-level failures (HTTP 403,
    timeouts, dropped connections): three in a row pause the queue."""

    def __init__(self, message: str, *, network: bool = False) -> None:
        super().__init__(message)
        self.network = network


class FormatUnavailableError(DownloadError):
    """YouTube didn't offer format 140 (AAC in M4A) for this video. There's no fallback to
    another format (CLAUDE.md rule 6)."""


class VideoUnavailableError(DownloadError):
    """This one video can't be downloaded: age-restricted, private, removed, blocked in
    this country, members-only. The queue carries on with the next job."""


class ReplayMissError(UserError):
    """Replay mode (MUSICORG_REPLAY_DIR) has no recorded response for a request. Tests
    never reach the network; record the response first (scripts/record_ytm.py)."""


# ---- fileops (step 03b) ----------------------------------------------------------------


class OutsideLibraryError(UserError):
    """A file operation was asked to change something outside the library's managed
    folders (contract 6.2). Nothing was changed. `path` is the path as it was given."""

    def __init__(self, message: str, path: object = None) -> None:
        super().__init__(message)
        self.path = path


class CrossVolumeError(UserError):
    """A move would cross drives. Moves are renames and never copy-then-delete (contract 6.4)."""


class FileInUseError(UserError):
    """Windows: another app has the file open, so it can't be moved or replaced."""


class FileOperationError(UserError):
    """The operating system refused a file operation, e.g. no permission. Nothing was
    left half done."""


class SourceChangedError(UserError):
    """A file outside the library changed while it was being copied in."""


class IntegrityError(MusicOrgError):
    """A copy or a tag write didn't verify. The original was left untouched."""

    exit_code = EXIT_USER_ERROR


class NotFoundError(UserError):
    """A batch, plan or item that doesn't exist."""


class PlanOutOfDateError(UserError):
    """A plan's preconditions no longer hold (a rip changed, an item was decided again,
    or the plan was applied already). Make a new plan."""


class UndoError(UserError):
    """An undo was refused, or stopped part way. It can be run again."""


class AudioError(UserError):
    """ffprobe or ffmpeg couldn't read a file's audio."""


class LibraryIndexError(UserError):
    """The library's index (index.sqlite) can't be opened or read. It's a cache: a
    rebuild fixes it."""
