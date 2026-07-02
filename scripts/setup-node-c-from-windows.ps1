# One-shot setup for node-c on a Windows PC with NVIDIA GPU (via WSL2).
# Run in PowerShell as Administrator for best results (WSL + firewall).
#
# What it does:
#   1. Ensures WSL2 + Ubuntu
#   2. Enables mirrored networking (.wslconfig) — no portproxy needed
#   3. Installs build deps inside WSL, clones/updates repo, builds CUDA agent
#   4. Prints run command for node-c
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\setup-node-c-from-windows.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\setup-node-c-from-windows.ps1 -RepoPath C:\Users\you\node-agent
#   powershell -ExecutionPolicy Bypass -File scripts\setup-node-c-from-windows.ps1 -SkipWslConfig

param(
    [string]$RepoPath = "",
    [string]$Orchestrator = "http://192.168.50.154:9000",
    [switch]$SkipWslConfig,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

function Info($msg) { Write-Host "setup-node-c: $msg" }

function Ensure-Wsl {
    if (Get-Command wsl -ErrorAction SilentlyContinue) {
        $status = wsl --status 2>&1
        if ($LASTEXITCODE -eq 0) { return }
    }
    Info "installing WSL2..."
    wsl --install -d Ubuntu
    throw "WSL installed — reboot Windows, open Ubuntu once, then re-run this script"
}

function Set-MirroredNetworking {
    if ($SkipWslConfig) { return }
    $wslconfig = Join-Path $env:USERPROFILE ".wslconfig"
    $content = @"
[wsl2]
networkingMode=mirrored
dnsTunneling=true
firewall=true
autoProxy=true
"@
    if (Test-Path $wslconfig) {
        $existing = Get-Content $wslconfig -Raw
        if ($existing -match "networkingMode=mirrored") {
            Info ".wslconfig already has mirrored networking"
            return
        }
        Info "backing up $wslconfig -> $wslconfig.bak"
        Copy-Item $wslconfig "$wslconfig.bak" -Force
    }
    Set-Content -Path $wslconfig -Value $content -Encoding UTF8
    Info "wrote $wslconfig (mirrored networking — no portproxy for 9003/9100-9700)"
    Info "restarting WSL..."
    wsl --shutdown
    Start-Sleep -Seconds 3
}

function Resolve-WslRepoPath {
    param([string]$WinPath)
    if (-not $WinPath) {
        $WinPath = Join-Path $env:USERPROFILE "node-agent"
    }
  $WinPath = $WinPath -replace '\\', '/'
    if ($WinPath -match '^([A-Z]):(.*)$') {
        $drive = $Matches[1].ToLower()
        return "/mnt/$drive$($Matches[2])"
    }
    return $WinPath
}

Ensure-Wsl
Set-MirroredNetworking

if (-not $RepoPath) {
    $RepoPath = Join-Path $env:USERPROFILE "node-agent"
}

$WslRepo = Resolve-WslRepoPath $RepoPath
Info "WSL repo path: $WslRepo"

$bashScript = @'
set -euo pipefail
REPO="REPO_PLACEHOLDER"
ORCH="ORCH_PLACEHOLDER"
SKIP_BUILD="SKIP_BUILD_PLACEHOLDER"

if [[ ! -d "$REPO/.git" ]]; then
  echo "setup-node-c: cloning into $REPO ..."
  mkdir -p "$(dirname "$REPO")"
  git clone --recurse-submodules git@github.com:AndrewNagikh/node-agent.git "$REPO" || \
  git clone --recurse-submodules https://github.com/AndrewNagikh/node-agent.git "$REPO"
fi

cd "$REPO"
git pull --ff-only || true
git submodule update --init --recursive

if [[ -f "$REPO/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$REPO/.env"
  set +a
fi

if ! command -v nvcc >/dev/null 2>&1; then
  echo "setup-node-c: CUDA toolkit not in PATH inside WSL."
  echo "  Install NVIDIA CUDA on Windows + WSL driver, then in WSL:"
  echo "  sudo apt install -y nvidia-cuda-toolkit   # or NVIDIA repo cuda-toolkit-12-6"
  echo "  Or use WSL CUDA from Windows driver (nvidia-smi should work in WSL first)."
fi

if [[ "$SKIP_BUILD" != "1" ]]; then
  export GGML_CUDA=ON
  ./scripts/setup-node.sh agents
fi

WIN_IP=$(powershell.exe -NoProfile -Command \
  "(Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp -ErrorAction SilentlyContinue | Where-Object { \$_.InterfaceAlias -notmatch 'vEthernet|WSL|Loopback|Virtual' } | Select-Object -First 1).IPAddress" \
  2>/dev/null | tr -d '\r\n' || hostname -I | awk '{print $1}')

echo ""
echo "=============================================="
echo " node-c ready to start (inside WSL):"
echo ""
echo "  cd $REPO"
echo "  export HF_TOKEN=\${HF_TOKEN:-}   # or copy from .env"
echo "  ORCHESTRATOR=$ORCH NODE_ID=node-c ADVERTISE_HOST=$WIN_IP ./run-agent.sh"
echo ""
echo " Verify from another machine:"
echo "  curl http://$WIN_IP:9003/health"
echo "=============================================="
'@

$bashScript = $bashScript.Replace("REPO_PLACEHOLDER", $WslRepo)
$bashScript = $bashScript.Replace("ORCH_PLACEHOLDER", $Orchestrator)
$bashScript = $bashScript.Replace("SKIP_BUILD_PLACEHOLDER", $(if ($SkipBuild) { "1" } else { "0" }))

Info "running setup inside WSL (may take several minutes on first build)..."
$bashScript | wsl -e bash -s
