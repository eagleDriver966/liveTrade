"""Thin data-access helpers for collection bookkeeping tables.

Keeps SQL for runs/days/sessions/parts in one place so the collector and
reconciler stay readable.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from typing import Any

from .status import Status


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_run(
    conn: sqlite3.Connection,
    run_id: str,
    mode: str,
    boundary_date: date | None,
    start_date: date | None,
    end_date: date | None,
    backend: str | None = None,
    notes: dict[str, Any] | None = None,
) -> None:
    now = _utc_iso()
    conn.execute(
        """INSERT OR IGNORE INTO collection_runs
           (run_id, mode, boundary_date, start_date, end_date, backend, status,
            created_at, updated_at, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            mode,
            boundary_date.isoformat() if boundary_date else None,
            start_date.isoformat() if start_date else None,
            end_date.isoformat() if end_date else None,
            backend,
            str(Status.COLLECTING),
            now,
            now,
            json.dumps(notes or {}),
        ),
    )
    conn.commit()


def set_run_status(conn: sqlite3.Connection, run_id: str, status: Status) -> None:
    conn.execute(
        "UPDATE collection_runs SET status=?, updated_at=? WHERE run_id=?",
        (str(status), _utc_iso(), run_id),
    )
    conn.commit()


def upsert_session(
    conn: sqlite3.Connection,
    run_id: str,
    trading_date: date,
    session: str,
    panel_position: Any,
    run_dir: str,
    status: Status,
    completion_reason: str | None = None,
    completion_signals: list[str] | None = None,
    part_count: int | None = None,
) -> int:
    now = _utc_iso()
    row = conn.execute(
        """SELECT id FROM collection_sessions
           WHERE run_id=? AND trading_date=? AND session=? AND run_dir=?""",
        (run_id, trading_date.isoformat(), session, run_dir),
    ).fetchone()
    if row is None:
        cur = conn.execute(
            """INSERT INTO collection_sessions
               (run_id, trading_date, session, panel_position, run_dir, status,
                completion_reason, completion_signals, part_count, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                trading_date.isoformat(),
                session,
                str(panel_position),
                run_dir,
                str(status),
                completion_reason,
                json.dumps(completion_signals or []),
                part_count or 0,
                now,
                now,
            ),
        )
        conn.commit()
        return cur.lastrowid
    session_id = row["id"]
    fields = ["status=?", "updated_at=?"]
    params: list[Any] = [str(status), now]
    if completion_reason is not None:
        fields.append("completion_reason=?")
        params.append(completion_reason)
    if completion_signals is not None:
        fields.append("completion_signals=?")
        params.append(json.dumps(completion_signals))
    if part_count is not None:
        fields.append("part_count=?")
        params.append(part_count)
    params.append(session_id)
    conn.execute(
        f"UPDATE collection_sessions SET {', '.join(fields)} WHERE id=?", params
    )
    conn.commit()
    return session_id


def register_part(
    conn: sqlite3.Connection,
    run_id: str,
    trading_date: date,
    session: str,
    part_number: int,
    filename: str,
    abs_path: str,
    sha256: str,
    row_count: int,
    newest_timestamp: str | None,
    oldest_timestamp: str | None,
    first_symbol: str | None,
    last_symbol: str | None,
    page_fingerprint: str | None,
    panel_position: Any,
    status: Status,
) -> int:
    now = _utc_iso()
    cur = conn.execute(
        """INSERT INTO history_export_parts
           (run_id, trading_date, session, part_number, filename, abs_path,
            sha256, row_count, newest_timestamp, oldest_timestamp, first_symbol,
            last_symbol, page_fingerprint, panel_position, status, validated_at, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            trading_date.isoformat(),
            session,
            part_number,
            filename,
            abs_path,
            sha256,
            row_count,
            newest_timestamp,
            oldest_timestamp,
            first_symbol,
            last_symbol,
            page_fingerprint,
            str(panel_position),
            str(status),
            now,
            now,
        ),
    )
    conn.commit()
    return cur.lastrowid


def mark_part_imported(conn: sqlite3.Connection, part_id: int) -> None:
    conn.execute(
        "UPDATE history_export_parts SET status=?, imported_at=? WHERE id=?",
        (str(Status.IMPORTED), _utc_iso(), part_id),
    )
    conn.commit()


def session_status_map(
    conn: sqlite3.Connection, run_id: str, trading_date: date
) -> dict[str, Status]:
    rows = conn.execute(
        """SELECT session, status FROM collection_sessions
           WHERE run_id=? AND trading_date=?""",
        (run_id, trading_date.isoformat()),
    ).fetchall()
    result: dict[str, Status] = {}
    for r in rows:
        try:
            result[r["session"]] = Status(r["status"])
        except ValueError:
            continue
    return result


def latest_validated_part(
    conn: sqlite3.Connection, run_id: str, trading_date: date, session: str
) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT * FROM history_export_parts
           WHERE run_id=? AND trading_date=? AND session=?
           ORDER BY part_number DESC LIMIT 1""",
        (run_id, trading_date.isoformat(), session),
    ).fetchone()
