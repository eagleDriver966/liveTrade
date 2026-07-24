# Windows Setup & Launch (Trade Ideas PC)

Development in the Linux environment is **complete for this phase**. The real
Windows backend and live pywinauto connection can only be validated on the PC
where Trade Ideas is installed and running. Follow these exact steps there.

## 0. Prerequisites

- Windows 10/11.
- Trade Ideas Pro installed, **open, and logged in**, with the three alert
  panels configured (premarket, regular, after-hours).
- Python 3.10+ (3.12 recommended) from python.org (check "Add python.exe to PATH").

## 1. Get the code and open a terminal

Open **PowerShell** in the project folder (the folder containing `Hdb.py`).

## 2. Create a virtual environment and install dependencies

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

This installs `pandas_market_calendars`, `tzdata`, `pytest`, and (Windows only)
`pywinauto`, `pywin32`, `comtypes`, `Pillow`.

## 3. Create your configuration

```powershell
copy config.template.json config.json
```

Edit `config.json`:
- `"automation": { "backend": "pywinauto", "app_title_re": ".*Trade Ideas.*" }`
- `"paths"`: set real (non-test) `database` and `exports_root` (must NOT contain
  the words `mock` or `test`).

## 4. Confirm the real backend is READY

```powershell
python Hdb.py --config config.json backend-status
```
Expect `REAL WINDOWS BACKEND READY`. If you see `WINDOWS BACKEND ERROR`, the exact
reason is printed (Trade Ideas not open, pywinauto not installed, etc.) - fix it
and retry. The app never falls back to mock.

## 5. Migrate the database (backup-first)

```powershell
python Hdb.py --config config.json migrate
```

## 6. Run the gated diagnostic sequence (in order)

```powershell
python Hdb.py --config config.json diagnostics                 # discover menus/controls + selector report
# Review the report under your diagnostics_root, then assign REAL tab positions:
python Hdb.py --config config.json assign HPRE <pos>
python Hdb.py --config config.json assign NHP  <pos>
python Hdb.py --config config.json assign HPOST <pos>
python Hdb.py --config config.json verify-panel HPRE --confirm
python Hdb.py --config config.json verify-panel NHP  --confirm
python Hdb.py --config config.json verify-panel HPOST --confirm
python Hdb.py --config config.json test-one-page 2026-02-02 NHP   # export ONE page (no More, no import)
python Hdb.py --config config.json approve-page                   # import after you review the report
python Hdb.py --config config.json test-one-more 2026-02-02 NHP   # ONE More -> part_002, then stop
python Hdb.py --config config.json eligibility                    # must show every condition PASS -> YES
```

Or launch the GUI (banner shows `REAL WINDOWS BACKEND READY`):

```powershell
python Hdb.py --config config.json gui
```

## 7. Do NOT run date-range collection yet

`Collect Single Date`, `Collect Date Range`, and `Resume Incomplete Run` stay
blocked until `eligibility` reports `Production collection eligible: YES`
(which also requires one full reconciled real session - a later objective).

## Fill in unresolved selectors

During diagnostics, resolve every item in
[`UNRESOLVED_SELECTORS.md`](UNRESOLVED_SELECTORS.md) and paste the discovered
control identifiers into `config.json -> automation.selectors`.

## Running the tests on Windows (optional)

```powershell
pytest -q
```
All tests use the mock backend and a throwaway database; they never touch your
production database or exports.
