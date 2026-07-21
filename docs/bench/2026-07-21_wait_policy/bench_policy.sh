#!/usr/bin/env bash
# Runs the 10-prompt bench against a fresh speculative session for whatever
# SPEC_WAIT_POLICY the entry node (node-b) is CURRENTLY running with -- the
# policy is set by restarting node-b's node_agent, not by this script.
#
# Usage: bench_policy.sh <policy_label> <results_csv>
set -euo pipefail

POLICY="${1:?policy label, e.g. fixed:8, p80, p95}"
CSV="${2:?path to results csv}"

ORCH="http://192.168.50.154:9000"
ENTRY_HOST="192.168.50.254"
ENTRY_DEBUG="http://${ENTRY_HOST}:9002/debug/log?worker=entry&lines=8000"
ENTRY_NETSTATS="http://${ENTRY_HOST}:9002/network/stats"
PROMPTS_FILE="$(dirname "$0")/bench_prompts.txt"
SEEN_FILE="$(dirname "$0")/seen_wait_lines.txt"
touch "$SEEN_FILE"

echo "== policy=$POLICY: creating session =="
SESS=$(curl -s --max-time 60 -X POST "$ORCH/session/create" -H "Content-Type: application/json" -d '{
  "model": "llama-3.2-3b",
  "speculative_draft_model_url": "https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_K_M.gguf",
  "speculative_draft_k": 4
}' | python3 -c "import json,sys; print(json.load(sys.stdin).get('session_id',''))")

if [ -z "$SESS" ]; then
  echo "session create failed"
  exit 1
fi
echo "session=$SESS"

TOKPS_LIST=()
i=0
while IFS= read -r PROMPT; do
  i=$((i+1))
  BODY=$(python3 -c "import json,sys; print(json.dumps({'session_id': sys.argv[1], 'prompt': sys.argv[2], 'max_tokens': 64}))" "$SESS" "$PROMPT")
  RESP=$(curl -s --max-time 90 -X POST "$ORCH/session/generate" -H "Content-Type: application/json" -d "$BODY")
  TOKPS=$(echo "$RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('timing',{}).get('decode_tokens_per_sec',0))")
  echo "  [$i] tok/s=$TOKPS"
  TOKPS_LIST+=("$TOKPS")
done < "$PROMPTS_FILE"

echo "== destroying session =="
curl -s --max-time 30 -X POST "$ORCH/session/destroy" -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SESS\"}" >/dev/null || true

echo "== node-b's view of the link to node-c (rolling ~128s window) =="
NETSTATS=$(curl -s --max-time 5 "$ENTRY_NETSTATS" || echo '{}')
echo "$NETSTATS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
nc = d.get('peers', {}).get('node-c', {})
print('  node-c: p50=%sms p95=%sms jitter=%sms loss=%s%% (n=%s)' % (
    nc.get('rtt_p50_ms'), nc.get('rtt_p95_ms'), nc.get('jitter_ms'),
    nc.get('loss_pct'), nc.get('samples')))
" 2>/dev/null || echo "  (network/stats not available -- node-b needs the observability rebuild)"

echo "== pulling entry log stats for this run (dedup against everything seen in prior runs) =="
# The /debug/log?worker=entry endpoint's before/after snapshots turned out
# not to be reliably synced with real time (stale reads leaking prior
# runs' tail into what should be a fresh window), so instead of diffing
# two point-in-time snapshots, keep a running set of every wait_window
# line ever consumed by this bench session and only treat genuinely
# unseen lines as belonging to the current run. Exact-line dedup is safe
# here because these lines are deterministic floats formatted to 1
# decimal -- a real coincidental duplicate would need identical p50/p95/
# hit_rate down to 0.1ms/1%, which doesn't happen in practice.
ALL_LOG=$(curl -s --max-time 10 "$ENTRY_DEBUG" | grep "SPEC_DEBUG entry: wait_window" || true)
STATS=$(comm -13 <(sort -u "$SEEN_FILE") <(printf '%s\n' "$ALL_LOG" | sort -u) || true)
# Restore original log order (comm sorts) so window progression reads
# naturally in the printed output.
STATS=$(printf '%s\n' "$ALL_LOG" | grep -F -f <(printf '%s\n' "$STATS") || true)
printf '%s\n' "$ALL_LOG" >> "$SEEN_FILE"

# Extra safety net for fixed policies: every genuine line from this run
# must show wait_window=X.0ms exactly.
case "$POLICY" in
  fixed:*)
    WANT_MS="${POLICY#fixed:}.0ms"
    STATS=$(printf '%s\n' "$STATS" | grep "wait_window=${WANT_MS} " || true)
    ;;
esac
echo "$STATS"
if [ -z "$STATS" ]; then
  echo "WARNING: no new wait_window recompute lines this run (fewer than 32 verify samples, or the entry node is still on a stale binary)"
fi

PYSCRIPT="$(dirname "$0")/bench_aggregate.py"
cat > "$PYSCRIPT" <<'PYEOF'
import sys, csv, os, statistics, json as _json

policy = sys.argv[1]
csv_path = sys.argv[2]
net_json = sys.argv[3]
tokps = [float(x) for x in sys.argv[4:]]

try:
    _nc = _json.loads(net_json).get('peers', {}).get('node-c', {})
except Exception:
    _nc = {}

avg = statistics.mean(tokps) if tokps else 0.0
mn = min(tokps) if tokps else 0.0
mx = max(tokps) if tokps else 0.0

stats_text = sys.stdin.read()
p50s, p95s, hits = [], [], []
for line in stats_text.splitlines():
    # SPEC_DEBUG entry: wait_window=10.0ms arrival_p50=0.0ms arrival_p95=8.0ms hit_rate=75%
    parts = {}
    for tok in line.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            parts[k] = v.rstrip("ms%")
    if "arrival_p50" in parts:
        p50s.append(float(parts["arrival_p50"]))
        p95s.append(float(parts["arrival_p95"]))
        hits.append(float(parts["hit_rate"]))

row = {
    "policy": policy,
    "avg_tok_s": round(avg, 2),
    "min_tok_s": round(mn, 2),
    "max_tok_s": round(mx, 2),
    "avg_arrival_p50_ms": round(statistics.mean(p50s), 2) if p50s else "",
    "avg_arrival_p95_ms": round(statistics.mean(p95s), 2) if p95s else "",
    "avg_hit_rate_pct": round(statistics.mean(hits), 1) if hits else "",
    "n_recomputes": len(p50s),
    "net_rtt_p50_ms": _nc.get("rtt_p50_ms", ""),
    "net_rtt_p95_ms": _nc.get("rtt_p95_ms", ""),
    "net_jitter_ms": _nc.get("jitter_ms", ""),
    "net_loss_pct": _nc.get("loss_pct", ""),
}

fieldnames = list(row.keys())
existing_rows = []
if os.path.exists(csv_path):
    with open(csv_path, newline="") as f:
        existing_rows = list(csv.DictReader(f))
existing_rows.append(row)
with open(csv_path, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    for r in existing_rows:
        w.writerow({k: r.get(k, "") for k in fieldnames})

print("== result row ==")
for k, v in row.items():
    print(f"  {k}={v}")
PYEOF

echo "$STATS" | python3 "$PYSCRIPT" "$POLICY" "$CSV" "$NETSTATS" "${TOKPS_LIST[@]}"
