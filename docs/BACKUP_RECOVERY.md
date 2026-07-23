# Backup & Recovery Guide

## Backups

* **Automatic:** every `migrate` creates a timestamped, consistent snapshot in
  `paths.backups_root` using SQLite's online backup API
  (`trade_ideas.db.YYYYMMDD_HHMMSS.bak`) **before** applying any migration.
* **Manual:** copy the `.db` file while no writer is active, or call
  `hdb.db.backup_database(db_path, backups_root)`.
* **Raw exports are their own backup.** Every history page lives immutably under
  `historical_exports/<year>/<date>/<session>/run_*/` and is never overwritten
  or deleted by the tool. The full database can be rebuilt from them with
  `Hdb.py import`.

## WAL mode

The database runs in WAL mode. When copying the DB manually, also copy the
`-wal` and `-shm` sidecar files, or use the online backup API (which produces a
single consistent file).

## Recovery scenarios

### Corrupt database
1. Stop all collection.
2. `migrate` aborts automatically if `PRAGMA integrity_check` fails.
3. Restore the newest good `backups/trade_ideas.db.*.bak` to `paths.database`.
4. Re-run `Hdb.py import` to re-ingest any exports newer than the backup
   (idempotent - dedup prevents double counting).

### Lost/rolled-back database, exports intact
1. `python Hdb.py migrate` (creates a fresh schema).
2. `python Hdb.py import` (re-imports the entire `historical_exports` tree).
3. `python Hdb.py verify` and `reconcile` to confirm.

### Interrupted run - failure **after a validated part, before More**
The last part is safely on disk, validated, and imported. Resume that session;
the visible page can be re-verified and collection continues from the next part.
Use `Hdb.py resume` to see the plan.

### Interrupted run - failure **after More, before export**
The previous page was destroyed by More and the new page was not saved. The tool
does **not** guess the lost state: it starts a **new run directory** for that
session, recollects safely, preserves the incomplete run, and deduplicates on
import. Earlier runs are never overwritten.

## Reconciliation

```bash
python Hdb.py --config config.json reconcile
```
Reports checksum mismatches, missing files, DB-only/disk-only parts, and
manifest discrepancies. `ok: true` means the database and on-disk raw exports
agree.
