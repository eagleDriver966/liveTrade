"""Operating-system detection and automation backend status.

At startup the app determines which backend is active and whether it is ready,
and displays exactly one prominent status:

* ``REAL WINDOWS BACKEND READY`` - running on Windows, pywinauto imported, and
  the Trade Ideas main window was found and connected.
* ``MOCK TEST BACKEND`` - the mock simulator is configured (tests/diagnostics
  only; production collection is blocked).
* ``WINDOWS BACKEND ERROR`` - the real backend was requested but could not
  initialize; the exact reason is shown and the app remains blocked.

The app NEVER silently falls back from the real backend to the mock backend for
a production command.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass, field
from typing import Any

# Prominent display labels (exact strings required by the operator).
LABEL_REAL = "REAL WINDOWS BACKEND READY"
LABEL_MOCK = "MOCK TEST BACKEND"
LABEL_ERROR = "WINDOWS BACKEND ERROR"

CODE_REAL = "REAL_READY"
CODE_MOCK = "MOCK"
CODE_ERROR = "ERROR"


@dataclass
class BackendStatus:
    code: str
    label: str
    backend_name: str
    os_name: str
    ready: bool
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "backend_name": self.backend_name,
            "os": self.os_name,
            "ready": self.ready,
            "message": self.message,
            "detail": self.detail,
        }


def determine_backend_status(
    backend_name: str,
    automation_config: dict[str, Any] | None = None,
    probe: bool = True,
) -> BackendStatus:
    """Detect OS + backend readiness without ever falling back to mock.

    When ``probe`` is True and the backend is ``pywinauto`` on Windows, this
    attempts to connect to Trade Ideas and reports the exact failure reason if
    it cannot.
    """
    os_name = platform.system()
    name = (backend_name or "").lower()

    if name == "mock":
        return BackendStatus(
            code=CODE_MOCK,
            label=LABEL_MOCK,
            backend_name="mock",
            os_name=os_name,
            ready=False,  # not a production-ready backend
            message="Mock test backend selected. For automated tests and "
                    "diagnostics only; production collection is blocked.",
        )

    if name != "pywinauto":
        return BackendStatus(
            code=CODE_ERROR,
            label=LABEL_ERROR,
            backend_name=name or "(unset)",
            os_name=os_name,
            ready=False,
            message=f"Unknown automation backend {backend_name!r}. "
                    "Set automation.backend to 'pywinauto' (Windows) or 'mock'.",
        )

    # Real backend requested.
    if os_name != "Windows":
        return BackendStatus(
            code=CODE_ERROR,
            label=LABEL_ERROR,
            backend_name="pywinauto",
            os_name=os_name,
            ready=False,
            message=f"Real Windows backend requires Windows; detected OS "
                    f"'{os_name}'. Run on the Windows machine with Trade Ideas "
                    "open, or use backend='mock' for tests.",
        )

    if not probe:
        return BackendStatus(
            code=CODE_ERROR,
            label=LABEL_ERROR,
            backend_name="pywinauto",
            os_name=os_name,
            ready=False,
            message="Backend not probed; call with probe=True to connect.",
        )

    # On Windows: try to import pywinauto and connect to Trade Ideas.
    try:
        from .automation.pywinauto_backend import PywinautoBackend

        backend = PywinautoBackend(config=automation_config or {})
        backend.connect()
        info = backend.process_info()
        return BackendStatus(
            code=CODE_REAL,
            label=LABEL_REAL,
            backend_name="pywinauto",
            os_name=os_name,
            ready=True,
            message="Connected to Trade Ideas.",
            detail=info,
        )
    except Exception as exc:  # exact reason surfaced; remain blocked
        return BackendStatus(
            code=CODE_ERROR,
            label=LABEL_ERROR,
            backend_name="pywinauto",
            os_name=os_name,
            ready=False,
            message=f"Real backend failed to initialize: {type(exc).__name__}: {exc}",
        )
