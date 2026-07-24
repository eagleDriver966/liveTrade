"""Tests: collection stays blocked until all verification gates pass."""

from __future__ import annotations

import os
from datetime import date

import pytest

from hdb import gates
from hdb.app import GatesNotPassedError, HdbApp, ProductionBlockedError


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


def test_collect_blocked_when_gates_missing(tmp_path):
    # Non-mock backend passes the mock guard but must still be blocked by gates.
    app = _app(tmp_path, backend="pywinauto")
    conn = app.connect_db()
    try:
        with pytest.raises(GatesNotPassedError):
            app.collect_single_date(conn, date(2026, 2, 2))
        with pytest.raises(GatesNotPassedError):
            app.collect_date_range(conn, newest=date(2026, 2, 3), boundary=date(2026, 2, 2))
        with pytest.raises(GatesNotPassedError):
            app.resume_incomplete(conn)
    finally:
        conn.close()


def test_collect_allowed_after_all_gates(tmp_path, monkeypatch):
    app = _app(tmp_path, backend="pywinauto")
    for key in gates.GATE_KEYS:
        gates.set_gate(app.config, key, save=False)

    # Stub the actual collection so we only prove the gate check passes.
    called = {}

    def fake_collect_date(conn, backend, cal, cfg, logger, run_id, td, control, **kw):
        called["ran"] = True
        return {}

    monkeypatch.setattr("hdb.app.collect_date", fake_collect_date)
    # Avoid building the real pywinauto backend.
    monkeypatch.setattr(app, "backend", lambda: object())

    conn = app.connect_db()
    try:
        app.collect_single_date(conn, date(2026, 2, 2))
        assert called.get("ran") is True
    finally:
        conn.close()


def test_mock_backend_blocks_before_gate_check(tmp_path):
    app = _app(tmp_path, backend="mock")
    conn = app.connect_db()
    try:
        # Mock guard fires first (ProductionBlockedError), regardless of gates.
        with pytest.raises(ProductionBlockedError):
            app.collect_single_date(conn, date(2026, 2, 2))
    finally:
        conn.close()


def test_mock_uses_separate_db_and_exports(tmp_path):
    app = _app(tmp_path, backend="mock")
    assert app.effective_database_path().endswith(".mocktest.db")
    assert app.effective_exports_root().endswith("_mocktest")
