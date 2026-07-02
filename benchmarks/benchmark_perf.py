#!/usr/bin/env python3
"""Task 10.1 — Monolithic vs Distributed performance benchmark engine."""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import benchmark_runner as br
from benchmark_export import write_trace
from benchmark_monolithic import run_monolithic_repeat
from benchmark_overhead import (
    build_comparison_table,
    compute_overhead,
    compute_scaling_table,
    estimate_decode_breakdown,
    estimate_hidden_hops,
)
from benchmark_sampler import MetricsSampler, estimate_network_delta, summarize_samples
from benchmark_stats import aggregate_repeats, summarize
from yaml_util import load_yaml_file

BENCH_DIR = Path(__file__).resolve().parent


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_prompts() -> dict[str, Any]:
    return load_yaml_file(BENCH_DIR / "benchmark_prompts.yaml")


def pick_prompt(category: str, prompt_length: int, base_override: str | None = None) -> str:
    doc = load_prompts()
    cats = doc.get("categories", {})
    cat = cats.get(category, cats.get("short", {}))
    prompts = cat.get("prompts", ["The capital of France is"])
    prompt = prompts[0] if prompts else "The capital of France is"
    if base_override:
        prompt = base_override
    return br.make_prompt(prompt, prompt_length)


def quality_check(prompt: str, text: str, first_token: str) -> dict[str, Any]:
    doc = load_prompts()
    expectations = doc.get("quality_expectations", {})
    must = expectations.get(prompt, {}).get("must_contain", [])
    semantic_ok = True
    if must:
        blob = (first_token + " " + text).lower()
        semantic_ok = any(m.lower() in blob for m in must)
    return {
        "first_token": first_token,
        "output_hash": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
        "token_count": len(text.split()),
        "semantic_equivalence": semantic_ok,
        "text_preview": text[:160],
    }


def _first_token(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    parts = re.split(r"(\s+)", text, maxsplit=1)
    return parts[0] if parts else text[:32]


def node_detail_sample(cluster: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for n in cluster.get("nodes", []):
        host, port, nid = n.get("host", ""), int(n.get("port", 0)), n.get("node_id", "")
        if not host or not port:
            continue
        st, cap = br.node_http(host, port, "/capabilities", timeout=5)
        status_st, status = br.node_http(host, port, "/status", timeout=5)
        out[nid] = {
            "capabilities": cap if st == 200 else {},
            "status": status if status_st == 200 else {},
        }
    return out


def ensure_model_ready(
    model_cfg: dict[str, Any],
    profile: dict[str, Any],
    cold: bool,
    traces_dir: Path,
) -> dict[str, Any]:
    """Cold: reset+sync. Warm: skip if READY."""
    mid = model_cfg["model_id"]
    metrics: dict[str, Any] = {"measurement_source": "direct"}
    if not cold:
        rec = br.model_record(mid)
        cov = (rec.get("coverage") or {}).get("state", "")
        if cov == "READY":
            metrics["skipped"] = True
            metrics["coverage_state"] = cov
            return metrics

    t0 = time.perf_counter()
    br.http("POST", f"/models/{mid}/reset", {"keep_manifest": False}, timeout=180)
    br.http("POST", "/models/register", {
        "model_id": mid,
        "display_name": model_cfg.get("label", mid),
        "source": "huggingface",
        "repository": model_cfg["repository"],
        "filename": model_cfg["filename"],
        "revision": model_cfg.get("revision", "main"),
    }, timeout=120)
    br.http("POST", f"/models/{mid}/discover", {}, timeout=180)
    br.http("POST", f"/models/{mid}/manifest", {}, timeout=180)
    t_layout = time.perf_counter()
    st, layout = br.http("POST", f"/models/{mid}/layout", {"force": True}, timeout=120)
    metrics["planner_ms"] = round((time.perf_counter() - t_layout) * 1000, 2)
    metrics["fits_cluster"] = layout.get("fits_cluster") if st == 200 else None

    sync_rec, _ = br.run_sync_loop(mid, profile, traces_dir, None)
    metrics["install_ms"] = sync_rec.duration_ms
    metrics["sync"] = sync_rec.metrics
    metrics["total_cold_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return metrics


def run_distributed_repeat(
    model_cfg: dict[str, Any],
    prompt: str,
    max_tokens: int,
    profile: dict[str, Any],
    cluster: dict[str, Any],
) -> dict[str, Any]:
    mid = model_cfg["model_id"]
    n_ctx = int(profile.get("n_ctx", 512))
    timeout = int(profile.get("generate_timeout_s", 180))
    interval = int(profile.get("sample_interval_ms", 100))

    sampler = MetricsSampler(interval, br.fetch_cluster_snapshot, node_detail_sample)
    t_total0 = time.perf_counter()

    # --- TTFT path (dedicated session) ---
    t_sess0 = time.perf_counter()
    st_sess, sess = br.http("POST", "/session/create", {"model": mid, "n_ctx": n_ctx}, timeout=300)
    session_ms = (time.perf_counter() - t_sess0) * 1000.0
    session_id = sess.get("session_id", "") if st_sess == 200 else ""
    pipeline = sess.get("pipeline", []) if isinstance(sess, dict) else []

    t_ttft0 = time.perf_counter()
    st1, out1 = br.http("POST", "/session/generate", {
        "session_id": session_id,
        "prompt": prompt,
        "max_tokens": 1,
    }, timeout=timeout)
    ttft_ms = (time.perf_counter() - t_ttft0) * 1000.0
    text1 = out1.get("text", "") if isinstance(out1, dict) else ""

    prefill_ms = 0.0
    if profile.get("prefill_probe", False):
        long_prompt = br.make_prompt(prompt, max(len(prompt) * 2, 128))
        t_pf0 = time.perf_counter()
        br.http("POST", "/session/generate", {
            "session_id": session_id,
            "prompt": long_prompt,
            "max_tokens": 1,
        }, timeout=timeout)
        prefill_ms = (time.perf_counter() - t_pf0) * 1000.0

    # --- Decode path (fresh session — same-session decode returns 503 after TTFT) ---
    sampler.start()
    t_dec_sess0 = time.perf_counter()
    st_dec_sess, sess_dec = br.http("POST", "/session/create", {"model": mid, "n_ctx": n_ctx}, timeout=300)
    decode_session_ms = (time.perf_counter() - t_dec_sess0) * 1000.0
    decode_session_id = sess_dec.get("session_id", "") if st_dec_sess == 200 else session_id
    if not pipeline and isinstance(sess_dec, dict):
        pipeline = sess_dec.get("pipeline", [])

    wt = int(profile.get("warmup_tokens", 0))
    if wt > 0:
        br.http("POST", "/session/generate", {
            "session_id": decode_session_id,
            "prompt": prompt,
            "max_tokens": wt,
        }, timeout=timeout)

    t_dec0 = time.perf_counter()
    st_dec, out_dec = br.http("POST", "/session/generate", {
        "session_id": decode_session_id,
        "prompt": prompt,
        "max_tokens": max_tokens,
    }, timeout=timeout)
    decode_ms = (time.perf_counter() - t_dec0) * 1000.0
    samples = sampler.stop()
    total_ms = (time.perf_counter() - t_total0) * 1000.0

    text = out_dec.get("text", "") if isinstance(out_dec, dict) else ""
    count = int(out_dec.get("count", len(out_dec.get("tokens", []))) if isinstance(out_dec, dict) else 0)
    first = _first_token(text or text1)
    decode_tps = round(count / (decode_ms / 1000.0), 2) if decode_ms > 0 and count else 0
    ms_per_token = round(decode_ms / max(count, 1), 2)

    hidden = estimate_hidden_hops(pipeline)
    hidden["avg_hop_latency_ms"] = None
    if hidden["hop_count"] and ttft_ms:
        hidden["avg_hop_latency_ms"] = round(
            max(ttft_ms - session_ms, 0) / max(hidden["hop_count"] * 2, 1), 3
        )

    ttft_total_ms = round(session_ms + ttft_ms, 2)

    return {
        "backend": "distributed",
        "http_status": {
            "session_create": st_sess,
            "ttft_generate": st1,
            "decode_generate": st_dec,
        },
        "load": {
            "session_configure_ms": round(session_ms, 2),
            "decode_session_configure_ms": round(decode_session_ms, 2),
            "materialization_ms": None,
            "measurement_source": "direct",
        },
        "ttft": {
            "total_ms": ttft_total_ms,
            "session_create_ms": round(session_ms, 2),
            "worker_startup_ms": None,
            "prefill_first_token_ms": round(ttft_ms, 2),
            "measurement_source": "direct",
        },
        "prefill": {
            "prompt_chars": len(prompt),
            "duration_ms": round(prefill_ms or ttft_ms, 2),
            "tokens_per_sec": round(1 / ((prefill_ms or ttft_ms) / 1000.0), 2) if (prefill_ms or ttft_ms) else None,
            "measurement_source": "direct" if profile.get("prefill_probe") else "derived_from_ttft",
        },
        "decode": {
            "duration_ms": round(decode_ms, 2),
            "token_count": count,
            "tokens_per_sec": decode_tps,
            "ms_per_token": ms_per_token,
            "measurement_source": "direct",
        },
        "hidden_transfer": hidden,
        "decode_breakdown": estimate_decode_breakdown(pipeline, decode_ms),
        "network": estimate_network_delta(samples),
        "runtime_samples": summarize_samples(samples),
        "samples_raw_count": len(samples),
        "pipeline": pipeline,
        "quality": quality_check(prompt, text, first),
        "total_ms": round(total_ms, 2),
    }


def run_perf_scenario(
    model_key: str,
    model_cfg: dict[str, Any],
    row: dict[str, Any],
    profile: dict[str, Any],
    run_mode: str,
    run_id: str,
    traces_dir: Path,
    cluster: dict[str, Any],
    log: Callable[[str], None],
) -> dict[str, Any]:
    cs = row["cluster_size_target"]
    prompt_cat = row.get("prompt_category", profile.get("prompt_category", "short"))
    prompt_len = int(row.get("prompt_length", 16))
    gen_tokens = int(row.get("generate_tokens", 16))
    repeats_n = int(profile.get("repeats", 5))
    prompt = pick_prompt(prompt_cat, prompt_len)
    cold = run_mode == "cold" or profile.get("reset_before_run", False)

    scenario_id = f"{model_key}_{cs}_{run_mode}_p{prompt_len}_g{gen_tokens}"
    log(f"\n=== perf {scenario_id} ===")

    if cs != "mono" and isinstance(cs, int) and cluster["node_count"] < cs:
        return {
            "scenario_id": scenario_id,
            "model_key": model_key,
            "model_id": model_cfg["model_id"],
            "cluster_size_target": cs,
            "cluster_size_observed": cluster["node_count"],
            "run_mode": run_mode,
            "skipped": True,
            "notes": [f"need {cs} nodes, have {cluster['node_count']}"],
        }

    cold_metrics: dict[str, Any] = {}
    if cs != "mono" and cold:
        cold_metrics = ensure_model_ready(model_cfg, profile, True, traces_dir)
        if cold_metrics.get("fits_cluster") is False:
            return {
                "scenario_id": scenario_id,
                "skipped": True,
                "notes": ["fits_cluster=false"],
                "cold": cold_metrics,
            }
    elif cs != "mono" and not cold:
        cold_metrics = ensure_model_ready(model_cfg, profile, False, traces_dir)

    repeats: list[dict[str, Any]] = []
    for i in range(repeats_n):
        log(f"  repeat {i + 1}/{repeats_n}")
        if cs == "mono":
            rep = run_monolithic_repeat(
                model_cfg, prompt, gen_tokens,
                int(profile.get("n_ctx", 512)),
                int(profile.get("mono_ngl", 99)),
                int(profile.get("generate_timeout_s", 300)),
            )
        else:
            rep = run_distributed_repeat(model_cfg, prompt, gen_tokens, profile, cluster)
        rep["repeat_index"] = i
        repeats.append(rep)
        write_trace(traces_dir, f"{scenario_id}_r{i}", rep)

    agg_paths = [
        ("ttft", "total_ms"),
        ("ttft", "session_create_ms"),
        ("ttft", "prefill_first_token_ms"),
        ("prefill", "duration_ms"),
        ("prefill", "tokens_per_sec"),
        ("decode", "duration_ms"),
        ("decode", "tokens_per_sec"),
        ("decode", "ms_per_token"),
        ("load", "total_ms"),
        ("load", "session_configure_ms"),
    ]

    def _get(d: dict, path: tuple[str, ...]) -> float | None:
        cur: Any = d
        for p in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(p)
        return float(cur) if isinstance(cur, (int, float)) else None

    aggregate: dict[str, Any] = {}
    for path in agg_paths:
        key = ".".join(path)
        vals = [_get(r, path) for r in repeats]
        vals = [v for v in vals if v is not None]
        aggregate[key] = summarize(vals)

    if cold_metrics.get("install_ms") is not None:
        aggregate["cold.install_ms"] = {"mean": cold_metrics["install_ms"]}
    if cold_metrics.get("planner_ms") is not None:
        aggregate["cold.planner_ms"] = {"mean": cold_metrics["planner_ms"]}

    hop_vals = [r.get("hidden_transfer", {}).get("avg_hop_latency_ms") for r in repeats]
    hop_vals = [v for v in hop_vals if isinstance(v, (int, float))]
    if hop_vals:
        aggregate["hidden.avg_hop_latency_ms"] = summarize(hop_vals)

    return {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "model_key": model_key,
        "model_id": model_cfg["model_id"],
        "cluster_size_target": cs,
        "cluster_size_observed": 1 if cs == "mono" else cluster.get("node_count"),
        "run_mode": run_mode,
        "prompt_category": prompt_cat,
        "prompt_length": prompt_len,
        "generate_tokens": gen_tokens,
        "prompt": prompt,
        "started_at": utc_now(),
        "cold": cold_metrics,
        "repeats": repeats,
        "aggregate": aggregate,
        "quality_summary": {
            "semantic_pass_rate": round(
                sum(1 for r in repeats if r.get("quality", {}).get("semantic_equivalence")) / max(len(repeats), 1),
                2,
            ),
        },
    }


def expand_perf_matrix(
    profile: dict[str, Any],
    models_catalog: dict[str, Any],
    model_filter: str | None = None,
) -> list[dict[str, Any]]:
    keys = profile.get("models", [])
    if model_filter:
        keys = [k for k in keys if k == model_filter or models_catalog.get(k, {}).get("model_id") == model_filter]
    rows = []
    modes = profile.get("modes", [profile.get("mode", "warm")])
    if isinstance(modes, str):
        modes = [modes]
    for mk in keys:
        if mk not in models_catalog:
            continue
        for cs in profile.get("cluster_sizes", [3]):
            for cat in profile.get("prompt_categories", ["short"]):
                for pl in profile.get("prompt_lengths", [16]):
                    for gt in profile.get("generate_tokens", [16]):
                        for run_mode in modes:
                            if run_mode in ("perf", "scaling"):
                                run_mode = "warm"
                            rows.append({
                                "model_key": mk,
                                "cluster_size_target": cs,
                                "prompt_category": cat,
                                "prompt_length": pl,
                                "generate_tokens": gt,
                                "run_mode": run_mode,
                            })
    return rows


def run_perf_suite(
    profile: dict[str, Any],
    profile_name: str,
    mode: str,
    models_catalog: dict[str, Any],
    run_id: str,
    out_dir: Path,
    model_filter: str | None = None,
    cluster_size_filter: int | str | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    traces_dir = out_dir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    min_nodes = 1
    sizes = profile.get("cluster_sizes", [3])
    numeric = [s for s in sizes if isinstance(s, int)]
    if numeric:
        min_nodes = min(numeric)
    ok, cluster = br.wait_cluster(min_nodes if "mono" not in sizes else 0, int(profile.get("wait_cluster_timeout_s", 120)))
    if not ok and numeric:
        log(f"Cluster has {cluster.get('node_count', 0)} nodes (need >={min_nodes})")
        return {"error": "insufficient nodes", "cluster": cluster}

    rows = expand_perf_matrix(profile, models_catalog, model_filter)
    if cluster_size_filter is not None:
        rows = [r for r in rows if str(r["cluster_size_target"]) == str(cluster_size_filter)]

    scenarios: list[dict[str, Any]] = []
    mono_baselines: dict[str, dict[str, Any]] = {}

    for row in rows:
        mk = row["model_key"]
        cfg = models_catalog[mk]
        run_mode = row.get("run_mode", mode if mode in ("cold", "warm") else "warm")
        cluster = br.fetch_cluster_snapshot()
        try:
            sc = run_perf_scenario(mk, cfg, row, profile, run_mode, run_id, traces_dir, cluster, log)
        except Exception as exc:  # noqa: BLE001
            sc = {"scenario_id": f"{mk}_error", "error": str(exc), "model_key": mk}
        scenarios.append(sc)
        if sc.get("cluster_size_target") == "mono" and sc.get("aggregate"):
            mono_baselines[mk] = sc

    for sc in scenarios:
        mk = sc.get("model_key", "")
        if mk in mono_baselines and sc.get("cluster_size_target") != "mono":
            sc["overhead_vs_mono"] = compute_overhead(mono_baselines[mk], sc)

    comparison = build_comparison_table(scenarios)
    scaling = compute_scaling_table(scenarios)

    return {
        "benchmark_version": "10.1",
        "run_id": run_id,
        "profile": profile_name,
        "mode": mode,
        "orchestrator": br.ORCH,
        "cluster": cluster,
        "software": __import__("benchmark_export").collect_git_metadata(),
        "scenarios": scenarios,
        "comparison": comparison,
        "scaling": scaling,
        "summary": {
            "scenario_count": len(scenarios),
            "models": sorted({s.get("model_key", "") for s in scenarios}),
        },
    }
