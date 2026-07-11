#!/usr/bin/env python3
"""Task 12.11 — unified perf trace post-processing pipeline."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from perf_trace.bottleneck import merge_budget_analysis
from perf_trace.ggml import merge_ggml
from perf_trace.html_timeline import write_timeline
from perf_trace.install import merge_install_trace
from perf_trace.merge import load_jsonl, merge_trace_dir
from perf_trace.metric_validation import (
    STAGES,
    filter_trace,
    load_raw_events,
    pick_primary_trace_id,
    stage_span_coverage,
    write_validation,
)
from perf_trace.observability import write_observability_artifacts
from perf_trace.queue import merge_queue
from perf_trace.regression import DEFAULT_BASELINE_DIR, run_regression
from perf_trace.session import merge_session_trace
from perf_trace.ttft import merge_ttft_trace
from perf_trace.utilization import merge_utilization


def _copy_raw_to_merged(raw_dir: Path, merged_dir: Path) -> int:
    merged_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in sorted(raw_dir.glob("*.jsonl")):
        shutil.copy2(path, merged_dir / path.name)
        count += 1
    return count


def run_postprocess(
        raw_dir: Path,
        out_root: Path,
        *,
        profile: str,
        model: str,
        cluster_size: int,
        baseline_dir: Path | None = None,
        pin_if_missing: bool = False,
        update_baseline: bool = False,
        regression: bool = True,
        timeline: bool = True,
        results_path: Path | None = None,
        generate_timing: dict[str, Any] | None = None,
        trace_id: str | None = None,
) -> dict[str, Any]:
    raw_dir = Path(raw_dir)
    out_root = Path(out_root)
    analysis = out_root / "analysis"
    install_analysis = out_root / "install_analysis"
    session_analysis = out_root / "session_analysis"
    ttft_analysis = out_root / "ttft_analysis"
    merged = out_root / "merged"

    merged_count = _copy_raw_to_merged(raw_dir, merged)
    merge_doc = merge_trace_dir(merged, analysis)

    install_doc = merge_install_trace(raw_dir, install_analysis)
    session_doc = merge_session_trace(raw_dir, session_analysis)
    ttft_doc = merge_ttft_trace(raw_dir, ttft_analysis)

    events = []
    for path in sorted(raw_dir.glob("*.jsonl")):
        events.extend(load_jsonl(path))
    merge_queue(events, analysis)

    merge_utilization(raw_dir, analysis)
    merge_ggml(raw_dir, analysis)

    budget_doc = merge_budget_analysis(
        analysis,
        analysis,
        install_dir=install_analysis,
        session_dir=session_analysis,
        ttft_dir=ttft_analysis,
    )

    regression_doc: dict[str, Any] | None = None
    if regression:
        regression_doc = run_regression(
            analysis,
            profile=profile,
            model=model,
            cluster_size=cluster_size,
            baseline_dir=baseline_dir or DEFAULT_BASELINE_DIR,
            out_dir=analysis,
            pin_if_missing=pin_if_missing,
            update_baseline=update_baseline,
        )

    timeline_path = analysis / "timeline.html"
    if timeline:
        write_timeline(analysis, timeline_path, ttft_dir=ttft_analysis)

    raw_events = load_raw_events(raw_dir)
    primary_tid = pick_primary_trace_id(raw_events, prefer=trace_id)
    trace_events = filter_trace(raw_events, primary_tid) if primary_tid else raw_events
    all_stages_pass = all(
        stage_span_coverage(trace_events, stage)["status"] == "PASS"
        for stage in STAGES
    )

    observability_doc = write_observability_artifacts(
        raw_dir,
        analysis,
        trace_id=primary_tid,
        all_stages_pass=all_stages_pass,
    )

    validation_doc = write_validation(
        raw_dir,
        analysis,
        trace_id=trace_id,
        generate_timing=generate_timing,
        results_path=results_path,
        observability=observability_doc,
    )

    return {
        "raw_files": merged_count,
        "analysis_dir": str(analysis),
        "merged_count": merge_doc.get("event_count", 0),
        "token_count": merge_doc.get("token_count", 0),
        "budget": budget_doc.get("budget", []),
        "budget_summary": budget_doc.get("status_counts", {}),
        "bottleneck_pct": (budget_doc.get("bottleneck") or {}).get("category_pct", {}),
        "regression": regression_doc.get("summary") if regression_doc else None,
        "timeline_html": str(timeline_path) if timeline else None,
        "install": install_doc.get("summary") if isinstance(install_doc, dict) else None,
        "session": session_doc.get("summary") if isinstance(session_doc, dict) else None,
        "ttft": ttft_doc.get("summary") if isinstance(ttft_doc, dict) else None,
        "validation": validation_doc,
        "validation_overall": validation_doc.get("overall"),
        "observability": {
            "trace_id": observability_doc.get("trace_id"),
            "timeline_events": observability_doc.get("timeline", {}).get("event_count"),
            "critical_path_ms": observability_doc.get("critical_path", {}).get("avg_serial_critical_path_ms"),
            "bubble_status": observability_doc.get("bubble", {}).get("status"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 12 perf trace post-process pipeline")
    parser.add_argument("--raw", type=Path, required=True, help="Directory with collected *.jsonl")
    parser.add_argument("--out", type=Path, required=True, help="Output root (analysis/ subdirs)")
    parser.add_argument("--profile", default="task12_docker")
    parser.add_argument("--model", default="tinyllama")
    parser.add_argument("--cluster-size", type=int, default=3)
    parser.add_argument("--baseline-dir", type=Path, default=DEFAULT_BASELINE_DIR)
    parser.add_argument("--pin-if-missing", action="store_true")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--no-regression", action="store_true")
    parser.add_argument("--no-timeline", action="store_true")
    parser.add_argument("--results", type=Path, help="benchmark results.json for TPS source of truth")
    parser.add_argument("--trace-id", default="", help="Primary decode trace id")
    args = parser.parse_args()

    doc = run_postprocess(
        args.raw,
        args.out,
        profile=args.profile,
        model=args.model,
        cluster_size=args.cluster_size,
        baseline_dir=args.baseline_dir,
        pin_if_missing=args.pin_if_missing,
        update_baseline=args.update_baseline,
        regression=not args.no_regression,
        timeline=not args.no_timeline,
        results_path=args.results,
        trace_id=args.trace_id or None,
    )
    print(json.dumps(doc, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
