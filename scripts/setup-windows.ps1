# Native Windows setup for node-c (RTX / CUDA) — no WSL, no portproxy.
# Run in PowerShell; use -Admin for firewall rules.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1 -Cuda -Firewall

param(
    [string]$Orchestrator = "http://192.168.50.154:9000",
    [switch]$Cuda,
    [switch]$Firewall,
    [switch]$SkipDeps,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Info($msg) { Write-Host "setup-windows: $msg" }

function Install-WingetPackages {
    if ($SkipDeps) { return }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Info "winget not found — install Git, CMake, VS 2022 Build Tools, CUDA Toolkit manually"
        return
    }
    $ids = @(
        "Git.Git",
        "Kitware.CMake",
        "Microsoft.VisualStudio.2022.BuildTools",
        "Nvidia.CUDA"
    )
    foreach ($id in $ids) {
        Info "ensuring $id ..."
        winget install --id $id -e --accept-package-agreements --accept-source-agreements 2>$null
    }
    Info "VS Build Tools: select 'Desktop development with C++' in installer if prompted"
    Info "CUDA: ensure nvcc is in PATH (restart terminal after install)"
}

if (-not (Test-Path (Join-Path $Root "llama.cpp\CMakeLists.txt"))) {
    Info "initializing submodule..."
    git -C $Root submodule update --init --recursive
}

Install-WingetPackages

if (-not $SkipBuild) {
    $buildParams = @{ Mode = "agents" }
    if ($Cuda) { $buildParams.Cuda = $true }
    if ($SkipDeps) { $buildParams.SkipDeps = $true }
    & (Join-Path $Root "build.ps1") @buildParams
}

$runArgs = @{
    NodeId        = "node-c"
    Orchestrator  = $Orchestrator
    Build         = $false
}
if ($Cuda) { $runArgs.Cuda = $true }

$ip = (Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp |
    Where-Object { $_.InterfaceAlias -notmatch 'vEthernet|WSL|Loopback|Virtual' } |
    Select-Object -First 1).IPAddress

Write-Host ""
Write-Host "=============================================="
Write-Host " Native Windows node-c ready"
Write-Host ""
Write-Host "  `$env:ORCHESTRATOR = `"$Orchestrator`""
Write-Host "  cd $Root"
Write-Host "  .\run-agent.ps1 -NodeId node-c -Build:`$false$(if ($Firewall) { ' -ConfigureFirewallOnly' })"
Write-Host ""
Write-Host " Verify from LAN:"
Write-Host "  curl http://${ip}:9003/health"
Write-Host "=============================================="

if ($Firewall) {
    & (Join-Path $Root "run-agent.ps1") -NodeId node-c -Orchestrator $Orchestrator -Build:$false -ConfigureFirewallOnly
    Info "firewall configured — run run-agent.ps1 to start the agent"
}
