"""Gated single-step diagnostic exports (no auto-continue, no import).

* ``one_page_export`` exports exactly the currently visible history page (never
  clicks More), verifies + parses it, and returns display metrics.  It does NOT
  import into the database - import happens only after the operator approves.
* ``one_more_transition`` clicks More exactly once, confirms the page changed,
  exports ``part_002``, and compares the two pages.  It stops after part_002.

These are backend-driven (real or mock) and contain no DB writes, so they are
safe to exercise in tests against the mock backend and a throwaway directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .automation.base import AutomationBackend
from .calendar_util import ExchangeCalendar
from .collector import _save_with_collision_protection, verify_active_panel
from .config import Config
from .csv_parser import parse_csv
from .fingerprint import compute_fingerprint, page_fingerprint
from .importer import build_alert_fields
from .paths import collision_safe_path, make_session_run, sha256_file, wait_for_file_settled
from .sessions import validate_page


@dataclass
class PageTestReport:
    ok: bool
    session: str
    trading_date: str
    part_number: int
    run_dir: str
    filename: str | None = None
    abs_path: str | None = None
    file_size: int | None = None
    parsed_row_count: int = 0
    newest_timestamp: str | None = None
    oldest_timestamp: str | None = None
    alert_types: list[str] = field(default_factory=list)
    symbols_sample: list[str] = field(default_factory=list)
    symbol_count: int = 0
    timestamp_parse_rate: float | None = None
    rows_without_timestamp: int = 0
    session_validation: dict[str, Any] = field(default_factory=dict)
    sha256: str | None = None
    page_fingerprint: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _analyze(parsed, session, trading_date, calendar) -> dict[str, Any]:
    rows = parsed.rows
    with_ts = sum(1 for r in rows if r.timestamp is not None)
    types = sorted({(r.fields.get("alert_type") or "").strip() for r in rows if r.fields.get("alert_type")})
    symbols = [r.fields.get("symbol") for r in rows if r.fields.get("symbol")]
    fps = []
    for r in rows:
        af = build_alert_fields(r, session, trading_date)
        if af["alert_timestamp"] is not None and af["symbol"]:
            fps.append(compute_fingerprint(af).value)
    validation = validate_page(parsed, session, trading_date, calendar)
    return {
        "parsed_row_count": parsed.row_count,
        "newest_timestamp": parsed.newest_timestamp.isoformat() if parsed.newest_timestamp else None,
        "oldest_timestamp": parsed.oldest_timestamp.isoformat() if parsed.oldest_timestamp else None,
        "alert_types": types,
        "symbols_sample": symbols[:10],
        "symbol_count": len(set(symbols)),
        "timestamp_parse_rate": (with_ts / len(rows)) if rows else None,
        "rows_without_timestamp": len(rows) - with_ts,
        "session_validation": validation.to_dict(),
        "page_fingerprint": page_fingerprint(fps) if fps else None,
        "event_fingerprints": fps,
    }


def _export_visible_page(
    backend: AutomationBackend, dirs, part_number: int, config: Config, logger,
) -> tuple[bool, str | None, str | None]:
    """Save the currently visible page to a unique path.  Returns
    (ok, abs_path, error)."""
    timeouts = config.get("timeouts", default={})
    desired = collision_safe_path(dirs.part_path(part_number))
    result, final_path = _save_with_collision_protection(
        backend, desired, timeouts.get("save_as_dialog_seconds", 20), logger
    )
    if not result.ok:
        return False, None, f"save_failed:{result.error}"
    settled = wait_for_file_settled(
        final_path,
        settle_seconds=timeouts.get("file_settle_seconds", 10),
        poll_interval=timeouts.get("file_settle_poll_interval", 0.5),
    )
    if not settled:
        return False, final_path, "file_not_settled"
    if os.path.getsize(final_path) == 0:
        return False, final_path, "file_empty"
    return True, final_path, None


def one_page_export(
    backend: AutomationBackend,
    calendar: ExchangeCalendar,
    config: Config,
    logger,
    session: str,
    trading_date: date,
    panel_position: Any,
    exports_root: str,
    run_id_dir: str | None = None,
) -> PageTestReport:
    """Export + parse exactly one visible history page.  Never clicks More.
    Never imports."""
    dirs = make_session_run(exports_root, trading_date, session, run_id=run_id_dir).ensure()
    report = PageTestReport(
        ok=False, session=session, trading_date=trading_date.isoformat(),
        part_number=1, run_dir=dirs.run_dir,
    )
    timeouts = config.get("timeouts", default={})

    try:
        backend.ensure_foreground()
        backend.select_panel(panel_position)
        verification = verify_active_panel(backend, panel_position, session, config)
        if not verification.confirmed:
            logger.capture_screenshot("wrong_active_panel", session=session)
            report.error = "panel_verification_failed:" + ",".join(verification.reasons)
            return report

        backend.open_history()
        backend.select_date(trading_date)
        if not backend.wait_history_loaded(timeouts.get("history_load_seconds", 30)):
            report.error = "history_load_timeout"
            logger.capture_screenshot("history_load_timeout", session=session)
            return report

        # Export the VISIBLE page only (no More).
        ok, path, err = _export_visible_page(backend, dirs, 1, config, logger)
        if not ok:
            report.error = err
            logger.capture_screenshot("one_page_export_failed", session=session)
            return report

        parsed = parse_csv(path, trading_date, calendar.timezone)
        metrics = _analyze(parsed, session, trading_date, calendar)
        report.ok = True
        report.filename = os.path.basename(path)
        report.abs_path = path
        report.file_size = os.path.getsize(path)
        report.sha256 = sha256_file(path)
        report.parsed_row_count = metrics["parsed_row_count"]
        report.newest_timestamp = metrics["newest_timestamp"]
        report.oldest_timestamp = metrics["oldest_timestamp"]
        report.alert_types = metrics["alert_types"]
        report.symbols_sample = metrics["symbols_sample"]
        report.symbol_count = metrics["symbol_count"]
        report.timestamp_parse_rate = metrics["timestamp_parse_rate"]
        report.rows_without_timestamp = metrics["rows_without_timestamp"]
        report.session_validation = metrics["session_validation"]
        report.page_fingerprint = metrics["page_fingerprint"]
        logger.info("test_one_page", **{k: report.to_dict()[k] for k in
                    ("filename", "parsed_row_count", "newest_timestamp",
                     "oldest_timestamp", "timestamp_parse_rate")})
        return report
    except Exception as exc:
        logger.exception("test_one_page_exception", exc, session=session)
        report.error = f"{type(exc).__name__}:{exc}"
        return report


@dataclass
class MoreTestReport:
    ok: bool
    session: str
    trading_date: str
    first_part: dict[str, Any] = field(default_factory=dict)
    second_part: dict[str, Any] = field(default_factory=dict)
    page_changed: bool = False
    moved_backward: bool = False
    new_fingerprints: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def one_more_transition(
    backend: AutomationBackend,
    calendar: ExchangeCalendar,
    config: Config,
    logger,
    session: str,
    trading_date: date,
    exports_root: str,
    run_id_dir: str,
    first_report: PageTestReport,
) -> MoreTestReport:
    """Click More exactly once, export part_002, and compare to the first page.

    Requires a validated first-page export (``first_report.ok``).  Stops after
    part_002; never continues automatically.
    """
    report = MoreTestReport(ok=False, session=session,
                            trading_date=trading_date.isoformat())
    if not first_report.ok:
        report.error = "first_page_not_validated"
        return report

    dirs = make_session_run(exports_root, trading_date, session, run_id=run_id_dir).ensure()
    timeouts = config.get("timeouts", default={})

    try:
        # Click More exactly once.
        logger.info("test_one_more_click", session=session)
        backend.click_more()
        backend.wait_history_loaded(timeouts.get("more_load_seconds", 30))

        ok, path, err = _export_visible_page(backend, dirs, 2, config, logger)
        if not ok:
            report.error = err
            logger.capture_screenshot("one_more_export_failed", session=session)
            return report

        parsed2 = parse_csv(path, trading_date, calendar.timezone)
        metrics2 = _analyze(parsed2, session, trading_date, calendar)

        first_fps = set(first_report.to_dict().get("event_fingerprints", []) or [])
        # first_report may not carry fingerprints; recompute from its file.
        if not first_fps and first_report.abs_path and os.path.exists(first_report.abs_path):
            p1 = parse_csv(first_report.abs_path, trading_date, calendar.timezone)
            first_fps = set(_analyze(p1, session, trading_date, calendar)["event_fingerprints"])

        second_fps = set(metrics2["event_fingerprints"])
        new_fps = second_fps - first_fps

        # Compare oldest timestamps (backward progress).
        moved_backward = False
        if first_report.oldest_timestamp and metrics2["oldest_timestamp"]:
            moved_backward = metrics2["oldest_timestamp"] < first_report.oldest_timestamp

        page_changed = (
            metrics2["page_fingerprint"] != first_report.page_fingerprint
            or moved_backward
            or bool(new_fps)
        )

        report.first_part = {
            "filename": first_report.filename,
            "oldest_timestamp": first_report.oldest_timestamp,
            "newest_timestamp": first_report.newest_timestamp,
            "page_fingerprint": first_report.page_fingerprint,
            "row_count": first_report.parsed_row_count,
        }
        report.second_part = {
            "filename": os.path.basename(path),
            "oldest_timestamp": metrics2["oldest_timestamp"],
            "newest_timestamp": metrics2["newest_timestamp"],
            "page_fingerprint": metrics2["page_fingerprint"],
            "row_count": metrics2["parsed_row_count"],
            "sha256": sha256_file(path),
        }
        report.page_changed = page_changed
        report.moved_backward = moved_backward
        report.new_fingerprints = len(new_fps)
        # A valid More transition changed the page AND showed progress.
        report.ok = page_changed and (moved_backward or bool(new_fps))
        if not report.ok:
            report.error = "no_backward_progress_and_no_new_fingerprints"
        logger.info("test_one_more", **report.to_dict())
        return report
    except Exception as exc:
        logger.exception("test_one_more_exception", exc, session=session)
        report.error = f"{type(exc).__name__}:{exc}"
        return report
