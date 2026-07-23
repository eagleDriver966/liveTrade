"""Tests: exchange calendar, session boundaries, holidays, early close, DST."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from hdb.calendar_util import ExchangeCalendar

NY = ZoneInfo("America/New_York")


def test_boundary_feb_1_2026_is_sunday(calendar):
    assert calendar.is_trading_day(date(2026, 2, 1)) is False
    assert calendar.first_trading_day_on_or_after(date(2026, 2, 1)) == date(2026, 2, 2)


def test_weekend_and_holiday_excluded(calendar):
    # 2026-01-01 New Year's Day holiday.
    assert calendar.is_trading_day(date(2026, 1, 1)) is False
    # Saturday.
    assert calendar.is_trading_day(date(2026, 2, 7)) is False


def test_regular_session_windows(calendar):
    w = calendar.all_session_windows(date(2026, 2, 2))
    assert w["HPRE"].start.strftime("%H:%M:%S") == "04:00:00"
    assert w["HPRE"].end.strftime("%H:%M:%S") == "09:29:59"
    assert w["NHP"].start.strftime("%H:%M:%S") == "09:30:00"
    assert w["NHP"].end.strftime("%H:%M:%S") == "16:00:00"
    assert w["HPOST"].start.strftime("%H:%M:%S") == "16:00:00"
    assert w["HPOST"].end.strftime("%H:%M:%S") == "20:00:00"


def test_early_close_day(calendar):
    # Day after Thanksgiving 2025-11-28 closes at 13:00.
    assert calendar.is_early_close(date(2025, 11, 28)) is True
    nhp = calendar.session_window("NHP", date(2025, 11, 28))
    assert nhp.end.strftime("%H:%M:%S") == "13:00:00"
    hpost = calendar.session_window("HPOST", date(2025, 11, 28))
    assert hpost.start.strftime("%H:%M:%S") == "13:00:00"


def test_dst_offsets(calendar):
    # Winter is EST (-05:00), summer is EDT (-04:00).
    winter = calendar.session_window("NHP", date(2026, 2, 2))
    assert winter.start.utcoffset().total_seconds() == -5 * 3600
    summer = calendar.session_window("NHP", date(2026, 7, 6))
    assert summer.start.utcoffset().total_seconds() == -4 * 3600


def test_session_of(calendar):
    d = date(2026, 2, 2)
    assert calendar.session_of(datetime(2026, 2, 2, 8, 0, tzinfo=NY)) == "HPRE"
    assert calendar.session_of(datetime(2026, 2, 2, 10, 0, tzinfo=NY)) == "NHP"
    assert calendar.session_of(datetime(2026, 2, 2, 18, 0, tzinfo=NY)) == "HPOST"
    # 03:00 is before HPRE.
    assert calendar.session_of(datetime(2026, 2, 2, 3, 0, tzinfo=NY)) is None


def test_trading_days_desc(calendar):
    days = calendar.trading_days_desc(date(2026, 2, 1), date(2026, 2, 5))
    assert days == [date(2026, 2, 5), date(2026, 2, 4), date(2026, 2, 3), date(2026, 2, 2)]


def test_session_end_with_safety(calendar):
    end = calendar.session_end_with_safety(date(2026, 2, 2), 3600)
    # HPOST ends 20:00, + 1h = 21:00.
    assert end.strftime("%H:%M:%S") == "21:00:00"
