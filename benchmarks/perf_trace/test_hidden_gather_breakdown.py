#!/usr/bin/env python3
"""Tests for Task 15.1b hidden gather breakdown."""

from __future__ import annotations

import unittest

from perf_trace.hidden_gather_breakdown import build_hidden_gather_breakdown


def _span(event: str, dur_us: int, *, wave: int = 1, phase: str = "decode", stage: str = "entry") -> dict:
    return {
        "kind": "span",
        "event": event,
        "stage": stage,
        "phase": phase,
        "WaveID": wave,
        "token_idx": wave,
        "trace_id": "trace-000001",
        "ts_us": 1_000_000 + wave * 100_000,
        "dur_us": dur_us,
    }


class HiddenGatherBreakdownTest(unittest.TestCase):
    def test_decomposition(self) -> None:
        events = [
            _span("GGML_GRAPH_EXECUTE", 8_000, wave=1, stage="entry"),
            _span("EMBD_D2H_GET_ASYNC", 5, wave=1, stage="entry"),
            _span("ENTRY_COMPUTE_END", 9_000, wave=1, stage="entry"),
            _span("LLAMA_BACKEND_SYNCHRONIZE", 4_800, wave=1, stage="entry"),
            _span("LLAMA_GET_EMBEDDINGS_ACCESS", 10, wave=1, stage="entry"),
            _span("GATHER_END", 4_820, wave=1, stage="entry"),
        ]
        doc = build_hidden_gather_breakdown(events, trace_id="trace-000001")
        self.assertEqual(doc["status"], "PASS")
        self.assertAlmostEqual(doc["decomposition"]["gather_total_ms"], 4.82, places=2)
        self.assertAlmostEqual(doc["decomposition"]["gpu_wait_backend_synchronize_ms"], 4.8, places=2)
        self.assertTrue(doc["acceptance"]["prediction_gpu_sync_dominates"])


if __name__ == "__main__":
    unittest.main()
