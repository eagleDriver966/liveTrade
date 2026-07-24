# Hdb - Trade Ideas Historical-Alert Collection System

A single-purpose Windows tool whose only objective is to **build and expand the
SQLite Trade Ideas alert database**. It connects to a running Trade Ideas
desktop app, saves each history page to CSV before clicking More, imports every
page into SQLite, deduplicates overlaps, and preserves source lineage.

> **Scope:** database collection only. It does **not** build trading signals,
> scoring models, profit analysis, dashboards, strategy backtests, Excel
> reporting, or broker integrations.

> **Historical research collection only.** No order/trade/brokerage-execution
> code paths exist. It never approves overwrite confirmations, never deletes raw
> exports, and never deletes database records. **Production collection is blocked
> when `backend=mock`** so simulated data can never enter the real database.

> **Note on origin:** No prior `Hdb.py` existed in this repository, so the
> system was built from scratch and implements the previously described
> capabilities plus the new historical-collection architecture. See
> [`docs/INSPECTION_REPORT.md`](docs/INSPECTION_REPORT.md).

## Sessions

Three sessions are collected independently per trading day (America/New_York):

| Session | Window | Panel role |
| --- | --- | --- |
| `HPRE` | 04:00:00 - 09:29:59 | Pre-market |
| `NHP` | 09:30:00 - official close | Regular market |
| `HPOST` | official close - 20:00:00 | After-hours |

Trading days, holidays, weekends, DST changes, and **early-close** days come
from the `XNYS` calendar via `pandas_market_calendars`.

## How it works (safety model)

Trade Ideas history shows the newest records first; **More** replaces the
visible page and steps backward in time. Therefore every visible page is
**exported, located on disk, parsed, validated, checksummed, assigned a
sequential part number, registered in the database, added to the manifest, and
marked VALIDATED before More is ever selected.** A failed save never leads to
More.

Completion of a session uses **multiple signals** (session boundary reached,
explicit no-more-history, repeated page by checksum/fingerprint, no backward
progress); a page with fewer than 1,000 rows is never conclusive on a single
signal.

## Architecture

```
Hdb.py                     GUI (Tkinter, queue+after) + CLI entry point
hdb/
  calendar_util.py         Trading days + tz-aware session windows
  csv_parser.py            Header detection, timestamp parsing, normalization
  sessions.py              Session classification + page validation
  fingerprint.py           Versioned SHA-256 event fingerprints (+fallbacks)
  paths.py                 Immutable run dirs, filenames, collision-safe saves
  manifest.py              Per-run manifest.json
  db.py / migrations.py    Backup, WAL, integrity check, transactional migrations
  importer.py              Dedup into alerts_flat + alert_sources lineage
  collection.py            Completion criteria + destructive-More guard (pure)
  collector.py             The export-before-More driver
  repository.py            Runs/sessions/parts bookkeeping
  reconcile.py             Verify + reconcile row counts + resume planning
  diagnostics.py           Discovery + panel-position assignment
  control.py               Cancel / pause / resume / progress
  app.py                   Service layer wiring everything together
  automation/
    base.py                AutomationBackend interface + data types
    mock_backend.py        Deterministic simulator (tests / dev, any OS)
    pywinauto_backend.py   Real Windows backend (lazy import; verify on Windows)
```

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.template.json config.json               # then edit paths/selectors

python Hdb.py --config config.json migrate        # backup + migrate DB
python Hdb.py --config config.json diagnostics    # discover Trade Ideas UI
python Hdb.py --config config.json gui            # or use the GUI
```

See [`docs/INSTALLATION.md`](docs/INSTALLATION.md) and
[`docs/OPERATING.md`](docs/OPERATING.md).

## Database

The existing `alerts_flat` table (the alert store) is preserved; each imported
alert gets a stable SHA-256 fingerprint for deduplication. Only the minimum
tracking tables are added:

`collection_runs`, `collection_sessions`, `history_export_parts`,
`alert_sources` (source lineage: filename, part number, source row number,
session, trading date), `rejected_rows`.

## Testing

```bash
pip install -r requirements.txt
pytest -q                       # 87 tests, cross-platform (uses mock backend)
python examples/demo_collection.py   # full pipeline demo (no Windows needed)
```

## Documentation

* [Installation](docs/INSTALLATION.md)
* [Operating guide](docs/OPERATING.md)
* [Troubleshooting](docs/TROUBLESHOOTING.md)
* [Backup & recovery](docs/BACKUP_RECOVERY.md)
* [Manifest format](docs/MANIFEST_FORMAT.md)
* [Inspection report](docs/INSPECTION_REPORT.md)
* [Implementation report](docs/IMPLEMENTATION_REPORT.md)
* [Selector discovery report](docs/SELECTOR_DISCOVERY_REPORT.md)
* [Unresolved UI selectors](docs/UNRESOLVED_SELECTORS.md)
