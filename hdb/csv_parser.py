"""Trade Ideas CSV parsing with dynamic header-row detection.

Trade Ideas "Save Contents" exports sometimes include preamble lines before the
real header row, inconsistent delimiters, and a variety of timestamp formats.
This module:

* sniffs the delimiter;
* finds the header row by scoring rows against known column tokens;
* normalizes column names to canonical field names;
* parses timestamps into tz-aware datetimes (given the trading date + tz);
* returns structured rows plus rejected rows with reasons.

It is intentionally dependency-light (stdlib ``csv`` only) so it is fully
testable everywhere.
"""

from __future__ import annotations

import csv
import io
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from .version import PARSER_VERSION

# Canonical field name -> set of accepted header aliases (lowercased, stripped).
COLUMN_ALIASES: dict[str, set[str]] = {
    "time": {"time", "alert time", "timestamp", "datetime", "time est", "time et"},
    "date": {"date", "trade date", "session date"},
    "symbol": {"symbol", "sym", "ticker"},
    "alert_type": {"type", "alert", "alert type", "description", "signal", "window"},
    "price": {"price", "last", "alert price", "trigger price"},
    "count": {"count", "alert count", "# alerts", "num alerts", "alerts"},
    "volume": {"volume", "vol", "total volume", "day volume"},
    "event_id": {"id", "event id", "alert id", "guid", "uuid"},
}

# Tokens whose presence strongly indicates a header row.
_HEADER_TOKENS = {alias for aliases in COLUMN_ALIASES.values() for alias in aliases}

_NUMERIC_CLEAN_RE = re.compile(r"[,$%\s]")


class CsvParseError(Exception):
    pass


@dataclass
class ParsedRow:
    row_number: int  # 1-based, within data rows (excludes header)
    raw: dict[str, str]  # original header->value
    fields: dict[str, Any]  # canonical field -> normalized value
    timestamp: datetime | None = None


@dataclass
class ParsedCsv:
    path: str
    delimiter: str
    header_row_index: int  # 0-based line index of the header in the file
    header: list[str]
    canonical_map: dict[str, str]  # original header -> canonical field
    rows: list[ParsedRow] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    parser_version: str = PARSER_VERSION

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def newest_timestamp(self) -> datetime | None:
        ts = [r.timestamp for r in self.rows if r.timestamp is not None]
        return max(ts) if ts else None

    @property
    def oldest_timestamp(self) -> datetime | None:
        ts = [r.timestamp for r in self.rows if r.timestamp is not None]
        return min(ts) if ts else None

    @property
    def first_symbol(self) -> str | None:
        for r in self.rows:
            sym = r.fields.get("symbol")
            if sym:
                return sym
        return None

    @property
    def last_symbol(self) -> str | None:
        for r in reversed(self.rows):
            sym = r.fields.get("symbol")
            if sym:
                return sym
        return None


def sniff_delimiter(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        return dialect.delimiter
    except csv.Error:
        # Fall back to the most frequent common delimiter on the first
        # non-empty line.
        for line in sample.splitlines():
            if line.strip():
                counts = {d: line.count(d) for d in [",", "\t", ";", "|"]}
                best = max(counts, key=counts.get)
                if counts[best] > 0:
                    return best
                break
        return ","


def _norm(token: str) -> str:
    return token.strip().strip('"').strip().lower()


def score_header_row(cells: list[str]) -> int:
    """Number of cells that match a known header token."""
    score = 0
    for cell in cells:
        if _norm(cell) in _HEADER_TOKENS:
            score += 1
    return score


def detect_header_row(rows: list[list[str]], max_scan: int = 50) -> int:
    """Return the 0-based index of the most likely header row.

    Scans up to ``max_scan`` rows and picks the earliest row with the highest
    known-token score (score must be >= 2 to be considered a header).
    """
    best_index = -1
    best_score = 1  # require at least 2 matching tokens
    for idx, cells in enumerate(rows[:max_scan]):
        score = score_header_row(cells)
        if score > best_score:
            best_score = score
            best_index = idx
    if best_index < 0:
        raise CsvParseError("Could not detect a header row (no known columns found)")
    return best_index


def build_canonical_map(header: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for original in header:
        norm = _norm(original)
        for canonical, aliases in COLUMN_ALIASES.items():
            if norm in aliases:
                mapping[original] = canonical
                break
    return mapping


# -- timestamp parsing -------------------------------------------------------
_TIME_FORMATS_WITH_DATE = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %H:%M",
    "%m/%d/%y %H:%M:%S",
    "%m/%d/%y %I:%M:%S %p",
]
_TIME_FORMATS_TIME_ONLY = [
    "%H:%M:%S",
    "%I:%M:%S %p",
    "%H:%M",
    "%I:%M %p",
    "%H:%M:%S.%f",
]


def parse_timestamp(
    value: str,
    trading_date: date,
    tz: ZoneInfo,
    date_value: str | None = None,
) -> datetime | None:
    """Parse a Trade Ideas time value into a tz-aware datetime.

    Handles time-only values (combined with the trading date), full datetime
    values, and an optional separate date column.
    """
    if value is None:
        return None
    raw = value.strip().strip('"').strip()
    if not raw:
        return None

    # Full datetime formats first.
    for fmt in _TIME_FORMATS_WITH_DATE:
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=tz)
        except ValueError:
            continue

    # If a separate date column is provided, combine.
    base_date = trading_date
    if date_value:
        parsed_date = _parse_date_only(date_value)
        if parsed_date is not None:
            base_date = parsed_date

    for fmt in _TIME_FORMATS_TIME_ONLY:
        try:
            t = datetime.strptime(raw, fmt).time()
            return datetime.combine(base_date, t, tzinfo=tz)
        except ValueError:
            continue
    return None


def _parse_date_only(value: str) -> date | None:
    raw = value.strip().strip('"').strip()
    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"]:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def normalize_number(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = _NUMERIC_CLEAN_RE.sub("", str(value)).strip()
    if cleaned in ("", "-", "N/A", "n/a", "NA"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_csv(
    path: str,
    trading_date: date,
    timezone: str = "America/New_York",
    max_scan: int = 50,
) -> ParsedCsv:
    """Parse a Trade Ideas CSV file into structured rows."""
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        text = fh.read()
    return parse_csv_text(text, trading_date, timezone, max_scan=max_scan, path=path)


def parse_csv_text(
    text: str,
    trading_date: date,
    timezone: str = "America/New_York",
    max_scan: int = 50,
    path: str = "<memory>",
) -> ParsedCsv:
    tz = ZoneInfo(timezone)
    if not text.strip():
        raise CsvParseError("Empty CSV content")
    sample = "\n".join(text.splitlines()[:20])
    delimiter = sniff_delimiter(sample)

    all_rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    all_rows = [r for r in all_rows if any(cell.strip() for cell in r)]
    if not all_rows:
        raise CsvParseError("No non-empty rows in CSV")

    header_idx = detect_header_row(all_rows, max_scan=max_scan)
    header = [h.strip() for h in all_rows[header_idx]]
    canonical_map = build_canonical_map(header)

    parsed = ParsedCsv(
        path=path,
        delimiter=delimiter,
        header_row_index=header_idx,
        header=header,
        canonical_map=canonical_map,
    )

    data_rows = all_rows[header_idx + 1 :]
    for i, cells in enumerate(data_rows, start=1):
        # Skip a repeated header row (page overlap can duplicate the header).
        if score_header_row(cells) >= 2 and cells[: len(header)] == header:
            continue
        raw: dict[str, str] = {}
        for j, col in enumerate(header):
            raw[col] = cells[j].strip() if j < len(cells) else ""
        fields = _canonicalize(raw, canonical_map, trading_date, tz)
        if not any(v not in (None, "") for v in fields.values()):
            parsed.rejected.append(
                {"row_number": i, "reason": "empty_row", "raw": raw}
            )
            continue
        ts = fields.get("timestamp")
        parsed.rows.append(
            ParsedRow(row_number=i, raw=raw, fields=fields, timestamp=ts)
        )
    return parsed


def _canonicalize(
    raw: dict[str, str],
    canonical_map: dict[str, str],
    trading_date: date,
    tz: ZoneInfo,
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for original, value in raw.items():
        canonical = canonical_map.get(original)
        if not canonical:
            continue
        fields[canonical] = value.strip() if isinstance(value, str) else value

    # Normalize numerics.
    if "price" in fields:
        fields["price_norm"] = normalize_number(fields.get("price"))
    if "volume" in fields:
        fields["volume_norm"] = normalize_number(fields.get("volume"))
    if "count" in fields:
        c = normalize_number(fields.get("count"))
        fields["count_norm"] = int(c) if c is not None else None

    # Timestamp.
    time_value = fields.get("time")
    date_value = fields.get("date")
    if time_value:
        fields["timestamp"] = parse_timestamp(
            time_value, trading_date, tz, date_value=date_value
        )
    return fields
