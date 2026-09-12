@echo off
rem One-click: run all unit tests (double-click to run).
rem Usage: run_tests.bat [--no-pause]  (--no-pause: skip the final pause, used by build_fpk.bat)
rem Uses the project venv when present, otherwise falls back to system python.
rem NOTE: keep this file ASCII-only; cmd parses .bat with the ANSI codepage.
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

set "PY=app\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [info] project venv not found, falling back to system python
  set "PY=python"
)

echo ==^> Python: %PY%
"%PY%" -c "import pytest, httpx, fastapi, sqlalchemy, openpyxl" >nul 2>&1 || "%PY%" -m pip install --disable-pip-version-check -r app\requirements.txt pytest httpx

echo ==^> Running unit tests...
"%PY%" -m pytest
set "RC=%errorlevel%"

if "%RC%"=="0" (
  echo [OK] all tests passed
) else (
  echo [FAILED] some tests failed - packaging is blocked
)
if not "%~1"=="--no-pause" pause
exit /b %RC%
