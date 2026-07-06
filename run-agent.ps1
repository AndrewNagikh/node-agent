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
    [string]$ModelsDir = $env:MODELS_DIR,
    [int]$Port = 0,
    [switch]$Build,
    [switch]$Cuda,
    [switch]$Firewall,
    [switch]$ConfigureFirewallOnly
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Resolve-NodeAgentBinary {
    $ninjaBin = Join-Path $Root "llama.cpp\build\bin\node_agent.exe"
    $msvcBin = Join-Path $Root "llama.cpp\build\bin\Release\node_agent.exe"
    if (Test-Path $ninjaBin) {
        return @{
            BinDir = Split-Path $ninjaBin -Parent
            Bin    = $ninjaBin
        }
    }
    if (Test-Path $msvcBin) {
        return @{
            BinDir = Split-Path $msvcBin -Parent
            Bin    = $msvcBin
        }
    }
    return $null
}

function Load-EnvFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) { return }
        $eq = $line.IndexOf('=')
        if ($eq -lt 1) { return }
        $key = $line.Substring(0, $eq).Trim()
        $val = $line.Substring($eq + 1).Trim().Trim('"').Trim("'")
        if ($key -eq 'HF_TOKEN' -and $val) {
            $env:HF_TOKEN = $val
            Write-Host "run-agent: loaded HF_TOKEN from $Path"
        }
    }
}

function Ensure-CudaPath {
    param([string]$AgentDir)
    if (-not (Test-Path (Join-Path $AgentDir "ggml-cuda.dll"))) { return }
    $toolkit = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
    if (-not (Test-Path $toolkit)) { return }
    $runtimeDir = Get-ChildItem $toolkit -Directory |
        Sort-Object Name -Descending |
        ForEach-Object {
            $x64 = Join-Path $_.FullName "bin\x64"
            if (Test-Path (Join-Path $x64 "cudart64_*.dll")) { return $x64 }
        } | Select-Object -First 1
    if ($runtimeDir) {
        $env:PATH = "$runtimeDir;$env:PATH"
        Write-Host "run-agent: CUDA runtime in PATH ($runtimeDir)"
    }
}

function Ensure-SystemPath {
    $system32 = Join-Path $env:WINDIR "System32"
    if ((Test-Path $system32) -and ($env:PATH -notlike "*$system32*")) {
        $env:PATH = "$system32;$env:PATH"
    }
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

$resolved = Resolve-NodeAgentBinary
if ($Build -or -not $resolved) {
    $buildArgs = @("agents")
    if ($Cuda) { $buildArgs += "-Cuda" }
    & (Join-Path $Root "build.ps1") @buildArgs
    $resolved = Resolve-NodeAgentBinary
}
if (-not $resolved) {
    throw "node_agent.exe not found - run .\build.ps1 agents or scripts\build-native.cmd"
}
$BinDir = $resolved.BinDir
$Bin = $resolved.Bin

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
    throw "could not detect LAN IP - set ADVERTISE_HOST"
}

# Workers must sit next to node_agent.exe
foreach ($w in @("split_gen3_a", "split_gen3_b", "split_gen3_c")) {
    $wp = Join-Path $BinDir "$w.exe"
    if (-not (Test-Path $wp)) {
        throw "missing worker $wp - run .\build.ps1 agents"
    }
}

Load-EnvFile -Path (Join-Path $Root ".env")

if (-not $ModelsDir) {
    $ModelsDir = Join-Path $env:USERPROFILE ".distributed-llm\models"
}
New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null
Write-Host "run-agent: models_dir=$ModelsDir"

Write-Host "run-agent: node=$NodeId port=$Port advertise=$AdvertiseHost orchestrator=$Orchestrator"
Write-Host "verify: curl http://${AdvertiseHost}:$Port/health"

Ensure-CudaPath -AgentDir $BinDir
Ensure-SystemPath

& $Bin --listen "0.0.0.0:$Port" --advertise-host $AdvertiseHost `
    --orchestrator $Orchestrator --node-id $NodeId --models-dir $ModelsDir
