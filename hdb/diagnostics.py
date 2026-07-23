"""Diagnostic discovery and panel-position assignment.

Diagnostic mode must run before unattended collection.  It produces a JSON report
and a human-readable report describing the Trade Ideas process, menus, panels,
tab positions, control metadata, History/More/File/Save-As/overwrite controls,
and screenshots, and lets the user assign discovered positions to HPRE/NHP/HPOST.

The heavy UI work is delegated to the automation backend's ``discover``; this
module structures the output and manages assignment persistence and the
"readiness" gate.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from .automation.base import AutomationBackend
from .config import SESSIONS, Config


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_discovery(
    backend: AutomationBackend,
    diagnostics_root: str,
    logger=None,
) -> dict[str, Any]:
    """Run backend discovery and write JSON + text reports.  Returns the report
    dict augmented with output paths."""
    backend.connect()
    backend.ensure_foreground()
    report: dict[str, Any] = {
        "generated_at": _utc_iso(),
        "discovery": backend.discover(),
    }
    # Capture a top-level screenshot for reference.
    shot = backend.screenshot("diagnostic_overview")
    report["overview_screenshot"] = shot

    os.makedirs(diagnostics_root, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(diagnostics_root, f"diagnostic_{stamp}.json")
    text_path = os.path.join(diagnostics_root, f"diagnostic_{stamp}.txt")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    with open(text_path, "w", encoding="utf-8") as fh:
        fh.write(render_text_report(report))
    report["json_path"] = json_path
    report["text_path"] = text_path
    if logger:
        logger.info("diagnostic_report", json_path=json_path, text_path=text_path)
    return report


def render_text_report(report: dict[str, Any]) -> str:
    disc = report.get("discovery", {})
    lines = [
        "Trade Ideas Diagnostic Report",
        f"Generated: {report.get('generated_at')}",
        "",
        "Process:",
        json.dumps(disc.get("process", {}), indent=2, default=str),
        "",
        "Panels (menu tab positions):",
    ]
    for panel in disc.get("panels", []):
        lines.append(
            f"  position={panel.get('position')} label={panel.get('label')!r} "
            f"automation_id={panel.get('automation_id')}"
        )
    lines += ["", "Menu hierarchy:", json.dumps(disc.get("menu_hierarchy", {}), indent=2)]
    lines += ["", "History controls:", json.dumps(disc.get("history_controls", {}), indent=2)]
    lines += ["", "Save As dialog:", json.dumps(disc.get("save_as_dialog", {}), indent=2)]
    lines += ["", "Overwrite dialog:", json.dumps(disc.get("overwrite_dialog", {}), indent=2)]
    lines += ["", f"Overview screenshot: {report.get('overview_screenshot')}"]
    return "\n".join(lines) + "\n"


def enumerate_positions(backend: AutomationBackend) -> list[dict[str, Any]]:
    """Activate each panel position, capturing a signature + screenshot for the
    user to review before assignment."""
    backend.connect()
    backend.ensure_foreground()
    entries: list[dict[str, Any]] = []
    for panel in backend.list_panels():
        backend.select_panel(panel.position)
        sig = backend.active_panel_signature()
        shot = backend.screenshot(f"panel_position_{panel.position}")
        entries.append(
            {
                "position": panel.position,
                "label": panel.label,
                "signature": sig,
                "screenshot": shot,
            }
        )
    return entries


def assign_panel(
    config: Config,
    session: str,
    position: int,
    signature: dict[str, Any] | None = None,
    logger=None,
) -> None:
    if session not in SESSIONS:
        raise ValueError(f"Unknown session {session!r}")
    config.assign_panel(session, position, signature)
    if config.path:
        config.save()
    if logger:
        logger.info("panel_assigned", session=session, position=position)


def readiness(config: Config) -> dict[str, Any]:
    """Report whether prerequisites for unattended collection are met."""
    checks = {
        "panels_assigned": config.panels_assigned(),
        "panel_positions": dict(config.panel_positions),
    }
    checks["ready"] = bool(checks["panels_assigned"])
    return checks
