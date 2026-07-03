#!/usr/bin/env python3
"""Compare two benchmark runs and show metric diffs.

Usage:
  python benchmarks/benchmark_compare.py logs/benchmark/run_a logs/benchmark/run_b
  python benchmarks/benchmark_compare.py logs/benchmark/run_a/results.json run_b/results.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BENCH_DIR = Path(__file__).resolve().parent
ROOT = BENCH_DIR.parent
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))


def load_run(path: Path) -> dict[str, Any]:
    if path.is_dir():
        path = path / "results.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def pct_change(old: float | None, new: float | None) -> str | None:
    if old is None or new is None or old == 0:
        return None
    delta = 100.0 * (new - old) / old
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.1f}%"


def stage_ms(scenario: dict[str, Any], name: str) -> float | None:
    for s in scenario.get("stages", []):
        if s.get("name") == name:
            v = s.get("duration_ms")
            return float(v) if v is not None else None
    return None


def stage_metric(scenario: dict[str, Any], stage: str, key: str) -> Any:
    for s in scenario.get("stages", []):
        if s.get("name") == stage:
            return s.get("metrics", {}).get(key)
    return None


def scenario_key(sc: dict[str, Any]) -> str:
    return sc.get("scenario_id") or f"{sc.get('model_key')}_{sc.get('prompt_length')}_{sc.get('generate_tokens')}"


def index_scenarios(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {scenario_key(sc): sc for sc in document.get("scenarios", [])}


def compare_metric(label: str, old: float | None, new: float | None, higher_is_better: bool = False) -> dict[str, Any]:
    delta = None
    if old is not None and new is not None:
        delta = new - old
    pct = pct_change(old, new)
    improved = None
    if delta is not None:
        if higher_is_better:
            improved = delta > 0
        else:
            improved = delta < 0
    return {
        "label": label,
        "a": old,
        "b": new,
        "delta": round(delta, 3) if delta is not None else None,
        "pct": pct,
        "improved": improved,
    }


def agg_mean(sc: dict[str, Any], key: str) -> float | None:
    entry = sc.get("aggregate", {}).get(key, {})
    if isinstance(entry, dict):
        v = entry.get("mean")
        return float(v) if isinstance(v, (int, float)) else None
    return None


def infra_metric(sc: dict[str, Any], key: str) -> float | None:
    infra = sc.get("infrastructure", {})
    if key == "planner":
        v = infra.get("planner_ms") or infra.get("stages", {}).get("planner_ms")
    elif key == "session_create":
        v = infra.get("session_create_ms")
    elif key == "materialization":
        v = infra.get("materialization_ms")
    elif key == "install":
        v = infra.get("install_ms")
    else:
        v = None
    return float(v) if isinstance(v, (int, float)) else None


def compare_runs(run_a: dict[str, Any], run_b: dict[str, Any], regression_threshold_pct: float = 10.0) -> dict[str, Any]:
    is_perf = run_a.get("benchmark_version", "").startswith("10.1") or run_b.get("benchmark_version", "").startswith("10.1")
    idx_a = index_scenarios(run_a)
    idx_b = index_scenarios(run_b)
    keys = sorted(set(idx_a) | set(idx_b))
    scenario_diffs = []
    regressions: list[dict[str, Any]] = []
    infra_regressions: list[dict[str, Any]] = []
    runtime_regressions: list[dict[str, Any]] = []

    INFRA_METRICS = ("Planner", "Session Create", "Materialization", "Install (cold)")
    RUNTIME_METRICS = ("TTFT", "Decode TPS", "Prefill TPS", "ms/token", "Jitter")

    for key in keys:
        a, b = idx_a.get(key), idx_b.get(key)
        if not a or not b:
            scenario_diffs.append({"scenario_id": key, "missing": "a" if not a else "b"})
            continue
        if is_perf:
            runtime_agg_a = a.get("runtime", {}).get("aggregate", {})
            runtime_agg_b = b.get("runtime", {}).get("aggregate", {})
            jitter_a = runtime_agg_a.get("jitter", {}).get("stddev") if isinstance(runtime_agg_a.get("jitter"), dict) else None
            jitter_b = runtime_agg_b.get("jitter", {}).get("stddev") if isinstance(runtime_agg_b.get("jitter"), dict) else None
            metrics = [
                compare_metric("TTFT", agg_mean(a, "ttft.total_ms"), agg_mean(b, "ttft.total_ms")),
                compare_metric("Decode TPS",
                                agg_mean(a, "decode.tokens_per_sec"),
                                agg_mean(b, "decode.tokens_per_sec"),
                                higher_is_better=True),
                compare_metric("Prefill TPS",
                                agg_mean(a, "prefill.tokens_per_sec"),
                                agg_mean(b, "prefill.tokens_per_sec"),
                                higher_is_better=True),
                compare_metric("ms/token",
                                agg_mean(a, "decode.ms_per_token"),
                                agg_mean(b, "decode.ms_per_token")),
                compare_metric("Jitter", jitter_a, jitter_b),
                compare_metric("Planner", infra_metric(a, "planner"), infra_metric(b, "planner")),
                compare_metric("Session Create",
                                infra_metric(a, "session_create"),
                                infra_metric(b, "session_create")),
                compare_metric("Materialization",
                                infra_metric(a, "materialization"),
                                infra_metric(b, "materialization")),
                compare_metric("Install (cold)",
                                a.get("infrastructure", {}).get("install_ms") or a.get("cold", {}).get("install_ms"),
                                b.get("infrastructure", {}).get("install_ms") or b.get("cold", {}).get("install_ms")),
            ]
        else:
            metrics = [
                compare_metric("Planner", stage_ms(a, "layout"), stage_ms(b, "layout")),
                compare_metric("Install", stage_ms(a, "synchronization"), stage_ms(b, "synchronization")),
                compare_metric("Coverage", stage_ms(a, "coverage"), stage_ms(b, "coverage")),
                compare_metric("Materialization", stage_ms(a, "materialization"), stage_ms(b, "materialization")),
                compare_metric("Session", stage_ms(a, "session_create"), stage_ms(b, "session_create")),
                compare_metric("Generate TPS",
                                stage_metric(a, "generate", "tokens_per_sec"),
                                stage_metric(b, "generate", "tokens_per_sec"),
                                higher_is_better=True),
                compare_metric("Prefill",
                                stage_metric(a, "generate", "prefill_ms"),
                                stage_metric(b, "generate", "prefill_ms")),
                compare_metric("Decode",
                                stage_metric(a, "generate", "decode_ms"),
                                stage_metric(b, "generate", "decode_ms")),
            ]
        scenario_diffs.append({
            "scenario_id": key,
            "model_key": a.get("model_key"),
            "metrics": metrics,
        })
        for m in metrics:
            pct_s = m.get("pct")
            if not pct_s or m.get("improved") is not False:
                continue
            try:
                pct_val = abs(float(str(pct_s).replace("%", "").replace("+", "")))
            except ValueError:
                continue
            if pct_val < regression_threshold_pct:
                continue
            entry = {
                "scenario_id": key,
                "metric": m["label"],
                "pct": pct_s,
                "a": m.get("a"),
                "b": m.get("b"),
                "category": "runtime" if m["label"] in RUNTIME_METRICS else "infrastructure",
            }
            regressions.append(entry)
            if m["label"] in INFRA_METRICS:
                infra_regressions.append(entry)
            elif m["label"] in RUNTIME_METRICS:
                runtime_regressions.append(entry)
            elif m["label"] in ("TTFT", "Decode TPS", "Generate TPS", "ms/token"):
                runtime_regressions.append(entry)

    mem_a = run_a.get("cluster", {}).get("memory", {}).get("free_total_gb")
    mem_b = run_b.get("cluster", {}).get("memory", {}).get("free_total_gb")

    return {
        "run_a": {
            "run_id": run_a.get("run_id"),
            "profile": run_a.get("profile"),
            "software": run_a.get("software", {}),
        },
        "run_b": {
            "run_id": run_b.get("run_id"),
            "profile": run_b.get("profile"),
            "software": run_b.get("software", {}),
        },
        "cluster_memory": compare_metric("Memory (free GB)", mem_a, mem_b, higher_is_better=True),
        "scenarios": scenario_diffs,
        "regressions": regressions,
        "infrastructure_regressions": infra_regressions,
        "runtime_regressions": runtime_regressions,
        "performance_regression": len(regressions) > 0,
    }


def format_text(diff: dict[str, Any]) -> str:
    lines = [
        "# Benchmark Compare",
        "",
        f"**A:** `{diff['run_a']['run_id']}` ({diff['run_a']['profile']})",
        f"**B:** `{diff['run_b']['run_id']}` ({diff['run_b']['profile']})",
        "",
        "## Git",
        "",
        f"- A node-agent: `{diff['run_a']['software'].get('node_agent', {}).get('sha', '')[:12]}`",
        f"- B node-agent: `{diff['run_b']['software'].get('node_agent', {}).get('sha', '')[:12]}`",
        "",
    ]
    cm = diff.get("cluster_memory", {})
    if cm.get("pct"):
        lines.append(f"## Cluster Memory: {cm['pct']}")
        lines.append("")

    for sc in diff.get("scenarios", []):
        if sc.get("missing"):
            lines.append(f"### {sc['scenario_id']} — missing in {sc['missing']}")
            continue
        lines.append(f"### {sc['scenario_id']} ({sc.get('model_key', '')})")
        lines.append("")
        lines.append("| Metric | A | B | Δ |")
        lines.append("|--------|---|---|---|")
        for m in sc.get("metrics", []):
            a = fmt_val(m["label"], m.get("a"))
            b = fmt_val(m["label"], m.get("b"))
            pct = m.get("pct") or "—"
            arrow = ""
            if m.get("improved") is True:
                arrow = " ✓"
            elif m.get("improved") is False:
                arrow = " ✗"
            lines.append(f"| {m['label']} | {a} | {b} | {pct}{arrow} |")
        lines.append("")

    if diff.get("performance_regression"):
        lines.extend([
            "## PERFORMANCE REGRESSION",
            "",
            f"Threshold: **{diff.get('regression_threshold_pct', 10)}%**",
            "",
        ])
        if diff.get("infrastructure_regressions"):
            lines.append("### Infrastructure")
            for reg in diff["infrastructure_regressions"]:
                lines.append(f"- `{reg['scenario_id']}` **{reg['metric']}** {reg['pct']}")
            lines.append("")
        if diff.get("runtime_regressions"):
            lines.append("### Runtime")
            for reg in diff["runtime_regressions"]:
                lines.append(f"- `{reg['scenario_id']}` **{reg['metric']}** {reg['pct']}")
            lines.append("")
        for reg in diff.get("regressions", []):
            if reg not in diff.get("infrastructure_regressions", []) and reg not in diff.get("runtime_regressions", []):
                lines.append(f"- `{reg['scenario_id']}` **{reg['metric']}** {reg['pct']}")
        lines.append("")

    return "\n".join(lines)


def fmt_val(label: str, v: Any) -> str:
    if v is None:
        return "—"
    if "TPS" in label:
        return f"{v:.1f} tok/s"
    if isinstance(v, float) and v >= 1000:
        return f"{v / 1000:.2f}s"
    if isinstance(v, float):
        return f"{v:.1f}"
    return str(v)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two benchmark runs")
    parser.add_argument("run_a", help="First run directory or results.json")
    parser.add_argument("run_b", help="Second run directory or results.json")
    parser.add_argument("--json", action="store_true", help="Print JSON diff")
    parser.add_argument("-o", "--output", help="Write markdown diff to file")
    parser.add_argument("--regression-threshold", type=float,
                        default=float(__import__("os").environ.get("BENCHMARK_REGRESSION_PCT", "10")),
                        help="Flag regression when TTFT/TPS degrades more than this %%")
    args = parser.parse_args()

    path_a = Path(args.run_a)
    path_b = Path(args.run_b)
    if not path_a.is_absolute():
        path_a = ROOT / path_a
    if not path_b.is_absolute():
        path_b = ROOT / path_b

    try:
        run_a = load_run(path_a)
        run_b = load_run(path_b)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    diff = compare_runs(run_a, run_b, regression_threshold_pct=args.regression_threshold)
    diff["regression_threshold_pct"] = args.regression_threshold
    text = format_text(diff)

    if args.json:
        print(json.dumps(diff, indent=2))
    else:
        print(text)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"Wrote {out}")

    return 2 if diff.get("performance_regression") else 0


if __name__ == "__main__":
    sys.exit(main())
