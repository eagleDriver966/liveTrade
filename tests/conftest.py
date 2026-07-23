"""Shared pytest fixtures."""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest

# Ensure repo root is importable when pytest is run from anywhere.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hdb import db as dbmod  # noqa: E402
from hdb import migrations, repository as repo  # noqa: E402
from hdb.calendar_util import ExchangeCalendar  # noqa: E402
from hdb.config import default_config  # noqa: E402
from hdb.logging_util import StructuredLogger  # noqa: E402


@pytest.fixture
def calendar() -> ExchangeCalendar:
    return ExchangeCalendar()


@pytest.fixture
def workdir(tmp_path):
    return tmp_path


@pytest.fixture
def migrated_db(tmp_path):
    db_path = os.path.join(tmp_path, "trade_ideas.db")
    migrations.run_full_migration(db_path, os.path.join(tmp_path, "backups"))
    conn = dbmod.connect(db_path)
    dbmod.enable_wal(conn)
    yield conn, db_path
    conn.close()


@pytest.fixture
def config(tmp_path):
    cfg = default_config()
    cfg.data["paths"]["exports_root"] = os.path.join(tmp_path, "historical_exports")
    cfg.data["paths"]["database"] = os.path.join(tmp_path, "trade_ideas.db")
    cfg.data["paths"]["backups_root"] = os.path.join(tmp_path, "backups")
    cfg.data["paths"]["logs_root"] = os.path.join(tmp_path, "logs")
    cfg.data["paths"]["screenshots_root"] = os.path.join(tmp_path, "shots")
    cfg.path = os.path.join(tmp_path, "config.json")
    return cfg


@pytest.fixture
def logger(tmp_path):
    return StructuredLogger(os.path.join(tmp_path, "logs"), run_id="test")


def make_row(t, s, price, alert_type="High", count=1, volume=1000, event_id=None):
    return {
        "time": t,
        "symbol": s,
        "alert_type": alert_type,
        "price": price,
        "count": count,
        "volume": volume,
        "event_id": event_id or f"{s}-{t}",
    }
