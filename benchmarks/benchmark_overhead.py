"""Overhead and scaling analysis for Task 10.1."""

from __future__ import annotations

from typing import Any


def compute_overhead(mono: dict[str, Any] | None, dist: dict[str, Any]) -> dict[str, Any]:
    if not mono or not mono.get("aggregate"):
        return {"measurement_source": "unavailable", "note": "no monolithic baseline"}
    m = mono["aggregate"]
    d = dist.get("aggregate", {})
    mono_ttft = _mean(m, "ttft.total_ms")
    dist_ttft = _mean(d, "ttft.total_ms")
    mono_decode = _mean(m, "decode.ms_per_token")
    dist_decode = _mean(d, "decode.ms_per_token")
    mono_tps = _mean(m, "decode.tokens_per_sec")
    dist_tps = _mean(d, "decode.tokens_per_sec")
    return {
        "ttft_overhead_ms": _delta(dist_ttft, mono_ttft),
        "ttft_overhead_pct": _pct(dist_ttft, mono_ttft),
        "decode_overhead_ms_per_token": _delta(dist_decode, mono_decode),
        "decode_overhead_pct": _pct(dist_decode, mono_decode),
        "tps_delta": _delta(dist_tps, mono_tps, higher_better=True),
        "tps_overhead_pct": _pct(mono_tps, dist_tps, invert=True),
        "measurement_source": "derived",
    }


def compute_scaling_table(scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group by model_key, compute speedup/efficiency vs 1-node or mono."""
    by_model: dict[str, list[dict[str, Any]]] = {}
    for sc in scenarios:
        if sc.get("skipped") or sc.get("error"):
            continue
        by_model.setdefault(sc.get("model_key", ""), []).append(sc)

    rows: list[dict[str, Any]] = []
    for model_key, items in by_model.items():
        baseline_tps = None
        baseline_label = None
        for pref in ("mono", 1, "1"):
            for it in items:
                cs = it.get("cluster_size_target")
                if str(cs) == str(pref):
                    baseline_tps = _mean(it.get("aggregate", {}), "decode.tokens_per_sec")
                    baseline_label = str(pref)
                    break
            if baseline_tps:
                break
        if not baseline_tps:
            continue
        for it in sorted(items, key=lambda x: str(x.get("cluster_size_target", ""))):
            cs = it.get("cluster_size_target")
            tps = _mean(it.get("aggregate", {}), "decode.tokens_per_sec")
            if tps is None:
                continue
            n = _cluster_n(cs)
            speedup = round(tps / baseline_tps, 3) if baseline_tps else None
            efficiency = round(speedup / n, 3) if speedup and n and n > 0 else None
            rows.append({
                "model_key": model_key,
                "cluster_size": cs,
                "decode_tps": tps,
                "baseline": baseline_label,
                "baseline_tps": baseline_tps,
                "speedup": speedup,
                "efficiency": efficiency,
            })
    return rows


def build_comparison_table(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    """Mono vs N-node comparison for report."""
    cols: dict[str, dict[str, Any]] = {}
    for sc in scenarios:
        cs = str(sc.get("cluster_size_target", ""))
        agg = sc.get("aggregate", {})
        infra = sc.get("infrastructure", {})
        cols[cs] = {
            "ttft_ms": _mean(agg, "ttft.total_ms"),
            "decode_tps": _mean(agg, "decode.tokens_per_sec"),
            "prefill_tps": _mean(agg, "prefill.tokens_per_sec"),
            "load_ms": infra.get("session_create_ms") or _mean(agg, "load.total_ms"),
            "install_ms": infra.get("install_ms") or _mean(agg, "cold.install_ms"),
            "hidden_latency_ms": _mean(agg, "hidden.avg_hop_latency_ms"),
        }
    return cols


def estimate_hidden_hops(pipeline: list[dict[str, Any]]) -> dict[str, Any]:
    hops = max(len(pipeline) - 1, 0)
    return {
        "hop_count": hops,
        "pipeline": pipeline,
        "measurement_source": "derived",
        "note": "Per-hop bytes/latency require runtime tracing; hop count from pipeline layout",
    }


def estimate_decode_breakdown(pipeline: list[dict[str, Any]], total_ms: float) -> dict[str, Any]:
    """Proportional breakdown placeholder when runtime spans unavailable."""
    n = len(pipeline) or 1
    hops = max(n - 1, 0)
    compute_share = 0.88 / n
    net_share = 0.12 / max(hops, 1) if hops else 0
    parts: dict[str, float] = {}
    for i, seg in enumerate(pipeline):
        role = seg.get("role", f"seg{i}")
        parts[f"{role}_compute"] = round(100 * compute_share, 1)
    for h in range(hops):
        parts[f"network_hop_{h + 1}"] = round(100 * net_share, 1)
    parts["sampling"] = 4.0
    return {
        "percent": parts,
        "measurement_source": "estimated",
        "note": "Replace with runtime span export when available",
        "total_ms": total_ms,
    }


def _mean(agg: dict[str, Any], path: str) -> float | None:
    entry = agg.get(path, {})
    if isinstance(entry, dict):
        return entry.get("mean")
    return None


def _delta(a: float | None, b: float | None, higher_better: bool = False) -> float | None:
    if a is None or b is None:
        return None
    return round(a - b, 3)


def _pct(a: float | None, b: float | None, invert: bool = False) -> float | None:
    if a is None or b is None or b == 0:
        return None
    if invert:
        return round(100 * (b - a) / b, 2)
    return round(100 * (a - b) / b, 2)


def _cluster_n(cs: Any) -> float:
    if cs == "mono":
        return 1.0
    try:
        return float(cs)
    except (TypeError, ValueError):
        return 1.0
