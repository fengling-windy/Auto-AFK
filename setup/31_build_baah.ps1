$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$proj = Join-Path $Root 'BAAH'
if (-not (Test-Path (Join-Path $proj '.git'))) { throw 'BAAH source missing, run 20_clone.ps1 first' }

$reqPath = Join-Path $proj 'requirements.txt'
$raw = Get-Content -LiteralPath $reqPath -Raw
$m = [regex]::Match($raw, '(?s)###CORE###(.*?)###CORE_END###')
if (-not $m.Success) { throw 'requirements.txt CORE section not found' }
$coreReq = Join-Path $proj 'requirements-core.txt'
[System.IO.File]::WriteAllText($coreReq, $m.Groups[1].Value.Trim() + "`r`n", (New-Object System.Text.UTF8Encoding($false)))

Write-Host '[BAAH] create venv (Python 3.11, required by onnxruntime 1.16.2)...'
$code = Invoke-Logged -FilePath 'uv' -Arguments @('venv', '--python', '3.11', (Join-Path $proj '.venv')) -LogFile (Join-Path $LogDir 'baah_build.log')
if ($code -ne 0) { throw "uv venv failed ($code)" }

$py = Join-Path $proj '.venv\Scripts\python.exe'
Write-Host '[BAAH] install CORE requirements...'
$code = Invoke-Logged -FilePath 'uv' -Arguments @('pip', 'install', '--python', $py, '-r', $coreReq) -LogFile (Join-Path $LogDir 'baah_build.log') -Append
if ($code -ne 0) { throw "pip install failed ($code)" }

Write-Host '[BAAH] smoke test: import core modules'
$code = Invoke-Logged -FilePath $py -Arguments @((Join-Path $Root 'tests\smoke_baah.py')) -LogFile (Join-Path $LogDir 'baah_smoke.log')
if ($code -ne 0) { throw "smoke test failed ($code)" }
Write-Host '[BAAH] done'
