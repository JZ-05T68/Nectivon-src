@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Nectivon Release - Stop

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

rem ---- Stop release instance (port 8501) ----
set "NECTIVON_RUNTIME_ROOT=%~dp0"
set "NECTIVON_PYTHON=%PYTHON_EXE%"
set "NECTIVON_ACTION=stop"
set "NECTIVON_INSTANCE=stable"
call "%~dp0_nectivon_launcher.cmd"

endlocal & exit /b %ERRORLEVEL%
