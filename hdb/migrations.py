"""Transactional, versioned SQLite migrations.

* v1 establishes the legacy baseline (``alerts_flat``) if it does not exist, so
  a brand-new database is compatible with the historical importer while an
  existing database keeps its data untouched.
* v2 adds the historical-collection tables and source-lineage tables.

Each migration runs inside a transaction and records a row in
``schema_versions``.  Existing records are never deleted.
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


# -- migration bodies --------------------------------------------------------
def _migration_1(conn: sqlite3.Connection) -> None:
    """Legacy baseline: preserve/establish alerts_flat.

    If ``alerts_flat`` already exists (from the original app) it is left
    untouched - we never alter or drop it.  Indexes are only created on columns
    that actually exist, so a legacy table with a different shape does not break
    the migration (schema-drift safe).
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
                fingerprint TEXT,
                source_file TEXT,
                imported_at TEXT
            )
            """
        )
    columns = {c[1] for c in conn.execute("PRAGMA table_info(alerts_flat)").fetchall()}
    if "fingerprint" in columns:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alerts_flat_fp ON alerts_flat(fingerprint)"
        )
    if "trading_date" in columns:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alerts_flat_date ON alerts_flat(trading_date)"
        )


def _migration_2(conn: sqlite3.Connection) -> None:
    """Historical-collection + lineage tables."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS collection_runs (
            run_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            boundary_date TEXT,
            start_date TEXT,
            end_date TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS collection_days (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
            trading_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(run_id, trading_date)
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

        CREATE TABLE IF NOT EXISTS alerts_normalized (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_fingerprint TEXT NOT NULL UNIQUE,
            fingerprint_version TEXT NOT NULL,
            trading_date TEXT,
            alert_timestamp TEXT,
            alert_type TEXT,
            symbol TEXT,
            price_norm REAL,
            count_norm INTEGER,
            volume_norm REAL,
            source_session TEXT,
            panel_role TEXT,
            event_id TEXT,
            first_seen_run_id TEXT,
            first_seen_at TEXT,
            occurrence_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_norm_date ON alerts_normalized(trading_date);
        CREATE INDEX IF NOT EXISTS idx_norm_symbol ON alerts_normalized(symbol);

        CREATE TABLE IF NOT EXISTS alerts_raw (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
            trading_date TEXT NOT NULL,
            session TEXT NOT NULL,
            panel_position TEXT,
            source_filename TEXT NOT NULL,
            source_part_number INTEGER NOT NULL,
            source_row_number INTEGER NOT NULL,
            raw_json TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            import_timestamp TEXT NOT NULL,
            normalization_status TEXT NOT NULL DEFAULT 'PENDING',
            event_fingerprint TEXT,
            fingerprint_version TEXT,
            normalized_id INTEGER REFERENCES alerts_normalized(id),
            UNIQUE(run_id, trading_date, session, source_filename, source_row_number)
        );
        CREATE INDEX IF NOT EXISTS idx_raw_fp ON alerts_raw(event_fingerprint);
        CREATE INDEX IF NOT EXISTS idx_raw_norm ON alerts_raw(normalized_id);

        CREATE TABLE IF NOT EXISTS alert_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            normalized_id INTEGER NOT NULL REFERENCES alerts_normalized(id),
            raw_id INTEGER NOT NULL REFERENCES alerts_raw(id),
            run_id TEXT NOT NULL,
            trading_date TEXT NOT NULL,
            session TEXT NOT NULL,
            panel_position TEXT,
            source_filename TEXT NOT NULL,
            source_part_number INTEGER NOT NULL,
            source_row_number INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(normalized_id, source_filename, source_row_number)
        );
        CREATE INDEX IF NOT EXISTS idx_sources_norm ON alert_sources(normalized_id);

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
    2: ("historical collection + lineage tables", _migration_2),
}


def migrate(
    conn: sqlite3.Connection,
    target_version: int = SCHEMA_VERSION,
    logger=None,
) -> list[int]:
    """Apply pending migrations up to ``target_version`` transactionally.

    Returns the list of versions applied.
    """
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
            logger.info(
                "migration_applied", version=version, description=description
            )
    return applied


def run_full_migration(
    db_path: str,
    backups_root: str,
    logger=None,
    target_version: int = SCHEMA_VERSION,
) -> dict:
    """End-to-end safe migration: backup, WAL, integrity check, migrate.

    Returns a summary dict for reporting.
    """
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
            # Do not proceed with a corrupt database.
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
