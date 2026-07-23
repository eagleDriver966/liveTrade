"""Tests: importing already-exported CSV trees + restart recovery via re-import."""

from __future__ import annotations

import os
from datetime import date

from hdb import db as dbmod, repository as repo
from hdb.calendar_util import ExchangeCalendar
from hdb.import_existing import import_export_tree, parse_part_filename

D = date(2026, 2, 2)


def test_parse_part_filename():
    m = parse_part_filename("HPRE_2026-02-02_part_003.csv")
    assert m["session"] == "HPRE"
    assert m["trading_date"] == D
    assert m["part_number"] == 3
    assert parse_part_filename("random.csv") is None


def _write_part(root, session, part, rows):
    run_dir = os.path.join(root, "2026", "2026-02-02", session, "run_1")
    os.makedirs(run_dir, exist_ok=True)
    fname = f"{session}_2026-02-02_part_{part:03d}.csv"
    with open(os.path.join(run_dir, fname), "w") as fh:
        fh.write("Time,Symbol,Type,Price,Count,Volume,Id\n")
        for t, s in rows:
            fh.write(f"{t},{s},High,1.0,1,1000,{s}-{t}\n")
    return run_dir


def test_import_export_tree(migrated_db, tmp_path):
    conn, _ = migrated_db
    root = os.path.join(tmp_path, "exp")
    _write_part(root, "NHP", 1, [("15:59:00", "AAA"), ("15:00:00", "BBB")])
    _write_part(root, "NHP", 2, [("10:00:00", "CCC"), ("09:30:00", "DDD")])
    cal = ExchangeCalendar()
    summary = import_export_tree(conn, root, cal, "imp1")
    assert summary.files == 2
    assert dbmod.row_count(conn, "alerts_normalized") == 4
    assert dbmod.row_count(conn, "alerts_raw") == 4


def test_reimport_tree_is_idempotent(migrated_db, tmp_path):
    conn, _ = migrated_db
    root = os.path.join(tmp_path, "exp")
    _write_part(root, "NHP", 1, [("15:59:00", "AAA")])
    cal = ExchangeCalendar()
    import_export_tree(conn, root, cal, "imp1")
    import_export_tree(conn, root, cal, "imp2")  # second run, same files
    # Raw dedupe keys on run_id so a new run re-adds raw rows, but normalized
    # dedupe keeps a single event.
    assert dbmod.row_count(conn, "alerts_normalized") == 1
