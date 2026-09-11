@echo off
rem ============================================================
rem  Phoenix HUD - double-click to open the web
rem  frontend (arc-reactor browser HUD at http://127.0.0.1:8055)
rem ============================================================
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install it from https://www.python.org/downloads/
    echo Tick "Add python.exe to PATH" during installation, then run this again.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo [phoenix] First run: creating a private environment...
    python -m venv .venv
)

call ".venv\Scripts\activate.bat"
python -c "import requests" >nul 2>nul
if errorlevel 1 (
    echo [phoenix] Installing requests...
    python -m pip install --quiet requests
)

rem Document reading dependency (PyMuPDF).
python -c "import fitz" >nul 2>nul
if errorlevel 1 (
    echo [phoenix] Installing PyMuPDF (Documents)...
    python -m pip install --quiet PyMuPDF
)

python webgui_server.py
pause
