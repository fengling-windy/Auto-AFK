# Experiment: does qwindows.dll actually get loaded into MuMuManager.exe?
#
# The discriminator is the WER crash report's LoadedModule list: if a fix works,
# qwindows.dll shows up there (as it does for MuMuNxMain.exe).  Process
# aliveness is useless here because this shell has no interactive desktop and
# every GUI process fail-fasts.
#
# Usage: powershell -File tools\mumu_qpa_probe.ps1 -Label x -EnvSpec "K=V;K2=V2"
param(
    [string]$Label = "baseline",
    [string]$EnvSpec = "",
    [int]$WaitSec = 12
)

$werDir = "C:\ProgramData\Microsoft\Windows\WER\ReportArchive"
$nx = "E:\MuMuPlayer\nx_main"
$exe = Join-Path $nx "MuMuManager.exe"

function Get-WerSnapshot {
    Get-ChildItem $werDir -Directory -Filter "AppCrash_MuMuManager.exe_*" -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Name
}

function Show-NewReports($before) {
    $after = Get-WerSnapshot
    $new = $after | Where-Object { $before -notcontains $_ }
    if (-not $new) { Write-Output "  NEW REPORTS: none"; return }
    foreach ($n in $new) {
        $f = Join-Path (Join-Path $werDir $n) "Report.wer"
        $txt = Get-Content $f -Raw -Encoding Unicode
        $mods = [regex]::Matches($txt, "(?m)^LoadedModule\[\d+\]=(.+)$") |
            ForEach-Object { $_.Groups[1].Value.Trim() }
        $qw = $mods | Where-Object { $_ -match 'qwindows' }
        $wt = $mods | Where-Object { $_ -match 'WTSAPI32' }
        Write-Output ("  {0}" -f $n.Substring(0, [Math]::Min(46, $n.Length)))
        Write-Output ("    modules={0}  qwindows={1}  WTSAPI32={2}" -f `
            $mods.Count, $(if ($qw) { "LOADED" } else { "MISSING" }), $(if ($wt) { "LOADED" } else { "MISSING" }))
    }
}

Get-Process -Name MuMuManager, MuMuNxUpdater, MuMuNxMain, MuMuNxDevice, MuMuNxService -ErrorAction SilentlyContinue |
    ForEach-Object { try { $_.Kill() } catch {} }
Start-Sleep -Milliseconds 500

$before = Get-WerSnapshot
$errFile = Join-Path $env:TEMP ("qpa_" + $Label + "_" + [guid]::NewGuid().ToString("N").Substring(0, 6) + ".log")

# apply candidate env for the child process only
foreach ($pair in ($EnvSpec -split ';' | Where-Object { $_ })) {
    $idx = $pair.IndexOf('=')
    if ($idx -lt 1) { continue }
    $k = $pair.Substring(0, $idx)
    $v = $pair.Substring($idx + 1)
    Set-Item -Path ("Env:" + $k) -Value $v
}

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $exe
$psi.WorkingDirectory = $nx
$psi.UseShellExecute = $false
$psi.RedirectStandardError = $true
$psi.RedirectStandardOutput = $true
$psi.CreateNoWindow = $true
$p = [System.Diagnostics.Process]::Start($psi)
$stderr = $p.StandardError.ReadToEnd()
$null = $p.StandardOutput.ReadToEnd()
$exited = $p.WaitForExit($WaitSec * 1000)
if (-not $exited) { try { $p.Kill() } catch {} }

Start-Sleep -Seconds 3
Write-Output ("=== {0} (env: {1}) ===" -f $Label, $(if ($EnvSpec) { $EnvSpec } else { "<inherited>" }))
Write-Output ("  exit={0} stderrBytes={1}" -f $(if ($exited) { $p.ExitCode } else { "timeout" }), $stderr.Length)
foreach ($needle in @('Cannot load', 'not a valid Qt plugin', 'undefined symbol', 'could not be initialized', 'loaded library', 'platformplugin', 'qwindows')) {
    $hits = ($stderr -split "`n") | Where-Object { $_ -match [regex]::Escape($needle) }
    if ($hits) { foreach ($h in $hits) { Write-Output ("  > " + $h.Trim()) } }
}
Show-NewReports $before
