"""Configuration loading and defaults.

Configuration is a JSON file (see ``config.template.json``).  Unknown keys are
preserved so forward-compatible options are not lost when re-saving.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from typing import Any

# The three sessions collected independently, in chronological order.
SESSIONS = ("HPRE", "NHP", "HPOST")

DEFAULT_CONFIG: dict[str, Any] = {
    "timezone": "America/New_York",
    "exchange_calendar": "XNYS",
    # Requested historical boundary.  Feb 1 2026 is a Sunday; collection begins
    # at the first valid trading day on or after this date.
    "history_boundary_date": "2026-02-01",
    # Session clock boundaries (local exchange time).  NHP end is derived from
    # the exchange calendar's official close (handles early-close days).
    "sessions": {
        "HPRE": {"start": "04:00:00", "end": "09:29:59"},
        "NHP": {"start": "09:30:00", "end": "market_close"},
        "HPOST": {"start": "market_close", "end": "20:00:00"},
    },
    # Which Trade Ideas panel (by menu tab position) supplies each session.
    # Determined during diagnostic setup; must not be assumed.
    "panel_positions": {"HPRE": None, "NHP": None, "HPOST": None},
    # Signatures captured during diagnostic assignment, keyed by session.
    "panel_signatures": {"HPRE": None, "NHP": None, "HPOST": None},
    "paths": {
        "database": "trade_ideas.db",
        "exports_root": "historical_exports",
        "logs_root": "logs",
        "screenshots_root": "logs/screenshots",
        "backups_root": "backups",
        "diagnostics_root": "diagnostics",
    },
    "collection": {
        # Do not collect a trading day until the final session has ended plus
        # this many seconds of safety delay.
        "current_day_safety_delay_seconds": 3600,
        # A page with fewer than this many rows is never conclusive on its own.
        "min_conclusive_rows": 1000,
        # Max destructive More pages per session before forcing INCOMPLETE.
        "max_pages_per_session": 500,
        # Require at least this many completion signals when deciding a session
        # is finished (except explicit no-more-history).
        "min_completion_signals": 2,
    },
    "timeouts": {
        "history_load_seconds": 30,
        "save_as_dialog_seconds": 20,
        "file_settle_seconds": 10,
        "more_load_seconds": 30,
        "file_settle_poll_interval": 0.5,
    },
    "automation": {
        "backend": "pywinauto",  # or "mock" for development/tests
        "ui_backend": "uia",
        "app_title_re": ".*Trade Ideas.*",
        # Coordinate fallbacks are opt-in only and isolated from business logic.
        "allow_coordinate_fallback": False,
        "coordinate_fallbacks": {},
        "screenshot_before_click": True,
    },
    "safety": {
        # Hard guardrails; changing these does not enable trading actions - the
        # code contains no order/trade endpoints at all.
        "trading_actions_enabled": False,
        "auto_approve_overwrite": False,
    },
}


@dataclass
class Config:
    """In-memory configuration wrapper around a plain dict."""

    data: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULT_CONFIG))
    path: str | None = None

    # -- accessors -----------------------------------------------------------
    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set(self, value: Any, *keys: str) -> None:
        node = self.data
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value

    @property
    def panel_positions(self) -> dict[str, Any]:
        return self.data.setdefault("panel_positions", {})

    def panel_position(self, session: str) -> Any:
        return self.panel_positions.get(session)

    def assign_panel(self, session: str, position: Any, signature: Any = None) -> None:
        if session not in SESSIONS:
            raise ValueError(f"Unknown session: {session!r}")
        self.panel_positions[session] = position
        self.data.setdefault("panel_signatures", {})[session] = signature

    def panels_assigned(self) -> bool:
        return all(self.panel_position(s) is not None for s in SESSIONS)

    # -- persistence ---------------------------------------------------------
    def save(self, path: str | None = None) -> str:
        target = path or self.path
        if not target:
            raise ValueError("No config path provided")
        tmp = f"{target}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=False)
        os.replace(tmp, target)
        self.path = target
        return target


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: str | None) -> Config:
    """Load config from ``path``, merging over defaults.  Missing file yields
    defaults (not written to disk automatically)."""
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            user_data = json.load(fh)
        merged = _deep_merge(DEFAULT_CONFIG, user_data)
        return Config(data=merged, path=path)
    return Config(data=copy.deepcopy(DEFAULT_CONFIG), path=path)


def default_config() -> Config:
    return Config(data=copy.deepcopy(DEFAULT_CONFIG))
