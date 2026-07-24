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
from . import migrations, reconcile, repository as repo
from .automation import make_backend
from .calendar_util import ExchangeCalendar
from .collector import collect_date, collect_range, collect_session
from .config import Config, load_config
from .control import CollectionControl
from .diagnostics import assign_panel, enumerate_positions, readiness, run_discovery
from .import_existing import import_export_tree
from .logging_util import StructuredLogger
from .paths import new_run_id
from .status import Status


class ProductionBlockedError(RuntimeError):
    """Raised when production collection is attempted with the mock backend.

    The mock backend must never write simulated data into the real database.
    """


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
    def connect_db(self):
        db_path = self.config.get("paths", "database", default="trade_ideas.db")
        conn = dbmod.connect(db_path)
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
        db_path = self.config.get("paths", "database", default="trade_ideas.db")
        backups_root = self.config.get("paths", "backups_root", default="backups")
        return migrations.run_full_migration(db_path, backups_root, logger=self.logger)

    # -- diagnostics ---------------------------------------------------------
    def run_diagnostics(self) -> dict[str, Any]:
        diag_root = self.config.get("paths", "diagnostics_root", default="diagnostics")
        return run_discovery(self.backend(), diag_root, logger=self.logger)

    def enumerate_panel_positions(self) -> list[dict[str, Any]]:
        return enumerate_positions(self.backend())

    def assign_panel(self, session: str, position: int, signature=None) -> None:
        assign_panel(self.config, session, position, signature, logger=self.logger)

    def readiness(self) -> dict[str, Any]:
        return readiness(self.config)

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

    # -- collection ----------------------------------------------------------
    def collect_single_date(
        self, conn, trading_date: date, control: CollectionControl | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_real_backend()
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
        self._require_real_backend()
        run_id = run_id or new_run_id()
        repo.create_run(conn, run_id, "single_session", None, trading_date,
                        trading_date, backend=self.backend_name)
        self.logger.run_id = run_id
        result = collect_session(
            conn, self.backend(), self.calendar, self.config, self.logger,
            run_id, trading_date, session, control,
        )
        return {"run_id": run_id, "result": result.to_dict()}

    def resume_incomplete(
        self, conn, control: CollectionControl | None = None
    ) -> dict[str, Any]:
        """Re-collect incomplete sessions safely.

        Each incomplete session is recollected in a NEW run directory (earlier
        runs are never overwritten); overlaps are deduped on import.
        """
        self._require_real_backend()
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
        exports_root = self.config.get("paths", "exports_root", default="historical_exports")
        summary = import_export_tree(conn, exports_root, self.calendar, run_id, logger=self.logger)
        repo.set_run_status(conn, run_id, Status.IMPORTED)
        return {"run_id": run_id, "summary": summary.as_dict()}

    # -- verify / reconcile / status ----------------------------------------
    def verify(self, conn) -> dict[str, Any]:
        return reconcile.verify_collection(conn)

    def reconcile(self, conn) -> dict[str, Any]:
        exports_root = self.config.get("paths", "exports_root", default="historical_exports")
        return reconcile.reconcile(conn, exports_root).as_dict()

    def resume_plan(self, conn) -> dict[str, Any]:
        return reconcile.resume_plan(conn)
