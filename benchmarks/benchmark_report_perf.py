#!/usr/bin/env python3
"""Task 10.1.1 performance reports — Markdown, HTML with Infrastructure/Runtime tabs."""

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


def _infra_cell(v: Any) -> str:
    if v is None:
        return "—"
    if v >= 1000:
        return f"{v / 1000:.2f}s"
    return f"{v:.0f}ms"


def _runtime_cell(v: Any, suffix: str = "") -> str:
    if v is None:
        return "—"
    if suffix == "tps":
        return f"{v:.1f} tok/s"
    if v >= 1000:
        return f"{v / 1000:.2f}s"
    return f"{v:.1f}{suffix}"


def build_infra_table_rows(doc: dict[str, Any]) -> list[str]:
    rows = []
    for row in doc.get("infrastructure", []):
        rows.append(
            f"| {html.escape(str(row.get('model_key', '')))} "
            f"| {_infra_cell(row.get('planner_ms'))} "
            f"| {_infra_cell(row.get('session_create_ms'))} "
            f"| {_infra_cell(row.get('materialization_ms'))} "
            f"| {_infra_cell(row.get('install_ms'))} "
            f"| {_infra_cell(row.get('session_destroy_ms'))} |"
        )
    return rows


def build_runtime_table_rows(doc: dict[str, Any]) -> list[str]:
    rows = []
    for row in doc.get("runtime", []):
        eff = row.get("reuse_efficiency")
        eff_s = f"{eff:.2f}" if isinstance(eff, (int, float)) else "—"
        rows.append(
            f"| {html.escape(str(row.get('model_key', '')))} "
            f"| {_runtime_cell(row.get('ttft_mean_ms'))} "
            f"| {_runtime_cell(row.get('ttft_p95_ms'))} "
            f"| {_runtime_cell(row.get('decode_tps_mean'), 'tps')} "
            f"| {_runtime_cell(row.get('prefill_tps_mean'), 'tps')} "
            f"| {_runtime_cell(row.get('ms_per_token_mean'), 'ms')} "
            f"| {_runtime_cell(row.get('jitter_stddev'), 'ms')} "
            f"| {eff_s} |"
        )
    return rows


def build_perf_markdown(doc: dict[str, Any]) -> str:
    cluster = doc.get("cluster", {})
    mem = cluster.get("memory", {})
    opts = doc.get("options", {})
    lines = [
        "# Cluster Performance Benchmark (Task 10.1.1)",
        "",
        f"**Run ID:** `{doc.get('run_id', '')}`  ",
        f"**Profile:** `{doc.get('profile', '')}`  ",
        f"**Mode:** `{doc.get('mode', '')}`  ",
        f"**Persistent session:** `{opts.get('persistent_session', True)}`  ",
        f"**Generations:** `{opts.get('generations', 20)}`  ",
        "",
        "## Cluster",
        "",
        f"- Nodes: **{cluster.get('node_count', 0)}**",
        f"- Free memory: **{mem.get('free_total_gb', '?')} GB**",
        "",
        "## Infrastructure Metrics",
        "",
        "| Model | Planner | Session Create | Materialization | Install | Destroy |",
        "|-------|---------|----------------|-----------------|---------|---------|",
    ]
    for row in doc.get("infrastructure", []):
        lines.append(
            f"| {row.get('model_key')} | {_infra_cell(row.get('planner_ms'))} "
            f"| {_infra_cell(row.get('session_create_ms'))} "
            f"| {_infra_cell(row.get('materialization_ms'))} "
            f"| {_infra_cell(row.get('install_ms'))} "
            f"| {_infra_cell(row.get('session_destroy_ms'))} |"
        )

    lines.extend([
        "",
        "## Runtime Metrics",
        "",
        "| Model | TTFT | TTFT p95 | Decode TPS | Prefill TPS | ms/token | Jitter | Reuse Eff. |",
        "|-------|------|----------|------------|-------------|----------|--------|------------|",
    ])
    for row in doc.get("runtime", []):
        eff = row.get("reuse_efficiency")
        eff_s = f"{eff:.2f}" if isinstance(eff, (int, float)) else "—"
        lines.append(
            f"| {row.get('model_key')} | {_runtime_cell(row.get('ttft_mean_ms'))} "
            f"| {_runtime_cell(row.get('ttft_p95_ms'))} "
            f"| {_runtime_cell(row.get('decode_tps_mean'), 'tps')} "
            f"| {_runtime_cell(row.get('prefill_tps_mean'), 'tps')} "
            f"| {_runtime_cell(row.get('ms_per_token_mean'), 'ms')} "
            f"| {_runtime_cell(row.get('jitter_stddev'))} "
            f"| {eff_s} |"
        )

    lines.extend(["", "## Monolithic vs Distributed", ""])
    comparison = doc.get("comparison", {})
    lines.append("| Size | TTFT | Decode TPS | Prefill TPS | Load/Install | Hidden hop |")
    lines.append("|------|------|------------|-------------|--------------|------------|")
    for cs in sorted(comparison.keys(), key=lambda x: (x != "mono", x)):
        row = comparison[cs]
        lines.append(
            f"| {cs} | {_fmt(row.get('ttft_ms'))} | {_fmt_tps(row.get('decode_tps'))} | "
            f"{_fmt_tps(row.get('prefill_tps'))} | {_fmt(row.get('load_ms') or row.get('install_ms'))} | "
            f"{_fmt(row.get('hidden_latency_ms'))} |"
        )

    lines.extend(["", "## Scenarios", ""])
    for sc in doc.get("scenarios", []):
        if sc.get("skipped"):
            lines.append(f"- **{sc.get('scenario_id')}** — skipped")
            continue
        agg = sc.get("aggregate", {})
        oh = sc.get("overhead_vs_mono", {})
        gens = sc.get("generations") or sc.get("runtime", {}).get("generation_count", "?")
        lines.append(
            f"- **{sc.get('scenario_id')}** — {gens} generates, "
            f"TTFT {_mean(agg, 'ttft.total_ms')}, decode {_mean(agg, 'decode.tokens_per_sec')}, "
            f"reuse eff {sc.get('reuse_efficiency', '—')}"
        )
        if oh.get("ttft_overhead_pct") is not None:
            lines.append(f"  - overhead vs mono: TTFT +{oh['ttft_overhead_pct']}%, TPS {oh.get('tps_overhead_pct')}%")

    lines.extend(["", "## Measurement Notes", ""])
    lines.append("- **Phase A (Infrastructure):** register → layout → sync → session create (once per model)")
    lines.append("- **Phase B (Runtime):** warmup + N generates on persistent session")
    lines.append("- **Jitter:** stddev of per-token decode latency across generations")
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
            rows.append(
                f"<tr><td>{html.escape(str(label))}</td>"
                f"<td><code>{'█' * w}</code></td><td>{val:.1f}</td></tr>"
            )
        return "".join(rows)

    infra_rows = "\n".join(build_infra_table_rows(doc)) or "<tr><td colspan='6'>No data</td></tr>"
    runtime_rows = "\n".join(build_runtime_table_rows(doc)) or "<tr><td colspan='8'>No data</td></tr>"
    opts = doc.get("options", {})

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Perf Benchmark {html.escape(doc.get('run_id',''))}</title>
<style>
body{{font-family:system-ui;background:#0f1115;color:#e8eaed;margin:2rem}}
h1,h2{{color:#8ab4f8}}
table{{border-collapse:collapse;width:100%;margin-bottom:1.5rem}}
td,th{{padding:.4rem .8rem;border-bottom:1px solid #333;text-align:left}}
code{{color:#81c995}}
.tab-bar{{display:flex;gap:.5rem;margin:1rem 0}}
.tab-btn{{background:#1a1d24;color:#8ab4f8;border:1px solid #333;padding:.5rem 1rem;cursor:pointer;border-radius:4px}}
.tab-btn.active{{background:#2d3a52}}
.tab-panel{{display:none}}
.tab-panel.active{{display:block}}
</style>
<script>
function showTab(id) {{
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  document.querySelector('[data-tab="'+id+'"]').classList.add('active');
}}
</script>
</head><body>
<h1>Performance Benchmark 10.1.1</h1>
<p>Run <code>{html.escape(doc.get('run_id',''))}</code> profile <code>{html.escape(doc.get('profile',''))}</code>
 — persistent={opts.get('persistent_session', True)}, generations={opts.get('generations', 20)}</p>

<div class="tab-bar">
  <button class="tab-btn active" data-tab="infra" onclick="showTab('infra')">Infrastructure</button>
  <button class="tab-btn" data-tab="runtime" onclick="showTab('runtime')">Runtime</button>
  <button class="tab-btn" data-tab="charts" onclick="showTab('charts')">Charts</button>
</div>

<div id="infra" class="tab-panel active">
<h2>Infrastructure Metrics</h2>
<table>
<tr><th>Model</th><th>Planner</th><th>Session Create</th><th>Materialization</th><th>Install</th><th>Destroy</th></tr>
{infra_rows}
</table>
</div>

<div id="runtime" class="tab-panel">
<h2>Runtime Metrics</h2>
<table>
<tr><th>Model</th><th>TTFT</th><th>TTFT p95</th><th>Decode TPS</th><th>Prefill TPS</th><th>ms/token</th><th>Jitter</th><th>Reuse Eff.</th></tr>
{runtime_rows}
</table>
</div>

<div id="charts" class="tab-panel">
<h2>TTFT (ms)</h2><table>{bars(ttft_vals, mx_ttft)}</table>
<h2>Decode TPS</h2><table>{bars(tps_vals, mx_tps)}</table>
</div>
</body></html>"""


def export_perf_csv(path: Path, scenarios: list[dict[str, Any]]) -> None:
    import csv
    cols = [
        "scenario_id", "model_key", "cluster_size", "run_mode", "prompt_length", "generate_tokens",
        "generations", "persistent_session",
        "planner_ms", "session_create_ms", "materialization_ms", "session_destroy_ms",
        "ttft_mean_ms", "ttft_p95_ms", "ttft_stddev",
        "decode_tps_mean", "decode_tps_stddev", "decode_tps_p95",
        "prefill_tps_mean", "ms_per_token_mean", "jitter_stddev",
        "reuse_efficiency", "ttft_overhead_pct", "semantic_pass_rate",
    ]
    rows = []
    for sc in scenarios:
        agg = sc.get("aggregate", {})
        infra = sc.get("infrastructure", {})
        runtime_agg = sc.get("runtime", {}).get("aggregate", {})
        oh = sc.get("overhead_vs_mono", {})
        ttft = runtime_agg.get("ttft", agg.get("ttft.total_ms", {}))
        decode = runtime_agg.get("decode_tokens_per_sec", agg.get("decode.tokens_per_sec", {}))
        prefill = runtime_agg.get("prefill_tokens_per_sec", agg.get("prefill.tokens_per_sec", {}))
        mspt = runtime_agg.get("ms_per_token", agg.get("decode.ms_per_token", {}))
        jitter = runtime_agg.get("jitter", {})
        rows.append({
            "scenario_id": sc.get("scenario_id"),
            "model_key": sc.get("model_key"),
            "cluster_size": sc.get("cluster_size_target"),
            "run_mode": sc.get("run_mode"),
            "prompt_length": sc.get("prompt_length"),
            "generate_tokens": sc.get("generate_tokens"),
            "generations": sc.get("generations"),
            "persistent_session": sc.get("persistent_session"),
            "planner_ms": infra.get("planner_ms") or infra.get("stages", {}).get("planner_ms"),
            "session_create_ms": infra.get("session_create_ms"),
            "materialization_ms": infra.get("materialization_ms"),
            "session_destroy_ms": infra.get("session_destroy_ms"),
            "ttft_mean_ms": ttft.get("mean") if isinstance(ttft, dict) else None,
            "ttft_p95_ms": ttft.get("p95") if isinstance(ttft, dict) else None,
            "ttft_stddev": ttft.get("stddev") if isinstance(ttft, dict) else None,
            "decode_tps_mean": decode.get("mean") if isinstance(decode, dict) else None,
            "decode_tps_stddev": decode.get("stddev") if isinstance(decode, dict) else None,
            "decode_tps_p95": decode.get("p95") if isinstance(decode, dict) else None,
            "prefill_tps_mean": prefill.get("mean") if isinstance(prefill, dict) else None,
            "ms_per_token_mean": mspt.get("mean") if isinstance(mspt, dict) else None,
            "jitter_stddev": jitter.get("stddev") if isinstance(jitter, dict) else None,
            "reuse_efficiency": sc.get("reuse_efficiency") or runtime_agg.get("reuse_efficiency"),
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
        json.dumps({
            "comparison": document.get("comparison"),
            "scaling": document.get("scaling"),
            "infrastructure": document.get("infrastructure"),
            "runtime": document.get("runtime"),
            "summary": document.get("summary"),
        }, indent=2),
        encoding="utf-8",
    )


def load_results(path: Path) -> dict[str, Any]:
    if path.is_dir():
        path = path / "results.json"
    return json.loads(path.read_text(encoding="utf-8"))
