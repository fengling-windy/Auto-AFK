$Root = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $Root 'logs\projects'
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Add-GoToPath {
    $candidates = @(
        'C:\Program Files\Go\bin',
        "$env:ProgramFiles\Go\bin",
        "$env:LOCALAPPDATA\Programs\Go\bin"
    )
    foreach ($c in $candidates) {
        if (Test-Path (Join-Path $c 'go.exe')) {
            $env:Path = "$c;$env:Path"
            return $c
        }
    }
    return $null
}

function Add-PnpmToPath {
    $npmPrefix = (npm prefix -g 2>$null)
    if ($npmPrefix -and (Test-Path (Join-Path $npmPrefix 'pnpm.cmd'))) {
        $env:Path = "$npmPrefix;$env:Path"
        return $npmPrefix
    }
    return $null
}

function Invoke-Logged {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $false)][object[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][string]$LogFile,
        [switch]$Append
    )
    $errFile = "$LogFile.err"
    if (-not $Append -and (Test-Path $LogFile)) { Remove-Item -LiteralPath $LogFile -Force }
    if (Test-Path $errFile) { Remove-Item -LiteralPath $errFile -Force }
    $p = Start-Process -FilePath $FilePath -ArgumentList $Arguments -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $LogFile -RedirectStandardError $errFile
    if (Test-Path $errFile) {
        $errText = Get-Content -LiteralPath $errFile -Raw -ErrorAction SilentlyContinue
        if ($errText) { Add-Content -LiteralPath $LogFile -Value $errText }
        Remove-Item -LiteralPath $errFile -Force -ErrorAction SilentlyContinue
    }
    Get-Content -LiteralPath $LogFile -Tail 5 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
    return $p.ExitCode
}
