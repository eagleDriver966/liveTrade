# Test Results

Command: `pytest -q` (Python 3.12.3, Linux)

```
........................................................................ [ 82%]
...............                                                          [100%]
87 passed in 29.41s
```

## Demo: `python examples/demo_collection.py`

```
1. MIGRATE:
   wal=wal integrity_ok=True applied=[1, 2] version=2

2. DIAGNOSTICS:
   report=diagnostic_20260724_071339.json panels_found=3
   panels_assigned=True positions={'HPRE': 0, 'NHP': 1, 'HPOST': 2}

3. COLLECT DATE RANGE (2026-02-03 -> 2026-02-02):
   run_id=run_20260724_071339 days=2

4. VERIFY:
   integrity_ok=True
   counts={"collection_runs": 1, "collection_sessions": 6, "history_export_parts": 12, "alert_sources": 24, "rejected_rows": 0, "alerts_flat": 20}
   verified_dates=['2026-02-03', '2026-02-02']
   row_count_reconciliation={"exported_part_rows": 24, "source_occurrences": 24, "data_rejected_rows": 0, "total_rejected_rows": 0, "balanced": true}

5. RECONCILE (DB vs on-disk parts):
   ok=True checked_parts=12 checksum_ok=12 mismatches=0 missing=0

6. RE-COLLECT SAME RANGE (dedup):
   alerts_flat_before=20 alerts_flat_after=20 (equal => dedup works: True)

RESULT: PASS
```
