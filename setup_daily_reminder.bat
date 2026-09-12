@echo off
REM Setup Daily GitHub Commit Reminder at 9 AM
REM Run as Administrator to create the scheduled task

cd /d "%~dp0"

echo Creating scheduled task for daily commit reminder at 9:00 AM...

schtasks /create /tn "Phoenix Daily Commit Reminder" ^
    /tr "\"%CD%\daily_commit_reminder.py\"" ^
    /sc DAILY /st 09:00 ^
    /f /rl HIGHEST

if errorlevel 1 (
    echo.
    echo Failed to create task. Run this as Administrator.
    echo.
    pause
    exit /b 1
)

echo.
echo Task created successfully!
echo It will run daily at 9:00 AM and show a console window with commit status.
echo.
echo To test it now: schtasks /run /tn "Phoenix Daily Commit Reminder"
echo To remove: schtasks /delete /tn "Phoenix Daily Commit Reminder" /f
echo.
pause