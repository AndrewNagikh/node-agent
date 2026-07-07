#!/usr/bin/env python3
from __future__ import annotations

import unittest

from perf_trace.ggml import load_ggml_events, summarize


class GgmlMergeTest(unittest.TestCase):
    def test_summarize_ggml_spans(self) -> None:
        events = [
            {
                "kind": "span",
                "event": "GGML_GRAPH_EXECUTE",
                "stage": "entry",
                "dur_us": 5000,
            },
            {
                "kind": "span",
                "event": "SCHED_QUEUE_WAIT",
                "stage": "middle",
                "dur_us": 1000,
            },
        ]
        summary = summarize(events)
        self.assertEqual(summary["span_count"], 2)
        self.assertIn("GGML_GRAPH_EXECUTE", summary["event_counts"])


if __name__ == "__main__":
    unittest.main()
