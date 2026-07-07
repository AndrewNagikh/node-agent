#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

from perf_trace.ttft import merge_ttft_trace


class TtftMergeTest(unittest.TestCase):
    def test_merge_ttft_events(self) -> None:
        ev = {
            "kind": "instant",
            "trace_id": "trace-000001",
            "phase": "ttft",
            "event": "CLIENT_TTFT",
            "attrs": {"token_id": 42, "prefill_ms": 120.5},
        }
        span = {
            "kind": "span",
            "trace_id": "trace-000001",
            "phase": "ttft",
            "event": "TTFT_PREFILL",
            "stage": "entry",
            "category": "TTFT",
            "dur_us": 120500,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sample.jsonl"
            path.write_text(json.dumps(ev) + "\n" + json.dumps(span) + "\n", encoding="utf-8")
            doc = merge_ttft_trace(root)
            self.assertEqual(doc["event_count"], 2)
            self.assertEqual(doc["summary"]["client_ttft_ms"], 120.5)


if __name__ == "__main__":
    unittest.main()
