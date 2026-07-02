# Task 10.0 — Cluster Benchmark Suite (Windows)
param(
    [string]$Profile = $(if ($env:BENCHMARK_PROFILE) { $env:BENCHMARK_PROFILE } else { "ci" })
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $env:ORCHESTRATOR) { $env:ORCHESTRATOR = "http://127.0.0.1:9000" }

Write-Host "Cluster Benchmark Suite (profile=$Profile)"
Write-Host "  ORCHESTRATOR=$($env:ORCHESTRATOR)"

Set-Location $Root
python benchmarks\benchmark_runner.py --profile $Profile
$latest = Get-ChildItem "$Root\logs\benchmark" -Directory -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($latest) {
    Write-Host "Artifacts:"
    Write-Host "  $($latest.FullName)\results.json"
    Write-Host "  $($latest.FullName)\report.md"
    Write-Host "  $($latest.FullName)\report.html"
}
