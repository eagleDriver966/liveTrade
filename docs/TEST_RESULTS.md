# Test Results

Command: `pytest -q` (Python 3.12.3, Linux)

```
........................................................................ [ 68%]
.................................                                        [100%]
105 passed in 34.12s
```

## Diagnostic demo: `python examples/demo_diagnostic.py`

```
1. BACKEND STATUS
   real (this OS): WINDOWS BACKEND ERROR
   mock         : MOCK TEST BACKEND

2. TEST ONE PAGE EXPORT (no More, no import)
   filename: NHP_2026-02-02_part_001.csv
   file_size: 192
   parsed_row_count: 2
   newest_timestamp: 2026-02-02T15:59:00-05:00
   oldest_timestamp: 2026-02-02T15:00:00-05:00
   alert_types: ['High']
   symbols_sample: ['AAPL', 'MSFT']
   symbol_count: 2
   timestamp_parse_rate: 1.0
   session_validation.ok: True

3. TEST ONE MORE TRANSITION (part_002, then stop)
   part_002: NHP_2026-02-02_part_002.csv
   page_changed=True moved_backward=True new_fingerprints=2 ok=True
   first oldest=2026-02-02T15:00:00-05:00 -> second oldest=2026-02-02T09:30:00-05:00

4. GATES (simulated after successful diagnostics)
   passed so far: ['real_backend_initialized', 'panels_assigned', 'panels_verified', 'history_selector_verified', 'save_contents_verified', 'save_as_verified', 'one_page_exported', 'one_more_verified']
   still blocking collection: ['one_session_reconciled']

RESULT: PASS
```

## Collection demo: `python examples/demo_collection.py`

```
   wal=wal integrity_ok=True applied=[1, 2] version=2

2. DIAGNOSTICS:
   report=diagnostic_20260724_080722.json panels_found=3
   panels_assigned=True positions={'HPRE': 0, 'NHP': 1, 'HPOST': 2}

3. COLLECT DATE RANGE (2026-02-03 -> 2026-02-02):
   run_id=run_20260724_080722 days=2

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
