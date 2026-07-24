#!/usr/bin/env python3
"""Demonstrate the gated diagnostic workflow using the mock backend.

Shows the backend status, the "Test One Page Export" report (all display
fields), and the "Test One More Transition" comparison - driven by the mock
simulator against a throwaway temp directory (no Windows, no DB writes).

On Windows with backend='pywinauto', the same flow runs against the real Trade
Ideas app via the application (Hdb.py test-one-page / test-one-more), gated so
collection stays blocked until every verification gate passes.

Run:  python examples/demo_diagnostic.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hdb import gates
from hdb.automation.mock_backend import MockBackend, MockPanel, MockScenario
from hdb.backend_status import determine_backend_status
from hdb.calendar_util import ExchangeCalendar
from hdb.config import default_config
from hdb.diagnostic_tests import one_more_transition, one_page_export
from hdb.logging_util import StructuredLogger

D = date(2026, 2, 2)


def _row(t, s, p):
    return {"time": t, "symbol": s, "alert_type": "High", "price": p,
            "count": 3, "volume": 250000, "event_id": f"{s}-{t}"}


def main() -> int:
    work = tempfile.mkdtemp(prefix="hdb_diag_")
    print(f"Workspace (throwaway): {work}\n")

    print("1. BACKEND STATUS")
    print(f"   real (this OS): {determine_backend_status('pywinauto').label}")
    print(f"   mock         : {determine_backend_status('mock').label}")

    cal = ExchangeCalendar()
    cfg = default_config()
    exports = os.path.join(work, "historical_exports")
    cfg.assign_panel("NHP", 1, {"session": "NHP",
                                "column_signature": ["Time", "Symbol", "Type",
                                                     "Price", "Count", "Volume", "Id"]})
    pages = [
        [_row("15:59:00", "AAPL", 191.2), _row("15:00:00", "MSFT", 410.5)],
        [_row("11:00:00", "TSLA", 240.1), _row("09:30:00", "NVDA", 720.0)],
    ]
    scen = MockScenario(panels={1: MockPanel(1, "Regular (NHP)", "NHP")},
                        history_pages={(1, D.isoformat()): pages},
                        explicit_no_more_after_last=True)
    backend = MockBackend(scenario=scen, screenshots_dir=os.path.join(work, "shots"))
    backend.connect()
    logger = StructuredLogger(os.path.join(work, "logs"), run_id="diag")
    logger.screenshot_provider = backend.screenshot

    print("\n2. TEST ONE PAGE EXPORT (no More, no import)")
    r1 = one_page_export(backend, cal, cfg, logger, "NHP", D, 1, exports, run_id_dir="run_demo")
    for k in ("filename", "file_size", "parsed_row_count", "newest_timestamp",
              "oldest_timestamp", "alert_types", "symbols_sample", "symbol_count",
              "timestamp_parse_rate"):
        print(f"   {k}: {getattr(r1, k)}")
    print(f"   session_validation.ok: {r1.session_validation.get('ok')}")

    print("\n3. TEST ONE MORE TRANSITION (part_002, then stop)")
    r2 = one_more_transition(backend, cal, cfg, logger, "NHP", D, exports, "run_demo", r1)
    print(f"   part_002: {r2.second_part.get('filename')}")
    print(f"   page_changed={r2.page_changed} moved_backward={r2.moved_backward} "
          f"new_fingerprints={r2.new_fingerprints} ok={r2.ok}")
    print(f"   first oldest={r2.first_part.get('oldest_timestamp')} -> "
          f"second oldest={r2.second_part.get('oldest_timestamp')}")

    print("\n4. GATES (simulated after successful diagnostics)")
    for g in ("real_backend_initialized", "panels_assigned", "panels_verified",
              "history_selector_verified", "save_contents_verified",
              "save_as_verified", "one_page_exported", "one_more_verified"):
        gates.set_gate(cfg, g, save=False)
    print(f"   passed so far: {[k for k in gates.GATE_KEYS if gates.gate_passed(cfg, k)]}")
    print(f"   still blocking collection: {gates.missing_gates(cfg)}")

    ok = r1.ok and r2.ok and r2.moved_backward
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
