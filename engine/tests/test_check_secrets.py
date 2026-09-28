"""scripts/check_secrets.py, the pre-commit/pre-push secret check.

It isn't part of the engine, but it's tested here so it runs on all three CI machines.
Fake secrets are assembled at runtime so this file never contains one, and neither the
check itself nor GitHub's secret scanning flags it.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_secrets.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_secrets", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_secrets"] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    return module


cs = _load()

FAKE = {
    "AWS access key": "AKIA" + "QZ7X" * 4,
    "GitHub token": "ghp_" + "a1B2c3D4e5" * 4,
    "Google API key": "AIza" + "Sy" + "b1C2d3E4f5" * 3 + "g6h",
    "Anthropic API key": "sk-" + "ant-" + "api03-" + "Zx9Yw8Vu7T" * 3,
    "Slack token": "xox" + "b-" + "1234567890-abcdefghij",
    "private key": "-----BEGIN " + "RSA PRIVATE" + " KEY-----",
    "Google/YouTube login cookie": "SAPI" + "SID=" + "AbCdEfGhIj/KlMnOpQrSt",
    "Netscape cookie file": "# Netscape " + "HTTP Cookie File",
    "password in a URL": "https://" + "me:hunter2" + "@example.org/x",
    "secret written into code": "lastfm_api" + '_key = "' + "9f8e7d6c5b4a39281706" + '"',
    "email address": "Contact: jane.doe" + "@" + "gmail.com",
}
PERSONAL_EMAIL = "jane.doe" + "@" + "gmail.com"


@pytest.mark.parametrize("kind", sorted(FAKE))
def test_detects(kind: str) -> None:
    findings = cs.check_line("f.py:1", FAKE[kind])
    assert kind in {f.kind for f in findings}


@pytest.mark.parametrize("kind", sorted(FAKE))
def test_never_prints_the_whole_secret(kind: str) -> None:
    for finding in cs.check_line("f.py:1", FAKE[kind]):
        assert FAKE[kind] not in str(finding)


@pytest.mark.parametrize(
    "line",
    [
        'token = "{token}"',
        'api_key = "<your key here>"',
        'password = "changeme123"',
        'secret = "xxxxxxxxxx"',
        'api_key = os.environ["LASTFM_API_KEY"]',
        "def tool_path(self, tool: str) -> Path | None:",
        'print("the password was wrong")',
        "See https://github.com/acoustid/chromaprint/releases",
        "SID: short",
        "Co-Authored-By: Claude <noreply@anthropic.com>",
        "Author: Someone <12345+someone@users.noreply.github.com>",
        "git clone git@github.com:owner/repo.git",
        '"GIT_AUTHOR_EMAIL": "test@example.invalid",',
        "@pytest.mark.live",
        "      - uses: actions/checkout@v7",
        "https://deno.land/x/install@v0.3.3/install.sh",
    ],
)
def test_ignores_ordinary_lines(line: str) -> None:
    assert cs.check_line("f.py:1", line) == []


def test_allow_marker() -> None:
    assert cs.check_line("f.py:1", FAKE["GitHub token"] + "  # secrets-ok") == []


@pytest.mark.parametrize(
    "path",
    [
        "cookies.txt",
        "some/dir/youtube-cookies.txt",
        "browser.json",
        "oauth.json",
        ".env",
        ".env.local",
        "deploy/server.pem",
        "id_ed25519",
        "CREDENTIALS.JSON",
    ],
)
def test_blocked_file_names(path: str) -> None:
    assert cs.check_name(path) is not None


@pytest.mark.parametrize("path", [".env.example", "engine/musicorg/config.py", "config.json"])
def test_allowed_file_names(path: str) -> None:
    assert cs.check_name(path) is None


def test_diff_reports_new_line_numbers() -> None:
    diff = (
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -10,0 +11,2 @@\n"
        "+ok = 1\n"
        f"+{FAKE['GitHub token']}\n"
        "-" + FAKE["AWS access key"] + "\n"  # a removed line is fine
    )
    findings = cs.check_diff(diff)
    assert [(f.where, f.kind) for f in findings] == [("x.py:12", "GitHub token")]


# ---- through git, the way the hooks run it -------------------------------------------


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    empty_config = tmp_path / "empty-gitconfig"
    empty_config.write_text("")
    work = tmp_path / "repo"
    for key, value in {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_CONFIG_GLOBAL": str(empty_config),
        "GIT_CONFIG_NOSYSTEM": "1",
    }.items():
        monkeypatch.setenv(key, value)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    return work


def run_check(repo: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=repo,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def test_staged_clean(repo: Path) -> None:
    (repo / "a.py").write_text("x = 1\n")
    git(repo, "add", "a.py")
    assert run_check(repo, "--staged").returncode == 0


def test_staged_secret_blocks_commit(repo: Path) -> None:
    (repo / "a.py").write_text(f"x = 1\nkey = '{FAKE['AWS access key']}'\n")
    git(repo, "add", "a.py")
    result = run_check(repo, "--staged")
    assert result.returncode == 1
    assert "Commit blocked" in result.stderr
    assert "a.py:2" in result.stderr
    assert FAKE["AWS access key"] not in result.stderr


def test_staged_cookie_file_blocks_commit(repo: Path) -> None:
    (repo / "cookies.txt").write_text("nothing yet\n")
    git(repo, "add", "-f", "cookies.txt")
    result = run_check(repo, "--staged")
    assert result.returncode == 1
    assert "cookies.txt" in result.stderr


def test_push_catches_secret_deleted_in_a_later_commit(repo: Path) -> None:
    (repo / "a.py").write_text(f"token = '{FAKE['GitHub token']}'\n")
    git(repo, "add", "a.py")
    git(repo, "commit", "-q", "-m", "oops")
    (repo / "a.py").write_text("token = None\n")
    git(repo, "commit", "-q", "-am", "remove it")
    head = git(repo, "rev-parse", "HEAD")
    zero = "0" * 40
    result = run_check(repo, "--push", stdin=f"refs/heads/main {head} refs/heads/main {zero}\n")
    assert result.returncode == 1
    assert "Push blocked" in result.stderr
    assert "GitHub token" in result.stderr


def test_output_is_utf8_on_a_windows_code_page(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows writes pipes in the ANSI code page unless told otherwise (CI caught this)."""
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    (repo / "música.py").write_text(f"token = '{FAKE['GitHub token']}'\n", encoding="utf-8")
    git(repo, "add", "música.py")
    result = subprocess.run([sys.executable, str(SCRIPT), "--all"], cwd=repo, capture_output=True)
    assert result.returncode == 1
    text = result.stderr.decode("utf-8")  # raises if it isn't UTF-8
    assert "música.py:1" in text


def test_all_and_history(repo: Path) -> None:
    (repo / "a.py").write_text("x = 1\n")
    git(repo, "add", "a.py")
    git(repo, "commit", "-q", "-m", "clean")
    assert run_check(repo, "--all").returncode == 0
    assert run_check(repo, "--history").returncode == 0


def test_this_repository_is_clean() -> None:
    result = run_check(REPO, "--all")
    assert result.returncode == 0, result.stderr


# ---- emails in commit details and messages ---------------------------------------------


def test_personal_commit_email_blocks_commit(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", PERSONAL_EMAIL)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", PERSONAL_EMAIL)
    (repo / "a.py").write_text("x = 1\n")
    git(repo, "add", "a.py")
    result = run_check(repo, "--staged")
    assert result.returncode == 1
    assert "personal email in the commit details" in result.stderr
    assert "users.noreply.github.com" in result.stderr  # tells you the fix
    assert PERSONAL_EMAIL not in result.stderr


def test_push_catches_personal_commit_email(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", PERSONAL_EMAIL)
    (repo / "a.py").write_text("x = 1\n")
    git(repo, "add", "a.py")
    git(repo, "commit", "-q", "-m", "clean file, personal author")
    head = git(repo, "rev-parse", "HEAD")
    result = run_check(repo, "--push", stdin=f"refs/heads/main {head} refs/heads/main {'0' * 40}\n")
    assert result.returncode == 1
    assert "commit author" in result.stderr


def test_history_catches_email_in_commit_message(repo: Path) -> None:
    (repo / "a.py").write_text("x = 1\n")
    git(repo, "add", "a.py")
    git(repo, "commit", "-q", "-m", f"thanks to {PERSONAL_EMAIL}")
    result = run_check(repo, "--history")
    assert result.returncode == 1
    assert "commit message" in result.stderr


def test_commit_message_file(tmp_path: Path) -> None:
    clean = tmp_path / "clean"
    clean.write_text(
        "Step 03a: library core\n\n"
        "Co-Authored-By: Claude <noreply@anthropic.com>\n"
        f"# Author: Jane <{PERSONAL_EMAIL}>  (git's own comment lines are ignored)\n"
    )
    assert run_check(tmp_path, "--message", str(clean)).returncode == 0

    leaky = tmp_path / "leaky"
    leaky.write_text(f"Fix login\n\nkey: {FAKE['GitHub token']}\n")
    result = run_check(tmp_path, "--message", str(leaky))
    assert result.returncode == 1
    assert "GitHub token" in result.stderr
