"""Tests: OS detection + backend status (REAL / MOCK / ERROR)."""

from __future__ import annotations

from hdb.backend_status import (
    CODE_ERROR,
    CODE_MOCK,
    LABEL_ERROR,
    LABEL_MOCK,
    determine_backend_status,
)


def test_mock_status():
    s = determine_backend_status("mock")
    assert s.code == CODE_MOCK
    assert s.label == LABEL_MOCK
    assert s.ready is False  # not production-ready


def test_pywinauto_on_non_windows_is_error():
    # This test suite runs on Linux; the real backend must report an error with
    # an exact reason and never fall back to mock.
    s = determine_backend_status("pywinauto")
    assert s.code == CODE_ERROR
    assert s.label == LABEL_ERROR
    assert s.ready is False
    assert "requires Windows" in s.message


def test_unknown_backend_is_error():
    s = determine_backend_status("banana")
    assert s.code == CODE_ERROR
    assert "Unknown automation backend" in s.message


def test_no_probe_pywinauto_windows_path_reports_not_probed(monkeypatch):
    import hdb.backend_status as bs

    monkeypatch.setattr(bs.platform, "system", lambda: "Windows")
    s = determine_backend_status("pywinauto", probe=False)
    assert s.code == CODE_ERROR
    assert "not probed" in s.message.lower()


def test_probe_failure_surfaces_exact_reason(monkeypatch):
    # Simulate Windows but make the backend connect() fail; the exact reason must
    # be surfaced and the status must be ERROR (never a silent mock fallback).
    import hdb.backend_status as bs

    monkeypatch.setattr(bs.platform, "system", lambda: "Windows")

    class _Boom:
        def __init__(self, *a, **k):
            pass

        def connect(self):
            raise RuntimeError("Trade Ideas not found")

    import hdb.automation.pywinauto_backend as pb

    monkeypatch.setattr(pb, "PywinautoBackend", _Boom)
    s = determine_backend_status("pywinauto", probe=True)
    assert s.code == CODE_ERROR
    assert "Trade Ideas not found" in s.message
