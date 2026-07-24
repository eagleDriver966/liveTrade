"""Tests: importer dedup into alerts_flat, alert_sources lineage, rejected rows."""

from __future__ import annotations

from datetime import date

from hdb import db as dbmod, repository as repo
from hdb.csv_parser import parse_csv_text
from hdb.importer import import_parsed_part

D = date(2026, 2, 2)


def _csv(rows):
    header = "Time,Symbol,Type,Price,Count,Volume,Id\n"
    body = "".join(
        f"{t},{s},High,{p},1,1000,{s}-{t}\n" for (t, s, p) in rows
    )
    return header + body


def _import(conn, text, run_id, part, session="NHP"):
    parsed = parse_csv_text(text, D)
    return import_parsed_part(conn, parsed, run_id, D, session, 1, part, f"{session}_{part}.csv")


def test_basic_import_lineage(migrated_db):
    conn, _ = migrated_db
    repo.create_run(conn, "r1", "single_session", None, D, D)
    stats = _import(conn, _csv([("15:59:00", "AAA", 10.0), ("15:58:00", "BBB", 11.0)]), "r1", 1)
    assert stats.alerts_inserted == 2
    assert stats.sources_added == 2
    assert dbmod.row_count(conn, "alerts_flat") == 2
    assert dbmod.row_count(conn, "alert_sources") == 2
    # Lineage columns populated.
    row = conn.execute("SELECT * FROM alert_sources LIMIT 1").fetchone()
    assert row["run_id"] == "r1"
    assert row["source_part_number"] == 1
    assert row["source_row_number"] >= 1
    assert row["source_filename"].endswith(".csv")
    # alerts_flat has session + fingerprint.
    a = conn.execute("SELECT * FROM alerts_flat LIMIT 1").fetchone()
    assert a["session"] == "NHP"
    assert a["fingerprint"]


def test_overlap_dedup_preserves_all_sources(migrated_db):
    conn, _ = migrated_db
    repo.create_run(conn, "r1", "single_session", None, D, D)
    # Page 1 and page 2 share CCC (page overlap).
    _import(conn, _csv([("15:59:00", "AAA", 10.0), ("15:57:00", "CCC", 12.0)]), "r1", 1)
    _import(conn, _csv([("15:57:00", "CCC", 12.0), ("09:30:00", "EEE", 14.0)]), "r1", 2)
    # 3 distinct alerts (AAA, CCC, EEE); 4 source occurrences (CCC twice).
    assert dbmod.row_count(conn, "alerts_flat") == 3
    assert dbmod.row_count(conn, "alert_sources") == 4
    ccc_sources = conn.execute(
        """SELECT COUNT(*) FROM alert_sources s JOIN alerts_flat a
           ON a.id = s.alert_flat_id WHERE a.symbol='CCC'"""
    ).fetchone()[0]
    assert ccc_sources == 2


def test_reimport_same_file_is_idempotent(migrated_db):
    conn, _ = migrated_db
    repo.create_run(conn, "r1", "single_session", None, D, D)
    text = _csv([("15:59:00", "AAA", 10.0)])
    _import(conn, text, "r1", 1)
    stats2 = _import(conn, text, "r1", 1)  # same run/part/file/rows
    assert stats2.sources_duplicate == 1
    assert stats2.sources_added == 0
    assert dbmod.row_count(conn, "alerts_flat") == 1
    assert dbmod.row_count(conn, "alert_sources") == 1


def test_rejected_rows_recorded(migrated_db):
    conn, _ = migrated_db
    repo.create_run(conn, "r1", "single_session", None, D, D)
    # Missing symbol -> rejected.
    text = "Time,Symbol,Type,Price,Count,Volume,Id\n15:59:00,,High,10.0,1,1000,x\n"
    stats = _import(conn, text, "r1", 1)
    assert stats.rejected == 1
    assert dbmod.row_count(conn, "rejected_rows") == 1
    assert dbmod.row_count(conn, "alerts_flat") == 0


def test_alerts_flat_dedup_across_runs(migrated_db):
    conn, _ = migrated_db
    repo.create_run(conn, "r1", "single_session", None, D, D)
    repo.create_run(conn, "r2", "single_session", None, D, D)
    text = _csv([("15:59:00", "AAA", 10.0)])
    _import(conn, text, "r1", 1)
    _import(conn, text, "r2", 1)  # different run re-collecting same alert
    # One distinct alert, but two source occurrences (one per run).
    assert dbmod.row_count(conn, "alerts_flat") == 1
    assert dbmod.row_count(conn, "alert_sources") == 2
