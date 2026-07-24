# Implementation Report

## Summary

The Trade Ideas historical-alert collection system was implemented from scratch
(no prior `Hdb.py` existed - see `INSPECTION_REPORT.md`). The platform-independent
core is fully implemented and tested on Linux; the Windows automation is isolated
behind an adapter interface with a real `pywinauto` backend (to be verified on
Windows) and a deterministic mock backend used by the tests.

## Phase status

| Phase | Description | Status |
| --- | --- | --- |
| 1 | Inspect existing application/database | Done - none existed |
| 2 | Inspection report | Done (`INSPECTION_REPORT.md`) |
| 3 | Diagnostic discovery | Done (`diagnostics.py`, mock-verified) |
| 4 | Panel-position assignment | Done (`diagnostics.assign_panel`, config) |
| 5 | Safe database migrations | Done (`migrations.py`, backup+WAL+integrity) |
| 6 | One File-menu export without More | Done (`collector`, one-page test) |
| 7 | Validate + import one page | Done (importer + validation tests) |
| 8 | Export-before-More for one session/date | Done (`collect_session`) |
| 9 | All three sessions for one date | Done (`collect_date`) |
| 10 | Date-range collection | Done (`collect_range`) |
| 11 | Restart + recovery | Done (`reconcile.resume_plan`, new-run-dir policy) |
| 12 | Tests + reconciliation reports | Done (83 tests, `reconcile.py`) |
| 13 | Unattended operation | **Gated** on Windows selector verification |

## Key design decisions

* **Export-before-More is enforced in code.** `collect_session` will not call
  `backend.click_more()` until the current page is saved, settled on disk,
  parsed, validated, checksummed, registered, imported, added to the manifest,
  and marked VALIDATED. A failed save returns `INCOMPLETE` and never advances.
* **Completion uses multiple signals** (`collection.evaluate_completion`):
  boundary reached, explicit no-more-history, repeated page
  (checksum/page-fingerprint/no-new-events), and no-backward-progress. A page
  with `< min_conclusive_rows` (default 1000) is never conclusive on a single
  signal.
* **Source lineage is preserved.** The alert is stored once in `alerts_flat`
  (deduped by fingerprint); every occurrence is recorded in `alert_sources`
  with run id, trading date, session, panel position, source filename, part
  number, and source row number (page overlap preserved).
* **Stable fingerprints** (`fingerprint.py`) use trading date, timestamp, alert
  type, symbol, price, alert count, volume, and session (not only
  symbol/type/time/price). They exclude filename/part/row so the same alert
  fingerprints identically across overlapping pages; a `fp-v1-lite` fallback
  handles missing volume/count and the version used is recorded.
* **Production is blocked with the mock backend.** All application collection
  paths raise `ProductionBlockedError` when `automation.backend='mock'`, so
  simulated data can never enter the real database. Tests/demo drive the engine
  directly against throwaway temp databases.
* **Migrations are transactional and backup-first**, enable WAL, run
  `PRAGMA integrity_check`, inspect existing structure, and record
  `schema_versions`. `alerts_flat` is preserved (a `session` column is added if
  missing) and schema-drift safe; only the 5 tracking tables are added.
* **Thread-safe GUI**: worker threads never touch Tk widgets; they push
  `ProgressEvent`s onto a `queue.Queue` drained by `root.after`. Cancel/pause/
  resume are cooperative.
* **Safety**: no order/trade/brokerage code paths exist; overwrite dialogs are
  cancelled (never approved); raw files and DB records are never deleted;
  incomplete data is never marked verified.

## Validation evidence

* `pytest -q` -> **87 passed** (see `docs/TEST_RESULTS.md`).
* `python examples/demo_collection.py` -> **PASS**: 2 days collected, 6 sessions,
  12 parts, 24 exported rows -> 20 distinct alerts in `alerts_flat` (overlaps
  deduped), 24 `alert_sources` occurrences, 0 rejected, both dates VERIFIED,
  row-count reconciliation balanced, re-collect dedup stable.
* GUI rendered under Xvfb showing a full VERIFIED run (screenshot in the PR).

## Not validated in this environment

Live Windows UI automation (real `pywinauto` against Trade Ideas) cannot run on
the Linux dev/CI VM. The `pywinauto` backend imports cleanly (lazy) but raises a
clear error if used off-Windows. All selectors must be confirmed on the target
machine - see `SELECTOR_DISCOVERY_REPORT.md` and `UNRESOLVED_SELECTORS.md`.
