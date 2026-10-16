Option Explicit
' Start the GUI hidden: any console allocated by the pythonw/venv launcher chain
' is created with SW_HIDE so no black window ever flashes.
Dim fso, sh, root, args, i, ts, f
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
sh.CurrentDirectory = root
args = ""
For i = 0 To WScript.Arguments.Count - 1
    args = args & " " & WScript.Arguments(i)
Next
ts = root & "\logs\launch_trace.log"
On Error Resume Next
Set f = fso.OpenTextFile(ts, 8, True)
f.WriteLine Now & " hidden_run enter args=[" & args & "]"
f.Close
Err.Clear
sh.Run """" & root & "\.venv\Scripts\pythonw.exe"" """ & root & "\gui\main.py""" & args, 0, False
Set f = fso.OpenTextFile(ts, 8, True)
f.WriteLine Now & " sh.Run Err=" & Err.Number & " " & Err.Description
f.Close
On Error Goto 0
