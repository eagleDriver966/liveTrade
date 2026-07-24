"""Tests: production collection is blocked when backend=mock (safety)."""

from __future__ import annotations

import os
from datetime import date

import pytest

from hdb import db as dbmod, migrations
from hdb.app import HdbApp, ProductionBlockedError


def _mock_app(tmp_path) -> HdbApp:
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
    app.config.data["automation"]["backend"] = "mock"
    app.config.save(cfg_path)
    app.migrate()
    return app


def test_collect_single_date_blocked_with_mock(tmp_path):
    app = _mock_app(tmp_path)
    conn = app.connect_db()
    try:
        with pytest.raises(ProductionBlockedError):
            app.collect_single_date(conn, date(2026, 2, 2))
        # Nothing was written to the real database.
        assert dbmod.row_count(conn, "alerts_flat") == 0
        assert dbmod.row_count(conn, "collection_runs") == 0
    finally:
        conn.close()


def test_collect_range_blocked_with_mock(tmp_path):
    app = _mock_app(tmp_path)
    conn = app.connect_db()
    try:
        with pytest.raises(ProductionBlockedError):
            app.collect_date_range(conn, newest=date(2026, 2, 3), boundary=date(2026, 2, 2))
        assert dbmod.row_count(conn, "alerts_flat") == 0
    finally:
        conn.close()


def test_resume_blocked_with_mock(tmp_path):
    app = _mock_app(tmp_path)
    conn = app.connect_db()
    try:
        with pytest.raises(ProductionBlockedError):
            app.resume_incomplete(conn)
    finally:
        conn.close()


def test_real_backend_name_allows_guard_to_pass(tmp_path):
    app = _mock_app(tmp_path)
    app.config.data["automation"]["backend"] = "pywinauto"
    # Guard itself must not raise for a non-mock backend.
    app._require_real_backend()
