# Remove legacy WSL portproxy rules that block native node_agent on 9003 and pipeline ports.
# Run as Administrator:
#   powershell -ExecutionPolicy Bypass -File scripts\cleanup-wsl-portproxy.ps1

$ErrorActionPreference = "Stop"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Re-launching as Administrator (click Yes on UAC prompt)..."
    Start-Process powershell -Verb RunAs -ArgumentList @(
        "-ExecutionPolicy", "Bypass",
        "-NoExit",
        "-File", $MyInvocation.MyCommand.Path
    )
    exit 0
}

function Get-PortProxyRuleCount {
    return @(netsh interface portproxy show v4tov4 |
        Select-String '^\d+\.\d+\.\d+\.\d+\s+\d+\s+\d+\.\d+\.\d+\.\d+\s+\d+').Count
}

$before = Get-PortProxyRuleCount
Write-Host "portproxy rules before: $before"

if ($before -eq 0) {
    Write-Host "No portproxy rules found - port 9003 should be free."
    exit 0
}

# All rules on this machine are legacy WSL forwards (9003 + 9100-9700).
Write-Host "Resetting all portproxy rules..."
netsh interface portproxy reset | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "netsh interface portproxy reset failed (exit $LASTEXITCODE)"
}

$after = Get-PortProxyRuleCount
Write-Host "portproxy rules after: $after"
Write-Host ""
Write-Host "Done. Verify: netstat -ano | findstr :9003"
