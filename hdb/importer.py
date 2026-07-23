"""Import validated CSV export parts into SQLite with full source lineage.

For every data row:

* the original values are stored in ``alerts_raw`` (raw JSON + lineage);
* a versioned event fingerprint is computed;
* one normalized event is stored in ``alerts_normalized`` (deduplicated by
  fingerprint), and *every* source occurrence is recorded in ``alert_sources``
  (a normalized event may appear in multiple adjacent overlapping pages);
* unparseable/invalid rows go to ``rejected_rows``.

Re-importing the same file is safe: raw rows use a UNIQUE lineage constraint and
normalized events dedupe by fingerprint, so duplicate pages never double-count.

A legacy mirror into ``alerts_flat`` keeps the original app features working.
"""

from __future__ import annotations

import hashlib
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
    raw_inserted: int = 0
    raw_duplicate: int = 0
    normalized_new: int = 0
    normalized_existing: int = 0
    sources_added: int = 0
    rejected: int = 0
    flat_inserted: int = 0
    fingerprint_versions: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        return d


def build_normalized_fields(
    row: ParsedRow,
    session: str,
    panel_position: Any,
    trading_date: date,
) -> dict[str, Any]:
    """Map a parsed row to canonical fingerprint/normalized fields."""
    f = row.fields
    ts = row.timestamp
    return {
        "trading_date": trading_date.isoformat(),
        "alert_timestamp": ts,
        "alert_type": f.get("alert_type"),
        "symbol": (f.get("symbol") or "").upper() or None,
        "price_norm": f.get("price_norm"),
        "count_norm": f.get("count_norm"),
        "volume_norm": f.get("volume_norm"),
        "source_session": session,
        "panel_role": session,
        "event_id": f.get("event_id"),
    }


def _legacy_fingerprint(nf: dict[str, Any]) -> str:
    ts = nf.get("alert_timestamp")
    ts_s = ts.replace(microsecond=0).isoformat() if isinstance(ts, datetime) else str(ts)
    payload = "|".join(
        [
            str(nf.get("symbol") or ""),
            str(nf.get("alert_type") or ""),
            ts_s,
            str(nf.get("price_norm") if nf.get("price_norm") is not None else ""),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def import_parsed_part(
    conn: sqlite3.Connection,
    parsed: ParsedCsv,
    run_id: str,
    trading_date: date,
    session: str,
    panel_position: Any,
    part_number: int,
    source_filename: str,
    mirror_to_flat: bool = True,
    logger=None,
) -> ImportStats:
    """Import one parsed CSV part.  Runs in a single transaction."""
    stats = ImportStats(part_number=part_number, source_filename=source_filename)
    now = _utc_iso()

    with dbmod.transaction(conn):
        # Rejected rows detected during parsing.
        for rej in parsed.rejected:
            conn.execute(
                """INSERT INTO rejected_rows
                   (run_id, trading_date, session, source_filename,
                    source_part_number, source_row_number, reason, raw_json, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    trading_date.isoformat(),
                    session,
                    source_filename,
                    part_number,
                    rej.get("row_number"),
                    rej.get("reason", "parse_rejected"),
                    json.dumps(rej.get("raw", {}), default=str),
                    now,
                ),
            )
            stats.rejected += 1

        for row in parsed.rows:
            nf = build_normalized_fields(row, session, panel_position, trading_date)

            # Reject rows with no usable timestamp or symbol.
            if nf["alert_timestamp"] is None or not nf["symbol"]:
                conn.execute(
                    """INSERT INTO rejected_rows
                       (run_id, trading_date, session, source_filename,
                        source_part_number, source_row_number, reason, raw_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        run_id,
                        trading_date.isoformat(),
                        session,
                        source_filename,
                        part_number,
                        row.row_number,
                        "missing_timestamp_or_symbol",
                        json.dumps(row.raw, default=str),
                        now,
                    ),
                )
                stats.rejected += 1
                continue

            fp = compute_fingerprint(nf)
            stats.fingerprint_versions[fp.version] = (
                stats.fingerprint_versions.get(fp.version, 0) + 1
            )

            # Insert raw row (dedupe by lineage).
            try:
                cur = conn.execute(
                    """INSERT INTO alerts_raw
                       (run_id, trading_date, session, panel_position,
                        source_filename, source_part_number, source_row_number,
                        raw_json, parser_version, import_timestamp,
                        normalization_status, event_fingerprint, fingerprint_version)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        run_id,
                        trading_date.isoformat(),
                        session,
                        str(panel_position),
                        source_filename,
                        part_number,
                        row.row_number,
                        json.dumps(row.raw, default=str),
                        PARSER_VERSION,
                        now,
                        "NORMALIZED",
                        fp.value,
                        fp.version,
                    ),
                )
                raw_id = cur.lastrowid
                stats.raw_inserted += 1
            except sqlite3.IntegrityError:
                # Same file+row already imported: skip (safe re-import).
                stats.raw_duplicate += 1
                continue

            # Upsert normalized event.
            existing = conn.execute(
                "SELECT id FROM alerts_normalized WHERE event_fingerprint=?",
                (fp.value,),
            ).fetchone()
            if existing is None:
                cur = conn.execute(
                    """INSERT INTO alerts_normalized
                       (event_fingerprint, fingerprint_version, trading_date,
                        alert_timestamp, alert_type, symbol, price_norm, count_norm,
                        volume_norm, source_session, panel_role, event_id,
                        first_seen_run_id, first_seen_at, occurrence_count)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                    (
                        fp.value,
                        fp.version,
                        nf["trading_date"],
                        nf["alert_timestamp"].isoformat()
                        if isinstance(nf["alert_timestamp"], datetime)
                        else nf["alert_timestamp"],
                        nf["alert_type"],
                        nf["symbol"],
                        nf["price_norm"],
                        nf["count_norm"],
                        nf["volume_norm"],
                        nf["source_session"],
                        nf["panel_role"],
                        nf["event_id"],
                        run_id,
                        now,
                    ),
                )
                normalized_id = cur.lastrowid
                stats.normalized_new += 1
            else:
                normalized_id = existing["id"]
                stats.normalized_existing += 1

            conn.execute(
                "UPDATE alerts_normalized SET occurrence_count = occurrence_count + 1 WHERE id=?",
                (normalized_id,),
            )
            conn.execute(
                "UPDATE alerts_raw SET normalized_id=? WHERE id=?",
                (normalized_id, raw_id),
            )

            # Record the source occurrence (dedupe by normalized+file+row).
            try:
                conn.execute(
                    """INSERT INTO alert_sources
                       (normalized_id, raw_id, run_id, trading_date, session,
                        panel_position, source_filename, source_part_number,
                        source_row_number, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        normalized_id,
                        raw_id,
                        run_id,
                        trading_date.isoformat(),
                        session,
                        str(panel_position),
                        source_filename,
                        part_number,
                        row.row_number,
                        now,
                    ),
                )
                stats.sources_added += 1
            except sqlite3.IntegrityError:
                pass

            # Legacy mirror.
            if mirror_to_flat:
                legacy_fp = _legacy_fingerprint(nf)
                dup = conn.execute(
                    "SELECT 1 FROM alerts_flat WHERE fingerprint=?", (legacy_fp,)
                ).fetchone()
                if dup is None:
                    conn.execute(
                        """INSERT INTO alerts_flat
                           (symbol, alert_type, alert_time, price, alert_count,
                            volume, trading_date, fingerprint, source_file, imported_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (
                            nf["symbol"],
                            nf["alert_type"],
                            nf["alert_timestamp"].isoformat()
                            if isinstance(nf["alert_timestamp"], datetime)
                            else nf["alert_timestamp"],
                            nf["price_norm"],
                            nf["count_norm"],
                            nf["volume_norm"],
                            nf["trading_date"],
                            legacy_fp,
                            source_filename,
                            now,
                        ),
                    )
                    stats.flat_inserted += 1

    if logger:
        logger.info("part_imported", **stats.as_dict())
    return stats
