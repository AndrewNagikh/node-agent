# Windows native build (CUDA recommended for node-c GPU).
#
# Usage (Developer PowerShell or "x64 Native Tools Command Prompt"):
#   .\build.ps1              # node_agent + pipeline workers
#   .\build.ps1 all          # orchestrator + agents
#   .\build.ps1 -Cuda        # GGML_CUDA=ON (RTX / NVIDIA)

param(
    [string]$Mode = "agents",
    [switch]$Cuda,
    [switch]$SkipDeps
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Llama = Join-Path $Root "llama.cpp"
$Build = Join-Path $Llama "build"

function Write-Info($msg) { Write-Host "build.ps1: $msg" }

function Ensure-Command($name, $installHint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "$name not found. $installHint"
    }
}

function Install-Deps {
    if ($SkipDeps) { return }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Info "winget missing - install Git, CMake, VS 2022 Build Tools manually"
        return
    }
    $packages = @(
        @{ Id = "Git.Git"; Name = "git" },
        @{ Id = "Kitware.CMake"; Name = "cmake" }
    )
    foreach ($p in $packages) {
        if (-not (Get-Command $p.Name -ErrorAction SilentlyContinue)) {
            Write-Info "installing $($p.Id) via winget..."
            winget install --id $p.Id -e --accept-package-agreements --accept-source-agreements
        }
    }
    if (-not (Get-Command cl -ErrorAction SilentlyContinue)) {
        Write-Info "if cmake configure fails, install VS 2022 Build Tools (C++):"
        Write-Info "  winget install Microsoft.VisualStudio.2022.BuildTools"
    }
}

if (-not (Test-Path (Join-Path $Llama "CMakeLists.txt"))) {
    Write-Info "initializing submodule..."
    git -C $Root submodule update --init --recursive
}

Install-Deps
Ensure-Command git "winget install Git.Git"
Ensure-Command cmake "winget install Kitware.CMake"

$targets = switch ($Mode) {
    "all"          { @("orchestrator", "node_agent", "split_gen3_a", "split_gen3_b", "split_gen3_c") }
    "orchestrator" { @("orchestrator") }
    "agents"       { @("node_agent", "split_gen3_a", "split_gen3_b", "split_gen3_c") }
    default        { throw "usage: .\build.ps1 [agents|orchestrator|all] [-Cuda]" }
}

$cmakeArgs = @(
    "-S", $Llama,
    "-B", $Build,
    "-DCMAKE_BUILD_TYPE=Release",
    "-DLLAMA_BUILD_TESTS=OFF",
    "-DLLAMA_DISTRIBUTED=ON"
)
if ($Cuda) {
    $cmakeArgs += "-DGGML_CUDA=ON"
    Write-Info "GGML_CUDA=ON (requires CUDA Toolkit in PATH)"
}

Write-Info "cmake configure..."
& cmake @cmakeArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Info "building $($targets -join ' ')..."
& cmake --build $Build --config Release --target $targets -j
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Built (Release):"
foreach ($t in $targets) {
    $exe = Join-Path $Build "bin\Release\$t.exe"
    if (-not (Test-Path $exe)) { $exe = Join-Path $Build "bin\$t.exe" }
    Write-Host "  $exe"
}
Write-Host ""
Write-Host 'Start node-c: .\run-agent.ps1 -NodeId node-c -Build:$false'
