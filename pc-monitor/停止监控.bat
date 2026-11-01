@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title PC Monitor - Stop

set "PID="
if exist "%~dp0server.pid" (
  set /p PID=<"%~dp0server.pid"
)

echo.
if defined PID (
  rem /T 连子进程（温度采集的 powershell）一起结束，避免残留
  taskkill /PID !PID! /T /F >nul 2>&1
  if errorlevel 1 (
    echo    进程 !PID! 已不存在，按端口清理...
  ) else (
    echo    已停止服务进程 ^(PID !PID!^)。
  )
) else (
  echo    未找到 PID 记录，按端口清理...
)

rem ==== 兜底：按 8000 端口收尾 ====
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /C:"0.0.0.0:8000" ^| findstr /C:"LISTENING"') do (
  taskkill /PID %%p /T /F >nul 2>&1
  echo    已释放端口 8000 ^(PID %%p^)。
)

del /q "%~dp0server.pid" >nul 2>&1

rem ==== 结果确认 ====
set "STILL="
for /f "tokens=*" %%l in ('netstat -ano ^| findstr /C:"0.0.0.0:8000" ^| findstr /C:"LISTENING"') do set "STILL=1"

echo.
if defined STILL (
  echo    端口 8000 仍被占用，可能有别的程序在用。
) else (
  echo    服务已停止，手机端会显示时钟日历页。
)
echo.
ping -n 4 127.0.0.1 >nul
exit /b 0
