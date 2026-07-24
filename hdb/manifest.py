"""Per-session-run ``manifest.json`` read/write.

The manifest is the on-disk source of truth for a single session run directory.
It lists every validated export part with its lineage and checksum, plus the
completion decision.  It is written atomically after each part is validated so a
crash never leaves a half-written manifest.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from .version import APP_VERSION, PARSER_VERSION

MANIFEST_VERSION = 1


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PartEntry:
    part_number: int
    filename: str
    sha256: str
    row_count: int
    newest_timestamp: str | None
    oldest_timestamp: str | None
    first_symbol: str | None
    last_symbol: str | None
    page_fingerprint: str | None
    status: str
    validated_at: str = field(default_factory=_utc_iso)
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass
class Manifest:
    manifest_version: int
    app_version: str
    parser_version: str
    run_id: str
    trading_date: str
    session: str
    panel_position: Any
    boundary_date: str
    created_at: str = field(default_factory=_utc_iso)
    updated_at: str = field(default_factory=_utc_iso)
    status: str = "COLLECTING"
    completion_reason: str | None = None
    completion_signals: list[str] = field(default_factory=list)
    parts: list[PartEntry] = field(default_factory=list)

    # -- construction --------------------------------------------------------
    @classmethod
    def new(
        cls,
        run_id: str,
        trading_date: date,
        session: str,
        panel_position: Any,
        boundary_date: date,
    ) -> "Manifest":
        return cls(
            manifest_version=MANIFEST_VERSION,
            app_version=APP_VERSION,
            parser_version=PARSER_VERSION,
            run_id=run_id,
            trading_date=trading_date.isoformat(),
            session=session,
            panel_position=panel_position,
            boundary_date=boundary_date.isoformat(),
        )

    # -- mutation ------------------------------------------------------------
    def add_part(self, entry: PartEntry) -> None:
        existing = {p.part_number for p in self.parts}
        if entry.part_number in existing:
            raise ValueError(f"Part {entry.part_number} already present in manifest")
        self.parts.append(entry)
        self.parts.sort(key=lambda p: p.part_number)
        self.updated_at = _utc_iso()

    @property
    def next_part_number(self) -> int:
        return (max((p.part_number for p in self.parts), default=0)) + 1

    def mark_complete(self, reason: str, signals: list[str], status: str) -> None:
        self.completion_reason = reason
        self.completion_signals = list(signals)
        self.status = status
        self.updated_at = _utc_iso()

    # -- serialization -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, default=str)
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, path: str) -> "Manifest":
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        parts = [PartEntry(**p) for p in data.pop("parts", [])]
        manifest = cls(**data)
        manifest.parts = parts
        return manifest
