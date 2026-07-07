#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from perf_trace.postprocess import run_postprocess


class PostprocessTest(unittest.TestCase):
    def test_run_postprocess_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw"
            raw.mkdir()
            event = {
                "kind": "span",
                "event": "ENTRY_COMPUTE_END",
                "phase": "decode",
                "stage": "entry",
                "category": "COMPUTE",
                "token_idx": 0,
                "trace_id": "trace-000001",
                "dur_us": 1000,
                "ts_us": 1000,
            }
            (raw / "node-a_decode.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")

            out = run_postprocess(
                raw,
                root / "perf",
                profile="task12_docker",
                model="tinyllama",
                cluster_size=3,
                regression=False,
            )
            self.assertTrue((Path(out["analysis_dir"]) / "trace.json").is_file())
            self.assertTrue((Path(out["analysis_dir"]) / "timeline.html").is_file())


if __name__ == "__main__":
    unittest.main()
