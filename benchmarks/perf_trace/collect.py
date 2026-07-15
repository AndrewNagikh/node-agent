#!/usr/bin/env python3
"""Collect Task 12 perf trace JSONL from Docker or LAN cluster."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_DOCKER_CONTAINERS = (
    "dist-orchestrator",
    "dist-node-a",
    "dist-node-b",
    "dist-node-c",
)


def _http_json(url: str, *, timeout: int = 30) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {"error": str(exc)}
        except json.JSONDecodeError:
            payload = {"error": raw or str(exc)}
        return exc.code, payload
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, {"error": str(exc)}


def _http_bytes(url: str, *, timeout: int = 60) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"Accept": "application/x-ndjson"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, b""


def _safe_dest_name(node_id: str, rel: str) -> str:
    rel_name = rel.replace("/", "_")
    return f"{node_id}_{rel_name}"


def collect_docker_traces(
        dest: Path,
        *,
        containers: tuple[str, ...] | None = None,
) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    found = 0
    targets = containers or DEFAULT_DOCKER_CONTAINERS
    for ctr in targets:
        proc = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if ctr not in proc.stdout.splitlines():
            continue
        list_proc = subprocess.run(
            ["docker", "exec", ctr, "sh", "-c",
             "find /data/models/perf_trace -name '*.jsonl' 2>/dev/null || true"],
            capture_output=True,
            text=True,
            check=False,
        )
        for remote in list_proc.stdout.splitlines():
            remote = remote.strip()
            if not remote:
                continue
            # Keep the trace subdirs in the name: decode/ and ttft/ hold files
            # with identical basenames that would otherwise overwrite each other.
            rel = remote.removeprefix("/data/models/perf_trace/")
            base = rel.replace("/", "_")
            out_path = dest / f"{ctr}_{base}"
            cp = subprocess.run(
                ["docker", "cp", f"{ctr}:{remote}", str(out_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if cp.returncode == 0 and out_path.is_file():
                found += 1
    return found


def collect_http_endpoint(
        dest: Path,
        *,
        host: str,
        port: int,
        node_id: str,
        min_mtime_unix: float | None = None,
) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    base = f"http://{host}:{port}"
    status, listing = _http_json(f"{base}/perf/trace/list")
    if status != 200:
        return 0
    found = 0
    for item in listing.get("files", []):
        rel = str(item.get("rel", ""))
        if not rel:
            continue
        if min_mtime_unix is not None:
            mtime = item.get("mtime_unix")
            # Nodes running an older build report no mtime; keep those files
            # rather than silently collecting nothing.
            if isinstance(mtime, (int, float)) and 0 < mtime < min_mtime_unix:
                continue
        q = urllib.parse.urlencode({"rel": rel})
        fstatus, payload = _http_bytes(f"{base}/perf/trace/file?{q}")
        if fstatus != 200 or not payload:
            continue
        out_path = dest / _safe_dest_name(node_id, rel)
        out_path.write_bytes(payload)
        found += 1
    return found


def collect_http_traces(
        dest: Path,
        orchestrator: str,
        cluster: dict[str, Any],
        *,
        min_mtime_unix: float | None = None,
) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    found = 0
    parsed = urlparse(orchestrator)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 9000)
    found += collect_http_endpoint(
        dest, host=host, port=port, node_id="orchestrator", min_mtime_unix=min_mtime_unix)

    seen: set[tuple[str, int]] = {(host, port)}
    for node in cluster.get("nodes", []):
        nh = node.get("connect_host") or node.get("host", "")
        np = int(node.get("connect_port") or node.get("port", 0) or 0)
        nid = str(node.get("node_id", nh))
        if not nh or not np or (nh, np) in seen:
            continue
        seen.add((nh, np))
        found += collect_http_endpoint(
            dest, host=nh, port=np, node_id=nid, min_mtime_unix=min_mtime_unix)
    return found


def collect_local_dir(src: Path, dest: Path, *, min_mtime_unix: float | None = None) -> int:
    if not src.is_dir():
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    found = 0
    for path in sorted(src.rglob("*.jsonl")):
        if min_mtime_unix is not None and path.stat().st_mtime < min_mtime_unix:
            continue
        rel = path.relative_to(src)
        out = dest / rel.name if rel.parent == Path(".") else dest / str(rel).replace("/", "_")
        shutil.copy2(path, out)
        found += 1
    return found


def collect_traces(
        dest: Path,
        *,
        orchestrator: str = "",
        cluster: dict[str, Any] | None = None,
        min_mtime_unix: float | None = None,
) -> int:
    """Collect traces using Docker, HTTP cluster export, or local override.

    Files whose mtime predates ``min_mtime_unix`` (typically the benchmark run
    start) are skipped: analyzers silently PASS on stale traces from earlier
    runs otherwise.
    """
    local = os.environ.get("PERF_TRACE_LOCAL_DIR", "").strip()
    if local:
        return collect_local_dir(Path(local), dest, min_mtime_unix=min_mtime_unix)

    if os.environ.get("BENCHMARK_DOCKER", "1") == "1":
        n = collect_docker_traces(dest)
        if n > 0:
            return n

    if orchestrator and cluster:
        n = collect_http_traces(dest, orchestrator, cluster, min_mtime_unix=min_mtime_unix)
        if n > 0:
            return n

    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Collect Task 12 perf trace JSONL")
    parser.add_argument("dest", type=Path, help="Output directory for raw JSONL")
    parser.add_argument("--orchestrator", default=os.environ.get("ORCHESTRATOR", "http://127.0.0.1:9000"))
    parser.add_argument("--cluster-json", type=Path, help="Cluster snapshot JSON")
    args = parser.parse_args()

    cluster: dict[str, Any] = {}
    if args.cluster_json and args.cluster_json.is_file():
        cluster = json.loads(args.cluster_json.read_text(encoding="utf-8"))

    count = collect_traces(args.dest, orchestrator=args.orchestrator, cluster=cluster)
    print(json.dumps({"collected": count, "dest": str(args.dest)}, indent=2))
    return 0 if count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
