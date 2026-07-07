#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from perf_trace.merge import aggregate_bottleneck, merge_trace_dir, token_rows


class PerfTraceMergeTest(unittest.TestCase):
    def test_token_rows_and_bottleneck(self) -> None:
        events = [
            {
                "kind": "span",
                "phase": "decode",
                "token_idx": 0,
                "stage": "entry",
                "category": "COMPUTE",
                "event": "ENTRY_COMPUTE_END",
                "dur_us": 7900,
                "trace_id": "trace-000001",
            },
            {
                "kind": "span",
                "phase": "decode",
                "token_idx": 0,
                "stage": "entry",
                "category": "NETWORK",
                "event": "HIDDEN_TRANSFER",
                "dur_us": 700,
                "attrs": {"payload_bytes": 8192, "link": "ab"},
            },
        ]
        rows = token_rows(events)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry_compute_ms"], 7.9)
        self.assertEqual(rows[0]["network_ms"], 0.7)
        bottleneck = aggregate_bottleneck(events)
        self.assertIn("COMPUTE", bottleneck["category_pct"])

    def test_queue_depth_instant(self) -> None:
        events = [
            {
                "kind": "instant",
                "phase": "decode",
                "token_idx": 0,
                "stage": "middle",
                "event": "QUEUE_DEPTH",
                "attrs": {"depth": 1},
            },
        ]
        rows = token_rows(events)
        self.assertEqual(rows[0]["middle_queue_depth"], 1)


if __name__ == "__main__":
    unittest.main()
