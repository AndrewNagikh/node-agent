#!/usr/bin/env python3
"""Multi-architecture cluster pipeline test (Task 9.8.3).

Usage:
  PYTHONUNBUFFERED=1 python3 scripts/cluster_multiarch_test.py
  PYTHONUNBUFFERED=1 python3 scripts/cluster_multiarch_test.py --model qwen2.5-1.5b
  PYTHONUNBUFFERED=1 python3 scripts/cluster_multiarch_test.py --skip-sync
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ORCH = os.environ.get("ORCHESTRATOR", "http://192.168.50.154:9000")
ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
LOG_DIR = ROOT / "logs"

MODELS = [
    {
        "label": "TinyLlama",
        "model_id": "tinyllama-1.1b",
        "repository": "TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF",
        "filename": "tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf",
        "family": "llama",
    },
    {
        "label": "Llama3.2",
        "model_id": "llama-3.2-1b",
        "repository": "hugging-quants/Llama-3.2-1B-Instruct-Q4_K_M-GGUF",
        "filename": "llama-3.2-1b-instruct-q4_k_m.gguf",
        "family": "llama",
    },
    {
        "label": "Qwen2.5",
        "model_id": "qwen2.5-1.5b",
        "repository": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "filename": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "family": "qwen",
    },
    {
        "label": "Gemma3",
        "model_id": "gemma-3-1b",
        "repository": "lmstudio-community/gemma-3-1b-it-GGUF",
        "filename": "gemma-3-1b-it-q4_k_m.gguf",
        "family": "gemma",
    },
]

# Primary cluster validation set (Gemma optional via --model gemma-3-1b).
CLUSTER_MODELS = MODELS[:3]


def log(msg: str) -> None:
    print(msg, flush=True)


def load_hf_token() -> None:
    if os.environ.get("HF_TOKEN"):
        return
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("HF_TOKEN="):
                os.environ["HF_TOKEN"] = line.split("=", 1)[1].strip()
                return


def http(method: str, path: str, body: dict | None = None, timeout: int = 120) -> tuple[int, Any]:
    url = ORCH.rstrip("/") + path
    data = None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = os.environ.get("HF_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
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


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ModelResult:
    label: str
    model_id: str
    steps: list[StepResult] = field(default_factory=list)
    generate_text: str = ""
    blob_ops: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(s.ok for s in self.steps)


def step(name: str, ok: bool, detail: str = "") -> StepResult:
    mark = "PASS" if ok else "FAIL"
    log(f"  [{mark}] {name}" + (f": {detail}" if detail else ""))
    return StepResult(name, ok, detail)


def model_status(model_id: str) -> dict:
    status, out = http("GET", f"/models/{model_id}", timeout=15)
    return out if status == 200 else {}


def wait_job(job_id: str, timeout_s: int = 3600, poll_s: float = 1.0) -> tuple[bool, str]:
    log(f"  ... waiting for sync job {job_id} (timeout {timeout_s}s)")
    deadline = time.time() + timeout_s
    last_line = ""
    while time.time() < deadline:
        status, job = http("GET", f"/jobs/{job_id}", timeout=30)
        if status == 200:
            state = job.get("state", "")
            progress = job.get("progress", job.get("completed_operations", ""))
            ready = job.get("ready_count", "")
            total = job.get("total_count", "")
            extra = f" {ready}/{total}" if ready != "" and total != "" else ""
            line = f"  ... job state={state} progress={progress}{extra}"
            if line != last_line:
                log(line)
                last_line = line
            if state == "completed":
                return True, ""
            if state == "failed":
                return False, job.get("error", json.dumps(job))
        time.sleep(poll_s)
    return False, "job timeout"


def coverage_state(model_id: str) -> str:
    status, out = http("GET", f"/models/{model_id}", timeout=15)
    if status != 200:
        return ""
    cov = out.get("coverage") or {}
    return cov.get("state", "")


def timed_step(name: str, ok: bool, detail: str = "", elapsed_s: float = 0.0) -> StepResult:
    suffix = f" ({elapsed_s:.1f}s)" if elapsed_s > 0 else ""
    return step(name, ok, (detail + suffix).strip(": "))


def analyze_blob_ops(plan: dict, family: str, model_id: str = "") -> tuple[bool, str, list[dict]]:
    ops = plan.get("install_plan", plan).get("operations", [])
    op_count = plan.get("operation_count", len(ops))
    blob_ops = []
    for op in ops:
        dl = op.get("download") or op
        blob_id = dl.get("blob_id", "")
        tensor = dl.get("tensor_name", "")
        if blob_id or tensor:
            blob_ops.append({
                "node": op.get("node_id") or dl.get("node"),
                "blob_id": blob_id,
                "tensor_name": tensor,
                "length": dl.get("tensor_length") or dl.get("length"),
            })

    if family == "qwen":
        names = {(b["blob_id"], b["tensor_name"]) for b in blob_ops}
        need = {("output_head", "output.weight"), ("output_norm", "output_norm.weight")}

        def qwen_blobs_on_layout_nodes() -> bool:
            if not model_id:
                return False
            rec = model_status(model_id)
            layout = rec.get("layout") or {}
            desired = layout.get("desired", layout)
            placements = desired.get("placements", [])
            if not placements:
                return False
            entry_node = final_node = ""
            last_layer = -1
            for p in placements:
                layer = p.get("layer", p.get("layer_index", -1))
                node = p.get("node", p.get("node_id", ""))
                if layer == 0:
                    entry_node = node
                if layer > last_layer:
                    last_layer = layer
                    final_node = node
            if not entry_node or not final_node:
                return False
            layers = rec.get("actual", {}).get("layers", [])

            def ready(node: str, blob_id: str, tensor: str) -> bool:
                return any(
                    L.get("node_id") == node
                    and L.get("blob_id") == blob_id
                    and L.get("tensor_name") == tensor
                    and str(L.get("state", "")).lower() == "ready"
                    for L in layers
                )

            return (
                ready(entry_node, "embedding", "token_embd.weight")
                and ready(final_node, "output_head", "output.weight")
                and ready(final_node, "output_norm", "output_norm.weight")
            )

        if qwen_blobs_on_layout_nodes():
            return True, "blobs on layout nodes", blob_ops

        if op_count == 0 and model_id:
            installed = {
                (L.get("blob_id"), L.get("tensor_name"))
                for L in model_status(model_id).get("actual", {}).get("layers", [])
                if L.get("blob_id")
            }
            if need <= installed:
                return True, "blobs already installed", blob_ops
            if coverage_state(model_id) == "READY" and need <= installed:
                return True, "blobs already installed", blob_ops
            if op_count == 0 and not blob_ops:
                return True, "nothing to download", blob_ops
        missing = need - names
        if missing:
            return False, f"missing qwen blob ops: {missing}", blob_ops
        for b in blob_ops:
            if b["blob_id"] == "output_head" and b.get("length", 0) > 500_000_000:
                return False, "output_head download suspiciously large (merged range?)", blob_ops
    return True, "", blob_ops


def ensure_registered(cfg: dict) -> StepResult:
    existing = model_status(cfg["model_id"])
    if existing.get("model_id") == cfg["model_id"]:
        return step("register", True, f"already {existing.get('status', '?')}")
    status, out = http("POST", "/models/register", {
        "model_id": cfg["model_id"],
        "display_name": cfg["label"],
        "source": "huggingface",
        "repository": cfg["repository"],
        "filename": cfg["filename"],
        "revision": "main",
    })
    return step("register", status == 200, out.get("error", "") if status != 200 else "")


def ensure_discovered(cfg: dict) -> StepResult:
    rec = model_status(cfg["model_id"])
    st = rec.get("status", "")
    has_files = bool(rec.get("files"))
    if st in ("MANIFEST_PENDING", "MANIFEST_READY", "LAYOUT_READY", "READY") and has_files:
        return step("discover", True, f"already {st}")
    status, out = http("POST", f"/models/{cfg['model_id']}/discover", {}, timeout=180)
    ok = status == 200 and out.get("status") == "ok"
    return step("discover", ok, out.get("error", json.dumps(out)) if not ok else f"files={out.get('files', 0)}")


def ensure_manifest(cfg: dict) -> tuple[StepResult, str]:
    st = model_status(cfg["model_id"]).get("status", "")
    if st in ("MANIFEST_READY", "LAYOUT_READY", "READY"):
        status, out = http("GET", f"/models/{cfg['model_id']}/manifest", timeout=30)
        arch = out.get("architecture", "?") if status == 200 else "?"
        return step("manifest", True, f"cached arch={arch}"), arch
    status, out = http("POST", f"/models/{cfg['model_id']}/manifest", {}, timeout=180)
    arch = out.get("architecture", "")
    ok = status == 200 and out.get("status") == "ok" and out.get("n_layer", 0) > 0
    return step("manifest", ok, f"arch={arch} layers={out.get('n_layer')}" if ok else json.dumps(out)), arch


def ensure_layout(cfg: dict) -> StepResult:
    status, out = http("GET", f"/models/{cfg['model_id']}/layout", timeout=30)
    if status == 200 and out.get("placements"):
        n = len(out["placements"]) if isinstance(out["placements"], list) else out.get("placements", 0)
        return step("layout", True, f"cached placements={n}")
    status, out = http("POST", f"/models/{cfg['model_id']}/layout", {}, timeout=120)
    ok = status == 200 and out.get("status") == "ok"
    cached = out.get("cached", False)
    detail = f"cached placements={out.get('placements')}" if cached else f"placements={out.get('placements')}"
    if not ok:
        detail = out.get("error", json.dumps(out))
    return step("layout", ok, detail)


def test_model(cfg: dict, skip_sync: bool = False, fast: bool = False) -> ModelResult:
    mid = cfg["model_id"]
    result = ModelResult(cfg["label"], mid)
    log(f"\n=== {cfg['label']} ({mid}) ===")

    st = model_status(mid)
    cov = (st.get("coverage") or {}).get("state", "")
    if fast and cov == "READY":
        log("  [fast] coverage READY — skip register/layout/sync")
        result.steps.append(step("register", True, "skipped"))
        result.steps.append(step("discover", True, "skipped"))
        result.steps.append(step("manifest", True, "skipped"))
        result.steps.append(step("layout", True, "skipped"))
        result.steps.append(step("install-plan", True, "skipped"))
        result.steps.append(step("install-sync", True, "skipped"))
        result.steps.append(step("coverage", True, f"state={cov}"))
    else:
        result.steps.append(ensure_registered(cfg))
        if not result.steps[-1].ok:
            return result

        result.steps.append(ensure_discovered(cfg))
        if not result.steps[-1].ok:
            return result

        manifest_step, _arch = ensure_manifest(cfg)
        result.steps.append(manifest_step)
        if not manifest_step.ok:
            return result

        result.steps.append(ensure_layout(cfg))
        if not result.steps[-1].ok:
            return result

        status, out = http("POST", f"/models/{mid}/install-plan", {}, timeout=120)
        blob_ok, blob_err, blob_ops = analyze_blob_ops(out, cfg["family"], mid) if status == 200 else (False, "", [])
        result.blob_ops = blob_ops
        op_count = out.get("operation_count", -1) if status == 200 else -1
        result.steps.append(step("install-plan", status == 200 and blob_ok,
                                 blob_err or out.get("error", "") if status != 200 or not blob_ok else
                                 f"ops={op_count} bytes={out.get('total_download_bytes')}"))
        if status != 200 or not blob_ok:
            return result

        if skip_sync or op_count == 0:
            detail = "skipped" if skip_sync else "nothing to download"
            result.steps.append(step("install-sync", True, detail))
        else:
            status, out = http("POST", f"/models/{mid}/install/execute", {}, timeout=120)
            job_id = out.get("job_id", "") if status == 200 else ""
            if status != 200 or not job_id:
                result.steps.append(step("install-sync", False, json.dumps(out)))
                return result
            ok, err = wait_job(job_id, timeout_s=3600)
            result.steps.append(step("install-sync", ok, err))

        status, out = http("POST", f"/models/{mid}/coverage/refresh", {}, timeout=120)
        cov_state = out.get("coverage", {}).get("state", "") if status == 200 else ""
        result.steps.append(step("coverage", status == 200 and cov_state == "READY",
                                 f"state={cov_state}" if cov_state else json.dumps(out)))

        if cov_state != "READY":
            return result

    status, out = http("POST", "/session/create", {"model": mid, "n_ctx": 512}, timeout=120)
    sid = out.get("session_id", "") if status == 200 else ""
    result.steps.append(step("session-create", status == 200 and bool(sid),
                             out.get("error", "") if status != 200 else ""))
    if not sid:
        return result

    status, out = http("POST", "/session/generate", {
        "session_id": sid,
        "prompt": "The capital of France is",
        "max_tokens": 16,
    }, timeout=600)
    text = out.get("text", out.get("content", ""))
    tokens = out.get("tokens", [])
    bad = not text.strip() or text.strip() == "enenenen" or (tokens and len(set(tokens)) == 1 and tokens[0] in (0, 268))
    if not bad and tokens and len(tokens) >= 4 and len(set(tokens)) == 1:
        bad = True
    if not bad and text and len(text) >= 8 and len(set(text.split())) == 1 and len(text) > 20:
        bad = True
    result.generate_text = text
    result.steps.append(step("generate", status == 200 and not bad,
                             f"text={text!r}" if text else json.dumps(out)))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="Run only this model_id")
    parser.add_argument("--skip-sync", action="store_true", help="Only build install plan, do not execute")
    parser.add_argument("--fast", action="store_true", help="Skip pipeline when coverage is already READY")
    args = parser.parse_args()

    load_hf_token()
    log(f"Orchestrator: {ORCH}")

    status, health = http("GET", "/health", timeout=10)
    if status != 200:
        log(f"Orchestrator unreachable: {status}")
        return 1
    log(f"Health: {health}")

    models = CLUSTER_MODELS
    if args.model:
        models = [m for m in MODELS if m["model_id"] == args.model]
        if not models:
            log(f"Unknown model_id: {args.model}")
            return 2

    results = [test_model(cfg, skip_sync=args.skip_sync, fast=args.fast) for cfg in models]

    log("\n" + "=" * 60)
    log("SUMMARY")
    log("=" * 60)
    for r in results:
        def s(name: str) -> str:
            for st in r.steps:
                if st.name == name:
                    return "PASS" if st.ok else "FAIL"
            return "----"
        overall = "PASS" if r.passed else "FAIL"
        log(f"{r.label:<14} plan={s('install-plan')} sync={s('install-sync')} cov={s('coverage')} gen={s('generate')} => {overall}")
        if r.generate_text:
            log(f"  generate: {r.generate_text!r}")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out_path = LOG_DIR / "cluster_multiarch_results.json"
    payload = [{
        "label": r.label,
        "model_id": r.model_id,
        "passed": r.passed,
        "generate_text": r.generate_text,
        "blob_ops": r.blob_ops,
        "steps": [{"name": s.name, "ok": s.ok, "detail": s.detail} for s in r.steps],
    } for r in results]
    out_path.write_text(json.dumps(payload, indent=2))
    log(f"\nResults saved: {out_path}")

    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
