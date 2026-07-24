# Unresolved UI Selectors (must be verified on Windows)

These items could not be resolved in the Linux dev/CI environment because Trade
Ideas and `pywinauto` are Windows-only. Each must be confirmed via diagnostic
mode on the target machine before enabling unattended collection. Until then,
the real `pywinauto` backend is **not** production-ready (the mock backend fully
exercises the surrounding logic).

| # | Item | File / config key | Verification method |
| --- | --- | --- | --- |
| 1 | Trade Ideas main-window title regex | `automation.app_title_re` | Diagnostics `process.window_title` |
| 2 | Panel-selection menu path | `selectors.panel_menu_path` | Diagnostics `menu_hierarchy`; enumerate + activate each position |
| 3 | Panel tab positions & order | `panel_positions` | **Assign Panel Positions**; confirm HPRE/NHP/HPOST mapping (do NOT assume order) |
| 4 | Active-panel confirmation signals | `verify_active_panel` | Confirm tab index + column signature + alert types match per panel |
| 5 | History open command | `selectors.history_menu_path` | Diagnostics; open History and read grid |
| 6 | History date field | `selectors.history_date_edit` | Set a known date; confirm page reloads |
| 7 | History results grid + row extraction | `selectors.history_grid` | Confirm row count / timestamps / symbols parse |
| 8 | More control | `selectors.more_button` | Confirm More replaces (not appends) the page |
| 9 | No-more-history indicator | `selectors.no_more_history_text` | Trigger end-of-history; confirm detection |
| 10 | File menu path | `selectors.file_menu_path` | Diagnostics `menu_hierarchy.File` |
| 11 | Save Contents command | `selectors.save_contents_menu_path` | Trigger export; confirm Save As opens |
| 12 | Save As dialog (filename edit + Save) | `pywinauto_backend.save_contents` | Confirm file written to the exact path |
| 13 | Overwrite-confirmation dialog (title + No/Cancel) | `pywinauto_backend.detect/cancel_overwrite` | Force a collision; confirm it is cancelled, never approved |
| 14 | Unexpected-dialog handling | (add as discovered) | Note any modal dialogs seen during runs |
| 15 | DPI/coordinate fallbacks (only if no accessible control) | `automation.coordinate_fallbacks` | Screenshot-verify before any coordinate click; keep disabled unless required |

## Sign-off checklist before unattended collection

- [ ] Diagnostics report reviewed on Windows.
- [ ] HPRE/NHP/HPOST assigned to correct tab positions and confirmed by signals.
- [ ] One File-menu export succeeds **without** More (phase 6).
- [ ] Exported page validates + imports (phase 7).
- [ ] Export-before-More verified for one session/date (phase 8).
- [ ] Destructive More handling verified (page replaced, not appended).
- [ ] Save As + overwrite dialog handling verified.
- [ ] Multi-part import + restart recovery verified.
- [ ] Reconciliation report clean.
