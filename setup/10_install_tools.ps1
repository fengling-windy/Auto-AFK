$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

Write-Host '=== [0/4] Tool check ==='

$needGo = -not (Add-GoToPath)
if ($needGo) {
    Write-Host '[Go] installing via winget (GoLang.Go)...'
    winget install --id GoLang.Go -e --accept-source-agreements --accept-package-agreements --disable-interactivity
    if (-not (Add-GoToPath)) { throw 'Go install failed or not on PATH' }
}
Write-Host ("[Go] " + (& go version))

$needPnpm = -not (Add-PnpmToPath)
if ($needPnpm) {
    Write-Host '[pnpm] installing via npm...'
    npm install -g pnpm@10
    if (-not (Add-PnpmToPath)) { throw 'pnpm install failed or not on PATH' }
}
Write-Host ("[pnpm] " + (& pnpm --version))

Write-Host '[uv] ensuring Python 3.11 and 3.12...'
uv python install 3.11 3.12
Write-Host '[uv] done'

Write-Host ('[git] ' + (& git --version))
Write-Host ('[node] ' + (& node --version))
Write-Host '=== Tools ready ==='
