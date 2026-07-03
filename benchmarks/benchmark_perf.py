#!/usr/bin/env python3
"""Task 10.1.2 — Model-centric persistent-session benchmark framework."""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
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
from benchmark_stats import summarize
from yaml_util import load_yaml_file

BENCH_DIR = Path(__file__).resolve().parent

PROMPT_PROFILE_TOKENS = {
    "short": 16,
    "medium": 128,
    "long": 1024,
    "code": 128,
    "chat": 64,
    "reasoning": 256,
}


@dataclass
class PerfOptions:
    persistent_session: bool = True
    warmup: bool = True
    runtime_only: bool = False
    infra_only: bool = False
    generations: int | None = None
    prompt_profile: str | None = None
    verify_session: bool = True
    resume: bool = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_prompts() -> dict[str, Any]:
    return load_yaml_file(BENCH_DIR / "benchmark_prompts.yaml")


def category_prompts(category: str) -> list[str]:
    doc = load_prompts()
    cats = doc.get("categories", {})
    cat = cats.get(category, cats.get("short", {}))
    return list(cat.get("prompts", ["The capital of France is"]))


def pick_prompt(category: str, prompt_length: int, base_override: str | None = None) -> str:
    prompts = category_prompts(category)
    prompt = prompts[0] if prompts else "The capital of France is"
    if base_override:
        prompt = base_override
    return br.make_prompt(prompt, prompt_length)


def rotate_prompts(category: str, prompt_length: int, count: int) -> list[str]:
    prompts = category_prompts(category)
    if not prompts:
        prompts = ["The capital of France is"]
    return [br.make_prompt(prompts[i % len(prompts)], prompt_length) for i in range(count)]


def resolve_prompt_length(profile: dict[str, Any], row: dict[str, Any], opts: PerfOptions) -> int:
    cat = opts.prompt_profile or row.get("prompt_category") or profile.get("prompt_category", "short")
    doc = load_prompts()
    cat_cfg = doc.get("categories", {}).get(cat, {})
    if cat_cfg.get("target_tokens"):
        return int(cat_cfg["target_tokens"])
    if cat in PROMPT_PROFILE_TOKENS:
        return PROMPT_PROFILE_TOKENS[cat]
    return int(row.get("prompt_length", profile.get("prompt_lengths", [16])[0] if profile.get("prompt_lengths") else 16))


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


def _timed_generate(
    session_id: str,
    prompt: str,
    max_tokens: int,
    timeout: int,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    status, out = br.http("POST", "/session/generate", {
        "session_id": session_id,
        "prompt": prompt,
        "max_tokens": max_tokens,
    }, timeout=timeout)
    duration_ms = (time.perf_counter() - t0) * 1000.0
    text = out.get("text", "") if isinstance(out, dict) else ""
    count = int(out.get("count", len(out.get("tokens", []))) if isinstance(out, dict) else 0)
    return {
        "http_status": status,
        "duration_ms": round(duration_ms, 2),
        "token_count": count,
        "text": text,
        "tokens_per_sec": round(count / (duration_ms / 1000.0), 2) if duration_ms > 0 and count else 0,
        "ms_per_token": round(duration_ms / max(count, 1), 2),
    }


def orchestrator_rss_sample(stage: str) -> dict[str, Any]:
    """Sample orchestrator control-plane RSS (requires /debug/rss on rebuilt orchestrator)."""
    st, out = br.http("GET", "/debug/rss", timeout=5)
    if st != 200 or not isinstance(out, dict):
        return {"stage": stage, "error": f"http_{st}"}
    return {
        "stage": stage,
        "rss_bytes": out.get("rss_bytes"),
        "rss_mb": out.get("rss_mb"),
        "baseline_delta_mb": out.get("baseline_delta_mb"),
    }


def summarize_orchestrator_rss(samples: list[dict[str, Any]]) -> dict[str, Any]:
    mbs = [s["rss_mb"] for s in samples if isinstance(s.get("rss_mb"), (int, float))]
    deltas = [s["baseline_delta_mb"] for s in samples if isinstance(s.get("baseline_delta_mb"), (int, float))]
    if not mbs:
        return {"samples": samples}
    return {
        "samples": samples,
        "peak_rss_mb": max(mbs),
        "avg_rss_mb": round(sum(mbs) / len(mbs), 2),
        "peak_baseline_delta_mb": max(deltas) if deltas else None,
    }


def run_phase_a_infra(
    model_cfg: dict[str, Any],
    profile: dict[str, Any],
    cold: bool,
    traces_dir: Path,
    cluster: dict[str, Any],
    runtime_only: bool = False,
) -> dict[str, Any]:
    """Phase A — register through session create (once per model/scenario)."""
    mid = model_cfg["model_id"]
    n_ctx = int(profile.get("n_ctx", 512))
    infra: dict[str, Any] = {"measurement_source": "direct", "stages": {}}

    if runtime_only:
        warm = ensure_model_ready(model_cfg, profile, cold=False, traces_dir=traces_dir)
        infra["skipped"] = True
        infra["reason"] = "runtime_only"
        infra["cold_check"] = warm
        t_sess0 = time.perf_counter()
        st_sess, sess = br.http("POST", "/session/create", {"model": mid, "n_ctx": n_ctx}, timeout=300)
        session_ms = (time.perf_counter() - t_sess0) * 1000.0
        session_id = sess.get("session_id", "") if st_sess == 200 else ""
        pipeline = sess.get("pipeline", []) if isinstance(sess, dict) else []
        infra["session_create_ms"] = round(session_ms, 2)
        infra["session_id"] = session_id
        infra["pipeline"] = pipeline
        infra["http_status"] = {"session_create": st_sess}
        return infra

    t_total0 = time.perf_counter()
    rss_samples: list[dict[str, Any]] = [orchestrator_rss_sample("phase_a_start")]

    if cold or profile.get("reset_before_run", False):
        t0 = time.perf_counter()
        br.http("POST", f"/models/{mid}/reset", {"keep_manifest": False}, timeout=180)
        infra["stages"]["reset_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    elif not cold:
        rec = br.model_record(mid)
        cov = (rec.get("coverage") or {}).get("state", "")
        if cov == "READY":
            infra["model_ready"] = True
            infra["coverage_state"] = cov

    t0 = time.perf_counter()
    st_reg, _ = br.http("POST", "/models/register", {
        "model_id": mid,
        "display_name": model_cfg.get("label", mid),
        "source": "huggingface",
        "repository": model_cfg["repository"],
        "filename": model_cfg["filename"],
        "revision": model_cfg.get("revision", "main"),
    }, timeout=120)
    infra["stages"]["register_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    rss_samples.append(orchestrator_rss_sample("register"))

    t0 = time.perf_counter()
    st_disc, _ = br.http("POST", f"/models/{mid}/discover", {}, timeout=180)
    infra["stages"]["discovery_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    rss_samples.append(orchestrator_rss_sample("discover"))

    t0 = time.perf_counter()
    st_man, _ = br.http("POST", f"/models/{mid}/manifest", {}, timeout=180)
    infra["stages"]["manifest_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    rss_samples.append(orchestrator_rss_sample("manifest"))

    t0 = time.perf_counter()
    st_lay, layout = br.http("POST", f"/models/{mid}/layout", {"force": True}, timeout=120)
    planner_ms = (time.perf_counter() - t0) * 1000.0
    infra["stages"]["planner_ms"] = round(planner_ms, 2)
    infra["planner_ms"] = round(planner_ms, 2)
    infra["fits_cluster"] = layout.get("fits_cluster") if st_lay == 200 else None
    rss_samples.append(orchestrator_rss_sample("layout"))

    t0 = time.perf_counter()
    br.http("POST", f"/models/{mid}/install-plan", {}, timeout=120)
    infra["stages"]["install_plan_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    rss_samples.append(orchestrator_rss_sample("install_plan"))

    sync_rec, cov_rec = br.run_sync_loop(mid, profile, traces_dir, None)
    infra["stages"]["synchronization_ms"] = sync_rec.duration_ms
    infra["stages"]["coverage_ms"] = cov_rec.duration_ms
    infra["install_ms"] = sync_rec.duration_ms
    infra["sync"] = sync_rec.metrics
    infra["coverage"] = cov_rec.metrics
    rss_samples.append(orchestrator_rss_sample("sync"))

    t0 = time.perf_counter()
    mat = br.run_materialization(mid, cluster)
    infra["stages"]["materialization_ms"] = mat.duration_ms
    infra["materialization_ms"] = mat.duration_ms
    infra["materialization"] = mat.metrics
    rss_samples.append(orchestrator_rss_sample("materialization"))

    t_sess0 = time.perf_counter()
    st_sess, sess = br.http("POST", "/session/create", {"model": mid, "n_ctx": n_ctx}, timeout=300)
    session_ms = (time.perf_counter() - t_sess0) * 1000.0
    session_id = sess.get("session_id", "") if st_sess == 200 else ""
    pipeline = sess.get("pipeline", []) if isinstance(sess, dict) else []

    infra["session_create_ms"] = round(session_ms, 2)
    infra["worker_configure_ms"] = round(session_ms, 2)
    infra["pipeline_ready_ms"] = round(session_ms, 2)
    infra["session_id"] = session_id
    infra["pipeline"] = pipeline
    infra["pipeline_nodes"] = len(pipeline)
    rss_samples.append(orchestrator_rss_sample("session_create"))
    infra["orchestrator_rss"] = summarize_orchestrator_rss(rss_samples)
    infra["total_ms"] = round((time.perf_counter() - t_total0) * 1000, 2)
    infra["http_status"] = {
        "register": st_reg,
        "discovery": st_disc,
        "manifest": st_man,
        "layout": st_lay,
        "session_create": st_sess,
    }
    return infra


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


def aggregate_runtime_generations(generations: list[dict[str, Any]]) -> dict[str, Any]:
    ttft_vals = [g["ttft_ms"] for g in generations if g.get("ttft_ms") is not None]
    decode_tps_vals = [g["decode_tokens_per_sec"] for g in generations if g.get("decode_tokens_per_sec") is not None]
    prefill_tps_vals = [g["prefill_tokens_per_sec"] for g in generations if g.get("prefill_tokens_per_sec") is not None]
    ms_per_token_vals = [g["ms_per_token"] for g in generations if g.get("ms_per_token") is not None]

    agg = {
        "ttft": summarize(ttft_vals),
        "decode_tokens_per_sec": summarize(decode_tps_vals),
        "prefill_tokens_per_sec": summarize(prefill_tps_vals),
        "ms_per_token": summarize(ms_per_token_vals),
        "jitter": summarize(ms_per_token_vals),
    }

    decode_durations = [g.get("decode_duration_ms") for g in generations if g.get("decode_duration_ms") is not None]
    if decode_durations:
        per_token = []
        for g in generations:
            dc = g.get("decode_duration_ms")
            tc = g.get("decode_token_count", 0)
            if dc and tc > 1:
                per_token.extend([dc / tc] * (tc - 1))
        if per_token:
            agg["jitter"] = summarize(per_token)

    if len(generations) >= 2:
        first_ms = generations[0].get("total_ms")
        later = [g.get("total_ms") for g in generations[1:] if g.get("total_ms") is not None]
        if first_ms and later:
            avg_later = sum(later) / len(later)
            agg["reuse_efficiency"] = round(avg_later / first_ms, 4) if first_ms > 0 else None
    elif len(generations) == 1:
        agg["reuse_efficiency"] = 1.0

    return agg


def run_phase_b_runtime(
    session_id: str,
    prompts: list[str],
    gen_tokens: int,
    profile: dict[str, Any],
    cluster: dict[str, Any],
    pipeline: list[dict[str, Any]],
    opts: PerfOptions,
) -> dict[str, Any]:
    """Phase B — N generates on a persistent session."""
    timeout = int(profile.get("generate_timeout_s", 180))
    interval = int(profile.get("sample_interval_ms", 100))
    n_generations = opts.generations if opts.generations is not None else int(profile.get("generations", 20))

    if not session_id:
        return {"error": "no session_id", "generations": [], "aggregate": {}}

    runtime: dict[str, Any] = {"measurement_source": "direct", "generations": []}
    sampler = MetricsSampler(interval, br.fetch_cluster_snapshot, node_detail_sample)

    if opts.warmup and profile.get("warmup", True):
        warmup_prompt = prompts[0] if prompts else "Hi"
        wt = int(profile.get("warmup_tokens", gen_tokens))
        warmup = _timed_generate(session_id, warmup_prompt, max(wt, 1), timeout)
        runtime["warmup"] = {**warmup, "discarded": True}

    sampler.start()
    generations: list[dict[str, Any]] = []
    for i in range(n_generations):
        prompt = prompts[i % len(prompts)] if prompts else "Hi"
        gen: dict[str, Any] = {"index": i, "prompt_preview": prompt[:80]}

        ttft_probe = _timed_generate(session_id, prompt, 1, timeout)
        gen["ttft_ms"] = ttft_probe["duration_ms"]
        gen["prefill_tokens_per_sec"] = round(
            1 / (ttft_probe["duration_ms"] / 1000.0), 2
        ) if ttft_probe["duration_ms"] > 0 else None

        if gen_tokens <= 1:
            gen.update({
                "http_status": ttft_probe["http_status"],
                "total_ms": ttft_probe["duration_ms"],
                "decode_duration_ms": 0,
                "decode_token_count": 0,
                "decode_tokens_per_sec": 0,
                "ms_per_token": ttft_probe["ms_per_token"],
                "token_count": ttft_probe["token_count"],
                "text": ttft_probe["text"],
            })
        else:
            decode = _timed_generate(session_id, prompt, gen_tokens, timeout)
            decode_only_ms = max(decode["duration_ms"] - ttft_probe["duration_ms"], 0)
            decode_count = max(decode["token_count"] - 1, 1)
            gen.update({
                "http_status": decode["http_status"],
                "total_ms": decode["duration_ms"],
                "decode_duration_ms": round(decode_only_ms, 2),
                "decode_token_count": decode_count,
                "decode_tokens_per_sec": round(
                    decode_count / (decode_only_ms / 1000.0), 2
                ) if decode_only_ms > 0 else decode["tokens_per_sec"],
                "ms_per_token": round(decode_only_ms / decode_count, 2) if decode_count else decode["ms_per_token"],
                "token_count": decode["token_count"],
                "text": decode["text"],
            })

        gen["quality"] = quality_check(prompt, gen.get("text", ""), _first_token(gen.get("text", "")))
        generations.append(gen)

    samples = sampler.stop()
    runtime["generations"] = generations
    runtime["aggregate"] = aggregate_runtime_generations(generations)
    runtime["hidden_transfer"] = estimate_hidden_hops(pipeline)
    runtime["network"] = estimate_network_delta(samples)
    runtime["runtime_samples"] = summarize_samples(samples)
    runtime["samples_raw_count"] = len(samples)
    runtime["generation_count"] = len(generations)
    return runtime


def run_persistent_distributed(
    model_cfg: dict[str, Any],
    prompts: list[str],
    gen_tokens: int,
    profile: dict[str, Any],
    cluster: dict[str, Any],
    traces_dir: Path,
    cold: bool,
    opts: PerfOptions,
) -> dict[str, Any]:
    infra = run_phase_a_infra(model_cfg, profile, cold, traces_dir, cluster, runtime_only=opts.runtime_only)
    result: dict[str, Any] = {"infrastructure": infra, "backend": "distributed"}

    if opts.infra_only:
        session_id = infra.get("session_id", "")
        if session_id:
            st, destroy_ms, _ = br.destroy_session(session_id)
            infra["session_destroy_ms"] = round(destroy_ms, 2)
            infra["destroy_status"] = st
        result["runtime"] = {"skipped": True, "reason": "infra_only"}
        return result

    session_id = infra.get("session_id", "")
    pipeline = infra.get("pipeline", [])
    runtime = run_phase_b_runtime(session_id, prompts, gen_tokens, profile, cluster, pipeline, opts)
    result["runtime"] = runtime

    st, destroy_ms, _ = br.destroy_session(session_id)
    infra["session_destroy_ms"] = round(destroy_ms, 2)
    infra["destroy_status"] = st

    result["aggregate"] = _legacy_aggregate_from_runtime(runtime)
    return result


def _legacy_aggregate_from_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    """Map runtime aggregate to keys expected by comparison/overhead modules."""
    agg = runtime.get("aggregate", {})
    out: dict[str, Any] = {}
    mapping = {
        "ttft.total_ms": ("ttft", "mean"),
        "ttft.prefill_first_token_ms": ("ttft", "mean"),
        "prefill.tokens_per_sec": ("prefill_tokens_per_sec", "mean"),
        "prefill.duration_ms": ("ttft", "mean"),
        "decode.tokens_per_sec": ("decode_tokens_per_sec", "mean"),
        "decode.ms_per_token": ("ms_per_token", "mean"),
        "decode.duration_ms": ("ms_per_token", "mean"),
    }
    for key, (section, field) in mapping.items():
        entry = agg.get(section, {})
        if isinstance(entry, dict) and entry.get(field) is not None:
            out[key] = {field: entry[field], "mean": entry.get("mean"), "median": entry.get("median"),
                        "stddev": entry.get("stddev"), "p95": entry.get("p95"),
                        "min": entry.get("min"), "max": entry.get("max"), "count": entry.get("count")}
    if agg.get("reuse_efficiency") is not None:
        out["reuse_efficiency"] = {"mean": agg["reuse_efficiency"]}
    return out


def run_distributed_repeat(
    model_cfg: dict[str, Any],
    prompt: str,
    max_tokens: int,
    profile: dict[str, Any],
    cluster: dict[str, Any],
    opts: PerfOptions | None = None,
) -> dict[str, Any]:
    """Single persistent-session benchmark run (replaces per-repeat session/create loop)."""
    opts = opts or PerfOptions()
    prompts = [prompt]
    traces_dir = Path("/tmp")
    cold = profile.get("reset_before_run", False)
    full = run_persistent_distributed(
        model_cfg, prompts, max_tokens, profile, cluster, traces_dir, cold, opts,
    )
    infra = full.get("infrastructure", {})
    runtime = full.get("runtime", {})
    agg = full.get("aggregate", {})
    pipeline = infra.get("pipeline", [])
    gens = runtime.get("generations", [])
    last = gens[-1] if gens else {}

    hidden = estimate_hidden_hops(pipeline)
    session_ms = infra.get("session_create_ms", 0)
    ttft_mean = agg.get("ttft.total_ms", {}).get("mean", 0) or 0

    return {
        "backend": "distributed",
        "persistent_session": True,
        "infrastructure": infra,
        "runtime": runtime,
        "http_status": {
            "session_create": infra.get("http_status", {}).get("session_create"),
            "last_generate": last.get("http_status"),
        },
        "load": {
            "session_configure_ms": session_ms,
            "materialization_ms": infra.get("materialization_ms"),
            "measurement_source": "direct",
        },
        "ttft": {
            "total_ms": round(session_ms + ttft_mean, 2) if ttft_mean else ttft_mean,
            "session_create_ms": session_ms,
            "prefill_first_token_ms": ttft_mean,
            "measurement_source": "direct",
        },
        "prefill": {
            "prompt_chars": len(prompt),
            "duration_ms": agg.get("prefill.duration_ms", {}).get("mean"),
            "tokens_per_sec": agg.get("prefill.tokens_per_sec", {}).get("mean"),
            "measurement_source": "direct",
        },
        "decode": {
            "duration_ms": None,
            "token_count": last.get("token_count"),
            "tokens_per_sec": agg.get("decode.tokens_per_sec", {}).get("mean"),
            "ms_per_token": agg.get("decode.ms_per_token", {}).get("mean"),
            "measurement_source": "direct",
        },
        "hidden_transfer": hidden,
        "decode_breakdown": estimate_decode_breakdown(pipeline, last.get("decode_duration_ms") or 0),
        "network": runtime.get("network", {}),
        "runtime_samples": runtime.get("runtime_samples", {}),
        "samples_raw_count": runtime.get("samples_raw_count", 0),
        "pipeline": pipeline,
        "quality": last.get("quality", {}),
        "aggregate": agg,
        "reuse_efficiency": runtime.get("aggregate", {}).get("reuse_efficiency"),
        "total_ms": infra.get("total_ms", 0),
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
    opts: PerfOptions | None = None,
) -> dict[str, Any]:
    opts = opts or PerfOptions()
    cs = row["cluster_size_target"]
    prompt_cat = opts.prompt_profile or row.get("prompt_category") or profile.get("prompt_category", "short")
    prompt_len = resolve_prompt_length(profile, row, opts)
    gen_tokens = int(row.get("generate_tokens", profile.get("generate_tokens", [16])[0] if profile.get("generate_tokens") else 16))
    n_generations = opts.generations if opts.generations is not None else int(profile.get("generations", 20))
    cold = run_mode == "cold" or profile.get("reset_before_run", False)
    persistent = opts.persistent_session or profile.get("persistent_session", True)

    scenario_id = f"{model_key}_{cs}_{run_mode}_p{prompt_len}_g{gen_tokens}"
    log(f"\n=== perf {scenario_id} (persistent={persistent}, gens={n_generations}) ===")

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

    prompts = rotate_prompts(prompt_cat, prompt_len, n_generations)

    if cs == "mono":
        return _run_mono_scenario(
            model_key, model_cfg, row, profile, run_mode, run_id, scenario_id,
            prompt_cat, prompt_len, gen_tokens, n_generations, prompts, opts, log,
        )

    if persistent:
        rep = run_persistent_distributed(
            model_cfg, prompts, gen_tokens, profile, cluster, traces_dir, cold, opts,
        )
        write_trace(traces_dir, scenario_id, rep)
        infra = rep.get("infrastructure", {})
        runtime = rep.get("runtime", {})
        if infra.get("fits_cluster") is False:
            return {
                "scenario_id": scenario_id,
                "skipped": True,
                "notes": ["fits_cluster=false"],
                "infrastructure": infra,
            }
        aggregate = rep.get("aggregate", _legacy_aggregate_from_runtime(runtime))
        return {
            "run_id": run_id,
            "scenario_id": scenario_id,
            "model_key": model_key,
            "model_id": model_cfg["model_id"],
            "cluster_size_target": cs,
            "cluster_size_observed": cluster.get("node_count"),
            "run_mode": run_mode,
            "prompt_category": prompt_cat,
            "prompt_length": prompt_len,
            "generate_tokens": gen_tokens,
            "generations": n_generations,
            "persistent_session": True,
            "started_at": utc_now(),
            "infrastructure": infra,
            "runtime": runtime,
            "aggregate": aggregate,
            "quality_summary": {
                "semantic_pass_rate": round(
                    sum(1 for g in runtime.get("generations", [])
                        if g.get("quality", {}).get("semantic_equivalence"))
                    / max(len(runtime.get("generations", [])), 1),
                    2,
                ),
            },
            "reuse_efficiency": runtime.get("aggregate", {}).get("reuse_efficiency"),
        }

    # Legacy non-persistent fallback (single repeat)
    rep = run_distributed_repeat(model_cfg, prompts[0], gen_tokens, profile, cluster, opts)
    return {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "model_key": model_key,
        "model_id": model_cfg["model_id"],
        "cluster_size_target": cs,
        "cluster_size_observed": cluster.get("node_count"),
        "run_mode": run_mode,
        "prompt_category": prompt_cat,
        "prompt_length": prompt_len,
        "generate_tokens": gen_tokens,
        "started_at": utc_now(),
        "repeats": [rep],
        "aggregate": rep.get("aggregate", {}),
        "infrastructure": rep.get("infrastructure", {}),
        "runtime": rep.get("runtime", {}),
    }


def _run_mono_scenario(
    model_key: str,
    model_cfg: dict[str, Any],
    row: dict[str, Any],
    profile: dict[str, Any],
    run_mode: str,
    run_id: str,
    scenario_id: str,
    prompt_cat: str,
    prompt_len: int,
    gen_tokens: int,
    n_generations: int,
    prompts: list[str],
    opts: PerfOptions,
    log: Callable[[str], None],
) -> dict[str, Any]:
    generations: list[dict[str, Any]] = []
    for i in range(n_generations):
        log(f"  mono generate {i + 1}/{n_generations}")
        prompt = prompts[i % len(prompts)]
        rep = run_monolithic_repeat(
            model_cfg, prompt, gen_tokens,
            int(profile.get("n_ctx", 512)),
            int(profile.get("mono_ngl", 99)),
            int(profile.get("generate_timeout_s", 300)),
        )
        rep["index"] = i
        generations.append({
            "index": i,
            "ttft_ms": rep.get("ttft", {}).get("prefill_first_token_ms"),
            "decode_tokens_per_sec": rep.get("decode", {}).get("tokens_per_sec"),
            "prefill_tokens_per_sec": rep.get("prefill", {}).get("tokens_per_sec"),
            "ms_per_token": rep.get("decode", {}).get("ms_per_token"),
            "total_ms": rep.get("total_ms"),
            "quality": rep.get("quality", {}),
        })

    runtime_agg = aggregate_runtime_generations(generations)
    aggregate = _legacy_aggregate_from_runtime({"aggregate": runtime_agg})
    return {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "model_key": model_key,
        "model_id": model_cfg["model_id"],
        "cluster_size_target": "mono",
        "cluster_size_observed": 1,
        "run_mode": run_mode,
        "prompt_category": prompt_cat,
        "prompt_length": prompt_len,
        "generate_tokens": gen_tokens,
        "generations": n_generations,
        "persistent_session": False,
        "backend": "mono",
        "started_at": utc_now(),
        "infrastructure": {"skipped": True, "reason": "mono_baseline"},
        "runtime": {"generations": generations, "aggregate": runtime_agg},
        "aggregate": aggregate,
        "quality_summary": {
            "semantic_pass_rate": round(
                sum(1 for g in generations if g.get("quality", {}).get("semantic_equivalence"))
                / max(len(generations), 1),
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
    prompt_cats = profile.get("prompt_profiles") or profile.get("prompt_categories", ["short"])
    for mk in keys:
        if mk not in models_catalog:
            continue
        for cs in profile.get("cluster_sizes", [3]):
            for cat in prompt_cats:
                pls = profile.get("prompt_lengths")
                if not pls and cat in PROMPT_PROFILE_TOKENS:
                    pls = [PROMPT_PROFILE_TOKENS[cat]]
                pls = pls or [16]
                for pl in pls:
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


def build_document_summary(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    infra_rows = []
    runtime_rows = []
    for sc in scenarios:
        if sc.get("skipped"):
            continue
        infra = sc.get("infrastructure", {})
        runtime = sc.get("runtime", {})
        runtime_agg = runtime.get("aggregate", sc.get("aggregate", {}))
        infra_rows.append({
            "model_key": sc.get("model_key"),
            "scenario_id": sc.get("scenario_id"),
            "planner_ms": infra.get("planner_ms") or infra.get("stages", {}).get("planner_ms"),
            "session_create_ms": infra.get("session_create_ms"),
            "materialization_ms": infra.get("materialization_ms"),
            "install_ms": infra.get("install_ms"),
            "session_destroy_ms": infra.get("session_destroy_ms"),
        })
        ttft = runtime_agg.get("ttft", {})
        decode = runtime_agg.get("decode_tokens_per_sec", runtime_agg.get("decode", {}))
        if isinstance(sc.get("aggregate"), dict) and not ttft:
            ttft = sc["aggregate"].get("ttft.total_ms", {})
            decode = sc["aggregate"].get("decode.tokens_per_sec", {})
        runtime_rows.append({
            "model_key": sc.get("model_key"),
            "scenario_id": sc.get("scenario_id"),
            "ttft_mean_ms": ttft.get("mean") if isinstance(ttft, dict) else None,
            "ttft_p95_ms": ttft.get("p95") if isinstance(ttft, dict) else None,
            "decode_tps_mean": decode.get("mean") if isinstance(decode, dict) else None,
            "prefill_tps_mean": (
                runtime_agg.get("prefill_tokens_per_sec", {}).get("mean")
                if isinstance(runtime_agg.get("prefill_tokens_per_sec"), dict) else None
            ),
            "ms_per_token_mean": (
                runtime_agg.get("ms_per_token", {}).get("mean")
                if isinstance(runtime_agg.get("ms_per_token"), dict) else None
            ),
            "jitter_stddev": (
                runtime_agg.get("jitter", {}).get("stddev")
                if isinstance(runtime_agg.get("jitter"), dict) else None
            ),
            "reuse_efficiency": sc.get("reuse_efficiency") or runtime_agg.get("reuse_efficiency"),
            "generation_count": runtime.get("generation_count") or sc.get("generations"),
        })
    return {
        "scenario_count": len(scenarios),
        "models": sorted({s.get("model_key", "") for s in scenarios}),
        "infrastructure": infra_rows,
        "runtime": runtime_rows,
    }


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
    opts: PerfOptions | None = None,
) -> dict[str, Any]:
    from benchmark_model import (
        PROMPT_PROFILE_ORDER,
        Checkpoint,
        model_result_to_scenarios,
        run_model_benchmark,
    )

    opts = opts or PerfOptions()
    traces_dir = out_dir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = Checkpoint(out_dir / "checkpoint.json")

    min_nodes = 1
    sizes = profile.get("cluster_sizes", [3])
    numeric = [s for s in sizes if isinstance(s, int)]
    if numeric:
        min_nodes = min(numeric)
    ok, cluster = br.wait_cluster(min_nodes if "mono" not in sizes else 0, int(profile.get("wait_cluster_timeout_s", 120)))
    if not ok and numeric:
        log(f"Cluster has {cluster.get('node_count', 0)} nodes (need >={min_nodes})")
        return {"error": "insufficient nodes", "cluster": cluster}

    model_keys = profile.get("models", [])
    if model_filter:
        model_keys = [k for k in model_keys if k == model_filter or models_catalog.get(k, {}).get("model_id") == model_filter]

    prompt_profiles = [opts.prompt_profile] if opts.prompt_profile else list(
        profile.get("prompt_profiles") or profile.get("prompt_categories", ["short"])
    )
    prompt_profiles = [p for p in PROMPT_PROFILE_ORDER if p in prompt_profiles] + [
        p for p in prompt_profiles if p not in PROMPT_PROFILE_ORDER
    ]

    profile = {**profile, "cluster_size_target": cluster_size_filter or (sizes[0] if sizes else 3)}

    if opts.resume:
        log("Resume status:")
        for row in checkpoint.model_status(model_keys):
            log(f"  {row['model_key']}: {row['status']}")

    model_results: list[dict[str, Any]] = []
    scenarios: list[dict[str, Any]] = []

    for mk in model_keys:
        if mk not in models_catalog:
            continue
        if opts.resume and checkpoint.is_done(mk):
            cached = checkpoint.data.get("models", {}).get(mk)
            if cached:
                model_results.append(cached)
                scenarios.extend(model_result_to_scenarios(cached, run_id))
            log(f"Skipping {mk} (checkpoint PASS)")
            continue

        cfg = models_catalog[mk]
        cluster = br.fetch_cluster_snapshot()
        run_mode = mode if mode in ("cold", "warm", "runtime-only") else profile.get("mode", "warm")
        if profile.get("reset_before_run"):
            run_mode = "cold"

        try:
            mr = run_model_benchmark(
                mk, cfg, profile, prompt_profiles, cluster, traces_dir, out_dir,
                run_mode, opts, log,
                phase_a_fn=run_phase_a_infra,
                rotate_prompts_fn=rotate_prompts,
                resolve_len_fn=resolve_prompt_length,
            )
        except Exception as exc:  # noqa: BLE001
            mr = {"model_key": mk, "error": str(exc)}
        model_results.append(mr)
        scenarios.extend(model_result_to_scenarios(mr, run_id))
        if not mr.get("error"):
            checkpoint.save_model(mk, mr)

    mono_baselines: dict[str, dict[str, Any]] = {}
    for sc in scenarios:
        if sc.get("cluster_size_target") == "mono" and sc.get("aggregate"):
            mono_baselines[sc.get("model_key", "")] = sc
    for sc in scenarios:
        mk = sc.get("model_key", "")
        if mk in mono_baselines and sc.get("cluster_size_target") != "mono":
            sc["overhead_vs_mono"] = compute_overhead(mono_baselines[mk], sc)

    comparison = build_comparison_table(scenarios)
    scaling = compute_scaling_table(scenarios)
    summary = build_document_summary(scenarios)
    summary["checkpoint"] = checkpoint.model_status(model_keys)
    summary["session_verification_failures"] = [
        f"{mr.get('model_key')}: {f}"
        for mr in model_results
        for f in mr.get("session_verification", {}).get("failures", [])
    ]

    return {
        "benchmark_version": "10.1.2",
        "run_id": run_id,
        "profile": profile_name,
        "mode": mode,
        "options": {
            "persistent_session": opts.persistent_session,
            "warmup": opts.warmup,
            "runtime_only": opts.runtime_only,
            "infra_only": opts.infra_only,
            "generations": opts.generations or profile.get("generations", 20),
            "prompt_profile": opts.prompt_profile,
            "verify_session": opts.verify_session,
            "resume": opts.resume,
            "prompt_profiles": prompt_profiles,
        },
        "orchestrator": br.ORCH,
        "cluster": cluster,
        "software": __import__("benchmark_export").collect_git_metadata(),
        "model_results": model_results,
        "scenarios": scenarios,
        "infrastructure": summary["infrastructure"],
        "runtime": summary["runtime"],
        "comparison": comparison,
        "scaling": scaling,
        "summary": summary,
    }
