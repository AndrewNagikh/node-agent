# Start native Windows node_agent (full pipeline support).
#
# With nodes.conf present (copy from nodes.conf.example and edit for your
# LAN), the only thing you need is the node id, in either style:
#
#   .\run-agent.ps1 NodeId=node-c
#   .\run-agent.ps1 -NodeId node-c -Cuda
#
# Everything else (Orchestrator, AdvertiseHost, Port) falls back to
# nodes.conf, then env vars, then the args below.
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
    [switch]$ConfigureFirewallOnly,
    # Accepts make-style KEY=value tokens, e.g. NodeId=node-c or
    # NODE_ID=node-c, alongside the normal -NodeId node-c PowerShell form.
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Apply-KvArgs {
    param([string[]]$Tokens)
    # Hashtable keys are case-insensitive in PowerShell, so normalize first.
    $map = @{
        'nodeid'        = 'NodeId'
        'node_id'       = 'NodeId'
        'orchestrator'  = 'Orchestrator'
        'advertisehost' = 'AdvertiseHost'
        'advertise_host'= 'AdvertiseHost'
        'modelsdir'     = 'ModelsDir'
        'models_dir'    = 'ModelsDir'
        'port'          = 'Port'
    }
    foreach ($tok in $Tokens) {
        if ($tok -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $k = $Matches[1].ToLowerInvariant(); $v = $Matches[2]
            if ($map.ContainsKey($k)) {
                if ($map[$k] -eq 'Port') {
                    Set-Variable -Name Port -Value ([int]$v) -Scope Script
                } else {
                    Set-Variable -Name $map[$k] -Value $v -Scope Script
                }
            } else {
                Write-Warning "run-agent: ignoring unknown KEY=value arg '$tok'"
            }
        } else {
            Write-Warning "run-agent: ignoring unrecognized argument '$tok'"
        }
    }
}

function Load-Topology {
    param([string]$RootDir)
    $conf = Join-Path $RootDir "nodes.conf"
    if (-not (Test-Path $conf)) { $conf = Join-Path $RootDir "nodes.conf.example" }
    if (-not (Test-Path $conf)) { return @{} }
    $known = @("ORCHESTRATOR_HOST", "ORCHESTRATOR_PORT",
               "NODE_A_HOST", "NODE_A_PORT", "NODE_B_HOST", "NODE_B_PORT",
               "NODE_C_HOST", "NODE_C_PORT")
    $topo = @{}
    Get-Content $conf | ForEach-Object {
        $line = ($_ -replace '#.*$', '').Trim()
        if (-not $line -or $line -notmatch '=') { return }
        $eq = $line.IndexOf('=')
        $key = $line.Substring(0, $eq).Trim()
        $val = $line.Substring($eq + 1).Trim()
        if ($known -contains $key) { $topo[$key] = $val }
    }
    return $topo
}

$kvTokens = @()
if ($NodeId -match '^[A-Za-z_][A-Za-z0-9_]*=') {
    $kvTokens += $NodeId
    $NodeId = "node-c"
}
if ($Rest) { $kvTokens += $Rest }
if ($kvTokens) { Apply-KvArgs -Tokens $kvTokens }
$Topology = Load-Topology -RootDir $Root

if (-not $Orchestrator -and $Topology["ORCHESTRATOR_HOST"]) {
    $orchPort = if ($Topology["ORCHESTRATOR_PORT"]) { $Topology["ORCHESTRATOR_PORT"] } else { "9000" }
    $Orchestrator = "http://$($Topology["ORCHESTRATOR_HOST"]):$orchPort"
}
if (-not $AdvertiseHost -and $Topology["$($NodeId.ToUpper() -replace '-', '_')_HOST"]) {
    $AdvertiseHost = $Topology["$($NodeId.ToUpper() -replace '-', '_')_HOST"]
}
if ($Port -eq 0 -and $Topology["$($NodeId.ToUpper() -replace '-', '_')_PORT"]) {
    $Port = [int]$Topology["$($NodeId.ToUpper() -replace '-', '_')_PORT"]
}

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

function Ensure-GpuClockLock {
  # Under real pipeline load, GPU compute arrives in short bursts separated by
  # cross-node network round-trips (waiting on the previous hop's hidden
  # state). Those bursts are too short/sparse for the driver's boost algorithm
  # to ramp clocks up, so the GPU sits at its idle P-state (P8) the whole
  # decode loop even though NVCP "Prefer maximum performance" is set. Locking
  # the clock bypasses that ramp-up heuristic entirely. Measured on node-c
  # (RTX 4070 Ti): stuck at P8/210MHz for the full pipeline run without this,
  # ~30-40 tok/s with high run-to-run variance; ~43-45 tok/s and stable with
  # the lock. See docs/SESSION_2026-07-15_HOMELAB_VALIDATION_AND_FIXES.md.
  param([string]$BinDir)
  if (-not (Test-Path (Join-Path $BinDir "ggml-cuda.dll"))) { return $false }
  if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) { return $false }
  if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
      [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warning "run-agent: GPU may stall at idle clock (P8) between decode bursts in the pipeline, costing throughput and stability. Run once as Administrator to lock the boost clock via nvidia-smi -lgc."
    return $false
  }
  $maxClock = (nvidia-smi --query-gpu=clocks.max.sm --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
  if (-not $maxClock) { return $false }
  $maxClock = $maxClock.Trim()
  & nvidia-smi -lgc "$maxClock,$maxClock" | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "run-agent: nvidia-smi -lgc failed (exit $LASTEXITCODE); continuing without clock lock"
    return $false
  }
  Write-Host "run-agent: locked GPU clock to $maxClock MHz (avoids P8 throttle during pipeline decode)"
  return $true
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
$gpuClockLocked = Ensure-GpuClockLock -BinDir $BinDir

try {
    & $Bin --listen "0.0.0.0:$Port" --advertise-host $AdvertiseHost `
        --orchestrator $Orchestrator --node-id $NodeId --models-dir $ModelsDir
} finally {
    if ($gpuClockLocked) {
        & nvidia-smi -rgc | Out-Null
        Write-Host "run-agent: reset GPU clock lock"
    }
}
