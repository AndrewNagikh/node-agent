#!/usr/bin/env python3
from __future__ import annotations

import unittest

from perf_trace.bottleneck import (
    budget_status,
    evaluate_budgets,
    rollup_decode_buckets,
    token_decode_metrics,
)


class BottleneckTest(unittest.TestCase):
    def test_budget_status_lte(self) -> None:
        self.assertEqual(budget_status(0.5, 1.0, "lte"), "PASS")
        self.assertEqual(budget_status(1.5, 1.0, "lte"), "WARN")
        self.assertEqual(budget_status(3.0, 1.0, "lte"), "FAIL")

    def test_budget_status_gte(self) -> None:
        self.assertEqual(budget_status(95.0, 90.0, "gte"), "PASS")
        self.assertEqual(budget_status(50.0, 90.0, "gte"), "WARN")

    def test_token_decode_metrics(self) -> None:
        tokens = [{
            "total_ms": 10.0,
            "entry_compute_ms": 3.0,
            "middle_compute_ms": 2.0,
            "final_compute_ms": 1.0,
            "network_ms": 2.0,
            "serialize_ms": 1.0,
        }]
        m = token_decode_metrics(tokens)
        self.assertEqual(m["decode_ms_per_token"], 10.0)
        self.assertEqual(m["pipeline_utilization_pct"], 60.0)

    def test_evaluate_budgets(self) -> None:
        rows = evaluate_budgets({
            "ttft_ms": 1500.0,
            "unknown_pct": 3.0,
            "pipeline_utilization_pct": 50.0,
        })
        by_metric = {r["metric"]: r["status"] for r in rows}
        self.assertEqual(by_metric["ttft_ms"], "PASS")
        self.assertEqual(by_metric["unknown_pct"], "PASS")
        self.assertEqual(by_metric["pipeline_utilization_pct"], "WARN")

    def test_rollup_decode_buckets(self) -> None:
        rollup = rollup_decode_buckets(
            {"category_pct": {"COMPUTE": 60.0, "NETWORK": 10.0, "UNKNOWN": 2.0}, "unknown_pct": 2.0},
            {"event_us": {"SCHED_QUEUE_WAIT": 1000000}},
            [{"total_ms": 10.0, "entry_compute_ms": 6.0, "middle_compute_ms": 0.0, "final_compute_ms": 0.0}],
        )
        self.assertIn("compute", rollup["buckets_pct"])


if __name__ == "__main__":
    unittest.main()
