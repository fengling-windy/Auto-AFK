$files = @(
    'E:\GameAFK\Auto-AFK\afk_core\emulator.py',
    'E:\GameAFK\Auto-AFK\afk_core\repair.py',
    'E:\GameAFK\Auto-AFK\afk_core\configsync.py',
    'E:\GameAFK\Auto-AFK\afk_core\doctor.py',
    'E:\GameAFK\Auto-AFK\afk_core\updater.py',
    'E:\GameAFK\Auto-AFK\afk_core\supervisor.py'
)
$utf8 = New-Object System.Text.UTF8Encoding($false)
foreach ($f in $files) {
    $text = [System.IO.File]::ReadAllText($f)
    $text = $text.Replace('from .proc import hidden_kwargs', 'from .proc import popen as proc_popen, run as proc_run')
    $text = $text.Replace('subprocess.run(', 'proc_run(')
    $text = $text.Replace('subprocess.Popen(', 'proc_popen(')
    $text = $text.Replace("                **hidden_kwargs(),`r`n", '')
    $text = $text.Replace("            **hidden_kwargs(),`r`n", '')
    [System.IO.File]::WriteAllText($f, $text, $utf8)
}
foreach ($f in $files) {
    $text = [System.IO.File]::ReadAllText($f)
    $runCount = ([regex]::Matches($text, 'proc_run\(')).Count
    $popenCount = ([regex]::Matches($text, 'proc_popen\(')).Count
    $leftover = ([regex]::Matches($text, 'subprocess\.(run|Popen)\(')).Count
    $name = [System.IO.Path]::GetFileName($f)
    Write-Host ("{0}: proc_run={1} proc_popen={2} leftover={3}" -f $name, $runCount, $popenCount, $leftover)
}
