"""Tests: verification gates persisted in config."""

from __future__ import annotations

from hdb import gates


def test_gates_start_all_missing(config):
    assert gates.all_passed(config) is False
    assert set(gates.missing_gates(config)) == set(gates.GATE_KEYS)


def test_set_and_query_gate(config):
    gates.set_gate(config, "real_backend_initialized", save=False)
    assert gates.gate_passed(config, "real_backend_initialized") is True
    assert "real_backend_initialized" not in gates.missing_gates(config)


def test_unknown_gate_rejected(config):
    import pytest

    with pytest.raises(ValueError):
        gates.set_gate(config, "not_a_gate", save=False)


def test_all_passed_when_every_gate_set(config):
    for key in gates.GATE_KEYS:
        gates.set_gate(config, key, save=False)
    assert gates.all_passed(config) is True
    assert gates.missing_gates(config) == []


def test_status_has_labels(config):
    status = gates.gates_status(config)
    for key in gates.GATE_KEYS:
        assert status[key]["label"]
        assert status[key]["passed"] is False
