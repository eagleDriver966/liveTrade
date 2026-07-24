"""Transactional, versioned SQLite migrations.

Scope (intentionally minimal):

* v1 - establish/preserve the legacy ``alerts_flat`` table (the alert store).
* v2 - add ONLY the collection-tracking tables required to build and expand the
  database safely: ``collection_runs``, ``collection_sessions``,
  ``history_export_parts``, ``alert_sources``, ``rejected_rows``.  It also adds a
  ``session`` column to ``alerts_flat`` if missing (never dropping data).

Each migration runs inside a transaction and records a row in
``schema_versions``.  Existing records are never deleted; an existing
``alerts_flat`` is never altered destructively.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Callable

from . import db as dbmod
from .version import APP_VERSION, SCHEMA_VERSION


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema_versions_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_versions (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT,
            app_version TEXT
        )
        """
    )
    conn.commit()


def current_version(conn: sqlite3.Connection) -> int:
    ensure_schema_versions_table(conn)
    row = conn.execute("SELECT MAX(version) FROM schema_versions").fetchone()
    return row[0] or 0


def _record_version(conn: sqlite3.Connection, version: int, description: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO schema_versions (version, applied_at, description, app_version)"
        " VALUES (?, ?, ?, ?)",
        (version, _utc_iso(), description, APP_VERSION),
    )


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()}


# -- migration bodies --------------------------------------------------------
def _migration_1(conn: sqlite3.Connection) -> None:
    """Legacy baseline: preserve/establish alerts_flat (the alert store).

    If ``alerts_flat`` already exists it is left untouched.  Indexes are only
    created on columns that exist (schema-drift safe).
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alerts_flat'"
    ).fetchone()
    if not exists:
        conn.execute(
            """
            CREATE TABLE alerts_flat (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                alert_type TEXT,
                alert_time TEXT,
                price REAL,
                alert_count INTEGER,
                volume REAL,
                trading_date TEXT,
                session TEXT,
                fingerprint TEXT,
                fingerprint_version TEXT,
                source_file TEXT,
                imported_at TEXT
            )
            """
        )
    columns = _table_columns(conn, "alerts_flat")
    if "fingerprint" in columns:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alerts_flat_fp ON alerts_flat(fingerprint)"
        )
    if "trading_date" in columns:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alerts_flat_date ON alerts_flat(trading_date)"
        )


def _migration_2(conn: sqlite3.Connection) -> None:
    """Add the minimal collection-tracking tables (and alerts_flat.session)."""
    # Ensure alerts_flat has the columns the importer needs, without dropping
    # anything on a pre-existing legacy table.
    columns = _table_columns(conn, "alerts_flat")
    for col, decl in (
        ("session", "TEXT"),
        ("fingerprint", "TEXT"),
        ("fingerprint_version", "TEXT"),
        ("alert_count", "INTEGER"),
        ("volume", "REAL"),
        ("source_file", "TEXT"),
        ("imported_at", "TEXT"),
    ):
        if col not in columns:
            conn.execute(f"ALTER TABLE alerts_flat ADD COLUMN {col} {decl}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_alerts_flat_fp ON alerts_flat(fingerprint)"
    )

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS collection_runs (
            run_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            boundary_date TEXT,
            start_date TEXT,
            end_date TEXT,
            backend TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS collection_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
            trading_date TEXT NOT NULL,
            session TEXT NOT NULL,
            panel_position TEXT,
            run_dir TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            completion_reason TEXT,
            completion_signals TEXT,
            part_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(run_id, trading_date, session, run_dir)
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_date ON collection_sessions(trading_date, session);

        CREATE TABLE IF NOT EXISTS history_export_parts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
            trading_date TEXT NOT NULL,
            session TEXT NOT NULL,
            part_number INTEGER NOT NULL,
            filename TEXT NOT NULL,
            abs_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            row_count INTEGER NOT NULL DEFAULT 0,
            newest_timestamp TEXT,
            oldest_timestamp TEXT,
            first_symbol TEXT,
            last_symbol TEXT,
            page_fingerprint TEXT,
            panel_position TEXT,
            status TEXT NOT NULL DEFAULT 'EXPORTED',
            validated_at TEXT,
            imported_at TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, trading_date, session, part_number)
        );
        CREATE INDEX IF NOT EXISTS idx_parts_sha ON history_export_parts(sha256);
        CREATE INDEX IF NOT EXISTS idx_parts_pagefp ON history_export_parts(page_fingerprint);

        CREATE TABLE IF NOT EXISTS alert_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_flat_id INTEGER NOT NULL REFERENCES alerts_flat(id),
            fingerprint TEXT NOT NULL,
            run_id TEXT NOT NULL,
            trading_date TEXT NOT NULL,
            session TEXT NOT NULL,
            panel_position TEXT,
            source_filename TEXT NOT NULL,
            source_part_number INTEGER NOT NULL,
            source_row_number INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, source_filename, source_row_number)
        );
        CREATE INDEX IF NOT EXISTS idx_sources_alert ON alert_sources(alert_flat_id);
        CREATE INDEX IF NOT EXISTS idx_sources_fp ON alert_sources(fingerprint);

        CREATE TABLE IF NOT EXISTS rejected_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            trading_date TEXT,
            session TEXT,
            source_filename TEXT,
            source_part_number INTEGER,
            source_row_number INTEGER,
            reason TEXT NOT NULL,
            raw_json TEXT,
            created_at TEXT NOT NULL
        );
        """
    )


MIGRATIONS: dict[int, tuple[str, Callable[[sqlite3.Connection], None]]] = {
    1: ("legacy baseline: alerts_flat", _migration_1),
    2: ("collection tracking tables", _migration_2),
}


def migrate(
    conn: sqlite3.Connection,
    target_version: int = SCHEMA_VERSION,
    logger=None,
) -> list[int]:
    """Apply pending migrations up to ``target_version`` transactionally."""
    ensure_schema_versions_table(conn)
    applied: list[int] = []
    start = current_version(conn)
    for version in sorted(MIGRATIONS):
        if version <= start or version > target_version:
            continue
        description, body = MIGRATIONS[version]
        with dbmod.transaction(conn):
            body(conn)
            _record_version(conn, version, description)
        applied.append(version)
        if logger:
            logger.info("migration_applied", version=version, description=description)
    return applied


def run_full_migration(
    db_path: str,
    backups_root: str,
    logger=None,
    target_version: int = SCHEMA_VERSION,
) -> dict:
    """End-to-end safe migration: backup, WAL, integrity check, migrate."""
    summary: dict = {
        "db_path": db_path,
        "backup_path": None,
        "wal_mode": None,
        "integrity_ok": None,
        "integrity_messages": [],
        "version_before": None,
        "version_after": None,
        "applied": [],
        "pre_migration_inspection": None,
    }

    backup_path = dbmod.backup_database(db_path, backups_root)
    summary["backup_path"] = backup_path
    if logger:
        logger.info("migration_backup", backup_path=backup_path)

    conn = dbmod.connect(db_path)
    try:
        summary["wal_mode"] = dbmod.enable_wal(conn)
        ok, messages = dbmod.integrity_check(conn)
        summary["integrity_ok"] = ok
        summary["integrity_messages"] = messages
        if not ok:
            if logger:
                logger.error("integrity_check_failed", messages=messages)
            raise RuntimeError(f"integrity_check failed: {messages}")

        summary["pre_migration_inspection"] = dbmod.inspect(conn)
        summary["version_before"] = current_version(conn)
        summary["applied"] = migrate(conn, target_version=target_version, logger=logger)
        summary["version_after"] = current_version(conn)
    finally:
        conn.close()
    return summary
