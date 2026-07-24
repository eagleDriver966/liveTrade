#!/usr/bin/env python3
"""Hdb - Trade Ideas historical-alert collection system.

This is the main entry point.  It provides:

* a Tkinter GUI (thread-safe via ``queue.Queue`` + ``root.after``) exposing
  Diagnostic Setup, Assign Panel Positions, Collect Single Date, Collect Date
  Range, Resume Incomplete Run, Import Existing Exports, Verify Collection, View
  Collection Status, Open Export Folder, Cancel, Pause, Resume and Settings;
* a command-line interface for headless operation, automation and testing.

All heavy work runs in worker threads or the CLI process and goes through
:class:`hdb.app.HdbApp`; worker threads never touch Tkinter widgets directly.

SAFETY: this tool performs *historical research collection only*.  It contains
no order/trade/brokerage-execution code paths, never approves overwrite
confirmations, and never deletes raw exports or database records.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
from datetime import date, datetime
from typing import Any

from hdb.app import GatesNotPassedError, HdbApp, ProductionBlockedError
from hdb.control import CollectionControl, ProgressEvent


# ===========================================================================
# CLI
# ===========================================================================
def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="Hdb", description=__doc__)
    parser.add_argument("--config", default="config.json", help="Path to config JSON")
    parser.add_argument("--echo", action="store_true", help="Echo logs to stdout")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="Backup + migrate the database")
    sub.add_parser("backend-status", help="Show OS + backend status (REAL/MOCK/ERROR)")
    sub.add_parser("gates", help="Show verification gate status")
    sub.add_parser("diagnostics", help="Run diagnostic discovery")

    p_assign = sub.add_parser("assign", help="Assign a panel position to a session")
    p_assign.add_argument("session", choices=["HPRE", "NHP", "HPOST"])
    p_assign.add_argument("position", type=int)

    p_vp = sub.add_parser("verify-panel", help="Activate + verify an assigned panel")
    p_vp.add_argument("session", choices=["HPRE", "NHP", "HPOST"])
    p_vp.add_argument("--confirm", action="store_true",
                      help="Record the panel as visually confirmed")

    p_t1 = sub.add_parser("test-one-page", help="Export+parse ONE page (no More, no import)")
    p_t1.add_argument("date", help="YYYY-MM-DD")
    p_t1.add_argument("session", choices=["HPRE", "NHP", "HPOST"])

    sub.add_parser("approve-page", help="Import the last validated one-page export")

    p_tm = sub.add_parser("test-one-more", help="One More transition -> part_002 (no auto-continue)")
    p_tm.add_argument("date", help="YYYY-MM-DD")
    p_tm.add_argument("session", choices=["HPRE", "NHP", "HPOST"])

    p_cd = sub.add_parser("collect-date", help="Collect one trading date (3 sessions)")
    p_cd.add_argument("date", help="YYYY-MM-DD")

    p_cs = sub.add_parser("collect-session", help="Collect one session on one date")
    p_cs.add_argument("date", help="YYYY-MM-DD")
    p_cs.add_argument("session", choices=["HPRE", "NHP", "HPOST"])

    p_cr = sub.add_parser("collect-range", help="Collect newest..boundary backward")
    p_cr.add_argument("--newest", default=None, help="YYYY-MM-DD (default: newest safe day)")
    p_cr.add_argument("--boundary", default=None, help="YYYY-MM-DD (default: config)")

    sub.add_parser("import", help="Import existing exports under exports_root")
    sub.add_parser("verify", help="Verify collection (counts + integrity + reconcile)")
    sub.add_parser("reconcile", help="Reconcile DB vs on-disk parts")
    sub.add_parser("resume", help="Show resume plan for incomplete work")
    sub.add_parser("resume-run", help="Resume: re-collect incomplete sessions")
    sub.add_parser("status", help="Show collection status")
    sub.add_parser("readiness", help="Show readiness for unattended collection")
    sub.add_parser("gui", help="Launch the Tkinter GUI")

    args = parser.parse_args(argv)
    app = HdbApp(config_path=args.config, echo_logs=args.echo)

    if args.command == "gui":
        return launch_gui(app)

    if args.command == "migrate":
        _print(app.migrate())
        return 0

    if args.command == "backend-status":
        status = app.backend_status()
        print(status["label"])
        _print(status)
        return 0 if status["code"] != "ERROR" else 4

    if args.command == "gates":
        _print(app.gates_status())
        return 0

    if args.command == "diagnostics":
        _print(app.run_diagnostics())
        return 0

    if args.command == "verify-panel":
        _print(app.verify_panel(args.session, confirm=args.confirm))
        return 0

    if args.command == "test-one-page":
        try:
            _print(app.test_one_page(args.session, date.fromisoformat(args.date)))
        except (ProductionBlockedError, GatesNotPassedError) as exc:
            print(f"BLOCKED: {exc}", file=sys.stderr)
            return 3
        return 0

    if args.command == "test-one-more":
        try:
            _print(app.test_one_more(args.session, date.fromisoformat(args.date)))
        except (ProductionBlockedError, GatesNotPassedError) as exc:
            print(f"BLOCKED: {exc}", file=sys.stderr)
            return 3
        return 0

    if args.command == "assign":
        # Capture the live signature for the position, if available.
        signature = None
        try:
            positions = app.enumerate_panel_positions()
            for entry in positions:
                if entry["position"] == args.position:
                    signature = entry["signature"]
        except Exception:
            signature = None
        app.assign_panel(args.session, args.position, signature)
        _print(app.readiness())
        return 0

    if args.command == "readiness":
        _print(app.readiness())
        return 0

    # Commands that need the DB.
    conn = app.connect_db()
    try:
        if args.command == "collect-date":
            _print(app.collect_single_date(conn, date.fromisoformat(args.date)))
        elif args.command == "collect-session":
            _print(app.collect_one_session(conn, date.fromisoformat(args.date), args.session))
        elif args.command == "collect-range":
            newest = date.fromisoformat(args.newest) if args.newest else None
            boundary = date.fromisoformat(args.boundary) if args.boundary else None
            _print(app.collect_date_range(conn, newest=newest, boundary=boundary))
        elif args.command == "resume-run":
            _print(app.resume_incomplete(conn))
        elif args.command == "approve-page":
            _print(app.approve_and_import_page(conn))
        elif args.command == "import":
            _print(app.import_existing(conn))
        elif args.command == "verify":
            _print(app.verify(conn))
        elif args.command == "reconcile":
            _print(app.reconcile(conn))
        elif args.command == "resume":
            _print(app.resume_plan(conn))
        elif args.command == "status":
            _print(app.verify(conn))
        else:  # pragma: no cover
            parser.error(f"Unknown command {args.command}")
    except (ProductionBlockedError, GatesNotPassedError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 3
    finally:
        conn.close()
    return 0


# ===========================================================================
# GUI
# ===========================================================================
def launch_gui(app: HdbApp) -> int:  # pragma: no cover - requires a display
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
    except Exception as exc:
        print(f"Tkinter is not available: {exc}", file=sys.stderr)
        return 2

    gui = TradeIdeasGUI(app, tk, ttk, scrolledtext, messagebox, simpledialog, filedialog)
    gui.run()
    return 0


class TradeIdeasGUI:  # pragma: no cover - requires a display
    """Tkinter GUI.  Worker threads communicate only via a queue drained by
    ``root.after``; they never modify widgets directly."""

    def __init__(self, app, tk, ttk, scrolledtext, messagebox, simpledialog, filedialog):
        self.app = app
        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.simpledialog = simpledialog
        self.filedialog = filedialog

        self.progress_queue: "queue.Queue[ProgressEvent]" = queue.Queue()
        self.control = CollectionControl(self.progress_queue)
        self.worker: threading.Thread | None = None

        self.root = tk.Tk()
        self.root.title("Hdb - Trade Ideas Historical Collection")
        self.root.geometry("900x640")

        self._build_widgets(scrolledtext)
        self._refresh_banner()
        self._poll_queue()

    def _refresh_banner(self):
        try:
            status = self.app.backend_status(probe=True)
        except Exception as exc:
            status = {"label": "WINDOWS BACKEND ERROR", "code": "ERROR",
                      "message": str(exc)}
        colors = {"REAL_READY": "#1b7a1b", "MOCK": "#b8860b", "ERROR": "#a11"}
        self.banner.config(text=status["label"],
                           bg=colors.get(status["code"], "#666"))
        self._append(f"[backend] {status['label']} - {status.get('message','')}")

    def _build_widgets(self, scrolledtext):
        tk, ttk = self.tk, self.ttk

        # Prominent backend-status banner.
        self.banner = tk.Label(self.root, text="(checking backend...)",
                               font=("TkDefaultFont", 12, "bold"),
                               fg="white", bg="#666", pady=6)
        self.banner.pack(side=tk.TOP, fill=tk.X)

        toolbar = ttk.Frame(self.root)
        toolbar.pack(side=tk.TOP, fill=tk.X, padx=6, pady=6)

        buttons = [
            ("Diagnostic Setup", self.on_diagnostics),
            ("Assign Panel Positions", self.on_assign),
            ("Verify Panels", self.on_verify_panels),
            ("Test One Page Export", self.on_test_one_page),
            ("Test One More Transition", self.on_test_one_more),
            ("View Collection Status", self.on_status),
            ("Collect Single Date", self.on_collect_single),
            ("Collect Date Range", self.on_collect_range),
            ("Resume Incomplete Run", self.on_resume_run),
        ]
        for i, (label, cmd) in enumerate(buttons):
            ttk.Button(toolbar, text=label, command=cmd).grid(
                row=i // 3, column=i % 3, padx=3, pady=3, sticky="ew"
            )

        control_bar = ttk.Frame(self.root)
        control_bar.pack(side=tk.TOP, fill=tk.X, padx=6)
        ttk.Button(control_bar, text="Pause", command=self.on_pause).pack(side=tk.LEFT, padx=3)
        ttk.Button(control_bar, text="Resume", command=self.on_resume_worker).pack(side=tk.LEFT, padx=3)
        ttk.Button(control_bar, text="Cancel", command=self.on_cancel).pack(side=tk.LEFT, padx=3)
        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(control_bar, textvariable=self.status_var).pack(side=tk.RIGHT, padx=8)

        self.log = scrolledtext.ScrolledText(self.root, height=28)
        self.log.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=True, padx=6, pady=6)

    # -- worker plumbing -----------------------------------------------------
    def _busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _start_worker(self, target, *a, **kw):
        if self._busy():
            self.messagebox.showwarning("Busy", "A task is already running.")
            return
        self.control = CollectionControl(self.progress_queue)

        def runner():
            conn = self.app.connect_db()
            try:
                target(conn, *a, **kw)
            except Exception as exc:
                self.progress_queue.put(ProgressEvent("error", str(exc)))
            finally:
                conn.close()
                self.progress_queue.put(ProgressEvent("done"))

        self.worker = threading.Thread(target=runner, daemon=True)
        self.worker.start()
        self.status_var.set("Running...")

    def _poll_queue(self):
        try:
            while True:
                event = self.progress_queue.get_nowait()
                self._append(f"[{event.kind}] {event.message} {event.data if event.data else ''}")
                if event.kind == "done":
                    self.status_var.set("Idle")
                elif event.kind == "error":
                    self.status_var.set("Error")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)

    def _append(self, text: str):
        self.log.insert(self.tk.END, text.rstrip() + "\n")
        self.log.see(self.tk.END)

    # -- button handlers -----------------------------------------------------
    def on_diagnostics(self):
        def task(conn):
            report = self.app.run_diagnostics()
            self.progress_queue.put(ProgressEvent("diagnostics", "report written",
                                                  {"json": report.get("json_path")}))
        self._start_worker(task)

    def on_assign(self):
        try:
            positions = self.app.enumerate_panel_positions()
        except Exception as exc:
            self.messagebox.showerror("Assign", f"Could not enumerate panels: {exc}")
            return
        summary = "\n".join(
            f"pos {p['position']}: {p.get('signature', {}).get('session')}" for p in positions
        )
        for session in ("HPRE", "NHP", "HPOST"):
            pos = self.simpledialog.askinteger(
                "Assign Panel", f"{summary}\n\nTab position for {session}:")
            if pos is None:
                continue
            sig = next((p["signature"] for p in positions if p["position"] == pos), None)
            self.app.assign_panel(session, pos, sig)
        self._append(f"Panel assignment: {self.app.readiness()}")

    def _blocked_if_mock(self) -> bool:
        if self.app.backend_name.lower() == "mock":
            self.messagebox.showerror(
                "Blocked",
                "Production collection is blocked while backend='mock'. "
                "Set automation.backend='pywinauto' on the Windows machine.")
            return True
        return False

    def on_collect_single(self):
        if self._blocked_if_mock():
            return
        d = self.simpledialog.askstring("Collect Single Date", "Date (YYYY-MM-DD):")
        if not d:
            return

        def task(conn):
            res = self.app.collect_single_date(conn, date.fromisoformat(d), self.control)
            self.progress_queue.put(ProgressEvent("result", "collect-date complete", res))
        self._start_worker(task)

    def on_collect_range(self):
        if self._blocked_if_mock():
            return
        if not self.messagebox.askyesno(
            "Collect Date Range",
            "Run a full backward range collection to the configured boundary?"
        ):
            return

        def task(conn):
            res = self.app.collect_date_range(conn, control=self.control)
            self.progress_queue.put(ProgressEvent("result", "collect-range complete", res))
        self._start_worker(task)

    def on_resume_run(self):
        if self.app.backend_name.lower() == "mock":
            self.messagebox.showerror(
                "Blocked", "Collection is blocked while backend='mock'.")
            return

        def task(conn):
            res = self.app.resume_incomplete(conn, self.control)
            self.progress_queue.put(ProgressEvent("resume", "resume complete", res))
        self._start_worker(task)

    def on_verify_panels(self):
        for session in ("HPRE", "NHP", "HPOST"):
            try:
                res = self.app.verify_panel(session, confirm=False)
            except Exception as exc:
                self._append(f"[verify-panel] {session} error: {exc}")
                continue
            if not res.get("ok"):
                self._append(f"[verify-panel] {session}: {res.get('error')}")
                continue
            confirmed = self.messagebox.askyesno(
                "Verify Panel",
                f"{session} activated at tab position {res['position']}.\n"
                f"Screenshot: {res.get('screenshot')}\n\n"
                f"Is the correct {session} panel visibly active?")
            if confirmed:
                self.app.verify_panel(session, confirm=True)
                self._append(f"[verify-panel] {session}: CONFIRMED (pos {res['position']})")
            else:
                self._append(f"[verify-panel] {session}: not confirmed")

    def on_test_one_page(self):
        if self._blocked_if_mock():
            return
        d = self.simpledialog.askstring("Test One Page", "Date (YYYY-MM-DD):")
        if not d:
            return
        session = self.simpledialog.askstring("Test One Page", "Session (HPRE/NHP/HPOST):")
        if not session:
            return

        def task(conn):
            res = self.app.test_one_page(session, date.fromisoformat(d))
            self.progress_queue.put(ProgressEvent("test_one_page", "one-page result", res))
        self._start_worker(task)

    def on_test_one_more(self):
        if self._blocked_if_mock():
            return
        d = self.simpledialog.askstring("Test One More", "Date (YYYY-MM-DD):")
        if not d:
            return
        session = self.simpledialog.askstring("Test One More", "Session (HPRE/NHP/HPOST):")
        if not session:
            return

        def task(conn):
            res = self.app.test_one_more(session, date.fromisoformat(d))
            self.progress_queue.put(ProgressEvent("test_one_more", "one-more result", res))
        self._start_worker(task)

    def on_status(self):
        def task(conn):
            # View Collection Status includes row-count reconciliation.
            res = self.app.verify(conn)
            self.progress_queue.put(ProgressEvent("status", "status", res))
        self._start_worker(task)

    def on_pause(self):
        self.control.request_pause()
        self.status_var.set("Paused")

    def on_resume_worker(self):
        self.control.resume()
        self.status_var.set("Running...")

    def on_cancel(self):
        self.control.request_cancel()
        self.status_var.set("Cancelling...")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    raise SystemExit(cli())
