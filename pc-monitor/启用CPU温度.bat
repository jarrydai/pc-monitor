@echo off
rem 本文件按 GBK 保存；显式锁定 936 代码页，防止系统开启 UTF-8 Beta 后中文乱码
chcp 936 >nul
setlocal
title 启用 CPU 精密温度传感器
cd /d "%~dp0"

echo.
echo  ==============================================================
echo    启用 CPU 精密温度传感器（LibreHardwareMonitor）
echo  ==============================================================
echo.
echo  本机主板的 ACPI 温区不是真实的 CPU 温度，要读到准确数值
echo  必须加载传感器驱动。下面全自动完成，中途会弹一次 UAC，
echo  请点【是】。整个过程约 10 秒。
echo.
echo  启用成功：手机 / 平板上显示真实 CPU 温度
echo  未启用时：CPU 温度显示 —（绝不显示假数据）
echo.
pause

if not exist "%~dp0sensors\lhm\LibreHardwareMonitor.exe" (
    echo  [1/3] 正在解压传感器组件，请稍候 ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='SilentlyContinue'; Expand-Archive -LiteralPath '%~dp0sensors\lhm.zip' -DestinationPath '%~dp0sensors\lhm' -Force"
) else (
    echo  [1/3] 传感器组件已就绪
)

if not exist "%~dp0sensors\lhm\LibreHardwareMonitor.exe" (
    echo.
    echo  [失败] 未能解压出 LibreHardwareMonitor.exe
    echo         请确认 sensors\lhm.zip 存在且完整后重试。
    echo.
    pause
    exit /b 1
)

echo  [2/3] 写入配置并注册开机自启 ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sensors\_elevate_temp.ps1"

echo.
echo  --------------------------------------------------------------
echo  完成后可直接关闭本窗口，监控服务无需重启。
echo  --------------------------------------------------------------
echo.
pause
endlocal
