"""CLAUDE.md rule 3: only fileops writes files, apart from a few named exceptions.

Every module in musicorg/ is parsed with Python's `ast` module (not grep), and each call
that could create, write, move, copy, rename, replace or delete a file or folder is
flagged, unless:

- the module is fileops.py, or state.py, index.py or config.py (their own files);
- it's tags.py's one mutagen `.save()` call;
- the call's line carries the comment `# fileops-ok: in-memory`.

The checks are deliberately simple and err on the side of flagging. Calls into musicorg's
own modules (e.g. `fileops.create_layout(...)`, `library.open(...)`) aren't flagged: the
writing code inside them is checked where it lives.
"""

from __future__ import annotations

import ast
import io
import re
import shutil
import tokenize
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

import musicorg

PACKAGE_DIR = Path(musicorg.__file__).parent
MARKER = "fileops-ok: in-memory"

# Modules the rule lets write (their own files; the test can't tell which files).
ALLOWED_MODULES = frozenset({"fileops.py", "state.py", "index.py", "config.py"})
# May make exactly one `.save()` call (mutagen's), and nothing else.
TAGS_MODULE = "tags.py"

WRITE_FUNCTIONS = frozenset(
    {
        "os.rename", "os.renames", "os.replace", "os.remove", "os.unlink", "os.rmdir",
        "os.removedirs", "os.makedirs", "os.mkdir", "os.link", "os.symlink", "os.truncate",
        "shutil.move", "shutil.copy", "shutil.copy2", "shutil.copyfile", "shutil.copytree",
        "shutil.rmtree",
        "send2trash.send2trash",
        "tempfile.mkstemp", "tempfile.mkdtemp", "tempfile.NamedTemporaryFile",
        "tempfile.TemporaryFile", "tempfile.SpooledTemporaryFile",
        "tempfile.TemporaryDirectory",
    }
)  # fmt: skip
# Methods that write whatever object they're called on (Path, mutagen, Pillow, ...).
WRITE_METHODS = frozenset(
    {"write_text", "write_bytes", "touch", "unlink", "rename", "rmdir", "mkdir", "save",
     "delete", "symlink_to", "hardlink_to"}
)  # fmt: skip
# open() and friends: the mode is the second argument or `mode=`.
OPEN_FUNCTIONS = frozenset(
    {"open", "io.open", "os.fdopen", "codecs.open", "gzip.open", "bz2.open", "lzma.open"}
)
WRITE_MODE_CHARS = frozenset("wax+")
OS_OPEN_WRITE_FLAGS = frozenset({"O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC"})
_MODE_LIKE = re.compile(r"[rwxabtU+]+")


@dataclass(frozen=True)
class Violation:
    module: str
    line: int
    what: str

    def __str__(self) -> str:
        return f"{self.module}:{self.line}: {self.what}"


def find_violations(package_dir: Path) -> list[Violation]:
    """Every write outside the allowed places, in every module under `package_dir`."""
    found: list[Violation] = []
    for path in sorted(package_dir.rglob("*.py")):
        module = path.relative_to(package_dir).as_posix()
        found += check_module(module, path.read_text(encoding="utf-8"))
    return found


def check_module(module: str, source: str) -> list[Violation]:
    """`module` is the path relative to the package, e.g. "library.py"."""
    if module in ALLOWED_MODULES:
        return []
    tree = ast.parse(source, filename=module)
    marked = _marked_lines(source)
    found = []
    saves = 0
    for node, what in _write_calls(tree):
        end = getattr(node, "end_lineno", None) or node.lineno
        if any(line in marked for line in range(node.lineno, end + 1)):
            continue
        if module == TAGS_MODULE and what == ".save()":
            saves += 1
            if saves == 1:
                continue
            what = "a second .save() (tags.py may make only its one mutagen save)"
        found.append(Violation(module, node.lineno, what))
    return sorted(found, key=lambda v: v.line)


def _marked_lines(source: str) -> set[int]:
    """Lines whose comment (not a string) carries the marker."""
    lines = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and MARKER in token.string:
            lines.add(token.start[0])
    return lines


def _write_calls(tree: ast.Module) -> Iterator[tuple[ast.stmt | ast.expr, str]]:
    aliases = _import_aliases(tree)
    own_functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] == "send2trash" for a in node.names):
                yield node, "imports send2trash"
            continue
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "send2trash":
                yield node, "imports send2trash"
            continue
        if not isinstance(node, ast.Call):
            continue

        dotted = _dotted_name(node.func)
        if dotted is not None and "." not in dotted and dotted in own_functions:
            continue  # e.g. library.py's own open()
        qualified = _qualify(dotted, aliases) if dotted else None
        if qualified is not None and qualified.startswith("musicorg."):
            continue  # a call into one of our modules; checked where it's defined

        if qualified in WRITE_FUNCTIONS:
            yield node, f"{qualified}()"
        elif qualified == "os.open":
            if _os_open_writes(node):
                yield node, "os.open() with write flags"
        elif qualified in OPEN_FUNCTIONS:
            problem = _mode_problem(node, position=1, strict=True)
            if problem:
                yield node, f"{qualified}() {problem}"
        elif isinstance(node.func, ast.Attribute):
            method = node.func.attr
            is_module_function = dotted is not None and dotted.split(".")[0] in aliases
            if method == "open":
                position = 1 if is_module_function else 0
                problem = _mode_problem(node, position=position, strict=is_module_function)
                if problem:
                    yield node, f".open() {problem}"
            elif method in WRITE_METHODS:
                yield node, f".{method}()"
            elif method == "replace" and len(node.args) == 1 and not node.keywords:
                # Path.replace(target) takes one argument; str.replace needs two.
                yield node, ".replace() (Path.replace)"


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """Local name → what it was imported as: `o` → "os", `move` → "shutil.move"."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    aliases[top] = top
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:  # relative import inside the package
                module = "musicorg" + (f".{module}" if module else "")
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{module}.{alias.name}"
    return aliases


def _dotted_name(node: ast.expr) -> str | None:
    """ "os.path.join" for os.path.join; None when the chain doesn't start with a name."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _qualify(dotted: str, aliases: dict[str, str]) -> str:
    first, _, rest = dotted.partition(".")
    base = aliases.get(first, first)
    return f"{base}.{rest}" if rest else base


def _mode_problem(call: ast.Call, position: int, strict: bool) -> str | None:
    """Why an open() call's mode counts as writing, or None if it's read-only.

    `strict`: a mode that isn't a plain string is flagged, since it can't be checked.
    """
    mode = next((kw.value for kw in call.keywords if kw.arg == "mode"), None)
    from_keyword = mode is not None
    if mode is None and len(call.args) > position:
        mode = call.args[position]
    if mode is None:
        return None  # default mode: read
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        if not from_keyword and not strict and not _MODE_LIKE.fullmatch(mode.value):
            return None  # not a mode at all, e.g. a zip member's name
        if set(mode.value) & WRITE_MODE_CHARS:
            return f"with mode {mode.value!r}"
        return None
    if strict or from_keyword:
        return "with a mode that isn't a plain string, so it can't be checked"
    return None


def _os_open_writes(call: ast.Call) -> bool:
    flags = next((kw.value for kw in call.keywords if kw.arg == "flags"), None)
    if flags is None and len(call.args) > 1:
        flags = call.args[1]
    if flags is None:
        return False
    names = {n.attr for n in ast.walk(flags) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(flags) if isinstance(n, ast.Name)}
    return bool(names & OS_OPEN_WRITE_FLAGS)


# ---- the rule, applied to the engine ---------------------------------------------------


def test_engine_follows_the_write_rules() -> None:
    violations = find_violations(PACKAGE_DIR)
    assert violations == [], (
        "Only fileops may write files (CLAUDE.md rule 3). Move these writes into fileops:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_every_module_was_checked() -> None:
    modules = {p.name for p in PACKAGE_DIR.rglob("*.py")}
    assert {"cli.py", "library.py", "naming.py", "fileops.py", "state.py"} <= modules


def test_planted_write_text_is_caught(tmp_path: Path) -> None:
    """Test the test: the same check fails once a stray write appears."""
    copy = tmp_path / "musicorg"
    shutil.copytree(PACKAGE_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
    assert find_violations(copy) == []

    (copy / "scratch.py").write_text(
        "from pathlib import Path\n\n\ndef note() -> None:\n    Path('x.txt').write_text('hi')\n",
        encoding="utf-8",
    )
    assert [str(v) for v in find_violations(copy)] == ["scratch.py:5: .write_text()"]


# ---- what counts as a write ------------------------------------------------------------

HEADER = "import io, os, shutil, tempfile, dataclasses\nfrom pathlib import Path\n"

WRITES = [
    'open(p, "w")',
    'open(p, "a", encoding="utf-8")',
    'open(p, mode="xb")',
    'open(p, "r+")',
    "open(p, mode)",
    'io.open(p, "wb")',
    'os.fdopen(fd, "w")',
    "os.open(p, os.O_WRONLY | os.O_CREAT)",
    'Path(p).open("w")',
    'p.open(mode="a")',
    "os.rename(a, b)",
    "os.replace(a, b)",
    "os.remove(a)",
    "os.unlink(a)",
    "os.rmdir(a)",
    "os.makedirs(a)",
    "os.mkdir(a)",
    "from os import remove\nremove(a)",
    "import os as o\no.replace(a, b)",
    "shutil.move(a, b)",
    "shutil.copy(a, b)",
    "shutil.copy2(a, b)",
    "shutil.copyfile(a, b)",
    "shutil.copytree(a, b)",
    "shutil.rmtree(a)",
    "from shutil import move as mv\nmv(a, b)",
    'p.write_text("x")',
    'p.write_bytes(b"x")',
    "p.touch()",
    "p.unlink(missing_ok=True)",
    "p.rename(q)",
    "p.replace(q)",
    "p.rmdir()",
    "p.mkdir(parents=True)",
    "import send2trash",
    "from send2trash import send2trash\nsend2trash(p)",
    "audio.save()",
    "image.save(p)",
    "row.delete()",
    "tempfile.mkstemp()",
    "tempfile.NamedTemporaryFile()",
]


@pytest.mark.parametrize("code", WRITES)
def test_writes_are_flagged(code: str) -> None:
    assert check_module("scratch.py", HEADER + code + "\n"), code


HARMLESS = [
    "open(p)",
    'open(p, "rb")',
    'open(p, mode="r", encoding="utf-8")',
    "p.open()",
    'p.open("rb")',
    'zf.open("x.txt")',
    "os.open(p, os.O_RDONLY)",
    'text = "abc".replace("a", "b")',
    'text.replace("a", "b", 1)',
    "dataclasses.replace(info, note=None)",
    "from dataclasses import replace\nreplace(info, note=None)",
    "when.replace(tzinfo=None)",
    "shutil.disk_usage(p)",
    "shutil.which('ffmpeg')",
    "os.path.exists(p)",
    "p.read_text()",
    "from musicorg import library\nlibrary.open(root, write=True)",
    "from musicorg import fileops\nfileops.create_layout(paths)",
    "from musicorg import state\nstate.State.load(p).data",
    "def open(root, write):\n    pass\n\nopen(root, True)",
    'image.save(buffer, "JPEG")  # fileops-ok: in-memory',
    'image.save(\n    buffer,\n    "JPEG",\n)  # fileops-ok: in-memory',
]


@pytest.mark.parametrize("code", HARMLESS)
def test_harmless_calls_are_not_flagged(code: str) -> None:
    assert check_module("scratch.py", HEADER + code + "\n") == []


def test_marker_must_be_a_comment() -> None:
    code = HEADER + 'p.write_text("fileops-ok: in-memory")\n'
    assert check_module("scratch.py", code)


@pytest.mark.parametrize("module", sorted(ALLOWED_MODULES))
def test_allowed_modules(module: str) -> None:
    code = HEADER + 'p.write_text("x")\nos.replace(a, b)\n'
    assert check_module(module, code) == []
    assert check_module(f"sub/{module}", code)  # only the package's own module counts


def test_tags_may_save_once_and_nothing_else() -> None:
    assert check_module(TAGS_MODULE, HEADER + "audio.save()\n") == []
    two_saves = check_module(TAGS_MODULE, HEADER + "audio.save()\naudio.save(v2_version=3)\n")
    assert [v.what for v in two_saves] == [
        "a second .save() (tags.py may make only its one mutagen save)"
    ]
    assert check_module(TAGS_MODULE, HEADER + 'p.write_text("x")\n')
