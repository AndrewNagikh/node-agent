#!/usr/bin/env python3
"""Model-centric benchmark runner — Task 10.1.2."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import benchmark_runner as br
from benchmark_export import write_trace
from benchmark_sampler import MetricsSampler, estimate_network_delta, summarize_samples
from benchmark_session import (
    collect_cluster_snapshot,
    format_generation_debug,
    verify_against_baseline,
)
from benchmark_stats import summarize

PROMPT_PROFILE_ORDER = ("short", "medium", "long", "chat", "code", "reasoning")

PROMPT_PROFILE_TOKENS = {
    "short": 16,
    "medium": 128,
    "long": 1024,
    "code": 128,
    "chat": 64,
    "reasoning": 256,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProgressReporter:
    def __init__(self, log: Callable[[str], None]) -> None:
        self.log = log
        self._t0 = time.perf_counter()
        self._done = 0
        self._total = 0
        self._ttft_vals: list[float] = []
        self._tps_vals: list[float] = []

    def set_total(self, total: int) -> None:
        self._total = total
        self._done = 0
        self._ttft_vals.clear()
        self._tps_vals.clear()

    def tick(
        self,
        model_key: str,
        prompt_profile: str,
        gen_index: int,
        gen_total: int,
        gen_result: dict[str, Any],
        verification: dict[str, Any],
    ) -> None:
        self._done += 1
        if gen_result.get("ttft_ms") is not None:
            self._ttft_vals.append(float(gen_result["ttft_ms"]))
        if gen_result.get("decode_tokens_per_sec") is not None:
            self._tps_vals.append(float(gen_result["decode_tokens_per_sec"]))
        elapsed = time.perf_counter() - self._t0
        eta = (elapsed / self._done) * (self._total - self._done) if self._done else 0
        avg_ttft = sum(self._ttft_vals) / len(self._ttft_vals) if self._ttft_vals else 0
        avg_tps = sum(self._tps_vals) / len(self._tps_vals) if self._tps_vals else 0
        self.log(
            f"\nModel: {model_key}\n"
            f"Prompt: {prompt_profile}\n"
            f"Generation: {gen_index + 1} / {gen_total}\n"
            f"Average TTFT: {avg_ttft:.0f}ms\n"
            f"Average TPS: {avg_tps:.1f} tok/s\n"
            f"Elapsed: {elapsed:.0f}s\n"
            f"ETA: {eta:.0f}s\n"
            f"Worker reused: {'YES' if verification.get('worker_reused', True) else 'NO'}\n"
            f"Session reused: {'YES' if verification.get('session_reused', True) else 'NO'}"
        )


class Checkpoint:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {"models": {}, "completed": []}
        if path.is_file():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def is_done(self, model_key: str) -> bool:
        return model_key in self.data.get("completed", [])

    def save_model(self, model_key: str, result: dict[str, Any]) -> None:
        self.data.setdefault("models", {})[model_key] = result
        done = self.data.setdefault("completed", [])
        if model_key not in done:
            done.append(model_key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, default=str), encoding="utf-8")

    def model_status(self, model_keys: list[str]) -> list[dict[str, str]]:
        done = set(self.data.get("completed", []))
        rows = []
        for i, mk in enumerate(model_keys):
            if mk in done:
                st = "PASS"
            elif i == len(done):
                st = "RUNNING"
            else:
                st = "NOT STARTED"
            rows.append({"model_key": mk, "status": st})
        return rows


def measure_generate(
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
    client_ms = (time.perf_counter() - t0) * 1000.0
    timing = out.get("timing", {}) if isinstance(out, dict) else {}
    text = out.get("text", "") if isinstance(out, dict) else ""
    generated = int(out.get("count", len(out.get("tokens", []))) if isinstance(out, dict) else 0)
    prompt_tokens = int(timing.get("prompt_tokens", 0))
    total_ms = float(timing.get("total_ms", client_ms))
    ttft_ms = float(timing.get("ttft_ms", timing.get("prefill_ms", total_ms)))
    decode_ms = float(timing.get("decode_ms", max(0.0, total_ms - ttft_ms)))
    decode_tps = timing.get("decode_tokens_per_sec")
    if decode_tps is None and decode_ms > 0 and generated > 1:
        decode_tps = round((generated - 1) * 1000.0 / decode_ms, 2)
    elif decode_tps is None and generated > 0 and total_ms > 0:
        decode_tps = round(generated * 1000.0 / total_ms, 2)
    return {
        "http_status": status,
        "total_latency_ms": round(total_ms, 2),
        "ttft_ms": round(ttft_ms, 2),
        "decode_ms": round(decode_ms, 2),
        "decode_tokens_per_sec": decode_tps,
        "prompt_tokens": prompt_tokens,
        "generated_tokens": generated,
        "text": text,
        "timing": timing,
        "session_stats": out.get("session_stats", {}) if isinstance(out, dict) else {},
    }


def aggregate_generations(generations: list[dict[str, Any]]) -> dict[str, Any]:
    ttft = [g["ttft_ms"] for g in generations if g.get("ttft_ms") is not None]
    tps = [g["decode_tokens_per_sec"] for g in generations if g.get("decode_tokens_per_sec") is not None]
    decode_ms = [g["decode_ms"] for g in generations if g.get("decode_ms") is not None]
    total = [g["total_latency_ms"] for g in generations if g.get("total_latency_ms") is not None]
    ms_per_token = []
    for g in generations:
        if g.get("decode_ms") and g.get("generated_tokens", 0) > 1:
            ms_per_token.append(g["decode_ms"] / max(g["generated_tokens"] - 1, 1))
    agg = {
        "ttft": summarize(ttft),
        "decode_tokens_per_sec": summarize(tps),
        "decode_ms": summarize(decode_ms),
        "total_latency_ms": summarize(total),
        "ms_per_token": summarize(ms_per_token),
        "jitter": summarize(ms_per_token),
    }
    if len(total) >= 2:
        agg["reuse_efficiency"] = round(
            (sum(total[1:]) / len(total[1:])) / total[0], 4,
        ) if total[0] > 0 else None
    return agg


def run_prompt_group(
    session_id: str,
    pipeline: list[dict[str, Any]],
    cluster: dict[str, Any],
    prompt_profile: str,
    prompts: list[str],
    gen_tokens: int,
    n_generations: int,
    profile: dict[str, Any],
    model_key: str,
    progress: ProgressReporter,
    log: Callable[[str], None],
    debug_log_path: Path | None,
    baseline_snap: dict[str, Any] | None,
) -> dict[str, Any]:
    timeout = int(profile.get("generate_timeout_s", 300))
    generations: list[dict[str, Any]] = []
    verifications: list[dict[str, Any]] = []
    failures: list[str] = []

    for i in range(n_generations):
        prompt = prompts[i % len(prompts)]
        snap = collect_cluster_snapshot(session_id, pipeline, cluster)
        verification = verify_against_baseline(baseline_snap, snap, i)
        verifications.append(verification)
        if not verification.get("valid", True):
            failures.extend(verification.get("reasons", []))

        debug_text = format_generation_debug(i, snap, verification)
        log(debug_text)
        if debug_log_path:
            with debug_log_path.open("a", encoding="utf-8") as fh:
                fh.write(debug_text + "\n\n")

        gen = measure_generate(session_id, prompt, gen_tokens, timeout)
        gen["index"] = i
        gen["prompt_profile"] = prompt_profile
        gen["prompt_preview"] = prompt[:80]
        gen["verification"] = verification
        generations.append(gen)
        progress.tick(model_key, prompt_profile, i, n_generations, gen, verification)

    return {
        "prompt_profile": prompt_profile,
        "generations": n_generations,
        "generate_tokens": gen_tokens,
        "runtime": {
            "generations": generations,
            "aggregate": aggregate_generations(generations),
            "verification_failures": failures,
            "session_valid": len(failures) == 0,
        },
        "aggregate": _legacy_from_runtime_agg(aggregate_generations(generations)),
    }


def _legacy_from_runtime_agg(agg: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    mapping = {
        "ttft.total_ms": "ttft",
        "decode.tokens_per_sec": "decode_tokens_per_sec",
        "decode.ms_per_token": "ms_per_token",
    }
    for key, section in mapping.items():
        entry = agg.get(section, {})
        if isinstance(entry, dict) and entry.get("mean") is not None:
            out[key] = dict(entry)
    return out


def run_model_benchmark(
    model_key: str,
    model_cfg: dict[str, Any],
    profile: dict[str, Any],
    prompt_profiles: list[str],
    cluster: dict[str, Any],
    traces_dir: Path,
    out_dir: Path,
    run_mode: str,
    opts: Any,
    log: Callable[[str], None],
    phase_a_fn: Callable[..., dict[str, Any]],
    rotate_prompts_fn: Callable[..., list[str]],
    resolve_len_fn: Callable[..., int],
) -> dict[str, Any]:
    from benchmark_perf import PerfOptions  # noqa: WPS433

    opts = opts or PerfOptions()
    gen_tokens = int(profile.get("generate_tokens", [16])[0] if isinstance(profile.get("generate_tokens"), list) else profile.get("generate_tokens", 16))
    n_generations = opts.generations or int(profile.get("generations", 20))
    cold = run_mode == "cold" or profile.get("reset_before_run", False)
    cs = profile.get("cluster_size_target", profile.get("cluster_sizes", [3])[0])

    log(f"\n{'='*60}\nModel: {model_key} | session-once | prompts={prompt_profiles}\n{'='*60}")

    if not opts.runtime_only:
        infra = phase_a_fn(model_cfg, profile, cold, traces_dir, cluster, runtime_only=False)
    else:
        infra = phase_a_fn(model_cfg, profile, False, traces_dir, cluster, runtime_only=True)

    if infra.get("fits_cluster") is False:
        return {"model_key": model_key, "skipped": True, "infrastructure": infra}

    session_id = infra.get("session_id", "")
    pipeline = infra.get("pipeline", [])
    if not session_id:
        return {"model_key": model_key, "error": "no session_id", "infrastructure": infra}

    debug_log = out_dir / f"debug_{model_key}.log"
    debug_log.write_text(f"# Runtime verification log — {model_key}\n\n", encoding="utf-8")

    sampler = MetricsSampler(
        int(profile.get("sample_interval_ms", 200)),
        br.fetch_cluster_snapshot,
        lambda c: {},  # filled by cluster snapshot only
    )
    sampler.start()

    if opts.warmup and profile.get("warmup", True):
        warmup_prompt = profile.get("warmup_prompt", "Hello")
        w = measure_generate(session_id, warmup_prompt, int(profile.get("warmup_tokens", 4)), int(profile.get("generate_timeout_s", 300)))
        infra["warmup_ms"] = w.get("total_latency_ms")
        log(f"Warmup done ({w.get('total_latency_ms')}ms) — discarded")

    baseline_snap = collect_cluster_snapshot(session_id, pipeline, cluster)
    log(format_generation_debug(0, baseline_snap))

    total_gens = len(prompt_profiles) * n_generations
    progress = ProgressReporter(log)
    progress.set_total(total_gens)

    prompt_groups: list[dict[str, Any]] = []
    all_verification_failures: list[str] = []

    for cat in prompt_profiles:
        plen = resolve_len_fn(profile, {"prompt_category": cat}, opts)
        prompts = rotate_prompts_fn(cat, plen, max(n_generations, 3))
        group = run_prompt_group(
            session_id, pipeline, cluster, cat, prompts, gen_tokens, n_generations,
            profile, model_key, progress, log, debug_log, baseline_snap,
        )
        prompt_groups.append(group)
        all_verification_failures.extend(group.get("runtime", {}).get("verification_failures", []))

    samples = sampler.stop()

    st, destroy_ms, _ = br.destroy_session(session_id)
    infra["session_destroy_ms"] = round(destroy_ms, 2)
    infra["destroy_status"] = st

    result = {
        "model_key": model_key,
        "model_id": model_cfg["model_id"],
        "cluster_size_target": cs,
        "cluster_size_observed": cluster.get("node_count"),
        "run_mode": run_mode,
        "persistent_session": True,
        "session_id": session_id,
        "started_at": utc_now(),
        "infrastructure": infra,
        "prompt_groups": prompt_groups,
        "resource_samples": summarize_samples(samples),
        "session_verification": {
            "valid": len(all_verification_failures) == 0,
            "failures": all_verification_failures,
        },
        "debug_log": str(debug_log),
    }
    write_trace(traces_dir, f"model_{model_key}", result)
    return result


def model_result_to_scenarios(model_result: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    """Flatten model result into per-prompt scenarios for reports."""
    if model_result.get("skipped") or model_result.get("error"):
        return [{
            "run_id": run_id,
            "scenario_id": f"{model_result.get('model_key')}_skipped",
            "model_key": model_result.get("model_key"),
            "skipped": model_result.get("skipped", False),
            "error": model_result.get("error"),
        }]
    scenarios = []
    infra = model_result.get("infrastructure", {})
    for group in model_result.get("prompt_groups", []):
        cat = group.get("prompt_profile", "short")
        runtime = group.get("runtime", {})
        scenarios.append({
            "run_id": run_id,
            "scenario_id": f"{model_result['model_key']}_{model_result.get('cluster_size_target')}_{cat}",
            "model_key": model_result["model_key"],
            "model_id": model_result.get("model_id"),
            "cluster_size_target": model_result.get("cluster_size_target"),
            "cluster_size_observed": model_result.get("cluster_size_observed"),
            "run_mode": model_result.get("run_mode"),
            "prompt_category": cat,
            "generate_tokens": group.get("generate_tokens"),
            "generations": group.get("generations"),
            "persistent_session": True,
            "infrastructure": infra if not scenarios else {"reused": True, "session_id": model_result.get("session_id")},
            "runtime": runtime,
            "aggregate": group.get("aggregate", {}),
            "session_verification": model_result.get("session_verification"),
            "reuse_efficiency": runtime.get("aggregate", {}).get("reuse_efficiency"),
        })
    return scenarios
