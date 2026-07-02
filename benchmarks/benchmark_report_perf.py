#!/usr/bin/env python3
"""Task 10.1 performance reports — Markdown, HTML, comparison tables."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def _mean(agg: dict[str, Any], key: str) -> str:
    entry = agg.get(key, {})
    if isinstance(entry, dict) and entry.get("mean") is not None:
        v = entry["mean"]
        if "tps" in key or "tokens_per_sec" in key:
            return f"{v:.1f} tok/s"
        if v >= 1000:
            return f"{v / 1000:.2f}s"
        return f"{v:.0f}ms"
    return "—"


def build_perf_markdown(doc: dict[str, Any]) -> str:
    cluster = doc.get("cluster", {})
    mem = cluster.get("memory", {})
    lines = [
        "# Cluster Performance Benchmark (Task 10.1)",
        "",
        f"**Run ID:** `{doc.get('run_id', '')}`  ",
        f"**Profile:** `{doc.get('profile', '')}`  ",
        f"**Mode:** `{doc.get('mode', '')}`  ",
        "",
        "## Cluster",
        "",
        f"- Nodes: **{cluster.get('node_count', 0)}**",
        f"- Free memory: **{mem.get('free_total_gb', '?')} GB**",
        "",
        "## Monolithic vs Distributed",
        "",
        "| Size | TTFT | Decode TPS | Prefill TPS | Load/Install | Hidden hop |",
        "|------|------|------------|-------------|--------------|------------|",
    ]

    comparison = doc.get("comparison", {})
    for cs in sorted(comparison.keys(), key=lambda x: (x != "mono", x)):
        row = comparison[cs]
        lines.append(
            f"| {cs} | {_fmt(row.get('ttft_ms'))} | {_fmt_tps(row.get('decode_tps'))} | "
            f"{_fmt_tps(row.get('prefill_tps'))} | {_fmt(row.get('load_ms') or row.get('install_ms'))} | "
            f"{_fmt(row.get('hidden_latency_ms'))} |"
        )

    lines.extend(["", "## Scaling Efficiency", ""])
    scaling = doc.get("scaling", [])
    if scaling:
        lines.append("| Model | Nodes | TPS | Speedup | Efficiency |")
        lines.append("|-------|-------|-----|---------|------------|")
        for row in scaling:
            eff = row.get("efficiency")
            eff_s = f"{100 * eff:.0f}%" if isinstance(eff, (int, float)) else "—"
            lines.append(
                f"| {row.get('model_key')} | {row.get('cluster_size')} | "
                f"{_fmt_tps(row.get('decode_tps'))} | {row.get('speedup', '—')} | {eff_s} |"
            )

    lines.extend(["", "## Scenarios", ""])
    for sc in doc.get("scenarios", []):
        if sc.get("skipped"):
            lines.append(f"- **{sc.get('scenario_id')}** — skipped")
            continue
        agg = sc.get("aggregate", {})
        oh = sc.get("overhead_vs_mono", {})
        lines.append(
            f"- **{sc.get('scenario_id')}** — TTFT {_mean(agg, 'ttft.total_ms')}, "
            f"decode {_mean(agg, 'decode.tokens_per_sec')}, "
            f"semantic pass {sc.get('quality_summary', {}).get('semantic_pass_rate', '—')}"
        )
        if oh.get("ttft_overhead_pct") is not None:
            lines.append(f"  - overhead vs mono: TTFT +{oh['ttft_overhead_pct']}%, TPS {oh.get('tps_overhead_pct')}%")

    lines.extend(["", "## Measurement Notes", ""])
    lines.append("- **Direct:** wall-clock API / llama-cli subprocess")
    lines.append("- **Derived:** overhead, scaling, hop latency estimates")
    lines.append("- **Unavailable:** per-hop hidden bytes, GPU busy % (needs runtime exporters)")
    lines.append("")
    return "\n".join(lines)


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if v >= 1000:
        return f"{v / 1000:.2f}s"
    return f"{v:.0f}ms"


def _fmt_tps(v: Any) -> str:
    if v is None:
        return "—"
    return f"{v:.1f} tok/s"


def build_perf_html(doc: dict[str, Any]) -> str:
    comparison = doc.get("comparison", {})
    labels = sorted(comparison.keys(), key=lambda x: (x != "mono", x))
    ttft_vals = [comparison[k].get("ttft_ms") or 0 for k in labels]
    tps_vals = [comparison[k].get("decode_tps") or 0 for k in labels]
    mx_ttft = max(ttft_vals) if ttft_vals else 1
    mx_tps = max(tps_vals) if tps_vals else 1

    def bars(vals: list[float], mx: float) -> str:
        rows = []
        for label, val in zip(labels, vals):
            w = int(24 * val / mx) if mx else 0
            rows.append(f"<tr><td>{html.escape(str(label))}</td><td><code>{'█' * w}</code></td><td>{val:.1f}</td></tr>")
        return "".join(rows)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Perf Benchmark {html.escape(doc.get('run_id',''))}</title>
<style>body{{font-family:system-ui;background:#0f1115;color:#e8eaed;margin:2rem}}
h1,h2{{color:#8ab4f8}} table{{border-collapse:collapse}} td,th{{padding:.4rem .8rem;border-bottom:1px solid #333}}
code{{color:#81c995}}</style></head><body>
<h1>Performance Benchmark 10.1</h1>
<p>Run <code>{html.escape(doc.get('run_id',''))}</code> profile <code>{html.escape(doc.get('profile',''))}</code></p>
<h2>TTFT (ms)</h2><table>{bars(ttft_vals, mx_ttft)}</table>
<h2>Decode TPS</h2><table>{bars(tps_vals, mx_tps)}</table>
</body></html>"""


def export_perf_csv(path: Path, scenarios: list[dict[str, Any]]) -> None:
    import csv
    cols = [
        "scenario_id", "model_key", "cluster_size", "run_mode", "prompt_length", "generate_tokens",
        "ttft_mean_ms", "ttft_stddev", "decode_tps_mean", "decode_tps_stddev",
        "prefill_tps_mean", "ms_per_token_mean", "install_ms", "planner_ms",
        "ttft_overhead_pct", "semantic_pass_rate",
    ]
    rows = []
    for sc in scenarios:
        agg = sc.get("aggregate", {})
        oh = sc.get("overhead_vs_mono", {})
        rows.append({
            "scenario_id": sc.get("scenario_id"),
            "model_key": sc.get("model_key"),
            "cluster_size": sc.get("cluster_size_target"),
            "run_mode": sc.get("run_mode"),
            "prompt_length": sc.get("prompt_length"),
            "generate_tokens": sc.get("generate_tokens"),
            "ttft_mean_ms": agg.get("ttft.total_ms", {}).get("mean"),
            "ttft_stddev": agg.get("ttft.total_ms", {}).get("stddev"),
            "decode_tps_mean": agg.get("decode.tokens_per_sec", {}).get("mean"),
            "decode_tps_stddev": agg.get("decode.tokens_per_sec", {}).get("stddev"),
            "prefill_tps_mean": agg.get("prefill.tokens_per_sec", {}).get("mean"),
            "ms_per_token_mean": agg.get("decode.ms_per_token", {}).get("mean"),
            "install_ms": sc.get("cold", {}).get("install_ms"),
            "planner_ms": sc.get("cold", {}).get("planner_ms"),
            "ttft_overhead_pct": oh.get("ttft_overhead_pct"),
            "semantic_pass_rate": sc.get("quality_summary", {}).get("semantic_pass_rate"),
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def write_perf_reports(out_dir: Path, document: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.md").write_text(build_perf_markdown(document), encoding="utf-8")
    (out_dir / "report.html").write_text(build_perf_html(document), encoding="utf-8")
    export_perf_csv(out_dir / "results_perf.csv", document.get("scenarios", []))
    (out_dir / "comparison.json").write_text(
        json.dumps({"comparison": document.get("comparison"), "scaling": document.get("scaling")}, indent=2),
        encoding="utf-8",
    )


def load_results(path: Path) -> dict[str, Any]:
    if path.is_dir():
        path = path / "results.json"
    return json.loads(path.read_text(encoding="utf-8"))
