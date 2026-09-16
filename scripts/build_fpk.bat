@echo off
rem One-click FPK packaging on Windows (native cmd; mirrors scripts/build_fpk.sh).
rem Flow: unit-test gate -> stage clean dir -> fnpack build -> fix cmd/ perms -> self-check.
rem Usage: double-click, or run from cmd.
rem Env vars: FNPACK  = path to fnpack.exe (default: auto-find on PATH)
rem           PYTHON  = python interpreter (default: project venv, then system python)
rem           SKIP_TESTS=1  skip unit-test gate (local debug only, never for release)
rem NOTE: keep this file ASCII-only; cmd parses .bat with the ANSI codepage.
chcp 65001 >nul
rem Piped python stdout defaults to the ANSI codepage (GBK on zh-CN systems),
rem garbling Chinese output in UTF-8 terminals; force UTF-8 to match chcp.
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0.."

rem ---- python: project venv first, then system python ----
set "PY=app\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [info] project venv not found, falling back to system python
  set "PY=python"
)
echo ==^> Python: %PY%

rem ---- 0. unit-test gate: all tests must pass before packaging ----
if "%SKIP_TESTS%"=="1" (
  echo [WARN] SKIP_TESTS=1 - unit test gate skipped, local debug only
  goto gate_done
)
echo ==^> Test gate: running all unit tests
call scripts\run_tests.bat --no-pause
if errorlevel 1 (
  echo [FAILED] unit tests failed - packaging is blocked
  goto fail
)
:gate_done

rem ---- 1. static artifact gate + stale check (mirrors build_fpk.sh) ----
rem app/static/assets is NOT tracked by git (.gitignore). A fresh clone has no
rem artifacts, and packing anyway yields a broken FPK (index.html points to
rem missing JS/CSS -> blank page). So this is a hard gate, not just a warning.
if not exist "app\static\assets\" (
  echo [ERROR] frontend build artifacts missing: app\static\assets not found
  echo         run first: cd frontend ^&^& npm ci ^&^& npm run build
  goto fail
)
dir /b /a-d "app\static\assets\" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] frontend build artifacts missing: app\static\assets is empty
  echo         run first: cd frontend ^&^& npm ci ^&^& npm run build
  goto fail
)
"%PY%" scripts\check_assets_refs.py app\static\index.html app\static || goto fail
"%PY%" -c "import pathlib;src=pathlib.Path('frontend/src');idx=pathlib.Path('app/static/index.html');newer=[p for p in src.rglob('*') if p.is_file() and idx.exists() and p.stat().st_mtime>idx.stat().st_mtime];print('WARN: frontend/src has files newer than app/static/index.html:',newer[0],'-> run: cd frontend && npm run build') if newer else None"

rem ---- 2. stage clean directory (only packaging-essential files) ----
set "STAGE=%CD%\.local_tmp\fpk-stage"
if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%\app\app" || goto fail
rem Copy file-by-file: space-separated multi-source copy fails with a
rem syntax error on some Windows setups.
for %%f in (manifest ICON.PNG ICON_256.PNG LICENSE) do copy /y "%%f" "%STAGE%\" >nul || goto fail
for %%d in (config cmd wizard) do xcopy /e /i /y /q "%%d" "%STAGE%\%%d\" >nul || goto fail
for %%f in (app\main.py app\config.py) do copy /y "%%f" "%STAGE%\app\app\" >nul || goto fail
for %%d in (api core db parsers schemas services utils static) do xcopy /e /i /y /q "app\%%d" "%STAGE%\app\app\%%d\" >nul || goto fail
copy /y app\requirements.txt "%STAGE%\app\" >nul || goto fail
xcopy /e /i /y /q app\ui "%STAGE%\app\ui\" >nul || goto fail
rem for /r with a non-wildcard set yields dir\name for EVERY walked directory,
rem so guard with if exist or rmdir spams "cannot find the file specified".
for /d /r "%STAGE%" %%d in (__pycache__) do if exist "%%d" rmdir /s /q "%%d"

rem ---- 3. locate fnpack (FNPACK env > PATH > local cache) ----
set "FNPACK_BIN=%FNPACK%"
if not defined FNPACK_BIN where fnpack.exe >nul 2>&1 && set "FNPACK_BIN=fnpack.exe"
if not defined FNPACK_BIN where fnpack >nul 2>&1 && set "FNPACK_BIN=fnpack"
if not defined FNPACK_BIN if exist "%USERPROFILE%\.cache\fnpack\fnpack.exe" set "FNPACK_BIN=%USERPROFILE%\.cache\fnpack\fnpack.exe"
if not defined FNPACK_BIN (
  echo [ERROR] fnpack not found: set FNPACK=path\to\fnpack.exe or add it to PATH
  goto fail
)
echo ==^> fnpack: %FNPACK_BIN%

if exist "%STAGE%\wizard\.gitkeep" del /q "%STAGE%\wizard\.gitkeep"

rem ---- 2.5 sync version from VERSION file to staged manifest & config.py ----
rem     VERSION is the single source of truth; manifest/config.py in repo may
rem     lag behind, so we overwrite the staged copies to keep fpk version in sync.
"%PY%" scripts\sync_version.py "%STAGE%" || goto fail

rem ---- 4. pack (fnpack validates manifest/config/icon/LICENSE/cmd scripts/wizard) ----
rem GOTCHA: fnpack exits 0 even when packing fails (it only prints "Packing failed"),
rem so `|| goto fail` never fires and the previous FPK would be reused as this build's
rem output. Delete the old artifact first, then gate on BOTH the output keyword and
rem the existence of the new artifact.
echo ==^> fnpack build
if exist "fn-finstat.fpk" del /q "fn-finstat.fpk"
set "FNPACK_LOG=%STAGE%\..\fnpack-build.log"
"%FNPACK_BIN%" build --directory "%STAGE%" > "%FNPACK_LOG%" 2>&1
type "%FNPACK_LOG%"
findstr /c:"Packing failed" "%FNPACK_LOG%" >nul
rem findstr returns 0 when the keyword IS present
if not errorlevel 1 (
  echo [ERROR] fnpack packing failed - old artifact deleted on purpose
  goto fail
)
if not exist "fn-finstat.fpk" (
  echo [ERROR] fnpack produced no fn-finstat.fpk
  goto fail
)

rem ---- 5. fix cmd/ permission bits (Windows fnpack writes 0666 -> 0755) ----
"%PY%" scripts\fix_fpk_perm.py fn-finstat.fpk || goto fail

rem ---- 6. self-check: all source files packed, device layout correct ----
"%PY%" scripts\fpk_selfcheck.py fn-finstat.fpk app || goto fail

echo [OK] packaging done: %CD%\fn-finstat.fpk
pause
exit /b 0

:fail
echo [FAILED] packaging aborted - see messages above
pause
exit /b 1
