@echo off
rem Lint + tests + coverage. Installs the dev tools into .venv on first use.
setlocal
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo .venv not found - run build.bat once first.
    exit /b 1
)
"%PY%" -m pip install -q -r "%~dp0requirements-dev.txt" || exit /b 1
echo == ruff ==
"%PY%" -m ruff check "%~dp0tracking" "%~dp0tests" || set FAILED=1
echo == tests + coverage ==
"%PY%" -m pytest -q --cov --cov-report=term --cov-report=html:"%~dp0build\coverage" || set FAILED=1
echo.
echo Coverage report: build\coverage\index.html
rem Your real library, re-processed with the current rules (only when a baseline exists:
rem "python -m tracking.scan.replay --save" records one). Any line listed is a change.
if exist "%LOCALAPPDATA%\Tracking\replay-baseline.tsv" (
    echo == replay of your library ==
    "%PY%" -m tracking.scan.replay || set FAILED=1
)
if defined FAILED exit /b 1
