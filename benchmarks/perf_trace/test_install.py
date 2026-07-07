#!/usr/bin/env python3
"""Tests for install trace merge."""

import json
import tempfile
import unittest
from pathlib import Path

from perf_trace.install import merge_install_trace, reuse_summary


class InstallMergeTest(unittest.TestCase):
    def test_merge_install_events(self) -> None:
        ev = {
            "kind": "span",
            "trace_id": "install-job1-tinyllama",
            "phase": "install",
            "event": "INSTALL_BLOB",
            "category": "INSTALL_REUSE",
            "node_id": "node-a",
            "dur_us": 4200,
            "attrs": {"sub": "download", "blob_id": "layer:0", "bytes": 1024},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.jsonl").write_text(json.dumps(ev) + "\n", encoding="utf-8")
            doc = merge_install_trace(root)
            self.assertEqual(doc["event_count"], 1)
            self.assertEqual(doc["blob_operations"], 1)
            self.assertEqual(doc["reuse"]["operation_counts"]["download"], 1)

    def test_reuse_summary_full_reuse(self) -> None:
        events = [{
            "kind": "instant",
            "event": "INSTALL_FULL_REUSE",
            "attrs": {"sub": "reuse"},
        }]
        summary = reuse_summary(events)
        self.assertTrue(summary["full_reuse"])


if __name__ == "__main__":
    unittest.main()
