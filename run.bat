@echo off
rem ============================================================
rem  Phoenix launcher - double-click this file on Windows.
rem  Creates a private Python environment on first run and
rem  installs the single dependency (requests) if missing.
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

rem GUI-only extra dependency (customtkinter).
python -c "import customtkinter" >nul 2>nul
if errorlevel 1 (
    echo [phoenix] Installing customtkinter (GUI)...
    python -m pip install --quiet customtkinter
)

rem Document reading dependency (PyMuPDF).
python -c "import fitz" >nul 2>nul
if errorlevel 1 (
    echo [phoenix] Installing PyMuPDF (Documents)...
    python -m pip install --quiet PyMuPDF
)

python main.py %*
pause
