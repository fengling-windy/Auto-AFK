$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw 'central venv missing, run 05_core_venv.ps1 first' }

Add-GoToPath | Out-Null
Add-PnpmToPath | Out-Null

Push-Location $Root
try {
    Write-Host '[verify] importing/refreshing configs from existing release installs...'
    & $py (Join-Path $Root 'setup\config_tool.py') import
    if ($LASTEXITCODE -ne 0) { throw "config import failed ($LASTEXITCODE)" }

    Write-Host '[verify] ensuring config links and sync...'
    & $py (Join-Path $Root 'setup\config_tool.py') link
    & $py (Join-Path $Root 'setup\config_tool.py') sync-in

    Write-Host '[verify] patch status...'
    & $py (Join-Path $Root 'monitor_kit\patcher.py') verify

    Write-Host '[verify] environment doctor...'
    & $py -m afk_core.cli doctor
    Write-Host '[verify] done'
} finally {
    Pop-Location
}
