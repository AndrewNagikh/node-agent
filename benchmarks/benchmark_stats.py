"""Statistical aggregation for benchmark repeats."""

from __future__ import annotations

import math
from typing import Any


def _sorted_nums(values: list[float]) -> list[float]:
    return sorted(v for v in values if isinstance(v, (int, float)))


def percentile(values: list[float], p: float) -> float | None:
    nums = _sorted_nums(values)
    if not nums:
        return None
    if len(nums) == 1:
        return nums[0]
    k = (len(nums) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return nums[int(k)]
    return nums[f] * (c - k) + nums[c] * (k - f)


def summarize(values: list[float]) -> dict[str, Any]:
    nums = _sorted_nums(values)
    if not nums:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "stddev": None,
            "min": None,
            "max": None,
            "p95": None,
        }
    n = len(nums)
    mean = sum(nums) / n
    median = nums[n // 2] if n % 2 else (nums[n // 2 - 1] + nums[n // 2]) / 2
    var = sum((x - mean) ** 2 for x in nums) / n if n > 1 else 0.0
    p95 = percentile(nums, 95)
    return {
        "count": n,
        "mean": round(mean, 4),
        "median": round(median, 4),
        "stddev": round(math.sqrt(var), 4),
        "min": round(nums[0], 4),
        "max": round(nums[-1], 4),
        "p95": round(p95, 4) if p95 is not None else None,
    }


def aggregate_repeats(repeats: list[dict[str, Any]], paths: list[tuple[str, ...]]) -> dict[str, Any]:
    """Aggregate numeric fields across repeats using dot-paths, e.g. ('ttft', 'total_ms')."""
    out: dict[str, Any] = {}
    for path in paths:
        key = ".".join(path)
        vals = []
        for rep in repeats:
            cur: Any = rep
            for p in path:
                if not isinstance(cur, dict):
                    cur = None
                    break
                cur = cur.get(p)
            if isinstance(cur, (int, float)):
                vals.append(float(cur))
        out[key] = summarize(vals)
    return out
