#!/usr/bin/env python3
"""Merge multiple benchmark results.json into one combined performance report."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from benchmark_export import write_json
from benchmark_overhead import build_comparison_table, compute_overhead, compute_scaling_table
from benchmark_perf import build_document_summary
from benchmark_report_perf import write_perf_reports


def load_document(path: Path) -> dict:
    p = path / "results.json" if path.is_dir() else path
    return json.loads(p.read_text(encoding="utf-8"))


def merge_documents(docs: list[dict], run_id: str, profile: str) -> dict:
    scenarios: list[dict] = []
    model_results: list[dict] = []
    seen: set[str] = set()

    for doc in docs:
        for sc in doc.get("scenarios", []):
            sid = sc.get("scenario_id", "")
            if sid in seen:
                continue
            seen.add(sid)
            scenarios.append(sc)
        for mr in doc.get("model_results", []):
            mk = mr.get("model_key", "")
            if any(m.get("model_key") == mk for m in model_results):
                continue
            model_results.append(mr)

    mono_baselines: dict[str, dict] = {}
    for sc in scenarios:
        if sc.get("cluster_size_target") == "mono" and sc.get("aggregate"):
            mono_baselines[sc.get("model_key", "")] = sc
    for sc in scenarios:
        mk = sc.get("model_key", "")
        if mk in mono_baselines and sc.get("cluster_size_target") != "mono":
            sc["overhead_vs_mono"] = compute_overhead(mono_baselines[mk], sc)

    summary = build_document_summary(scenarios)
    base = docs[0]
    return {
        "benchmark_version": base.get("benchmark_version", "10.1.2"),
        "run_id": run_id,
        "profile": profile,
        "mode": "warm",
        "merged_from": [d.get("run_id", "") for d in docs],
        "options": {
            **base.get("options", {}),
            "merged": True,
            "source_runs": [d.get("run_id", "") for d in docs],
        },
        "orchestrator": base.get("orchestrator"),
        "cluster": base.get("cluster"),
        "software": base.get("software"),
        "model_results": model_results,
        "scenarios": scenarios,
        "infrastructure": summary["infrastructure"],
        "runtime": summary["runtime"],
        "comparison": build_comparison_table(scenarios),
        "scaling": compute_scaling_table(scenarios),
        "summary": {
            **summary,
            "merged_model_count": len({s.get("model_key") for s in scenarios}),
            "skipped_scenarios": [s.get("scenario_id") for s in scenarios if s.get("skipped")],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge benchmark result directories")
    parser.add_argument("inputs", nargs="+", help="Result dirs or results.json paths")
    parser.add_argument("--output-dir", required=True, help="Combined report output directory")
    parser.add_argument("--profile", default="warm_all", help="Profile label for merged report")
    parser.add_argument("--run-id", default=None, help="Run ID (default: timestamp)")
    args = parser.parse_args()

    docs = [load_document(Path(p)) for p in args.inputs]
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    document = merge_documents(docs, run_id, args.profile)
    write_json(out_dir / "results.json", document)
    write_perf_reports(out_dir, document)

    n_ok = sum(
        1 for sc in document["scenarios"]
        if not sc.get("skipped") and sc.get("runtime", {}).get("generations")
    )
    n_skip = sum(1 for sc in document["scenarios"] if sc.get("skipped"))
    print(f"Merged {len(docs)} runs → {out_dir}")
    print(f"  scenarios: {len(document['scenarios'])} ({n_ok} with runtime, {n_skip} skipped)")
    print(f"  models: {document['summary'].get('models', [])}")
    print(f"  report: {out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
