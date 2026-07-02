#!/usr/bin/env python3
"""Generate Markdown and HTML reports from benchmark results."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def _stage_ms(scenario: dict[str, Any], name: str) -> float | None:
    for s in scenario.get("stages", []):
        if s.get("name") == name:
            return s.get("duration_ms")
    return None


def _stage_metric(scenario: dict[str, Any], stage: str, key: str) -> Any:
    for s in scenario.get("stages", []):
        if s.get("name") == stage:
            return s.get("metrics", {}).get(key)
    return None


def _bar(value: float, max_value: float, width: int = 20) -> str:
    if max_value <= 0:
        return ""
    filled = int(round(width * min(value, max_value) / max_value))
    return "█" * filled


def summarize_scenario(sc: dict[str, Any]) -> dict[str, Any]:
    return {
        "scenario_id": sc.get("scenario_id", ""),
        "model": sc.get("model_key", ""),
        "planner_ms": _stage_ms(sc, "layout"),
        "install_ms": _stage_ms(sc, "synchronization"),
        "coverage_ms": _stage_ms(sc, "coverage"),
        "materialization_ms": _stage_ms(sc, "materialization"),
        "session_ms": _stage_ms(sc, "session_create"),
        "generate_ms": _stage_ms(sc, "generate"),
        "generate_tps": _stage_metric(sc, "generate", "tokens_per_sec"),
        "prefill_ms": _stage_metric(sc, "generate", "prefill_ms"),
        "decode_ms": _stage_metric(sc, "generate", "decode_ms"),
        "fits_cluster": _stage_metric(sc, "layout", "fits_cluster"),
        "placements": _stage_metric(sc, "layout", "placement_count"),
    }


def build_markdown(document: dict[str, Any]) -> str:
    cluster = document.get("cluster", {})
    mem = cluster.get("memory", {})
    scenarios = document.get("scenarios", [])
    lines = [
        "# Cluster Benchmark",
        "",
        f"**Run ID:** `{document.get('run_id', '')}`  ",
        f"**Profile:** `{document.get('profile', '')}`  ",
        f"**Mode:** `{document.get('mode', '')}`  ",
        f"**Orchestrator:** `{document.get('orchestrator', '')}`  ",
        "",
        "## Cluster",
        "",
        f"- Nodes: **{cluster.get('node_count', 0)}**",
        f"- Free memory: **{mem.get('free_total_gb', '?')} GB** (RAM {mem.get('free_ram_gb', '?')} + VRAM {mem.get('free_vram_gb', '?')})",
        f"- Total memory: **{mem.get('total_total_gb', '?')} GB**",
        "",
    ]

    for n in cluster.get("nodes", []):
        lines.append(
            f"  - `{n.get('node_id')}` {n.get('gpu', '')} — "
            f"RAM {n.get('free_ram_gb')}/{n.get('total_ram_gb')} GB, "
            f"VRAM {n.get('free_vram_gb')}/{n.get('total_vram_gb')} GB"
        )

    lines.extend(["", "## Software", ""])
    sw = document.get("software", {})
    na = sw.get("node_agent", {})
    ll = sw.get("llama_cpp", {})
    lines.extend([
        f"- node-agent: `{na.get('sha', '')[:12]}` ({na.get('branch', '')})",
        f"- llama.cpp: `{ll.get('sha', '')[:12]}`",
        f"- build: **{sw.get('build_type', '')}** / backend **{sw.get('backend', '')}**",
        f"- OS: {sw.get('os', '')} {sw.get('arch', '')}",
        "",
        "## Scenarios",
        "",
        "| Model | Planner | Install | Coverage | Materialize | Generate TPS | Fits |",
        "|-------|---------|---------|----------|-------------|--------------|------|",
    ])

    for sc in scenarios:
        if sc.get("skipped"):
            lines.append(f"| {sc.get('model_key')} | — | — | — | — | — | skipped |")
            continue
        sm = summarize_scenario(sc)
        lines.append(
            f"| {sm['model']} | {fmt_ms(sm['planner_ms'])} | {fmt_s(sm['install_ms'])} | "
            f"{fmt_ms(sm['coverage_ms'])} | {fmt_ms(sm['materialization_ms'])} | "
            f"{fmt_tps(sm['generate_tps'])} | {sm['fits_cluster']} |"
        )

    if scenarios:
        sc = next((s for s in scenarios if not s.get("skipped")), scenarios[0])
        sm = summarize_scenario(sc)
        lines.extend([
            "",
            "## Highlights (first scenario)",
            "",
            f"- Planner: **{fmt_ms(sm['planner_ms'])}** ({sm['placements']} placements)",
            f"- Install: **{fmt_s(sm['install_ms'])}**",
            f"- Coverage: **{fmt_ms(sm['coverage_ms'])}**",
            f"- Materialization: **{fmt_ms(sm['materialization_ms'])}**",
            f"- Generate: **{fmt_tps(sm['generate_tps'])}**",
            f"- Prefill: **{fmt_ms(sm['prefill_ms'])}** / Decode: **{fmt_ms(sm['decode_ms'])}** per token",
        ])

    summary = document.get("summary", {})
    if summary.get("generate_tps_avg"):
        lines.extend([
            "",
            "## Summary",
            "",
            f"- Scenarios: {summary.get('scenario_count', 0)}",
            f"- Avg generate TPS: **{summary.get('generate_tps_avg')}**",
            f"- Max generate TPS: **{summary.get('generate_tps_max')}**",
        ])

    lines.append("")
    return "\n".join(lines)


def fmt_ms(v: Any) -> str:
    if v is None:
        return "—"
    if v >= 1000:
        return f"{v / 1000:.1f}s"
    return f"{v:.0f}ms"


def fmt_s(v: Any) -> str:
    if v is None:
        return "—"
    return f"{v / 1000:.1f}s"


def fmt_tps(v: Any) -> str:
    if v is None:
        return "—"
    return f"{v:.1f} tok/s"


def build_html(document: dict[str, Any]) -> str:
    cluster = document.get("cluster", {})
    mem = cluster.get("memory", {})
    scenarios = [s for s in document.get("scenarios", []) if not s.get("skipped")]
    summaries = [summarize_scenario(s) for s in scenarios]

    def chart_row(label: str, values: list[float | None], unit: str = "ms") -> str:
        nums = [v for v in values if isinstance(v, (int, float))]
        mx = max(nums) if nums else 1
        rows = []
        for sc, val in zip(scenarios, values):
            if not isinstance(val, (int, float)):
                continue
            bar = _bar(val, mx, 24)
            display = fmt_s(val) if unit == "s" else (fmt_tps(val) if unit == "tps" else fmt_ms(val))
            rows.append(
                f"<tr><td>{html.escape(sc.get('model_key', ''))}</td>"
                f"<td><code>{bar}</code></td><td>{display}</td></tr>"
            )
        return f"<h3>{html.escape(label)}</h3><table><tbody>{''.join(rows)}</tbody></table>"

    planner_vals = [s["planner_ms"] for s in summaries]
    install_vals = [s["install_ms"] for s in summaries]
    tps_vals = [s["generate_tps"] for s in summaries]

    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Cluster Benchmark {html.escape(document.get('run_id', ''))}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; background: #0f1115; color: #e8eaed; }}
    h1, h2, h3 {{ color: #8ab4f8; }}
    table {{ border-collapse: collapse; margin: 1rem 0; }}
    td, th {{ padding: 0.4rem 0.8rem; border-bottom: 1px solid #333; }}
    code {{ color: #81c995; }}
    .meta {{ color: #9aa0a6; }}
  </style>
</head>
<body>
  <h1>Cluster Benchmark</h1>
  <p class="meta">Run <code>{html.escape(document.get('run_id', ''))}</code> |
     profile <code>{html.escape(document.get('profile', ''))}</code> |
     {cluster.get('node_count', 0)} nodes |
     {mem.get('free_total_gb', '?')} GB free</p>
  {chart_row('Planner time', planner_vals)}
  {chart_row('Install', install_vals, unit='s')}
  {chart_row('Generate TPS', tps_vals, unit='tps')}
</body>
</html>
"""
    return body


def write_reports(out_dir: Path, document: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.md").write_text(build_markdown(document), encoding="utf-8")
    (out_dir / "report.html").write_text(build_html(document), encoding="utf-8")


def load_results(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
