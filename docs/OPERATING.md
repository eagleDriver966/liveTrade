# Operating Guide

Follow the phased sequence. **Do not skip to multi-date unattended automation.**

## Phase order

1. Migrate the database.
2. Run diagnostics and confirm the Trade Ideas UI is discoverable.
3. Assign panel positions (HPRE, NHP, HPOST) - never assume the order.
4. Automate one File-menu export **without** using More.
5. Validate and import that one page.
6. Export-before-More for one session, one date.
7. All three sessions for one date.
8. Date-range collection.
9. Restart/recovery.
10. Reconciliation reports.
11. Only then, unattended operation.

## GUI

```bash
python Hdb.py --config config.json gui
```

Buttons: **Diagnostic Setup**, **Assign Panel Positions**, **Collect Single
Date**, **Collect Date Range**, **Resume Incomplete Run**, **View Collection
Status**, plus **Pause / Resume / Cancel**.

Work runs in background threads; the log pane updates via a queue drained by
`root.after`. Pause/Resume/Cancel are cooperative and safe - a cancel is only
honored at safe checkpoints (never mid-save, never right before a destructive
More on unvalidated data).

**View Collection Status** shows per-table counts, VERIFIED dates, and the
row-count reconciliation (exported part rows vs source occurrences + rejected).

## Backend status (shown prominently at startup)

The app detects the OS and backend and displays exactly one of:

* `REAL WINDOWS BACKEND READY` - on Windows, pywinauto imported, Trade Ideas
  window found and connected.
* `MOCK TEST BACKEND` - the mock simulator (tests/diagnostics only).
* `WINDOWS BACKEND ERROR` - the real backend was requested but could not
  initialize; the **exact reason** is shown and the app stays blocked.

The app never silently falls back from the real backend to mock.

```bash
python Hdb.py --config config.json backend-status
```

## Mock backend is blocked for production

When `automation.backend = "mock"`, all collection **and** the real diagnostic
export tests are blocked and refuse to write to the real database (the CLI exits
with code 3; the GUI shows a blocked dialog). Mock uses a **separate test
database and export directory** (suffixes `.mocktest.db` / `_mocktest`) so mock
data never touches production. Set `automation.backend = "pywinauto"` on the
Windows machine to collect.

## Hard production-safety gate (independent of test gates)

`Collect Single Date`, `Collect Date Range` and `Resume Incomplete Run` are
blocked by a **hard, live** production-eligibility check that is independent of
the diagnostic/test gates. All 13 conditions must hold:

1. `operating_system == "Windows"`
2. `backend_type == "windows_real"`
3. `real_backend_connected == true` (live pywinauto connection this execution)
4. Trade Ideas process ID detected
5. Trade Ideas main-window handle verified
6. `mock_mode == false`
7. production database path is not a mock/test path
8. production export directory is not a mock/test directory
9. panel assignments discovered using the current real Trade Ideas process
10. all panel assignments visually confirmed in the current configuration version
11. one real page export passed
12. one real More transition passed
13. one full real session reconciled

```bash
python Hdb.py --config config.json eligibility    # full report + PASS/FAIL per condition
python Hdb.py --config config.json gates          # real + mock gate namespaces (separate)
```

### Real vs mock gates (strictly separated)

Real gates (`real_backend_initialized`, `real_panel_assignments_verified`,
`real_page_export_verified`, `real_more_transition_verified`,
`real_session_reconciled`) can be set only while a genuine real connection is
active, are tagged with the live process signature + configuration version, and
are invalidated whenever panel assignments change.

Mock gates (`mock_backend_initialized`, `mock_panels_verified`,
`mock_page_export_verified`, `mock_more_transition_verified`,
`mock_session_reconciled`) are **TEST ONLY** and never satisfy any production
condition. A mock backend can never set, satisfy, emulate or persist a real
gate. No sequence of mock actions unlocks production controls; under mock the
status reads **MOCK TEST RESULTS ONLY** and the Collect controls are disabled.

### Gated diagnostic sequence (run in order on Windows)

```bash
python Hdb.py --config config.json backend-status              # REAL WINDOWS BACKEND READY
python Hdb.py --config config.json diagnostics                 # discover menus/controls
python Hdb.py --config config.json assign HPRE <pos>           # assign real positions
python Hdb.py --config config.json assign NHP  <pos>
python Hdb.py --config config.json assign HPOST <pos>
python Hdb.py --config config.json verify-panel NHP --confirm  # activate + visually confirm each
python Hdb.py --config config.json test-one-page 2026-02-02 NHP  # export ONE page, no More, no import
python Hdb.py --config config.json approve-page                 # import that page after you review it
python Hdb.py --config config.json test-one-more 2026-02-02 NHP  # ONE More -> part_002, then stop
python Hdb.py --config config.json eligibility                  # confirm all 13 conditions PASS
# one full session reconcile (later objective) sets the final gate; only when
# `eligibility` reports YES are Collect Single/Date Range/Resume unblocked.
```

## CLI

```bash
python Hdb.py --config config.json migrate
python Hdb.py --config config.json diagnostics
python Hdb.py --config config.json assign HPRE 0
python Hdb.py --config config.json assign NHP 1
python Hdb.py --config config.json assign HPOST 2
python Hdb.py --config config.json readiness

python Hdb.py --config config.json collect-session 2026-02-02 NHP   # phase 6-7
python Hdb.py --config config.json collect-date 2026-02-02          # phase 8
python Hdb.py --config config.json collect-range --newest 2026-02-05 --boundary 2026-02-02

python Hdb.py --config config.json import        # import existing CSV exports
python Hdb.py --config config.json verify        # counts + integrity + reconciliation
python Hdb.py --config config.json reconcile     # DB vs on-disk checksums
python Hdb.py --config config.json resume        # show resume plan (read-only)
python Hdb.py --config config.json resume-run    # re-collect incomplete sessions
```

## Current-day safety

The newest collectable trading day is only offered once its final session
(HPOST) has ended **plus** `collection.current_day_safety_delay_seconds`
(default 3600s). Today is never collected until that has passed.

## Status model

`PENDING -> COLLECTING -> EXPORTED -> VALIDATED -> IMPORTED -> VERIFIED`, with
`EMPTY_VERIFIED`, `DUPLICATE_PAGE`, `INCOMPLETE`, `FAILED`, `CANCELLED`.

A **date** is `VERIFIED` only when HPRE, NHP and HPOST are each `VERIFIED` or
`EMPTY_VERIFIED`.

## Completion criteria (per session)

A session is complete when documented signals are met:

* **Session boundary reached** - oldest collected timestamp reaches/passes the
  official session start.
* **Explicit completion** - Trade Ideas reports no-more-history / no-more-results.
* **Repeated page** - same checksum, same alert set, same oldest/newest, or no
  new alert fingerprints.
* **No backward progress** - oldest timestamp fails to move earlier after More.

At least two signals are used where possible; a page with fewer than 1,000 rows
is never conclusive on a single signal alone.

## File layout

```
historical_exports/2026/2026-02-02/NHP/run_YYYYMMDD_HHMMSS/
    NHP_2026-02-02_part_001.csv
    NHP_2026-02-02_part_002.csv
    manifest.json
```

Run directories are immutable. Raw exports are never overwritten or deleted. If
an overwrite-confirmation dialog appears it is **cancelled** and a unique
filename is generated and retried.
