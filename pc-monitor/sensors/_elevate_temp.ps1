param([switch]$Elevated)

# 启用 CPU 精密温度传感器（LibreHardwareMonitor）
# 需要管理员权限：加载内核驱动、注册开机自启
$ErrorActionPreference = 'Continue'

$root    = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$sensors = Join-Path $root 'sensors'
$lhmDir  = Join-Path $sensors 'lhm'
$exe     = Join-Path $lhmDir 'LibreHardwareMonitor.exe'
$cfg     = Join-Path $lhmDir 'LibreHardwareMonitor.config'
$task    = 'PCMonitorTempSensor'
$url     = 'http://127.0.0.1:8099/data.json'

function Test-SensorWeb {
    $code = & curl.exe -s --noproxy '*' -o NUL -w "%{http_code}" --max-time 2 $url 2>$null
    return ("$code".Trim() -eq '200')
}

function Write-LhmConfig {
    $pairs = [ordered]@{
        runWebServerMenuItem = 'true'
        listenerPort         = '8099'
        startMinMenuItem     = 'true'
        minTrayMenuItem      = 'true'
    }
    if (Test-Path $cfg) {
        try {
            $doc = New-Object System.Xml.XmlDocument
            $doc.Load($cfg)
            $app = $doc.SelectSingleNode('//appSettings')
            if (-not $app) { throw 'no appSettings' }
            foreach ($k in $pairs.Keys) {
                $n = $app.SelectSingleNode("add[@key='$k']")
                if ($n) {
                    $n.SetAttribute('value', $pairs[$k])
                } else {
                    $e = $doc.CreateElement('add')
                    [void]$e.SetAttribute('key', $k)
                    [void]$e.SetAttribute('value', $pairs[$k])
                    [void]$app.AppendChild($e)
                }
            }
            $doc.Save($cfg)
            return
        } catch { }
    }
    $xml = @'
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <appSettings>
    <add key="runWebServerMenuItem" value="true" />
    <add key="listenerPort" value="8099" />
    <add key="startMinMenuItem" value="true" />
    <add key="minTrayMenuItem" value="true" />
  </appSettings>
</configuration>
'@
    Set-Content -LiteralPath $cfg -Value $xml -Encoding UTF8
}

function Stop-Lhm {
    Get-Process -Name 'LibreHardwareMonitor' -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 600
}

Clear-Host
Write-Host ''
Write-Host '  启用 CPU 精密温度传感器' -ForegroundColor Cyan
Write-Host '  ------------------------------------------------------------' -ForegroundColor DarkGray

if (Test-SensorWeb) {
    Write-Host ''
    Write-Host '  传感器已在运行，无需重复启用。' -ForegroundColor Green
    Write-Host '  手机 / 平板上的监控页面已经在显示真实 CPU 温度。'
    Write-Host ''
    Read-Host '  按回车键关闭'
    exit 0
}

if (-not (Test-Path $exe)) {
    Write-Host ''
    Write-Host '  [失败] 未找到 LibreHardwareMonitor.exe' -ForegroundColor Red
    Write-Host '         请先双击运行 启用CPU温度.bat（它会自动解压组件）。'
    Write-Host ''
    Read-Host '  按回车键关闭'
    exit 1
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin -and -not $Elevated) {
    Write-Host ''
    Write-Host '  加载传感器驱动需要管理员权限。' -ForegroundColor Yellow
    Write-Host '  稍后会弹出 UAC 窗口，请点【是】。' -ForegroundColor Yellow
    Write-Host ''
    try {
        Start-Process -FilePath 'powershell.exe' -Verb RunAs `
            -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Elevated"
        Write-Host '  已请求授权，请在弹出的新窗口中查看结果。' -ForegroundColor Green
        Start-Sleep -Seconds 2
    } catch {
        Write-Host '  授权被取消。' -ForegroundColor Red
        Write-Host '  可改为：右键 启用CPU温度.bat - 以管理员身份运行。' -ForegroundColor Red
        Read-Host '  按回车键关闭'
    }
    exit 0
}

Write-Host ''
Write-Host '  [1/3] 写入配置（本地 Web 服务，端口 8099）...' -ForegroundColor Cyan
Write-LhmConfig
Write-Host '        已完成' -ForegroundColor DarkGray

Write-Host '  [2/3] 注册开机自启并启动传感器 ...' -ForegroundColor Cyan
# 用任务 XML 注册：可以显式指定 WorkingDirectory（schtasks 命令行不支持），
# 否则 LHM 会以 System32 为工作目录启动，可能找不到同目录的 config
$taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>PC Monitor CPU temperature sensor (LibreHardwareMonitor)</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$exe</Command>
      <WorkingDirectory>$lhmDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
$xmlPath = Join-Path $env:TEMP 'pcm_temp_task.xml'
Set-Content -LiteralPath $xmlPath -Value $taskXml -Encoding Unicode
& schtasks.exe /Create /F /TN $task /XML $xmlPath | Out-Null
$taskOk = ($LASTEXITCODE -eq 0)
Remove-Item -LiteralPath $xmlPath -Force -ErrorAction SilentlyContinue

if (-not $taskOk) {
    & schtasks.exe /Create /F /TN $task /RL HIGHEST /SC ONLOGON /TR "`"$exe`"" | Out-Null
    $taskOk = ($LASTEXITCODE -eq 0)
}

Stop-Lhm
if ($taskOk) {
    & schtasks.exe /Run /TN $task | Out-Null
    Write-Host '        计划任务 PCMonitorTempSensor 已就绪（开机自动运行）' -ForegroundColor DarkGray
} else {
    Write-Host '        计划任务不可用，改为直接启动进程' -ForegroundColor Yellow
    Start-Process -FilePath $exe -WorkingDirectory $lhmDir
}

Write-Host '  [3/3] 验证（首次要安装驱动，最多等 30 秒）...' -ForegroundColor Cyan
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    if (Test-SensorWeb) { $ok = $true; break }
    if ($i -eq 9) { Write-Host '        仍在等待 Web 服务响应 ...' -ForegroundColor DarkGray }
}

Write-Host ''
if ($ok) {
    Write-Host '  成功：CPU 精密温度已启用！' -ForegroundColor Green
    Write-Host '  手机 / 平板上的监控页面会在几秒内自动显示真实 CPU 温度。' -ForegroundColor Green
} else {
    Write-Host '  传感器进程已启动，但 Web 服务暂时没有响应。' -ForegroundColor Yellow
    Write-Host '  处理办法：'
    Write-Host '    · 看系统托盘是否有 LibreHardwareMonitor 图标：'
    Write-Host '        有 - 右键图标，Options 里勾选 Run Web Server'
    Write-Host '        无 - 重新运行本脚本'
    Write-Host '    · 仍然不行就打开 sensors\lhm\LibreHardwareMonitor.exe 手动确认一次'
}
Write-Host ''
Read-Host '  按回车键关闭'
