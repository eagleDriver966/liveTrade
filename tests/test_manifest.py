"""Tests: manifest read/write, part ordering, completion, atomic save."""

from __future__ import annotations

import os
from datetime import date

import pytest

from hdb.manifest import Manifest, PartEntry

D = date(2026, 2, 2)
B = date(2026, 2, 1)


def _entry(n, sha="abc"):
    return PartEntry(
        part_number=n,
        filename=f"HPRE_2026-02-02_part_{n:03d}.csv",
        sha256=sha,
        row_count=10,
        newest_timestamp="2026-02-02T09:29:00-05:00",
        oldest_timestamp="2026-02-02T04:00:00-05:00",
        first_symbol="AAA",
        last_symbol="ZZZ",
        page_fingerprint="pf",
        status="VALIDATED",
    )


def test_add_parts_and_next_number():
    m = Manifest.new("run_X", D, "HPRE", 0, B)
    assert m.next_part_number == 1
    m.add_part(_entry(1))
    m.add_part(_entry(2))
    assert m.next_part_number == 3
    assert [p.part_number for p in m.parts] == [1, 2]


def test_duplicate_part_rejected():
    m = Manifest.new("run_X", D, "HPRE", 0, B)
    m.add_part(_entry(1))
    with pytest.raises(ValueError):
        m.add_part(_entry(1))


def test_save_and_load_roundtrip(tmp_path):
    m = Manifest.new("run_X", D, "HPRE", 2, B)
    m.add_part(_entry(1))
    m.mark_complete("boundary_reached", ["boundary_reached", "no_backward_progress"], "VERIFIED")
    path = os.path.join(tmp_path, "manifest.json")
    m.save(path)
    loaded = Manifest.load(path)
    assert loaded.run_id == "run_X"
    assert loaded.session == "HPRE"
    assert loaded.status == "VERIFIED"
    assert loaded.completion_reason == "boundary_reached"
    assert len(loaded.parts) == 1
    assert loaded.parts[0].sha256 == "abc"


def test_atomic_save_no_temp_left(tmp_path):
    m = Manifest.new("run_X", D, "HPRE", 0, B)
    path = os.path.join(tmp_path, "manifest.json")
    m.save(path)
    assert not os.path.exists(path + ".tmp")
