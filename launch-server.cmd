@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run setup-v2.ps1 first.
    pause
    exit /b 1
)
if not exist "web\dist\app\index.html" (
    echo Web build missing. Run setup-v2.ps1 first.
    pause
    exit /b 1
)
echo User workspace: http://127.0.0.1:8765/app/
echo Admin console: http://127.0.0.1:8765/admin/
echo Press Ctrl+C to stop only this FileMind service.
".venv\Scripts\python.exe" -m filemind_server serve
if errorlevel 1 pause
