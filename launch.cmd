@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Please run setup.ps1 with Python 3.12 first. See README.md.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" run.py
if errorlevel 1 pause
