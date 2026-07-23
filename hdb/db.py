"""SQLite connection management and low-level helpers.

Responsibilities:

* open connections with sane pragmas (foreign keys, row factory);
* enable WAL mode;
* run ``PRAGMA integrity_check``;
* create timestamped backups before migrations;
* inspect existing tables/columns/indexes/row counts.

No business logic lives here.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator


def connect(path: str, timeout: float = 30.0) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def enable_wal(conn: sqlite3.Connection) -> str:
    mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    conn.execute("PRAGMA synchronous = NORMAL")
    return mode


def integrity_check(conn: sqlite3.Connection) -> tuple[bool, list[str]]:
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    messages = [r[0] for r in rows]
    ok = messages == ["ok"]
    return ok, messages


def backup_database(db_path: str, backups_root: str) -> str | None:
    """Create a timestamped copy of the database (and WAL/SHM if present).

    Returns the backup path, or None if the source does not exist yet.
    """
    if not os.path.exists(db_path):
        return None
    os.makedirs(backups_root, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.basename(db_path)
    backup_path = os.path.join(backups_root, f"{base}.{stamp}.bak")

    # Use SQLite's online backup API for a consistent snapshot.
    src = connect(db_path)
    try:
        dst = sqlite3.connect(backup_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return backup_path


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def table_columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [dict(r) for r in rows]


def table_indexes(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    rows = conn.execute(f"PRAGMA index_list({table})").fetchall()
    return [dict(r) for r in rows]


def row_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.Error:
        return -1


def inspect(conn: sqlite3.Connection) -> dict[str, Any]:
    """Full structural inspection used by the inspection/reconciliation report."""
    result: dict[str, Any] = {"tables": {}}
    for table in list_tables(conn):
        result["tables"][table] = {
            "columns": table_columns(conn, table),
            "indexes": table_indexes(conn, table),
            "row_count": row_count(conn, table),
        }
    return result


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit transaction context: commit on success, rollback on error."""
    try:
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
