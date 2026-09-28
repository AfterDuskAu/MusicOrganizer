"""Exception types.

Every message is plain English and can be shown to the user as it is. Each type carries
the CLI exit code from docs/ENGINE_API.md.
"""

from __future__ import annotations

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


class LibraryLockedError(MusicOrgError):
    """Another engine process holds the library's single-writer lock (step 03a)."""

    exit_code = EXIT_LOCKED


class ToolMissingError(MusicOrgError):
    """ffmpeg, ffprobe, fpcalc or deno is missing or unusable."""

    exit_code = EXIT_TOOL_MISSING

    def __init__(self, tool: str, message: str) -> None:
        super().__init__(message)
        self.tool = tool


class YouTubeBlockedError(MusicOrgError):
    """YouTube is slowing us down, or the queue is paused because of it (step 09a)."""

    exit_code = EXIT_YOUTUBE_BLOCKED
