"""Explicit status model shared across the collection pipeline.

Statuses are stored as plain strings in SQLite so they remain human-readable and
stable across code changes.
"""

from __future__ import annotations

from enum import Enum


class Status(str, Enum):
    """Lifecycle statuses for runs, days, sessions, and export parts."""

    PENDING = "PENDING"
    COLLECTING = "COLLECTING"
    EXPORTED = "EXPORTED"
    VALIDATED = "VALIDATED"
    IMPORTED = "IMPORTED"
    VERIFIED = "VERIFIED"
    EMPTY_VERIFIED = "EMPTY_VERIFIED"
    DUPLICATE_PAGE = "DUPLICATE_PAGE"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# Statuses that mean a session part is safe to advance past (More is allowed).
PART_SAFE_TO_ADVANCE = frozenset({Status.VALIDATED, Status.IMPORTED, Status.VERIFIED})

# Statuses that count a session as successfully finished for a trading day.
SESSION_DONE = frozenset({Status.VERIFIED, Status.EMPTY_VERIFIED})

# Terminal failure-ish statuses.
TERMINAL_UNSUCCESSFUL = frozenset(
    {Status.FAILED, Status.CANCELLED, Status.INCOMPLETE}
)


def date_is_verified(session_statuses: dict[str, Status]) -> bool:
    """A trading date is VERIFIED only when HPRE, NHP and HPOST are each
    VERIFIED or EMPTY_VERIFIED.
    """
    required = ("HPRE", "NHP", "HPOST")
    return all(session_statuses.get(s) in SESSION_DONE for s in required)
