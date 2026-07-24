"""Import validated CSV export parts into the SQLite alert database.

Model (simplified to scope):

* the alert itself is stored once in ``alerts_flat`` (deduplicated by a stable
  SHA-256 fingerprint);
* every source occurrence is recorded in ``alert_sources`` with full lineage
  (run id, trading date, session, source filename, part number, source row
  number) - a single alert can appear in multiple overlapping pages;
* unparseable/invalid rows go to ``rejected_rows``.

Re-importing the same file is safe (idempotent): ``alert_sources`` has a UNIQUE
lineage constraint and ``alerts_flat`` dedupes by fingerprint, so overlapping
pages never double-count.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from . import db as dbmod
from .csv_parser import ParsedCsv, ParsedRow
from .fingerprint import compute_fingerprint
from .version import PARSER_VERSION


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ImportStats:
    part_number: int
    source_filename: str
    alerts_inserted: int = 0        # new rows added to alerts_flat
    alerts_duplicate: int = 0       # fingerprint already present
    sources_added: int = 0          # occurrences recorded in alert_sources
    sources_duplicate: int = 0      # occurrence already recorded (re-import)
    rejected: int = 0
    fingerprint_versions: dict[str, int] = field(default_factory=dict)

    @property
    def rows_seen(self) -> int:
        return self.alerts_inserted + self.alerts_duplicate + self.rejected

    def as_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["rows_seen"] = self.rows_seen
        return d


def build_alert_fields(row: ParsedRow, session: str, trading_date: date) -> dict[str, Any]:
    """Map a parsed row to the canonical alert/fingerprint fields."""
    f = row.fields
    return {
        "trading_date": trading_date.isoformat(),
        "alert_timestamp": row.timestamp,
        "alert_type": f.get("alert_type"),
        "symbol": (f.get("symbol") or "").upper() or None,
        "price": f.get("price_norm"),
        "alert_count": f.get("count_norm"),
        "volume": f.get("volume_norm"),
        "session": session,
    }


def _reject(
    conn: sqlite3.Connection, run_id, trading_date, session, source_filename,
    part_number, source_row_number, reason, raw, now,
) -> None:
    conn.execute(
        """INSERT INTO rejected_rows
           (run_id, trading_date, session, source_filename, source_part_number,
            source_row_number, reason, raw_json, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            run_id, trading_date.isoformat(), session, source_filename,
            part_number, source_row_number, reason,
            json.dumps(raw, default=str), now,
        ),
    )


def import_parsed_part(
    conn: sqlite3.Connection,
    parsed: ParsedCsv,
    run_id: str,
    trading_date: date,
    session: str,
    panel_position: Any,
    part_number: int,
    source_filename: str,
    logger=None,
) -> ImportStats:
    """Import one parsed CSV part.  Runs in a single transaction."""
    stats = ImportStats(part_number=part_number, source_filename=source_filename)
    now = _utc_iso()

    with dbmod.transaction(conn):
        for rej in parsed.rejected:
            _reject(conn, run_id, trading_date, session, source_filename,
                    part_number, rej.get("row_number"),
                    rej.get("reason", "parse_rejected"), rej.get("raw", {}), now)
            stats.rejected += 1

        for row in parsed.rows:
            fields = build_alert_fields(row, session, trading_date)
            if fields["alert_timestamp"] is None or not fields["symbol"]:
                _reject(conn, run_id, trading_date, session, source_filename,
                        part_number, row.row_number, "missing_timestamp_or_symbol",
                        row.raw, now)
                stats.rejected += 1
                continue

            fp = compute_fingerprint(fields)
            stats.fingerprint_versions[fp.version] = (
                stats.fingerprint_versions.get(fp.version, 0) + 1
            )

            # Deduplicate the alert by fingerprint.
            existing = conn.execute(
                "SELECT id FROM alerts_flat WHERE fingerprint=?", (fp.value,)
            ).fetchone()
            if existing is None:
                ts = fields["alert_timestamp"]
                cur = conn.execute(
                    """INSERT INTO alerts_flat
                       (symbol, alert_type, alert_time, price, alert_count, volume,
                        trading_date, session, fingerprint, fingerprint_version,
                        source_file, imported_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        fields["symbol"], fields["alert_type"],
                        ts.isoformat() if isinstance(ts, datetime) else ts,
                        fields["price"], fields["alert_count"], fields["volume"],
                        fields["trading_date"], session, fp.value, fp.version,
                        source_filename, now,
                    ),
                )
                alert_id = cur.lastrowid
                stats.alerts_inserted += 1
            else:
                alert_id = existing["id"]
                stats.alerts_duplicate += 1

            # Record the source occurrence (preserve all overlaps).
            try:
                conn.execute(
                    """INSERT INTO alert_sources
                       (alert_flat_id, fingerprint, run_id, trading_date, session,
                        panel_position, source_filename, source_part_number,
                        source_row_number, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        alert_id, fp.value, run_id, trading_date.isoformat(), session,
                        str(panel_position), source_filename, part_number,
                        row.row_number, now,
                    ),
                )
                stats.sources_added += 1
            except sqlite3.IntegrityError:
                stats.sources_duplicate += 1

    if logger:
        logger.info("part_imported", **stats.as_dict())
    return stats
