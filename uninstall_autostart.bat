@echo off
rem ============================================================
rem  Removes the Phoenix HUD autostart task.
rem ============================================================
set TASKNAME=PhoenixHUD

schtasks /Delete /TN "%TASKNAME%" /F
if errorlevel 1 (
    echo [phoenix] No autostart task found ^(already removed?^)
) else (
    echo [phoenix] Autostart removed. Start the HUD manually
    echo           with hud.bat or: python webgui_server.py
)
pause
