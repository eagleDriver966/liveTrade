"""Tests: hard production gate blocks collection; mock can never unlock it."""

from __future__ import annotations

import os
from datetime import date

import pytest

from hdb import gates
from hdb.app import GatesNotPassedError, HdbApp, ProductionBlockedError
from hdb.automation.mock_backend import MockBackend, MockPanel, MockScenario

D = date(2026, 2, 2)


def _app(tmp_path, backend="pywinauto") -> HdbApp:
    cfg_path = os.path.join(tmp_path, "config.json")
    app = HdbApp(config_path=cfg_path, echo_logs=False)
    app.config.data["paths"] = {
        "database": os.path.join(tmp_path, "trade_ideas.db"),
        "exports_root": os.path.join(tmp_path, "exp"),
        "logs_root": os.path.join(tmp_path, "logs"),
        "screenshots_root": os.path.join(tmp_path, "shots"),
        "backups_root": os.path.join(tmp_path, "backups"),
        "diagnostics_root": os.path.join(tmp_path, "diag"),
    }
    app.config.data["automation"]["backend"] = backend
    app.config.save(cfg_path)
    app.migrate()
    return app


def test_collect_blocked_by_production_gate_on_non_windows(tmp_path):
    # Even with backend=pywinauto, this Linux CI can never be production-eligible.
    app = _app(tmp_path, backend="pywinauto")
    assert app.production_eligibility()["eligible"] is False
    conn = app.connect_db()
    try:
        with pytest.raises(ProductionBlockedError):
            app.collect_single_date(conn, D)
        with pytest.raises(ProductionBlockedError):
            app.collect_date_range(conn, newest=date(2026, 2, 3), boundary=D)
        with pytest.raises(ProductionBlockedError):
            app.resume_incomplete(conn)
    finally:
        conn.close()


def test_mock_backend_blocks_collection(tmp_path):
    app = _app(tmp_path, backend="mock")
    conn = app.connect_db()
    try:
        with pytest.raises(ProductionBlockedError):
            app.collect_single_date(conn, D)
    finally:
        conn.close()


def test_mock_uses_separate_db_and_exports(tmp_path):
    app = _app(tmp_path, backend="mock")
    assert app.effective_database_path().endswith(".mocktest.db")
    assert app.effective_exports_root().endswith("_mocktest")


def test_completing_every_mock_workflow_cannot_enable_production(tmp_path):
    """REGRESSION: run the full mock workflow and prove production stays blocked
    and no real_* gate is ever set."""
    app = _app(tmp_path, backend="mock")

    # Full mock diagnostic workflow via the mock backend.
    scen = MockScenario(
        panels={0: MockPanel(0, "HPRE", "HPRE"), 1: MockPanel(1, "NHP", "NHP"),
                2: MockPanel(2, "HPOST", "HPOST")},
    )
    backend = MockBackend(scenario=scen, screenshots_dir=os.path.join(tmp_path, "shots"))
    backend.connect()
    app.set_backend(backend)

    app.backend_status()                         # sets mock_backend_initialized only
    for e in app.enumerate_panel_positions():
        app.assign_panel(e["signature"]["session"], e["position"], e["signature"])
    for s in ("HPRE", "NHP", "HPOST"):
        app.verify_panel(s, confirm=True)        # sets mock_panels_verified only

    # Directly set EVERY mock gate to simulate a fully "passed" mock run.
    for k in gates.MOCK_GATE_KEYS:
        gates.set_mock_gate(app.config, k, save=False)

    # No real gate may exist, and production must be ineligible.
    for k in gates.REAL_GATE_KEYS:
        assert gates.real_gate_entry(app.config, k) is None, f"{k} must not be set by mock"
    assert app.production_eligibility()["eligible"] is False

    conn = app.connect_db()
    try:
        with pytest.raises(ProductionBlockedError):
            app.collect_single_date(conn, D)
        with pytest.raises(ProductionBlockedError):
            app.collect_date_range(conn)
        with pytest.raises(ProductionBlockedError):
            app.resume_incomplete(conn)
        # Real production DB is untouched by the mock workflow.
        assert not os.path.exists(app.config.get("paths", "database"))
    finally:
        conn.close()


def test_mock_test_actions_are_blocked_at_app_level(tmp_path):
    app = _app(tmp_path, backend="mock")
    # The real one-page / one-more diagnostic tests refuse to run under mock.
    with pytest.raises(ProductionBlockedError):
        app.test_one_page("NHP", D)
    with pytest.raises(ProductionBlockedError):
        app.test_one_more("NHP", D)


def test_eligibility_report_lists_all_conditions(tmp_path):
    app = _app(tmp_path, backend="mock")
    report = app.production_eligibility()
    # 13 hard conditions + the final verdict line.
    assert len(report["conditions"]) == 13
    assert report["report"][-1] == "Production collection eligible: NO"
    for cond in report["conditions"]:
        assert cond["evidence"]  # every condition cites an evidence source
