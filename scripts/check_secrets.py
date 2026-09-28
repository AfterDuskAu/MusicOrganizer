#!/usr/bin/env python3
"""Stop keys, passwords and login cookies from being committed.

This repository is public. Anything committed is exposed for good, even if a later
commit deletes it, and bots scan GitHub for secrets within minutes. This check runs as
the git pre-commit and pre-push hook (.githooks/, switched on with
`git config core.hooksPath .githooks`) and in CI.

    python3 scripts/check_secrets.py --staged    what's about to be committed
    python3 scripts/check_secrets.py --push      commits about to be pushed (pre-push hook)
    python3 scripts/check_secrets.py --all       every tracked file
    python3 scripts/check_secrets.py --history   every commit ever made

Findings are printed with the value mostly hidden, because CI logs are public too.
If a line is a genuine false alarm, end it with a `secrets-ok` comment.

Standard library only, Python 3.9+, so it runs without the engine's .venv.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ALLOW_MARKER = "secrets-ok"
ZERO_SHA = "0" * 40
MAX_FILE_BYTES = 5 * 1024 * 1024

# File names that almost always hold secrets, wherever they are in the repo.
BLOCKED_NAMES = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.keychain",
    "*.keystore",
    "id_rsa",
    "id_rsa.*",
    "id_ecdsa",
    "id_ecdsa.*",
    "id_ed25519",
    "id_ed25519.*",
    ".netrc",
    ".pypirc",
    "credentials.json",
    "client_secret*.json",
    "service-account*.json",
    # YouTube and YouTube Music logins: yt-dlp cookie files, ytmusicapi auth files.
    "cookies.txt",
    "*cookies*.txt",
    "*.cookies",
    "browser.json",
    "oauth.json",
    "headers_auth.json",
]
ALLOWED_NAMES = [".env.example", ".env.sample"]

_KEY_NAME = (
    r"[A-Za-z0-9_\-]*(?:password|passwd|secret|token|api[_\-]?key|access[_\-]?key"
    r"|private[_\-]?key|auth[_\-]?key)[A-Za-z0-9_\-]*"
)
PATTERNS = [
    ("private key", r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"),
    ("AWS access key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("GitHub token", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
    ("Google API key", r"\bAIza[0-9A-Za-z_\-]{35}"),
    ("Google OAuth client secret", r"\bGOCSPX-[A-Za-z0-9_\-]{20,}"),
    ("Google OAuth token", r"\bya29\.[A-Za-z0-9_\-]{20,}"),
    ("Anthropic API key", r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
    ("OpenAI-style API key", r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),
    ("Slack token", r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"),
    ("Stripe live key", r"\b[rs]k_live_[A-Za-z0-9]{16,}"),
    (
        "JSON web token",
        r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
    ),
    (
        "Google/YouTube login cookie",
        r"(?:__Secure-[13]P(?:SID|APISID|SIDTS|SIDCC)|\bSAPISID|\bAPISID|\bHSID|\bSSID"
        r"|\bSID|\bLOGIN_INFO)(?:=|\t)[^\s;\"']{10,}",
    ),
    ("Netscape cookie file", r"^# (?:Netscape )?HTTP Cookie File"),
    (
        "authorization header",
        r"(?i)\bauthorization[\"']?\s*[:=]\s*[\"']?(?:bearer|basic|sapisidhash)\s+"
        r"[A-Za-z0-9._~+/=\-]{12,}",
    ),
    ("password in a URL", r"\b[a-z][a-z0-9+.\-]*://[^/\s:@\"']+:[^/\s@\"']{3,}@"),
    (
        "secret written into code",
        r"(?i)\b" + _KEY_NAME + r"[\"']?\s*[:=]\s*[\"'](?P<value>[^\"'\s]{8,})[\"']",
    ),
]
_COMPILED = [(name, re.compile(pattern)) for name, pattern in PATTERNS]
# Values the "secret written into code" rule ignores: placeholders, not real secrets.
_PLACEHOLDER = re.compile(
    r"(?i)^(?:<.*>|\$\{.*\}|\{.*\}|x{4,}|\*{4,}|\.{3,})$"
    r"|example|changeme|placeholder|dummy|your[_\-]|redacted"
)


@dataclass(frozen=True)
class Finding:
    where: str  # "path:line", or just the path for a file-name finding
    kind: str
    sample: str  # the matched text, mostly hidden

    def __str__(self) -> str:
        return f"  {self.where}  {self.kind}  {self.sample}"


def redact(text: str) -> str:
    text = text.strip()
    if len(text) <= 8:
        return "[hidden]"
    return f"{text[:4]}…[{len(text) - 4} characters hidden]"


def check_name(path: str) -> Finding | None:
    name = PurePosixPath(path).name
    if any(fnmatch.fnmatch(name.lower(), allowed) for allowed in ALLOWED_NAMES):
        return None
    for pattern in BLOCKED_NAMES:
        if fnmatch.fnmatch(name.lower(), pattern):
            return Finding(path, "file name that usually holds secrets", f"({pattern})")
    return None


def check_line(where: str, line: str) -> list[Finding]:
    if ALLOW_MARKER in line:
        return []
    findings = []
    for kind, regex in _COMPILED:
        for match in regex.finditer(line):
            if "value" in regex.groupindex:
                value = match.group("value")
                if _PLACEHOLDER.search(value) or "{" in value:
                    continue
            findings.append(Finding(where, kind, redact(match.group(0))))
    return findings


def check_text(path: str, text: str) -> list[Finding]:
    findings = []
    for number, line in enumerate(text.splitlines(), start=1):
        findings += check_line(f"{path}:{number}", line)
    return findings


def check_diff(diff: str, label: str = "") -> list[Finding]:
    """Check only the added lines of a unified diff (from git diff/show -U0)."""
    findings: list[Finding] = []
    path = "?"
    line_number = 0
    hunk = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:]
            path = target.removeprefix("b/")
            continue
        match = hunk.match(line)
        if match:
            line_number = int(match.group(1))
            continue
        if line.startswith("+"):
            findings += check_line(f"{label}{path}:{line_number}", line[1:])
            line_number += 1
    return findings


# ---- git ---------------------------------------------------------------------------------


def git(*args: str, stdin: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _diff_args() -> list[str]:
    return ["-U0", "--no-color", "--no-ext-diff", "--diff-filter=ACMR"]


def scan_staged() -> list[Finding]:
    findings = []
    names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split("\0")
    findings += [f for f in (check_name(n) for n in names if n) if f]
    findings += check_diff(git("diff", "--cached", *_diff_args()))
    return findings


def scan_commit(sha: str) -> list[Finding]:
    label = f"{sha[:8]} "
    findings = []
    names = git("show", "--format=", "--name-only", "--diff-filter=ACMR", "-z", sha)
    for name in names.split("\0"):
        name = name.strip()
        finding = check_name(name) if name else None
        if finding:
            findings.append(Finding(label + finding.where, finding.kind, finding.sample))
    findings += check_diff(git("show", "--format=", *_diff_args(), sha), label)
    return findings


def scan_push(stdin_text: str) -> list[Finding]:
    """pre-push gives lines of: <local ref> <local sha> <remote ref> <remote sha>."""
    commits: list[str] = []
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) != 4 or parts[1] == ZERO_SHA:
            continue  # malformed, or deleting a remote branch
        local_sha, remote_sha = parts[1], parts[3]
        if remote_sha == ZERO_SHA:
            revs = git("rev-list", local_sha, "--not", "--remotes")
        else:
            revs = git("rev-list", f"{remote_sha}..{local_sha}")
        commits += [c for c in revs.split() if c not in commits]
    findings = []
    for sha in commits:
        findings += scan_commit(sha)
    return findings


def scan_history() -> list[Finding]:
    findings = []
    for sha in git("rev-list", "--all").split():
        findings += scan_commit(sha)
    return findings


def scan_all() -> list[Finding]:
    root = Path(git("rev-parse", "--show-toplevel").strip())
    findings = []
    for name in git("ls-files", "-z").split("\0"):
        if not name:
            continue
        finding = check_name(name)
        if finding:
            findings.append(finding)
        path = root / name
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue  # binary; the file-name check still applies
        findings += check_text(name, data.decode("utf-8", errors="replace"))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refuse keys, passwords and login cookies.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="check what's about to be committed")
    mode.add_argument("--push", action="store_true", help="pre-push hook: read refs from stdin")
    mode.add_argument("--all", action="store_true", help="check every tracked file")
    mode.add_argument("--history", action="store_true", help="check every commit")
    args = parser.parse_args(argv)

    try:
        if args.staged:
            findings, blocked = scan_staged(), "Commit blocked"
        elif args.push:
            findings, blocked = scan_push(sys.stdin.read()), "Push blocked"
        elif args.all:
            findings, blocked = scan_all(), "Found"
        else:
            findings, blocked = scan_history(), "Found in history"
    except RuntimeError as exc:
        print(f"Secret check couldn't run: {exc}", file=sys.stderr)
        return 2

    if not findings:
        return 0
    print(
        f"Secret check: {blocked}. These look like keys, passwords or login cookies:",
        file=sys.stderr,
    )
    print(file=sys.stderr)
    for finding in findings:
        print(finding, file=sys.stderr)
    print(file=sys.stderr)
    print(
        "This repository is public, so anything committed is exposed for good.\n"
        "Remove these, and keep secrets in files that .gitignore excludes.\n"
        f"If a line is a genuine false alarm, end it with a `{ALLOW_MARKER}` comment.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
