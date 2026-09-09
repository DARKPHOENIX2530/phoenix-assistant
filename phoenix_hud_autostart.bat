@echo off
rem ============================================================
rem  Phoenix HUD worker: starts the HUD server on port 8055 if
rem  it is not already running, then (unless launched with the
rem  "login" argument) opens your browser to it.
rem
rem  The login autostart runs this hidden with the "login" arg.
rem  Double-clicking it normally also opens the browser.
rem ============================================================
cd /d "%~dp0"

rem Prefer the project venv's windowless python if it exists
set "PYW=pythonw"
if exist ".venv\Scripts\pythonw.exe" set "PYW=%CD%\.venv\Scripts\pythonw.exe"

rem Already up? Nothing to do.
powershell -NoProfile -Command "try{Invoke-WebRequest -Uri 'http://127.0.0.1:8055/' -UseBasicParsing -TimeoutSec 2|Out-Null;exit 0}catch{exit 1}" >nul 2>nul
if not errorlevel 1 goto up

start "" "%PYW%" webgui_server.py --no-browser

rem Wait up to ~15 s for the server to answer
set /a tries=0
:wait
timeout /t 1 /nobreak >nul
powershell -NoProfile -Command "try{Invoke-WebRequest -Uri 'http://127.0.0.1:8055/' -UseBasicParsing -TimeoutSec 2|Out-Null;exit 0}catch{exit 1}" >nul 2>nul
if not errorlevel 1 goto up
set /a tries+=1
if %tries% lss 15 goto wait

echo [phoenix] HUD failed to start. Run hud.bat to see the error.
if "%~1"=="login" exit /b 1
pause
exit /b 1

:up
echo [phoenix] HUD running at http://127.0.0.1:8055
if "%~1"=="login" exit /b 0
start "" http://127.0.0.1:8055
exit /b 0
