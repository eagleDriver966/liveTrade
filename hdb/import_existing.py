"""Import already-exported CSV files (and whole export trees) into SQLite.

Supports the "Import Existing Exports" GUI action and restart recovery: it walks
run directories, reads manifests when present, and imports each part with full
lineage.  Re-importing is safe (idempotent) thanks to the importer's dedup.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import repository as repo
from .calendar_util import ExchangeCalendar
from .csv_parser import parse_csv
from .importer import ImportStats, import_parsed_part
from .manifest import Manifest
from .paths import sha256_file
from .status import Status

_PART_RE = re.compile(r"^(?P<session>HPRE|NHP|HPOST)_(?P<date>\d{4}-\d{2}-\d{2})_part_(?P<part>\d{3})\.csv$")


@dataclass
class ImportSummary:
    files: int = 0
    parts_imported: int = 0
    rows_raw: int = 0
    rows_rejected: int = 0
    normalized_new: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        return d


def parse_part_filename(name: str) -> dict[str, Any] | None:
    m = _PART_RE.match(os.path.basename(name))
    if not m:
        return None
    return {
        "session": m.group("session"),
        "trading_date": date.fromisoformat(m.group("date")),
        "part_number": int(m.group("part")),
    }


def import_run_dir(
    conn,
    run_dir: str,
    calendar: ExchangeCalendar,
    run_id: str,
    logger=None,
    mirror_to_flat: bool = True,
) -> ImportSummary:
    """Import every part CSV in a single ``run_YYYYMMDD_HHMMSS`` directory."""
    summary = ImportSummary()
    # Ensure the owning run row exists (idempotent) so FK constraints hold even
    # when this is called directly (e.g. restart recovery / ad-hoc imports).
    repo.create_run(conn, run_id, "import", None, None, None)
    manifest = None
    manifest_path = os.path.join(run_dir, "manifest.json")
    panel_position = None
    if os.path.exists(manifest_path):
        try:
            manifest = Manifest.load(manifest_path)
            panel_position = manifest.panel_position
        except Exception as exc:
            summary.errors.append(f"manifest_load_failed:{exc}")

    files = sorted(f for f in os.listdir(run_dir) if f.lower().endswith(".csv"))
    for fname in files:
        meta = parse_part_filename(fname)
        if not meta:
            summary.errors.append(f"unrecognized_filename:{fname}")
            continue
        path = os.path.join(run_dir, fname)
        try:
            parsed = parse_csv(path, meta["trading_date"], calendar.timezone)
        except Exception as exc:
            summary.errors.append(f"parse_failed:{fname}:{exc}")
            if logger:
                logger.exception("import_parse_failed", exc, file=fname)
            continue

        checksum = sha256_file(path)
        repo.upsert_session(
            conn, run_id, meta["trading_date"], meta["session"], panel_position,
            run_dir, Status.IMPORTED,
        )
        try:
            repo.register_part(
                conn, run_id, meta["trading_date"], meta["session"],
                meta["part_number"], fname, path, checksum, parsed.row_count,
                parsed.newest_timestamp.isoformat() if parsed.newest_timestamp else None,
                parsed.oldest_timestamp.isoformat() if parsed.oldest_timestamp else None,
                parsed.first_symbol, parsed.last_symbol, None, panel_position,
                Status.VALIDATED,
            )
        except Exception:
            # Part may already be registered from a prior import; continue.
            pass

        stats: ImportStats = import_parsed_part(
            conn, parsed, run_id, meta["trading_date"], meta["session"],
            panel_position, meta["part_number"], fname,
            mirror_to_flat=mirror_to_flat, logger=logger,
        )
        summary.files += 1
        summary.parts_imported += 1
        summary.rows_raw += stats.raw_inserted
        summary.rows_rejected += stats.rejected
        summary.normalized_new += stats.normalized_new

    return summary


def import_export_tree(
    conn,
    exports_root: str,
    calendar: ExchangeCalendar,
    run_id: str,
    logger=None,
    mirror_to_flat: bool = True,
) -> ImportSummary:
    """Walk the entire ``historical_exports`` tree and import every run dir."""
    total = ImportSummary()
    for root, dirs, files in os.walk(exports_root):
        if os.path.basename(root).startswith("run_") and any(
            f.lower().endswith(".csv") for f in files
        ):
            s = import_run_dir(conn, root, calendar, run_id, logger=logger,
                               mirror_to_flat=mirror_to_flat)
            total.files += s.files
            total.parts_imported += s.parts_imported
            total.rows_raw += s.rows_raw
            total.rows_rejected += s.rows_rejected
            total.normalized_new += s.normalized_new
            total.errors.extend(s.errors)
    return total
