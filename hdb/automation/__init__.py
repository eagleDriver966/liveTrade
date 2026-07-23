"""Isolated Trade Ideas automation adapters.

The abstract interface lives in :mod:`hdb.automation.base`.  Two backends
implement it:

* :mod:`hdb.automation.mock_backend` - a deterministic simulator used for
  development and integration tests on any OS (writes real CSV files, simulates
  More/Save As/overwrite dialogs, wrong-panel, timeouts, etc.).
* :mod:`hdb.automation.pywinauto_backend` - the real Windows implementation
  (imports ``pywinauto`` lazily so this package imports fine on Linux/macOS).
"""

from .base import (
    AutomationBackend,
    AutomationError,
    ControlInfo,
    HistoryPageState,
    OverwriteDialogError,
    PanelInfo,
    PanelVerificationError,
    SaveAsResult,
    SaveAsTimeout,
)

__all__ = [
    "AutomationBackend",
    "AutomationError",
    "ControlInfo",
    "HistoryPageState",
    "OverwriteDialogError",
    "PanelInfo",
    "PanelVerificationError",
    "SaveAsResult",
    "SaveAsTimeout",
]


def make_backend(name: str, **kwargs):
    """Factory: return a backend instance by name (``mock`` or ``pywinauto``)."""
    name = (name or "").lower()
    if name == "mock":
        from .mock_backend import MockBackend

        return MockBackend(**kwargs)
    if name == "pywinauto":
        from .pywinauto_backend import PywinautoBackend

        return PywinautoBackend(**kwargs)
    raise ValueError(f"Unknown automation backend: {name!r}")
