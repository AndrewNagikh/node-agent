#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from perf_trace.html_timeline import (
    build_timeline_document,
    decode_token_bars,
    downsample_gpu,
    render_html,
    ttft_stage_bars,
    write_timeline,
)


class HtmlTimelineTest(unittest.TestCase):
    def test_ttft_stage_bars(self) -> None:
        bars = ttft_stage_bars({
            "summary": {
                "stage_us": {"entry": 1000000, "middle": 500000, "final": 250000},
                "client_ttft_ms": 12.5,
            },
        })
        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[0]["stage"], "entry")

    def test_decode_token_bars(self) -> None:
        rows = decode_token_bars([{
            "token": "0",
            "total_ms": "10",
            "entry_compute_ms": "4",
            "middle_compute_ms": "3",
            "final_compute_ms": "2",
            "network_ms": "1",
            "trace_id": "trace-000001",
        }])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["token"], 0)

    def test_downsample_gpu(self) -> None:
        samples = [
            {"phase": "decode", "ts_us": "1000", "gpu_util_pct": "10", "node_id": "node-a"},
            {"phase": "decode", "ts_us": "2000", "gpu_util_pct": "20", "node_id": "node-a"},
            {"phase": "install", "ts_us": "3000", "gpu_util_pct": "99", "node_id": "node-a"},
        ]
        pts = downsample_gpu(samples, max_points=10)
        self.assertEqual(len(pts), 2)

    def test_render_and_write(self) -> None:
        doc = {
            "meta": {"analysis_dir": "/tmp/test"},
            "budget": [{"label": "TTFT", "value": 10, "target": 2000, "unit": "ms", "status": "PASS"}],
            "ttft_bars": [{"stage": "entry", "ms": 5.0, "color": "#000"}],
            "decode_rows": [{
                "token": 0,
                "total_ms": 10.0,
                "segments": [{"name": "entry", "ms": 10.0, "color": "#000"}],
            }],
            "gpu_points": [{"t_ms": 0.0, "util_pct": 5.0}],
            "buckets_pct": {"compute": 50.0},
        }
        html = render_html(doc)
        self.assertIn("Task 12 Performance Timeline", html)
        self.assertIn("token 0", html)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "budget.json").write_text('{"metrics": {}, "budget": []}', encoding="utf-8")
            (root / "tokens.csv").write_text(
                "token,total_ms,entry_compute_ms,middle_compute_ms,final_compute_ms,network_ms,trace_id\n"
                "0,10,4,3,2,1,trace-1\n",
                encoding="utf-8",
            )
            (root / "gpu.csv").write_text(
                "phase,ts_us,gpu_util_pct,node_id\n"
                "decode,1000,10,node-a\n",
                encoding="utf-8",
            )
            out = write_timeline(root)
            self.assertTrue(Path(out["timeline_html"]).is_file())


if __name__ == "__main__":
    unittest.main()
