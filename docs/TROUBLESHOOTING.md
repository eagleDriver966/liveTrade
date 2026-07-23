# Troubleshooting Guide

Structured JSON logs (`logs/hdb_YYYYMMDD.jsonl`) and readable logs
(`logs/hdb_YYYYMMDD.log`) capture every action. Screenshots are written to
`paths.screenshots_root` on failures.

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `AutomationError: pywinauto is required ... only available on Windows` | Running the real backend off Windows | Use `automation.backend = "mock"` for dev/tests; run live collection on Windows. |
| `panel_verification_failed` | Active panel could not be confirmed by multiple signals | Re-run diagnostics; re-assign panel positions; check `panel_signatures` in config. Collection refuses destructive More when unconfirmed. |
| `history_load_timeout` | History page did not load in time | Increase `timeouts.history_load_seconds`; confirm the date exists and Trade Ideas is responsive. |
| `save_failed: save_as_timeout` | Save As dialog did not appear | Increase `timeouts.save_as_dialog_seconds`; verify `selectors.save_contents_menu_path`; check the screenshot. No More is performed after a failed save. |
| `overwrite_prompted` in logs | Target filename already existed | Handled automatically: the overwrite is cancelled and a unique filename is used. Never approved. |
| `parse_failed` / `page_validation_failed` | Malformed CSV or wrong-session content | Session marked `INCOMPLETE`; the raw file is retained. Inspect the CSV and screenshot; fix selectors/panel mapping and re-run. |
| `file_not_settled` | Export file kept changing size / stayed empty | Increase `timeouts.file_settle_seconds`; check disk/AV interference. |
| Session ends too early | Small final page treated as conclusive | Ensure `collection.min_conclusive_rows` (default 1000) and `min_completion_signals` (default 2) are set; review completion signals in the log. |
| `integrity_check failed` | Corrupt SQLite file | Migration aborts. Restore from `backups/` (see Backup & Recovery). |
| GUI won't start (`Tkinter is not available`) | Tk not installed | Install Tk (`python3-tk` on Debian/Ubuntu; bundled on Windows). |
| DB locked errors | Another writer/connection open | Close other tools; WAL mode is enabled; retry. |

## Diagnosing wrong-panel collection

Before any History/More/export, the active panel is verified with multiple
signals (tab index, column signature, session signature). If they disagree the
run stops with a screenshot. Re-run **Diagnostic Setup** and **Assign Panel
Positions** to refresh signatures after any Trade Ideas layout change.

## Where to look

* Per-run truth: `historical_exports/.../run_*/manifest.json`.
* DB bookkeeping: `collection_runs`, `collection_days`, `collection_sessions`,
  `history_export_parts`.
* Data + lineage: `alerts_raw`, `alerts_normalized`, `alert_sources`,
  `rejected_rows`.
* `python Hdb.py --config config.json reconcile` cross-checks DB vs disk
  checksums and manifests.
