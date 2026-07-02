# Start native Windows node_agent (full pipeline support).
#
# Usage:
#   $env:ORCHESTRATOR = "http://192.168.50.154:9000"
#   .\run-agent.ps1 -NodeId node-c -Cuda
#
# First-time setup: scripts\setup-windows.ps1

param(
    [string]$NodeId = "node-c",
    [string]$Orchestrator = $env:ORCHESTRATOR,
    [string]$AdvertiseHost = $env:ADVERTISE_HOST,
    [int]$Port = 0,
    [switch]$Build,
    [switch]$Cuda,
    [switch]$Firewall,
    [switch]$ConfigureFirewallOnly
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BinDir = Join-Path $Root "llama.cpp\build\bin\Release"
$Bin = Join-Path $BinDir "node_agent.exe"
if (-not (Test-Path $Bin)) {
    $BinDir = Join-Path $Root "llama.cpp\build\bin"
    $Bin = Join-Path $BinDir "node_agent.exe"
}

function Ensure-FirewallRules {
  param([int]$HttpPort)
  if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
      [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warning "Run as Administrator once with -Firewall to open TCP $HttpPort and 9100-9700 (pipeline ports)"
    return
  }
  $name = "DistributedLLM-$NodeId"
  $existing = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
  if ($existing) {
    Write-Host "firewall: rule '$name' already exists"
    return
  }
  New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow -Protocol TCP `
    -LocalPort $HttpPort,9100-9700 | Out-Null
  Write-Host "firewall: allowed inbound TCP $HttpPort and 9100-9700"
}

if ($Build -or -not (Test-Path $Bin)) {
    $buildArgs = @("agents")
    if ($Cuda) { $buildArgs += "-Cuda" }
    & (Join-Path $Root "build.ps1") @buildArgs
}

$ports = @{ "node-a" = 9001; "node-b" = 9002; "node-c" = 9003 }
if ($Port -eq 0) { $Port = $ports[$NodeId] }
if (-not $Port) { throw "unknown NodeId $NodeId (use node-a, node-b, node-c)" }

if ($Firewall -or $ConfigureFirewallOnly) {
    Ensure-FirewallRules -HttpPort $Port
}
if ($ConfigureFirewallOnly) {
    return
}

if (-not $Orchestrator) {
    throw "Set ORCHESTRATOR env or pass -Orchestrator"
}

if (-not $AdvertiseHost) {
    $AdvertiseHost = (Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp |
        Where-Object { $_.InterfaceAlias -notmatch 'vEthernet|WSL|Loopback|Virtual' } |
        Select-Object -First 1).IPAddress
}
if (-not $AdvertiseHost) {
    throw "could not detect LAN IP — set ADVERTISE_HOST"
}

# Workers must sit next to node_agent.exe
foreach ($w in @("split_gen3_a", "split_gen3_b", "split_gen3_c")) {
    $wp = Join-Path $BinDir "$w.exe"
    if (-not (Test-Path $wp)) {
        throw "missing worker $wp — run .\build.ps1 agents"
    }
}

Write-Host "run-agent: node=$NodeId port=$Port advertise=$AdvertiseHost orchestrator=$Orchestrator"
Write-Host "verify: curl http://${AdvertiseHost}:$Port/health"

& $Bin --listen "0.0.0.0:$Port" --advertise-host $AdvertiseHost `
    --orchestrator $Orchestrator --node-id $NodeId
