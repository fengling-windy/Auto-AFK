$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw 'central venv missing, run 05_core_venv.ps1 first' }

Write-Host '[patch] applying monitor patches...'
& $py (Join-Path $Root 'monitor_kit\patcher.py') apply
if ($LASTEXITCODE -ne 0) { throw "patch apply failed ($LASTEXITCODE)" }

Write-Host '[patch] verifying...'
& $py (Join-Path $Root 'monitor_kit\patcher.py') verify
Write-Host '[patch] done'
