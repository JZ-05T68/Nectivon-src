@echo off
chcp 936 >nul
REM 知识星图 - 一键本地服务（可选）
REM 作用：把页面挂在 http://localhost 上打开。一般用不到，双击 index.html 已经能跑。
REM 用法：双击本文件；演示结束后在这个窗口按 Ctrl+C，再关掉窗口。
cd /d "%~dp0"
set PORT=8731
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY (
  echo 没找到 Python，已直接用默认浏览器打开 index.html。
  echo 建议：右键 index.html - 打开方式 - Edge 或 Chrome
  start "" "index.html"
  pause
  exit /b
)
echo 本地服务已启动： http://localhost:%PORT%/index.html
echo 浏览器稍后自动打开。演示结束后，在这个窗口按 Ctrl+C，再关掉窗口。
start "" "http://localhost:%PORT%/index.html"
%PY% -m http.server %PORT% --bind 127.0.0.1
pause
