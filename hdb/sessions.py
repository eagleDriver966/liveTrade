"""Session classification and page validation.

Given a parsed CSV page and the expected (session, trading_date), decide whether
the page is consistent with that session's time window, and classify individual
rows.  A configured panel is supposed to contain alerts for only its intended
session, so out-of-window rows are surfaced (not silently dropped).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .calendar_util import ExchangeCalendar, SessionWindow
from .csv_parser import ParsedCsv, ParsedRow


@dataclass
class PageValidation:
    session: str
    trading_date: date
    ok: bool
    row_count: int
    in_window_rows: int
    out_of_window_rows: int
    newest_timestamp: datetime | None
    oldest_timestamp: datetime | None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session": self.session,
            "trading_date": self.trading_date.isoformat(),
            "ok": self.ok,
            "row_count": self.row_count,
            "in_window_rows": self.in_window_rows,
            "out_of_window_rows": self.out_of_window_rows,
            "newest_timestamp": self.newest_timestamp.isoformat()
            if self.newest_timestamp
            else None,
            "oldest_timestamp": self.oldest_timestamp.isoformat()
            if self.oldest_timestamp
            else None,
            "reasons": self.reasons,
            "warnings": self.warnings,
        }


def classify_row(row: ParsedRow, windows: dict[str, SessionWindow]) -> str | None:
    if row.timestamp is None:
        return None
    for session, window in windows.items():
        if window.contains(row.timestamp):
            return session
    return None


def validate_page(
    parsed: ParsedCsv,
    session: str,
    trading_date: date,
    calendar: ExchangeCalendar,
    out_of_window_tolerance: float = 0.05,
) -> PageValidation:
    """Validate that ``parsed`` is a plausible page for ``session``/``date``.

    A small fraction of out-of-window rows is tolerated (panels can include a
    boundary alert), but a page dominated by wrong-session timestamps fails.
    """
    windows = calendar.all_session_windows(trading_date)
    target = windows[session]

    in_window = 0
    out_window = 0
    reasons: list[str] = []
    warnings: list[str] = []

    for row in parsed.rows:
        if row.timestamp is None:
            continue
        if target.contains(row.timestamp):
            in_window += 1
        else:
            out_window += 1

    total_ts = in_window + out_window
    newest = parsed.newest_timestamp
    oldest = parsed.oldest_timestamp

    ok = True
    if parsed.row_count == 0:
        # An empty page is not itself an error; the caller decides EMPTY_VERIFIED.
        warnings.append("no_data_rows")
    elif total_ts == 0:
        ok = False
        reasons.append("no_parseable_timestamps")
    else:
        frac_out = out_window / total_ts
        if frac_out > out_of_window_tolerance:
            ok = False
            reasons.append(
                f"too_many_out_of_window_rows:{out_window}/{total_ts}"
            )

    # Cross-date contamination check.
    if newest is not None and newest.date() != trading_date:
        warnings.append(f"newest_timestamp_date_mismatch:{newest.date()}")
    if oldest is not None and oldest.date() != trading_date:
        warnings.append(f"oldest_timestamp_date_mismatch:{oldest.date()}")

    return PageValidation(
        session=session,
        trading_date=trading_date,
        ok=ok,
        row_count=parsed.row_count,
        in_window_rows=in_window,
        out_of_window_rows=out_window,
        newest_timestamp=newest,
        oldest_timestamp=oldest,
        reasons=reasons,
        warnings=warnings,
    )


def reached_session_start(
    oldest_timestamp: datetime | None,
    session: str,
    trading_date: date,
    calendar: ExchangeCalendar,
) -> bool:
    """True when the oldest collected timestamp has reached/passed the official
    session start (a completion signal)."""
    if oldest_timestamp is None:
        return False
    window = calendar.session_window(session, trading_date)
    return oldest_timestamp <= window.start
