"""The library's SQLite databases (docs/LIBRARY_CONTRACT.md section 5).

- `.musicorg/index.sqlite`: a cache of what scans found. Everything in it can be rebuilt
  from the files and state.json (`musicorg index rebuild`).
- `.musicorg/queue.sqlite`: the job queue (step 09a). Not a cache: never deleted.

Under rule 3 of CLAUDE.md this module owns both files. Write opens (commands holding the
library's lock) use WAL mode, so read-only opens (commands without the lock) can read
while a writer works. Each thread gets its own connection.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import unicodedata
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any

from musicorg.errors import LibraryIndexError
from musicorg.naming import LibraryPaths

SCHEMA_VERSION = 1

_TABLES = """
CREATE TABLE sources (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    added_at TEXT NOT NULL,
    scanned_at TEXT
);
CREATE TABLE external_items (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha1_head TEXT,
    ext TEXT NOT NULL,
    codec TEXT,
    duration_s REAL,
    bitrate_kbps INTEGER,
    raw_tags_json TEXT NOT NULL DEFAULT '{}',
    parsed_artist TEXT,
    parsed_title TEXT,
    parsed_version_json TEXT NOT NULL DEFAULT '[]',
    parse_confidence REAL NOT NULL DEFAULT 0,
    parsed_json TEXT NOT NULL DEFAULT '{}',
    flags_json TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL DEFAULT 'new',
    reasons_json TEXT NOT NULL DEFAULT '[]',
    scanned_at TEXT NOT NULL,
    UNIQUE (source_id, rel_path)
);
CREATE INDEX external_items_state ON external_items (state);
CREATE TABLE library_tracks (
    rel_path TEXT PRIMARY KEY,
    musicorg_id TEXT,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    title TEXT,
    artist TEXT,
    album TEXT,
    duration_s REAL,
    source TEXT,
    source_id TEXT,
    only_copy INTEGER NOT NULL DEFAULT 0,
    origin_path TEXT
);
CREATE INDEX library_tracks_id ON library_tracks (musicorg_id);
CREATE INDEX library_tracks_origin ON library_tracks (origin_path);
CREATE TABLE search_cache (
    query_key TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    response_json TEXT NOT NULL
);
CREATE TABLE candidates (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES external_items(id) ON DELETE CASCADE,
    video_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    score REAL NOT NULL,
    reasons_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX candidates_item ON candidates (item_id);
CREATE TABLE fingerprints (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    duration_s REAL,
    fp_blob BLOB
);
"""
_TABLE_NAMES = (
    "candidates", "external_items", "sources", "library_tracks", "search_cache", "fingerprints",
)  # fmt: skip
_JSON_COLUMNS = ("raw_tags_json", "parsed_version_json", "parsed_json", "flags_json",
                 "reasons_json")  # fmt: skip


def item_id(source_id: str, rel_path: str) -> str:
    """An external item's stable id: `i_` + 16 hex characters of SHA-1 of
    `<source_id>/<rel_path>` (NFC, `/` separators). It survives a rebuild, so decisions
    in state.json reattach to the same file."""
    key = unicodedata.normalize("NFC", f"{source_id}/{rel_path}")
    return "i_" + hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


class Index:
    """index.sqlite. `write=False` opens it read-only; a missing file then reads as empty."""

    def __init__(self, paths: LibraryPaths, *, write: bool) -> None:
        self.paths = paths
        self.path = paths.index_file
        self.write = write
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        if write:
            self._prepare()

    # ---- connections -------------------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection | None:
        """This thread's connection; None for a read-only open of a missing index."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def _connect(self) -> sqlite3.Connection | None:
        try:
            if self.write:
                conn = sqlite3.connect(self.path, timeout=30, check_same_thread=True)
            else:
                if not self.path.exists():
                    return None
                uri = f"{self.path.as_uri()}?mode=ro"
                conn = sqlite3.connect(uri, uri=True, timeout=30, check_same_thread=True)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
        except sqlite3.Error as exc:
            raise _damaged(self.path, exc) from exc
        with self._lock:
            self._connections.append(conn)
        return conn

    def _prepare(self) -> None:
        conn = self.conn
        assert conn is not None
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version == 0 and not self._tables():
                with conn:
                    conn.executescript(_TABLES)
                    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                return
        except sqlite3.DatabaseError as exc:
            raise _damaged(self.path, exc) from exc
        self._check_version(version)

    def _tables(self) -> list[str]:
        conn = self.conn
        assert conn is not None
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        return [row[0] for row in rows]

    def _check_version(self, version: int) -> None:
        if version > SCHEMA_VERSION:
            raise LibraryIndexError(
                f"The library's index ({self.path}) was made by a newer version of Music "
                "Organizer. Update the engine, or run `musicorg index rebuild`."
            )
        if version < SCHEMA_VERSION:
            raise LibraryIndexError(
                f"The library's index ({self.path}) is from an older version. Run "
                "`musicorg index rebuild` to bring it up to date."
            )

    def close(self) -> None:
        with self._lock:
            connections, self._connections = self._connections, []
        for conn in connections:
            conn.close()
        self._local = threading.local()

    def __enter__(self) -> Index:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._writable()
        with conn:
            yield conn

    def _writable(self) -> sqlite3.Connection:
        if not self.write:
            raise RuntimeError("the index was opened read-only")
        conn = self.conn
        assert conn is not None
        return conn

    def _rows(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        conn = self.conn
        if conn is None:
            return []
        try:
            return conn.execute(sql, tuple(params)).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return []  # a read-only open of an index that was never set up
            raise _damaged(self.path, exc) from exc
        except sqlite3.DatabaseError as exc:
            raise _damaged(self.path, exc) from exc

    # ---- rebuilding --------------------------------------------------------------------

    def reset(self) -> None:
        """Empty the index and recreate its tables (the start of a rebuild). Only
        index.sqlite is touched; queue.sqlite never is."""
        conn = self._writable()
        with conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            for name in _TABLE_NAMES:
                conn.execute(f"DROP TABLE IF EXISTS {name}")
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            conn.executescript(_TABLES)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.execute("VACUUM")

    # ---- sources -----------------------------------------------------------------------

    def put_source(self, source_id: str, path: str, added_at: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO sources (id, path, added_at) VALUES (?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET path = excluded.path",
                (source_id, path, added_at),
            )

    def remove_source(self, source_id: str) -> int:
        """Forget a source and its items. Returns how many items went."""
        with self.transaction() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM external_items WHERE source_id = ?", (source_id,)
            ).fetchone()[0]
            conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        return int(count)

    def mark_scanned(self, source_id: str, when: str) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE sources SET scanned_at = ? WHERE id = ?", (when, source_id))

    def sources(self) -> dict[str, dict[str, Any]]:
        rows = self._rows(
            "SELECT s.id, s.path, s.added_at, s.scanned_at, COUNT(i.id) AS items "
            "FROM sources s LEFT JOIN external_items i ON i.source_id = s.id "
            "GROUP BY s.id ORDER BY s.added_at"
        )
        return {row["id"]: dict(row) for row in rows}

    # ---- external items ----------------------------------------------------------------

    def known_files(self, source_id: str) -> dict[str, tuple[str, int, int]]:
        """rel_path → (item id, size, mtime_ns) for a source's items."""
        rows = self._rows(
            "SELECT id, rel_path, size, mtime_ns FROM external_items WHERE source_id = ?",
            (source_id,),
        )
        return {row["rel_path"]: (row["id"], row["size"], row["mtime_ns"]) for row in rows}

    def put_items(self, items: Iterable[dict[str, Any]]) -> None:
        """Insert or replace items (dicts with the column names; JSON columns may be
        Python values). A replaced item loses its match candidates."""
        rows = [_to_row(item) for item in items]
        if not rows:
            return
        columns = list(rows[0])
        sql = (
            f"INSERT INTO external_items ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)}) "
            f"ON CONFLICT (id) DO UPDATE SET "
            + ", ".join(f"{c} = excluded.{c}" for c in columns if c != "id")
        )
        with self.transaction() as conn:
            conn.executemany(
                "DELETE FROM candidates WHERE item_id = ?", [(row["id"],) for row in rows]
            )
            conn.executemany(sql, [tuple(row[c] for c in columns) for row in rows])

    def delete_items(self, ids: Iterable[str]) -> int:
        batch = [(i,) for i in ids]
        if not batch:
            return 0
        with self.transaction() as conn:
            conn.executemany("DELETE FROM external_items WHERE id = ?", batch)
        return len(batch)

    def item(self, item_id: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM external_items WHERE id = ?", (item_id,))
        return _from_row(rows[0]) if rows else None

    def items(self, *, source_id: str | None = None) -> list[dict[str, Any]]:
        if source_id is None:
            rows = self._rows("SELECT * FROM external_items ORDER BY source_id, rel_path")
        else:
            rows = self._rows(
                "SELECT * FROM external_items WHERE source_id = ? ORDER BY rel_path", (source_id,)
            )
        return [_from_row(row) for row in rows]

    def set_state(self, item_id: str, state: str, reasons: list[str] | None = None) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE external_items SET state = ?, reasons_json = ? WHERE id = ?",
                (state, json.dumps(reasons or []), item_id),
            )

    def counts_by_state(self) -> dict[str, int]:
        rows = self._rows("SELECT state, COUNT(*) AS n FROM external_items GROUP BY state")
        return {row["state"]: row["n"] for row in sorted(rows, key=lambda r: r["state"])}

    def low_confidence_count(self, below: float) -> int:
        rows = self._rows(
            "SELECT COUNT(*) AS n FROM external_items WHERE parse_confidence < ?", (below,)
        )
        return int(rows[0]["n"]) if rows else 0

    def items_in_states(self, states: Iterable[str]) -> list[dict[str, Any]]:
        wanted = list(states)
        marks = ", ".join("?" for _ in wanted)
        rows = self._rows(
            f"SELECT * FROM external_items WHERE state IN ({marks}) ORDER BY source_id, rel_path",
            wanted,
        )
        return [_from_row(row) for row in rows]

    # ---- match candidates (step 06) -----------------------------------------------------

    def set_match(
        self, item_id: str, state: str, reasons: list[str], candidates: list[dict[str, Any]]
    ) -> None:
        """An item's match result: its state and reasons, and its candidates (each a dict
        with `id`, `video_id`, `score`, `reasons` and the `payload` to show), replacing any
        it had. One transaction, so an interrupted run never leaves half a result."""
        with self.transaction() as conn:
            conn.execute("DELETE FROM candidates WHERE item_id = ?", (item_id,))
            conn.executemany(
                "INSERT INTO candidates (id, item_id, video_id, payload_json, score, reasons_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        c["id"],
                        item_id,
                        c["video_id"],
                        json.dumps(c["payload"], ensure_ascii=False),
                        c["score"],
                        json.dumps(c["reasons"], ensure_ascii=False),
                    )
                    for c in candidates
                ],
            )
            conn.execute(
                "UPDATE external_items SET state = ?, reasons_json = ? WHERE id = ?",
                (state, json.dumps(reasons, ensure_ascii=False), item_id),
            )

    def candidates(self, item_id: str) -> list[dict[str, Any]]:
        """An item's candidates, best first."""
        rows = self._rows(
            "SELECT * FROM candidates WHERE item_id = ? ORDER BY score DESC, rowid", (item_id,)
        )
        return [
            {
                "id": row["id"],
                "video_id": row["video_id"],
                "payload": json.loads(row["payload_json"]),
                "score": row["score"],
                "reasons": json.loads(row["reasons_json"]),
            }
            for row in rows
        ]

    def all_candidates(self) -> dict[str, list[dict[str, Any]]]:
        """Every item's candidates, best first, by item id (one query for a report)."""
        rows = self._rows("SELECT * FROM candidates ORDER BY item_id, score DESC, rowid")
        found: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            found.setdefault(row["item_id"], []).append({
                "id": row["id"],
                "video_id": row["video_id"],
                "payload": json.loads(row["payload_json"]),
                "score": row["score"],
                "reasons": json.loads(row["reasons_json"]),
            })  # fmt: skip
        return found

    # ---- search cache (step 06) ---------------------------------------------------------

    def cached_search(self, key: str, *, max_age_days: float) -> Any | None:
        """A raw search response saved under `key`, if it's younger than `max_age_days`."""
        rows = self._rows(
            "SELECT fetched_at, response_json FROM search_cache WHERE query_key = ?", (key,)
        )
        if not rows:
            return None
        try:
            fetched = datetime.fromisoformat(rows[0]["fetched_at"])
        except ValueError:
            return None
        if datetime.now(UTC) - fetched > timedelta(days=max_age_days):
            return None
        return json.loads(rows[0]["response_json"])

    def put_search(self, key: str, response: Any) -> None:
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO search_cache (query_key, fetched_at, response_json) "
                "VALUES (?, ?, ?)",
                (key, now, json.dumps(response, ensure_ascii=False)),
            )

    # ---- library tracks ----------------------------------------------------------------

    def put_library_tracks(self, tracks: Iterable[dict[str, Any]]) -> None:
        rows = list(tracks)
        if not rows:
            return
        columns = list(rows[0])
        sql = (
            f"INSERT OR REPLACE INTO library_tracks ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})"
        )
        with self.transaction() as conn:
            conn.executemany(sql, [tuple(row[c] for c in columns) for row in rows])

    def library_tracks(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._rows("SELECT * FROM library_tracks ORDER BY rel_path")]

    def library_track_count(self) -> int:
        rows = self._rows("SELECT COUNT(*) AS n FROM library_tracks")
        return int(rows[0]["n"]) if rows else 0


def _to_row(item: dict[str, Any]) -> dict[str, Any]:
    row = dict(item)
    for column in _JSON_COLUMNS:
        if column in row and not isinstance(row[column], str):
            row[column] = json.dumps(row[column], ensure_ascii=False)
    return row


def _from_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for column in _JSON_COLUMNS:
        if isinstance(item.get(column), str):
            item[column] = json.loads(item[column])
    return item


def _damaged(path: Path, exc: Exception) -> LibraryIndexError:
    return LibraryIndexError(
        f"The library's index ({path}) can't be read ({exc}). It's only a cache: run "
        "`musicorg index rebuild` to make it again. Your music and decisions are safe."
    )


def open_index(paths: LibraryPaths, *, write: bool) -> Index:
    """The library's index. Open it for writing only while holding the library's lock."""
    return Index(paths, write=write)
