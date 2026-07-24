"""US exchange calendar and per-session time boundaries.

Wraps ``pandas_market_calendars`` (XNYS by default) to answer:

* which days are valid trading days (weekends/holidays excluded);
* the official market close for a day (handles early-close days);
* the exact tz-aware start/end datetime for each session on a given day
  (handles DST because boundaries are localized to the exchange timezone).

Session clock boundaries come from configuration; the NHP end and HPOST start
are derived from the real official close so early-close days are correct.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_market_calendars as mcal

SESSION_ORDER = ("HPRE", "NHP", "HPOST")


@dataclass(frozen=True)
class SessionWindow:
    """A concrete, tz-aware session window for one trading day."""

    session: str
    trading_date: date
    start: datetime  # tz-aware, inclusive
    end: datetime  # tz-aware, inclusive
    official_close: datetime  # tz-aware official market close for the day
    early_close: bool

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment <= self.end


def _parse_clock(value: str) -> time:
    parts = [int(p) for p in value.split(":")]
    while len(parts) < 3:
        parts.append(0)
    return time(parts[0], parts[1], parts[2])


class ExchangeCalendar:
    """Trading-day and session-boundary helper for one exchange calendar."""

    def __init__(
        self,
        calendar_name: str = "XNYS",
        timezone: str = "America/New_York",
        session_spec: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.calendar_name = calendar_name
        self.timezone = timezone
        self.tz = ZoneInfo(timezone)
        self._cal = mcal.get_calendar(calendar_name)
        self.session_spec = session_spec or {
            "HPRE": {"start": "04:00:00", "end": "09:29:59"},
            "NHP": {"start": "09:30:00", "end": "market_close"},
            "HPOST": {"start": "market_close", "end": "20:00:00"},
        }

    # -- trading days --------------------------------------------------------
    @lru_cache(maxsize=64)
    def _schedule(self, start: date, end: date) -> pd.DataFrame:
        return self._cal.schedule(
            start_date=start.isoformat(), end_date=end.isoformat(), tz=self.timezone
        )

    def is_trading_day(self, day: date) -> bool:
        sched = self._schedule(day, day)
        return len(sched) == 1

    def valid_trading_days(self, start: date, end: date) -> list[date]:
        if start > end:
            return []
        sched = self._schedule(start, end)
        return [ts.date() for ts in sched.index]

    def first_trading_day_on_or_after(self, day: date, horizon_days: int = 30) -> date:
        sched = self._schedule(day, day + timedelta(days=horizon_days))
        if len(sched) == 0:
            raise ValueError(
                f"No trading day found within {horizon_days} days of {day}"
            )
        return sched.index[0].date()

    def last_completed_trading_day(self, as_of: date) -> date:
        """Most recent valid trading day strictly on/before ``as_of``.

        Callers still apply the current-day safety delay separately.
        """
        sched = self._schedule(as_of - timedelta(days=30), as_of)
        if len(sched) == 0:
            raise ValueError(f"No trading day found on/before {as_of}")
        return sched.index[-1].date()

    def trading_days_desc(self, boundary: date, newest: date) -> list[date]:
        """All valid trading days from ``newest`` back to the first trading day
        on/after ``boundary``, newest first."""
        first = self.first_trading_day_on_or_after(boundary)
        if newest < first:
            return []
        days = self.valid_trading_days(first, newest)
        return list(reversed(days))

    # -- official close / early close ---------------------------------------
    def official_close(self, day: date) -> datetime:
        sched = self._schedule(day, day)
        if len(sched) == 0:
            raise ValueError(f"{day} is not a trading day")
        close = sched.iloc[0]["market_close"]
        return close.to_pydatetime().astimezone(self.tz)

    def market_open(self, day: date) -> datetime:
        sched = self._schedule(day, day)
        if len(sched) == 0:
            raise ValueError(f"{day} is not a trading day")
        open_ = sched.iloc[0]["market_open"]
        return open_.to_pydatetime().astimezone(self.tz)

    def is_early_close(self, day: date) -> bool:
        close = self.official_close(day)
        return (close.hour, close.minute) < (16, 0)

    # -- session windows -----------------------------------------------------
    def _resolve_boundary(self, token: str, day: date, close: datetime) -> datetime:
        if token == "market_close":
            return close
        clock = _parse_clock(token)
        return datetime.combine(day, clock, tzinfo=self.tz)

    def session_window(self, session: str, day: date) -> SessionWindow:
        if session not in self.session_spec:
            raise ValueError(f"Unknown session {session!r}")
        if not self.is_trading_day(day):
            raise ValueError(f"{day} is not a trading day")
        close = self.official_close(day)
        spec = self.session_spec[session]
        start = self._resolve_boundary(spec["start"], day, close)
        end = self._resolve_boundary(spec["end"], day, close)
        return SessionWindow(
            session=session,
            trading_date=day,
            start=start,
            end=end,
            official_close=close,
            early_close=self.is_early_close(day),
        )

    def all_session_windows(self, day: date) -> dict[str, SessionWindow]:
        return {s: self.session_window(s, day) for s in SESSION_ORDER}

    def session_of(self, moment: datetime) -> str | None:
        """Return the session a tz-aware datetime falls in, or ``None``."""
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=self.tz)
        moment = moment.astimezone(self.tz)
        day = moment.date()
        if not self.is_trading_day(day):
            return None
        for session in SESSION_ORDER:
            if self.session_window(session, day).contains(moment):
                return session
        return None

    def session_end_with_safety(
        self, day: date, safety_delay_seconds: int
    ) -> datetime:
        """End of the final session (HPOST) plus a safety delay - the earliest
        a trading day may be collected."""
        hpost = self.session_window("HPOST", day)
        return hpost.end + timedelta(seconds=safety_delay_seconds)
