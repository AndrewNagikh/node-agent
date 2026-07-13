#!/usr/bin/env bash
# Shared helpers for run-agent.sh and run-orchestrator.sh

# Load nodes.conf (or nodes.conf.example as a fallback) so launches only
# need NODE_ID. Only known ORCHESTRATOR_*/NODE_*_HOST/NODE_*_PORT keys are
# read from the file; anything already set in the environment wins.
node_agent_load_topology() {
  local root="$1"
  local conf="$root/nodes.conf"
  [[ -f "$conf" ]] || conf="$root/nodes.conf.example"
  [[ -f "$conf" ]] || return 0

  local line key val
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="$(echo "$line" | xargs)"
    [[ -z "$line" ]] && continue
    [[ "$line" != *=* ]] && continue
    key="${line%%=*}"
    val="${line#*=}"
    case "$key" in
      ORCHESTRATOR_HOST|ORCHESTRATOR_PORT|NODE_A_HOST|NODE_A_PORT|NODE_B_HOST|NODE_B_PORT|NODE_C_HOST|NODE_C_PORT)
        [[ -z "${!key:-}" ]] && printf -v "$key" '%s' "$val"
        ;;
    esac
  done < "$conf"
}

node_agent_topology_orchestrator_url() {
  if [[ -n "${ORCHESTRATOR_HOST:-}" ]]; then
    echo "http://${ORCHESTRATOR_HOST}:${ORCHESTRATOR_PORT:-9000}"
  fi
}

node_agent_topology_node_host() {
  case "${1:-}" in
    node-a) echo "${NODE_A_HOST:-}" ;;
    node-b) echo "${NODE_B_HOST:-}" ;;
    node-c) echo "${NODE_C_HOST:-}" ;;
  esac
}

node_agent_topology_node_port() {
  case "${1:-}" in
    node-a) echo "${NODE_A_PORT:-}" ;;
    node-b) echo "${NODE_B_PORT:-}" ;;
    node-c) echo "${NODE_C_PORT:-}" ;;
  esac
}

# Parse `KEY=value` positional args (make-style), e.g. `NODE_ID=node-a`, in
# addition to `--flag value` and pre-set env vars. Only whitelisted keys are
# accepted (space-separated list in $1). Writes matches directly into shell
# vars of the same name; leaves everything else in the global array
# NODE_AGENT_KV_REMAINING for the caller's normal --flag parser to consume.
# (No `local -n` nameref: macOS ships bash 3.2, which predates it.)
node_agent_parse_kv_args() {
  local allowed="$1"
  shift
  NODE_AGENT_KV_REMAINING=()
  local arg key val
  for arg in "$@"; do
    if [[ "$arg" == *=* && "${arg%%=*}" =~ ^[A-Z_][A-Z0-9_]*$ ]]; then
      key="${arg%%=*}"
      val="${arg#*=}"
      if [[ " $allowed " == *" $key "* ]]; then
        printf -v "$key" '%s' "$val"
        continue
      fi
    fi
    NODE_AGENT_KV_REMAINING+=("$arg")
  done
}

node_agent_is_wsl() {
  grep -qi microsoft /proc/version 2>/dev/null
}

node_agent_detect_lan_ip() {
  if [[ -n "${ADVERTISE_HOST:-}" ]]; then
    echo "$ADVERTISE_HOST"
    return
  fi

  local ip=""

  if node_agent_is_wsl; then
    if command -v powershell.exe >/dev/null 2>&1; then
      ip="$(powershell.exe -NoProfile -Command \
        "(Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp -ErrorAction SilentlyContinue | Where-Object { \$_.InterfaceAlias -notmatch 'vEthernet|WSL|Loopback|Virtual' } | Select-Object -First 1).IPAddress" \
        2>/dev/null | tr -d '\r\n' || true)"
    fi
  elif [[ "$(uname -s)" == "Darwin" ]]; then
    for iface in en0 en1 en2; do
      ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
      if [[ -n "$ip" ]]; then
        break
      fi
    done
  else
    if command -v ip >/dev/null 2>&1; then
      ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="src") print $(i+1); exit}')"
    fi
    if [[ -z "$ip" ]] && command -v hostname >/dev/null 2>&1; then
      ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    fi
  fi

  if [[ -z "$ip" ]]; then
    echo "run-agent: could not detect LAN IP; set ADVERTISE_HOST=YOUR_IP" >&2
    return 1
  fi

  echo "$ip"
}

node_agent_default_port() {
  local from_topology
  from_topology="$(node_agent_topology_node_port "${1:-}")"
  if [[ -n "$from_topology" ]]; then
    echo "$from_topology"
    return
  fi
  case "${1:-}" in
    node-a) echo 9001 ;;
    node-b) echo 9002 ;;
    node-c) echo 9003 ;;
    *)      echo 9001 ;;
  esac
}

# Legacy helper: only returns MODEL when explicitly set (no auto-discovery).
node_agent_find_model() {
  if [[ -n "${MODEL:-}" && -f "$MODEL" ]]; then
    echo "$MODEL"
    return
  fi
  echo ""
}

node_agent_ensure_hf_token() {
  if [[ -n "${HF_TOKEN:-}" ]]; then
    return 0
  fi

  if [[ -n "${HUGGINGFACE_HUB_TOKEN:-}" ]]; then
    export HF_TOKEN="$HUGGINGFACE_HUB_TOKEN"
    return 0
  fi

  if [[ -f "$HOME/.cache/huggingface/token" ]]; then
    export HF_TOKEN="$(tr -d '[:space:]' < "$HOME/.cache/huggingface/token")"
    return 0
  fi

  local root="${1:-}"
  if [[ -n "$root" && -f "$root/.env" ]]; then
    # shellcheck disable=SC1090
    set -a
    # shellcheck disable=SC1091
    source "$root/.env"
    set +a
  fi
}

node_agent_ensure_built() {
  local root="$1"
  local bin="$root/llama.cpp/build/bin/node_agent"
  if [[ ! -x "$bin" ]]; then
    echo "run-agent: binary missing, running ./build.sh ..."
    "$root/build.sh"
  fi
}

node_agent_wsl_portproxy_hint() {
  if ! node_agent_is_wsl; then
    return
  fi

  local win_ip port wsl_ip
  win_ip="$(node_agent_detect_lan_ip 2>/dev/null || true)"
  port="${PORT:-9003}"
  wsl_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"

  cat >&2 <<EOF
run-agent: WSL detected — ensure Windows port forwarding is active (PowerShell as Admin):

  \$winIp = "$win_ip"
  \$wslIp = "$wsl_ip"
  netsh interface portproxy add v4tov4 listenaddress=\$winIp listenport=$port connectaddress=\$wslIp connectport=$port
  9100..9700 | ForEach-Object { netsh interface portproxy add v4tov4 listenaddress=\$winIp listenport=\$_ connectaddress=\$wslIp connectport=\$_ }

EOF
}
