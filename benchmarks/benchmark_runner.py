#!/usr/bin/env python3
"""Cluster Benchmark Suite — distributed inference measurement (no PASS/FAIL).

Usage:
  python benchmarks/benchmark_runner.py
  python benchmarks/benchmark_runner.py --profile ci
  python benchmarks/benchmark_runner.py --profile scaling --mode scaling
  python benchmarks/benchmark_runner.py --model tinyllama --cluster-size 3
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yaml_util import load_yaml_file

from benchmark_export import (
    build_results_document,
    collect_git_metadata,
    default_output_dir,
    make_run_id,
    save_results_bundle,
    write_trace,
)
from benchmark_report import write_reports

BENCH_DIR = Path(__file__).resolve().parent
ROOT = BENCH_DIR.parent
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))
ENV_FILES = [ROOT / ".env", ROOT / "llama.cpp" / ".env"]

ORCH = os.environ.get("ORCHESTRATOR", "http://127.0.0.1:9000")

# When orchestrator runs in Docker but the benchmark driver runs on the host,
# /nodes advertises docker network aliases (node-a, …) — map to published ports.
DOCKER_NODE_PORTS: dict[str, tuple[str, int]] = {
    "node-a": ("127.0.0.1", 9001),
    "node-b": ("127.0.0.1", 9002),
    "node-c": ("127.0.0.1", 9003),
}


def resolve_node_endpoint(host: str, port: int) -> tuple[str, int]:
    if os.environ.get("BENCHMARK_DOCKER", "1") == "1":
        mapped = DOCKER_NODE_PORTS.get(host)
        if mapped:
            return mapped
    return host, port


def log(msg: str) -> None:
    print(msg, flush=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_yaml(path: Path) -> dict[str, Any]:
    from yaml_util import load_yaml_file
    return load_yaml_file(path)


def load_hf_token() -> None:
    if os.environ.get("HF_TOKEN"):
        return
    for path in ENV_FILES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("HF_TOKEN="):
                os.environ["HF_TOKEN"] = line.split("=", 1)[1].strip()
                return


def http(method: str, path: str, body: dict | None = None, timeout: int = 120) -> tuple[int, Any]:
    url = ORCH.rstrip("/") + path
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = os.environ.get("HF_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            payload = json.loads(raw) if raw else {"error": str(e)}
        except json.JSONDecodeError:
            payload = {"error": raw or str(e)}
        return e.code, payload
    except (urllib.error.URLError, ConnectionResetError, TimeoutError, OSError) as e:
        return 0, {"error": str(e)}


def docker_compose_dir() -> Path:
    return ROOT / "llama.cpp" / "tools" / "distributed" / "docker"


def docker_nodes_need_restart() -> bool:
    """True if any node container is not running (OOM exit 137, etc.)."""
    compose_dir = docker_compose_dir()
    if not (compose_dir / "docker-compose.yml").is_file():
        return False
    result = subprocess.run(
        ["docker", "compose", "ps", "--format", "{{.Service}}\t{{.State}}"],
        cwd=compose_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    for line in result.stdout.splitlines():
        service, _, state = line.partition("\t")
        if service.startswith("node-") and "running" not in state.lower():
            return True
    return False


def restart_docker_nodes(services: list[str] | None = None) -> None:
    compose_dir = docker_compose_dir()
    targets = services or ["node-a", "node-b", "node-c"]
    log(f"Restarting Docker nodes: {', '.join(targets)}")
    subprocess.run(
        ["docker", "compose", "restart", *targets],
        cwd=compose_dir,
        check=False,
        capture_output=True,
    )


def ensure_docker_perf_trace_enabled() -> None:
    """Recreate Docker cluster with DIST_PERF_TRACE=1 (compose env interpolation)."""
    compose_dir = docker_compose_dir()
    if not (compose_dir / "docker-compose.yml").is_file():
        return
    log("Enabling DIST_PERF_TRACE on Docker cluster (recreate containers)")
    env = os.environ.copy()
    env["DIST_PERF_TRACE"] = "1"
    if os.environ.get("DIST_PERF_TRACE_GGML", "0") not in ("0", "", "false", "FALSE"):
        env["DIST_PERF_TRACE_GGML"] = "1"
    subprocess.run(
        [
            "docker", "compose", "up", "-d", "--force-recreate",
            "orchestrator", "node-a", "node-b", "node-c",
        ],
        cwd=compose_dir,
        env=env,
        check=False,
    )
    min_nodes = 3
    ok, _ = wait_cluster(min_nodes, int(os.environ.get("BENCHMARK_WAIT_CLUSTER_S", "120")))
    if not ok:
        log("Warning: cluster not ready after perf trace recreate")


def purge_model_cluster(model_id: str, keep_manifest: bool = False) -> tuple[int, Any]:
    """Clear layer store + registry install state for one model on all nodes."""
    return http(
        "POST",
        f"/models/{model_id}/reset",
        {"keep_manifest": keep_manifest},
        timeout=180,
    )


def node_http(host: str, port: int, path: str, method: str = "GET", body: dict | None = None,
              timeout: int = 15) -> tuple[int, Any]:
    host, port = resolve_node_endpoint(host, port)
    url = f"http://{host}:{port}{path}"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            payload = json.loads(raw) if raw else {"error": str(e)}
        except json.JSONDecodeError:
            payload = {"error": raw or str(e)}
        return e.code, payload
    except (urllib.error.URLError, ConnectionResetError, TimeoutError, OSError) as e:
        return 0, {"error": str(e)}


@dataclass
class StageRecord:
    name: str
    started_at: str = ""
    finished_at: str = ""
    duration_ms: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": round(self.duration_ms, 2),
            "metrics": self.metrics,
            "notes": self.notes,
        }


class StageTimer:
    def __init__(self, name: str) -> None:
        self.record = StageRecord(name=name)
        self._t0 = 0.0

    def __enter__(self) -> StageRecord:
        self.record.started_at = utc_now()
        self._t0 = time.perf_counter()
        return self.record

    def __exit__(self, *_: Any) -> None:
        self.record.duration_ms = (time.perf_counter() - self._t0) * 1000.0
        self.record.finished_at = utc_now()


def make_prompt(base: str, length: int) -> str:
    if length <= len(base):
        return base[:length]
    filler = " word" * ((length - len(base)) // 5 + 1)
    return (base + filler)[:length]


def fetch_cluster_snapshot() -> dict[str, Any]:
    status, out = http("GET", "/nodes", timeout=15)
    nodes = out.get("nodes", []) if status == 200 else []
    total_free_ram = total_ram = total_free_vram = total_vram = 0
    enriched = []
    for n in nodes:
        mem = n.get("memory", {})
        fr = int(mem.get("free_ram", 0))
        tr = int(mem.get("total_ram", 0))
        fv = int(mem.get("free_vram", 0))
        tv = int(mem.get("total_vram", 0))
        total_free_ram += fr
        total_ram += tr
        total_free_vram += fv
        total_vram += tv
        enriched.append({
            "node_id": n.get("node_id", n.get("node", "")),
            "host": n.get("host", ""),
            "port": n.get("port", 0),
            "connect_host": resolve_node_endpoint(n.get("host", ""), int(n.get("port", 0)))[0],
            "connect_port": resolve_node_endpoint(n.get("host", ""), int(n.get("port", 0) or 0))[1],
            "gpu": n.get("gpu", n.get("hardware", {}).get("gpu_name", "")),
            "backend": n.get("hardware", {}).get("backend", n.get("system", {}).get("arch", "")),
            "score": n.get("score", n.get("performance", {}).get("score")),
            "free_ram_gb": round(fr / (1024 ** 3), 2),
            "total_ram_gb": round(tr / (1024 ** 3), 2),
            "free_vram_gb": round(fv / (1024 ** 3), 2),
            "total_vram_gb": round(tv / (1024 ** 3), 2),
            "prefill_tps": n.get("prefill_tps", n.get("performance", {}).get("prefill_tps")),
            "decode_tps": n.get("decode_tps", n.get("performance", {}).get("decode_tps")),
        })
    gb = lambda b: round(b / (1024 ** 3), 2)
    return {
        "node_count": len(enriched),
        "nodes": enriched,
        "memory": {
            "free_ram_gb": gb(total_free_ram),
            "total_ram_gb": gb(total_ram),
            "free_vram_gb": gb(total_free_vram),
            "total_vram_gb": gb(total_vram),
            "free_total_gb": gb(total_free_ram + total_free_vram),
            "total_total_gb": gb(total_ram + total_vram),
        },
    }


def wait_cluster(min_count: int, timeout_s: int) -> tuple[bool, dict[str, Any]]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        snap = fetch_cluster_snapshot()
        last = snap
        if snap["node_count"] >= min_count:
            return True, snap
        time.sleep(2)
    return False, last


def release_cluster_workers(cluster: dict[str, Any]) -> dict[str, Any]:
    """POST /shutdown on every node to free worker memory between scenarios."""
    results: dict[str, Any] = {}
    for n in cluster.get("nodes", []):
        host = n.get("connect_host") or n.get("host", "")
        port = int(n.get("connect_port") or n.get("port", 0))
        nid = n.get("node_id", "")
        if not host or not port or not nid:
            continue
        status, out = node_http(host, port, "/shutdown", method="POST", body={}, timeout=30)
        results[nid] = {"status": status, "response": out}
    return results


def recover_docker_cluster(min_count: int, timeout_s: int) -> tuple[bool, dict[str, Any]]:
    """Wait for cluster; restart Docker nodes if degraded or OOM-killed."""
    if os.environ.get("BENCHMARK_DOCKER", "1") != "1":
        return wait_cluster(min_count, timeout_s)

    if docker_nodes_need_restart():
        restart_docker_nodes()

    ok, snap = wait_cluster(min_count, min(45, timeout_s))
    if ok:
        return ok, snap

    log(f"Cluster degraded ({snap.get('node_count', 0)}/{min_count} nodes) — restarting Docker nodes")
    restart_docker_nodes()
    return wait_cluster(min_count, timeout_s)


def inter_scenario_cleanup(
    profile: dict[str, Any],
    cluster: dict[str, Any],
    scenario: dict[str, Any],
    min_nodes: int,
) -> dict[str, Any]:
    """Destroy session, purge model layers, shutdown workers, restart nodes if needed."""
    if not profile.get("release_workers_between_models", True):
        return cluster

    sess_stage = next(
        (s for s in scenario.get("stages", []) if s.get("name") == "session_create"),
        None,
    )
    session_id = ""
    if sess_stage:
        session_id = sess_stage.get("metrics", {}).get("session_id", "")

    if session_id:
        destroy_session(session_id)

    completed_model = scenario.get("model_id", "")
    purge_metrics: dict[str, Any] = {}
    if profile.get("purge_model_after_scenario", True) and completed_model:
        status, out = purge_model_cluster(completed_model, keep_manifest=False)
        purge_metrics = {"status": status, "response": out if isinstance(out, dict) else {}}
        if status != 200:
            log(f"Warning: purge {completed_model} returned HTTP {status}")

    try:
        cluster = fetch_cluster_snapshot()
    except Exception as exc:  # noqa: BLE001
        log(f"Warning: cluster snapshot failed during cleanup: {exc}")
        cluster = cluster or {}

    worker_release = release_cluster_workers(cluster)
    shutdown_failed = any(
        r.get("status", 0) != 200 for r in worker_release.values() if isinstance(r, dict)
    )

    docker_mode = os.environ.get("BENCHMARK_DOCKER", "1") == "1"
    proactive_restart = profile.get("restart_docker_nodes_between_models", False)
    if docker_mode and (proactive_restart or shutdown_failed or docker_nodes_need_restart()):
        reason = "scheduled" if proactive_restart else "node failure/OOM recovery"
        log(f"Docker node restart ({reason})")
        restart_docker_nodes()

    cooldown = float(profile.get("inter_scenario_cooldown_s", 5))
    if cooldown > 0:
        log(f"Inter-scenario cooldown {cooldown}s")
        time.sleep(cooldown)

    ok, cluster = recover_docker_cluster(
        min_nodes,
        int(profile.get("wait_cluster_timeout_s", 120)),
    )
    if not ok:
        log(f"Warning: cluster not fully recovered ({cluster.get('node_count', 0)}/{min_nodes} nodes)")
    return cluster


def model_record(model_id: str) -> dict[str, Any]:
    status, out = http("GET", f"/models/{model_id}", timeout=30)
    return out if status == 200 else {}


def extract_planner_metrics(layout: dict[str, Any], cluster: dict[str, Any]) -> dict[str, Any]:
    placements = layout.get("placements", [])
    if isinstance(placements, int):
        placement_count = placements
        by_node: dict[str, dict[str, Any]] = {}
    else:
        placement_count = len(placements)
        by_node = {}
        for p in placements:
            node = p.get("node", p.get("node_id", ""))
            size = int(p.get("size_bytes", 0))
            entry = by_node.setdefault(node, {"layers": 0, "size_bytes": 0, "devices": set()})
            entry["layers"] += 1
            entry["size_bytes"] += size
            entry["devices"].add(p.get("device", "cpu"))

    node_caps = {
        n["node_id"]: {
            "free_ram_gb": n["free_ram_gb"],
            "free_vram_gb": n["free_vram_gb"],
        }
        for n in cluster.get("nodes", [])
    }
    utilization = {}
    for node, info in by_node.items():
        cap = node_caps.get(node, {})
        used_mb = info["size_bytes"] / (1024 ** 2)
        vram = cap.get("free_vram_gb", 0) or 0
        ram = cap.get("free_ram_gb", 0) or 0
        pool = max(vram, ram, 0.001)
        utilization[node] = {
            "layers": info["layers"],
            "size_mb": round(used_mb, 1),
            "fill_pct": round(100.0 * (used_mb / 1024) / pool, 1),
            "devices": sorted(info["devices"]),
        }

    layer_counts = [u["layers"] for u in utilization.values()]
    balance = 0.0
    if layer_counts:
        avg = sum(layer_counts) / len(layer_counts)
        balance = round(max(layer_counts) - min(layer_counts), 2) if len(layer_counts) > 1 else 0.0

    return {
        "fits_cluster": layout.get("fits_cluster"),
        "placement_count": placement_count,
        "total_weight_bytes": layout.get("total_weight_bytes", 0),
        "total_required_memory": layout.get("total_required_memory", 0),
        "warnings": layout.get("warnings", []),
        "nodes_used": sorted(utilization.keys()),
        "node_utilization": utilization,
        "layer_balance_delta": balance,
    }


def extract_install_plan_metrics(plan: dict[str, Any]) -> dict[str, Any]:
    ops = plan.get("install_plan", plan).get("operations", [])
    if not ops and plan.get("operation_count"):
        ops = plan.get("operations", [])
    blob_ids = set()
    for op in ops:
        dl = op.get("download") or op
        blob = dl.get("blob_id", "")
        if blob:
            blob_ids.add(blob)
    return {
        "operation_count": plan.get("operation_count", len(ops)),
        "total_download_bytes": plan.get("total_download_bytes", 0),
        "blob_count": len(blob_ids),
        "operations_sample": ops[:5],
    }


def job_progress(job: dict[str, Any]) -> dict[str, Any]:
    nodes = job.get("nodes", {})
    out: dict[str, Any] = {}
    for nid, nd in nodes.items():
        if isinstance(nd, dict):
            out[nid] = {
                "state": nd.get("state", nd.get("status", "")),
                "ready_count": nd.get("ready_count"),
                "total_count": nd.get("total_count"),
            }
    return out


def wait_install_job(job_id: str, timeout_s: int, traces_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    deadline = time.time() + timeout_s
    traces: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    while time.time() < deadline:
        status, job = http("GET", f"/jobs/{job_id}", timeout=30)
        if status == 200:
            last = job
            traces.append({
                "t": time.time(),
                "state": job.get("state", ""),
                "nodes": job_progress(job),
            })
            state = job.get("state", "")
            if state in ("completed", "failed"):
                write_trace(traces_dir, f"job_{job_id}", {"snapshots": traces, "final": job})
                return job, traces
        time.sleep(3)
    write_trace(traces_dir, f"job_{job_id}_timeout", {"snapshots": traces, "last": last})
    return {"state": "timeout", "error": "timeout", **last}, traces


def sample_node_runtime(cluster: dict[str, Any]) -> dict[str, Any]:
    samples = {}
    for n in cluster.get("nodes", []):
        host = n.get("connect_host") or n.get("host", "")
        port = int(n.get("connect_port") or n.get("port", 0))
        nid = n.get("node_id", "")
        if not host or not port:
            continue
        status, out = node_http(host, port, "/status", timeout=5)
        if status == 200:
            samples[nid] = out
    return samples


def run_sync_loop(
    model_id: str,
    profile: dict[str, Any],
    traces_dir: Path,
    fault_cfg: dict[str, Any] | None = None,
) -> tuple[StageRecord, StageRecord]:
    sync_rec = StageRecord(name="synchronization")
    cov_rec = StageRecord(name="coverage")
    sync_rec.started_at = utc_now()
    t0 = time.perf_counter()

    max_rounds = int(profile.get("sync_max_rounds", 8))
    timeout_s = int(profile.get("sync_timeout_s", 1800))
    retries = 0
    total_bytes = 0
    total_ops = 0
    job_durations: list[float] = []
    blobs_downloaded = blobs_reused = 0
    fault_injected = False

    last_cov: dict[str, Any] = {}
    for round_i in range(max_rounds):
        cov_rec.metrics["rounds"] = round_i + 1
        t_cov = time.perf_counter()
        status, out = http("POST", f"/models/{model_id}/coverage/refresh", timeout=120)
        refresh_ms = (time.perf_counter() - t_cov) * 1000.0
        last_cov = out.get("coverage", {}) if status == 200 else {}
        cov_rec.metrics["refresh_latency_ms"] = round(refresh_ms, 2)

        ready = last_cov.get("ready_layers", 0)
        total = last_cov.get("total_layers", 0)
        missing = last_cov.get("missing_layers", 0)
        state = last_cov.get("state", "")
        cov_rec.metrics.update({
            "state": state,
            "ready_layers": ready,
            "total_layers": total,
            "missing_layers": missing,
        })

        status, plan = http("POST", f"/models/{model_id}/install-plan", timeout=120)
        if status != 200:
            sync_rec.notes.append(f"install-plan error round {round_i + 1}")
            break
        ops = int(plan.get("operation_count", 0) or 0)
        bytes_ = int(plan.get("total_download_bytes", 0) or 0)
        total_ops += ops
        total_bytes += bytes_
        if ops == 0:
            break

        status, out = http("POST", f"/models/{model_id}/install/execute", timeout=120)
        job_id = out.get("job_id", "") if status == 200 else ""
        if not job_id:
            retries += 1
            continue

        if fault_cfg and not fault_injected and fault_cfg.get("fault_stage") == "synchronization":
            time.sleep(2)
            inject_fault(fault_cfg, fetch_cluster_snapshot())
            fault_injected = True
            sync_rec.metrics["fault_injected"] = True

        t_job = time.perf_counter()
        job, _ = wait_install_job(job_id, timeout_s, traces_dir)
        job_durations.append(time.perf_counter() - t_job)
        if job.get("state") != "completed":
            retries += 1
            sync_rec.notes.append(job.get("error", job.get("state", "job incomplete")))
            continue
        blobs_downloaded += ops

    sync_rec.duration_ms = (time.perf_counter() - t0) * 1000.0
    sync_rec.finished_at = utc_now()
    install_s = sum(job_durations) or (sync_rec.duration_ms / 1000.0)
    sync_rec.metrics = {
        "retry_count": retries,
        "operation_count": total_ops,
        "total_download_bytes": total_bytes,
        "blobs_downloaded": blobs_downloaded,
        "blobs_reused": blobs_reused,
        "blobs_repaired": 0,
        "blobs_deleted": 0,
        "install_time_s": round(install_s, 2),
        "download_mbps": round((total_bytes / (1024 ** 2)) / max(install_s, 0.001), 2) if total_bytes else 0,
        "verify_mbps": None,
    }

    cov_rec.started_at = sync_rec.finished_at
    t_rec = time.perf_counter()
    status, out = http("POST", f"/models/{model_id}/reconcile", {}, timeout=60)
    cov_rec.metrics["reconciliation_latency_ms"] = round((time.perf_counter() - t_rec) * 1000.0, 2)
    if status == 200:
        cov_rec.metrics["reconcile_state"] = out.get("state", "")

    cov_rec.metrics["ready_time_ms"] = round(sync_rec.duration_ms, 2)
    cov_rec.duration_ms = cov_rec.metrics.get("refresh_latency_ms", 0) + cov_rec.metrics.get("reconciliation_latency_ms", 0)
    cov_rec.finished_at = utc_now()
    return sync_rec, cov_rec


def inject_fault(fault_cfg: dict[str, Any], cluster: dict[str, Any]) -> dict[str, Any]:
    target = fault_cfg.get("fault_node", "")
    for n in cluster.get("nodes", []):
        if n.get("node_id") == target:
            host = n.get("connect_host") or n.get("host", "")
            port = int(n.get("connect_port") or n.get("port", 0))
            status, out = node_http(host, port, "/shutdown", method="POST", body={}, timeout=10)
            return {"node": target, "status": status, "response": out}
    return {"node": target, "status": 0, "error": "node not found"}


def orchestrator_rss_sample(stage: str) -> dict[str, Any]:
    """Sample orchestrator control-plane RSS (/debug/rss)."""
    status, out = http("GET", "/debug/rss", timeout=5)
    if status != 200 or not isinstance(out, dict):
        return {"stage": stage, "http_status": status}
    return {
        "stage": stage,
        "rss_bytes": out.get("rss_bytes"),
        "rss_mb": out.get("rss_mb"),
        "http_status": status,
    }


def collect_all_node_runtime_stats(cluster: dict[str, Any]) -> dict[str, Any]:
    """Aggregate /status runtime_stats from every online node."""
    out: dict[str, Any] = {}
    totals: dict[str, int] = {}
    for n in cluster.get("nodes", []):
        host = n.get("connect_host") or n.get("host", "")
        port = int(n.get("connect_port") or n.get("port", 0))
        nid = n.get("node_id", "")
        if not host or not port or not nid:
            continue
        status, body = node_http(host, port, "/status", timeout=5)
        stats = body.get("runtime_stats", {}) if status == 200 and isinstance(body, dict) else {}
        out[nid] = stats
        if isinstance(stats, dict):
            for key, val in stats.items():
                if isinstance(val, (int, float)):
                    totals[key] = totals.get(key, 0) + int(val)
    out["_totals"] = totals
    return out


def run_materialization(model_id: str, cluster: dict[str, Any]) -> StageRecord:
    with StageTimer("materialization") as rec:
        before = sample_node_runtime(cluster)
        rec.metrics["runtime_before"] = before
        tensor_count = 0
        worker_bytes = 0
        for nid, st in before.items():
            workers = st.get("workers", {})
            for role, pid in workers.items():
                if pid:
                    rec.metrics.setdefault("workers_started", []).append({"node": nid, "role": role, "pid": pid})
        rec_metrics = model_record(model_id)
        layers = rec_metrics.get("actual", {}).get("layers", [])
        tensor_count = len(layers)
        rec.metrics["tensor_count"] = tensor_count
        rec.metrics["metadata_size"] = len(json.dumps(rec_metrics.get("manifest", {})))
        rec.metrics["worker_gguf_bytes"] = worker_bytes
        rec.metrics["peak_ram_gb"] = None
    return rec


def destroy_session(session_id: str, timeout: int = 60) -> tuple[int, float, dict[str, Any]]:
    """Destroy a session; returns (http_status, duration_ms, response)."""
    if not session_id:
        return 0, 0.0, {"error": "empty session_id"}
    t0 = time.perf_counter()
    status, out = http("DELETE", f"/session/{session_id}", timeout=timeout)
    if status == 404 or status >= 500:
        status, out = http("POST", "/session/destroy", {"session_id": session_id}, timeout=timeout)
    duration_ms = (time.perf_counter() - t0) * 1000.0
    return status, duration_ms, out if isinstance(out, dict) else {"raw": out}


def merge_perf_trace_from_response(response: dict[str, Any], out_dir: Path) -> dict[str, Any] | None:
    """Merge Task 12 perf trace JSONL if trace dir is available locally."""
    timing = response.get("timing") if isinstance(response, dict) else None
    if not isinstance(timing, dict):
        return None
    trace_dir = timing.get("perf_trace_dir")
    if not trace_dir:
        return None
    src = Path(str(trace_dir))
    if not src.is_dir():
        return None
    try:
        sys.path.insert(0, str(BENCH_DIR))
        from perf_trace.merge import merge_trace_dir  # noqa: WPS433

        dst = out_dir / "perf_trace" / str(timing.get("trace_id", "unknown"))
        return merge_trace_dir(src, dst)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "trace_dir": str(src)}


def _primary_scenario(scenarios: list[dict[str, Any]]) -> dict[str, Any] | None:
    for sc in scenarios:
        if sc.get("skipped") or sc.get("error"):
            continue
        return sc
    return scenarios[0] if scenarios else None


def run_perf_trace_bundle(
        out_dir: Path,
        cluster: dict[str, Any],
        *,
        profile_name: str,
        scenarios: list[dict[str, Any]],
        pin_if_missing: bool = True,
) -> dict[str, Any] | None:
    """Collect raw JSONL traces and run Task 12 post-processing pipeline."""
    sys.path.insert(0, str(BENCH_DIR))
    from perf_trace.collect import collect_traces  # noqa: WPS433
    from perf_trace.postprocess import run_postprocess  # noqa: WPS433

    raw_dir = out_dir / "perf_trace" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    collected = collect_traces(raw_dir, orchestrator=ORCH, cluster=cluster)
    log(f"Perf trace: collected {collected} JSONL file(s) -> {raw_dir}")
    if collected < 1:
        return None

    primary = _primary_scenario(scenarios) or {}
    model_key = str(primary.get("model_key", "tinyllama"))
    cluster_size = int(primary.get("cluster_size_target", cluster.get("node_count", 3)))

    doc = run_postprocess(
        raw_dir,
        out_dir / "perf_trace",
        profile=profile_name,
        model=model_key,
        cluster_size=cluster_size,
        pin_if_missing=pin_if_missing,
    )
    log(f"Perf trace: analysis -> {doc.get('analysis_dir')}")
    if doc.get("timeline_html"):
        log(f"Perf trace: timeline -> {doc['timeline_html']}")
    return doc


def run_generate_stage(
    session_id: str,
    prompt: str,
    max_tokens: int,
    label: str = "generate",
) -> StageRecord:
    rec = StageRecord(name=label)
    rec.started_at = utc_now()
    t0 = time.perf_counter()
    status, out = http("POST", "/session/generate", {
        "session_id": session_id,
        "prompt": prompt,
        "max_tokens": max_tokens,
    }, timeout=600)
    rec.duration_ms = (time.perf_counter() - t0) * 1000.0
    rec.finished_at = utc_now()
    rec.response = out if isinstance(out, dict) else {"raw": out}
    count = int(out.get("count", len(out.get("tokens", []))) if isinstance(out, dict) else 0)
    elapsed_s = rec.duration_ms / 1000.0
    rec.metrics = {
        "http_status": status,
        "token_count": count,
        "tokens_per_sec": round(count / elapsed_s, 2) if elapsed_s > 0 and count else 0,
        "text_length": len(out.get("text", "") if isinstance(out, dict) else ""),
        "latency_ms": round(rec.duration_ms, 2),
    }
    if status != 200 and isinstance(out, dict):
        rec.notes.append(str(out.get("error", out)))
    if isinstance(out, dict) and isinstance(out.get("timing"), dict):
        rec.metrics["timing"] = out["timing"]
    return rec


def run_prefill_decode_estimates(session_id: str, prompt: str) -> dict[str, Any]:
    long_prompt = make_prompt(prompt, max(len(prompt) * 4, 128))
    pre = run_generate_stage(session_id, long_prompt, 1, label="prefill_probe")
    dec = run_generate_stage(session_id, "Hi", 32, label="decode_probe")
    return {
        "prefill_ms": pre.duration_ms,
        "decode_ms": dec.duration_ms / max(dec.metrics.get("token_count", 1), 1),
        "prefill_tokens_per_sec": pre.metrics.get("tokens_per_sec"),
        "decode_tokens_per_sec": dec.metrics.get("tokens_per_sec"),
    }


def run_stress(session_id: str, prompt: str, max_tokens: int, requests: int, warmup: int) -> dict[str, Any]:
    for _ in range(warmup):
        http("POST", "/session/generate", {
            "session_id": session_id,
            "prompt": prompt,
            "max_tokens": 4,
        }, timeout=120)

    tps_samples = []
    t0 = time.perf_counter()
    errors = 0
    total_tokens = 0
    for i in range(requests):
        status, out = http("POST", "/session/generate", {
            "session_id": session_id,
            "prompt": prompt,
            "max_tokens": max_tokens,
        }, timeout=600)
        if status != 200:
            errors += 1
            continue
        count = int(out.get("count", len(out.get("tokens", []))))
        total_tokens += count
        tps_samples.append(count)
    elapsed = time.perf_counter() - t0
    return {
        "request_count": requests,
        "errors": errors,
        "total_tokens": total_tokens,
        "elapsed_s": round(elapsed, 2),
        "tokens_per_sec_avg": round(total_tokens / elapsed, 2) if elapsed > 0 else 0,
        "throughput_rps": round(requests / elapsed, 2) if elapsed > 0 else 0,
        "token_samples": tps_samples[:20],
    }


def run_scenario(
    model_key: str,
    model_cfg: dict[str, Any],
    matrix_row: dict[str, Any],
    profile: dict[str, Any],
    run_id: str,
    traces_dir: Path,
    cluster: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    model_id = model_cfg["model_id"]
    scenario_id = (
        f"{model_key}_c{matrix_row.get('cluster_size_target')}"
        f"_p{matrix_row.get('prompt_length')}_g{matrix_row.get('generate_tokens')}"
    )
    log(f"\n=== benchmark {scenario_id} ===")

    min_nodes = int(matrix_row.get("cluster_size_target", 3))
    ok, cluster = recover_docker_cluster(
        min_nodes,
        int(profile.get("wait_cluster_timeout_s", 120)),
    )
    if not ok:
        notes.append(
            f"cluster has {cluster.get('node_count', 0)} nodes, need {min_nodes} — scenario may fail"
        )

    if profile.get("release_workers_before_run", True):
        release_cluster_workers(cluster)

    stages: list[StageRecord] = []
    started_at = utc_now()
    notes: list[str] = []
    stress_result: dict[str, Any] = {}
    fault_result: dict[str, Any] = {}
    session_id = ""

    # Register
    with StageTimer("register") as reg:
        if profile.get("reset_before_run", True):
            http("POST", f"/models/{model_id}/reset", {"keep_manifest": False}, timeout=180)
        status, out = http("POST", "/models/register", {
            "model_id": model_id,
            "display_name": model_cfg.get("label", model_key),
            "source": "huggingface",
            "repository": model_cfg["repository"],
            "filename": model_cfg["filename"],
            "revision": model_cfg.get("revision", "main"),
        }, timeout=120)
        reg.metrics = {"http_status": status, "already_registered": status == 409}
        reg.response = out if isinstance(out, dict) else {}
    stages.append(reg)

    # Discovery
    with StageTimer("discovery") as disc:
        status, out = http("POST", f"/models/{model_id}/discover", {}, timeout=180)
        disc.metrics = {"http_status": status, "files": out.get("files") if isinstance(out, dict) else None}
        disc.response = out if isinstance(out, dict) else {}
    stages.append(disc)

    # Manifest
    with StageTimer("manifest") as man:
        status, out = http("POST", f"/models/{model_id}/manifest", {}, timeout=180)
        man.metrics = {
            "http_status": status,
            "architecture": out.get("architecture") if isinstance(out, dict) else None,
            "n_layer": out.get("n_layer") if isinstance(out, dict) else None,
        }
        man.response = out if isinstance(out, dict) else {}
    stages.append(man)

    # Layout (planner)
    with StageTimer("layout") as lay:
        status, out = http("POST", f"/models/{model_id}/layout", {"force": True}, timeout=120)
        lay.response = out if isinstance(out, dict) else {}
        lay.metrics = extract_planner_metrics(out if isinstance(out, dict) else {}, cluster)
        lay.metrics["http_status"] = status
    stages.append(lay)

    fits = lay.metrics.get("fits_cluster")
    stop_if_not_fits = profile.get("stop_if_not_fits", mode == "large")
    if fits is False and stop_if_not_fits:
        notes.append("planner: fits_cluster=false — remaining stages skipped")
        finished_at = utc_now()
        return {
            "run_id": run_id,
            "scenario_id": scenario_id,
            "model_key": model_key,
            "model_id": model_id,
            "mode": mode,
            "cluster_size_target": matrix_row.get("cluster_size_target"),
            "cluster_size_observed": cluster.get("node_count"),
            "prompt_length": matrix_row.get("prompt_length"),
            "generate_tokens": matrix_row.get("generate_tokens"),
            "started_at": started_at,
            "finished_at": finished_at,
            "stages": [s.to_dict() for s in stages],
            "notes": notes,
            "skipped_after_layout": True,
        }

    # Install plan
    with StageTimer("install_plan") as iplan:
        status, out = http("POST", f"/models/{model_id}/install-plan", {}, timeout=120)
        iplan.metrics = extract_install_plan_metrics(out if isinstance(out, dict) else {})
        iplan.metrics["http_status"] = status
        iplan.response = out if isinstance(out, dict) else {}
    stages.append(iplan)

    # Synchronization + Coverage
    fault_cfg = profile if mode == "fault" else None
    sync_rec, cov_rec = run_sync_loop(model_id, profile, traces_dir, fault_cfg)
    stages.extend([sync_rec, cov_rec])

    if mode == "fault" and fault_cfg:
        t_fault = time.perf_counter()
        snap = fetch_cluster_snapshot()
        target = fault_cfg.get("fault_node", "")
        online = {n["node_id"] for n in snap.get("nodes", [])}
        deadline = time.time() + 300
        while time.time() < deadline:
            snap = fetch_cluster_snapshot()
            online = {n["node_id"] for n in snap.get("nodes", [])}
            if target in online:
                break
            time.sleep(5)
        fault_result = {
            "fault_node": target,
            "recovery_ms": round((time.perf_counter() - t_fault) * 1000.0, 2),
            "node_back_online": target in online,
        }

    # Materialization
    stages.append(run_materialization(model_id, cluster))

    # Session create
    n_ctx = int(profile.get("n_ctx", 512))
    session_create_timeout_s = int(profile.get(
        "session_create_timeout_s",
        900 if os.environ.get("DIST_RUNTIME_LAYER_FIRST", "0") == "1" else 300,
    ))
    with StageTimer("session_create") as sess:
        status, out = http(
            "POST",
            "/session/create",
            {"model": model_id, "n_ctx": n_ctx},
            timeout=session_create_timeout_s,
        )
        sess.response = out if isinstance(out, dict) else {}
        session_id = out.get("session_id", "") if isinstance(out, dict) else ""
    sess.metrics = {
        "http_status": status,
        "session_id": session_id,
        "pipeline_nodes": len(out.get("pipeline", [])) if isinstance(out, dict) else 0,
        "memory": out.get("memory", {}) if isinstance(out, dict) else {},
        "configure_time_ms": round(sess.duration_ms, 2),
    }
    if isinstance(out, dict):
        sess.metrics["runtime_graph"] = out.get("runtime_graph")
        sess.metrics["orchestrator_rss"] = orchestrator_rss_sample("session_create")
        sess.metrics["node_runtime_stats"] = collect_all_node_runtime_stats(cluster)
        totals = sess.metrics["node_runtime_stats"].get("_totals", {})
        sess.metrics["materialization_count"] = totals.get("materialization_count", 0)
        sess.metrics["runtime_load_count"] = totals.get("runtime_load_count", 0)
        sess.metrics["layer_first_ok"] = totals.get("materialization_count", 0) == 0
    stages.append(sess)

    prompt_len = int(matrix_row.get("prompt_length", 16))
    gen_tokens = int(matrix_row.get("generate_tokens", 32))
    base_prompt = profile.get("base_prompt", "The capital of France is")
    prompt = make_prompt(base_prompt, prompt_len)

    runtime_before = sample_node_runtime(cluster)

    # Warmup
    warmup_tokens = int(profile.get("warmup_tokens", 4))
    if session_id and warmup_tokens > 0:
        warmup = run_generate_stage(session_id, prompt, warmup_tokens, label="warmup")
        stages.append(warmup)

    # Generate
    if session_id:
        gen = run_generate_stage(session_id, prompt, gen_tokens, label="generate")
        if gen.metrics.get("http_status") == 200:
            estimates = run_prefill_decode_estimates(session_id, base_prompt)
            gen.metrics.update(estimates)
        gen.metrics["runtime_before"] = runtime_before
        gen.metrics["runtime_after"] = sample_node_runtime(cluster)
        if profile.get("perf_trace") and isinstance(gen.response, dict):
            perf_doc = merge_perf_trace_from_response(gen.response, traces_dir.parent)
            if perf_doc:
                gen.metrics["perf_trace"] = {
                    "event_count": perf_doc.get("event_count"),
                    "token_count": perf_doc.get("token_count"),
                    "bottleneck_pct": (perf_doc.get("bottleneck") or {}).get("category_pct"),
                }
        stages.append(gen)

    if mode == "stress" and session_id:
        requests = int(os.environ.get("BENCHMARK_STRESS_REQUESTS", profile.get("stress_requests", 100)))
        warmup = int(profile.get("stress_warmup", 5))
        stress_result = run_stress(session_id, prompt, gen_tokens, requests, warmup)

    # Cleanup — destroy session and release worker memory on nodes.
    with StageTimer("cleanup") as clean:
        if session_id:
            status, destroy_ms, destroy_out = destroy_session(session_id)
            clean.metrics = {
                "session_id": session_id,
                "destroy_status": status,
                "destroy_ms": round(destroy_ms, 2),
            }
            if isinstance(destroy_out, dict) and destroy_out.get("error"):
                clean.notes.append(str(destroy_out["error"]))
        else:
            clean.metrics = {"session_id": ""}
        worker_release = release_cluster_workers(cluster)
        clean.metrics["worker_shutdown"] = worker_release
    stages.append(clean)

    finished_at = utc_now()
    return {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "model_key": model_key,
        "model_id": model_id,
        "mode": mode,
        "cluster_size_target": matrix_row.get("cluster_size_target"),
        "cluster_size_observed": cluster.get("node_count"),
        "prompt_length": prompt_len,
        "generate_tokens": gen_tokens,
        "started_at": started_at,
        "finished_at": finished_at,
        "stages": [s.to_dict() for s in stages],
        "stress": stress_result,
        "fault": fault_result,
        "notes": notes,
    }


def expand_matrix(profile: dict[str, Any], models_catalog: dict[str, Any],
                  model_filter: str | None = None) -> list[dict[str, Any]]:
    model_keys = profile.get("models", [])
    if model_filter:
        model_keys = [k for k in model_keys if k == model_filter or models_catalog.get(k, {}).get("model_id") == model_filter]
    rows = []
    for mk in model_keys:
        if mk not in models_catalog:
            continue
        for cs in profile.get("cluster_sizes", [3]):
            for pl in profile.get("prompt_lengths", [16]):
                for gt in profile.get("generate_tokens", [32]):
                    rows.append({
                        "model_key": mk,
                        "cluster_size_target": cs,
                        "prompt_length": pl,
                        "generate_tokens": gt,
                    })
    return rows


PERF_PROFILES = frozenset({"perf", "perf_smoke", "smoke", "quick", "cold", "warm", "warm_ext", "scaling"})
PERF_MODES = frozenset({"cold", "warm", "scaling", "perf", "runtime-only", "smoke", "quick"})


def is_perf_run(profile_name: str, mode: str) -> bool:
    return profile_name in PERF_PROFILES or mode in PERF_MODES


def run_perf_main(args: argparse.Namespace) -> int:
    from benchmark_export import collect_git_metadata, default_output_dir, make_run_id, write_json
    from benchmark_perf import PerfOptions, run_perf_suite
    from benchmark_report_perf import write_perf_reports

    matrix_doc = load_yaml_file(BENCH_DIR / "benchmark_matrix_perf.yaml")
    models_doc = load_yaml_file(BENCH_DIR / "benchmark_models.yaml")
    models_catalog = models_doc.get("models", {})
    defaults = matrix_doc.get("defaults", {})
    profiles = matrix_doc.get("profiles", {})
    profile_name = args.profile if args.profile in profiles else args.mode if args.mode in profiles else "perf_smoke"
    profile = {**defaults, **profiles.get(profile_name, profiles.get("perf_smoke", {}))}
    mode = args.mode or profile.get("mode", profile_name)

    persistent = profile.get("persistent_session", True)
    if getattr(args, "persistent_session", None) is not None:
        persistent = args.persistent_session
    warmup = profile.get("warmup", True)
    if getattr(args, "warmup", None) is not None:
        warmup = args.warmup

    opts = PerfOptions(
        persistent_session=persistent,
        warmup=warmup,
        runtime_only=getattr(args, "runtime_only", False) or profile_name == "runtime-only",
        infra_only=getattr(args, "infra_only", False),
        generations=getattr(args, "generations", None),
        prompt_profile=getattr(args, "prompt_profile", None),
        verify_session=not getattr(args, "no_verify", False),
        resume=getattr(args, "resume", False),
    )

    load_hf_token()
    run_id = make_run_id()
    out_dir = Path(args.output_dir) if args.output_dir else default_output_dir(run_id)
    if opts.resume and getattr(args, "resume_dir", None):
        out_dir = Path(args.resume_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log(f"Performance Benchmark 10.1.2 — profile={profile_name} mode={mode}")
    log(f"  persistent_session={opts.persistent_session} generations={opts.generations or profile.get('generations', 20)}")
    if opts.runtime_only:
        log("  runtime_only=True (skip full infra pipeline)")
    if opts.infra_only:
        log("  infra_only=True (skip runtime benchmark)")
    log(f"Orchestrator: {ORCH}")
    log(f"Output: {out_dir}")

    document = run_perf_suite(
        profile=profile,
        profile_name=profile_name,
        mode=mode,
        models_catalog=models_catalog,
        run_id=run_id,
        out_dir=out_dir,
        model_filter=args.model,
        cluster_size_filter=args.cluster_size,
        log=log,
        opts=opts,
    )
    if document.get("error"):
        log(document["error"])
        return 1

    write_json(out_dir / "results.json", document)
    write_perf_reports(out_dir, document)
    log(f"\nSaved: {out_dir / 'results.json'}")
    log(f"Saved: {out_dir / 'results_perf.csv'}")
    log(f"Saved: {out_dir / 'report.md'}")
    log(f"Saved: {out_dir / 'comparison.json'}")
    log(f"Scenarios: {document.get('summary', {}).get('scenario_count', 0)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Cluster Benchmark Suite")
    parser.add_argument("--profile", default=os.environ.get("BENCHMARK_PROFILE", "default"),
                        help="Matrix profile from benchmark_matrix.yaml (default, ci, scaling, ...)")
    parser.add_argument("--mode", default=None, help="Override profile mode")
    parser.add_argument("--model", help="Run single model key or model_id")
    parser.add_argument("--cluster-size", type=int, help="Filter matrix to one cluster size")
    parser.add_argument("--output-dir", help="Output directory (default logs/benchmark/YYYYMMDD_HHMMSS)")
    parser.add_argument("--list-models", action="store_true", help="List model keys and exit")
    parser.add_argument("--regression-threshold", type=float,
                        default=float(os.environ.get("BENCHMARK_REGRESSION_PCT", "10")),
                        help="Regression threshold %% for benchmark_compare")
    parser.add_argument("--persistent-session", action=argparse.BooleanOptionalAction, default=None,
                        help="Reuse one session per model (default from YAML profile)")
    parser.add_argument("--warmup", action=argparse.BooleanOptionalAction, default=None,
                        help="Run warmup generate before benchmark loop")
    parser.add_argument("--runtime-only", action="store_true",
                        help="Skip full infra pipeline; session create + runtime only")
    parser.add_argument("--infra-only", action="store_true",
                        help="Run infrastructure benchmark only (no runtime generations)")
    parser.add_argument("--generations", type=int, default=None,
                        help="Number of generate calls per session (default: profile generations)")
    parser.add_argument("--prompt-profile", choices=["short", "medium", "long", "code", "chat", "reasoning"],
                        default=None, help="Prompt profile (YAML-defined categories)")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint in output dir")
    parser.add_argument("--resume-dir", help="Checkpoint directory for --resume")
    parser.add_argument("--no-verify", action="store_true", help="Disable session reuse verification")
    parser.add_argument("--profile-runtime", action="store_true",
                        help="Enable Task 12 perf trace (DIST_PERF_TRACE=1) and merge decode timeline")
    args = parser.parse_args()

    models_doc = load_yaml_file(BENCH_DIR / "benchmark_models.yaml")
    models_catalog = models_doc.get("models", {})

    if args.list_models:
        for key, cfg in models_catalog.items():
            log(f"  {key}: {cfg.get('model_id')} ({cfg.get('label', '')})")
        return 0

    mode_early = args.mode or os.environ.get("BENCHMARK_MODE", "")
    profile_early = args.profile
    if is_perf_run(profile_early, mode_early):
        return run_perf_main(args)

    matrix_doc = load_yaml_file(BENCH_DIR / "benchmark_matrix.yaml")
    defaults = matrix_doc.get("defaults", {})
    profiles = matrix_doc.get("profiles", {})
    profile = {**defaults, **profiles.get(args.profile, profiles.get("default", {}))}
    mode = args.mode or profile.get("mode", "default")
    if args.profile_runtime:
        os.environ["DIST_PERF_TRACE"] = "1"
        os.environ["DIST_PERF_TRACE_GGML"] = "1"
        profile["perf_trace"] = True
        if os.environ.get("BENCHMARK_DOCKER", "1") == "1" and os.environ.get("BENCHMARK_SKIP_DOCKER_PERF_RECREATE", "0") != "1":
            ensure_docker_perf_trace_enabled()

    load_hf_token()
    run_id = make_run_id()
    out_dir = Path(args.output_dir) if args.output_dir else default_output_dir(run_id)
    traces_dir = out_dir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    log(f"Cluster Benchmark Suite — profile={args.profile} mode={mode}")
    log(f"Orchestrator: {ORCH}")
    log(f"Output: {out_dir}")

    min_nodes = min(profile.get("cluster_sizes", [3])) if mode != "scaling" else 2
    ok, cluster = wait_cluster(min_nodes, int(profile.get("wait_cluster_timeout_s", 120)))
    if not ok:
        log(f"Cluster has {cluster.get('node_count', 0)} nodes (need >={min_nodes})")
        return 1
    log(f"Cluster: {cluster['node_count']} nodes, "
        f"{cluster['memory']['free_total_gb']} GB free / {cluster['memory']['total_total_gb']} GB total")

    rows = expand_matrix(profile, models_catalog, args.model)
    if args.cluster_size is not None:
        rows = [r for r in rows if r["cluster_size_target"] == args.cluster_size]
    if not rows:
        log("No scenarios to run (check --model / profile)")
        return 2

    scenarios: list[dict[str, Any]] = []
    for row in rows:
        mk = row["model_key"]
        cfg = models_catalog[mk]
        target = row["cluster_size_target"]
        if cluster["node_count"] < target:
            scenarios.append({
                "scenario_id": f"{mk}_c{target}_skipped",
                "model_key": mk,
                "model_id": cfg["model_id"],
                "cluster_size_target": target,
                "cluster_size_observed": cluster["node_count"],
                "skipped": True,
                "notes": [f"cluster has {cluster['node_count']} nodes, need {target}"],
                "stages": [],
            })
            continue
        cluster = fetch_cluster_snapshot()
        min_nodes = int(row["cluster_size_target"])
        ok, cluster = wait_cluster(min_nodes, int(profile.get("wait_cluster_timeout_s", 120)))
        if not ok:
            scenarios.append({
                "run_id": run_id,
                "scenario_id": f"{mk}_c{target}_cluster_wait",
                "model_key": mk,
                "model_id": cfg["model_id"],
                "cluster_size_target": target,
                "cluster_size_observed": cluster.get("node_count", 0),
                "error": f"cluster has {cluster.get('node_count', 0)} nodes, need {min_nodes}",
                "stages": [],
            })
            continue
        try:
            sc = run_scenario(mk, cfg, row, profile, run_id, traces_dir, cluster, mode)
        except Exception as exc:  # noqa: BLE001
            sc = {
                "run_id": run_id,
                "scenario_id": f"{mk}_c{target}_error",
                "model_key": mk,
                "model_id": cfg["model_id"],
                "cluster_size_target": target,
                "cluster_size_observed": cluster.get("node_count"),
                "error": str(exc),
                "stages": [],
            }
        scenarios.append(sc)
        write_trace(traces_dir, sc.get("scenario_id", mk), sc)
        try:
            cluster = inter_scenario_cleanup(profile, cluster, sc, min_nodes)
        except Exception as exc:  # noqa: BLE001
            log(f"Warning: inter-scenario cleanup failed after {mk}: {exc}")
            _, cluster = recover_docker_cluster(
                min_nodes,
                int(profile.get("wait_cluster_timeout_s", 120)),
            )

    metadata = collect_git_metadata()
    perf_trace_doc: dict[str, Any] | None = None
    if args.profile_runtime or profile.get("perf_trace"):
        perf_trace_doc = run_perf_trace_bundle(
            out_dir,
            cluster,
            profile_name=args.profile,
            scenarios=scenarios,
            pin_if_missing=True,
        )

    document = build_results_document(
        run_id=run_id,
        profile=args.profile,
        mode=mode,
        orchestrator=ORCH,
        cluster=cluster,
        scenarios=scenarios,
        metadata=metadata,
        perf_trace=perf_trace_doc,
    )
    paths = save_results_bundle(out_dir, document)
    write_reports(out_dir, document)
    log(f"\nSaved: {paths['results_json']}")
    log(f"Saved: {paths['results_csv']}")
    log(f"Saved: {out_dir / 'report.md'}")
    log(f"Saved: {out_dir / 'report.html'}")
    if perf_trace_doc and perf_trace_doc.get("timeline_html"):
        log(f"Saved: {perf_trace_doc['timeline_html']}")
    if perf_trace_doc and (out_dir / "perf_trace" / "analysis" / "report.md").is_file():
        log(f"Saved: {out_dir / 'perf_trace' / 'analysis' / 'report.md'}")
    log(f"Scenarios: {len(scenarios)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
