#!/usr/bin/env python3
"""Task 12.9 — perf trace regression vs pinned baseline."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

RegressionStatus = Literal["PASS", "WARN", "FAIL", "SKIP", "IMPROVED"]

DEFAULT_BASELINE_DIR = Path("logs/perf_trace/_baselines")

REGRESSION_METRICS: list[dict[str, Any]] = [
    {"key": "decode_ms_per_token", "label": "Decode ms/token", "lower_is_better": True, "critical": True},
    {"key": "ttft_ms", "label": "TTFT warm", "lower_is_better": True, "critical": True},
    {"key": "session_create_ms", "label": "Session create", "lower_is_better": True, "critical": False},
    {"key": "hidden_transfer_ms_per_hop", "label": "Network / hop", "lower_is_better": True, "critical": False},
    {"key": "serialize_ms_per_hop", "label": "Serialization / hop", "lower_is_better": True, "critical": False},
    {"key": "scheduler_wait_ms_per_token", "label": "Scheduler wait", "lower_is_better": True, "critical": False},
    {"key": "pipeline_utilization_pct", "label": "Pipeline utilization", "lower_is_better": False, "critical": False},
    {"key": "worker_idle_pct", "label": "Worker idle", "lower_is_better": True, "critical": False},
    {"key": "install_reuse_pct", "label": "Install reuse", "lower_is_better": False, "critical": False},
    {"key": "entry_compute_ms", "label": "Entry compute", "lower_is_better": True, "critical": False},
    {"key": "middle_compute_ms", "label": "Middle compute", "lower_is_better": True, "critical": False},
    {"key": "final_compute_ms", "label": "Final compute", "lower_is_better": True, "critical": False},
]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _avg_field(tokens: list[dict[str, Any]], field: str) -> float | None:
    vals = [float(t[field]) for t in tokens if isinstance(t.get(field), (int, float))]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 3)


def snapshot_from_budget(budget_doc: dict[str, Any], trace_doc: dict[str, Any] | None = None) -> dict[str, Any]:
    metrics = budget_doc.get("metrics") if isinstance(budget_doc.get("metrics"), dict) else {}
    tokens = []
    if trace_doc:
        raw_tokens = trace_doc.get("tokens")
        if isinstance(raw_tokens, list):
            tokens = raw_tokens

    snap: dict[str, Any] = {
        "decode_ms_per_token": metrics.get("decode_ms_per_token"),
        "ttft_ms": metrics.get("ttft_ms"),
        "session_create_ms": metrics.get("session_create_ms"),
        "hidden_transfer_ms_per_hop": metrics.get("hidden_transfer_ms_per_hop"),
        "serialize_ms_per_hop": metrics.get("serialize_ms_per_hop"),
        "scheduler_wait_ms_per_token": metrics.get("scheduler_wait_ms_per_token"),
        "pipeline_utilization_pct": metrics.get("pipeline_utilization_pct"),
        "worker_idle_pct": metrics.get("worker_idle_pct"),
        "install_reuse_pct": metrics.get("install_reuse_pct"),
        "entry_compute_ms": _avg_field(tokens, "entry_compute_ms"),
        "middle_compute_ms": _avg_field(tokens, "middle_compute_ms"),
        "final_compute_ms": _avg_field(tokens, "final_compute_ms"),
    }
    rollup = metrics.get("rollup") if isinstance(metrics.get("rollup"), dict) else {}
    buckets = rollup.get("buckets_pct")
    if isinstance(buckets, dict):
        snap["buckets_pct"] = buckets
    return {k: v for k, v in snap.items() if v is not None}


def baseline_filename(profile: str, model: str, cluster_size: int) -> str:
    safe_profile = profile.replace("/", "_")
    safe_model = model.replace("/", "_")
    return f"{safe_profile}_{safe_model}_c{cluster_size}.json"


def pct_change(prev: float, curr: float) -> float:
    if prev == 0:
        return 0.0 if curr == 0 else 100.0
    return round(100.0 * (curr - prev) / prev, 1)


def regression_status(
        delta_pct: float | None,
        *,
        lower_is_better: bool,
        warn_pct: float,
        fail_pct: float,
) -> RegressionStatus:
    if delta_pct is None:
        return "SKIP"
    bad = delta_pct if lower_is_better else -delta_pct
    if bad < 0:
        return "IMPROVED"
    if bad <= warn_pct:
        return "PASS"
    if bad <= fail_pct:
        return "WARN"
    return "FAIL"


def compare_snapshots(
        prev: dict[str, Any],
        curr: dict[str, Any],
        *,
        warn_pct: float = 10.0,
        fail_pct: float = 25.0,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in REGRESSION_METRICS:
        key = str(spec["key"])
        p = prev.get(key)
        c = curr.get(key)
        if not isinstance(p, (int, float)) or not isinstance(c, (int, float)):
            rows.append({
                "metric": key,
                "label": spec["label"],
                "prev": p,
                "curr": c,
                "delta": None,
                "delta_pct": None,
                "status": "SKIP",
                "critical": spec.get("critical", False),
                "improved": None,
            })
            continue
        delta = round(float(c) - float(p), 3)
        d_pct = pct_change(float(p), float(c))
        status = regression_status(
            d_pct,
            lower_is_better=bool(spec.get("lower_is_better", True)),
            warn_pct=warn_pct,
            fail_pct=fail_pct,
        )
        rows.append({
            "metric": key,
            "label": spec["label"],
            "prev": float(p),
            "curr": float(c),
            "delta": delta,
            "delta_pct": d_pct,
            "status": status,
            "critical": spec.get("critical", False),
            "improved": status == "IMPROVED",
        })
    return rows


def summarize_regression(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status", "SKIP"))
        counts[status] = counts.get(status, 0) + 1
    critical_failures = [
        row for row in rows
        if row.get("critical") and row.get("status") == "FAIL"
    ]
    return {
        "status_counts": counts,
        "has_regression": any(row.get("status") in ("WARN", "FAIL") for row in rows),
        "has_critical_fail": len(critical_failures) > 0,
        "critical_failures": critical_failures,
    }


def build_regression_md(document: dict[str, Any]) -> str:
    lines = [
        "# Task 12 Regression Diff",
        "",
        f"**Profile:** `{document.get('profile', '')}`  ",
        f"**Model:** `{document.get('model', '')}`  ",
        f"**Cluster:** `{document.get('cluster_size', '')}`  ",
        f"**Baseline pinned:** `{document.get('baseline_pinned', False)}`  ",
        "",
        "| Metric | Prev | Curr | Delta | Status |",
        "|--------|------|------|-------|--------|",
    ]
    for row in document.get("comparisons", []):
        prev = row.get("prev")
        curr = row.get("curr")
        d_pct = row.get("delta_pct")
        prev_s = "—" if prev is None else f"{prev}"
        curr_s = "—" if curr is None else f"{curr}"
        delta_s = "—" if d_pct is None else f"{d_pct:+.1f}%"
        mark = ""
        if row.get("status") == "IMPROVED":
            mark = " ✓"
        elif row.get("status") == "FAIL":
            mark = " ✗"
        lines.append(
            f"| {row.get('label', '')} | {prev_s} | {curr_s} | {delta_s} | {row.get('status', 'SKIP')}{mark} |"
        )
    summary = document.get("summary") or {}
    lines.extend([
        "",
        "## Summary",
        "",
        f"- Regression detected: **{summary.get('has_regression', False)}**",
        f"- Critical FAIL: **{summary.get('has_critical_fail', False)}**",
        "",
    ])
    return "\n".join(lines)


def run_regression(
        analysis_dir: Path,
        *,
        profile: str,
        model: str,
        cluster_size: int,
        baseline_dir: Path | None = None,
        out_dir: Path | None = None,
        pin_if_missing: bool = False,
        update_baseline: bool = False,
        warn_pct: float | None = None,
        fail_pct: float | None = None,
) -> dict[str, Any]:
    out = out_dir or analysis_dir
    out.mkdir(parents=True, exist_ok=True)
    base_dir = baseline_dir or DEFAULT_BASELINE_DIR
    base_dir.mkdir(parents=True, exist_ok=True)

    warn = float(warn_pct if warn_pct is not None else os.environ.get("REGRESSION_WARN_PCT", "10"))
    fail = float(fail_pct if fail_pct is not None else os.environ.get("REGRESSION_FAIL_PCT", "25"))

    budget_doc = _read_json(analysis_dir / "budget.json")
    trace_doc = _read_json(analysis_dir / "trace.json")
    curr = snapshot_from_budget(budget_doc, trace_doc)

    baseline_path = base_dir / baseline_filename(profile, model, cluster_size)
    baseline_pinned = False

    if not baseline_path.is_file():
        if pin_if_missing or update_baseline:
            baseline_doc = {
                "profile": profile,
                "model": model,
                "cluster_size": cluster_size,
                "pinned_at": datetime.now(timezone.utc).isoformat(),
                "snapshot": curr,
            }
            baseline_path.write_text(json.dumps(baseline_doc, indent=2), encoding="utf-8")
            baseline_pinned = True
            prev = curr
        else:
            prev = {}
    else:
        prev = (_read_json(baseline_path).get("snapshot") or {})

    comparisons = compare_snapshots(prev, curr, warn_pct=warn, fail_pct=fail)
    summary = summarize_regression(comparisons)

    if update_baseline and not baseline_pinned:
        baseline_doc = {
            "profile": profile,
            "model": model,
            "cluster_size": cluster_size,
            "pinned_at": datetime.now(timezone.utc).isoformat(),
            "snapshot": curr,
        }
        baseline_path.write_text(json.dumps(baseline_doc, indent=2), encoding="utf-8")

    document = {
        "analysis_dir": str(analysis_dir),
        "profile": profile,
        "model": model,
        "cluster_size": cluster_size,
        "baseline_path": str(baseline_path),
        "baseline_pinned": baseline_pinned,
        "warn_pct": warn,
        "fail_pct": fail,
        "prev": prev,
        "curr": curr,
        "comparisons": comparisons,
        "summary": summary,
    }

    (out / "regression_diff.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
    (out / "regression.md").write_text(build_regression_md(document), encoding="utf-8")
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 12 perf trace regression diff")
    parser.add_argument("analysis_dir", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--profile", default=os.environ.get("PERF_TRACE_PROFILE", "task12_docker"))
    parser.add_argument("--model", default=os.environ.get("PERF_TRACE_MODEL", "tinyllama"))
    parser.add_argument("--cluster-size", type=int, default=int(os.environ.get("PERF_TRACE_CLUSTER_SIZE", "3")))
    parser.add_argument("--baseline-dir", type=Path, default=DEFAULT_BASELINE_DIR)
    parser.add_argument("--pin-if-missing", action="store_true")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--warn-pct", type=float)
    parser.add_argument("--fail-pct", type=float)
    args = parser.parse_args()

    doc = run_regression(
        args.analysis_dir,
        profile=args.profile,
        model=args.model,
        cluster_size=args.cluster_size,
        baseline_dir=args.baseline_dir,
        out_dir=args.output,
        pin_if_missing=args.pin_if_missing,
        update_baseline=args.update_baseline,
        warn_pct=args.warn_pct,
        fail_pct=args.fail_pct,
    )
    print(json.dumps({
        "baseline_pinned": doc["baseline_pinned"],
        "summary": doc["summary"],
    }, indent=2))
    return 2 if doc["summary"].get("has_critical_fail") else 0


if __name__ == "__main__":
    raise SystemExit(main())
