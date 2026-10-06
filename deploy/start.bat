@echo off
REM 漫画工作台 Comic Workbench - 一键启动（Windows）
REM
REM 用法：
REM   start.bat            默认端口 8770
REM   start.bat 9000       指定端口
REM
REM 需要已安装 Python 3.11+ 并加入 PATH（安装时勾选 "Add python.exe to PATH"）。
setlocal
cd /d "%~dp0"

set "PORT=%~1"
if "%PORT%"=="" set "PORT=8770"
set "PY=python"

if not exist ".venv" (
  echo [setup] 创建虚拟环境 .venv ...
  %PY% -m venv .venv || goto :fail
  .venv\Scripts\python -m pip install -q --upgrade pip
  echo [setup] 安装依赖（首次约 1-2 分钟）...
  .venv\Scripts\pip install -q -r backend\requirements.txt || goto :fail
)

if not exist "frontend\dist" (
  echo [error] 缺少前端构建产物 frontend\dist —— 请确认解包完整
  goto :fail
)

echo.
echo   漫画工作台 -^> http://127.0.0.1:%PORT%
echo   （Ctrl+C 停止）
echo.
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% --app-dir backend
goto :eof

:fail
echo.
echo [失败] 启动未完成。请确认已安装 Python 3.11+ 且可用 `python --version` 验证。
pause
