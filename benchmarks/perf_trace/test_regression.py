#!/usr/bin/env python3
from __future__ import annotations

import unittest

from perf_trace.regression import (
    compare_snapshots,
    pct_change,
    regression_status,
    summarize_regression,
)


class RegressionTest(unittest.TestCase):
    def test_pct_change(self) -> None:
        self.assertEqual(pct_change(100.0, 110.0), 10.0)
        self.assertEqual(pct_change(100.0, 90.0), -10.0)

    def test_regression_status(self) -> None:
        self.assertEqual(regression_status(5.0, lower_is_better=True, warn_pct=10, fail_pct=25), "PASS")
        self.assertEqual(regression_status(15.0, lower_is_better=True, warn_pct=10, fail_pct=25), "WARN")
        self.assertEqual(regression_status(30.0, lower_is_better=True, warn_pct=10, fail_pct=25), "FAIL")
        self.assertEqual(regression_status(-10.0, lower_is_better=True, warn_pct=10, fail_pct=25), "IMPROVED")

    def test_compare_snapshots(self) -> None:
        rows = compare_snapshots(
            {"decode_ms_per_token": 100.0, "ttft_ms": 50.0},
            {"decode_ms_per_token": 118.0, "ttft_ms": 45.0},
            warn_pct=10,
            fail_pct=25,
        )
        by_metric = {r["metric"]: r["status"] for r in rows}
        self.assertEqual(by_metric["decode_ms_per_token"], "WARN")
        self.assertEqual(by_metric["ttft_ms"], "IMPROVED")

    def test_summarize_critical_fail(self) -> None:
        rows = compare_snapshots(
            {"decode_ms_per_token": 100.0},
            {"decode_ms_per_token": 130.0},
            warn_pct=10,
            fail_pct=25,
        )
        summary = summarize_regression(rows)
        self.assertTrue(summary["has_critical_fail"])


if __name__ == "__main__":
    unittest.main()
