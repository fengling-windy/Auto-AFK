$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$proj = Join-Path $Root 'March7thAssistant'
if (-not (Test-Path (Join-Path $proj '.git'))) { throw 'March7thAssistant source missing, run 20_clone.ps1 first' }

Push-Location $proj
try {
    Write-Host '[M7A] uv sync (Python 3.12)...'
    $code = Invoke-Logged -FilePath 'uv' -Arguments @('sync', '--python', '3.12') -LogFile (Join-Path $LogDir 'm7a_build.log')
    if ($code -ne 0) { throw "uv sync failed ($code)" }

    Write-Host '[M7A] smoke test: main.py -h'
    $py = Join-Path $proj '.venv\Scripts\python.exe'
    $code = Invoke-Logged -FilePath $py -Arguments @('main.py', '-h') -LogFile (Join-Path $LogDir 'm7a_smoke.log')
    if ($code -ne 0) { throw "smoke test failed ($code)" }
    Write-Host '[M7A] done'
} finally {
    Pop-Location
}
