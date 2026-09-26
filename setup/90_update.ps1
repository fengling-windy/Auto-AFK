$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$projects = @('March7thAssistant', 'BAAH', 'MaaEnd')
foreach ($name in $projects) {
    $dir = Join-Path $Root $name
    if (-not (Test-Path (Join-Path $dir '.git'))) {
        Write-Host ("[skip] " + $name + ' not cloned')
        continue
    }
    Write-Host ("[update] " + $name)
    Push-Location $dir
    try {
        & git pull --ff-only
        & git submodule update --init --recursive
    } finally {
        Pop-Location
    }
}

$py = Join-Path $Root '.venv\Scripts\python.exe'
Write-Host '[update] re-applying monitor patches...'
& $py (Join-Path $Root 'monitor_kit\patcher.py') apply
if ($LASTEXITCODE -ne 0) { throw 'patch re-apply failed, fall back to external monitoring' }

Write-Host '[update] MaaEnd rebuild is required if its sources changed.'
Write-Host '[update] run: powershell -File setup\32_build_maaend.ps1'
Write-Host '[update] done'
