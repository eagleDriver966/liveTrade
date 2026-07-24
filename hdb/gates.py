"""Verification gates that must all pass before real collection is allowed.

Gates are persisted in ``config.json`` (not the database).  Collect Single Date,
Collect Date Range and Resume Incomplete Run stay blocked until every gate has
passed.  Gates are set only by successful real diagnostic steps.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Ordered list of gates the operator must clear (in the intended order).
GATE_KEYS = [
    "real_backend_initialized",
    "panels_assigned",
    "panels_verified",
    "history_selector_verified",
    "save_contents_verified",
    "save_as_verified",
    "one_page_exported",
    "one_more_verified",
    "one_session_reconciled",
]

GATE_LABELS = {
    "real_backend_initialized": "Real Windows backend initialized",
    "panels_assigned": "Panel positions assigned (HPRE/NHP/HPOST)",
    "panels_verified": "Panel positions visually verified",
    "history_selector_verified": "History selector verified",
    "save_contents_verified": "File -> Save Contents verified",
    "save_as_verified": "Save As dialog verified",
    "one_page_exported": "One real page exported and parsed",
    "one_more_verified": "One More transition exported and verified",
    "one_session_reconciled": "One full real session reconciled",
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gates_node(config) -> dict[str, Any]:
    return config.data.setdefault("gates", {})


def set_gate(config, key: str, passed: bool = True, save: bool = True, **meta: Any) -> None:
    if key not in GATE_KEYS:
        raise ValueError(f"Unknown gate {key!r}")
    node = _gates_node(config)
    node[key] = {"passed": bool(passed), "at": _utc_iso(), **meta}
    if save and config.path:
        config.save()


def gate_passed(config, key: str) -> bool:
    node = _gates_node(config)
    entry = node.get(key)
    return bool(entry and entry.get("passed"))


def gates_status(config) -> dict[str, Any]:
    node = _gates_node(config)
    status = {}
    for key in GATE_KEYS:
        entry = node.get(key) or {}
        status[key] = {
            "label": GATE_LABELS[key],
            "passed": bool(entry.get("passed")),
            "at": entry.get("at"),
        }
    return status


def missing_gates(config) -> list[str]:
    return [k for k in GATE_KEYS if not gate_passed(config, k)]


def all_passed(config) -> bool:
    return not missing_gates(config)
