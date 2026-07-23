"""Tests: file layout, filename generation, collision handling, file settle."""

from __future__ import annotations

import os
from datetime import date

from hdb.paths import (
    collision_safe_path,
    make_session_run,
    sha256_file,
    wait_for_file_settled,
)

D = date(2026, 2, 2)


def test_run_dir_layout(tmp_path):
    dirs = make_session_run(str(tmp_path / "exp"), D, "HPRE", run_id="run_X")
    assert dirs.run_dir.endswith(os.path.join("2026", "2026-02-02", "HPRE", "run_X"))
    assert dirs.part_filename(1) == "HPRE_2026-02-02_part_001.csv"
    assert dirs.part_filename(42) == "HPRE_2026-02-02_part_042.csv"
    dirs.ensure()
    assert os.path.isdir(dirs.run_dir)


def test_collision_safe_path(tmp_path):
    p = tmp_path / "a.csv"
    assert collision_safe_path(str(p)) == str(p)
    p.write_text("x")
    alt = collision_safe_path(str(p))
    assert alt != str(p)
    assert "collision" in alt
    assert not os.path.exists(alt)


def test_sha256_file(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes(b"hello")
    import hashlib

    assert sha256_file(str(p)) == hashlib.sha256(b"hello").hexdigest()


def test_wait_for_file_settled(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("content")
    assert wait_for_file_settled(str(p), settle_seconds=3, poll_interval=0.1) is True


def test_wait_for_missing_file_times_out(tmp_path):
    p = tmp_path / "missing.csv"
    assert wait_for_file_settled(str(p), settle_seconds=1, poll_interval=0.1) is False
