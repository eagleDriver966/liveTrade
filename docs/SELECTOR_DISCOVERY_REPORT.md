# Selector Discovery Report

This report documents how Trade Ideas UI elements are discovered and where their
identifiers are configured. Because the development/CI environment is Linux (no
Trade Ideas, no `pywinauto`), the concrete identifiers below are **placeholders**
to be filled during diagnostics on the Windows machine. The discovery mechanism
itself is implemented and mock-verified.

## How discovery works

`python Hdb.py --config config.json diagnostics` calls
`AutomationBackend.discover()`. On Windows (`pywinauto_backend`) this walks the
UI Automation tree and reports:

* process id and main-window title;
* full control list (name, control type, automation id, class name, rectangle);
* panel-selection menu entries / tab positions;
* menu hierarchy (File, Alerts/panels, History);
* History controls and the More control;
* Save Contents command;
* Save As dialog controls;
* overwrite-confirmation dialog controls;
* screenshots.

Output is written as JSON + human-readable text under `paths.diagnostics_root`.

## Configurable selectors (`config.json -> automation.selectors`)

| Key | Meaning | Status |
| --- | --- | --- |
| `panel_menu_path` | Menu path opening the panel/tab chooser | **TBD on Windows** |
| `history_menu_path` | Menu path to open History for the active panel | **TBD** |
| `history_date_edit` | Automation id of the History date field | **TBD** |
| `history_grid` | Automation id of the history results grid | **TBD** |
| `more_button` | Automation id of the More control | **TBD** |
| `no_more_history_text` | Control indicating no-more-results | **TBD** |
| `file_menu_path` | File menu path | `File` (assumed; verify) |
| `save_contents_menu_path` | File -> Save Contents path | `File->Save Contents` (verify) |

## Panel identification signals

Panels are identified by **tab position**, not window title. The active panel is
verified before any destructive action using multiple signals (see
`collector.verify_active_panel`): selected menu state, active tab index, visible
tab position, alert-table column signature, alert-type values, control hierarchy,
and screenshot fingerprint. Assignments (`panel_positions`, `panel_signatures`)
are captured during **Assign Panel Positions** and stored in config.

## Next step

Run diagnostics on the Windows machine, paste discovered ids into
`config.json`, then verify each item in `UNRESOLVED_SELECTORS.md`.
