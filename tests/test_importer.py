"""Tests: importer lineage, dedup across overlapping pages, rejected rows, flat mirror."""

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
    assert stats.raw_inserted == 2
    assert stats.normalized_new == 2
    assert stats.sources_added == 2
    assert dbmod.row_count(conn, "alerts_raw") == 2
    assert dbmod.row_count(conn, "alerts_normalized") == 2
    assert dbmod.row_count(conn, "alert_sources") == 2
    # Lineage columns populated.
    row = conn.execute("SELECT * FROM alerts_raw LIMIT 1").fetchone()
    assert row["run_id"] == "r1"
    assert row["source_part_number"] == 1
    assert row["parser_version"]
    assert row["raw_json"]


def test_overlap_dedup_preserves_all_sources(migrated_db):
    conn, _ = migrated_db
    repo.create_run(conn, "r1", "single_session", None, D, D)
    # Page 1 and page 2 share CCC (page overlap).
    _import(conn, _csv([("15:59:00", "AAA", 10.0), ("15:57:00", "CCC", 12.0)]), "r1", 1)
    _import(conn, _csv([("15:57:00", "CCC", 12.0), ("09:30:00", "EEE", 14.0)]), "r1", 2)
    # 3 distinct normalized events (AAA, CCC, EEE), CCC has 2 sources.
    assert dbmod.row_count(conn, "alerts_normalized") == 3
    assert dbmod.row_count(conn, "alert_sources") == 4  # 2 + 2
    occ = conn.execute("SELECT occurrence_count FROM alerts_normalized WHERE symbol='CCC'").fetchone()[0]
    assert occ == 2


def test_reimport_same_file_is_idempotent(migrated_db):
    conn, _ = migrated_db
    repo.create_run(conn, "r1", "single_session", None, D, D)
    text = _csv([("15:59:00", "AAA", 10.0)])
    _import(conn, text, "r1", 1)
    stats2 = _import(conn, text, "r1", 1)  # same run/part/file/rows
    assert stats2.raw_duplicate == 1
    assert stats2.raw_inserted == 0
    assert dbmod.row_count(conn, "alerts_raw") == 1


def test_rejected_rows_recorded(migrated_db):
    conn, _ = migrated_db
    repo.create_run(conn, "r1", "single_session", None, D, D)
    # Missing symbol -> rejected.
    text = "Time,Symbol,Type,Price,Count,Volume,Id\n15:59:00,,High,10.0,1,1000,x\n"
    stats = _import(conn, text, "r1", 1)
    assert stats.rejected == 1
    assert dbmod.row_count(conn, "rejected_rows") == 1


def test_flat_mirror_populated(migrated_db):
    conn, _ = migrated_db
    repo.create_run(conn, "r1", "single_session", None, D, D)
    stats = _import(conn, _csv([("15:59:00", "AAA", 10.0)]), "r1", 1)
    assert stats.flat_inserted == 1
    assert dbmod.row_count(conn, "alerts_flat") == 1
