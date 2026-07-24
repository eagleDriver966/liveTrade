"""Tests: separated real/mock gate namespaces + config-version invalidation."""

from __future__ import annotations

import pytest

from hdb import gates


def test_real_and_mock_namespaces_are_separate(config):
    gates.set_mock_gate(config, "mock_backend_initialized", save=False)
    assert gates.mock_gate_passed(config, "mock_backend_initialized") is True
    # No real gate is set as a side effect.
    assert gates.real_gate_entry(config, "real_backend_initialized") is None


def test_set_real_gate_requires_process_signature(config):
    with pytest.raises(ValueError):
        gates.set_real_gate(config, "real_page_export_verified", "", save=False)


def test_real_gate_valid_requires_matching_process(config):
    gates.set_real_gate(config, "real_page_export_verified", "1234:Trade Ideas",
                        save=False)
    assert gates.real_gate_valid(config, "real_page_export_verified", "1234:Trade Ideas")
    # Different live process signature -> invalid.
    assert not gates.real_gate_valid(config, "real_page_export_verified", "9999:Other")


def test_config_version_bump_invalidates_real_gates(config):
    gates.set_real_gate(config, "real_panel_assignments_verified", "1:TI", save=False)
    assert gates.real_gate_valid(config, "real_panel_assignments_verified", "1:TI")
    gates.bump_config_version(config, save=False)
    # After a config change (e.g. reassignment) the real gate no longer counts.
    assert not gates.real_gate_valid(config, "real_panel_assignments_verified", "1:TI")
    assert gates.real_gate_entry(config, "real_panel_assignments_verified") is None


def test_mock_gate_names_are_not_real_gate_names():
    assert set(gates.MOCK_GATE_KEYS).isdisjoint(set(gates.REAL_GATE_KEYS))
    for k in gates.MOCK_GATE_KEYS:
        assert k.startswith("mock_")
    for k in gates.REAL_GATE_KEYS:
        assert k.startswith("real_")


def test_set_mock_gate_rejects_real_name(config):
    with pytest.raises(ValueError):
        gates.set_mock_gate(config, "real_backend_initialized", save=False)


def test_set_real_gate_rejects_mock_name(config):
    with pytest.raises(ValueError):
        gates.set_real_gate(config, "mock_backend_initialized", "1:TI", save=False)
