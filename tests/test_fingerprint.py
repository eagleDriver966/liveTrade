"""Tests: stable fingerprints, fallback version, page fingerprint."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from hdb.fingerprint import (
    choose_version,
    compute_fingerprint,
    page_fingerprint,
)

NY = ZoneInfo("America/New_York")


def _fields(**over):
    base = {
        "trading_date": "2026-02-02",
        "alert_timestamp": datetime(2026, 2, 2, 10, 0, tzinfo=NY),
        "alert_type": "High",
        "symbol": "AAA",
        "price": 10.5,
        "alert_count": 3,
        "volume": 1000.0,
        "session": "NHP",
    }
    base.update(over)
    return base


def test_stable_and_deterministic():
    a = compute_fingerprint(_fields())
    b = compute_fingerprint(_fields())
    assert a.value == b.value
    assert a.version == "fp-v1"


def test_lineage_excluded():
    # Adding filename/part/row must not change the fingerprint.
    f = _fields()
    a = compute_fingerprint(f)
    f2 = dict(f)
    f2.update({"filename": "x.csv", "part_number": 3, "source_row_number": 99})
    b = compute_fingerprint(f2)
    assert a.value == b.value


def test_uses_more_than_symbol_type_time_price():
    # Rows identical in symbol/type/time/price but differing in count/volume
    # must fingerprint differently (we use the richer field set).
    a = compute_fingerprint(_fields(alert_count=3, volume=1000.0))
    b = compute_fingerprint(_fields(alert_count=9, volume=5000.0))
    assert a.value != b.value


def test_version_fallback_without_volume_count():
    f = _fields(volume=None, alert_count=None)
    assert choose_version(f) == "fp-v1-lite"
    fp = compute_fingerprint(f)
    assert fp.version == "fp-v1-lite"


def test_different_session_differs():
    a = compute_fingerprint(_fields(session="NHP"))
    b = compute_fingerprint(_fields(session="HPRE"))
    assert a.value != b.value


def test_price_change_differs():
    a = compute_fingerprint(_fields(price=10.5))
    b = compute_fingerprint(_fields(price=10.6))
    assert a.value != b.value


def test_page_fingerprint_order_independent():
    fps = ["a", "b", "c"]
    assert page_fingerprint(fps) == page_fingerprint(list(reversed(fps)))
    assert page_fingerprint(["a", "b"]) != page_fingerprint(["a", "c"])
