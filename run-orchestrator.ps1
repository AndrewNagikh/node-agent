# Start native Windows orchestrator.
#
# Usage:
#   .\run-orchestrator.ps1 -Build
#   .\run-orchestrator.ps1 -Port 9000 -ModelsDir "$env:USERPROFILE\.distributed-llm\models"
#
# First-time setup: run from Developer PowerShell or x64 Native Tools Command Prompt.

param(
    [int]$Port = 9000,
    [string]$Model = $env:MODEL,
    [string]$ModelsDir = $env:MODELS_DIR,
    [switch]$Build,
    [switch]$Firewall,
    [switch]$ConfigureFirewallOnly
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Resolve-OrchestratorBinary {
    $ninjaBin = Join-Path $Root "llama.cpp\build\bin\orchestrator.exe"
    $msvcBin = Join-Path $Root "llama.cpp\build\bin\Release\orchestrator.exe"
    if (Test-Path $ninjaBin) {
        return $ninjaBin
    }
    if (Test-Path $msvcBin) {
        return $msvcBin
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
            Write-Host "run-orchestrator: loaded HF_TOKEN from $Path"
        }
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
        Write-Warning "Run as Administrator once with -Firewall to open TCP $HttpPort"
        return
    }
    $name = "DistributedLLM-Orchestrator"
    $existing = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "firewall: rule '$name' already exists"
        return
    }
    New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow -Protocol TCP `
        -LocalPort $HttpPort | Out-Null
    Write-Host "firewall: allowed inbound TCP $HttpPort"
}

if ($Firewall -or $ConfigureFirewallOnly) {
    Ensure-FirewallRules -HttpPort $Port
}
if ($ConfigureFirewallOnly) {
    return
}

$Bin = Resolve-OrchestratorBinary
if ($Build -or -not $Bin) {
    & (Join-Path $Root "build.ps1") orchestrator
    $Bin = Resolve-OrchestratorBinary
}
if (-not $Bin) {
    throw "orchestrator.exe not found - run .\build.ps1 orchestrator"
}

Load-EnvFile -Path (Join-Path $Root ".env")
Ensure-SystemPath

$argsList = @("--listen", "0.0.0.0:$Port")

if ($Model) {
    if (-not (Test-Path $Model)) {
        throw "MODEL not found: $Model"
    }
    $argsList += @("--model", $Model)
}

if ($ModelsDir) {
    New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null
    $argsList += @("--models-dir", $ModelsDir)
}

Write-Host "run-orchestrator: listen=0.0.0.0:$Port"
if ($Model) {
    Write-Host "run-orchestrator: model=$Model"
} else {
    Write-Host "run-orchestrator: layer-first mode (no local MODEL)"
}
if ($ModelsDir) {
    Write-Host "run-orchestrator: models_dir=$ModelsDir"
}

& $Bin @argsList
