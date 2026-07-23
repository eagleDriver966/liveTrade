# Test Results

Command: `pytest -q` (Python 3.12.3, Linux)

```
........................................................................ [ 86%]
...........                                                              [100%]
83 passed in 29.29s
```

## Demo: `python examples/demo_collection.py`

```
1. MIGRATE:
   wal=wal integrity_ok=True applied=[1, 2] version=2

2. DIAGNOSTICS:
   report=diagnostic_20260723_025543.json panels_found=3
   readiness=True positions={'HPRE': 0, 'NHP': 1, 'HPOST': 2}

3. COLLECT DATE RANGE (2026-02-03 -> 2026-02-02):
   run_id=run_20260723_025543 days=2

4. VERIFY:
   integrity_ok=True
   counts={"collection_runs": 1, "collection_days": 2, "collection_sessions": 6, "history_export_parts": 12, "alerts_raw": 24, "alerts_normalized": 20, "alert_sources": 24, "rejected_rows": 0, "alerts_flat": 20}
   verified_dates=['2026-02-03', '2026-02-02']

5. RECONCILE (DB vs on-disk parts):
   ok=True checked_parts=12 checksum_ok=12 mismatches=0 missing=0

6. RE-IMPORT EXISTING EXPORTS (idempotency):
   normalized_before=20 normalized_after=20 (equal => dedup works: True)

RESULT: PASS
```
