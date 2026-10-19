@echo off
rem Build a fresh dist\Tracking\Tracking.exe (sets up .venv on first use) and self-test it.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
if errorlevel 1 (
    echo.
    echo Build failed - see the messages above.
    pause
    exit /b 1
)
