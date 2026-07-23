"""The collection driver: safe, resumable export-before-More automation.

Ties together the automation backend, exchange calendar, file layout, manifest,
CSV parser, page validation, importer, and completion decisions to implement the
confirmed Trade Ideas workflow for one session, one date, or a date range.

Safety invariants enforced here:
* the active panel is verified with multiple signals before any destructive
  action;
* every visible page is exported, validated, checksummed, registered, imported
  and marked VALIDATED *before* More is ever selected;
* a failed save never leads to More;
* overwrite dialogs are cancelled and a unique filename is generated;
* immutable run directories are never overwritten.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import repository as repo
from .automation.base import AutomationBackend, SaveAsResult
from .calendar_util import ExchangeCalendar
from .collection import PageRecord, can_advance_more, evaluate_completion
from .config import Config
from .control import CollectionControl
from .csv_parser import parse_csv
from .fingerprint import compute_fingerprint, page_fingerprint
from .importer import build_normalized_fields, import_parsed_part
from .manifest import Manifest, PartEntry
from .paths import (
    collision_safe_path,
    make_session_run,
    sha256_file,
    wait_for_file_settled,
)
from .sessions import validate_page
from .status import Status


@dataclass
class PanelVerification:
    confirmed: bool
    expected_position: Any
    signals: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


@dataclass
class SessionResult:
    session: str
    trading_date: date
    status: Status
    run_dir: str
    parts: int = 0
    completion_reason: str | None = None
    completion_signals: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session": self.session,
            "trading_date": self.trading_date.isoformat(),
            "status": str(self.status),
            "run_dir": self.run_dir,
            "parts": self.parts,
            "completion_reason": self.completion_reason,
            "completion_signals": self.completion_signals,
            "error": self.error,
        }


def verify_active_panel(
    backend: AutomationBackend,
    expected_position: Any,
    expected_session: str,
    config: Config,
) -> PanelVerification:
    """Confirm the active panel using multiple independent signals.

    Never allow a destructive action unless confirmed.
    """
    sig = backend.active_panel_signature()
    reasons: list[str] = []
    confirmed = bool(sig.get("confirmed"))

    # Signal 1: active tab index matches expected position.
    if sig.get("active_tab_index") != expected_position:
        confirmed = False
        reasons.append(
            f"tab_index_mismatch:{sig.get('active_tab_index')}!={expected_position}"
        )

    # Signal 2: stored panel signature (column signature / session) matches.
    stored = (config.get("panel_signatures", expected_session) or {})
    if isinstance(stored, dict):
        exp_cols = stored.get("column_signature") if stored else None
        act_cols = sig.get("column_signature")
        if exp_cols and act_cols and exp_cols != act_cols:
            confirmed = False
            reasons.append("column_signature_mismatch")
        exp_session = stored.get("session") if stored else None
        if exp_session and sig.get("session") and exp_session != sig.get("session"):
            confirmed = False
            reasons.append("session_signature_mismatch")

    return PanelVerification(
        confirmed=confirmed,
        expected_position=expected_position,
        signals=sig,
        reasons=reasons,
    )


def _save_with_collision_protection(
    backend: AutomationBackend,
    desired_path: str,
    timeout: float,
    logger,
    max_attempts: int = 5,
) -> tuple[SaveAsResult, str]:
    """Drive Save As, handling overwrite collisions by generating a unique name.

    Returns (result, final_path).  Never approves an overwrite.
    """
    target = desired_path
    last: SaveAsResult | None = None
    for attempt in range(1, max_attempts + 1):
        result = backend.save_contents(target, timeout)
        last = result
        if result.ok:
            return result, target
        if result.overwrite_prompted:
            if logger:
                logger.warn("overwrite_collision", path=target, attempt=attempt)
                logger.capture_screenshot("overwrite_prompt", path=target)
            # Do NOT approve; cancel already handled inside backend; new name.
            target = collision_safe_path(target)
            continue
        # Other failure (timeout etc.): stop; caller must not select More.
        if logger:
            logger.error("save_failed", path=target, error=result.error, attempt=attempt)
            logger.capture_screenshot("save_failed", path=target)
        break
    return last or SaveAsResult(ok=False, path=None, error="unknown"), target


def collect_session(
    conn,
    backend: AutomationBackend,
    calendar: ExchangeCalendar,
    config: Config,
    logger,
    run_id: str,
    trading_date: date,
    session: str,
    control: CollectionControl | None = None,
    run_id_dir: str | None = None,
) -> SessionResult:
    """Collect one session for one trading date (export-before-More)."""
    control = control or CollectionControl()
    exports_root = config.get("paths", "exports_root", default="historical_exports")
    boundary = date.fromisoformat(config.get("history_boundary_date", default="2026-02-01"))
    panel_position = config.panel_position(session)

    dirs = make_session_run(exports_root, trading_date, session, run_id=run_id_dir).ensure()
    result = SessionResult(
        session=session, trading_date=trading_date, status=Status.COLLECTING,
        run_dir=dirs.run_dir,
    )

    if panel_position is None:
        result.status = Status.FAILED
        result.error = "panel_position_not_assigned"
        logger.error("panel_not_assigned", session=session)
        return result

    repo.upsert_session(
        conn, run_id, trading_date, session, panel_position, dirs.run_dir, Status.COLLECTING
    )
    manifest = Manifest.new(run_id_dir or dirs.run_id, trading_date, session, panel_position, boundary)
    manifest.save(dirs.manifest_path)

    timeouts = config.get("timeouts", default={})
    coll_cfg = config.get("collection", default={})

    try:
        control.check_cancel()
        backend.ensure_foreground()
        logger.info("panel_select", session=session, position=panel_position)
        backend.select_panel(panel_position)

        verification = verify_active_panel(backend, panel_position, session, config)
        logger.info("panel_verify", session=session, confirmed=verification.confirmed,
                    reasons=verification.reasons)
        if not verification.confirmed:
            logger.capture_screenshot("wrong_active_panel", session=session)
            result.status = Status.FAILED
            result.error = "panel_verification_failed:" + ",".join(verification.reasons)
            repo.upsert_session(conn, run_id, trading_date, session, panel_position,
                                dirs.run_dir, Status.FAILED)
            return result

        backend.open_history()
        backend.select_date(trading_date)
        if not backend.wait_history_loaded(timeouts.get("history_load_seconds", 30)):
            logger.capture_screenshot("history_load_timeout", session=session)
            result.status = Status.FAILED
            result.error = "history_load_timeout"
            repo.upsert_session(conn, run_id, trading_date, session, panel_position,
                                dirs.run_dir, Status.FAILED)
            return result

        previous: PageRecord | None = None
        prev_state_sig: tuple | None = None
        part_number = 0
        max_pages = coll_cfg.get("max_pages_per_session", 500)

        while True:
            control.wait_if_paused()
            control.check_cancel()

            state = backend.read_page_state()
            explicit_no_more = bool(state.no_more_history)
            state_sig = (
                state.row_count, state.newest_timestamp, state.oldest_timestamp,
                state.first_symbol, state.last_symbol,
            )

            # If Trade Ideas explicitly reports no-more-history and More produced
            # the identical visible page, stop without re-exporting a duplicate
            # part.  (Already-validated previous page guarantees this is safe.)
            if previous is not None and explicit_no_more and state_sig == prev_state_sig:
                signals = ["explicit_no_more_history", "no_new_page_after_more"]
                manifest.mark_complete("explicit_no_more_history", signals, str(Status.VERIFIED))
                manifest.save(dirs.manifest_path)
                repo.upsert_session(conn, run_id, trading_date, session, panel_position,
                                    dirs.run_dir, Status.VERIFIED,
                                    completion_reason="explicit_no_more_history",
                                    completion_signals=signals, part_count=part_number)
                logger.info("session_complete_no_new_page", session=session,
                            parts=part_number, signals=signals)
                result.status = Status.VERIFIED
                result.parts = part_number
                result.completion_reason = "explicit_no_more_history"
                result.completion_signals = signals
                return result

            part_number += 1

            desired = dirs.part_path(part_number)
            desired = collision_safe_path(desired)  # immutable dir; guard anyway
            save_result, final_path = _save_with_collision_protection(
                backend, desired, timeouts.get("save_as_dialog_seconds", 20), logger
            )
            if not save_result.ok:
                # Do NOT select More after a failed save; preserve state.
                result.status = Status.INCOMPLETE
                result.error = f"save_failed:{save_result.error}"
                result.parts = part_number - 1
                repo.upsert_session(conn, run_id, trading_date, session, panel_position,
                                    dirs.run_dir, Status.INCOMPLETE, part_count=part_number - 1)
                logger.error("session_incomplete_save", session=session, part=part_number)
                return result

            settled = wait_for_file_settled(
                final_path,
                settle_seconds=timeouts.get("file_settle_seconds", 10),
                poll_interval=timeouts.get("file_settle_poll_interval", 0.5),
            )
            if not settled:
                result.status = Status.INCOMPLETE
                result.error = "file_not_settled"
                result.parts = part_number - 1
                logger.error("file_not_settled", session=session, path=final_path)
                logger.capture_screenshot("file_not_settled", session=session)
                repo.upsert_session(conn, run_id, trading_date, session, panel_position,
                                    dirs.run_dir, Status.INCOMPLETE, part_count=part_number - 1)
                return result

            # Parse + validate + fingerprint + checksum.
            try:
                parsed = parse_csv(final_path, trading_date, calendar.timezone)
            except Exception as exc:
                result.status = Status.INCOMPLETE
                result.error = f"parse_failed:{exc}"
                result.parts = part_number - 1
                logger.exception("parse_failed", exc, session=session, path=final_path)
                logger.capture_screenshot("parse_failed", session=session)
                repo.upsert_session(conn, run_id, trading_date, session, panel_position,
                                    dirs.run_dir, Status.INCOMPLETE, part_count=part_number - 1)
                return result

            validation = validate_page(parsed, session, trading_date, calendar)
            if not validation.ok:
                logger.warn("page_validation_failed", session=session,
                            **validation.to_dict())
                logger.capture_screenshot("session_verification_failure", session=session)
                result.status = Status.INCOMPLETE
                result.error = "page_validation_failed:" + ",".join(validation.reasons)
                result.parts = part_number - 1
                repo.upsert_session(conn, run_id, trading_date, session, panel_position,
                                    dirs.run_dir, Status.INCOMPLETE, part_count=part_number - 1)
                return result

            checksum = sha256_file(final_path)
            event_fps: list[str] = []
            for row in parsed.rows:
                nf = build_normalized_fields(row, session, panel_position, trading_date)
                if nf["alert_timestamp"] is None or not nf["symbol"]:
                    continue
                event_fps.append(compute_fingerprint(nf).value)
            page_fp = page_fingerprint(event_fps) if event_fps else None

            part_id = repo.register_part(
                conn, run_id, trading_date, session, part_number,
                os.path.basename(final_path), final_path, checksum,
                parsed.row_count,
                parsed.newest_timestamp.isoformat() if parsed.newest_timestamp else None,
                parsed.oldest_timestamp.isoformat() if parsed.oldest_timestamp else None,
                parsed.first_symbol, parsed.last_symbol, page_fp, panel_position,
                Status.VALIDATED,
            )
            logger.info("part_validated", session=session, part=part_number,
                        rows=parsed.row_count, sha256=checksum,
                        newest=str(parsed.newest_timestamp),
                        oldest=str(parsed.oldest_timestamp), page_fp=page_fp)

            entry = PartEntry(
                part_number=part_number,
                filename=os.path.basename(final_path),
                sha256=checksum,
                row_count=parsed.row_count,
                newest_timestamp=parsed.newest_timestamp.isoformat() if parsed.newest_timestamp else None,
                oldest_timestamp=parsed.oldest_timestamp.isoformat() if parsed.oldest_timestamp else None,
                first_symbol=parsed.first_symbol,
                last_symbol=parsed.last_symbol,
                page_fingerprint=page_fp,
                status=str(Status.VALIDATED),
            )
            manifest.add_part(entry)
            manifest.save(dirs.manifest_path)

            # Import (marks IMPORTED).
            stats = import_parsed_part(
                conn, parsed, run_id, trading_date, session, panel_position,
                part_number, os.path.basename(final_path), logger=logger,
            )
            repo.mark_part_imported(conn, part_id)
            control.emit("part_done", f"{session} {trading_date} part {part_number}",
                         session=session, part=part_number, rows=parsed.row_count,
                         imported=stats.raw_inserted)

            current = PageRecord(
                part_number=part_number,
                row_count=parsed.row_count,
                newest_timestamp=parsed.newest_timestamp,
                oldest_timestamp=parsed.oldest_timestamp,
                sha256=checksum,
                page_fingerprint=page_fp,
                event_fingerprints=frozenset(event_fps),
                status=Status.IMPORTED,
            )

            decision = evaluate_completion(
                current, previous, session, trading_date, calendar,
                explicit_no_more_history=explicit_no_more,
                min_conclusive_rows=coll_cfg.get("min_conclusive_rows", 1000),
                min_completion_signals=coll_cfg.get("min_completion_signals", 2),
            )
            logger.info("completion_decision", session=session, part=part_number,
                        **decision.to_dict())

            if decision.complete:
                final_status = (
                    Status.EMPTY_VERIFIED
                    if decision.status == Status.EMPTY_VERIFIED
                    else Status.VERIFIED
                )
                manifest.mark_complete(decision.reason or "complete",
                                       decision.signals, str(final_status))
                manifest.save(dirs.manifest_path)
                repo.upsert_session(conn, run_id, trading_date, session, panel_position,
                                    dirs.run_dir, final_status,
                                    completion_reason=decision.reason,
                                    completion_signals=decision.signals,
                                    part_count=part_number)
                result.status = final_status
                result.parts = part_number
                result.completion_reason = decision.reason
                result.completion_signals = decision.signals
                return result

            # Destructive-More guard.
            allowed, why = can_advance_more(current)
            if not allowed:
                result.status = Status.INCOMPLETE
                result.error = f"more_blocked:{why}"
                result.parts = part_number
                repo.upsert_session(conn, run_id, trading_date, session, panel_position,
                                    dirs.run_dir, Status.INCOMPLETE, part_count=part_number)
                logger.error("more_blocked", session=session, reason=why)
                return result

            if part_number >= max_pages:
                result.status = Status.INCOMPLETE
                result.error = "max_pages_reached"
                result.parts = part_number
                repo.upsert_session(conn, run_id, trading_date, session, panel_position,
                                    dirs.run_dir, Status.INCOMPLETE, part_count=part_number)
                logger.warn("max_pages_reached", session=session, part=part_number)
                return result

            logger.info("more_action", session=session, after_part=part_number)
            backend.click_more()
            backend.wait_history_loaded(timeouts.get("more_load_seconds", 30))
            previous = current
            prev_state_sig = state_sig

    except Exception as exc:  # defensive: never leave a half state silently
        logger.exception("session_exception", exc, session=session)
        logger.capture_screenshot("session_exception", session=session)
        result.status = Status.FAILED
        result.error = f"{type(exc).__name__}:{exc}"
        repo.upsert_session(conn, run_id, trading_date, session, panel_position,
                            dirs.run_dir, Status.FAILED)
        return result


def collect_date(
    conn,
    backend: AutomationBackend,
    calendar: ExchangeCalendar,
    config: Config,
    logger,
    run_id: str,
    trading_date: date,
    control: CollectionControl | None = None,
    sessions: tuple[str, ...] = ("HPRE", "NHP", "HPOST"),
) -> dict[str, SessionResult]:
    """Collect all three sessions for one trading date."""
    control = control or CollectionControl()
    repo.upsert_day(conn, run_id, trading_date, Status.COLLECTING)
    results: dict[str, SessionResult] = {}
    for session in sessions:
        control.check_cancel()
        control.emit("session_start", f"{session} {trading_date}", session=session,
                     trading_date=trading_date.isoformat())
        results[session] = collect_session(
            conn, backend, calendar, config, logger, run_id, trading_date, session, control
        )

    status_map = repo.session_status_map(conn, run_id, trading_date)
    from .status import date_is_verified

    day_status = Status.VERIFIED if date_is_verified(status_map) else Status.INCOMPLETE
    repo.upsert_day(conn, run_id, trading_date, day_status)
    logger.info("date_done", trading_date=trading_date.isoformat(),
                status=str(day_status),
                sessions={k: str(v.status) for k, v in results.items()})
    return results


def collect_range(
    conn,
    backend: AutomationBackend,
    calendar: ExchangeCalendar,
    config: Config,
    logger,
    run_id: str,
    newest: date,
    boundary: date | None = None,
    control: CollectionControl | None = None,
) -> dict[str, dict[str, SessionResult]]:
    """Collect newest -> boundary (backward), day by day."""
    control = control or CollectionControl()
    boundary = boundary or date.fromisoformat(
        config.get("history_boundary_date", default="2026-02-01")
    )
    days = calendar.trading_days_desc(boundary, newest)
    logger.info("range_start", newest=newest.isoformat(), boundary=boundary.isoformat(),
                day_count=len(days))
    all_results: dict[str, dict[str, SessionResult]] = {}
    for day in days:
        control.check_cancel()
        all_results[day.isoformat()] = collect_date(
            conn, backend, calendar, config, logger, run_id, day, control
        )
    return all_results
