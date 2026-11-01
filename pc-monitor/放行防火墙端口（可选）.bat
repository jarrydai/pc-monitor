@echo off
setlocal
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting administrator rights ...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo Adding inbound firewall rule for TCP port 8000 ...
netsh advfirewall firewall delete rule name="PC-Monitor-8000" >nul 2>&1
netsh advfirewall firewall add rule name="PC-Monitor-8000" dir=in action=allow protocol=TCP localport=8000 profile=private,domain
echo.
echo Done. Phone should now be able to reach http://192.168.1.107:8000
pause
