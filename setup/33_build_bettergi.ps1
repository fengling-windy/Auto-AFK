$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$proj = Join-Path $Root 'BetterGI'
if (-not (Test-Path (Join-Path $proj '.git'))) { throw 'BetterGI source missing, run 20_clone.ps1 first' }

Write-Host '[BetterGI] checking .NET 8 desktop runtime...'
$runtime = Get-ChildItem 'C:\Program Files\dotnet\shared\Microsoft.WindowsDesktop.App' -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like '8.*' }
if (-not $runtime) {
    Write-Host '[BetterGI] installing .NET 8 desktop runtime via winget...'
    winget install --id Microsoft.DotNet.DesktopRuntime.8 -e --accept-source-agreements --accept-package-agreements --disable-interactivity
}

Push-Location $proj
try {
    Write-Host '[BetterGI] dotnet publish...'
    $code = Invoke-Logged -FilePath 'dotnet' -Arguments @(
        'publish', 'BetterGenshinImpact\BetterGenshinImpact.csproj',
        '-c', 'Release', '-r', 'win-x64', '--self-contained', 'false', '-o', 'publish'
    ) -LogFile (Join-Path $LogDir 'bettergi_build.log')
    if ($code -ne 0) { throw "dotnet publish failed ($code)" }

    Write-Host '[BetterGI] linking config/log dirs...'
    Push-Location $Root
    & (Join-Path $Root '.venv\Scripts\python.exe') (Join-Path $Root 'setup\config_tool.py') link --project BetterGI
    Pop-Location

    Write-Host '[BetterGI] done: ' (Join-Path $proj 'publish\BetterGI.exe')
} finally {
    Pop-Location
}
