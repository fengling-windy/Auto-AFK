$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$repos = @(
    @{ Name = 'March7thAssistant'; Url = 'https://github.com/moesnow/March7thAssistant.git'; Extra = @('--recurse-submodules') },
    @{ Name = 'BAAH';              Url = 'https://github.com/BlueArchiveArisHelper/BAAH.git'; Extra = @() },
    @{ Name = 'MaaEnd';            Url = 'https://github.com/MaaEnd/MaaEnd.git'; Extra = @('--recursive', '-b', 'v2') },
    @{ Name = 'BetterGI';          Url = 'https://github.com/babalae/better-genshin-impact.git'; Extra = @('--depth', '1') }
)

foreach ($r in $repos) {
    $dir = Join-Path $Root $r.Name
    if (Test-Path (Join-Path $dir '.git')) {
        Write-Host ("[skip] " + $r.Name + ' already cloned')
        continue
    }
    Write-Host ("[clone] " + $r.Name)
    & git clone @($r.Extra) $r.Url $dir
    if ($LASTEXITCODE -ne 0) { throw ("clone failed: " + $r.Name) }
}
Write-Host '=== Clone done ==='
