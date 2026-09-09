@echo off
rem ============================================================
rem  Installs Phoenix HUD autostart: the HUD server launches
rem  hidden at every Windows login, so http://127.0.0.1:8055
rem  always answers - no double-clicking anything.
rem
rem  Creates a Scheduled Task (runs only when you are logged in).
rem  Remove it anytime with uninstall_autostart.bat
rem ============================================================
cd /d "%~dp0"

set TASKNAME=PhoenixHUD

rem Worker must exist and be runnable
if not exist "phoenix_hud_autostart.bat" (
    echo [phoenix] phoenix_hud_autostart.bat missing - cannot install.
    pause
    exit /b 1
)

schtasks /Create /F /TN "%TASKNAME%" ^
    /TR "\"%CD%\phoenix_hud_autostart.bat\" login" ^
    /SC ONLOGON /RL LIMITED /F

if errorlevel 1 (
    echo [phoenix] Failed to create task. Try running this as Administrator.
    pause
    exit /b 1
)

echo [phoenix] Autostart installed. The HUD will start hidden every login.
echo           Test it now:
schtasks /Run /TN "%TASKNAME%" >nul 2>nul
timeout /t 3 /nobreak >nul
powershell -NoProfile -Command "try{Invoke-WebRequest -Uri 'http://127.0.0.1:8055/' -UseBasicParsing -TimeoutSec 2|Out-Null;echo '  OK - http://127.0.0.1:8055 answers'}catch{echo '  not up yet - give it a few seconds or run phoenix_hud_autostart.bat to see errors'}"
echo           Remove anytime with: uninstall_autostart.bat
pause
