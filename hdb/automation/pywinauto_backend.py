"""Real Trade Ideas automation via ``pywinauto`` (UI Automation backend).

This module imports ``pywinauto`` lazily so the package still imports on
Linux/macOS for development and testing.  It cannot be exercised in the Linux
CI/dev environment; every selector here MUST be verified on the user's Windows
machine during diagnostic mode (see docs/UNRESOLVED_SELECTORS.md).

Design rules honoured:
* prefer accessible controls (UIA) over coordinates;
* coordinate fallbacks are opt-in, DPI/resolution-aware, isolated, and
  screenshotted before clicking;
* never approve an overwrite dialog - cancel and report instead.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime
from typing import Any

from .base import (
    AutomationBackend,
    AutomationError,
    HistoryPageState,
    PanelInfo,
    SaveAsResult,
)


def _require_pywinauto():
    try:
        import pywinauto  # noqa: F401
        from pywinauto import Application, Desktop  # noqa: F401
        from pywinauto.keyboard import send_keys  # noqa: F401
    except Exception as exc:  # pragma: no cover - Windows-only
        raise AutomationError(
            "pywinauto is required for the real Trade Ideas backend and is only "
            "available on Windows. Install with `pip install pywinauto` on the "
            "Windows collection machine, or use the 'mock' backend for tests. "
            f"Import error: {exc}"
        ) from exc
    return pywinauto


class PywinautoBackend(AutomationBackend):
    """Windows implementation.  Selectors are configurable and unverified until
    diagnostic mode confirms them on the target machine."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        screenshots_dir: str | None = None,
    ) -> None:
        self.config = config or {}
        self.screenshots_dir = screenshots_dir
        self.app = None
        self.main = None
        self._selectors = self.config.get("selectors", {})
        self._allow_coord = self.config.get("allow_coordinate_fallback", False)
        self._coord = self.config.get("coordinate_fallbacks", {})
        self._screenshot_before_click = self.config.get("screenshot_before_click", True)

    # -- lifecycle -----------------------------------------------------------
    def connect(self) -> None:  # pragma: no cover - Windows-only
        pywinauto = _require_pywinauto()
        from pywinauto import Application

        title_re = self.config.get("app_title_re", ".*Trade Ideas.*")
        backend = self.config.get("ui_backend", "uia")
        self.app = Application(backend=backend).connect(title_re=title_re, timeout=30)
        self.main = self.app.top_window()

    def ensure_foreground(self) -> None:  # pragma: no cover - Windows-only
        if self.main is None:
            self.connect()
        try:
            self.main.set_focus()
        except Exception as exc:
            raise AutomationError(f"Could not bring Trade Ideas to foreground: {exc}")

    def process_info(self) -> dict[str, Any]:  # pragma: no cover - Windows-only
        if self.main is None:
            self.connect()
        return {
            "process_id": self.main.process_id(),
            "window_title": self.main.window_text(),
            "backend": "pywinauto",
        }

    # -- panels --------------------------------------------------------------
    def list_panels(self) -> list[PanelInfo]:  # pragma: no cover - Windows-only
        """Enumerate panel-selection menu entries / tab positions.

        UNVERIFIED: the exact menu path and control identifiers must be
        confirmed by diagnostic mode.  This uses the configured menu path and
        falls back to enumerating tab controls.
        """
        self.ensure_foreground()
        panels: list[PanelInfo] = []
        menu_path = self._selectors.get("panel_menu_path")
        try:
            if menu_path:
                self.main.menu_select(menu_path)
            tabs = self.main.descendants(control_type="TabItem")
            for idx, tab in enumerate(tabs):
                panels.append(
                    PanelInfo(
                        position=idx,
                        label=tab.window_text(),
                        automation_id=getattr(tab.element_info, "automation_id", None),
                        signature=self._panel_signature_for(tab, idx),
                    )
                )
        except Exception as exc:
            raise AutomationError(f"Could not enumerate panels: {exc}")
        return panels

    def _panel_signature_for(self, tab, idx: int) -> dict[str, Any]:  # pragma: no cover
        return {
            "active_tab_index": idx,
            "label": tab.window_text(),
        }

    def select_panel(self, position: int) -> None:  # pragma: no cover - Windows-only
        self.ensure_foreground()
        tabs = self.main.descendants(control_type="TabItem")
        if position >= len(tabs):
            raise AutomationError(
                f"Panel position {position} out of range (found {len(tabs)} tabs)"
            )
        tabs[position].select()

    def active_panel_signature(self) -> dict[str, Any]:  # pragma: no cover - Windows-only
        self.ensure_foreground()
        sig: dict[str, Any] = {"confirmed": False}
        try:
            tabs = self.main.descendants(control_type="TabItem")
            for idx, tab in enumerate(tabs):
                state = tab.get_toggle_state() if hasattr(tab, "get_toggle_state") else None
                is_selected = getattr(tab.element_info, "is_selected", lambda: False)
                selected = False
                try:
                    selected = bool(is_selected())
                except Exception:
                    selected = state == 1
                if selected:
                    sig.update(
                        {
                            "active_tab_index": idx,
                            "visible_tab_position": idx,
                            "label": tab.window_text(),
                            "confirmed": True,
                        }
                    )
            sig["column_signature"] = self._read_column_headers()
        except Exception as exc:
            sig["error"] = str(exc)
        return sig

    def _read_column_headers(self) -> list[str]:  # pragma: no cover - Windows-only
        headers = []
        try:
            for h in self.main.descendants(control_type="HeaderItem"):
                headers.append(h.window_text())
        except Exception:
            pass
        return headers

    # -- history -------------------------------------------------------------
    def open_history(self) -> None:  # pragma: no cover - Windows-only
        self.ensure_foreground()
        menu_path = self._selectors.get("history_menu_path")
        if menu_path:
            self.main.menu_select(menu_path)

    def select_date(self, trading_date: date) -> None:  # pragma: no cover - Windows-only
        edit_id = self._selectors.get("history_date_edit")
        if not edit_id:
            raise AutomationError("history_date_edit selector not configured/verified")
        from pywinauto.keyboard import send_keys

        edit = self.main.child_window(auto_id=edit_id, control_type="Edit")
        edit.set_focus()
        edit.set_edit_text(trading_date.strftime("%m/%d/%Y"))
        send_keys("{ENTER}")

    def wait_history_loaded(self, timeout: float) -> bool:  # pragma: no cover
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.read_page_state().row_count >= 0:
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def read_page_state(self) -> HistoryPageState:  # pragma: no cover - Windows-only
        grid_id = self._selectors.get("history_grid")
        rows_data: list[dict[str, Any]] = []
        try:
            grid = self.main.child_window(auto_id=grid_id) if grid_id else None
            data_items = grid.descendants(control_type="DataItem") if grid else []
            # Real extraction is grid-specific and must be verified on Windows.
            row_count = len(data_items)
        except Exception as exc:
            raise AutomationError(f"Could not read history grid: {exc}")
        return HistoryPageState(
            row_count=row_count,
            newest_timestamp=None,
            oldest_timestamp=None,
            first_symbol=None,
            last_symbol=None,
            column_signature=self._read_column_headers(),
            no_more_history=self.detect_no_more_history(),
        )

    def click_more(self) -> None:  # pragma: no cover - Windows-only
        more_id = self._selectors.get("more_button")
        if not more_id:
            raise AutomationError("more_button selector not configured/verified")
        self.main.child_window(auto_id=more_id, control_type="Button").click_input()

    def detect_no_more_history(self) -> bool:  # pragma: no cover - Windows-only
        text_id = self._selectors.get("no_more_history_text")
        if not text_id:
            return False
        try:
            ctrl = self.main.child_window(auto_id=text_id)
            return ctrl.exists() and ctrl.is_visible()
        except Exception:
            return False

    # -- export --------------------------------------------------------------
    def open_file_menu(self) -> None:  # pragma: no cover - Windows-only
        menu_path = self._selectors.get("file_menu_path", "File")
        self.main.menu_select(menu_path)

    def save_contents(self, absolute_path: str, timeout: float) -> SaveAsResult:  # pragma: no cover
        from pywinauto import Desktop
        from pywinauto.keyboard import send_keys

        save_path = self._selectors.get("save_contents_menu_path", "File->Save Contents")
        try:
            self.main.menu_select(save_path)
        except Exception as exc:
            return SaveAsResult(ok=False, path=None, error=f"menu_select_failed:{exc}")

        # Wait for the Save As dialog.
        deadline = time.monotonic() + timeout
        dlg = None
        while time.monotonic() < deadline:
            try:
                dlg = Desktop(backend="uia").window(title_re="Save As")
                if dlg.exists():
                    break
            except Exception:
                pass
            time.sleep(0.3)
        if dlg is None or not dlg.exists():
            return SaveAsResult(ok=False, path=None, error="save_as_timeout")

        try:
            edit = dlg.child_window(control_type="Edit", found_index=0)
            edit.set_edit_text(absolute_path)
            dlg.child_window(title="Save", control_type="Button").click_input()
        except Exception as exc:
            return SaveAsResult(ok=False, path=None, error=f"save_as_fill_failed:{exc}")

        # Overwrite confirmation: never approve.
        time.sleep(0.5)
        if self.detect_overwrite_dialog():
            self.cancel_overwrite()
            return SaveAsResult(
                ok=False, path=absolute_path, overwrite_prompted=True,
                error="overwrite_prompted",
            )

        return SaveAsResult(ok=os.path.exists(absolute_path), path=absolute_path)

    def detect_overwrite_dialog(self) -> bool:  # pragma: no cover - Windows-only
        from pywinauto import Desktop

        try:
            dlg = Desktop(backend="uia").window(title_re="Confirm Save As")
            return dlg.exists()
        except Exception:
            return False

    def cancel_overwrite(self) -> None:  # pragma: no cover - Windows-only
        from pywinauto import Desktop

        try:
            dlg = Desktop(backend="uia").window(title_re="Confirm Save As")
            dlg.child_window(title="No", control_type="Button").click_input()
        except Exception:
            from pywinauto.keyboard import send_keys

            send_keys("{ESC}")

    # -- diagnostics ---------------------------------------------------------
    def screenshot(self, name: str) -> str | None:  # pragma: no cover - Windows-only
        if not self.screenshots_dir:
            return None
        os.makedirs(self.screenshots_dir, exist_ok=True)
        path = os.path.join(self.screenshots_dir, f"{name}.png")
        try:
            self.ensure_foreground()
            img = self.main.capture_as_image()
            img.save(path)
            return path
        except Exception:
            return None

    def discover(self) -> dict[str, Any]:  # pragma: no cover - Windows-only
        self.ensure_foreground()
        report: dict[str, Any] = {"process": self.process_info()}
        try:
            report["controls"] = [
                {
                    "name": c.window_text(),
                    "control_type": c.element_info.control_type,
                    "automation_id": c.element_info.automation_id,
                    "class_name": c.element_info.class_name,
                    "rectangle": str(c.rectangle()),
                }
                for c in self.main.descendants()
            ]
        except Exception as exc:
            report["controls_error"] = str(exc)
        try:
            report["panels"] = [p.to_dict() for p in self.list_panels()]
        except Exception as exc:
            report["panels_error"] = str(exc)
        return report
