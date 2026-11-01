@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title PC Monitor

rem ==== 选择 Python（优先 WorkBuddy 内置版本，该路径已有防火墙放行规则）====
set "PY=%LOCALAPPDATA%\Programs\WorkBuddy\resources\vendor\python\python.exe"
if not exist "%PY%" set "PY=python"

rem 清掉代理变量：避免 Start-Process 重建环境块时因大小写重复键报错
set "HTTP_PROXY="
set "HTTPS_PROXY="
set "ALL_PROXY="

rem ==== 已在运行则直接退出 ====
set "RUNNING="
for /f "tokens=*" %%l in ('netstat -ano ^| findstr /C:"0.0.0.0:8000" ^| findstr /C:"LISTENING"') do set "RUNNING=1"
if defined RUNNING (
  echo.
  echo    服务已经在运行了，无需重复启动。
  echo.
  ping -n 3 127.0.0.1 >nul
  exit /b 0
)

echo.
echo    正在后台启动电脑监控服务，请稍候...
echo.

rem ==== 隐藏窗口后台启动 ====
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%PY%' -ArgumentList '-u','%~dp0server.py' -WorkingDirectory '%~dp0' -WindowStyle Hidden -RedirectStandardOutput '%~dp0server.log' -RedirectStandardError '%~dp0server.err.log'" >nul 2>&1

ping -n 4 127.0.0.1 >nul

rem ==== 校验是否真的起来了 ====
set "UP="
for /f "tokens=*" %%l in ('netstat -ano ^| findstr /C:"0.0.0.0:8000" ^| findstr /C:"LISTENING"') do set "UP=1"

if defined UP (
  echo    启动成功！服务正在后台运行，没有窗口，关掉本窗口也不影响。
  echo.
  for /f "tokens=1 delims=" %%a in ('findstr /C:"http://" "%~dp0server.log" 2^>nul') do echo    %%a
  echo.
  echo    停止服务：双击「停止监控.bat」
  echo.
  ping -n 6 127.0.0.1 >nul
  exit /b 0
) else (
  echo    启动失败，以下是错误信息：
  echo    ---------------------------------------------
  type "%~dp0server.err.log" 2>nul
  type "%~dp0server.log" 2>nul
  echo    ---------------------------------------------
  echo.
  pause
  exit /b 1
)
