"""Tests: cancel/pause/resume primitives and diagnostic discovery/assignment."""

from __future__ import annotations

import os

import pytest

from hdb.automation.mock_backend import MockBackend, MockPanel, MockScenario
from hdb.control import CancelledError, CollectionControl
from hdb.diagnostics import (
    assign_panel,
    enumerate_positions,
    readiness,
    run_discovery,
)


def test_control_cancel():
    c = CollectionControl()
    assert c.is_cancelled() is False
    c.request_cancel()
    assert c.is_cancelled() is True
    with pytest.raises(CancelledError):
        c.check_cancel()


def test_control_pause_resume():
    c = CollectionControl()
    c.request_pause()
    assert c.is_paused() is True
    c.resume()
    assert c.is_paused() is False


def test_control_cancel_unpauses():
    c = CollectionControl()
    c.request_pause()
    c.request_cancel()
    assert c.is_paused() is False  # cancel clears pause so worker can exit


def _scenario():
    return MockScenario(
        panels={
            0: MockPanel(0, "Pre Market", "HPRE"),
            1: MockPanel(1, "Regular", "NHP"),
            2: MockPanel(2, "After Hours", "HPOST"),
        }
    )


def test_enumerate_positions(tmp_path):
    backend = MockBackend(scenario=_scenario(),
                          screenshots_dir=os.path.join(tmp_path, "shots"))
    entries = enumerate_positions(backend)
    assert [e["position"] for e in entries] == [0, 1, 2]
    assert entries[0]["signature"]["session"] == "HPRE"
    assert entries[0]["screenshot"] is not None


def test_run_discovery_writes_reports(tmp_path):
    backend = MockBackend(scenario=_scenario(),
                          screenshots_dir=os.path.join(tmp_path, "shots"))
    report = run_discovery(backend, os.path.join(tmp_path, "diag"))
    assert os.path.exists(report["json_path"])
    assert os.path.exists(report["text_path"])
    assert "panels" in report["discovery"]


def test_assign_and_readiness(tmp_path, config):
    r = readiness(config)
    assert r["ready"] is False
    assign_panel(config, "HPRE", 0, {"session": "HPRE"})
    assign_panel(config, "NHP", 1, {"session": "NHP"})
    assign_panel(config, "HPOST", 2, {"session": "HPOST"})
    r2 = readiness(config)
    assert r2["ready"] is True
    assert config.panels_assigned() is True
