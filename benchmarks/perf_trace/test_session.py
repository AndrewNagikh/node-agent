#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

from perf_trace.session import merge_session_trace


class SessionMergeTest(unittest.TestCase):
    def test_merge_session_events(self) -> None:
        ev = {
            "kind": "span",
            "trace_id": "session-sess123",
            "phase": "session_create",
            "event": "SESSION_CONFIGURE_NODE",
            "node_id": "node-a",
            "dur_us": 230000,
            "attrs": {"role": "entry"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.jsonl").write_text(json.dumps(ev) + "\n", encoding="utf-8")
            doc = merge_session_trace(root)
            self.assertEqual(doc["span_count"], 1)
            self.assertIn("SESSION_CONFIGURE_NODE", doc["breakdown"]["event_us"])


if __name__ == "__main__":
    unittest.main()
