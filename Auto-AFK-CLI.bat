@echo off
set "ROOT=%~dp0"
if not exist "%ROOT%.venv\Scripts\python.exe" (
    echo [Auto-AFK] central venv missing, run setup\05_core_venv.ps1 first.
    pause
    exit /b 1
)
cd /d "%ROOT%"
"%ROOT%.venv\Scripts\python.exe" -m afk_core.cli %*
pause
