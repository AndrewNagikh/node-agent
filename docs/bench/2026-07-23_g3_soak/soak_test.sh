#!/usr/bin/env bash
# G3 soak protocol: >=20 sequential create/generate/destroy cycles across
# >=3 models (incl. one 32B+ rung), driven purely through the orchestrator
# HTTP API -- no manual node/process intervention of any kind.
set -uo pipefail

ORCH="http://192.168.50.154:9000"
OUT_JSONL="/private/tmp/claude-502/-Users-user-Documents-node-agent/c352eef4-56ac-4918-a4a6-2239eeeba155/scratchpad/soak/results.jsonl"
LOG="/private/tmp/claude-502/-Users-user-Documents-node-agent/c352eef4-56ac-4918-a4a6-2239eeeba155/scratchpad/soak/soak.log"

: > "$OUT_JSONL"
: > "$LOG"

MODELS=(llama-3.2-3b qwen3-14b qwen2.5-32b qwen3-30b)
PROMPT="Explain in one sentence why the sky is blue."
MAX_TOKENS=24

DURATION_SEC=$((30 * 60))
MIN_CYCLES=20

START_TS=$(date +%s)
cycle=0

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

log "soak start: duration budget=${DURATION_SEC}s min_cycles=${MIN_CYCLES} models=${MODELS[*]}"

while true; do
  now=$(date +%s)
  elapsed=$((now - START_TS))
  if [ "$elapsed" -ge "$DURATION_SEC" ] && [ "$cycle" -ge "$MIN_CYCLES" ]; then
    log "time budget exhausted and min cycles reached -- stopping"
    break
  fi
  if [ "$elapsed" -ge $((DURATION_SEC + 900)) ]; then
    log "hard safety cutoff (budget + 15min) hit -- stopping regardless of cycle count"
    break
  fi

  model="${MODELS[$((cycle % ${#MODELS[@]}))]}"
  cycle=$((cycle + 1))
  cyc_start=$(date +%s.%N)
  cyc_start_iso=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  log "cycle $cycle model=$model : create..."
  create_resp=$(curl -s -m 120 -X POST "$ORCH/session/create" -H "Content-Type: application/json" -d "{\"model\":\"$model\"}")
  session_id=$(echo "$create_resp" | python3 -c "import json,sys
try:
    d=json.load(sys.stdin)
    print(d.get('session_id',''))
except Exception:
    print('')" 2>/dev/null)

  if [ -z "$session_id" ]; then
    err=$(echo "$create_resp" | python3 -c "import json,sys
try:
    d=json.load(sys.stdin)
    print(d.get('error','unknown'))
except Exception:
    print('unparseable response')" 2>/dev/null)
    cyc_end=$(date +%s.%N)
    dur=$(echo "$cyc_end - $cyc_start" | bc)
    log "cycle $cycle model=$model : CREATE FAILED err=$err dur=${dur}s"
    python3 -c "import json,time
print(json.dumps({
  'cycle': $cycle, 'model': '$model', 'phase': 'create',
  'ok': False, 'error': '''$err'''.strip(), 'start_iso': '$cyc_start_iso',
  'duration_s': $dur
}))" >> "$OUT_JSONL"
    continue
  fi

  log "cycle $cycle model=$model : session=$session_id created, generating..."
  gen_start=$(date +%s.%N)
  gen_resp=$(curl -s -m 180 -X POST "$ORCH/session/generate" -H "Content-Type: application/json" \
    -d "{\"session_id\":\"$session_id\",\"prompt\":\"$PROMPT\",\"max_tokens\":$MAX_TOKENS}")
  gen_end=$(date +%s.%N)
  gen_dur=$(echo "$gen_end - $gen_start" | bc)

  gen_ok=$(echo "$gen_resp" | python3 -c "import json,sys
try:
    d=json.load(sys.stdin)
    print('1' if d.get('count',0) and d.get('count',0)>0 else '0')
except Exception:
    print('0')" 2>/dev/null)

  text=$(echo "$gen_resp" | python3 -c "import json,sys
try:
    d=json.load(sys.stdin)
    print(d.get('text','').replace(chr(10),' ')[:120])
except Exception:
    print('')" 2>/dev/null)
  tok_s=$(echo "$gen_resp" | python3 -c "import json,sys
try:
    d=json.load(sys.stdin)
    print(d.get('timing',{}).get('decode_tokens_per_sec',0))
except Exception:
    print(0)" 2>/dev/null)
  ttft=$(echo "$gen_resp" | python3 -c "import json,sys
try:
    d=json.load(sys.stdin)
    print(d.get('timing',{}).get('ttft_ms',0))
except Exception:
    print(0)" 2>/dev/null)

  if [ "$gen_ok" = "1" ]; then
    log "cycle $cycle model=$model : GENERATE OK tok/s=$tok_s ttft_ms=$ttft dur=${gen_dur}s text=\"$text\""
  else
    log "cycle $cycle model=$model : GENERATE FAILED resp=$gen_resp"
  fi

  destroy_start=$(date +%s.%N)
  destroy_resp=$(curl -s -m 60 -X POST "$ORCH/session/destroy" -H "Content-Type: application/json" -d "{\"session_id\":\"$session_id\"}")
  destroy_end=$(date +%s.%N)
  destroy_dur=$(echo "$destroy_end - $destroy_start" | bc)
  destroy_ok=$(echo "$destroy_resp" | python3 -c "import json,sys
try:
    d=json.load(sys.stdin)
    print('1' if d.get('destroyed') else '0')
except Exception:
    print('0')" 2>/dev/null)
  log "cycle $cycle model=$model : destroy ok=$destroy_ok dur=${destroy_dur}s"

  cyc_end=$(date +%s.%N)
  cyc_dur=$(echo "$cyc_end - $cyc_start" | bc)

  python3 -c "import json
print(json.dumps({
  'cycle': $cycle, 'model': '$model', 'session_id': '$session_id',
  'ok': $([ "$gen_ok" = "1" ] && echo True || echo False),
  'start_iso': '$cyc_start_iso',
  'create_to_generate_s': None,
  'generate_s': $gen_dur,
  'destroy_s': $destroy_dur,
  'destroy_ok': $([ "$destroy_ok" = "1" ] && echo True || echo False),
  'cycle_total_s': $cyc_dur,
  'decode_tok_s': $tok_s,
  'ttft_ms': $ttft,
  'text_sample': '''$text'''
}))" >> "$OUT_JSONL"

  log "cycle $cycle model=$model : DONE total=${cyc_dur}s (elapsed=${elapsed}s of ${DURATION_SEC}s budget)"
done

end_ts=$(date +%s)
total_elapsed=$((end_ts - START_TS))
log "soak complete: cycles=$cycle total_elapsed=${total_elapsed}s"
