@echo off
rem One-click FPK packaging on Windows (native cmd; mirrors scripts/build_fpk.sh).
rem Flow: unit-test gate -> stage clean dir -> fnpack build -> fix cmd/ perms -> self-check.
rem Usage: double-click, or run from cmd.
rem Artifacts: fn-finstat.fpk (raw) + fn-finstat-<alias>.fpk (latest for release,
rem            dev for test builds) + fn-finstat-v<version>.fpk (versioned copy)
rem            + MD5SUMS.txt (checksums of the two delivered copies)
rem Env vars: FNPACK  = path to fnpack.exe (default: auto-find on PATH)
rem           PYTHON  = python interpreter (default: project venv, then system python)
rem           SKIP_TESTS=1  skip unit-test gate (local debug only, never for release)
rem           BUILD_CHANNEL = release (default) or dev (test build, -dev version suffix)
rem NOTE: keep this file ASCII-only; cmd parses .bat with the ANSI codepage.
chcp 65001 >nul
rem Piped python stdout defaults to the ANSI codepage (GBK on zh-CN systems),
rem garbling Chinese output in UTF-8 terminals; force UTF-8 to match chcp.
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0.."

rem ---- python: PYTHON env var > project venv > system python ----
set "PY=%PYTHON%"
if defined PY goto py_chosen
set "PY=app\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [info] project venv not found, falling back to system python
  set "PY=python"
)
:py_chosen
echo ==^> Python: %PY%

rem ---- 0.4 build channel: release (default, version = VERSION) or dev ----
rem dev appends a semver pre-release segment (e.g. 0.7.1-dev.42.g1a2b3c4) so the
rem package is recognisable as a test build in its filename, its manifest and the
rem in-app About page. frontend/package.json always keeps the plain VERSION value.
if not defined BUILD_CHANNEL set "BUILD_CHANNEL=release"
if not defined SHORT_SHA (
  for /f "delims=" %%s in ('git rev-parse --short=7 HEAD 2^>nul') do set "SHORT_SHA=%%s"
)
rem Pass the optional args only when they are actually set. An empty "" argument
rem makes cmd's for/f (which runs the command through `cmd /c`) mis-parse the
rem nested quotes: it aborts with "The system cannot find the path specified."
rem and BUILD_VERSION stays empty, so local packaging dies at version resolution.
rem CI always has BUILD_NUMBER set, which is why this only showed up locally.
set "BN_ARG="
if defined BUILD_NUMBER set "BN_ARG=--build-number %BUILD_NUMBER%"
set "SHA_ARG="
if defined SHORT_SHA set "SHA_ARG=--short-sha %SHORT_SHA%"
if not defined BUILD_VERSION (
  for /f "delims=" %%v in ('"%PY%" scripts\sync_version.py --print --channel %BUILD_CHANNEL% %BN_ARG% %SHA_ARG%') do set "BUILD_VERSION=%%v"
)
if not defined BUILD_VERSION (
  echo [ERROR] cannot resolve package version for channel %BUILD_CHANNEL%
  goto fail
)
rem Channel alias (latest / dev) comes from the same script as the version, so the
rem alias word is never hardcoded here and cannot drift from ci_build.sh.
if not defined CHANNEL_ALIAS (
  for /f "delims=" %%a in ('"%PY%" scripts\sync_version.py --print-alias --channel %BUILD_CHANNEL%') do set "CHANNEL_ALIAS=%%a"
)
if not defined CHANNEL_ALIAS (
  echo [ERROR] cannot resolve channel alias for channel %BUILD_CHANNEL%
  goto fail
)
echo ==^> channel: %BUILD_CHANNEL% / version: %BUILD_VERSION% / alias: %CHANNEL_ALIAS%

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
rem The WHOLE app/static dir is NOT tracked by git (.gitignore): it is produced by
rem `vite build` (outDir=../app/static, emptyOutDir=true). A fresh clone has no
rem artifacts, and packing anyway yields a broken FPK (index.html points to
rem missing JS/CSS -> blank page). So this is a hard gate, not just a warning.
rem index.html is checked first: checking assets/ alone would surface as a vague
rem "entry file not found" from the ref checker, hiding the actual fix.
rem All python invocations use `call` so the script still works when %PY%
rem resolves to a .bat (e.g. a pyenv-win shim instead of a real python.exe).
if not exist "app\static\index.html" (
  echo [ERROR] frontend build artifacts missing: app\static\index.html not found
  echo         run first: cd frontend ^&^& npm ci ^&^& npm run build
  goto fail
)
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
call "%PY%" scripts\check_assets_refs.py app\static\index.html app\static || goto fail
call "%PY%" -c "import pathlib;src=pathlib.Path('frontend/src');idx=pathlib.Path('app/static/index.html');newer=[p for p in src.rglob('*') if p.is_file() and idx.exists() and p.stat().st_mtime>idx.stat().st_mtime];print('WARN: frontend/src has files newer than app/static/index.html:',newer[0],'-> run: cd frontend && npm run build') if newer else None"

rem ---- 2. stage clean directory (only packaging-essential files) ----
set "STAGE=%CD%\.local_tmp\fpk-stage"
if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%\app\app" || goto fail
rem Copy file-by-file: space-separated multi-source copy fails with a
rem syntax error on some Windows setups.
for %%f in (manifest LICENSE) do copy /y "%%f" "%STAGE%\" >nul || goto fail
for %%f in (assets\icons\ICON.PNG assets\icons\ICON_256.PNG) do copy /y "%%f" "%STAGE%\" >nul || goto fail
for %%d in (config cmd wizard) do xcopy /e /i /y /q "%%d" "%STAGE%\%%d\" >nul || goto fail
rem Top-level .py modules all packaged (do NOT revert to an explicit list:
rem listing only main/config once omitted the new top-level file_settings.py,
rem crashing on device at import; fpk_selfcheck.py derives its expected list
rem from the same app\*.py glob so both sides stay in sync).
for %%f in (app\*.py) do copy /y "%%f" "%STAGE%\app\app\" >nul || goto fail
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

rem ---- 2.5 write the package version into staged manifest & config.py ----
rem     VERSION is the single source of truth; manifest/config.py in repo may
rem     lag behind, so we overwrite the staged copies to keep fpk version in sync.
rem     Under the dev channel this value carries the -dev suffix, which is what
rem     makes the installed package show up as a test build on the device.
call "%PY%" scripts\sync_version.py "%STAGE%" --version "%BUILD_VERSION%" --channel "%BUILD_CHANNEL%" || goto fail

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
call "%PY%" scripts\fix_fpk_perm.py fn-finstat.fpk || goto fail

rem ---- 6. self-check: all source files packed, device layout correct ----
call "%PY%" scripts\fpk_selfcheck.py fn-finstat.fpk app || goto fail

rem ---- 7. two copies: channel alias (stable download entry) + versioned (traceable) ----
rem The alias file is overwritten on every build of the same channel, which keeps
rem fn-finstat-latest.fpk / fn-finstat-dev.fpk stable download links; the versioned
rem copy answers "which build is this".
set "FPK_ALIAS=fn-finstat-%CHANNEL_ALIAS%.fpk"
set "FPK_VERSIONED=fn-finstat-v%BUILD_VERSION%.fpk"
copy /y "fn-finstat.fpk" "%FPK_ALIAS%" >nul || goto fail
copy /y "fn-finstat.fpk" "%FPK_VERSIONED%" >nul || goto fail

rem ---- 8. MD5 checksums for the two delivered artifacts ----
rem Only the delivered copies are listed: the raw fn-finstat.fpk is not published,
rem so listing it would just make users wonder where that file is. Delete the old
rem file first so an interrupted build cannot leave stale checksums behind.
if exist "MD5SUMS.txt" del /q "MD5SUMS.txt"
call "%PY%" scripts\gen_checksums.py -o MD5SUMS.txt "%FPK_ALIAS%" "%FPK_VERSIONED%" || goto fail

echo [OK] packaging done: %CD%\fn-finstat.fpk
echo [OK] channel alias: %CD%\%FPK_ALIAS%
echo [OK] versioned copy: %CD%\%FPK_VERSIONED%
echo [OK] md5 checksums: %CD%\MD5SUMS.txt
if "%BUILD_CHANNEL%"=="dev" echo [WARN] test build - do not ship as a release artifact
pause
exit /b 0

:fail
echo [FAILED] packaging aborted - see messages above
pause
exit /b 1
