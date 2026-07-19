"""Tests for perf_trace.collect freshness filtering."""

from __future__ import annotations

import os
import time
from pathlib import Path

from perf_trace.collect import collect_local_dir, collect_traces


def _write_jsonl(path: Path, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"event":"X"}\n', encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_collect_local_dir_skips_stale_files(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    now = time.time()
    _write_jsonl(src / "fresh.jsonl", mtime=now)
    _write_jsonl(src / "stale.jsonl", mtime=now - 7 * 24 * 3600)

    found = collect_local_dir(src, dest, min_mtime_unix=now - 3600)

    assert found == 1
    assert (dest / "fresh.jsonl").is_file()
    assert not (dest / "stale.jsonl").exists()


def test_collect_local_dir_no_filter_keeps_all(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    now = time.time()
    _write_jsonl(src / "fresh.jsonl", mtime=now)
    _write_jsonl(src / "stale.jsonl", mtime=now - 7 * 24 * 3600)

    assert collect_local_dir(src, dest) == 2


def test_collect_traces_local_override_applies_filter(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    now = time.time()
    _write_jsonl(src / "fresh.jsonl", mtime=now)
    _write_jsonl(src / "stale.jsonl", mtime=now - 7 * 24 * 3600)
    monkeypatch.setenv("PERF_TRACE_LOCAL_DIR", str(src))

    found = collect_traces(dest, min_mtime_unix=now - 3600)

    assert found == 1
