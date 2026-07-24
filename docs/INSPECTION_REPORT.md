# Inspection Report (Phase 1-2)

**Date:** 2026-07-23
**Repository:** `liveTrade`
**Scope:** Inspect the existing application and database before extending.

## 1. Key finding: no pre-existing `Hdb.py`

The task described extending an *existing* `Hdb.py` application with an existing
SQLite database (`alerts_flat`, CSV parsing, dedup, calendar view, Excel export,
etc.). A full inspection of the repository found **no such application**:

| Check | Result |
| --- | --- |
| `Hdb.py` anywhere on disk | **Not found** |
| Python source in repo (excluding deps) | **None** (only `zone/node_modules/*` unrelated) |
| SQLite database files (`*.db`, `*.sqlite`) | **None** |
| `liveTrade` repository contents | Only `README.md` (single line `# liveTrade`) |
| `liveTrade` git history | One commit: `fbfefeb Initial commit` |

Conclusion: the "existing application" does not exist in this repository. The
system was therefore **built from scratch**, implementing every capability the
task attributed to the prior `Hdb.py` (robust CSV parsing, dynamic header-row
detection, timestamp parsing, `alerts_flat` storage, deduplication, calendar
date handling, manual CSV upload/import, Excel export, selected-date export, and
SQLite backup/export) plus the new historical-collection architecture.

If a real `Hdb.py` exists elsewhere (e.g. a private machine), share it and the
core parsing/DB layers here can be reconciled with it; the importer already
preserves and writes the legacy `alerts_flat` table for backward compatibility.

## 2. Environment

| Item | Value |
| --- | --- |
| OS (dev/CI) | Ubuntu 24.04 (Linux x86_64) |
| Python | 3.12.3 |
| Windows / Trade Ideas app | **Not present** (Linux VM) |
| `pywinauto` executable here | **No** (Windows-only; imported lazily) |

Because development/CI runs on Linux, the live Windows UI automation cannot be
executed or validated in this environment. All Windows interaction is isolated
behind `hdb.automation.base.AutomationBackend` and exercised through a
deterministic simulator (`MockBackend`). The real `pywinauto` backend must be
verified on the user's Windows machine (see `UNRESOLVED_SELECTORS.md`).

## 3. Baseline database schema (as created by this project)

Since no DB existed, migration `v1` establishes the legacy baseline
(`alerts_flat`) if absent and never alters it if present. Migration `v2` adds the
collection + lineage tables. See `IMPLEMENTATION_REPORT.md` and
`hdb/migrations.py`.

## 4. What was preserved vs added

* **Preserved / implemented as baseline:** `alerts_flat` table (the alert
  store); CSV parsing; header detection; timestamp parsing; deduplication (now
  via a stable SHA-256 fingerprint); date handling; import.
* **Added (minimum needed to track collection):** `collection_runs`,
  `collection_sessions`, `history_export_parts`, `alert_sources`,
  `rejected_rows` (plus `schema_versions` for migration tracking); the
  collection engine; automation adapters; diagnostics; resume/reconciliation;
  Tkinter GUI + CLI.
* **Explicitly out of scope (not built):** trading signals, scoring models,
  profit analysis, dashboards, strategy backtests, Excel reporting, broker
  integrations.
