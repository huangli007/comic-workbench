@echo off
REM 漫画工作台 Comic Workbench - 停止服务（Windows）
REM
REM 用法：
REM   stop.bat            停默认端口 8770
REM   stop.bat 9000       停指定端口
REM
REM 只处理 LISTENING 状态的进程，避免误杀连到该端口的客户端（浏览器等）。
setlocal enabledelayedexpansion
set "PORT=%~1"
if "%PORT%"=="" set "PORT=8770"

set "FOUND="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
  echo 停止 PID %%p
  taskkill /F /PID %%p >nul 2>&1
  set "FOUND=1"
)

if defined FOUND (
  echo.
  echo 已停止端口 %PORT% 上的服务
) else (
  echo.
  echo 端口 %PORT% 上没有运行中的服务
)
endlocal
