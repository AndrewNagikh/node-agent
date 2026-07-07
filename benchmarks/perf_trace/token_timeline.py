#!/usr/bin/env python3
"""Task 12.2 — reconstruct full lifecycle of one decode token (read-only)."""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STAGE_NODE = {"entry": "node-a", "middle": "node-b", "final": "node-c"}


@dataclass
class Step:
    step_id: str
    ts_us: int
    dur_us: int
    actor: str
    action: str
    initiated_by: str
    waited_on: str
    notes: str = ""

    @property
    def dur_ms(self) -> float:
        return self.dur_us / 1000.0

    @property
    def end_us(self) -> int:
        return self.ts_us + self.dur_us


@dataclass
class TokenTimeline:
    trace_id: str
    token_idx: int
    ordinal: int
    session_t0_us: int
    entry_recv_17_us: int
    entry_recv_next_us: int
    steps: list[Step] = field(default_factory=list)

    @property
    def period_ms(self) -> float:
        return (self.entry_recv_next_us - self.entry_recv_17_us) / 1000.0


def load_events(trace_dir: Path, trace_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in sorted(trace_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("trace_id") == trace_id and ev.get("phase") == "decode":
                events.append(ev)
    return events


def dedupe(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for ev in events:
        key = (ev.get("node_id"), ev.get("event"), ev.get("token_idx"), ev.get("ts_us"), ev.get("kind"))
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    return sorted(out, key=lambda e: e["ts_us"])


def entry_receive_clusters(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    recvs = [
        e for e in events
        if e.get("node_id") == STAGE_NODE["entry"] and e.get("event") == "ENTRY_RECEIVE"
    ]
    recvs = dedupe(recvs)
    if not recvs:
        return []
    clusters: list[list[dict[str, Any]]] = [[recvs[0]]]
    for ev in recvs[1:]:
        if (ev["ts_us"] - clusters[-1][-1]["ts_us"]) / 1000.0 > 2000:
            clusters.append([ev])
        else:
            clusters[-1].append(ev)
    return clusters


def pick_session_cluster(events: list[dict[str, Any]], token_idx: int) -> list[dict[str, Any]]:
    clusters = entry_receive_clusters(events)
    candidates = [c for c in clusters if any(e.get("token_idx") == token_idx for e in c)]
    if not candidates:
        return []
    return max(candidates, key=len)


def first_in_window(
        events: list[dict[str, Any]],
        *,
        node_id: str,
        names: set[str],
        lo: int,
        hi: int,
        after: int | None = None,
        token_idx: int | None = None,
) -> dict[str, Any] | None:
    cands: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("node_id") != node_id:
            continue
        if ev.get("event") not in names:
            continue
        ts = ev["ts_us"]
        if not (lo <= ts <= hi):
            continue
        if after is not None and ts < after:
            continue
        if token_idx is not None and ev.get("token_idx") != token_idx:
            continue
        cands.append(ev)
    if not cands:
        return None
    return min(cands, key=lambda e: e["ts_us"])


def span_bounds(ev: dict[str, Any]) -> tuple[int, int]:
    end_us = int(ev["ts_us"])
    dur = int(ev.get("dur_us") or 0)
    return end_us - dur, end_us


def recv_ordinal(
        events: list[dict[str, Any]],
        stage: str,
        ordinal: int,
        *,
        lo: int,
        hi: int,
) -> dict[str, Any] | None:
    event = {"entry": "ENTRY_RECEIVE", "middle": "MIDDLE_RECEIVE", "final": "FINAL_RECEIVE"}[stage]
    node = STAGE_NODE[stage]
    recvs = dedupe([
        e for e in events
        if e.get("node_id") == node and e.get("event") == event and lo <= e["ts_us"] <= hi
    ])
    if ordinal >= len(recvs):
        return None
    return recvs[ordinal]


def reconstruct_token_timeline(
        trace_dir: Path,
        trace_id: str,
        token_idx: int,
) -> TokenTimeline | None:
    events = dedupe(load_events(trace_dir, trace_id))
    session = pick_session_cluster(events, token_idx)
    if not session:
        return None

    ordinal = next(i for i, e in enumerate(session) if e.get("token_idx") == token_idx)
    entry_recv_ev = session[ordinal]
    entry_recv_us = int(entry_recv_ev["ts_us"])
    entry_next_us = int(session[ordinal + 1]["ts_us"]) if ordinal + 1 < len(session) else entry_recv_us

    prev_recv_us = int(session[ordinal - 1]["ts_us"]) if ordinal > 0 else entry_recv_us - int(entry_next_us - entry_recv_us)

    lo = prev_recv_us
    hi = entry_next_us
    session_lo = int(session[0]["ts_us"]) - 1000
    session_hi = int(session[-1]["ts_us"]) + 1000
    steps: list[Step] = []

    # Orchestrator send — inferred immediately after previous token response (≈ previous entry recv period end).
    orch_send_us = entry_recv_us
    steps.append(Step(
        step_id="orchestrator_send",
        ts_us=orch_send_us,
        dur_us=0,
        actor="orchestrator",
        action=f"pipeline_gen3_send_req(DECODE, token {token_idx})",
        initiated_by="orchestrator",
        waited_on=f"token {token_idx - 1} full round-trip",
        notes="No per-token orchestrator span in trace; timestamp = entry ENTRY_RECEIVE (request arrived).",
    ))

    steps.append(Step(
        step_id="entry_recv",
        ts_us=entry_recv_us,
        dur_us=0,
        actor="entry",
        action="ENTRY_RECEIVE on ctrl socket",
        initiated_by="orchestrator",
        waited_on="orchestrator send (blocked in split_gen_recv_req)",
    ))

    comp_begin = first_in_window(
        events, node_id=STAGE_NODE["entry"], names={"ENTRY_COMPUTE_BEGIN"},
        lo=entry_recv_us, hi=entry_next_us, after=entry_recv_us,
    )
    comp_end = first_in_window(
        events, node_id=STAGE_NODE["entry"], names={"ENTRY_COMPUTE_END"},
        lo=entry_recv_us, hi=entry_next_us, after=entry_recv_us, token_idx=token_idx,
    )
    if comp_end is None:
        return None

    begin_us = int(comp_begin["ts_us"]) if comp_begin else span_bounds(comp_end)[0]
    end_us = span_bounds(comp_end)[1]
    steps.append(Step(
        step_id="entry_compute",
        ts_us=begin_us,
        dur_us=end_us - begin_us,
        actor="entry",
        action="ENTRY_COMPUTE_BEGIN → ENTRY_COMPUTE_END (GGML decode)",
        initiated_by="entry recv",
        waited_on="—",
    ))

    # Entry send — causal: after compute. Traced HIDDEN_TRANSFER may appear earlier (pipeline lag / tagging).
    send_ev = first_in_window(
        events, node_id=STAGE_NODE["entry"],
        names={"ENTRY_SEND_END", "HIDDEN_TRANSFER"},
        lo=end_us, hi=entry_next_us, token_idx=token_idx,
    )
    if send_ev is None:
        send_us = end_us
        send_dur = 80  # typical from trace (~0.07ms)
        send_note = "Inferred at COMPUTE_END; no post-compute ENTRY_SEND in trace window."
    else:
        send_us, send_end = span_bounds(send_ev)
        send_dur = send_end - send_us
        send_note = "Measured HIDDEN_TRANSFER / ENTRY_SEND_END after compute."
    steps.append(Step(
        step_id="entry_send",
        ts_us=send_us,
        dur_us=send_dur,
        actor="entry",
        action="Forward hidden state → middle (TCP ab)",
        initiated_by="entry compute complete",
        waited_on="entry compute",
        notes=send_note,
    ))

    # Middle / final — prefer causal events after entry send; fall back to ordinal-aligned receives.
    mid_recv = first_in_window(
        events, node_id=STAGE_NODE["middle"], names={"MIDDLE_RECEIVE"},
        lo=int(send_us), hi=entry_next_us,
    ) or recv_ordinal(events, "middle", ordinal, lo=session_lo, hi=session_hi)
    if mid_recv is None:
        return None

    steps.append(Step(
        step_id="middle_recv",
        ts_us=int(mid_recv["ts_us"]),
        dur_us=0,
        actor="middle",
        action="MIDDLE_RECEIVE hidden tensor",
        initiated_by="entry send",
        waited_on="entry forward (blocked in split_ab_recv_hidden)" if int(mid_recv["ts_us"]) >= send_us else "prior pipeline wave (tagged same token_idx)",
        notes="" if int(mid_recv["ts_us"]) >= send_us else "Receive timestamp precedes entry compute end — pipeline lag / token_idx tags prior wave.",
    ))

    mid_begin = first_in_window(
        events, node_id=STAGE_NODE["middle"], names={"MIDDLE_COMPUTE_BEGIN"},
        lo=int(mid_recv["ts_us"]), hi=entry_next_us,
    )
    mid_end = first_in_window(
        events, node_id=STAGE_NODE["middle"], names={"MIDDLE_COMPUTE_END"},
        lo=int(mid_recv["ts_us"]), hi=entry_next_us, after=int(mid_recv["ts_us"]), token_idx=token_idx,
    )
    if mid_end:
        mb, me = span_bounds(mid_end)
        if mid_begin and int(mid_begin["ts_us"]) >= int(mid_recv["ts_us"]):
            mb = int(mid_begin["ts_us"])
        steps.append(Step(
            step_id="middle_compute",
            ts_us=mb,
            dur_us=me - mb,
            actor="middle",
            action="MIDDLE_COMPUTE_BEGIN → MIDDLE_COMPUTE_END",
            initiated_by="middle recv",
            waited_on="—",
        ))
        mid_send = first_in_window(
            events, node_id=STAGE_NODE["middle"], names={"MIDDLE_SEND_END", "HIDDEN_TRANSFER"},
            lo=me, hi=entry_next_us, token_idx=token_idx,
        )
        if mid_send is None:
            ms_us, ms_dur = me, 80
            ms_note = "Inferred at MIDDLE_COMPUTE_END."
        else:
            ms_us, ms_end = span_bounds(mid_send)
            ms_dur = ms_end - ms_us
            ms_note = ""
        steps.append(Step(
            step_id="middle_send",
            ts_us=ms_us,
            dur_us=ms_dur,
            actor="middle",
            action="Forward hidden → final (TCP bc)",
            initiated_by="middle compute complete",
            waited_on="middle compute",
            notes=ms_note,
        ))
        mid_done = ms_us + ms_dur
    else:
        mid_done = int(mid_recv["ts_us"])

    fin_recv = first_in_window(
        events, node_id=STAGE_NODE["final"], names={"FINAL_RECEIVE"},
        lo=mid_done, hi=entry_next_us,
    ) or recv_ordinal(events, "final", ordinal, lo=session_lo, hi=session_hi)
    if fin_recv is None:
        return None

    steps.append(Step(
        step_id="final_recv",
        ts_us=int(fin_recv["ts_us"]),
        dur_us=0,
        actor="final",
        action="FINAL_RECEIVE hidden tensor",
        initiated_by="middle send",
        waited_on="middle forward",
    ))

    fin_begin = first_in_window(
        events, node_id=STAGE_NODE["final"], names={"FINAL_COMPUTE_BEGIN"},
        lo=int(fin_recv["ts_us"]), hi=entry_next_us,
    )
    fin_end = first_in_window(
        events, node_id=STAGE_NODE["final"], names={"FINAL_COMPUTE_END"},
        lo=int(fin_recv["ts_us"]), hi=entry_next_us, after=int(fin_recv["ts_us"]), token_idx=token_idx,
    )
    sampler = first_in_window(
        events, node_id=STAGE_NODE["final"], names={"SAMPLER_END"},
        lo=int(fin_recv["ts_us"]), hi=entry_next_us, token_idx=token_idx,
    )
    if fin_end:
        fb, fe = span_bounds(fin_end)
        if fin_begin and int(fin_begin["ts_us"]) >= int(fin_recv["ts_us"]):
            fb = int(fin_begin["ts_us"])
        steps.append(Step(
            step_id="final_compute",
            ts_us=fb,
            dur_us=fe - fb,
            actor="final",
            action="FINAL_COMPUTE_BEGIN → FINAL_COMPUTE_END",
            initiated_by="final recv",
            waited_on="—",
        ))
        if sampler:
            ss, se = span_bounds(sampler)
            steps.append(Step(
                step_id="final_sample",
                ts_us=ss,
                dur_us=se - ss,
                actor="final",
                action="SAMPLER_END (next token id)",
                initiated_by="final compute",
                waited_on="final compute",
            ))
            pipeline_done = se
        else:
            pipeline_done = fe
    else:
        pipeline_done = int(fin_recv["ts_us"])

    orch_recv_us = entry_next_us
    steps.append(Step(
        step_id="orchestrator_recv",
        ts_us=pipeline_done,
        dur_us=max(0, orch_recv_us - pipeline_done),
        actor="orchestrator",
        action="pipeline_gen3_recv_a_resp returns token to node_agent loop",
        initiated_by="final pipeline completion",
        waited_on="entry worker forwarding response",
        notes="End inferred before next ENTRY_RECEIVE; duration = bubble until token N+1 dispatch.",
    ))

    steps.append(Step(
        step_id="orchestrator_send_next",
        ts_us=entry_next_us,
        dur_us=0,
        actor="orchestrator",
        action=f"pipeline_gen3_send_req(DECODE, token {token_idx + 1})",
        initiated_by="orchestrator",
        waited_on=f"token {token_idx} response",
    ))

    return TokenTimeline(
        trace_id=trace_id,
        token_idx=token_idx,
        ordinal=ordinal,
        session_t0_us=int(session[0]["ts_us"]),
        entry_recv_17_us=entry_recv_us,
        entry_recv_next_us=entry_next_us,
        steps=steps,
    )


def occupancy_bars(timeline: TokenTimeline, width: int = 56) -> dict[str, str]:
    base = timeline.entry_recv_17_us
    period_us = max(1, timeline.entry_recv_next_us - timeline.entry_recv_17_us)
    scale = width / period_us

    def blank() -> list[str]:
        return [" "] * width

    def paint(row: list[str], start_us: int, dur_us: int, ch: str) -> None:
        rel0 = max(0, start_us - base)
        rel1 = max(rel0 + 1, start_us + dur_us - base)
        i0 = min(width - 1, int(rel0 * scale))
        i1 = min(width, max(i0 + 1, int(rel1 * scale)))
        for i in range(i0, i1):
            row[i] = ch

    rows = {k: blank() for k in ("entry", "middle", "final", "orchestrator")}
    mapping = {
        "entry_recv": ("entry", "|"),
        "entry_compute": ("entry", "█"),
        "entry_send": ("entry", "▶"),
        "middle_recv": ("middle", "|"),
        "middle_compute": ("middle", "█"),
        "middle_send": ("middle", "▶"),
        "final_recv": ("final", "|"),
        "final_compute": ("final", "█"),
        "final_sample": ("final", "░"),
        "orchestrator_send": ("orchestrator", "|"),
        "orchestrator_recv": ("orchestrator", "·"),
        "orchestrator_send_next": ("orchestrator", "|"),
    }
    for step in timeline.steps:
        key = mapping.get(step.step_id)
        if not key:
            continue
        row_name, ch = key
        paint(rows[row_name], step.ts_us, max(step.dur_us, 1), ch)

    return {name: "".join(chars) for name, chars in rows.items()}


def transitions(timeline: TokenTimeline) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for prev, nxt in zip(timeline.steps, timeline.steps[1:]):
        gap_us = max(0, nxt.ts_us - prev.end_us)
        out.append({
            "from": prev.step_id,
            "to": nxt.step_id,
            "gap_ms": gap_us / 1000.0,
            "initiated_by": nxt.initiated_by,
            "waited": f"{nxt.actor} waited on {nxt.waited_on}",
        })
    return out


def critical_path_ms(timeline: TokenTimeline) -> float:
    base = timeline.entry_recv_17_us
    ends = [s.end_us for s in timeline.steps if s.step_id in ("final_sample", "final_compute", "orchestrator_recv")]
    completion = min(ends) if ends else timeline.entry_recv_next_us
    return (completion - base) / 1000.0


def build_markdown(timeline: TokenTimeline) -> str:
    base = timeline.entry_recv_17_us
    bars = occupancy_bars(timeline)
    lines = [
        f"# Task 12.2 — Token {timeline.token_idx} Timeline Reconstruction",
        "",
        f"**Trace:** `{timeline.trace_id}`  ",
        f"**Session ordinal:** {timeline.ordinal} (0-based entry receive index)  ",
        f"**Wall period** (entry recv N → entry recv N+1): **{timeline.period_ms:.2f} ms**  ",
        "",
        "## Step-by-step (non-aggregated)",
        "",
        "| Step | t+ms | dur ms | Actor | Action | Initiated by | Waited on |",
        "|------|-----:|-------:|-------|--------|--------------|-----------|",
    ]

    prev_end = base
    for step in timeline.steps:
        rel = (step.ts_us - base) / 1000.0
        gap = (step.ts_us - prev_end) / 1000.0 if prev_end else 0.0
        note = step.notes.replace("|", "/") if step.notes else ""
        if gap > 0.05 and step.step_id not in ("entry_recv", "orchestrator_send"):
            note = (note + f" gap_since_prev={gap:.2f}ms").strip()
        lines.append(
            f"| `{step.step_id}` | {rel:.2f} | {step.dur_ms:.2f} | {step.actor} | {step.action} | "
            f"{step.initiated_by} | {step.waited_on} |"
        )
        if note:
            lines[-1] += f" <!-- {note} -->"
        prev_end = step.end_us

    lines.extend([
        "",
        "## Causal chain (protocol order)",
        "",
        "```",
        "orchestrator send",
        "  ↓",
        "entry recv",
        "  ↓",
        "entry compute",
        "  ↓",
        "entry send",
        "  ↓",
        "middle recv → middle compute → middle send",
        "  ↓",
        "final recv → final compute → final sample",
        "  ↓",
        "orchestrator receives (bubble until next dispatch)",
        "  ↓",
        f"orchestrator send token {timeline.token_idx + 1}",
        "```",
        "",
        "## Occupancy (0 = entry recv token N, width = period until token N+1)",
        "",
        "```",
        f"Period: {timeline.period_ms:.1f}ms" + " " * 40,
        f"Entry        |{bars['entry']}|",
        f"Middle       |{bars['middle']}|",
        f"Final        |{bars['final']}|",
        f"Orchestrator |{bars['orchestrator']}|",
        "             " + "0" + " " * (len(bars['entry']) // 2 - 1) + f"{timeline.period_ms:.0f}ms",
        "```",
    ])

    compute_us = sum(s.dur_us for s in timeline.steps if "compute" in s.step_id or s.step_id == "final_sample")
    orch_bubble = next((s for s in timeline.steps if s.step_id == "orchestrator_recv"), None)
    bubble_us = orch_bubble.dur_us if orch_bubble else 0
    period_us = timeline.entry_recv_next_us - timeline.entry_recv_17_us
    crit = critical_path_ms(timeline)

    lines.extend([
        "",
        "## Transitions (who initiated / who waited)",
        "",
        "| From → To | gap ms | Initiated by | Waited |",
        "|-----------|-------:|--------------|--------|",
    ])
    for tr in transitions(timeline):
        lines.append(
            f"| `{tr['from']}` → `{tr['to']}` | {tr['gap_ms']:.2f} | {tr['initiated_by']} | {tr['waited']} |"
        )

    lines.extend([
        "",
        "## Mermaid (wall-clock, ms from entry recv token 17)",
        "",
        "```mermaid",
        "gantt",
        "    dateFormat X",
        "    axisFormat %L",
        f"    title Token {timeline.token_idx} pipeline ({timeline.trace_id}, Docker)",
        "",
    ])
    for step in timeline.steps:
        if step.dur_us <= 0 and step.step_id.endswith("_recv"):
            continue
        rel_start = (step.ts_us - base) / 1000.0
        rel_end = (step.end_us - base) / 1000.0
        label = step.step_id.replace("_", " ")
        lines.append(f"    section {step.actor}")
        lines.append(f"    {label} : {rel_start:.1f}, {rel_end:.1f}")
    lines.extend([
        "```",
        "",
        "## Bubble accounting",
        "",
        f"- **Critical path** (entry recv → final sample): **{crit:.2f} ms**",
        f"- **Orchestrator+entry bubble** (pipeline done → next dispatch): **{bubble_us/1000:.2f} ms**",
        f"- **Entry period** (recv N → recv N+1): **{period_us/1000:.2f} ms**",
        f"- Bubble share: **{100*bubble_us/period_us:.1f}%** of period",
        f"- Sum of stage compute spans (overlapped, not additive): **{compute_us/1000:.2f} ms**",
        "",
        "## Interpretation",
        "",
        "1. **Who holds the ~42 ms bubble?** Orchestrator thread in `pipeline_gen3_send_recv` after final "
        "response is ready — workers are idle; next token cannot be sent until recv completes.",
        "2. **Can token 18 be dispatched earlier?** **No** under current protocol: one blocking RPC per token "
        "on the orchestrator↔entry ctrl socket (`node_agent.cpp` serial decode loop).",
        "3. **Stages overlap in wall time** — middle recv at +7 ms is a *prior wave* (pipeline lag); "
        "entry compute for token 17 still runs until +24 ms. Align by receive **ordinal**, not `token_idx` alone.",
        "4. **Network is not the stall** — hidden hops are < 0.1 ms; bubble is protocol/orchestrator coupling.",
        "",
        "## Reproduce",
        "",
        "```bash",
        "PYTHONPATH=benchmarks python3 benchmarks/perf_trace/token_timeline.py \\",
        f"  logs/perf_trace/docker_verify_20260707_151625/raw --trace {timeline.trace_id} --token {timeline.token_idx} \\",
        "  -o logs/perf_trace/docker_verify_20260707_151625/analysis/token_17_timeline.json \\",
        "  --md docs/TASK_12_TOKEN_17_TIMELINE.md",
        "```",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconstruct single-token decode timeline")
    parser.add_argument("raw_dir", type=Path)
    parser.add_argument("--trace", default="trace-000004")
    parser.add_argument("--token", type=int, default=17)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--md", type=Path)
    args = parser.parse_args()

    timeline = reconstruct_token_timeline(args.raw_dir, args.trace, args.token)
    if timeline is None:
        print(json.dumps({"error": "timeline not found"}))
        return 1

    doc = {
        "trace_id": timeline.trace_id,
        "token_idx": timeline.token_idx,
        "ordinal": timeline.ordinal,
        "period_ms": timeline.period_ms,
        "steps": [
            {
                "step_id": s.step_id,
                "ts_us": s.ts_us,
                "dur_us": s.dur_us,
                "t_ms": (s.ts_us - timeline.entry_recv_17_us) / 1000.0,
                "actor": s.actor,
                "action": s.action,
                "initiated_by": s.initiated_by,
                "waited_on": s.waited_on,
                "notes": s.notes,
            }
            for s in timeline.steps
        ],
        "occupancy": occupancy_bars(timeline),
    }

    if args.output:
        args.output.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    if args.md:
        args.md.write_text(build_markdown(timeline), encoding="utf-8")
    print(json.dumps(doc, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
