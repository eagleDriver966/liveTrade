#!/usr/bin/env python3
"""End-to-end demonstration of the Hdb collection pipeline using the mock
Trade Ideas backend (no Windows required).

Simulates two trading days (2026-02-02, 2026-02-03) with all three sessions,
including page overlap and multi-page destructive More, then imports, verifies
and reconciles.  Prints a human-readable summary and exits non-zero on any
integrity problem.

Run:  python examples/demo_collection.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hdb.app import HdbApp
from hdb.automation.mock_backend import MockBackend, MockPanel, MockScenario


def row(t, s, p, at="High"):
    return {"time": t, "symbol": s, "alert_type": at, "price": p,
            "count": 1, "volume": 100000, "event_id": f"{s}-{t}"}


def build_scenario() -> MockScenario:
    panels = {
        0: MockPanel(0, "Pre-Market (HPRE)", "HPRE"),
        1: MockPanel(1, "Regular (NHP)", "NHP"),
        2: MockPanel(2, "After-Hours (HPOST)", "HPOST"),
    }
    pages: dict = {}
    for d in ("2026-02-02", "2026-02-03"):
        # HPRE (04:00-09:29): two pages, backward.
        pages[(0, d)] = [
            [row("09:29:00", "PREA", 5.0), row("08:00:00", "PREB", 6.0)],
            [row("08:00:00", "PREB", 6.0), row("04:00:00", "PREC", 7.0)],  # overlap
        ]
        # NHP (09:30-16:00): three pages, backward to open.
        pages[(1, d)] = [
            [row("15:59:00", "AAA", 10.0), row("15:00:00", "BBB", 11.0)],
            [row("14:00:00", "CCC", 12.0), row("11:00:00", "DDD", 13.0)],
            [row("11:00:00", "DDD", 13.0), row("09:30:00", "EEE", 14.0)],  # overlap
        ]
        # HPOST (16:00-20:00): one page.
        pages[(2, d)] = [
            [row("19:59:00", "PSTA", 20.0), row("16:00:00", "PSTB", 21.0)],
        ]
    return MockScenario(panels=panels, history_pages=pages,
                        explicit_no_more_after_last=True)


def main() -> int:
    work = tempfile.mkdtemp(prefix="hdb_demo_")
    print(f"Demo workspace: {work}\n")
    config_path = os.path.join(work, "config.json")

    app = HdbApp(config_path=config_path, echo_logs=False)
    app.config.data["paths"] = {
        "database": os.path.join(work, "trade_ideas.db"),
        "exports_root": os.path.join(work, "historical_exports"),
        "logs_root": os.path.join(work, "logs"),
        "screenshots_root": os.path.join(work, "logs", "screenshots"),
        "backups_root": os.path.join(work, "backups"),
        "diagnostics_root": os.path.join(work, "diagnostics"),
    }
    app.config.data["automation"]["backend"] = "mock"
    app.config.save(config_path)

    # 1) Migrate.
    summary = app.migrate()
    print("1. MIGRATE:")
    print(f"   wal={summary['wal_mode']} integrity_ok={summary['integrity_ok']} "
          f"applied={summary['applied']} version={summary['version_after']}")

    # 2) Diagnostic setup + panel assignment (mock backend).
    scenario = build_scenario()
    backend = MockBackend(scenario=scenario,
                          screenshots_dir=app.config.data["paths"]["screenshots_root"])
    app.set_backend(backend)
    diag = app.run_diagnostics()
    print("\n2. DIAGNOSTICS:")
    print(f"   report={os.path.basename(diag['json_path'])} "
          f"panels_found={len(diag['discovery']['panels'])}")
    for entry in app.enumerate_panel_positions():
        app.assign_panel(entry["signature"]["session"], entry["position"], entry["signature"])
    print(f"   readiness={app.readiness()['ready']} "
          f"positions={app.config.panel_positions}")

    # 3) Collect a date range (newest -> boundary).
    conn = app.connect_db()
    try:
        print("\n3. COLLECT DATE RANGE (2026-02-03 -> 2026-02-02):")
        res = app.collect_date_range(conn, newest=date(2026, 2, 3),
                                     boundary=date(2026, 2, 2))
        print(f"   run_id={res['run_id']} days={res['days']}")

        # 4) Verify + reconcile.
        verify = app.verify(conn)
        print("\n4. VERIFY:")
        print(f"   integrity_ok={verify['integrity_ok']}")
        print(f"   counts={json.dumps(verify['counts'])}")
        print(f"   verified_dates={verify['verified_dates']}")

        recon = app.reconcile(conn)
        print("\n5. RECONCILE (DB vs on-disk parts):")
        print(f"   ok={recon['ok']} checked_parts={recon['checked_parts']} "
              f"checksum_ok={recon['checksum_ok']} "
              f"mismatches={len(recon['checksum_mismatch'])} "
              f"missing={len(recon['missing_files'])}")

        # 6) Re-import existing exports (idempotency check).
        before = verify["counts"]["alerts_normalized"]
        app.import_existing(conn)
        after = app.verify(conn)["counts"]["alerts_normalized"]
        print("\n6. RE-IMPORT EXISTING EXPORTS (idempotency):")
        print(f"   normalized_before={before} normalized_after={after} "
              f"(equal => dedup works: {before == after})")

        ok = (
            verify["integrity_ok"]
            and recon["ok"]
            and verify["verified_dates"] == ["2026-02-03", "2026-02-02"]
            and before == after
        )
        print("\nRESULT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        conn.close()
        # Leave workspace for inspection; comment out to auto-clean.
        # shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
