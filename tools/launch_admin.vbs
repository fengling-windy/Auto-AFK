Option Explicit
Dim fso, shellApp, root
Set fso = CreateObject("Scripting.FileSystemObject")
Set shellApp = CreateObject("Shell.Application")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
shellApp.ShellExecute root & "\.venv\Scripts\pythonw.exe", """" & root & "\gui\main.py"" --autorun", root, "runas", 1
