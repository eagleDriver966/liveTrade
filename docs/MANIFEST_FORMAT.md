# Manifest Format

Each session **run directory** contains a `manifest.json` - the on-disk source of
truth for that run. It is written atomically after every validated part, so a
crash never leaves a half-written manifest.

## Location

```
historical_exports/<year>/<trading_date>/<session>/run_<YYYYMMDD_HHMMSS>/manifest.json
```

## Schema

```jsonc
{
  "manifest_version": 1,
  "app_version": "2.0.0",
  "parser_version": "hdb-parser-1",
  "run_id": "run_20260202_090001",
  "trading_date": "2026-02-02",
  "session": "NHP",                     // HPRE | NHP | HPOST
  "panel_position": 1,                  // tab position used (from config)
  "boundary_date": "2026-02-01",        // requested historical boundary
  "created_at": "2026-02-02T14:00:01Z",
  "updated_at": "2026-02-02T14:03:12Z",
  "status": "VERIFIED",                 // COLLECTING while in progress
  "completion_reason": "boundary_reached",
  "completion_signals": ["boundary_reached", "no_backward_progress"],
  "parts": [
    {
      "part_number": 1,
      "filename": "NHP_2026-02-02_part_001.csv",
      "sha256": "<hex>",
      "row_count": 1200,
      "newest_timestamp": "2026-02-02T15:59:58-05:00",
      "oldest_timestamp": "2026-02-02T14:31:00-05:00",
      "first_symbol": "AAPL",
      "last_symbol": "TSLA",
      "page_fingerprint": "<sha256 over the page's alert fingerprints>",
      "status": "VALIDATED",
      "validated_at": "2026-02-02T14:01:10Z",
      "notes": {}
    }
    // ... part_002, part_003, ...
  ]
}
```

## Field notes

* `parts` are ordered by `part_number` (001, 002, ...). Part 001 is the newest
  history page; each subsequent part is one destructive **More** older.
* `sha256` is over the raw CSV bytes; `page_fingerprint` is an order-independent
  SHA-256 over the page's alert fingerprints (used for repeated-page detection).
* `status` at the top level is `COLLECTING` until a completion condition is met,
  then `VERIFIED` (or `EMPTY_VERIFIED` for an empty session).
* The manifest is cross-checked against the database and re-hashed by
  `Hdb.py reconcile`.

A real generated example is committed at
[`examples/sample_manifest.json`](../examples/sample_manifest.json).
