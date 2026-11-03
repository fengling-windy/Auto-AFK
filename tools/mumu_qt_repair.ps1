# mumu_qt_repair.ps1
# Diagnose and repair the MuMu front-end crash:
#   "This application failed to start because no Qt platform plugin could be initialized."
#   (0xC0000409 / FAST_FAIL_FATAL_APP_EXIT, faulting module Qt5Core.dll)
#
# MUST be run inside your own interactive desktop session (double-click / right-click
# "Run with PowerShell"), NOT from a service or remote-agent shell - a session without
# a real desktop can never create windows and would report a false failure.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\mumu_qt_repair.ps1
#       -> diagnose only (no changes)
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\mumu_qt_repair.ps1 -Fix
#       -> diagnose, then apply the safe repairs it can prove are needed
#          (needs an Administrator console; it will tell you if it cannot)
#   ... -Fix -DisableNahimic
#       -> additionally stop + disable the Nahimic (A-Volute/SteelSeries) audio APO service,
#          which is the only third-party DLL present in every recorded crash report.
#          Re-enable later with:  sc.exe config NahimicService start= auto && sc.exe start NahimicService
#
# Everything it changes is logged to .\logs\mumu_qt_repair\<timestamp>\
# (falls back to %LOCALAPPDATA%\Auto-AFK\mumu_qt_repair\ then %TEMP%\Auto-AFK-mumu_qt_repair\;
#  the script prints the directory it actually used at the end - send that log back if unsure)

[CmdletBinding()]
param(
    [switch]$Fix,
    [switch]$DisableNahimic,
    [string]$MuMuRoot = 'E:\MuMuPlayer',
    [int]$WaitSeconds = 25
)

$ErrorActionPreference = 'Continue'
$script:Problems = @()
$script:Fixes    = @()
$script:Stamp    = Get-Date -Format 'yyyyMMdd-HHmmss'

function ConvertTo-WritableDir([string]$path) {
    # Walk up until we find (or can create) a directory we may write into.
    $p = $path
    while ($p -and -not (Test-Path -LiteralPath $p)) {
        $parent = Split-Path -Parent $p
        if (-not $parent -or $parent -eq $p) { break }
        $p = $parent
    }
    if (-not $p) { return $null }
    try {
        [void][IO.Directory]::CreateDirectory($path)
        return $path
    } catch { return $null }
}

# Preferred: <repo>\logs\mumu_qt_repair\<stamp>. Fall back to %LOCALAPPDATA% when the
# repository tree is read-only for this account (a common locked-down setup).
$candidates = @(
    [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\logs\mumu_qt_repair\$($script:Stamp)")),
    (Join-Path $env:LOCALAPPDATA "Auto-AFK\mumu_qt_repair\$($script:Stamp)"),
    (Join-Path $env:TEMP "Auto-AFK-mumu_qt_repair\$($script:Stamp)")
)
$script:LogDir = $null
foreach ($c in $candidates) {
    $script:LogDir = ConvertTo-WritableDir $c
    if ($script:LogDir) { break }
}
if (-not $script:LogDir) { throw 'no writable directory available for the repair log' }
$script:LogFile = Join-Path $script:LogDir 'repair.log'

function Say([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg
    Write-Host $line
    try { [IO.File]::AppendAllText($script:LogFile, $line + "`r`n", [Text.Encoding]::UTF8) } catch { }
}
function Problem([string]$msg) { $script:Problems += $msg; Say "PROBLEM  $msg" }
function Fixed([string]$msg)   { $script:Fixes    += $msg; Say "FIXED    $msg" }
function Ok([string]$msg)      { Say "ok       $msg" }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    return $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---------------------------------------------------------------- 1. environment
function Step-Environment {
    Say '--- 1. environment ---'
    Say ("user            : {0}" -f [Security.Principal.WindowsIdentity]::GetCurrent().Name)
    Say ("interactive     : {0}" -f [Environment]::UserInteractive)
    Say ("session id      : {0}" -f (Get-Process -Id $PID).SessionId)
    $explorer = if (Get-Process explorer -ErrorAction SilentlyContinue) { 'running' } else { 'NOT RUNNING' }
    Say ("explorer.exe    : {0}" -f $explorer)

    $qt = @()
    foreach ($scope in 'Process', 'User', 'Machine') {
        $vars = switch ($scope) {
            'Process' { Get-ChildItem Env: | Where-Object { $_.Name -like 'QT_*' } | ForEach-Object { "{0}={1}" -f $_.Name, $_.Value } }
            'User'    { (Get-ItemProperty 'HKCU:\Environment' -ErrorAction SilentlyContinue).PSObject.Properties |
                        Where-Object { $_.Name -like 'QT_*' } | ForEach-Object { "{0}={1}" -f $_.Name, $_.Value } }
            'Machine' { (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment' -ErrorAction SilentlyContinue).PSObject.Properties |
                        Where-Object { $_.Name -like 'QT_*' } | ForEach-Object { "{0}={1}" -f $_.Name, $_.Value } }
        }
        if ($vars) { $vars | ForEach-Object { $qt += "[$scope] $_" } }
    }
    if ($qt.Count -eq 0) {
        Ok 'no QT_* variable set (good - a stale one is a classic cause of this error)'
    } else {
        $qt | ForEach-Object { Say "  $_" }
        Problem 'QT_* variables exist; a wrong QT_PLUGIN_PATH / QT_QPA_PLATFORM_PLUGIN_PATH breaks plugin lookup'
    }

    # PATH entries that could shadow a DLL the platform plugin needs
    $suspects = 'd3dcompiler_47.dll', 'opengl32.dll', 'qwindows.dll', 'Qt5Core.dll', 'Qt5Gui.dll'
    $pathHits = @()
    foreach ($dir in ($env:PATH -split ';')) {
        $d = $dir.Trim().TrimEnd('\')
        if (-not $d -or -not (Test-Path -LiteralPath $d)) { continue }
        if ($d -like 'C:\WINDOWS*') { continue }
        foreach ($s in $suspects) {
            $f = Join-Path $d $s
            if (Test-Path -LiteralPath $f) { $pathHits += "$f" }
        }
    }
    if ($pathHits.Count -eq 0) { Ok 'no Qt/runtime DLL shadowing nx_main on PATH' }
    else {
        $pathHits | ForEach-Object { Say "  PATH shadow: $_" }
        Problem 'a non-Windows directory on PATH ships Qt/runtime DLLs (may or may not matter here)'
    }
}

# ---------------------------------------------------------------- 2. installation
function Step-Installation {
    Say '--- 2. MuMu installation ---'
    $nx = Join-Path $MuMuRoot 'nx_main'
    if (-not (Test-Path -LiteralPath $nx)) { Problem "nx_main not found under $MuMuRoot"; return }

    $plat = Join-Path $nx 'plugins\platforms'
    $qwin = Join-Path $plat 'qwindows.dll'
    $core = Join-Path $nx 'Qt5Core.dll'
    $gui  = Join-Path $nx 'Qt5Gui.dll'
    $conf = Join-Path $nx 'qt.conf'

    foreach ($f in @($qwin, $core, $gui)) {
        if (Test-Path -LiteralPath $f) {
            $i = Get-Item -LiteralPath $f
            Ok ("{0}  {1} bytes  v{2}" -f $i.Name, $i.Length, $i.VersionInfo.FileVersion)
        } else {
            Problem "missing file: $f"
        }
    }
    $platFiles = @(Get-ChildItem -LiteralPath $plat -Filter *.dll -ErrorAction SilentlyContinue)
    Say ("platform plugins found: {0}" -f ($platFiles.Name -join ', '))
    if ($platFiles.Count -eq 0) { Problem 'plugins\platforms is empty - Qt can only report "Available platform plugins are: windows", so qwindows.dll must be restored by reinstalling MuMu' }

    # stray plugin dirs that Qt would scan BEFORE the real one
    foreach ($stray in @((Join-Path $nx 'platforms'), (Join-Path $MuMuRoot 'platforms'))) {
        if (Test-Path -LiteralPath $stray) {
            $n = @(Get-ChildItem -LiteralPath $stray -ErrorAction SilentlyContinue).Count
            if ($n -eq 0) { Problem "stray EMPTY plugin dir found (shadows nothing but is suspicious): $stray" }
            else { Problem "stray plugin dir found: $stray ($n entries)" }
        }
    }

    # qt.conf: an empty [Paths] is legal (defaults to <appdir>/plugins) but a wrong
    # Prefix/Plugins is fatal. Report the effective content.
    if (Test-Path -LiteralPath $conf) {
        $raw = (Get-Content -LiteralPath $conf -Raw) -replace "`r", ''
        Say ("qt.conf ({0} bytes): {1}" -f $raw.Length, ($raw -replace "`n", ' | '))
        if ($raw -match '(?im)^\s*Prefix\s*=' -or $raw -match '(?im)^\s*Plugins\s*=') {
            $target = if ($raw -match '(?im)^\s*Plugins\s*=\s*(.+)$') { $Matches[1].Trim() } else { '' }
            if ($target -and -not (Test-Path -LiteralPath (Join-Path $nx $target))) {
                Problem "qt.conf points Plugins at '$target' which does not exist under nx_main"
            }
        }
    } else {
        Say 'qt.conf absent (Qt then defaults to <appdir>/plugins - normally fine)'
    }

    # VC++ runtime shipped next to the exe wins over the system copy; a stale UCRT
    # next to a Qt 5.15 build is a known source of odd startup behaviour.
    $ucr = Join-Path $nx 'ucrtbase.dll'
    if (Test-Path -LiteralPath $ucr) {
        $u = Get-Item -LiteralPath $ucr
        $sys = Get-Item 'C:\Windows\System32\ucrtbase.dll' -ErrorAction SilentlyContinue
        Say ("bundled ucrtbase {0}  vs  system {1}" -f $u.VersionInfo.FileVersion, $sys.VersionInfo.FileVersion)
    }
    foreach ($p in @('wtsapi32.dll', 'dwmapi.dll', 'opengl32.dll', 'd3dcompiler_47.dll')) {
        if (Test-Path -LiteralPath (Join-Path $nx $p)) {
            Problem "nx_main contains a private copy of system DLL '$p' - it can shadow the system one and break the platform plugin"
        }
    }
}

# ---------------------------------------------------------------- 3. reproduce
function Invoke-FrontEnd([string]$exeName, [string]$arguments) {
    $nx = Join-Path $MuMuRoot 'nx_main'
    $exe = Join-Path $nx $exeName
    if (-not (Test-Path -LiteralPath $exe)) { return $null }

    Get-Process | Where-Object { $_.ProcessName -match 'MuMu' } | ForEach-Object {
        try { $_.Kill() } catch { }
    }
    Start-Sleep -Milliseconds 900

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exe
    $psi.WorkingDirectory = $nx
    $psi.UseShellExecute = $false
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardOutput = $true
    if ($arguments) { $psi.Arguments = $arguments }
    $psi.EnvironmentVariables['QT_DEBUG_PLUGINS'] = '1'
    $psi.EnvironmentVariables['QT_FORCE_STDERR_LOGGING'] = '1'
    $psi.EnvironmentVariables['QT_ASSUME_STDERR_HAS_CONSOLE'] = '1'

    $tag = ($exeName -replace '\.exe$', '') + ($arguments -replace '[^\w]', '_')
    $errFile = Join-Path $script:LogDir "$tag.err.txt"
    $outFile = Join-Path $script:LogDir "$tag.out.txt"

    $proc = [System.Diagnostics.Process]::Start($psi)
    $errT = $proc.StandardError.ReadToEndAsync()
    $outT = $proc.StandardOutput.ReadToEndAsync()
    $exited = $proc.WaitForExit($WaitSeconds * 1000)
    if (-not $exited) { try { $proc.Kill() } catch { } ; Start-Sleep -Milliseconds 400 }
    $code = $null
    try { $code = $proc.ExitCode } catch { }
    $err = $errT.Result; $out = $outT.Result
    Set-Content -LiteralPath $errFile -Value $err -Encoding UTF8
    Set-Content -LiteralPath $outFile -Value $out -Encoding UTF8

    $hex = if ($null -ne $code) { '{0:X8}' -f ([int64]$code -band 0xFFFFFFFF) } else { 'n/a' }
    $loadedPlugin = ($err -match 'loaded library\s+".*qwindows\.dll"')
    $notFound     = ($err -match 'Cannot load library|cannot open shared object|The specified module could not be found|%1 is not a valid Win32 application')
    $foundMeta    = ($err -match 'Found metadata in lib .*qwindows\.dll')

    Say ("{0} {1} -> exit={2}" -f $exeName, $arguments, $hex)
    Say ("   qwindows metadata found : {0}" -f $foundMeta)
    Say ("   qwindows library loaded : {0}" -f $loadedPlugin)
    Say ("   loader error in stderr  : {0}" -f $notFound)
    Say ("   stderr saved to         : $errFile")

    return [pscustomobject]@{
        Exe = $exeName; Args = $arguments; ExitCode = $code; ExitHex = $hex
        PluginLoaded = $loadedPlugin; MetadataFound = $foundMeta; LoaderError = $notFound
        Stderr = $err; StderrFile = $errFile
    }
}

function Step-Reproduce {
    Say '--- 3. reproduce the crash (this opens the update window briefly) ---'
    if (-not [Environment]::UserInteractive) {
        Problem 'not running on an interactive desktop - results are NOT meaningful, run it from your own desktop'
    }
    if (-not (Get-Process explorer -ErrorAction SilentlyContinue)) {
        Problem 'explorer.exe is not running in this session - if you are on a normal desktop run this again from it'
    }

    $r1 = Invoke-FrontEnd 'MuMuManager.exe' ''
    $r2 = Invoke-FrontEnd 'MuMuNxUpdater.exe' '--silence-check'
    return @($r1, $r2) | Where-Object { $_ }
}

function Report-Verdict($results) {
    Say '--- 4. verdict ---'
    if (-not $results -or $results.Count -eq 0) { Problem 'could not launch either front-end'; return }
    foreach ($r in $results) {
        if ($r.ExitHex -eq 'C0000409') {
            if ($r.PluginLoaded) {
                Problem ("{0} aborted (0xC0000409) AFTER loading qwindows.dll - the platform plugin itself is fine, the crash is inside MuMu/Qt afterwards" -f $r.Exe)
            } elseif ($r.MetadataFound) {
                Problem ("{0} read the plugin metadata but could NOT load qwindows.dll - this is the real 'no Qt platform plugin' failure" -f $r.Exe)
            } else {
                Problem ("{0} aborted (0xC0000409) before touching the platform plugin" -f $r.Exe)
            }
        } elseif ($r.ExitCode -eq 0) {
            Ok ("{0} ran cleanly - the error does not reproduce right now" -f $r.Exe)
        } elseif ($r.ExitHex -match '^F+$' -or $r.ExitCode -eq -1) {
            # These front-ends routinely exit with -1 after printing their CLI help;
            # only a 0xC0000409 (or a WER report) proves the Qt failure.
            Say ("{0} exited with {1} (no crash code; end-of-run terminal state)" -f $r.Exe, $r.ExitHex)
        } else {
            Problem ("{0} exited with {1}" -f $r.Exe, $r.ExitHex)
        }
    }
}

# ---------------------------------------------------------------- 5. repairs
function Step-Fix($results) {
    if (-not $Fix) { Say '--- 5. repairs skipped (pass -Fix to apply) ---'; return }
    Say '--- 5. repairs ---'
    $admin = Test-Admin
    Say ("administrator   : {0}" -f $admin)

    $crashed = @($results | Where-Object { $_.ExitHex -eq 'C0000409' })
    if ($crashed.Count -eq 0) { Ok 'nothing to repair: no crash observed in this run'; return }

    # (a) stale QT_* variables in HKCU/HKLM -> remove them
    foreach ($pair in @(@('HKCU:\Environment', 'User'), @('HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment', 'Machine'))) {
        $key = $pair[0]; $scope = $pair[1]
        $props = (Get-ItemProperty $key -ErrorAction SilentlyContinue).PSObject.Properties | Where-Object { $_.Name -like 'QT_*' }
        foreach ($p in @($props)) {
            if ($scope -eq 'Machine' -and -not $admin) { Problem "cannot clear $($p.Name) (machine scope, needs admin)"; continue }
            try {
                Remove-ItemProperty -Path $key -Name $p.Name -Force -ErrorAction Stop
                Fixed "removed stale $scope variable $($p.Name)"
            } catch { Problem "failed to clear $($p.Name): $($_.Exception.Message)" }
        }
    }

    # (b) qt.conf: make the plugin path explicit instead of relying on defaults
    $nx = Join-Path $MuMuRoot 'nx_main'
    $conf = Join-Path $nx 'qt.conf'
    $backup = Join-Path $script:LogDir 'qt.conf.bak'
    if (-not (Test-Path -LiteralPath $backup)) {
        if (Test-Path -LiteralPath $conf) { Copy-Item -LiteralPath $conf -Destination $backup -Force }
    }
    $wanted = "[Paths]`r`nPrefix = .`r`nPlugins = plugins`r`n"
    $current = if (Test-Path -LiteralPath $conf) { (Get-Content -LiteralPath $conf -Raw) } else { '' }
    if (($current -replace "`r", '') -ne ($wanted -replace "`r", '')) {
        try {
            Set-Content -LiteralPath $conf -Value $wanted -Encoding ASCII -NoNewline
            Fixed "qt.conf rewritten to an explicit [Paths] Prefix=. Plugins=plugins (backup: $backup)"
        } catch {
            Problem "cannot write qwindows/qt.conf under $nx : $($_.Exception.Message) (needs admin / not read-only)"
        }
    } else { Ok 'qt.conf already explicit' }

    # (c) Nahimic APO injection - the only third-party DLL in every crash report
    $nahimicSvc = Get-Service -Name 'NahimicService' -ErrorAction SilentlyContinue
    if ($nahimicSvc) {
        Say ("NahimicService : {0} / {1}" -f $nahimicSvc.Status, $nahimicSvc.StartType)
        if ($DisableNahimic) {
            if (-not $admin) { Problem 'stopping NahimicService needs an Administrator console' }
            else {
                try {
                    Stop-Service -Name 'NahimicService' -Force -ErrorAction Stop
                    Set-Service -Name 'NahimicService' -StartupType Manual -ErrorAction Stop
                    Fixed 'NahimicService stopped and set to Manual (revert: sc.exe config NahimicService start= auto && sc.exe start NahimicService)'
                } catch { Problem "failed to stop NahimicService: $($_.Exception.Message)" }
            }
        } else {
            Say '   (add -DisableNahimic to stop it and retest)'
        }
    } else { Ok 'NahimicService not installed' }
}

# ---------------------------------------------------------------- 6. retest
function Step-Retest {
    if (-not $Fix) { return }
    Say '--- 6. retest after repairs ---'
    $r = Invoke-FrontEnd 'MuMuNxUpdater.exe' '--silence-check'
    if ($r) {
        if ($r.ExitHex -eq 'C0000409') { Problem 'still crashing after repairs' }
        else { Fixed ("no longer crashing (exit={0})" -f $r.ExitHex) }
    }
}

# ---------------------------------------------------------------- main
Say ("MuMu Qt repair - log: {0}" -f $script:LogFile)
Step-Environment
Step-Installation
$results = Step-Reproduce
Report-Verdict $results
Step-Fix $results
Step-Retest

Say '--- summary ---'
if ($script:Fixes.Count)    { Say ("applied fixes  : {0}" -f $script:Fixes.Count); $script:Fixes | ForEach-Object { Say "  + $_" } }
if ($script:Problems.Count) { Say ("problems       : {0}" -f $script:Problems.Count); $script:Problems | ForEach-Object { Say "  ! $_" } }

if ($script:Problems.Count -eq 0) {
    Say 'RESULT: no problem detected in this run.'
    exit 0
} elseif ($script:Fixes.Count -gt 0) {
    Say "RESULT: applied $($script:Fixes.Count) fix(es); see summary above and the log directory."
    exit 0
} else {
    Say 'RESULT: problem confirmed but not repaired. Send this log directory back for the next step:'
    Say "        $($script:LogDir)"
    exit 1
}
