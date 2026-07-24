#!/usr/bin/env python3
"""End-to-end demonstration of the Hdb collection pipeline using the mock
Trade Ideas backend, writing to a THROWAWAY temp database (no Windows required).

The application blocks production collection when backend=mock, so this demo
drives the collection engine directly (as the tests do) against a temp database.
Mock/simulated data is never written to a real database.

Simulates two trading days (2026-02-02, 2026-02-03) with all three sessions,
including page overlap and multi-page destructive More, then verifies and
reconciles.  Prints a human-readable summary and exits non-zero on any problem.

Run:  python examples/demo_collection.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hdb import db as dbmod, migrations, reconcile, repository as repo
from hdb.automation.mock_backend import MockBackend, MockPanel, MockScenario
from hdb.calendar_util import ExchangeCalendar
from hdb.collector import collect_range
from hdb.config import default_config
from hdb.diagnostics import enumerate_positions, run_discovery
from hdb.logging_util import StructuredLogger
from hdb.paths import new_run_id


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
    print(f"Demo workspace (throwaway temp DB): {work}\n")

    db_path = os.path.join(work, "trade_ideas.db")
    exports_root = os.path.join(work, "historical_exports")
    cfg = default_config()
    cfg.data["paths"]["exports_root"] = exports_root
    cfg.data["automation"]["backend"] = "mock"

    # 1) Migrate.
    summary = migrations.run_full_migration(db_path, os.path.join(work, "backups"))
    print("1. MIGRATE:")
    print(f"   wal={summary['wal_mode']} integrity_ok={summary['integrity_ok']} "
          f"applied={summary['applied']} version={summary['version_after']}")

    cal = ExchangeCalendar()
    scenario = build_scenario()
    backend = MockBackend(scenario=scenario,
                          screenshots_dir=os.path.join(work, "shots"))
    backend.connect()
    logger = StructuredLogger(os.path.join(work, "logs"), run_id="demo")
    logger.screenshot_provider = backend.screenshot

    # 2) Diagnostic setup + panel assignment (mock backend).
    diag = run_discovery(backend, os.path.join(work, "diagnostics"))
    print("\n2. DIAGNOSTICS:")
    print(f"   report={os.path.basename(diag['json_path'])} "
          f"panels_found={len(diag['discovery']['panels'])}")
    for entry in enumerate_positions(backend):
        cfg.assign_panel(entry["signature"]["session"], entry["position"], entry["signature"])
    print(f"   panels_assigned={cfg.panels_assigned()} "
          f"positions={cfg.panel_positions}")

    conn = dbmod.connect(db_path)
    dbmod.enable_wal(conn)
    try:
        # 3) Collect a date range (newest -> boundary) via the engine directly.
        print("\n3. COLLECT DATE RANGE (2026-02-03 -> 2026-02-02):")
        run_id = new_run_id()
        repo.create_run(conn, run_id, "date_range", date(2026, 2, 2),
                        date(2026, 2, 3), date(2026, 2, 2), backend="mock")
        results = collect_range(conn, backend, cal, cfg, logger, run_id,
                                newest=date(2026, 2, 3), boundary=date(2026, 2, 2))
        print(f"   run_id={run_id} days={len(results)}")

        # 4) Verify + reconcile.
        verify = reconcile.verify_collection(conn)
        print("\n4. VERIFY:")
        print(f"   integrity_ok={verify['integrity_ok']}")
        print(f"   counts={json.dumps(verify['counts'])}")
        print(f"   verified_dates={verify['verified_dates']}")
        print(f"   row_count_reconciliation={json.dumps(verify['row_count_reconciliation'])}")

        recon = reconcile.reconcile(conn, exports_root).as_dict()
        print("\n5. RECONCILE (DB vs on-disk parts):")
        print(f"   ok={recon['ok']} checked_parts={recon['checked_parts']} "
              f"checksum_ok={recon['checksum_ok']} "
              f"mismatches={len(recon['checksum_mismatch'])} "
              f"missing={len(recon['missing_files'])}")

        # 6) Re-collect the same range in a NEW run: dedup keeps alerts stable.
        before = reconcile.verify_collection(conn)["counts"]["alerts_flat"]
        run_id2 = new_run_id() + "_b"
        repo.create_run(conn, run_id2, "date_range", date(2026, 2, 2),
                        date(2026, 2, 3), date(2026, 2, 2), backend="mock")
        collect_range(conn, backend, cal, cfg, logger, run_id2,
                      newest=date(2026, 2, 3), boundary=date(2026, 2, 2))
        after = reconcile.verify_collection(conn)["counts"]["alerts_flat"]
        print("\n6. RE-COLLECT SAME RANGE (dedup):")
        print(f"   alerts_flat_before={before} alerts_flat_after={after} "
              f"(equal => dedup works: {before == after})")

        ok = (
            verify["integrity_ok"]
            and recon["ok"]
            and verify["verified_dates"] == ["2026-02-03", "2026-02-02"]
            and verify["row_count_reconciliation"]["balanced"]
            and before == after
        )
        print("\nRESULT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
