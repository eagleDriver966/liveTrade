# Test Results

Command: `pytest -q` (Python 3.12.3, Linux)

```
........................................................................ [ 66%]
.....................................                                    [100%]
109 passed in 34.19s
```

## Production eligibility (mock/Linux) - `Hdb.py eligibility`

```
Operating system: Linux — FAIL
Backend type: mock — FAIL
Real backend connected: no — FAIL
Trade Ideas PID: unavailable — FAIL
Real main-window handle: unavailable — FAIL
Mock mode: true — FAIL
Production database path: /tmp/pe/prod.mocktest.db — FAIL
Production export directory: /tmp/pe/exp_mocktest — FAIL
Real panel assignments (current process): unavailable — FAIL
Real panels visually confirmed (this config version): no — FAIL
Real one-page export: not tested — FAIL
Real More transition: not tested — FAIL
Real session reconciliation: not tested — FAIL
Production collection eligible: NO
```

## Diagnostic demo - `python examples/demo_diagnostic.py`

```
   timestamp_parse_rate: 1.0
   session_validation.ok: True

3. TEST ONE MORE TRANSITION (part_002, then stop)
   part_002: NHP_2026-02-02_part_002.csv
   page_changed=True moved_backward=True new_fingerprints=2 ok=True
   first oldest=2026-02-02T15:00:00-05:00 -> second oldest=2026-02-02T09:30:00-05:00

4. MOCK GATES (these are TEST-ONLY and never satisfy production)
   mock gates set: ['mock_backend_initialized', 'mock_panels_verified', 'mock_page_export_verified', 'mock_more_transition_verified', 'mock_session_reconciled']
   real gates set: [] (must be empty)
   NOTE: MOCK TEST RESULTS ONLY - production controls remain disabled.

RESULT: PASS
```
