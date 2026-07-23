"""Reconciliation, verification, and resume planning.

* ``verify_collection`` summarizes DB state (counts, per-date session status,
  VERIFIED dates) and runs an integrity check.
* ``reconcile`` cross-checks on-disk parts/manifests against the DB and
  re-validates checksums, reporting any drift.
* ``find_incomplete`` / ``resume_plan`` support run/date/session/part recovery.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from . import db as dbmod
from .manifest import Manifest
from .paths import sha256_file
from .status import SESSION_DONE, Status


@dataclass
class ReconcileReport:
    checked_parts: int = 0
    checksum_ok: int = 0
    checksum_mismatch: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    db_only_parts: list[str] = field(default_factory=list)
    disk_only_parts: list[str] = field(default_factory=list)
    manifest_discrepancies: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "checked_parts": self.checked_parts,
            "checksum_ok": self.checksum_ok,
            "checksum_mismatch": self.checksum_mismatch,
            "missing_files": self.missing_files,
            "db_only_parts": self.db_only_parts,
            "disk_only_parts": self.disk_only_parts,
            "manifest_discrepancies": self.manifest_discrepancies,
            "ok": not (
                self.checksum_mismatch
                or self.missing_files
                or self.manifest_discrepancies
            ),
        }


def verify_collection(conn) -> dict[str, Any]:
    ok, messages = dbmod.integrity_check(conn)
    summary: dict[str, Any] = {
        "integrity_ok": ok,
        "integrity_messages": messages,
        "counts": {},
        "dates": {},
        "verified_dates": [],
    }
    for table in (
        "collection_runs", "collection_days", "collection_sessions",
        "history_export_parts", "alerts_raw", "alerts_normalized",
        "alert_sources", "rejected_rows", "alerts_flat",
    ):
        summary["counts"][table] = dbmod.row_count(conn, table)

    rows = conn.execute(
        """SELECT trading_date, session, status FROM collection_sessions
           ORDER BY trading_date DESC, session"""
    ).fetchall()
    by_date: dict[str, dict[str, str]] = {}
    for r in rows:
        by_date.setdefault(r["trading_date"], {})[r["session"]] = r["status"]
    summary["dates"] = by_date

    for d, sess in by_date.items():
        try:
            done = all(
                Status(sess.get(s, "PENDING")) in SESSION_DONE
                for s in ("HPRE", "NHP", "HPOST")
            )
        except ValueError:
            done = False
        if done:
            summary["verified_dates"].append(d)
    return summary


def reconcile(conn, exports_root: str) -> ReconcileReport:
    report = ReconcileReport()

    db_parts = conn.execute(
        "SELECT abs_path, sha256, run_id, trading_date, session, part_number "
        "FROM history_export_parts"
    ).fetchall()
    db_paths = set()
    for r in db_parts:
        report.checked_parts += 1
        path = r["abs_path"]
        db_paths.add(os.path.normpath(path) if path else path)
        if not path or not os.path.exists(path):
            report.missing_files.append(path or "<null>")
            continue
        actual = sha256_file(path)
        if actual == r["sha256"]:
            report.checksum_ok += 1
        else:
            report.checksum_mismatch.append(path)

    # Disk-only parts + manifest cross-check.
    if os.path.isdir(exports_root):
        for root, _dirs, files in os.walk(exports_root):
            csvs = [f for f in files if f.lower().endswith(".csv")]
            for f in csvs:
                full = os.path.normpath(os.path.join(root, f))
                if full not in db_paths:
                    report.disk_only_parts.append(full)
            manifest_path = os.path.join(root, "manifest.json")
            if os.path.exists(manifest_path):
                _check_manifest(manifest_path, csvs, report)
    return report


def _check_manifest(manifest_path: str, csvs: list[str], report: ReconcileReport) -> None:
    try:
        manifest = Manifest.load(manifest_path)
    except Exception as exc:
        report.manifest_discrepancies.append(f"{manifest_path}:load_failed:{exc}")
        return
    listed = {p.filename for p in manifest.parts}
    present = set(csvs)
    for missing in listed - present:
        report.manifest_discrepancies.append(f"{manifest_path}:missing_on_disk:{missing}")
    # Verify checksums recorded in the manifest.
    base = os.path.dirname(manifest_path)
    for part in manifest.parts:
        fpath = os.path.join(base, part.filename)
        if os.path.exists(fpath):
            if sha256_file(fpath) != part.sha256:
                report.manifest_discrepancies.append(
                    f"{manifest_path}:checksum_mismatch:{part.filename}"
                )


def find_incomplete(conn) -> dict[str, Any]:
    """Return runs/days/sessions that are not in a completed state (for resume)."""
    incomplete_sessions = conn.execute(
        """SELECT run_id, trading_date, session, status, run_dir, part_count
           FROM collection_sessions
           WHERE status NOT IN ('VERIFIED','EMPTY_VERIFIED')
           ORDER BY trading_date DESC"""
    ).fetchall()
    incomplete_days = conn.execute(
        """SELECT run_id, trading_date, status FROM collection_days
           WHERE status NOT IN ('VERIFIED')
           ORDER BY trading_date DESC"""
    ).fetchall()
    return {
        "sessions": [dict(r) for r in incomplete_sessions],
        "days": [dict(r) for r in incomplete_days],
    }


def resume_plan(conn) -> dict[str, Any]:
    """Describe the safe next actions for incomplete work.

    Sessions that failed *after* a validated part but *before* More can resume
    from the next part when the visible page can be re-verified; sessions that
    failed after More but before export must start a NEW run directory (never
    overwrite an earlier run) and dedupe on import.
    """
    inc = find_incomplete(conn)
    plan: list[dict[str, Any]] = []
    for s in inc["sessions"]:
        status = s["status"]
        if status == Status.INCOMPLETE.value:
            action = "start_new_run_dir_and_recollect"  # safe default
        elif status in (Status.COLLECTING.value, Status.FAILED.value):
            action = "restart_session_new_run_dir"
        else:
            action = "review"
        plan.append(
            {
                "run_id": s["run_id"],
                "trading_date": s["trading_date"],
                "session": s["session"],
                "status": status,
                "existing_run_dir": s["run_dir"],
                "recommended_action": action,
            }
        )
    return {"plan": plan, "incomplete": inc}
