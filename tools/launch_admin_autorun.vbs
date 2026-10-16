Option Explicit
' Launch GUI as admin and auto-start the one-click chain (--autorun).
' Relay through wscript so every child console is created with SW_HIDE (no black window).
Dim fso, shellApp, root, ts, f
Set fso = CreateObject("Scripting.FileSystemObject")
Set shellApp = CreateObject("Shell.Application")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
ts = root & "\logs\launch_trace.log"
On Error Resume Next
Set f = fso.OpenTextFile(ts, 8, True)
f.WriteLine Now & " launch_admin_autorun enter"
f.Close
Err.Clear
shellApp.ShellExecute "wscript.exe", """" & root & "\tools\hidden_run.vbs"" --autorun", root, "runas", 1
Set f = fso.OpenTextFile(ts, 8, True)
f.WriteLine Now & " ShellExecute Err=" & Err.Number & " " & Err.Description
f.Close
On Error Goto 0
