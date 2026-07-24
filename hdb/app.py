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
from . import diagnostic_tests, gates, migrations, reconcile, repository as repo
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

        Sets the ``real_backend_initialized`` gate when the real backend is ready.
        """
        status = determine_backend_status(
            self.backend_name,
            automation_config=self.config.get("automation", default={}),
            probe=probe,
        )
        gates.set_gate(self.config, "real_backend_initialized",
                       passed=status.ready, save=False,
                       label=status.label, message=status.message)
        return status.to_dict()

    # -- diagnostics ---------------------------------------------------------
    def run_diagnostics(self) -> dict[str, Any]:
        diag_root = self.config.get("paths", "diagnostics_root", default="diagnostics")
        return run_discovery(self.backend(), diag_root, logger=self.logger)

    def enumerate_panel_positions(self) -> list[dict[str, Any]]:
        return enumerate_positions(self.backend())

    def assign_panel(self, session: str, position: int, signature=None) -> None:
        assign_panel(self.config, session, position, signature, logger=self.logger)
        if self.config.panels_assigned():
            gates.set_gate(self.config, "panels_assigned", save=True)

    def verify_panel(self, session: str, confirm: bool = False) -> dict[str, Any]:
        """Activate an assigned panel, capture a screenshot + control signature.

        With ``confirm=True`` (after the operator visually confirms), the panel
        is recorded as verified; when all three are verified the
        ``panels_verified`` gate passes.
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
            "session": session,
            "position": position,
            "role": session,
            "signature": signature,
            "screenshot": shot,
            "confirmed": bool(confirm),
        }
        verified = self.config.data.setdefault("panel_verified", {})
        verified[session] = record
        if self.config.path:
            self.config.save()
        # Gate passes only when all three are confirmed.
        all_confirmed = all(
            (verified.get(s) or {}).get("confirmed") for s in ("HPRE", "NHP", "HPOST")
        )
        if all_confirmed:
            gates.set_gate(self.config, "panels_verified", save=True)
        return {"ok": True, **record}

    def readiness(self) -> dict[str, Any]:
        base = readiness(self.config)
        base["gates"] = gates.gates_status(self.config)
        base["missing_gates"] = gates.missing_gates(self.config)
        base["backend_name"] = self.backend_name
        return base

    # -- gates ---------------------------------------------------------------
    def gates_status(self) -> dict[str, Any]:
        return {
            "gates": gates.gates_status(self.config),
            "missing": gates.missing_gates(self.config),
            "all_passed": gates.all_passed(self.config),
        }

    def _require_gates_passed(self) -> None:
        missing = gates.missing_gates(self.config)
        if missing:
            raise GatesNotPassedError(
                "Collection is blocked until all verification gates pass. "
                f"Missing: {missing}. Run diagnostics, assign+verify panels, and "
                "the one-page / one-more / one-session tests first."
            )

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

    def _require_gates_through_one_more(self) -> None:
        needed = [g for g in gates.GATE_KEYS if g != "one_session_reconciled"]
        missing = [g for g in needed if not gates.gate_passed(self.config, g)]
        if missing:
            raise GatesNotPassedError(
                f"This step is blocked until earlier gates pass. Missing: {missing}."
            )

    # -- gated diagnostic exports (no auto-continue) -------------------------
    def test_one_page(self, session: str, trading_date: date) -> dict[str, Any]:
        """Export + parse exactly one real history page (no More, no import)."""
        self._require_real_backend()
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
            # A successful export proves History, Save Contents, and Save As.
            for g in ("history_selector_verified", "save_contents_verified",
                      "save_as_verified", "one_page_exported"):
                gates.set_gate(self.config, g, save=False)
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
            if self.config.path:
                self.config.save()
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
        validated one-page export first."""
        self._require_real_backend()
        if not gates.gate_passed(self.config, "one_page_exported"):
            return {"ok": False, "error": "one_page_export_gate_not_passed"}
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
            gates.set_gate(self.config, "one_more_verified", save=True)
        return report.to_dict()

    # -- collection ----------------------------------------------------------
    def collect_single_date(
        self, conn, trading_date: date, control: CollectionControl | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_real_backend()
        self._require_gates_passed()
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
        self._require_real_backend()
        self._require_gates_passed()
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
        # One full session is the step that produces the final gate; it requires
        # all earlier gates (through the one-More test) to have passed.
        self._require_real_backend()
        self._require_gates_through_one_more()
        run_id = run_id or new_run_id()
        repo.create_run(conn, run_id, "single_session", None, trading_date,
                        trading_date, backend=self.backend_name)
        self.logger.run_id = run_id
        result = collect_session(
            conn, self.backend(), self.calendar, self.config, self.logger,
            run_id, trading_date, session, control,
        )
        # Reconcile row counts; only mark the session gate on a clean, verified run.
        recon = reconcile.verify_collection(conn).get("row_count_reconciliation", {})
        if result.status in (Status.VERIFIED, Status.EMPTY_VERIFIED) and recon.get("balanced"):
            gates.set_gate(self.config, "one_session_reconciled", save=True,
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
        self._require_real_backend()
        self._require_gates_passed()
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
