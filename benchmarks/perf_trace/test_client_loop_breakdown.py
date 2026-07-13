#!/usr/bin/env python3
"""Tests for Task 17.1A client decode-loop breakdown."""

from __future__ import annotations

import unittest

from perf_trace.client_loop_breakdown import build_client_loop_breakdown


def _span(event: str, dur_us: int, *, wave: int, ts_us: int,
          phase: str = "decode", stage: str = "client", node_id: str = "node-a") -> dict:
    return {
        "kind": "span",
        "event": event,
        "stage": stage,
        "phase": phase,
        "WaveID": wave,
        "token_idx": wave,
        "trace_id": "trace-000001",
        "node_id": node_id,
        "ts_us": ts_us,
        "dur_us": dur_us,
    }


def _flags(ts_us: int) -> dict:
    return {
        "kind": "instant",
        "event": "RUNTIME_FLAGS",
        "stage": "client",
        "phase": "decode",
        "trace_id": "trace-000001",
        "ts_us": ts_us,
        "attrs": {
            "protocol": 2,
            "entry_queue": True,
            "stage_queue": True,
            "client_pipeline": True,
            "external_embedding": False,
        },
    }


def _make_wave(wave: int, base_us: int) -> list[dict]:
    # token arrives at base+27000 (27 ms pipeline), then send/ack/complete.
    return [
        _span("CLIENT_TOKEN_WAIT_END", 27_000, wave=wave, ts_us=base_us),
        _span("CLIENT_SEND_END", 300, wave=wave + 1, ts_us=base_us + 27_100),
        _span("CLIENT_ACK_WAIT_END", 2_000, wave=wave + 1, ts_us=base_us + 27_500),
        _span("CLIENT_COMPLETE_WAIT_END", 6_000, wave=wave, ts_us=base_us + 29_600),
    ]


class ClientLoopBreakdownTest(unittest.TestCase):
    def test_attribution(self) -> None:
        events = [_flags(900_000)]
        period_us = 38_000
        for wave in range(1, 8):
            events.extend(_make_wave(wave, 1_000_000 + wave * period_us))
        doc = build_client_loop_breakdown(events, trace_id="trace-000001")

        self.assertEqual(doc["status"], "PASS")
        self.assertEqual(doc["runtime_flags"]["protocol"], 2)
        self.assertTrue(doc["runtime_flags"]["client_pipeline"])
        # Period is reconstructed from consecutive token arrivals: 38 ms.
        self.assertAlmostEqual(doc["avg_period_ms"], 38.0, delta=0.2)
        self.assertAlmostEqual(doc["stages"]["token_wait"]["avg_ms"], 27.0, delta=0.1)
        self.assertAlmostEqual(doc["stages"]["complete_wait"]["avg_ms"], 6.0, delta=0.1)
        # 27 + 6 + 2 + 0.3 = 35.3 of 38 ms -> ~93% attributed.
        self.assertTrue(doc["attribution_gate_90pct"], doc["attribution_pct_of_period"])

    def test_missing_client_spans_is_unknown(self) -> None:
        events = [
            _span("ENTRY_COMPUTE_END", 9_000, wave=3, ts_us=1_000_000, stage="entry"),
        ]
        doc = build_client_loop_breakdown(events, trace_id="trace-000001")
        self.assertEqual(doc["status"], "UNKNOWN")
        self.assertIsNone(doc["runtime_flags"])

    def test_shared_volume_duplicate_files_are_deduped(self) -> None:
        # perf_trace containers share one Docker volume; collecting raw/*.jsonl
        # from every container copies identical events N times. attribution
        # must not exceed ~100% of period after dedup.
        events = [_flags(900_000)]
        period_us = 38_000
        for wave in range(1, 8):
            events.extend(_make_wave(wave, 1_000_000 + wave * period_us))
        quadrupled = events * 4  # simulate 4 containers copying the same volume
        doc = build_client_loop_breakdown(quadrupled, trace_id="trace-000001")

        self.assertEqual(doc["status"], "PASS")
        self.assertLessEqual(doc["attribution_pct_of_period"], 100.5)
        self.assertAlmostEqual(doc["stages"]["token_wait"]["avg_ms"], 27.0, delta=0.1)

    def test_warmup_and_measured_calls_under_same_trace_id_dont_collide(self) -> None:
        # Two /pipeline/generate calls (warmup, then measured) share trace_id
        # and each reset token_idx to 0 -- RUNTIME_FLAGS segmentation must
        # keep them apart so a wave's stats don't sum both calls together.
        events = []
        # warmup call: flags at t=100_000, waves with a different (worse) period
        events.append(_flags(100_000))
        warmup_period_us = 90_000
        for wave in range(1, 5):
            events.extend(_make_wave(wave, 200_000 + wave * warmup_period_us))
        # measured call: flags well after warmup finishes, clean 38ms period
        events.append(_flags(5_000_000))
        measured_period_us = 38_000
        for wave in range(1, 8):
            events.extend(_make_wave(wave, 5_100_000 + wave * measured_period_us))

        doc = build_client_loop_breakdown(events, trace_id="trace-000001")
        self.assertEqual(doc["status"], "PASS")
        self.assertEqual(doc["generate_call_count"], 2)
        # Must reflect the measured call's period (38ms), not a mix with warmup.
        self.assertAlmostEqual(doc["avg_period_ms"], 38.0, delta=0.5)
        self.assertLessEqual(doc["attribution_pct_of_period"], 100.5)

    def test_blocking_path(self) -> None:
        events = []
        for wave in range(1, 7):
            base = 1_000_000 + wave * 54_000
            events.append(_span("CLIENT_BLOCKING_RT_END", 50_000, wave=wave, ts_us=base))
        doc = build_client_loop_breakdown(events, trace_id="trace-000001")
        self.assertEqual(doc["status"], "PASS")
        self.assertAlmostEqual(doc["stages"]["blocking_rt"]["avg_ms"], 50.0, delta=0.1)
        self.assertAlmostEqual(doc["avg_period_ms"], 54.0, delta=0.2)


if __name__ == "__main__":
    unittest.main()
