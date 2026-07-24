"""Tests: gated one-page export + one-More transition via the mock backend.

These exercise the diagnostic flows without any database writes and in an
isolated temp directory.
"""

from __future__ import annotations

import os
from datetime import date

from hdb.automation.mock_backend import MockBackend, MockPanel, MockScenario
from hdb.calendar_util import ExchangeCalendar
from hdb.config import default_config
from hdb.diagnostic_tests import one_more_transition, one_page_export
from hdb.logging_util import StructuredLogger

D = date(2026, 2, 2)


def _row(t, s, p=10.0):
    return {"time": t, "symbol": s, "alert_type": "High", "price": p,
            "count": 1, "volume": 1000, "event_id": f"{s}-{t}"}


def _setup(tmp_path, pages):
    cal = ExchangeCalendar()
    cfg = default_config()
    exports = os.path.join(tmp_path, "exp")
    cfg.assign_panel("NHP", 1, {"session": "NHP",
                                "column_signature": ["Time", "Symbol", "Type",
                                                     "Price", "Count", "Volume", "Id"]})
    scen = MockScenario(panels={1: MockPanel(1, "NHP", "NHP")},
                        history_pages={(1, D.isoformat()): pages},
                        explicit_no_more_after_last=True)
    backend = MockBackend(scenario=scen, screenshots_dir=os.path.join(tmp_path, "shots"))
    backend.connect()
    logger = StructuredLogger(os.path.join(tmp_path, "logs"), run_id="diag")
    logger.screenshot_provider = backend.screenshot
    return cal, cfg, backend, logger, exports


def test_one_page_exports_and_parses_without_more(tmp_path):
    pages = [[_row("15:59:00", "AAA"), _row("09:30:00", "BBB")],
             [_row("09:00:00", "CCC")]]  # a second page exists but must NOT be touched
    cal, cfg, backend, logger, exports = _setup(tmp_path, pages)
    report = one_page_export(backend, cal, cfg, logger, "NHP", D, 1, exports,
                           run_id_dir="run_x")
    assert report.ok is True
    assert report.parsed_row_count == 2
    assert report.filename == "NHP_2026-02-02_part_001.csv"
    assert report.file_size > 0
    assert report.timestamp_parse_rate == 1.0
    assert report.session_validation["ok"] is True
    assert "High" in report.alert_types
    # Only part_001 exists - More was never clicked.
    run_dir = os.path.join(exports, "2026", "2026-02-02", "NHP", "run_x")
    files = sorted(f for f in os.listdir(run_dir) if f.endswith(".csv"))
    assert files == ["NHP_2026-02-02_part_001.csv"]


def test_one_page_requires_correct_panel(tmp_path):
    pages = [[_row("15:59:00", "AAA")]]
    cal, cfg, backend, logger, exports = _setup(tmp_path, pages)
    # Point NHP at a non-existent position -> selecting fails -> not confirmed.
    cfg.assign_panel("NHP", 9, {"session": "NHP"})
    report = one_page_export(backend, cal, cfg, logger, "NHP", D, 9, exports,
                           run_id_dir="run_y")
    assert report.ok is False


def test_one_more_transition(tmp_path):
    pages = [[_row("15:59:00", "AAA"), _row("15:00:00", "BBB")],
             [_row("11:00:00", "CCC"), _row("09:30:00", "DDD")]]
    cal, cfg, backend, logger, exports = _setup(tmp_path, pages)
    first = one_page_export(backend, cal, cfg, logger, "NHP", D, 1, exports,
                          run_id_dir="run_z")
    assert first.ok is True
    more = one_more_transition(backend, cal, cfg, logger, "NHP", D, exports, "run_z", first)
    assert more.ok is True
    assert more.page_changed is True
    assert more.moved_backward is True
    assert more.second_part["filename"] == "NHP_2026-02-02_part_002.csv"
    # Both parts now exist.
    run_dir = os.path.join(exports, "2026", "2026-02-02", "NHP", "run_z")
    files = sorted(f for f in os.listdir(run_dir) if f.endswith(".csv"))
    assert files == ["NHP_2026-02-02_part_001.csv", "NHP_2026-02-02_part_002.csv"]


def test_one_more_requires_validated_first_page(tmp_path):
    pages = [[_row("15:59:00", "AAA")]]
    cal, cfg, backend, logger, exports = _setup(tmp_path, pages)
    from hdb.diagnostic_tests import PageTestReport

    bad_first = PageTestReport(ok=False, session="NHP", trading_date=D.isoformat(),
                               part_number=1, run_dir="")
    more = one_more_transition(backend, cal, cfg, logger, "NHP", D, exports, "run_q", bad_first)
    assert more.ok is False
    assert more.error == "first_page_not_validated"
