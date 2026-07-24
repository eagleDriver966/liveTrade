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

## Mock backend is blocked for production

When `automation.backend = "mock"`, all collection actions are **blocked** and
refuse to write to the real database (the CLI exits with code 3; the GUI shows a
blocked dialog). The mock backend is only for diagnostics and the automated
tests. Set `automation.backend = "pywinauto"` on the Windows machine to collect.

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
