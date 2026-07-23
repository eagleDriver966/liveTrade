"""Tests: header detection, timestamp parsing, numeric normalization."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import pytest

from hdb.csv_parser import (
    CsvParseError,
    build_canonical_map,
    detect_header_row,
    normalize_number,
    parse_csv_text,
    parse_timestamp,
)

NY = ZoneInfo("America/New_York")
D = date(2026, 2, 2)

PREAMBLE_CSV = """Trade Ideas Pro - History Export
Generated 2026-02-02
\r
Time,Symbol,Type,Price,Count,Volume,Id
15:59:00,AAA,High,10.5,3,\"1,000,000\",e1
15:58:00,BBB,Low,9.25,1,500000,e2
"""


def test_detect_header_row_with_preamble():
    rows = [line.split(",") for line in PREAMBLE_CSV.splitlines() if line.strip()]
    idx = detect_header_row(rows)
    assert rows[idx][0].strip() == "Time"


def test_parse_with_preamble():
    parsed = parse_csv_text(PREAMBLE_CSV, D, "America/New_York")
    assert parsed.row_count == 2
    assert parsed.first_symbol == "AAA"
    assert parsed.last_symbol == "BBB"
    assert parsed.newest_timestamp.strftime("%H:%M:%S") == "15:59:00"
    assert parsed.oldest_timestamp.strftime("%H:%M:%S") == "15:58:00"
    # Volume with thousands separators normalized.
    assert parsed.rows[0].fields["volume_norm"] == 1000000.0
    assert parsed.rows[0].fields["price_norm"] == 10.5


def test_canonical_map_aliases():
    header = ["Alert Time", "Ticker", "Description", "Last", "Alerts", "Vol"]
    m = build_canonical_map(header)
    assert m["Alert Time"] == "time"
    assert m["Ticker"] == "symbol"
    assert m["Description"] == "alert_type"
    assert m["Last"] == "price"
    assert m["Alerts"] == "count"
    assert m["Vol"] == "volume"


def test_tab_delimited():
    text = "Time\tSymbol\tType\tPrice\n10:00:00\tZZZ\tHigh\t5.0\n"
    parsed = parse_csv_text(text, D)
    assert parsed.delimiter == "\t"
    assert parsed.first_symbol == "ZZZ"


def test_parse_timestamp_formats():
    assert parse_timestamp("09:30:00", D, NY).strftime("%H:%M:%S") == "09:30:00"
    assert parse_timestamp("9:30:00 AM", D, NY).strftime("%H:%M:%S") == "09:30:00"
    assert parse_timestamp("01:05:00 PM", D, NY).strftime("%H:%M:%S") == "13:05:00"
    full = parse_timestamp("2026-02-02 15:59:00", D, NY)
    assert full.strftime("%Y-%m-%d %H:%M:%S") == "2026-02-02 15:59:00"
    assert parse_timestamp("", D, NY) is None
    assert parse_timestamp("garbage", D, NY) is None


def test_normalize_number():
    assert normalize_number("$1,234.50") == 1234.5
    assert normalize_number("1,000,000") == 1000000.0
    assert normalize_number("N/A") is None
    assert normalize_number("") is None
    assert normalize_number(None) is None


def test_empty_csv_raises():
    with pytest.raises(CsvParseError):
        parse_csv_text("   \n  \n", D)


def test_repeated_header_row_skipped():
    text = (
        "Time,Symbol,Type,Price\n"
        "10:00:00,AAA,High,1.0\n"
        "Time,Symbol,Type,Price\n"  # duplicated header from page overlap
        "09:00:00,BBB,Low,2.0\n"
    )
    parsed = parse_csv_text(text, D)
    assert parsed.row_count == 2
    assert {r.fields["symbol"] for r in parsed.rows} == {"AAA", "BBB"}
