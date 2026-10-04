@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem =====================================================================
rem Nectivon canonical Windows launcher implementation (single source).
rem Thin wrappers (Nectivon*.bat) set the NECTIVON_* variables below and
rem then "call" this file. Do not duplicate lifecycle logic elsewhere.
rem
rem Encoding contract: this file must stay pure ASCII (no BOM, CRLF) and
rem must never call chcp. Switching the code page mid-batch makes cmd.exe
rem re-read the file and can drop or glue lines (observed as commands that
rem vanish or execute garbage), which is exactly why the previous Chinese
rem BATs failed intermittently. All human-readable output is produced by
rem the Python service manager: on a real console it uses the Unicode
rem console API, on pipes it honors PYTHONUTF8, so Chinese stays correct
rem in both worlds without any chcp in this file.
rem =====================================================================
set "EXIT_CODE=0"

if not defined NECTIVON_ACTION goto :bad_config
if not defined NECTIVON_INSTANCE goto :bad_config
if not defined NECTIVON_RUNTIME_ROOT goto :bad_config

set "MGR_CMD="
if /i "%NECTIVON_ACTION%"=="start" set "MGR_CMD=start"
if /i "%NECTIVON_ACTION%"=="start-nobrowser" set "MGR_CMD=start --no-browser"
if /i "%NECTIVON_ACTION%"=="stop" set "MGR_CMD=stop"
if /i "%NECTIVON_ACTION%"=="status" set "MGR_CMD=status"
if /i "%NECTIVON_ACTION%"=="enable-autostart" set "MGR_CMD=enable-autostart"
if /i "%NECTIVON_ACTION%"=="disable-autostart" set "MGR_CMD=disable-autostart"
if not defined MGR_CMD goto :bad_config

if /i "%NECTIVON_INSTANCE%"=="stable" (
    set "URL_PORT=8501"
) else if /i "%NECTIVON_INSTANCE%"=="staging" (
    if not defined NECTIVON_STAGING_ROOT goto :bad_config
    if not defined NECTIVON_STAGING_PORT goto :bad_config
    set "URL_PORT=%NECTIVON_STAGING_PORT%"
    set "MGR_CMD=%MGR_CMD% --staging --staging-root "%NECTIVON_STAGING_ROOT%" --staging-port %NECTIVON_STAGING_PORT%"
) else (
    goto :bad_config
)

if /i "%NECTIVON_INSTANCE%"=="staging" if /i "%MGR_CMD:~0,16%"=="enable-autostart" goto :bad_config
if /i "%NECTIVON_INSTANCE%"=="staging" if /i "%MGR_CMD:~0,16%"=="disable-autostart" goto :bad_config

if /i "%NECTIVON_NO_BROWSER%"=="1" (
    if "%MGR_CMD:~0,5%"=="start" set "MGR_CMD=%MGR_CMD% --no-browser"
)

set "RT=%NECTIVON_RUNTIME_ROOT%"
if "%RT:~-1%"=="\" set "RT=%RT:~0,-1%"
if not defined NECTIVON_PYTHON set "NECTIVON_PYTHON=%RT%\.venv\Scripts\python.exe"
set "NECTIVON_MANAGER=%RT%\scripts\service_manager.py"

if not exist "%NECTIVON_PYTHON%" (
    echo [ERROR] Nectivon Python interpreter not found:
    echo   %NECTIVON_PYTHON%
    echo Check the runtime root and finish the first-time install of .venv.
    set "EXIT_CODE=3"
    goto :finish
)

if not exist "%NECTIVON_MANAGER%" (
    echo [ERROR] Nectivon service manager not found:
    echo   %NECTIVON_MANAGER%
    echo The runtime directory is incomplete; scripts\ was moved or deleted.
    set "EXIT_CODE=4"
    goto :finish
)

pushd "%RT%"
"%NECTIVON_PYTHON%" "%NECTIVON_MANAGER%" %MGR_CMD%
set "EXIT_CODE=%ERRORLEVEL%"
popd

if not "%EXIT_CODE%"=="0" (
    echo [ERROR] The previous step failed, exit code: %EXIT_CODE%
) else (
    if /i "%MGR_CMD:~0,5%"=="start" echo URL: http://127.0.0.1:%URL_PORT%
)
goto :finish

:bad_config
echo [CONFIG ERROR] Launcher environment variables are missing or unsupported.
echo This file is the internal implementation _nectivon_launcher.cmd.
echo Start Nectivon via the Nectivon*.bat entry scripts instead.
set "EXIT_CODE=9"
goto :finish

:finish
if not defined EXIT_CODE set "EXIT_CODE=1"
echo.
if not "%NECTIVON_NO_PAUSE%"=="1" (
    echo Press any key to close this window...
    pause >nul
)
endlocal & exit /b %EXIT_CODE%
