"""Abstract automation interface and shared data types.

All Trade Ideas UI interaction goes through :class:`AutomationBackend`.  Business
logic depends only on this interface, never on ``pywinauto`` directly, so the
whole system is testable with the mock backend.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any


class AutomationError(Exception):
    """Base class for automation failures."""


class PanelVerificationError(AutomationError):
    """Raised/returned when the active panel cannot be confirmed."""


class SaveAsTimeout(AutomationError):
    """The Save As dialog did not appear or complete in time."""


class OverwriteDialogError(AutomationError):
    """An overwrite-confirmation dialog appeared (never auto-approved)."""


@dataclass
class ControlInfo:
    """A discovered UI control (for diagnostics)."""

    name: str | None = None
    automation_id: str | None = None
    control_type: str | None = None
    class_name: str | None = None
    rectangle: tuple[int, int, int, int] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "automation_id": self.automation_id,
            "control_type": self.control_type,
            "class_name": self.class_name,
            "rectangle": list(self.rectangle) if self.rectangle else None,
            "extra": self.extra,
        }


@dataclass
class PanelInfo:
    """A selectable alert panel (menu entry / tab position)."""

    position: int
    label: str | None = None
    automation_id: str | None = None
    signature: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "label": self.label,
            "automation_id": self.automation_id,
            "signature": self.signature,
        }


@dataclass
class HistoryPageState:
    """Snapshot of the currently visible history page."""

    row_count: int
    newest_timestamp: str | None
    oldest_timestamp: str | None
    first_symbol: str | None
    last_symbol: str | None
    column_signature: list[str] = field(default_factory=list)
    no_more_history: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class SaveAsResult:
    ok: bool
    path: str | None
    overwrite_prompted: bool = False
    error: str | None = None


class AutomationBackend(ABC):
    """Interface implemented by mock and pywinauto backends."""

    # -- lifecycle -----------------------------------------------------------
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def ensure_foreground(self) -> None: ...

    @abstractmethod
    def process_info(self) -> dict[str, Any]: ...

    # -- panels --------------------------------------------------------------
    @abstractmethod
    def list_panels(self) -> list[PanelInfo]: ...

    @abstractmethod
    def select_panel(self, position: int) -> None: ...

    @abstractmethod
    def active_panel_signature(self) -> dict[str, Any]:
        """Return multiple independent signals identifying the active panel:
        selected menu state, active tab index, visible tab position, column
        signature, alert-type values, control hierarchy hint, screenshot
        fingerprint."""

    # -- history -------------------------------------------------------------
    @abstractmethod
    def open_history(self) -> None: ...

    @abstractmethod
    def select_date(self, trading_date: date) -> None: ...

    @abstractmethod
    def wait_history_loaded(self, timeout: float) -> bool: ...

    @abstractmethod
    def read_page_state(self) -> HistoryPageState: ...

    @abstractmethod
    def click_more(self) -> None: ...

    @abstractmethod
    def detect_no_more_history(self) -> bool: ...

    # -- export --------------------------------------------------------------
    @abstractmethod
    def open_file_menu(self) -> None: ...

    @abstractmethod
    def save_contents(self, absolute_path: str, timeout: float) -> SaveAsResult:
        """Trigger File -> Save Contents, drive the Save As dialog, and write the
        current page to ``absolute_path``.  Must NOT approve an overwrite
        confirmation - cancel and report ``overwrite_prompted=True`` instead."""

    @abstractmethod
    def detect_overwrite_dialog(self) -> bool: ...

    @abstractmethod
    def cancel_overwrite(self) -> None: ...

    # -- diagnostics ---------------------------------------------------------
    @abstractmethod
    def screenshot(self, name: str) -> str | None: ...

    @abstractmethod
    def discover(self) -> dict[str, Any]:
        """Return a full diagnostic snapshot (menus, controls, panels, dialogs)."""
