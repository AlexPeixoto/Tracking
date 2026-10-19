@echo off
rem Start Tracking. Builds the exe first if it doesn't exist yet.
set "EXE=%~dp0dist\Tracking\Tracking.exe"
if not exist "%EXE%" (
    echo Tracking.exe not built yet, building it now...
    call "%~dp0build.bat" || exit /b 1
)
start "" "%EXE%"
