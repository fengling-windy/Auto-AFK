param(
    [string]$Version = "2.0.0",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    $py = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        $py = "python"
    }

    Write-Host "[pack] 1/4 generate gui/afk.ico..."
    $env:QT_QPA_PLATFORM = "offscreen"
    & $py -c "import setup.create_shortcuts as s; path = s.create_icon(); assert path, 'icon creation failed'"
    if ($LASTEXITCODE -ne 0) { throw "icon generation failed" }

    Write-Host "[pack] 2/4 run PyInstaller (--onedir)..."
    & $py -m PyInstaller --noconfirm --clean packaging\Auto-AFK.spec
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }
    if (-not (Test-Path (Join-Path $Root "dist\Auto-AFK\Auto-AFK.exe"))) {
        throw "dist\Auto-AFK\Auto-AFK.exe was not produced"
    }

    $outDir = Join-Path $PSScriptRoot "output"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null

    Write-Host "[pack] 3/4 build portable zip..."
    $stage = Join-Path $outDir "portable_stage"
    if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    Copy-Item (Join-Path $Root "dist\Auto-AFK\*") $stage -Recurse -Force
    New-Item -ItemType Directory -Force -Path (Join-Path $stage "configs\core") | Out-Null
    Copy-Item (Join-Path $Root "configs\core\*.yaml") (Join-Path $stage "configs\core") -Force
    foreach ($dir in @("monitor_kit", "setup", "tools")) {
        Copy-Item (Join-Path $Root $dir) (Join-Path $stage $dir) -Recurse -Force
    }
    Get-ChildItem $stage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    foreach ($f in @("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md", "Auto-AFK.bat", "Auto-AFK-Admin.bat", "Auto-AFK-CLI.bat", "一键挂机.bat")) {
        if (Test-Path (Join-Path $Root $f)) {
            Copy-Item (Join-Path $Root $f) $stage -Force
        }
    }
    $zipPath = Join-Path $outDir ("Auto-AFK-v{0}-win64-portable.zip" -f $Version)
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zipPath -CompressionLevel Optimal
    Remove-Item $stage -Recurse -Force

    if (-not $SkipInstaller) {
        Write-Host "[pack] 4/4 compile Inno Setup installer..."
        $iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
        if (-not $iscc) {
            foreach ($cand in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "${env:ProgramFiles}\Inno Setup 6\ISCC.exe")) {
                if (Test-Path $cand) { $iscc = $cand; break }
            }
        }
        if (-not $iscc) {
            throw "ISCC.exe (Inno Setup 6) not found. Install Inno Setup 6 or pass -SkipInstaller."
        }
        & $iscc "/DAppVersion=$Version" (Join-Path $PSScriptRoot "installer.iss")
        if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }
    }

    Write-Host "[pack] done -> $outDir"
}
finally {
    Pop-Location
}
