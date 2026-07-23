"""Immutable, run-specific export directory layout + safe file helpers.

Layout::

    historical_exports/2026/2026-02-02/HPRE/run_YYYYMMDD_HHMMSS/
        HPRE_2026-02-02_part_001.csv
        manifest.json

Raw exports are never overwritten or deleted automatically.  Filenames are
generated uniquely; if a target already exists, a collision-safe variant is
produced instead of overwriting.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from datetime import date, datetime


def new_run_id(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return "run_" + now.strftime("%Y%m%d_%H%M%S")


@dataclass(frozen=True)
class SessionRunDirs:
    exports_root: str
    trading_date: date
    session: str
    run_id: str

    @property
    def year_dir(self) -> str:
        return os.path.join(self.exports_root, str(self.trading_date.year))

    @property
    def date_dir(self) -> str:
        return os.path.join(self.year_dir, self.trading_date.isoformat())

    @property
    def session_dir(self) -> str:
        return os.path.join(self.date_dir, self.session)

    @property
    def run_dir(self) -> str:
        return os.path.join(self.session_dir, self.run_id)

    @property
    def manifest_path(self) -> str:
        return os.path.join(self.run_dir, "manifest.json")

    def ensure(self) -> "SessionRunDirs":
        os.makedirs(self.run_dir, exist_ok=True)
        return self

    def part_filename(self, part_number: int) -> str:
        return f"{self.session}_{self.trading_date.isoformat()}_part_{part_number:03d}.csv"

    def part_path(self, part_number: int) -> str:
        return os.path.join(self.run_dir, self.part_filename(part_number))


def make_session_run(
    exports_root: str,
    trading_date: date,
    session: str,
    run_id: str | None = None,
) -> SessionRunDirs:
    return SessionRunDirs(
        exports_root=exports_root,
        trading_date=trading_date,
        session=session,
        run_id=run_id or new_run_id(),
    )


def collision_safe_path(desired_path: str) -> str:
    """Return ``desired_path`` if free, else a unique sibling path.

    Never overwrites.  Used after an unexpected on-disk collision so a raw
    export is preserved.
    """
    if not os.path.exists(desired_path):
        return desired_path
    root, ext = os.path.splitext(desired_path)
    stamp = datetime.now().strftime("%H%M%S")
    counter = 1
    while True:
        candidate = f"{root}__collision_{stamp}_{counter}{ext}"
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def sha256_file(path: str, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def wait_for_file_settled(
    path: str,
    settle_seconds: float = 10.0,
    poll_interval: float = 0.5,
    stable_reads: int = 3,
) -> bool:
    """Wait until ``path`` exists, is non-empty, and its size stops changing.

    Returns True when settled, False on timeout.
    """
    deadline = time.monotonic() + settle_seconds
    last_size = -1
    stable = 0
    while time.monotonic() < deadline:
        if os.path.exists(path):
            size = os.path.getsize(path)
            if size > 0 and size == last_size:
                stable += 1
                if stable >= stable_reads:
                    return True
            else:
                stable = 0
            last_size = size
        time.sleep(poll_interval)
    # Final check.
    return (
        os.path.exists(path)
        and os.path.getsize(path) > 0
        and stable >= 1
    )
