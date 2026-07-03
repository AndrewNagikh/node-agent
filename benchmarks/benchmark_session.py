#!/usr/bin/env python3
"""Session verification and debug snapshots for Task 10.1.2."""

from __future__ import annotations

from typing import Any

import benchmark_runner as br

COUNTER_KEYS = (
    "configure_count",
    "materialization_count",
    "worker_spawn_count",
    "pipeline_generate_count",
    "kv_cache_reset_count",
    "context_create_count",
    "runtime_load_count",
    "tokenizer_init_count",
    "materialization_generation",
)


def fetch_session_debug(session_id: str) -> dict[str, Any]:
    status, out = br.http("GET", f"/session/{session_id}", timeout=15)
    if status != 200 or not isinstance(out, dict):
        return {"error": out, "http_status": status, "session_id": session_id}
    return out


def _worker_pids_from_status(status: dict[str, Any]) -> dict[str, int]:
    workers = status.get("workers", {})
    if isinstance(workers, dict):
        return {k: int(v or 0) for k, v in workers.items()}
    return {}


def _stats_from_status(status: dict[str, Any]) -> dict[str, int]:
    stats = status.get("runtime_stats", {})
    if not isinstance(stats, dict):
        stats = {}
    out: dict[str, int] = {}
    for key in COUNTER_KEYS:
        if key in stats and stats[key] is not None:
            out[key] = int(stats[key])
    return out


def collect_cluster_snapshot(
    session_id: str,
    pipeline: list[dict[str, Any]],
    cluster: dict[str, Any],
) -> dict[str, Any]:
    """Snapshot session + per-node worker PIDs and runtime counters."""
    snap: dict[str, Any] = {
        "session_id": session_id,
        "session": fetch_session_debug(session_id),
        "nodes": {},
    }
    sess = snap["session"]
    if isinstance(sess, dict):
        snap["tokenizer_init_count"] = int(sess.get("tokenizer_init_count", 0))
        snap["generate_count"] = int(sess.get("generate_count", 0))
        snap["configure_count"] = int(sess.get("configure_count", 0))

    node_hosts = {n.get("node_id"): n for n in cluster.get("nodes", [])}
    for stage in pipeline:
        nid = stage.get("node_id", "")
        host = stage.get("host", "")
        port = int(stage.get("http_port", stage.get("port", 0)))
        if not host and nid in node_hosts:
            host = node_hosts[nid].get("host", "")
            port = int(node_hosts[nid].get("port", port))
        if not host or not port:
            continue
        st, status = br.node_http(host, port, "/status", timeout=5)
        snap["nodes"][nid] = {
            "role": stage.get("role"),
            "host": host,
            "port": port,
            "http_status": st,
            "status": status if st == 200 else {},
            "worker_pids": _worker_pids_from_status(status if isinstance(status, dict) else {}),
            "runtime_stats": _stats_from_status(status if isinstance(status, dict) else {}),
        }
    return snap


def _aggregate_counters(snap: dict[str, Any]) -> dict[str, int]:
    totals: dict[str, int] = {}
    if snap.get("tokenizer_init_count") is not None:
        totals["tokenizer_init_count"] = int(snap["tokenizer_init_count"])
    if snap.get("configure_count") is not None:
        totals["configure_count"] = int(snap["configure_count"])
    for node in snap.get("nodes", {}).values():
        for key, val in node.get("runtime_stats", {}).items():
            totals[key] = totals.get(key, 0) + int(val)
    return totals


def verify_against_baseline(
    baseline: dict[str, Any] | None,
    current: dict[str, Any],
    generation_index: int,
) -> dict[str, Any]:
    """After generation 0, counters and session_id must remain stable."""
    result: dict[str, Any] = {
        "generation_index": generation_index,
        "session_id": current.get("session_id"),
        "valid": True,
        "reasons": [],
        "session_reused": True,
        "worker_reused": True,
        "materialization_reused": True,
        "tokenizer_reused": True,
        "runtime_reused": True,
    }
    if generation_index == 0 or not baseline:
        result["baseline"] = True
        return result

    if baseline.get("session_id") != current.get("session_id"):
        result["valid"] = False
        result["session_reused"] = False
        result["reasons"].append("Session recreated unexpectedly")

    base_pids: dict[str, dict[str, int]] = {}
    cur_pids: dict[str, dict[str, int]] = {}
    for nid, nd in baseline.get("nodes", {}).items():
        base_pids[nid] = nd.get("worker_pids", {})
    for nid, nd in current.get("nodes", {}).items():
        cur_pids[nid] = nd.get("worker_pids", {})

    for nid, pids in base_pids.items():
        cur = cur_pids.get(nid, {})
        for role, pid in pids.items():
            if pid and cur.get(role) and cur.get(role) != pid:
                result["valid"] = False
                result["worker_reused"] = False
                result["reasons"].append(f"Worker PID changed on {nid}/{role}: {pid} -> {cur.get(role)}")

    base_counts = _aggregate_counters(baseline)
    cur_counts = _aggregate_counters(current)
    for key in COUNTER_KEYS:
        b = base_counts.get(key)
        c = cur_counts.get(key)
        if b is None or c is None:
            continue
        if c > b:
            if key == "tokenizer_init_count":
                result["valid"] = False
                result["tokenizer_reused"] = False
                result["reasons"].append(f"tokenizer_init_count increased: {b} -> {c}")
            elif key in ("configure_count", "worker_spawn_count"):
                result["valid"] = False
                result["reasons"].append(f"{key} increased: {b} -> {c}")
            elif key == "materialization_count":
                result["valid"] = False
                result["materialization_reused"] = False
                result["reasons"].append(f"materialization_count increased: {b} -> {c}")
            elif key == "runtime_load_count":
                result["valid"] = False
                result["runtime_reused"] = False
                result["reasons"].append(f"runtime_load_count increased: {b} -> {c}")

    return result


def format_generation_debug(
    generation_index: int,
    snap: dict[str, Any],
    verification: dict[str, Any] | None = None,
) -> str:
    lines = [f"Generation #{generation_index + 1}", f"  session_id = {snap.get('session_id')}"]
    counts = _aggregate_counters(snap)
    for nid, nd in sorted(snap.get("nodes", {}).items()):
        pids = nd.get("worker_pids", {})
        role = nd.get("role", nid)
        pid = pids.get(role) or pids.get("entry") or pids.get("middle") or pids.get("final") or 0
        lines.append(f"  {role} worker pid ({nid}) = {pid}")
    lines.append(f"  configure_count = {counts.get('configure_count', 0)}")
    lines.append(f"  materialization_count = {counts.get('materialization_count', 0)}")
    lines.append(f"  runtime_load_count = {counts.get('runtime_load_count', 0)}")
    lines.append(f"  tokenizer_init_count = {counts.get('tokenizer_init_count', 0)}")
    lines.append(f"  context_create_count = {counts.get('context_create_count', 0)}")
    lines.append(f"  kv_cache_reset_count = {counts.get('kv_cache_reset_count', 0)}")
    mat = counts.get("materialization_count", 0) > 0
    lines.append(f"  materialized = {'yes' if mat else 'no'}")
    lines.append(f"  runtime loaded = {'yes' if counts.get('runtime_load_count', 0) > 0 else 'no'}")
    lines.append(f"  tokenizer initialized = {'yes' if counts.get('tokenizer_init_count', 0) > 0 else 'no'}")
    lines.append(f"  context recreated = {'yes' if counts.get('context_create_count', 0) > generation_index + 1 else 'no'}")
    if verification and not verification.get("valid", True):
        lines.append(f"  VERIFICATION FAIL: {'; '.join(verification.get('reasons', []))}")
    return "\n".join(lines)
