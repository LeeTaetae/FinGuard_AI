param(
    [ValidateRange(1, 65535)][int]$Port = 5000
)

$ErrorActionPreference = 'Stop'
$state = Join-Path $PSScriptRoot ".uipath/crm-server-$Port/active"
$active = Get-Content -LiteralPath $state -Raw -ErrorAction SilentlyContinue
if (-not $active) {
    Write-Output 'No FinGuard launcher-managed server was found.'
    exit 0
}

$runId = $active.Trim()
if ($runId -cnotmatch '^[0-9a-f]{32}$') { throw 'Invalid launcher state.' }
$stopFile = Join-Path (Split-Path -Parent $state) "stop-$runId"
[System.IO.File]::WriteAllText($stopFile, '')
$deadline = [DateTime]::UtcNow.AddSeconds(15)
do {
    Start-Sleep -Milliseconds 200
    if ((Get-Content -LiteralPath $state -Raw -ErrorAction SilentlyContinue) -ne $runId) {
        Write-Output 'FinGuard launcher-managed server stopped.'
        exit 0
    }
} while ([DateTime]::UtcNow -lt $deadline)
throw 'Shutdown did not complete. No Windows process was forcibly stopped.'
