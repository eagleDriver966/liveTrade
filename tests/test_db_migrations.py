"""Tests: transactional migrations, backup, WAL, integrity, alerts_flat preserved."""

from __future__ import annotations

import os
import sqlite3

from hdb import db as dbmod
from hdb import migrations


def test_fresh_migration_creates_all_tables(tmp_path):
    db_path = os.path.join(tmp_path, "t.db")
    summary = migrations.run_full_migration(db_path, os.path.join(tmp_path, "backups"))
    assert summary["wal_mode"] == "wal"
    assert summary["integrity_ok"] is True
    assert summary["applied"] == [1, 2]
    conn = dbmod.connect(db_path)
    tables = set(dbmod.list_tables(conn))
    expected = {
        "alerts_flat", "collection_runs", "collection_sessions",
        "history_export_parts", "alert_sources", "rejected_rows", "schema_versions",
    }
    assert expected <= tables
    # The simplified scope must NOT create these tables.
    assert "alerts_normalized" not in tables
    assert "alerts_raw" not in tables
    assert "collection_days" not in tables
    conn.close()


def test_migration_is_idempotent(tmp_path):
    db_path = os.path.join(tmp_path, "t.db")
    migrations.run_full_migration(db_path, os.path.join(tmp_path, "backups"))
    summary2 = migrations.run_full_migration(db_path, os.path.join(tmp_path, "backups"))
    assert summary2["applied"] == []
    assert summary2["version_after"] == 2


def test_backup_created_and_data_preserved(tmp_path):
    db_path = os.path.join(tmp_path, "t.db")
    migrations.run_full_migration(db_path, os.path.join(tmp_path, "backups"))
    conn = dbmod.connect(db_path)
    conn.execute(
        "INSERT INTO alerts_flat (symbol, alert_type, alert_time, price, trading_date, fingerprint)"
        " VALUES ('AAA','High','2026-02-02T10:00:00',1.0,'2026-02-02','fp1')"
    )
    conn.commit()
    conn.close()

    # Re-run migration: existing data must be preserved and a backup created.
    summary = migrations.run_full_migration(db_path, os.path.join(tmp_path, "backups"))
    assert summary["backup_path"] is not None
    assert os.path.exists(summary["backup_path"])

    conn = dbmod.connect(db_path)
    assert dbmod.row_count(conn, "alerts_flat") == 1
    conn.close()


def test_transaction_rollback_on_error(tmp_path):
    db_path = os.path.join(tmp_path, "t.db")
    conn = dbmod.connect(db_path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    conn.commit()
    try:
        with dbmod.transaction(conn):
            conn.execute("INSERT INTO t (id) VALUES (1)")
            conn.execute("INSERT INTO t (id) VALUES (1)")  # duplicate PK -> error
    except sqlite3.IntegrityError:
        pass
    # The first insert must have been rolled back.
    assert dbmod.row_count(conn, "t") == 0
    conn.close()


def test_schema_versions_recorded(tmp_path):
    db_path = os.path.join(tmp_path, "t.db")
    migrations.run_full_migration(db_path, os.path.join(tmp_path, "backups"))
    conn = dbmod.connect(db_path)
    rows = conn.execute("SELECT version FROM schema_versions ORDER BY version").fetchall()
    assert [r[0] for r in rows] == [1, 2]
    conn.close()
