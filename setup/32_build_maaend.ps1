$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$proj = Join-Path $Root 'MaaEnd'
if (-not (Test-Path (Join-Path $proj '.git'))) { throw 'MaaEnd source missing, run 20_clone.ps1 first' }

$goDir = Add-GoToPath
if (-not $goDir) { throw 'Go not found, run 10_install_tools.ps1 first' }
Write-Host ("[MaaEnd] go: " + (& go version))

$pnpmDir = Add-PnpmToPath
if (-not $pnpmDir) { throw 'pnpm not found, run 10_install_tools.ps1 first' }
Write-Host ("[MaaEnd] pnpm: " + (& pnpm --version))

$tokenFile = Join-Path $Root 'configs\core\github_token.txt'
$token = ''
if (Test-Path $tokenFile) {
    foreach ($line in Get-Content -LiteralPath $tokenFile) {
        $trimmed = $line.Trim()
        if ($trimmed -and -not $trimmed.StartsWith('#')) { $token = $trimmed; break }
    }
}
if ($token) {
    $env:GH_TOKEN = $token
    $env:GITHUB_TOKEN = $token
    Write-Host '[MaaEnd] using GitHub token from configs\core\github_token.txt'
} else {
    Remove-Item Env:GH_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
    Write-Host '[MaaEnd] no valid token configured, using anonymous GitHub API'
}

Push-Location $proj
try {
    Write-Host '[MaaEnd] uv run setup-workspace...'
    $code = Invoke-Logged -FilePath 'uv' -Arguments @('run', 'setup-workspace') -LogFile (Join-Path $LogDir 'maaend_workspace.log')
    if ($code -ne 0) { throw "setup-workspace failed ($code)" }

    Write-Host '[MaaEnd] pnpm install...'
    $code = Invoke-Logged -FilePath 'pnpm.cmd' -Arguments @('install') -LogFile (Join-Path $LogDir 'maaend_pnpm.log')
    if ($code -ne 0) { throw "pnpm install failed ($code)" }

    Write-Host '[MaaEnd] uv run build-and-install...'
    $code = Invoke-Logged -FilePath 'uv' -Arguments @('run', 'build-and-install') -LogFile (Join-Path $LogDir 'maaend_build.log')
    if ($code -ne 0) { throw "build-and-install failed ($code)" }

    Write-Host '[MaaEnd] check install dir'
    Get-ChildItem (Join-Path $proj 'install') -ErrorAction SilentlyContinue | Select-Object Name
    Write-Host '[MaaEnd] done'
} finally {
    Pop-Location
}
