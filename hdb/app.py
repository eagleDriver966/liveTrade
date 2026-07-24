"""Application service layer.

Wires config, database, calendar, logger and automation backend together and
exposes high-level operations used by both the Tkinter GUI (in worker threads)
and the command-line interface.  Nothing here touches Tkinter.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any

from . import db as dbmod
from . import diagnostic_tests, gates, migrations, production_gate, reconcile, repository as repo
from .automation import make_backend
from .backend_status import determine_backend_status
from .calendar_util import ExchangeCalendar
from .collector import collect_date, collect_range, collect_session
from .config import Config, load_config
from .control import CollectionControl
from .csv_parser import parse_csv
from .diagnostics import assign_panel, enumerate_positions, readiness, run_discovery
from .import_existing import import_export_tree
from .importer import import_parsed_part
from .logging_util import StructuredLogger
from .paths import new_run_id, sha256_file
from .status import Status


class ProductionBlockedError(RuntimeError):
    """Raised when production collection is attempted with the mock backend.

    The mock backend must never write simulated data into the real database.
    """


class GatesNotPassedError(RuntimeError):
    """Raised when collection is attempted before all verification gates pass."""


class HdbApp:
    def __init__(self, config_path: str = "config.json", echo_logs: bool = False) -> None:
        self.config: Config = load_config(config_path)
        if self.config.path is None:
            self.config.path = config_path
        self.calendar = ExchangeCalendar(
            calendar_name=self.config.get("exchange_calendar", default="XNYS"),
            timezone=self.config.get("timezone", default="America/New_York"),
            session_spec=self.config.get("sessions", default=None),
        )
        self.logs_root = self.config.get("paths", "logs_root", default="logs")
        self.screenshots_root = self.config.get(
            "paths", "screenshots_root", default="logs/screenshots"
        )
        self._backend = None
        self.logger = StructuredLogger(self.logs_root, echo=echo_logs)

    # -- infrastructure ------------------------------------------------------
    def _is_mock(self) -> bool:
        return self.backend_name.lower() == "mock"

    def effective_database_path(self) -> str:
        """Real DB path, or a separate test DB when backend=mock.

        Guarantees mock/simulated data never lands in the production database.
        """
        db_path = self.config.get("paths", "database", default="trade_ideas.db")
        if self._is_mock():
            root, ext = os.path.splitext(db_path)
            return f"{root}.mocktest{ext or '.db'}"
        return db_path

    def effective_exports_root(self) -> str:
        """Real exports dir, or a separate test dir when backend=mock."""
        root = self.config.get("paths", "exports_root", default="historical_exports")
        if self._is_mock():
            return f"{root}_mocktest"
        return root

    def connect_db(self):
        conn = dbmod.connect(self.effective_database_path())
        dbmod.enable_wal(conn)
        return conn

    def backend(self):
        if self._backend is None:
            name = self.config.get("automation", "backend", default="pywinauto")
            kwargs: dict[str, Any] = {"screenshots_dir": self.screenshots_root}
            if name == "pywinauto":
                kwargs["config"] = self.config.get("automation", default={})
            self._backend = make_backend(name, **kwargs)
            # Wire screenshots into the logger.
            self.logger.screenshot_provider = self._backend.screenshot
        return self._backend

    def set_backend(self, backend) -> None:
        """Inject a backend (used by tests)."""
        self._backend = backend
        self.logger.screenshot_provider = backend.screenshot

    # -- migration -----------------------------------------------------------
    def migrate(self) -> dict[str, Any]:
        backups_root = self.config.get("paths", "backups_root", default="backups")
        return migrations.run_full_migration(
            self.effective_database_path(), backups_root, logger=self.logger
        )

    # -- backend status ------------------------------------------------------
    def backend_status(self, probe: bool = True) -> dict[str, Any]:
        """Detect OS + backend readiness (REAL / MOCK / ERROR).

        A mock backend only ever sets ``mock_backend_initialized``.  The real
        ``real_backend_initialized`` gate is set (ephemerally, this execution)
        ONLY when a genuine live pywinauto connection to a running Trade Ideas
        process was established.  Never falls back from real to mock.
        """
        status = determine_backend_status(
            self.backend_name,
            automation_config=self.config.get("automation", default={}),
            probe=probe,
        )
        if self._is_mock():
            gates.set_mock_gate(self.config, "mock_backend_initialized", save=False)
        elif status.ready and (status.detail or {}).get("process_id") is not None:
            detail = status.detail
            sig = f"{detail.get('process_id')}:{detail.get('window_title')}"
            # Ephemeral (per-execution) - not persisted across runs.
            gates.set_real_gate(self.config, "real_backend_initialized", sig, save=False)
        return status.to_dict()

    def _live_process_signature(self) -> tuple[bool, str | None, dict[str, Any]]:
        """Probe the real backend; return (connected, process_signature, status).

        Always (connected=False, None) for the mock backend or off-Windows.
        """
        if self._is_mock():
            return False, None, {"code": "MOCK", "label": "MOCK TEST BACKEND"}
        status = self.backend_status(probe=True)
        detail = status.get("detail") or {}
        pid = detail.get("process_id")
        if status.get("code") == "REAL_READY" and status.get("ready") and pid is not None:
            return True, f"{pid}:{detail.get('window_title')}", status
        return False, None, status

    def _require_live_real_connection(self) -> str:
        connected, sig, status = self._live_process_signature()
        if not connected or not sig:
            raise ProductionBlockedError(
                "A live real Trade Ideas connection is required for this action. "
                f"Backend status: {status.get('label')} - {status.get('message', '')}"
            )
        return sig

    # -- production eligibility (hard, live) ---------------------------------
    def production_eligibility(self) -> dict[str, Any]:
        return production_gate.evaluate(self).to_dict()

    def _require_production_eligible(self) -> None:
        result = production_gate.evaluate(self)
        if not result.eligible:
            raise ProductionBlockedError(
                "Production collection is NOT eligible. Hard production-safety "
                "gate failed:\n" + result.report_text()
            )

    # -- diagnostics ---------------------------------------------------------
    def run_diagnostics(self) -> dict[str, Any]:
        diag_root = self.config.get("paths", "diagnostics_root", default="diagnostics")
        return run_discovery(self.backend(), diag_root, logger=self.logger)

    def enumerate_panel_positions(self) -> list[dict[str, Any]]:
        return enumerate_positions(self.backend())

    def assign_panel(self, session: str, position: int, signature=None) -> None:
        prior = self.config.panel_position(session)
        assign_panel(self.config, session, position, signature, logger=self.logger)
        # Any change to assignments invalidates ALL prior real verifications.
        if prior != position:
            gates.bump_config_version(self.config, save=True)

    def verify_panel(self, session: str, confirm: bool = False) -> dict[str, Any]:
        """Activate an assigned panel, capture a screenshot + control signature.

        Mock backends only ever set the ``mock_panels_verified`` gate.  The real
        ``real_panel_assignments_verified`` gate is set only under a live real
        connection, tagged with the live process signature + config version.
        """
        position = self.config.panel_position(session)
        if position is None:
            return {"ok": False, "error": f"{session} has no assigned position"}
        backend = self.backend()
        backend.ensure_foreground()
        backend.select_panel(position)
        signature = backend.active_panel_signature()
        shot = backend.screenshot(f"verify_panel_{session}_pos{position}")
        record = {
            "session": session, "position": position, "role": session,
            "signature": signature, "screenshot": shot, "confirmed": bool(confirm),
        }
        verified = self.config.data.setdefault("panel_verified", {})
        verified[session] = record
        if self.config.path:
            self.config.save()

        all_confirmed = all(
            (verified.get(s) or {}).get("confirmed") for s in ("HPRE", "NHP", "HPOST")
        )
        result = {"ok": True, **record, "all_confirmed": all_confirmed}
        if not all_confirmed:
            return result

        if self._is_mock():
            gates.set_mock_gate(self.config, "mock_panels_verified", save=True)
            result["mock_note"] = "MOCK TEST RESULTS ONLY"
        else:
            # Real gate: require a live connection and tag with its signature.
            sig = self._require_live_real_connection()
            gates.set_real_gate(self.config, "real_panel_assignments_verified", sig,
                                save=True, all_confirmed=True)
        return result

    def readiness(self) -> dict[str, Any]:
        base = readiness(self.config)
        base["backend_name"] = self.backend_name
        base["gates"] = self.gates_status()
        base["production_eligibility"] = self.production_eligibility()
        return base

    # -- gates ---------------------------------------------------------------
    def gates_status(self) -> dict[str, Any]:
        connected, sig, _ = self._live_process_signature()
        out = {
            "real_gates": gates.real_gates_status(self.config, sig),
            "mock_gates": gates.mock_gates_status(self.config),
            "config_version": gates.get_config_version(self.config),
        }
        if self._is_mock():
            out["status_label"] = "MOCK TEST RESULTS ONLY"
        return out

    # -- date selection helpers ---------------------------------------------
    def newest_collectable_day(self, now: datetime | None = None) -> date:
        """Most recent trading day whose final session ended + safety delay ago."""
        now = now or datetime.now(self.calendar.tz)
        if now.tzinfo is None:
            now = now.replace(tzinfo=self.calendar.tz)
        delay = self.config.get(
            "collection", "current_day_safety_delay_seconds", default=3600
        )
        candidate = self.calendar.last_completed_trading_day(now.date())
        # Walk back until the candidate's HPOST end + delay has passed.
        for _ in range(10):
            end = self.calendar.session_end_with_safety(candidate, delay)
            if now >= end:
                return candidate
            candidate = self.calendar.last_completed_trading_day(
                candidate - timedelta(days=1)
            )
        return candidate

    # -- production safety ---------------------------------------------------
    @property
    def backend_name(self) -> str:
        return self.config.get("automation", "backend", default="pywinauto")

    def _require_real_backend(self) -> None:
        """Block production collection when the backend is the mock simulator.

        Guarantees mock/simulated data can never be written to the real
        database via the application's collection paths.
        """
        if self.backend_name.lower() == "mock":
            raise ProductionBlockedError(
                "Production collection is blocked while automation.backend='mock'. "
                "The mock backend is for diagnostics/tests only and must never "
                "insert simulated data into the real database. Set "
                "automation.backend='pywinauto' on the Windows machine to collect."
            )

    # -- gated diagnostic exports (no auto-continue) -------------------------
    def test_one_page(self, session: str, trading_date: date) -> dict[str, Any]:
        """Export + parse exactly one real history page (no More, no import).

        Requires a live real connection; sets the real one-page gate on success.
        """
        self._require_real_backend()
        sig = self._require_live_real_connection()
        position = self.config.panel_position(session)
        if position is None:
            return {"ok": False, "error": f"{session} has no assigned panel position"}
        run_id_dir = new_run_id()
        report = diagnostic_tests.one_page_export(
            self.backend(), self.calendar, self.config, self.logger,
            session, trading_date, position, self.effective_exports_root(),
            run_id_dir=run_id_dir,
        )
        if report.ok:
            # A successful real export proves History, Save Contents, and Save As.
            gates.set_real_gate(self.config, "real_page_export_verified", sig, save=False)
            self.config.data["diagnostics_state"] = {
                "one_page": {
                    "session": session,
                    "trading_date": trading_date.isoformat(),
                    "run_id_dir": run_id_dir,
                    "abs_path": report.abs_path,
                    "oldest_timestamp": report.oldest_timestamp,
                    "newest_timestamp": report.newest_timestamp,
                    "page_fingerprint": report.page_fingerprint,
                    "parsed_row_count": report.parsed_row_count,
                    "filename": report.filename,
                }
            }
            gates.set_real_gate(self.config, "real_page_export_verified", sig, save=True)
        return report.to_dict()

    def approve_and_import_page(self, conn) -> dict[str, Any]:
        """Import the most recent one-page export into the database (operator
        approval step)."""
        self._require_real_backend()
        state = (self.config.get("diagnostics_state", default={}) or {}).get("one_page")
        if not state or not state.get("abs_path") or not os.path.exists(state["abs_path"]):
            return {"ok": False, "error": "no_validated_one_page_export"}
        trading_date = date.fromisoformat(state["trading_date"])
        session = state["session"]
        run_id = new_run_id()
        repo.create_run(conn, run_id, "one_page_import", None, trading_date,
                        trading_date, backend=self.backend_name)
        parsed = parse_csv(state["abs_path"], trading_date, self.calendar.timezone)
        stats = import_parsed_part(
            conn, parsed, run_id, trading_date, session,
            self.config.panel_position(session), 1,
            os.path.basename(state["abs_path"]), logger=self.logger,
        )
        repo.set_run_status(conn, run_id, Status.IMPORTED)
        return {"ok": True, "run_id": run_id, "stats": stats.as_dict()}

    def test_one_more(self, session: str, trading_date: date) -> dict[str, Any]:
        """Click More exactly once, export part_002, compare.  Requires a
        validated real one-page export first (same config version + process)."""
        self._require_real_backend()
        sig = self._require_live_real_connection()
        if not gates.real_gate_valid(self.config, "real_page_export_verified", sig):
            return {"ok": False, "error": "real_one_page_export_not_valid_for_current_process"}
        state = (self.config.get("diagnostics_state", default={}) or {}).get("one_page")
        if not state:
            return {"ok": False, "error": "no_one_page_state"}
        from .diagnostic_tests import PageTestReport

        first = PageTestReport(
            ok=True, session=session, trading_date=trading_date.isoformat(),
            part_number=1, run_dir="", abs_path=state.get("abs_path"),
            filename=state.get("filename"),
            oldest_timestamp=state.get("oldest_timestamp"),
            newest_timestamp=state.get("newest_timestamp"),
            page_fingerprint=state.get("page_fingerprint"),
            parsed_row_count=state.get("parsed_row_count", 0),
        )
        report = diagnostic_tests.one_more_transition(
            self.backend(), self.calendar, self.config, self.logger,
            session, trading_date, self.effective_exports_root(),
            state["run_id_dir"], first,
        )
        if report.ok:
            gates.set_real_gate(self.config, "real_more_transition_verified", sig, save=True)
        return report.to_dict()

    # -- collection ----------------------------------------------------------
    def collect_single_date(
        self, conn, trading_date: date, control: CollectionControl | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_production_eligible()
        run_id = run_id or new_run_id()
        repo.create_run(
            conn, run_id, "single_date",
            date.fromisoformat(self.config.get("history_boundary_date", default="2026-02-01")),
            trading_date, trading_date, backend=self.backend_name,
        )
        self.logger.run_id = run_id
        results = collect_date(
            conn, self.backend(), self.calendar, self.config, self.logger,
            run_id, trading_date, control,
        )
        repo.set_run_status(conn, run_id, Status.VERIFIED)
        return {"run_id": run_id, "results": {k: v.to_dict() for k, v in results.items()}}

    def collect_date_range(
        self, conn, newest: date | None = None, boundary: date | None = None,
        control: CollectionControl | None = None, run_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_production_eligible()
        run_id = run_id or new_run_id()
        newest = newest or self.newest_collectable_day()
        boundary = boundary or date.fromisoformat(
            self.config.get("history_boundary_date", default="2026-02-01")
        )
        repo.create_run(conn, run_id, "date_range",
                        boundary, newest, boundary, backend=self.backend_name)
        self.logger.run_id = run_id
        results = collect_range(
            conn, self.backend(), self.calendar, self.config, self.logger,
            run_id, newest, boundary, control,
        )
        repo.set_run_status(conn, run_id, Status.VERIFIED)
        return {"run_id": run_id, "days": len(results)}

    def collect_one_session(
        self, conn, trading_date: date, session: str,
        control: CollectionControl | None = None, run_id: str | None = None,
    ) -> dict[str, Any]:
        # One full session is the step that produces the final real gate; it
        # requires a live real connection plus the earlier real gates.
        self._require_real_backend()
        sig = self._require_live_real_connection()
        for gkey in ("real_panel_assignments_verified", "real_page_export_verified",
                     "real_more_transition_verified"):
            if not gates.real_gate_valid(self.config, gkey, sig):
                raise GatesNotPassedError(
                    f"One-session collection blocked: real gate {gkey} is not valid "
                    "for the current process/config version.")
        run_id = run_id or new_run_id()
        repo.create_run(conn, run_id, "single_session", None, trading_date,
                        trading_date, backend=self.backend_name)
        self.logger.run_id = run_id
        result = collect_session(
            conn, self.backend(), self.calendar, self.config, self.logger,
            run_id, trading_date, session, control,
        )
        # Reconcile row counts; only set the real session gate on a clean run.
        recon = reconcile.verify_collection(conn).get("row_count_reconciliation", {})
        if result.status in (Status.VERIFIED, Status.EMPTY_VERIFIED) and recon.get("balanced"):
            gates.set_real_gate(self.config, "real_session_reconciled", sig, save=True,
                                trading_date=trading_date.isoformat(), session=session)
        return {"run_id": run_id, "result": result.to_dict(),
                "row_count_reconciliation": recon}

    def resume_incomplete(
        self, conn, control: CollectionControl | None = None
    ) -> dict[str, Any]:
        """Re-collect incomplete sessions safely.

        Each incomplete session is recollected in a NEW run directory (earlier
        runs are never overwritten); overlaps are deduped on import.
        """
        self._require_production_eligible()
        plan = reconcile.resume_plan(conn)
        recollected: list[dict[str, Any]] = []
        for item in plan["plan"]:
            control and control.check_cancel()
            trading_date = date.fromisoformat(item["trading_date"])
            session = item["session"]
            run_id = new_run_id()
            repo.create_run(conn, run_id, "resume", None, trading_date,
                            trading_date, backend=self.backend_name)
            self.logger.run_id = run_id
            result = collect_session(
                conn, self.backend(), self.calendar, self.config, self.logger,
                run_id, trading_date, session, control,
            )
            recollected.append({"run_id": run_id, **result.to_dict()})
        return {"resumed": recollected, "plan": plan}

    def import_existing(self, conn, run_id: str | None = None) -> dict[str, Any]:
        """Import already-saved CSV exports under ``exports_root`` (recovery/CLI).

        Reads real files on disk; safe to re-run (idempotent dedup).
        """
        run_id = run_id or new_run_id()
        repo.create_run(conn, run_id, "import", None, None, None,
                        backend=self.backend_name)
        summary = import_export_tree(conn, self.effective_exports_root(),
                                     self.calendar, run_id, logger=self.logger)
        repo.set_run_status(conn, run_id, Status.IMPORTED)
        return {"run_id": run_id, "summary": summary.as_dict()}

    # -- verify / reconcile / status ----------------------------------------
    def verify(self, conn) -> dict[str, Any]:
        return reconcile.verify_collection(conn)

    def reconcile(self, conn) -> dict[str, Any]:
        return reconcile.reconcile(conn, self.effective_exports_root()).as_dict()

    def resume_plan(self, conn) -> dict[str, Any]:
        return reconcile.resume_plan(conn)
