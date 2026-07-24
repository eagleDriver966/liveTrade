"""Collection decision logic (automation-independent).

This module contains the *pure* rules that make the collection safe and
correct, with no Windows/automation dependencies so they are fully unit-tested:

* completion-criteria evaluation using multiple signals;
* repeated-page / no-backward-progress detection;
* the destructive-More guard (never advance unless the current page is fully
  validated + imported).

The actual clicking/exporting is done by :mod:`hdb.collector` using these
decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .calendar_util import ExchangeCalendar
from .status import PART_SAFE_TO_ADVANCE, Status


@dataclass
class PageRecord:
    """Metadata about one exported+validated history page."""

    part_number: int
    row_count: int
    newest_timestamp: datetime | None
    oldest_timestamp: datetime | None
    sha256: str | None
    page_fingerprint: str | None
    event_fingerprints: frozenset[str] = field(default_factory=frozenset)
    status: Status = Status.PENDING


@dataclass
class CompletionDecision:
    complete: bool
    signals: list[str]
    reason: str | None
    status: Status

    def to_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "signals": list(self.signals),
            "reason": self.reason,
            "status": str(self.status),
        }


def detect_signals(
    current: PageRecord,
    previous: PageRecord | None,
    session: str,
    trading_date: date,
    calendar: ExchangeCalendar,
) -> list[str]:
    """Return the set of completion signals present for ``current``."""
    signals: list[str] = []

    # Session boundary reached (oldest timestamp <= official session start).
    if current.oldest_timestamp is not None:
        start = calendar.session_window(session, trading_date).start
        if current.oldest_timestamp <= start:
            signals.append("boundary_reached")

    if previous is not None:
        if (
            current.page_fingerprint is not None
            and current.page_fingerprint == previous.page_fingerprint
        ):
            signals.append("same_page_fingerprint")
        if current.sha256 is not None and current.sha256 == previous.sha256:
            signals.append("same_checksum")
        if (
            current.oldest_timestamp is not None
            and current.newest_timestamp is not None
            and current.oldest_timestamp == previous.oldest_timestamp
            and current.newest_timestamp == previous.newest_timestamp
        ):
            signals.append("same_oldest_and_newest")

        # No new event fingerprints compared to previous page.
        if current.event_fingerprints and previous.event_fingerprints:
            new_events = current.event_fingerprints - previous.event_fingerprints
            if not new_events:
                signals.append("no_new_event_fingerprints")

        # No backward progress: oldest failed to move earlier.
        if (
            current.oldest_timestamp is not None
            and previous.oldest_timestamp is not None
            and current.oldest_timestamp >= previous.oldest_timestamp
        ):
            signals.append("no_backward_progress")

    return signals


def evaluate_completion(
    current: PageRecord,
    previous: PageRecord | None,
    session: str,
    trading_date: date,
    calendar: ExchangeCalendar,
    *,
    explicit_no_more_history: bool = False,
    min_conclusive_rows: int = 1000,
    min_completion_signals: int = 2,
) -> CompletionDecision:
    """Decide whether the session is complete after the current page.

    Rules:
    * Explicit "no more history" from Trade Ideas is always conclusive.
    * An empty first page => EMPTY_VERIFIED.
    * Otherwise require >= ``min_completion_signals`` signals, OR a
      ``boundary_reached`` signal on a substantial page
      (>= ``min_conclusive_rows`` rows).
    * A page with fewer than ``min_conclusive_rows`` rows is never conclusive on
      a single signal alone.
    """
    # Explicit end-of-history is authoritative.
    if explicit_no_more_history:
        return CompletionDecision(
            complete=True,
            signals=["explicit_no_more_history"],
            reason="explicit_no_more_history",
            status=Status.VERIFIED,
        )

    # Empty page.
    if current.row_count == 0:
        if previous is None:
            return CompletionDecision(
                complete=True,
                signals=["empty_first_page"],
                reason="empty_session",
                status=Status.EMPTY_VERIFIED,
            )
        # An empty page after data: treat as end (no backward progress possible).
        return CompletionDecision(
            complete=True,
            signals=["empty_page_after_data"],
            reason="empty_page_after_data",
            status=Status.VERIFIED,
        )

    signals = detect_signals(current, previous, session, trading_date, calendar)

    if not signals:
        return CompletionDecision(
            complete=False, signals=signals, reason=None, status=Status.COLLECTING
        )

    repeated = any(
        s in signals
        for s in ("same_page_fingerprint", "same_checksum", "no_new_event_fingerprints")
    )

    substantial = current.row_count >= min_conclusive_rows
    strong_boundary = "boundary_reached" in signals and substantial

    # A small page can only conclude with multiple independent signals.
    enough_signals = len(set(signals)) >= min_completion_signals

    if strong_boundary or enough_signals:
        status = Status.DUPLICATE_PAGE if repeated and "boundary_reached" not in signals else Status.VERIFIED
        reason = (
            "boundary_reached"
            if strong_boundary
            else "+".join(sorted(set(signals)))
        )
        return CompletionDecision(
            complete=True, signals=sorted(set(signals)), reason=reason, status=status
        )

    # Single weak signal on a small page: not conclusive yet, but if we detect a
    # repeat we must still stop (More would only reproduce the same page).
    if repeated:
        return CompletionDecision(
            complete=True,
            signals=sorted(set(signals)),
            reason="repeated_page_single_signal",
            status=Status.DUPLICATE_PAGE,
        )

    return CompletionDecision(
        complete=False, signals=sorted(set(signals)), reason=None, status=Status.COLLECTING
    )


def can_advance_more(current: PageRecord) -> tuple[bool, str]:
    """Destructive-More guard.

    Never allow More unless the current page has been validated/imported.
    Returns ``(allowed, reason)``.
    """
    if current.status in PART_SAFE_TO_ADVANCE:
        return True, "current_page_validated"
    return False, f"current_page_status_not_safe:{current.status}"
