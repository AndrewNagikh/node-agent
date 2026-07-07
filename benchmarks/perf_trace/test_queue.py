#!/usr/bin/env python3
from __future__ import annotations

import unittest

from perf_trace.queue import queue_rows, queue_summary


class PerfTraceQueueTest(unittest.TestCase):
    def test_queue_rows_and_summary(self) -> None:
        events = [
            {
                "phase": "decode",
                "event": "QUEUE_DEPTH",
                "token_idx": 0,
                "stage": "entry",
                "attrs": {"depth": 0},
            },
            {
                "phase": "decode",
                "event": "QUEUE_DEPTH",
                "token_idx": 0,
                "stage": "middle",
                "attrs": {"depth": 0},
            },
            {
                "phase": "decode",
                "event": "QUEUE_DEPTH",
                "token_idx": 1,
                "stage": "middle",
                "attrs": {"depth": 1},
            },
        ]
        rows = queue_rows(events)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["middle_queue_depth"], 0)
        self.assertEqual(rows[1]["middle_queue_depth"], 1)
        summary = queue_summary(rows)
        self.assertEqual(summary["middle"]["max"], 1)
        self.assertEqual(summary["middle"]["pattern"], "0,1")


if __name__ == "__main__":
    unittest.main()
