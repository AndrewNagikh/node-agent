#!/usr/bin/env python3
"""Tests for Task 15.1 hidden transport breakdown."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from perf_trace.hidden_transport_breakdown import (
    build_hidden_transport_breakdown,
    write_hidden_transport_breakdown,
)


def _span(
        event: str,
        dur_us: int,
        *,
        wave: int = 1,
        trace_id: str = "trace-000001",
        attrs: dict | None = None,
) -> dict:
    row = {
        "kind": "span",
        "event": event,
        "stage": "entry",
        "phase": "decode",
        "category": "SERIALIZATION",
        "WaveID": wave,
        "token_idx": wave,
        "trace_id": trace_id,
        "dur_us": dur_us,
    }
    if attrs:
        row["attrs"] = attrs
    return row


def _instant(event: str, attrs: dict, *, wave: int = 1, trace_id: str = "trace-000001") -> dict:
    return {
        "kind": "instant",
        "event": event,
        "stage": "entry",
        "phase": "decode",
        "WaveID": wave,
        "token_idx": wave,
        "trace_id": trace_id,
        "attrs": attrs,
    }


def _sample_pack(wave: int = 1) -> list[dict]:
    alloc_attrs = {
        "op": "vector_resize",
        "capacity_before": 0,
        "capacity_after": 2048,
        "capacity_grew": True,
        "bytes_requested": 8192,
    }
    return [
        _span("ALLOC_END", 410, wave=wave, attrs=alloc_attrs),
        _span("GATHER_END", 2_730, wave=wave),
        _span("COPY_END", 1_180, wave=wave),
        _span("SERIALIZE_END", 0, wave=wave),
        _span("FRAME_END", 160, wave=wave),
        _span("SEND_END", 40, wave=wave),
        _span("HIDDEN_PACK_TOTAL_END", 4_520, wave=wave),
        _instant("HIDDEN_PACK_SUMMARY", {
            "heap_copy_count": 2,
            "copy_path": "ggml_embeddings->std::vector->kernel_tcp",
            "alloc_per_token": True,
            "vector_capacity_grew": True,
        }, wave=wave),
        _span("ALLOC_END", 390, wave=wave + 1, attrs=alloc_attrs),
        _span("GATHER_END", 2_500, wave=wave + 1),
        _span("COPY_END", 1_000, wave=wave + 1),
        _span("FRAME_END", 150, wave=wave + 1),
        _span("SEND_END", 35, wave=wave + 1),
        _span("HIDDEN_PACK_TOTAL_END", 4_075, wave=wave + 1),
    ]


class HiddenTransportBreakdownTest(unittest.TestCase):
    def test_stage_stats_and_answers(self) -> None:
        doc = build_hidden_transport_breakdown(_sample_pack(), trace_id="trace-000001")
        self.assertEqual(doc["status"], "PASS")
        self.assertAlmostEqual(doc["total_ms"], 4.298, places=2)
        stages = doc["stages"]
        self.assertAlmostEqual(stages["gather_hidden"]["avg_ms"], 2.615, places=2)
        self.assertEqual(doc["answers"]["most_expensive_operation"]["stage"], "gather_hidden")
        self.assertFalse(doc["serialization_stage_present"])
        self.assertTrue(doc["allocation_audit"]["alloc_per_token"])

    def test_write_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            raw.mkdir()
            (raw / "node-a.jsonl").write_text(
                "\n".join(json.dumps(e) for e in _sample_pack()) + "\n",
                encoding="utf-8",
            )
            analysis = Path(tmp) / "analysis"
            docs = Path(tmp) / "TASK_15_1.md"
            doc = write_hidden_transport_breakdown(
                raw, analysis, trace_id="trace-000001", docs_path=docs)
            self.assertTrue((analysis / "hidden_transport_breakdown.json").is_file())
            self.assertTrue((analysis / "hidden_transport_breakdown.csv").is_file())
            self.assertTrue(docs.is_file())
            self.assertIn("gather_hidden", doc["answers"]["most_expensive_operation"]["stage"])


if __name__ == "__main__":
    unittest.main()
