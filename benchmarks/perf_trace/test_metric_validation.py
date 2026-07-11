#!/usr/bin/env python3
"""Tests for Task 13.1 metric validation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from perf_trace.metric_validation import (
    build_token_chain,
    compute_ceiling_tps,
    compute_tps_from_timing,
    cross_check_tps_vs_ceiling,
    run_metric_validation,
    stage_span_coverage,
    write_validation,
)


def _ev(
        event: str,
        stage: str,
        *,
        phase: str = "decode",
        wave: int = 1,
        trace_id: str = "trace-000001",
        dur_us: int = 0,
        ts_us: int = 0,
) -> dict:
    return {
        "kind": "span" if dur_us else "instant",
        "event": event,
        "stage": stage,
        "phase": phase,
        "WaveID": wave,
        "token_idx": wave,
        "trace_id": trace_id,
        "dur_us": dur_us,
        "ts_us": ts_us,
    }


def _full_decode_chain(wave: int = 1, ts_base: int = 1_000_000) -> list[dict]:
    t = ts_base + wave * 100_000
    return [
        _ev("ENTRY_RECEIVE", "entry", wave=wave, ts_us=t),
        _ev("ENTRY_COMPUTE_BEGIN", "entry", wave=wave, ts_us=t + 1000),
        _ev("ENTRY_COMPUTE_END", "entry", wave=wave, dur_us=5000, ts_us=t + 2000),
        _ev("ENTRY_SEND_END", "entry", wave=wave, dur_us=1000, ts_us=t + 8000),
        _ev("MIDDLE_RECEIVE", "middle", wave=wave, ts_us=t + 10000),
        _ev("MIDDLE_COMPUTE_BEGIN", "middle", wave=wave, ts_us=t + 11000),
        _ev("MIDDLE_COMPUTE_END", "middle", wave=wave, dur_us=4000, ts_us=t + 12000),
        _ev("MIDDLE_SEND_END", "middle", wave=wave, dur_us=1000, ts_us=t + 17000),
        _ev("FINAL_RECEIVE", "final", wave=wave, ts_us=t + 20000),
        _ev("FINAL_COMPUTE_BEGIN", "final", wave=wave, ts_us=t + 21000),
        _ev("FINAL_COMPUTE_END", "final", wave=wave, dur_us=6000, ts_us=t + 22000),
        _ev("SAMPLER_END", "final", wave=wave, dur_us=500, ts_us=t + 29000),
    ]


class MetricValidationTest(unittest.TestCase):
    def test_stage_coverage_pass(self) -> None:
        events = _full_decode_chain(1)
        doc = stage_span_coverage(events, "entry")
        self.assertEqual(doc["status"], "PASS")

    def test_stage_coverage_fail_mislabeled(self) -> None:
        events = [_ev("MIDDLE_RECEIVE", "middle", phase="session_create", wave=1)]
        doc = stage_span_coverage(events, "middle")
        self.assertEqual(doc["status"], "FAIL")
        self.assertIn("missing decode spans", doc["reason"] or "")

    def test_tps_source_of_truth(self) -> None:
        doc = compute_tps_from_timing({"decode_ms": 100.0, "generated_tokens": 10})
        self.assertEqual(doc["status"], "PASS")
        self.assertEqual(doc["value"], 100.0)

    def test_tps_invalid_cross_check(self) -> None:
        tps = compute_tps_from_timing({"decode_ms": 50.0, "generated_tokens": 10})
        ceiling = {"status": "PASS", "value": 5.0}
        cross = cross_check_tps_vs_ceiling(tps, ceiling)
        self.assertEqual(cross["status"], "INVALID")

    def test_bubble_unknown_when_middle_missing(self) -> None:
        events = _full_decode_chain(1) + _full_decode_chain(2)
        # entry-only decode on middle
        events = [e for e in events if e.get("stage") != "middle" or e.get("stage") == "middle"]
        events = [e for e in _full_decode_chain(1) if e.get("stage") == "entry"]
        events += [e for e in _full_decode_chain(2) if e.get("stage") == "entry"]
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            path = raw / "node-a_trace.jsonl"
            path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
            analysis = Path(tmp) / "analysis"
            doc = run_metric_validation(
                raw,
                analysis,
                trace_id="trace-000001",
                generate_timing={"decode_ms": 100, "generated_tokens": 8, "trace_id": "trace-000001"},
            )
            self.assertEqual(doc["metrics"]["bubble"]["status"], "UNKNOWN")
            self.assertIn("missing", doc["metrics"]["bubble"]["reason"] or "")

    def test_full_chain_pass(self) -> None:
        events = _full_decode_chain(17)
        chain = build_token_chain(events, 17)
        self.assertTrue(chain["complete"])

    def test_write_validation_on_homelab_like_partial(self) -> None:
        """Simulates homelab: entry decode + middle/final session_create only."""
        entry = [e for e in _full_decode_chain(1) if e.get("stage") == "entry"]
        middle_sc = [
            _ev("MIDDLE_RECEIVE", "middle", phase="session_create", wave=1, ts_us=5000),
            _ev("MIDDLE_COMPUTE_END", "middle", phase="session_create", wave=1, dur_us=4000),
            _ev("MIDDLE_SEND_END", "middle", phase="session_create", wave=1, dur_us=1000),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            (raw / "node-a.jsonl").write_text(
                "\n".join(json.dumps(e) for e in entry) + "\n", encoding="utf-8")
            (raw / "node-c.jsonl").write_text(
                "\n".join(json.dumps(e) for e in middle_sc) + "\n", encoding="utf-8")
            analysis = Path(tmp) / "analysis"
            analysis.mkdir()
            doc = write_validation(
                raw,
                analysis,
                generate_timing={"decode_ms": 62.5, "generated_tokens": 16, "trace_id": "trace-000001"},
            )
            self.assertEqual(doc["checks"][1]["name"], "decode_trace_middle")
            self.assertEqual(doc["checks"][1]["status"], "FAIL")
            self.assertEqual(doc["metrics"]["bubble"]["status"], "UNKNOWN")
            self.assertTrue((analysis / "validation.json").is_file())

    def test_ceiling_from_critical_path(self) -> None:
        crit = {"avg_wall_critical_path_ms": 25.0}
        doc = compute_ceiling_tps(crit)
        self.assertEqual(doc["status"], "PASS")
        self.assertEqual(doc["value"], 40.0)


if __name__ == "__main__":
    unittest.main()
