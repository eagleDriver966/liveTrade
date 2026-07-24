"""Verification gates, split into strictly separated namespaces.

There are two independent namespaces so a mock workflow can NEVER set, satisfy,
emulate or persist a production (real) gate:

* ``real_*`` gates - persisted only via :func:`set_real_gate`, which records the
  live Trade Ideas process signature and the current configuration version.
  Callers must only invoke it while a genuine pywinauto connection is active.
* ``mock_*`` gates - persisted via :func:`set_mock_gate`; used exclusively by the
  mock test workflow.  They never appear in, and never satisfy, production
  eligibility.

Production eligibility itself is computed live in :mod:`hdb.production_gate`;
these persisted gates are only part of the evidence and are invalidated whenever
the configuration version changes (e.g. panel reassignment).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Production (real) gates - guarded; require a live real connection to set.
REAL_GATE_KEYS = [
    "real_backend_initialized",
    "real_panel_assignments_verified",
    "real_page_export_verified",
    "real_more_transition_verified",
    "real_session_reconciled",
]

REAL_GATE_LABELS = {
    "real_backend_initialized": "Real Windows backend initialized (live connection)",
    "real_panel_assignments_verified": "Real panel assignments visually verified",
    "real_page_export_verified": "Real one-page export verified",
    "real_more_transition_verified": "Real More transition verified",
    "real_session_reconciled": "Real full session reconciled",
}

# Mock gates - separate names; never reused in production checks.
MOCK_GATE_KEYS = [
    "mock_backend_initialized",
    "mock_panels_verified",
    "mock_page_export_verified",
    "mock_more_transition_verified",
    "mock_session_reconciled",
]

MOCK_GATE_LABELS = {
    "mock_backend_initialized": "Mock backend initialized (TEST ONLY)",
    "mock_panels_verified": "Mock panels verified (TEST ONLY)",
    "mock_page_export_verified": "Mock one-page export (TEST ONLY)",
    "mock_more_transition_verified": "Mock More transition (TEST ONLY)",
    "mock_session_reconciled": "Mock session reconciled (TEST ONLY)",
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- configuration version ---------------------------------------------------
def get_config_version(config) -> int:
    return int(config.data.get("config_version", 1))


def bump_config_version(config, save: bool = True) -> int:
    """Increment the configuration version and invalidate ALL real gates.

    Any change to panel assignments must invalidate prior real verifications so
    they cannot be reused against a different configuration.
    """
    new_version = get_config_version(config) + 1
    config.data["config_version"] = new_version
    config.data["real_gates"] = {}  # invalidate real evidence
    if save and config.path:
        config.save()
    return new_version


# -- real gates (guarded) ----------------------------------------------------
def set_real_gate(
    config, key: str, process_signature: str, save: bool = True, **meta: Any
) -> None:
    """Persist a real gate with the live process signature + config version.

    NOTE: callers MUST only call this while a genuine real connection is active.
    A ``process_signature`` is required and must be non-empty.
    """
    if key not in REAL_GATE_KEYS:
        raise ValueError(f"Unknown real gate {key!r}")
    if not process_signature:
        raise ValueError("set_real_gate requires a live process_signature")
    node = config.data.setdefault("real_gates", {})
    node[key] = {
        "passed": True,
        "at": _utc_iso(),
        "config_version": get_config_version(config),
        "process_signature": process_signature,
        **meta,
    }
    if save and config.path:
        config.save()


def real_gate_entry(config, key: str) -> dict[str, Any] | None:
    return (config.data.get("real_gates", {}) or {}).get(key)


def real_gate_valid(config, key: str, current_process_signature: str | None) -> bool:
    """A real gate counts only if it passed in the CURRENT config version and
    (when a live signature is supplied) matches the CURRENT real process."""
    entry = real_gate_entry(config, key)
    if not entry or not entry.get("passed"):
        return False
    if entry.get("config_version") != get_config_version(config):
        return False
    if current_process_signature is not None:
        if entry.get("process_signature") != current_process_signature:
            return False
    return True


# -- mock gates --------------------------------------------------------------
def set_mock_gate(config, key: str, save: bool = True, **meta: Any) -> None:
    if key not in MOCK_GATE_KEYS:
        raise ValueError(f"Unknown mock gate {key!r}")
    node = config.data.setdefault("mock_gates", {})
    node[key] = {"passed": True, "at": _utc_iso(), **meta}
    if save and config.path:
        config.save()


def mock_gate_passed(config, key: str) -> bool:
    return bool((config.data.get("mock_gates", {}) or {}).get(key, {}).get("passed"))


# -- status snapshots --------------------------------------------------------
def real_gates_status(config, current_process_signature: str | None = None) -> dict[str, Any]:
    out = {}
    for key in REAL_GATE_KEYS:
        entry = real_gate_entry(config, key) or {}
        out[key] = {
            "label": REAL_GATE_LABELS[key],
            "passed": real_gate_valid(config, key, current_process_signature),
            "raw_passed": bool(entry.get("passed")),
            "config_version": entry.get("config_version"),
            "process_signature": entry.get("process_signature"),
            "at": entry.get("at"),
        }
    return out


def mock_gates_status(config) -> dict[str, Any]:
    out = {}
    for key in MOCK_GATE_KEYS:
        entry = (config.data.get("mock_gates", {}) or {}).get(key, {})
        out[key] = {
            "label": MOCK_GATE_LABELS[key],
            "passed": bool(entry.get("passed")),
            "at": entry.get("at"),
        }
    return out
