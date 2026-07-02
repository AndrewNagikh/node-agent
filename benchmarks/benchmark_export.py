#!/usr/bin/env python3
"""Export helpers for Cluster Benchmark Suite — JSON, CSV, git metadata."""

from __future__ import annotations

import csv
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BENCH_DIR = Path(__file__).resolve().parent
ROOT = BENCH_DIR.parent
LLAMA = ROOT / "llama.cpp"


def _run_git(args: list[str], cwd: Path | None = None) -> str:
    try:
        out = subprocess.check_output(
            ["git", *args],
            cwd=str(cwd or ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def detect_build_type() -> str:
    for key in ("BENCHMARK_BUILD_TYPE", "BUILD_TYPE"):
        if os.environ.get(key):
            return os.environ[key]
    build_dir = LLAMA / "build"
    cache = build_dir / "CMakeCache.txt"
    if cache.is_file():
        text = cache.read_text(errors="ignore")
        if "GGML_CUDA:BOOL=ON" in text:
            return "cuda"
        if "GGML_METAL:BOOL=ON" in text:
            return "metal"
    if platform.system() == "Darwin":
        return "metal"
    return "cpu"


def detect_backend() -> str:
    return os.environ.get("BENCHMARK_BACKEND", detect_build_type())


def detect_compiler() -> str:
    for var in ("CXX", "CC"):
        if os.environ.get(var):
            return os.environ[var]
    if platform.system() == "Windows":
        return "msvc"
    return _run_git(["config", "--get", "init.templateDir"]) and "gcc/clang" or "unknown"


def collect_git_metadata() -> dict[str, Any]:
    llama_sha = _run_git(["rev-parse", "HEAD"], LLAMA) if (LLAMA / ".git").exists() else ""
    return {
        "node_agent": {
            "sha": _run_git(["rev-parse", "HEAD"]),
            "branch": _run_git(["rev-parse", "--abbrev-ref", "HEAD"]),
            "describe": _run_git(["describe", "--tags", "--always", "--dirty"]),
        },
        "llama_cpp": {
            "sha": llama_sha,
            "describe": _run_git(["describe", "--tags", "--always", "--dirty"], LLAMA) if llama_sha else "",
        },
        "build_type": detect_build_type(),
        "backend": detect_backend(),
        "compiler": detect_compiler(),
        "os": platform.system(),
        "os_release": platform.release(),
        "arch": platform.machine(),
        "python": sys.version.split()[0],
    }


def make_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def default_output_dir(run_id: str | None = None) -> Path:
    rid = run_id or make_run_id()
    return ROOT / "logs" / "benchmark" / rid


def ensure_run_dirs(out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "root": out_dir,
        "traces": out_dir / "traces",
        "raw": out_dir / "raw",
    }
    paths["traces"].mkdir(exist_ok=True)
    paths["raw"].mkdir(exist_ok=True)
    return paths


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def write_trace(traces_dir: Path, name: str, payload: Any) -> Path:
    path = traces_dir / f"{name}.json"
    write_json(path, payload)
    return path


def flatten_metrics(run: dict[str, Any]) -> dict[str, Any]:
    """Single CSV row from one scenario result."""
    stages = {s["name"]: s for s in run.get("stages", [])}
    planner = stages.get("layout", {}).get("metrics", {})
    install = stages.get("synchronization", {}).get("metrics", {})
    coverage = stages.get("coverage", {}).get("metrics", {})
    material = stages.get("materialization", {}).get("metrics", {})
    session = stages.get("session_create", {}).get("metrics", {})
    generate = stages.get("generate", {}).get("metrics", {})
    warmup = stages.get("warmup", {}).get("metrics", {})

    row = {
        "run_id": run.get("run_id", ""),
        "scenario_id": run.get("scenario_id", ""),
        "model_key": run.get("model_key", ""),
        "model_id": run.get("model_id", ""),
        "cluster_size_target": run.get("cluster_size_target", ""),
        "cluster_size_observed": run.get("cluster_size_observed", ""),
        "prompt_length": run.get("prompt_length", ""),
        "generate_tokens": run.get("generate_tokens", ""),
        "planner_ms": stages.get("layout", {}).get("duration_ms", ""),
        "placements": planner.get("placement_count", ""),
        "fits_cluster": planner.get("fits_cluster", ""),
        "install_ms": stages.get("synchronization", {}).get("duration_ms", ""),
        "install_ops": install.get("operation_count", ""),
        "install_bytes": install.get("total_download_bytes", ""),
        "install_retries": install.get("retry_count", ""),
        "download_mbps": install.get("download_mbps", ""),
        "verify_mbps": install.get("verify_mbps", ""),
        "coverage_ms": stages.get("coverage", {}).get("duration_ms", ""),
        "coverage_ready_ms": coverage.get("ready_time_ms", ""),
        "materialization_ms": stages.get("materialization", {}).get("duration_ms", ""),
        "worker_gguf_bytes": material.get("worker_gguf_bytes", ""),
        "session_ms": stages.get("session_create", {}).get("duration_ms", ""),
        "warmup_ms": stages.get("warmup", {}).get("duration_ms", ""),
        "generate_ms": stages.get("generate", {}).get("duration_ms", ""),
        "generate_tps": generate.get("tokens_per_sec", ""),
        "prefill_ms": generate.get("prefill_ms", warmup.get("prefill_ms", "")),
        "decode_ms": generate.get("decode_ms", ""),
        "token_count": generate.get("token_count", ""),
        "stress_requests": run.get("stress", {}).get("request_count", ""),
        "stress_tps_avg": run.get("stress", {}).get("tokens_per_sec_avg", ""),
        "fault_recovery_ms": run.get("fault", {}).get("recovery_ms", ""),
    }
    return row


CSV_COLUMNS = [
    "run_id", "scenario_id", "model_key", "model_id",
    "cluster_size_target", "cluster_size_observed",
    "prompt_length", "generate_tokens",
    "planner_ms", "placements", "fits_cluster",
    "install_ms", "install_ops", "install_bytes", "install_retries",
    "download_mbps", "verify_mbps",
    "coverage_ms", "coverage_ready_ms",
    "materialization_ms", "worker_gguf_bytes",
    "session_ms", "warmup_ms", "generate_ms",
    "generate_tps", "prefill_ms", "decode_ms", "token_count",
    "stress_requests", "stress_tps_avg", "fault_recovery_ms",
]


def export_csv(path: Path, scenarios: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [flatten_metrics(s) for s in scenarios]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_results_document(
    run_id: str,
    profile: str,
    mode: str,
    orchestrator: str,
    cluster: dict[str, Any],
    scenarios: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = scenarios[0].get("started_at") if scenarios else None
    finished = scenarios[-1].get("finished_at") if scenarios else None
    return {
        "benchmark_version": "1.0",
        "run_id": run_id,
        "profile": profile,
        "mode": mode,
        "orchestrator": orchestrator,
        "started_at": started,
        "finished_at": finished,
        "cluster": cluster,
        "hardware": cluster.get("nodes", []),
        "software": metadata or collect_git_metadata(),
        "scenarios": scenarios,
        "summary": summarize_scenarios(scenarios),
    }


def summarize_scenarios(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    if not scenarios:
        return {}
    tps_vals = []
    for sc in scenarios:
        gen = next((s for s in sc.get("stages", []) if s["name"] == "generate"), None)
        if gen:
            tps = gen.get("metrics", {}).get("tokens_per_sec")
            if isinstance(tps, (int, float)):
                tps_vals.append(tps)
    return {
        "scenario_count": len(scenarios),
        "models": sorted({sc.get("model_key", "") for sc in scenarios}),
        "generate_tps_avg": round(sum(tps_vals) / len(tps_vals), 2) if tps_vals else None,
        "generate_tps_max": round(max(tps_vals), 2) if tps_vals else None,
    }


def save_results_bundle(out_dir: Path, document: dict[str, Any]) -> dict[str, Path]:
    paths = ensure_run_dirs(out_dir)
    results_path = paths["root"] / "results.json"
    csv_path = paths["root"] / "results.csv"
    write_json(results_path, document)
    export_csv(csv_path, document.get("scenarios", []))
    return {"results_json": results_path, "results_csv": csv_path, **paths}
