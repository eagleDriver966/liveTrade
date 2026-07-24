"""Tests: verify_collection, reconcile (checksum drift), resume planning."""

from __future__ import annotations

import os
from datetime import date

from hdb import repository as repo
from hdb import reconcile
from hdb.status import Status

D = date(2026, 2, 2)


def _seed_part(conn, tmp_path, sha_override=None, write_file=True):
    repo.create_run(conn, "r1", "single_session", None, D, D)
    run_dir = os.path.join(tmp_path, "exp", "2026", "2026-02-02", "NHP", "run_1")
    os.makedirs(run_dir, exist_ok=True)
    fname = "NHP_2026-02-02_part_001.csv"
    path = os.path.join(run_dir, fname)
    content = "Time,Symbol,Type,Price\n15:59:00,AAA,High,1.0\n"
    if write_file:
        with open(path, "w") as fh:
            fh.write(content)
    from hdb.paths import sha256_file

    real_sha = sha256_file(path) if write_file else "deadbeef"
    repo.register_part(
        conn, "r1", D, "NHP", 1, fname, path, sha_override or real_sha, 1,
        None, None, "AAA", "AAA", None, 1, Status.VALIDATED,
    )
    return path, real_sha


def test_verify_collection_counts_and_integrity(migrated_db):
    conn, _ = migrated_db
    result = reconcile.verify_collection(conn)
    assert result["integrity_ok"] is True
    assert "alerts_flat" in result["counts"]
    assert "alert_sources" in result["counts"]
    assert "alerts_normalized" not in result["counts"]
    assert "row_count_reconciliation" in result
    assert result["row_count_reconciliation"]["balanced"] is True


def test_reconcile_checksum_ok(migrated_db, tmp_path):
    conn, _ = migrated_db
    _seed_part(conn, tmp_path)
    report = reconcile.reconcile(conn, os.path.join(tmp_path, "exp")).as_dict()
    assert report["ok"] is True
    assert report["checksum_ok"] == 1
    assert report["checksum_mismatch"] == []


def test_reconcile_detects_checksum_mismatch(migrated_db, tmp_path):
    conn, _ = migrated_db
    _seed_part(conn, tmp_path, sha_override="0" * 64)
    report = reconcile.reconcile(conn, os.path.join(tmp_path, "exp")).as_dict()
    assert report["ok"] is False
    assert len(report["checksum_mismatch"]) == 1


def test_reconcile_detects_missing_file(migrated_db, tmp_path):
    conn, _ = migrated_db
    _seed_part(conn, tmp_path, write_file=False)
    report = reconcile.reconcile(conn, os.path.join(tmp_path, "exp")).as_dict()
    assert len(report["missing_files"]) == 1


def test_verified_dates(migrated_db):
    conn, _ = migrated_db
    repo.create_run(conn, "r1", "single_date", None, D, D)
    for s in ("HPRE", "NHP", "HPOST"):
        repo.upsert_session(conn, "r1", D, s, 1, "rd", Status.VERIFIED)
    result = reconcile.verify_collection(conn)
    assert D.isoformat() in result["verified_dates"]


def test_resume_plan_lists_incomplete(migrated_db):
    conn, _ = migrated_db
    repo.create_run(conn, "r1", "single_date", None, D, D)
    repo.upsert_session(conn, "r1", D, "HPRE", 1, "rd", Status.VERIFIED)
    repo.upsert_session(conn, "r1", D, "NHP", 1, "rd2", Status.INCOMPLETE)
    plan = reconcile.resume_plan(conn)
    sessions = {p["session"] for p in plan["plan"]}
    assert "NHP" in sessions
    assert "HPRE" not in sessions  # verified sessions are not in the resume plan
