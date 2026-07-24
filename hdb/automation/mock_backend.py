"""Deterministic Trade Ideas simulator implementing :class:`AutomationBackend`.

Used for development and integration tests on any OS.  It writes *real* CSV
files to disk when ``save_contents`` is called, simulates the destructive More
behavior (each More replaces the visible page and steps backward in time),
simulates the overwrite-confirmation dialog (never auto-approved), Save As
timeouts, wrong-panel activation, malformed CSV, empty sessions, overlapping
pages, and repeated final pages.

A "scenario" fully describes panels and per-(panel,date) history pages.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .base import (
    AutomationBackend,
    HistoryPageState,
    PanelInfo,
    SaveAsResult,
)


@dataclass
class MockPanel:
    position: int
    label: str
    session: str
    column_signature: list[str] = field(default_factory=lambda: [
        "Time", "Symbol", "Type", "Price", "Count", "Volume", "Id"
    ])
    alert_types: list[str] = field(default_factory=lambda: ["High", "Low"])


@dataclass
class MockScenario:
    """Full description of a simulated Trade Ideas session set."""

    panels: dict[int, MockPanel] = field(default_factory=dict)
    # history_pages[(position, date_iso)] = list of pages; each page is a list
    # of row dicts: {time, symbol, alert_type, price, count, volume, event_id}
    history_pages: dict[tuple[int, str], list[list[dict[str, Any]]]] = field(
        default_factory=dict
    )
    explicit_no_more_after_last: bool = True
    # Simulate replacing the last page with an identical copy when More is
    # pressed past the end (repeated final page) instead of signalling no-more.
    repeat_last_page_on_overrun: bool = False
    save_as_timeout: bool = False
    malformed_csv: bool = False
    # If set, select_panel does NOT actually switch (wrong-panel simulation).
    ignore_panel_selection: bool = False
    process_id: int = 4242
    window_title: str = "Trade Ideas Pro - Simulated"


class MockBackend(AutomationBackend):
    def __init__(
        self,
        scenario: MockScenario | None = None,
        screenshots_dir: str | None = None,
    ) -> None:
        self.scenario = scenario or MockScenario()
        self.screenshots_dir = screenshots_dir
        self._connected = False
        self._active_position: int | None = None
        self._history_key: tuple[int, str] | None = None
        self._page_index: int = 0
        self._history_open = False

    # -- lifecycle -----------------------------------------------------------
    def connect(self) -> None:
        self._connected = True

    def ensure_foreground(self) -> None:
        if not self._connected:
            self.connect()

    def process_info(self) -> dict[str, Any]:
        return {
            "process_id": self.scenario.process_id,
            "window_title": self.scenario.window_title,
            "backend": "mock",
        }

    # -- panels --------------------------------------------------------------
    def list_panels(self) -> list[PanelInfo]:
        panels = []
        for pos in sorted(self.scenario.panels):
            p = self.scenario.panels[pos]
            panels.append(
                PanelInfo(
                    position=pos,
                    label=p.label,
                    automation_id=f"panel_{pos}",
                    signature={
                        "column_signature": p.column_signature,
                        "alert_types": p.alert_types,
                        "session": p.session,
                    },
                )
            )
        return panels

    def select_panel(self, position: int) -> None:
        if position not in self.scenario.panels:
            raise KeyError(f"No panel at position {position}")
        if self.scenario.ignore_panel_selection:
            # Simulate a UI that failed to switch: keep previous (or default 0).
            if self._active_position is None:
                self._active_position = min(self.scenario.panels)
            return
        self._active_position = position

    def active_panel_signature(self) -> dict[str, Any]:
        pos = self._active_position
        if pos is None or pos not in self.scenario.panels:
            return {"active_tab_index": None, "confirmed": False}
        p = self.scenario.panels[pos]
        return {
            "selected_menu_position": pos,
            "active_tab_index": pos,
            "visible_tab_position": pos,
            "column_signature": p.column_signature,
            "alert_types": p.alert_types,
            "session": p.session,
            "screenshot_fingerprint": f"panel-{pos}-fp",
            "confirmed": True,
        }

    # -- history -------------------------------------------------------------
    def open_history(self) -> None:
        if self._active_position is None:
            raise RuntimeError("No active panel selected before open_history")
        self._history_open = True

    def select_date(self, trading_date: date) -> None:
        if self._active_position is None:
            raise RuntimeError("No active panel")
        self._history_key = (self._active_position, trading_date.isoformat())
        self._page_index = 0

    def wait_history_loaded(self, timeout: float) -> bool:
        return self._history_key is not None

    def _current_pages(self) -> list[list[dict[str, Any]]]:
        if self._history_key is None:
            return []
        return self.scenario.history_pages.get(self._history_key, [])

    def _current_page_rows(self) -> list[dict[str, Any]]:
        pages = self._current_pages()
        if not pages:
            return []
        idx = min(self._page_index, len(pages) - 1)
        return pages[idx]

    def read_page_state(self) -> HistoryPageState:
        rows = self._current_page_rows()
        pages = self._current_pages()
        overrun = self._page_index >= len(pages) if pages else True
        no_more = False
        if overrun and not self.scenario.repeat_last_page_on_overrun:
            no_more = self.scenario.explicit_no_more_after_last

        times = [r.get("time") for r in rows if r.get("time")]
        symbols = [r.get("symbol") for r in rows if r.get("symbol")]
        col_sig = []
        if self._active_position in self.scenario.panels:
            col_sig = self.scenario.panels[self._active_position].column_signature
        return HistoryPageState(
            row_count=len(rows),
            newest_timestamp=max(times) if times else None,
            oldest_timestamp=min(times) if times else None,
            first_symbol=symbols[0] if symbols else None,
            last_symbol=symbols[-1] if symbols else None,
            column_signature=col_sig,
            no_more_history=no_more,
        )

    def click_more(self) -> None:
        pages = self._current_pages()
        if self.scenario.repeat_last_page_on_overrun and self._page_index >= len(pages) - 1:
            # Stay on the last page (simulates replace-with-identical).
            self._page_index = max(0, len(pages) - 1)
            return
        self._page_index += 1

    def detect_no_more_history(self) -> bool:
        return self.read_page_state().no_more_history

    # -- export --------------------------------------------------------------
    def open_file_menu(self) -> None:
        return None

    def save_contents(self, absolute_path: str, timeout: float) -> SaveAsResult:
        if self.scenario.save_as_timeout:
            return SaveAsResult(ok=False, path=None, error="save_as_timeout")

        # Overwrite confirmation: never auto-approve.
        if os.path.exists(absolute_path):
            return SaveAsResult(
                ok=False,
                path=absolute_path,
                overwrite_prompted=True,
                error="overwrite_prompted",
            )

        os.makedirs(os.path.dirname(absolute_path), exist_ok=True)
        content = self._render_csv(self._current_page_rows())
        with open(absolute_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return SaveAsResult(ok=True, path=absolute_path)

    def _render_csv(self, rows: list[dict[str, Any]]) -> str:
        if self.scenario.malformed_csv:
            return "this is not,a valid\ntrade ideas\x00export\n\n,,,,"
        # Realistic Trade Ideas layout: preamble lines, then header, then data.
        lines = [
            "Trade Ideas Pro - History Export",
            "Generated by simulator",
            "",
            "Time,Symbol,Type,Price,Count,Volume,Id",
        ]
        for r in rows:
            lines.append(
                ",".join(
                    [
                        str(r.get("time", "")),
                        str(r.get("symbol", "")),
                        str(r.get("alert_type", "")),
                        str(r.get("price", "")),
                        str(r.get("count", "")),
                        str(r.get("volume", "")),
                        str(r.get("event_id", "")),
                    ]
                )
            )
        return "\n".join(lines) + "\n"

    def detect_overwrite_dialog(self) -> bool:
        return False

    def cancel_overwrite(self) -> None:
        return None

    # -- diagnostics ---------------------------------------------------------
    def screenshot(self, name: str) -> str | None:
        if not self.screenshots_dir:
            return None
        os.makedirs(self.screenshots_dir, exist_ok=True)
        path = os.path.join(self.screenshots_dir, f"{name}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"mock screenshot: {name}\nactive_panel={self._active_position}\n")
        return path

    def discover(self) -> dict[str, Any]:
        return {
            "process": self.process_info(),
            "panels": [p.to_dict() for p in self.list_panels()],
            "menu_hierarchy": {
                "File": ["Save Contents", "Export", "Exit"],
                "Alerts": [p.label for p in self.list_panels()],
                "History": ["Show History", "More"],
            },
            "history_controls": {"more": "btn_more", "date_picker": "date_edit"},
            "save_as_dialog": {"filename_edit": "Edit", "save_button": "Save"},
            "overwrite_dialog": {"yes": "Yes", "no": "No"},
        }
