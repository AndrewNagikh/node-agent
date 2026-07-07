#!/usr/bin/env python3
from __future__ import annotations

import unittest

from perf_trace.utilization import gpu_rows, load_gpu_events, summarize


class UtilizationTest(unittest.TestCase):
    def test_summarize_gpu_samples(self) -> None:
        events = [
            {
                "event": "GPU_SAMPLE",
                "node_id": "node-a",
                "phase": "decode",
                "attrs": {
                    "backend": "cpu",
                    "gpu_util_pct": 42.5,
                    "gpu_mem_used_mb": 0.0,
                    "cpu_busy_pct": 42.5,
                    "util_valid": False,
                },
            },
            {
                "event": "GPU_SAMPLE",
                "node_id": "node-a",
                "phase": "decode",
                "attrs": {
                    "backend": "cuda",
                    "gpu_util_pct": 80.0,
                    "gpu_mem_used_mb": 1024.0,
                    "cpu_busy_pct": 10.0,
                    "util_valid": True,
                },
            },
        ]
        rows = gpu_rows(events)
        self.assertEqual(len(rows), 2)
        summary = summarize(events)
        self.assertEqual(summary["sample_count"], 2)
        self.assertEqual(summary["backends"]["cpu"], 1)
        self.assertEqual(summary["backends"]["cuda"], 1)
        self.assertEqual(summary["by_node"]["node-a"]["max"], 80.0)


if __name__ == "__main__":
    unittest.main()
