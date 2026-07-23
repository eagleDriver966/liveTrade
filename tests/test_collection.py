"""Tests: completion-criteria decisions and destructive-More guard."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from hdb.collection import (
    PageRecord,
    can_advance_more,
    evaluate_completion,
)
from hdb.status import Status

NY = ZoneInfo("America/New_York")
D = date(2026, 2, 2)


def _page(part, rows, oldest_h, newest_h, sha, pfp, events=(), status=Status.IMPORTED):
    return PageRecord(
        part_number=part,
        row_count=rows,
        oldest_timestamp=datetime(2026, 2, 2, oldest_h, 0, tzinfo=NY),
        newest_timestamp=datetime(2026, 2, 2, newest_h, 0, tzinfo=NY),
        sha256=sha,
        page_fingerprint=pfp,
        event_fingerprints=frozenset(events),
        status=status,
    )


def test_empty_first_page_is_empty_verified(calendar):
    cur = PageRecord(part_number=1, row_count=0, oldest_timestamp=None,
                     newest_timestamp=None, sha256="x", page_fingerprint=None)
    d = evaluate_completion(cur, None, "HPRE", D, calendar)
    assert d.complete is True
    assert d.status == Status.EMPTY_VERIFIED


def test_explicit_no_more_history_always_completes(calendar):
    cur = _page(1, 5, 10, 15, "s1", "p1")
    d = evaluate_completion(cur, None, "NHP", D, calendar, explicit_no_more_history=True)
    assert d.complete is True
    assert d.status == Status.VERIFIED
    assert "explicit_no_more_history" in d.signals


def test_boundary_reached_large_page_conclusive(calendar):
    # Oldest at 09:30 == NHP start; large page -> conclusive alone.
    cur = _page(3, 1500, 9, 12, "s3", "p3")  # 09:00 < start, definitely reached
    d = evaluate_completion(cur, None, "NHP", D, calendar, min_conclusive_rows=1000)
    assert d.complete is True
    assert "boundary_reached" in d.signals


def test_small_page_single_signal_not_conclusive(calendar):
    # Boundary reached but tiny page and no other signal -> keep going.
    cur = _page(2, 5, 9, 12, "s2", "p2")
    d = evaluate_completion(cur, None, "NHP", D, calendar, min_conclusive_rows=1000)
    assert d.complete is False


def test_repeated_page_detected(calendar):
    prev = _page(1, 500, 10, 15, "same", "samepfp", events=("a", "b"))
    cur = _page(2, 500, 10, 15, "same", "samepfp", events=("a", "b"))
    d = evaluate_completion(cur, prev, "NHP", D, calendar, min_conclusive_rows=1000)
    assert d.complete is True
    # Multiple signals: same fingerprint, checksum, oldest/newest, no new events, no progress.
    assert len(d.signals) >= 2


def test_no_backward_progress_plus_no_new_events(calendar):
    prev = _page(1, 500, 11, 15, "s1", "p1", events=("a", "b"))
    cur = _page(2, 500, 11, 15, "s2", "p2", events=("a", "b"))  # same times, no new events
    d = evaluate_completion(cur, prev, "NHP", D, calendar, min_conclusive_rows=1000)
    assert d.complete is True
    assert "no_backward_progress" in d.signals


def test_progress_continues(calendar):
    prev = _page(1, 500, 14, 15, "s1", "p1", events=("a", "b"))
    cur = _page(2, 500, 11, 13, "s2", "p2", events=("c", "d"))  # moved backward, new events
    d = evaluate_completion(cur, prev, "NHP", D, calendar, min_conclusive_rows=1000)
    assert d.complete is False


def test_can_advance_more_guard():
    validated = PageRecord(part_number=1, row_count=1, oldest_timestamp=None,
                           newest_timestamp=None, sha256="x", page_fingerprint=None,
                           status=Status.IMPORTED)
    ok, _ = can_advance_more(validated)
    assert ok is True

    not_ready = PageRecord(part_number=1, row_count=1, oldest_timestamp=None,
                           newest_timestamp=None, sha256="x", page_fingerprint=None,
                           status=Status.EXPORTED)
    ok2, reason = can_advance_more(not_ready)
    assert ok2 is False
    assert "not_safe" in reason
