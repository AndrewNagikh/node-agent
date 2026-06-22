#!/usr/bin/env bash
# Shared helpers for run-agent.sh and run-orchestrator.sh

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
  case "${1:-}" in
    node-a) echo 9001 ;;
    node-b) echo 9002 ;;
    node-c) echo 9003 ;;
    *)      echo 9001 ;;
  esac
}

node_agent_find_model() {
  if [[ -n "${MODEL:-}" && -f "$MODEL" ]]; then
    echo "$MODEL"
    return
  fi

  local -a candidates=(
    "${MODEL:-}"
    "$HOME/models/llama-3.2-1b-instruct-q4_k_m.gguf"
    "$HOME/.cache/huggingface/hub/models--hugging-quants--Llama-3.2-1B-Instruct-Q4_K_M-GGUF/snapshots/"*/llama-3.2-1b-instruct-q4_k_m.gguf
    /mnt/c/Users/*/models/llama-3.2-1b-instruct-q4_k_m.gguf
  )

  local p
  for p in "${candidates[@]}"; do
    if [[ -f "$p" ]]; then
      echo "$p"
      return
    fi
  done

  echo "run-agent: model not found. Set MODEL=/path/to/llama-3.2-1b-instruct-q4_k_m.gguf" >&2
  return 1
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
