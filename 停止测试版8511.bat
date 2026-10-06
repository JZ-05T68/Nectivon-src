@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Nectivon Test 8511 - Stop

cd /d "%~dp0"

rem ---- Locate Python interpreter ----
set "PYTHON_EXE="
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not defined PYTHON_EXE (
    for /f "delims=" %%P in ('where python 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
)
if not defined PYTHON_EXE (
    echo [ERROR] Python interpreter not found.
    echo Working directory: %CD%
    pause
    exit /b 3
)

rem ---- Stop test instance on port 8511 ----
set "NECTIVON_RUNTIME_ROOT=%~dp0"
set "NECTIVON_PYTHON=%PYTHON_EXE%"
set "NECTIVON_ACTION=stop"
set "NECTIVON_INSTANCE=staging"
set "NECTIVON_STAGING_ROOT=%~dp0staging-data"
set "NECTIVON_STAGING_PORT=8511"
call "%~dp0_nectivon_launcher.cmd"

endlocal & exit /b %ERRORLEVEL%
