#!/usr/bin/env python3
"""Tests for Task 14 observability artifacts."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from perf_trace.observability import (
    build_unified_timeline,
    write_observability_artifacts,
)
from perf_trace.test_metric_validation import _full_decode_chain


class ObservabilityTest(unittest.TestCase):
    def test_unified_timeline_sort(self) -> None:
        events = _full_decode_chain(1) + _full_decode_chain(2)
        doc = build_unified_timeline(events, trace_id="trace-000001")
        waves = [e["WaveID"] for e in doc["events"]]
        self.assertEqual(waves, sorted(waves))
        self.assertEqual(doc["wave_count"], 2)

    def test_write_artifacts_full_chain(self) -> None:
        events = _full_decode_chain(17) + _full_decode_chain(18)
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            (raw / "all.jsonl").write_text(
                "\n".join(json.dumps(e) for e in events) + "\n",
                encoding="utf-8",
            )
            analysis = Path(tmp) / "analysis"
            doc = write_observability_artifacts(
                raw, analysis, trace_id="trace-000001", all_stages_pass=True
            )
            for name in (
                "timeline.json",
                "critical_path.json",
                "bubble.json",
                "utilization.json",
                "serialization.json",
                "network.json",
                "scheduler.json",
            ):
                self.assertTrue((analysis / name).is_file(), name)
            self.assertIsNotNone(
                doc["critical_path"].get("avg_wall_critical_path_ms")
                or doc["critical_path"].get("avg_serial_critical_path_ms")
            )
            chain = doc["timeline"]["token_chains"].get("17")
            self.assertIsNotNone(chain)
            self.assertGreaterEqual(len(chain["steps"]), 8)

    def test_bubble_unknown_when_partial(self) -> None:
        events = [e for e in _full_decode_chain(1) if e.get("stage") == "entry"]
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            (raw / "entry.jsonl").write_text(
                "\n".join(json.dumps(e) for e in events) + "\n",
                encoding="utf-8",
            )
            analysis = Path(tmp) / "analysis"
            doc = write_observability_artifacts(raw, analysis, all_stages_pass=False)
            self.assertEqual(doc["bubble"]["status"], "UNKNOWN")
            self.assertEqual(doc["utilization"]["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
