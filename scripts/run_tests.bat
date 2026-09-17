@echo off
rem One-click: run all unit tests (double-click to run).
rem Usage: run_tests.bat [--no-pause]  (--no-pause: skip the final pause, used by build_fpk.bat)
rem Uses the project venv when present, otherwise falls back to system python.
rem Set PYTHON to override the interpreter (same semantics as run_tests.sh).
rem NOTE: keep this file ASCII-only; cmd parses .bat with the ANSI codepage.
chcp 65001 >nul
rem Piped python stdout defaults to the ANSI codepage (GBK on zh-CN systems);
rem force UTF-8 to match chcp 65001 so Chinese output is never garbled.
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0.."

rem PYTHON env var wins when set (mirrors run_tests.sh), else project venv > system python
set "PY=%PYTHON%"
if defined PY goto py_chosen
set "PY=app\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [info] project venv not found, falling back to system python
  set "PY=python"
)
:py_chosen

echo ==^> Python: %PY%
rem `call` also works when PY resolves to a .bat (e.g. a pyenv-win shim):
rem invoking another batch file without `call` never returns to this script.
call "%PY%" -c "import pytest, httpx, fastapi, sqlalchemy, openpyxl" >nul 2>&1 || call "%PY%" -m pip install --disable-pip-version-check -r app\requirements.txt pytest httpx

echo ==^> Running unit tests...
call "%PY%" -m pytest
set "RC=%errorlevel%"

if "%RC%"=="0" (
  echo [OK] all tests passed
) else (
  echo [FAILED] some tests failed - packaging is blocked
)
if not "%~1"=="--no-pause" pause
exit /b %RC%
