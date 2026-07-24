# Installation Guide

## Target machine

Historical collection runs on the **Windows** computer that runs Trade Ideas.
The platform-independent core (parsing, calendar, DB, import, tests) also runs on
Linux/macOS for development and CI.

## Prerequisites

* Python 3.10+ (3.12 recommended).
* Trade Ideas Pro desktop application (for live collection).
* On Windows: the ability to install `pywinauto` (uses `pywin32`/`comtypes`).

## Steps

1. **Get the code** and open a terminal in the project root.

2. **Create a virtual environment**

   Windows (PowerShell):
   ```powershell
   py -3.12 -m venv .venv
   .venv\Scripts\Activate.ps1
   ```
   Linux/macOS:
   ```bash
   python3 -m venv .venv
   . .venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```
   * On Windows this also installs `pywinauto`, `pywin32`, `comtypes`, `Pillow`
     (they are marked `sys_platform == "win32"` and skipped elsewhere).
   * `pandas_market_calendars`, `tzdata`, `pytest` install everywhere.
   * The GUI uses Tkinter, which ships with standard CPython on Windows/macOS.
     On Debian/Ubuntu install it via the system package `python3-tk`.

4. **Create your configuration**
   ```bash
   cp config.template.json config.json      # Windows: copy config.template.json config.json
   ```
   Edit `config.json`:
   * `paths.*` - where the DB, exports, logs, backups, diagnostics live.
   * `automation.backend` - `"pywinauto"` for live collection, `"mock"` for tests.
   * `automation.app_title_re` - regex matching the Trade Ideas window title.
   * `automation.selectors.*` - filled in during diagnostics (see below).
   * `history_boundary_date` - default `2026-02-01`.

5. **Initialize the database (backup-first, transactional)**
   ```bash
   python Hdb.py --config config.json migrate
   ```

6. **Run diagnostics on the Windows machine** (Trade Ideas must be open)
   ```bash
   python Hdb.py --config config.json diagnostics
   ```
   Review the JSON/text reports under `paths.diagnostics_root`, then assign panel
   positions (GUI: *Assign Panel Positions*, or CLI `assign`).

7. **Verify the install**
   ```bash
   pytest -q
   python examples/demo_collection.py
   ```

## Upgrading

Re-running `migrate` is safe and idempotent: it creates a timestamped backup,
runs `PRAGMA integrity_check`, and applies only pending migrations inside
transactions.
