$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$venv = Join-Path $Root '.venv'
if (-not (Test-Path $venv)) {
    Write-Host '[core] create central venv (Python 3.12)...'
    $code = Invoke-Logged -FilePath 'uv' -Arguments @('venv', '--python', '3.12', $venv) -LogFile (Join-Path $LogDir 'core_build.log')
    if ($code -ne 0) { throw 'central venv creation failed' }
}

$py = Join-Path $venv 'Scripts\python.exe'
Write-Host '[core] install GUI + supervisor deps...'
$code = Invoke-Logged -FilePath 'uv' -Arguments @(
    'pip', 'install', '--python', $py,
    'PySide6==6.11.1', 'psutil', 'pyyaml', 'mss', 'onepush', 'winotify'
) -LogFile (Join-Path $LogDir 'core_build.log') -Append
if ($code -ne 0) { throw 'core deps install failed' }

$code = Invoke-Logged -FilePath $py -Arguments @('-c', 'import PySide6, psutil, yaml, mss, winotify, onepush; print("core venv ok")') -LogFile (Join-Path $LogDir 'core_smoke.log')
if ($code -ne 0) { throw 'core venv smoke failed' }
Write-Host '[core] done'
