@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Nectivon - Environment Check

cd /d "%~dp0"

echo ============================================================
echo  Nectivon Environment Check
echo ============================================================
echo  Working directory : %CD%
echo  Script directory  : %~dp0
echo ============================================================
echo.

rem ---- Locate Python interpreter ----
set "PYTHON_EXE="
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not defined PYTHON_EXE (
    for /f "delims=" %%P in ('where python 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
)
if not defined PYTHON_EXE (
    echo [ERROR] Python interpreter not found.
    echo Expected: .venv\Scripts\python.exe or Python 3.11+ in PATH.
    pause
    exit /b 1
)

rem ---- Run comprehensive environment diagnostic ----
rem  (checks Python version, .venv, runtime dependencies,
rem   provider config file, credential store presence;
rem   never prints API keys)
set "PYTHONUTF8=1"
"%PYTHON_EXE%" "%~dp0scripts\check_environment.py"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo ============================================================
if "%EXIT_CODE%"=="0" (
    echo  [PASS] Environment check completed successfully.
) else (
    echo  [FAIL] Environment check exited with code %EXIT_CODE%.
)
echo ============================================================
echo.
pause
endlocal & exit /b %EXIT_CODE%
