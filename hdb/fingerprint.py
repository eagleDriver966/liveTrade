"""Stable SHA-256 event fingerprints for safe deduplication.

Per the project scope, a fingerprint uses the stable alert fields (not only
Symbol/Type/Time/Price):

    trading date, timestamp, alert type, symbol, price, alert count, volume,
    session.

A single fallback version (``fp-v1-lite``) is used when volume/count are
unavailable, and the version used is recorded alongside the fingerprint.

Fingerprints never include source lineage (filename, part number, source row
number) so the same real-world alert fingerprints identically across overlapping
pages.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Canonical field order per version.
FINGERPRINT_FIELDS: dict[str, list[str]] = {
    "fp-v1": [
        "trading_date",
        "alert_timestamp",
        "alert_type",
        "symbol",
        "price",
        "alert_count",
        "volume",
        "session",
    ],
    # Fallback: no volume/count available.
    "fp-v1-lite": [
        "trading_date",
        "alert_timestamp",
        "alert_type",
        "symbol",
        "price",
        "session",
    ],
}

VERSION_PREFERENCE = ["fp-v1", "fp-v1-lite"]

# Fields that must be present & non-empty for a version to be usable.
_REQUIRED_FOR_VERSION: dict[str, list[str]] = {
    "fp-v1": [
        "trading_date", "alert_timestamp", "alert_type", "symbol",
        "alert_count", "volume",
    ],
    "fp-v1-lite": ["trading_date", "alert_timestamp", "alert_type", "symbol"],
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
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return repr(round(value, 6))
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
    return VERSION_PREFERENCE[-1]


def compute_fingerprint(fields: dict[str, Any], version: str | None = None) -> Fingerprint:
    """Compute a stable fingerprint for one alert.

    ``fields`` uses canonical keys (see FINGERPRINT_FIELDS).  If ``version`` is
    not given, the best usable version is chosen automatically.
    """
    chosen = version or choose_version(fields)
    if chosen not in FINGERPRINT_FIELDS:
        raise ValueError(f"Unknown fingerprint version {chosen!r}")
    parts = [chosen]
    for key in FINGERPRINT_FIELDS[chosen]:
        parts.append(f"{key}={_canonical(fields.get(key))}")
    payload = "\x1f".join(parts).encode("utf-8")
    return Fingerprint(value=hashlib.sha256(payload).hexdigest(), version=chosen)


def page_fingerprint(event_fingerprints: list[str]) -> str:
    """Order-independent SHA-256 over a page's alert fingerprints.

    Used to detect a repeated page (identical alert set) during collection.
    """
    h = hashlib.sha256()
    for fp in sorted(event_fingerprints):
        h.update(fp.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()
