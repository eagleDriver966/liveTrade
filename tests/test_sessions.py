"""Tests: session classification and page validation."""

from __future__ import annotations

from datetime import date

from hdb.csv_parser import parse_csv_text
from hdb.sessions import reached_session_start, validate_page

D = date(2026, 2, 2)


def _csv(rows):
    header = "Time,Symbol,Type,Price,Count,Volume,Id\n"
    body = "".join(f"{t},{s},High,1.0,1,1000,{s}\n" for t, s in rows)
    return header + body


def test_validate_page_in_window(calendar):
    text = _csv([("15:59:00", "AAA"), ("10:00:00", "BBB"), ("09:30:00", "CCC")])
    parsed = parse_csv_text(text, D)
    v = validate_page(parsed, "NHP", D, calendar)
    assert v.ok is True
    assert v.in_window_rows == 3
    assert v.out_of_window_rows == 0


def test_validate_page_out_of_window_fails(calendar):
    # All timestamps are premarket, but we claim NHP.
    text = _csv([("08:00:00", "AAA"), ("08:30:00", "BBB"), ("09:00:00", "CCC")])
    parsed = parse_csv_text(text, D)
    v = validate_page(parsed, "NHP", D, calendar)
    assert v.ok is False
    assert any("too_many_out_of_window_rows" in r for r in v.reasons)


def test_validate_page_empty_is_warning_not_error(calendar):
    text = "Time,Symbol,Type,Price,Count,Volume,Id\n"
    parsed = parse_csv_text(text, D)
    v = validate_page(parsed, "HPRE", D, calendar)
    assert v.ok is True
    assert "no_data_rows" in v.warnings


def test_reached_session_start(calendar):
    text = _csv([("09:30:00", "AAA")])
    parsed = parse_csv_text(text, D)
    assert reached_session_start(parsed.oldest_timestamp, "NHP", D, calendar) is True
    text2 = _csv([("11:00:00", "AAA")])
    parsed2 = parse_csv_text(text2, D)
    assert reached_session_start(parsed2.oldest_timestamp, "NHP", D, calendar) is False
