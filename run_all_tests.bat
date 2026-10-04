@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Nectivon - Full Test Suite (pytest + ruff)

cd /d "%~dp0"

echo ============================================================
echo  Nectivon Full Regression Test Suite
echo  Working directory: %CD%
echo ============================================================
echo.

rem ---- Locate Python interpreter ----
set "PYTHON_EXE="
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not defined PYTHON_EXE set "PYTHON_EXE=python"

rem ---- Prefer uv if available ----
set "USE_UV=0"
where uv >nul 2>nul
if %ERRORLEVEL%==0 set "USE_UV=1"

rem ---- Step 1: ruff lint check ----
if "%USE_UV%"=="1" (
    echo [1/2] Running: uv run ruff check src pages tests
    uv run ruff check src pages tests
) else (
    echo [1/2] Running: "%PYTHON_EXE%" -m ruff check src pages tests
    "%PYTHON_EXE%" -m ruff check src pages tests
)
if errorlevel 1 (
    echo.
    echo [FAIL] ruff check failed with exit code %ERRORLEVEL%.
    pause
    exit /b %ERRORLEVEL%
)

rem ---- Step 2: pytest full suite ----
if "%USE_UV%"=="1" (
    echo.
    echo [2/2] Running: uv run pytest
    uv run pytest
) else (
    echo.
    echo [2/2] Running: "%PYTHON_EXE%" -m pytest
    "%PYTHON_EXE%" -m pytest
)
set "TEST_EXIT_CODE=%ERRORLEVEL%"

echo.
echo ============================================================
if "%TEST_EXIT_CODE%"=="0" (
    echo  [PASS] All tests passed successfully.
) else (
    echo  [FAIL] Tests exited with code %TEST_EXIT_CODE%.
)
echo ============================================================
echo.
pause
endlocal & exit /b %TEST_EXIT_CODE%
