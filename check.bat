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
if defined FAILED exit /b 1
