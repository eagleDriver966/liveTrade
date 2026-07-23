"""Versioned SHA-256 event fingerprints for de-duplicating normalized alerts.

The legacy Symbol-Type-Time-Price fingerprint is insufficient (collisions across
sessions, count/volume changes).  This module produces a stable SHA-256 over a
canonical, ordered set of fields and records which fingerprint *version* was used
so overlapping pages that share an event resolve to one normalized row.

Fingerprints intentionally exclude source lineage (filename, part number, source
row number) so the same real-world event fingerprints identically regardless of
which page/file it appeared in.

Fallback versions are used when preferred fields are unavailable; the version
used is stored alongside the fingerprint.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Field lists per version, in canonical order.  Newer/preferred first.
FINGERPRINT_FIELDS: dict[str, list[str]] = {
    # Preferred: includes a Trade Ideas event identifier when present.
    "fp-v1": [
        "trading_date",
        "alert_timestamp",
        "alert_type",
        "symbol",
        "price_norm",
        "count_norm",
        "volume_norm",
        "source_session",
        "panel_role",
        "event_id",
    ],
    # Fallback A: no vendor event id.
    "fp-v1a": [
        "trading_date",
        "alert_timestamp",
        "alert_type",
        "symbol",
        "price_norm",
        "count_norm",
        "volume_norm",
        "source_session",
        "panel_role",
    ],
    # Fallback B: no volume/count (older exports).
    "fp-v1b": [
        "trading_date",
        "alert_timestamp",
        "alert_type",
        "symbol",
        "price_norm",
        "source_session",
        "panel_role",
    ],
    # Fallback C: minimal - time + symbol + type + session only.
    "fp-v1c": [
        "trading_date",
        "alert_timestamp",
        "alert_type",
        "symbol",
        "source_session",
    ],
}

# Order in which versions are attempted (preferred -> most degraded).
VERSION_PREFERENCE = ["fp-v1", "fp-v1a", "fp-v1b", "fp-v1c"]

# Fields that must be present & non-empty for a version to be usable.  The
# ladder degrades: v1 needs a vendor event id; v1a needs volume+count; v1b needs
# a price; v1c needs only the minimal identity fields.
_REQUIRED_FOR_VERSION: dict[str, list[str]] = {
    "fp-v1": ["trading_date", "alert_timestamp", "symbol", "alert_type", "event_id"],
    "fp-v1a": ["trading_date", "alert_timestamp", "symbol", "alert_type",
               "volume_norm", "count_norm"],
    "fp-v1b": ["trading_date", "alert_timestamp", "symbol", "alert_type", "price_norm"],
    "fp-v1c": ["trading_date", "alert_timestamp", "symbol", "alert_type"],
}


@dataclass(frozen=True)
class Fingerprint:
    value: str  # hex digest
    version: str

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.version}:{self.value}"


def _canonical(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        # Normalize to ISO-8601 with timezone offset; second precision.
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, float):
        # Stable numeric text; avoid trailing float noise.
        if value == int(value):
            return str(int(value))
        return repr(round(value, 6))
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value).strip()


def _has_required(fields: dict[str, Any], version: str) -> bool:
    for key in _REQUIRED_FOR_VERSION[version]:
        val = fields.get(key)
        if val is None or (isinstance(val, str) and val.strip() == ""):
            return False
    return True


def choose_version(fields: dict[str, Any]) -> str:
    for version in VERSION_PREFERENCE:
        if _has_required(fields, version):
            return version
    # If even the minimal version's requirements are missing, still use the
    # minimal version (caller will likely route the row to rejected_rows).
    return VERSION_PREFERENCE[-1]


def compute_fingerprint(fields: dict[str, Any], version: str | None = None) -> Fingerprint:
    """Compute a fingerprint for a normalized event.

    ``fields`` should use canonical keys (see FINGERPRINT_FIELDS).  If
    ``version`` is not given, the best usable version is chosen automatically.
    """
    chosen = version or choose_version(fields)
    if chosen not in FINGERPRINT_FIELDS:
        raise ValueError(f"Unknown fingerprint version {chosen!r}")
    parts = [chosen]
    for key in FINGERPRINT_FIELDS[chosen]:
        parts.append(f"{key}={_canonical(fields.get(key))}")
    payload = "\x1f".join(parts).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return Fingerprint(value=digest, version=chosen)


def page_fingerprint(event_fingerprints: list[str]) -> str:
    """Order-independent SHA-256 over a page's normalized event fingerprints.

    Used to detect a repeated final page (identical normalized event set).
    """
    h = hashlib.sha256()
    for fp in sorted(event_fingerprints):
        h.update(fp.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()
