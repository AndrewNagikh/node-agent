#!/usr/bin/env python3
"""Task 12.10 — HTML timeline (TTFT + decode + GPU overlay)."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

COLORS = {
    "entry": "#4c8bf5",
    "middle": "#34a853",
    "final": "#fbbc04",
    "network": "#ff6d01",
    "serialize": "#9aa0a6",
    "sampling": "#a142f4",
    "wait": "#5f6368",
    "idle": "#3c4043",
    "orchestrator": "#8ab4f8",
    "gpu": "#e8710a",
}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _f(val: Any, default: float = 0.0) -> float:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def ttft_stage_bars(ttft_doc: dict[str, Any]) -> list[dict[str, Any]]:
    summary = ttft_doc.get("summary") if isinstance(ttft_doc.get("summary"), dict) else {}
    stage_us = summary.get("stage_us") if isinstance(summary.get("stage_us"), dict) else {}
    order = ("orchestrator", "entry", "middle", "final")
    bars: list[dict[str, Any]] = []
    for stage in order:
        us = stage_us.get(stage)
        if isinstance(us, (int, float)) and us > 0:
            bars.append({
                "stage": stage,
                "ms": round(float(us) / 1000.0, 2),
                "color": COLORS.get(stage, "#8ab4f8"),
            })
    client_ttft = summary.get("client_ttft_ms")
    return bars


def decode_token_bars(tokens: list[dict[str, str]], *, limit: int = 48) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in tokens[:limit]:
        tok = int(_f(row.get("token"), -1))
        if tok < 0:
            continue
        segments = [
            ("entry", _f(row.get("entry_compute_ms"))),
            ("middle", _f(row.get("middle_compute_ms"))),
            ("final", _f(row.get("final_compute_ms"))),
            ("network", _f(row.get("network_ms"))),
            ("serialize", _f(row.get("serialize_ms"))),
            ("sampling", _f(row.get("sampling_ms"))),
            ("wait", _f(row.get("entry_wait_ms")) + _f(row.get("middle_wait_ms")) + _f(row.get("final_wait_ms"))),
        ]
        total = _f(row.get("total_ms")) or sum(v for _, v in segments) or 1.0
        accounted = sum(v for _, v in segments)
        idle = max(0.0, total - accounted)
        if idle > 0.01:
            segments.append(("idle", idle))
        rows.append({
            "token": tok,
            "trace_id": row.get("trace_id", ""),
            "total_ms": round(total, 2),
            "segments": [
                {"name": name, "ms": round(ms, 2), "color": COLORS.get(name, "#666")}
                for name, ms in segments
                if ms > 0.001
            ],
        })
    return rows


def downsample_gpu(
        samples: list[dict[str, str]],
        *,
        phases: tuple[str, ...] = ("decode", "ttft"),
        max_points: int = 180,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in samples:
        phase = str(row.get("phase", ""))
        if phase not in phases:
            continue
        ts = _f(row.get("ts_us"))
        util = _f(row.get("gpu_util_pct"))
        filtered.append({
            "ts_us": ts,
            "util_pct": util,
            "node_id": row.get("node_id", ""),
            "phase": phase,
        })
    if not filtered:
        return []
    filtered.sort(key=lambda x: x["ts_us"])
    t0 = filtered[0]["ts_us"]
    for item in filtered:
        item["t_ms"] = round((item["ts_us"] - t0) / 1000.0, 1)

    if len(filtered) <= max_points:
        return filtered

    step = max(1, len(filtered) // max_points)
    out: list[dict[str, Any]] = []
    bucket: list[dict[str, Any]] = []
    for i, item in enumerate(filtered):
        bucket.append(item)
        if len(bucket) >= step:
            avg_util = sum(b["util_pct"] for b in bucket) / len(bucket)
            out.append({
                "t_ms": bucket[-1]["t_ms"],
                "util_pct": round(avg_util, 2),
                "node_id": "cluster",
                "phase": bucket[-1]["phase"],
            })
            bucket = []
    return out


def _bar_row(label: str, segments: list[dict[str, Any]], total: float, *, width_px: int = 480) -> str:
    if total <= 0:
        total = sum(s["ms"] for s in segments) or 1.0
    parts: list[str] = []
    for seg in segments:
        w = max(1, int(width_px * seg["ms"] / total))
        parts.append(
            f'<span class="seg" style="width:{w}px;background:{seg["color"]}" '
            f'title="{seg["name"]}: {seg["ms"]}ms"></span>'
        )
    bar = "".join(parts)
    return (
        f'<div class="row"><span class="label">{label}</span>'
        f'<div class="bar">{bar}</div>'
        f'<span class="val">{total:.1f}ms</span></div>'
    )


def _gpu_svg(points: list[dict[str, Any]], width: int = 720, height: int = 140) -> str:
    if not points:
        return '<p class="meta">No GPU samples for decode/ttft phases.</p>'
    max_t = max(p["t_ms"] for p in points) or 1.0
    max_u = max(max(p["util_pct"] for p in points), 1.0)
    coords = []
    for p in points:
        x = 40 + (width - 60) * (p["t_ms"] / max_t)
        y = height - 20 - (height - 40) * (p["util_pct"] / max_u)
        coords.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(coords)
    return f'''
    <svg width="{width}" height="{height}" class="gpu-chart" viewBox="0 0 {width} {height}">
      <polyline fill="none" stroke="{COLORS["gpu"]}" stroke-width="2" points="{poly}" />
      <text x="40" y="16" class="svg-label">GPU/CPU util %</text>
      <text x="{width - 80}" y="{height - 4}" class="svg-label">{max_t:.0f} ms</text>
    </svg>'''


def render_html(document: dict[str, Any]) -> str:
    meta = document.get("meta", {})
    budget = document.get("budget", [])
    ttft_bars = document.get("ttft_bars", [])
    decode_rows = document.get("decode_rows", [])
    gpu_points = document.get("gpu_points", [])
    buckets = document.get("buckets_pct", {})

    budget_rows = ""
    for row in budget:
        if row.get("status") == "SKIP":
            continue
        val = row.get("value")
        tgt = row.get("target")
        val_s = "—" if val is None else f"{val}{row.get('unit', '')}"
        tgt_s = "—" if tgt is None else f"{tgt}{row.get('unit', '')}"
        budget_rows += (
            f"<tr><td>{row.get('label', '')}</td><td>{val_s}</td>"
            f"<td>{tgt_s}</td><td class='st-{row.get('status', 'SKIP')}'>{row.get('status', '')}</td></tr>"
        )

    ttft_html = ""
    if ttft_bars:
        total = sum(b["ms"] for b in ttft_bars)
        segs = [{"name": b["stage"], "ms": b["ms"], "color": b["color"]} for b in ttft_bars]
        ttft_html = _bar_row("TTFT stages", segs, total)
        client = meta.get("client_ttft_ms")
        if client is not None:
            ttft_html += f'<p class="meta">CLIENT_TTFT: <strong>{client} ms</strong></p>'

    decode_html = ""
    for row in decode_rows:
        decode_html += _bar_row(f"token {row['token']}", row["segments"], row["total_ms"])

    bucket_html = ""
    if buckets:
        bucket_html = "<ul class='buckets'>"
        for name, pct in buckets.items():
            bucket_html += f"<li><span style='color:{COLORS.get(name, '#ccc')}'>{name}</span>: {pct}%</li>"
        bucket_html += "</ul>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Task 12 Perf Timeline</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; background: #0f1115; color: #e8eaed; }}
    h1, h2, h3 {{ color: #8ab4f8; }}
    .meta {{ color: #9aa0a6; margin: 0.5rem 0 1rem; }}
    table {{ border-collapse: collapse; margin: 1rem 0; width: 100%; max-width: 900px; }}
    td, th {{ padding: 0.45rem 0.7rem; border-bottom: 1px solid #333; text-align: left; }}
    .st-PASS {{ color: #81c995; }}
    .st-WARN {{ color: #fdd663; }}
    .st-FAIL {{ color: #f28b82; }}
    .st-SKIP {{ color: #9aa0a6; }}
    .row {{ display: flex; align-items: center; gap: 0.6rem; margin: 0.35rem 0; }}
    .label {{ width: 6.5rem; font-size: 0.85rem; color: #9aa0a6; }}
    .bar {{ display: flex; height: 16px; background: #202124; border-radius: 3px; overflow: hidden; }}
    .seg {{ display: inline-block; height: 100%; }}
    .val {{ width: 5rem; font-size: 0.8rem; color: #bdc1c6; text-align: right; }}
    .legend span {{ display: inline-block; margin-right: 1rem; font-size: 0.8rem; }}
    .legend i {{ display: inline-block; width: 10px; height: 10px; margin-right: 0.25rem; }}
    .buckets {{ list-style: none; padding: 0; }}
    .buckets li {{ margin: 0.25rem 0; }}
    .svg-label {{ fill: #9aa0a6; font-size: 11px; }}
  </style>
</head>
<body>
  <h1>Task 12 Performance Timeline</h1>
  <p class="meta">Analysis: <code>{meta.get('analysis_dir', '')}</code></p>

  <h2>Summary / Budget</h2>
  <table>
    <thead><tr><th>Metric</th><th>Value</th><th>Target</th><th>Status</th></tr></thead>
    <tbody>{budget_rows}</tbody>
  </table>
  {bucket_html}

  <h2>TTFT Timeline</h2>
  {ttft_html or '<p class="meta">No TTFT data.</p>'}

  <h2>Decode Timeline (per token)</h2>
  <div class="legend">
    <span><i style="background:{COLORS['entry']}"></i>entry</span>
    <span><i style="background:{COLORS['middle']}"></i>middle</span>
    <span><i style="background:{COLORS['final']}"></i>final</span>
    <span><i style="background:{COLORS['network']}"></i>network</span>
    <span><i style="background:{COLORS['wait']}"></i>wait/idle</span>
  </div>
  {decode_html or '<p class="meta">No decode token rows.</p>'}

  <h2>GPU / CPU Utilization</h2>
  {_gpu_svg(gpu_points)}

</body>
</html>"""


def build_timeline_document(
        analysis_dir: Path,
        *,
        ttft_dir: Path | None = None,
        token_limit: int = 48,
) -> dict[str, Any]:
    budget_doc = _read_json(analysis_dir / "budget.json")
    ttft_doc = _read_json((ttft_dir or analysis_dir) / "ttft.json")
    if not ttft_doc and ttft_dir:
        ttft_doc = _read_json(ttft_dir / "ttft.json")

    tokens = _read_csv(analysis_dir / "tokens.csv")
    gpu_samples = _read_csv(analysis_dir / "gpu.csv")

    ttft_bars = ttft_stage_bars(ttft_doc)
    decode_rows = decode_token_bars(tokens, limit=token_limit)
    gpu_points = downsample_gpu(gpu_samples)

    metrics = budget_doc.get("metrics") if isinstance(budget_doc.get("metrics"), dict) else {}
    rollup = metrics.get("rollup") if isinstance(metrics.get("rollup"), dict) else {}
    buckets = rollup.get("buckets_pct") if isinstance(rollup.get("buckets_pct"), dict) else {}

    ttft_summary = ttft_doc.get("summary") if isinstance(ttft_doc.get("summary"), dict) else {}

    return {
        "meta": {
            "analysis_dir": str(analysis_dir),
            "client_ttft_ms": ttft_summary.get("client_ttft_ms"),
            "decode_ms_per_token": metrics.get("decode_ms_per_token"),
            "token_count": len(decode_rows),
        },
        "budget": budget_doc.get("budget", []),
        "buckets_pct": buckets,
        "ttft_bars": ttft_bars,
        "decode_rows": decode_rows,
        "gpu_points": gpu_points,
    }


def write_timeline(
        analysis_dir: Path,
        out_path: Path | None = None,
        *,
        ttft_dir: Path | None = None,
) -> dict[str, Any]:
    document = build_timeline_document(analysis_dir, ttft_dir=ttft_dir)
    html = render_html(document)
    path = out_path or (analysis_dir / "timeline.html")
    path.write_text(html, encoding="utf-8")
    document["timeline_html"] = str(path)
    (analysis_dir / "timeline.json").write_text(json.dumps({
        "meta": document["meta"],
        "ttft_bars": document["ttft_bars"],
        "decode_token_count": len(document["decode_rows"]),
        "gpu_point_count": len(document["gpu_points"]),
        "timeline_html": str(path),
    }, indent=2), encoding="utf-8")
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Task 12 HTML perf timeline")
    parser.add_argument("analysis_dir", type=Path)
    parser.add_argument("-o", "--output", type=Path, help="timeline.html path")
    parser.add_argument("--ttft", type=Path, help="TTFT analysis directory")
    args = parser.parse_args()
    doc = write_timeline(args.analysis_dir, args.output, ttft_dir=args.ttft)
    print(json.dumps({
        "timeline_html": doc.get("timeline_html"),
        "tokens": doc["meta"].get("token_count"),
        "gpu_points": len(doc.get("gpu_points", [])),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
