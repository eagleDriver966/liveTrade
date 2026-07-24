"""Hard production-safety gate - evaluated LIVE, independent of test gates.

Production collection is eligible only when every one of the 13 conditions
below holds.  Most are evaluated live at call time (OS, backend type, an actual
pywinauto connection to a running Trade Ideas process, mock flag, and the
database/export paths); the rest read real-gate evidence that could only have
been produced by a genuine real connection in the CURRENT configuration version.

A mock backend can never make this return eligible: it fails the backend-type,
mock-mode, connection, PID and window-handle conditions immediately, and it can
never set any ``real_*`` gate.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass, field
from typing import Any

from . import gates

MOCK_PATH_MARKERS = ("mocktest", "_mocktest", "mock", "/test/", "test.db", "tests")


@dataclass
class Condition:
    name: str
    value: str
    ok: bool
    evidence: str

    def line(self) -> str:
        return f"{self.name}: {self.value} — {'PASS' if self.ok else 'FAIL'}"


@dataclass
class ProductionEligibility:
    conditions: list[Condition] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        return bool(self.conditions) and all(c.ok for c in self.conditions)

    def report_lines(self) -> list[str]:
        lines = [c.line() for c in self.conditions]
        lines.append(f"Production collection eligible: {'YES' if self.eligible else 'NO'}")
        return lines

    def report_text(self) -> str:
        return "\n".join(self.report_lines())

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "conditions": [
                {"name": c.name, "value": c.value, "ok": c.ok, "evidence": c.evidence}
                for c in self.conditions
            ],
            "report": self.report_lines(),
        }


def _looks_like_mock_path(path: str) -> bool:
    p = (path or "").lower()
    return any(marker in p for marker in MOCK_PATH_MARKERS)


def evaluate(app) -> ProductionEligibility:
    """Evaluate all 13 hard conditions against the live environment + app."""
    conds: list[Condition] = []
    add = lambda *a: conds.append(Condition(*a))  # noqa: E731

    # 1. Operating system.
    os_name = platform.system()
    add("Operating system", os_name, os_name == "Windows", "platform.system()")

    # 2. Backend type.
    backend_type = "windows_real" if app.backend_name.lower() == "pywinauto" else app.backend_name
    add("Backend type", backend_type, backend_type == "windows_real",
        "config.automation.backend")

    # 6. Mock mode (evaluated early; drives whether we even probe).
    mock_mode = app._is_mock()

    # Live connection probe (only meaningful for the real backend on Windows).
    connected = False
    pid: Any = None
    window: Any = None
    process_signature: str | None = None
    conn_evidence = "no live connection attempted"
    if backend_type == "windows_real" and os_name == "Windows":
        status = app.backend_status(probe=True)
        connected = status.get("code") == "REAL_READY" and status.get("ready") is True
        detail = status.get("detail") or {}
        pid = detail.get("process_id")
        window = detail.get("window_title")
        conn_evidence = "live pywinauto connection this execution"
        if connected and pid is not None:
            process_signature = f"{pid}:{window}"
    else:
        conn_evidence = f"not attempted (os={os_name}, backend={backend_type})"

    # 3. Real backend connected.
    add("Real backend connected", "yes" if connected else "no", connected, conn_evidence)

    # 4. Trade Ideas PID.
    add("Trade Ideas PID", str(pid) if pid is not None else "unavailable",
        connected and pid is not None, conn_evidence)

    # 5. Real main-window handle.
    add("Real main-window handle", str(window) if window else "unavailable",
        connected and bool(window), conn_evidence)

    # 6. mock_mode == false.
    add("Mock mode", "true" if mock_mode else "false", not mock_mode,
        "config.automation.backend == 'mock'")

    # 7. Production database path is not a mock/test path.
    db_path = app.effective_database_path()
    db_ok = (not mock_mode) and (not _looks_like_mock_path(db_path))
    add("Production database path", db_path, db_ok, "app.effective_database_path()")

    # 8. Production export directory is not a mock/test directory.
    exp_dir = app.effective_exports_root()
    exp_ok = (not mock_mode) and (not _looks_like_mock_path(exp_dir))
    add("Production export directory", exp_dir, exp_ok, "app.effective_exports_root()")

    cfg = app.config
    cv = gates.get_config_version(cfg)

    def real_ok(key: str) -> bool:
        # Must be valid for the current config version AND (when connected) match
        # the current live process signature.
        return gates.real_gate_valid(cfg, key, process_signature) and connected

    # 9. Panel assignments discovered using the current real process.
    pa = gates.real_gate_entry(cfg, "real_panel_assignments_verified")
    pa_sig = pa.get("process_signature") if pa else None
    cond9_ok = bool(pa and pa.get("passed") and connected and pa_sig == process_signature)
    add("Real panel assignments (current process)",
        "verified" if cond9_ok else ("unavailable" if not pa else "stale/mismatch"),
        cond9_ok, f"config.real_gates.real_panel_assignments_verified (config v{cv})")

    # 10. All panel assignments visually confirmed in the current config version.
    cond10_ok = gates.real_gate_valid(cfg, "real_panel_assignments_verified", None) \
        and bool(pa and pa.get("all_confirmed"))
    add("Real panels visually confirmed (this config version)",
        "yes" if cond10_ok else "no", cond10_ok,
        f"config.real_gates (config v{cv})")

    # 11. One real page export passed.
    c11 = real_ok("real_page_export_verified")
    add("Real one-page export", "passed" if c11 else "not tested", c11,
        "config.real_gates.real_page_export_verified")

    # 12. One real More transition passed.
    c12 = real_ok("real_more_transition_verified")
    add("Real More transition", "passed" if c12 else "not tested", c12,
        "config.real_gates.real_more_transition_verified")

    # 13. One full real session reconciled.
    c13 = real_ok("real_session_reconciled")
    add("Real session reconciliation", "passed" if c13 else "not tested", c13,
        "config.real_gates.real_session_reconciled")

    return ProductionEligibility(conditions=conds)
