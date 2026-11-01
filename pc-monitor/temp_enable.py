# -*- coding: utf-8 -*-
"""
启用 CPU 精密温度传感器（内置于 exe，不再需要 bat / ps1）

流程：
  1. 检查传感器组件（LibreHardwareMonitor）是否就绪，缺了就从 exe 内置资源解压
  2. 写入配置：开启本地 Web 服务（127.0.0.1:8099）
  3. 注册计划任务 PCMonitorTempSensor：登录自启 + 最高权限 + 正确工作目录
  4. 启动并验证，最多等 30 秒

因为 LibreHardwareMonitor 的清单声明了 requireAdministrator，
这个模块必须在提权后的进程里执行（由 server.py 用 ShellExecuteW runas 拉起）。
"""
import io
import os
import subprocess
import time

IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0
TASK_NAME = "PCMonitorTempSensor"
LHM_PORT = 8099
LHM_URL = "http://127.0.0.1:%d/data.json" % LHM_PORT

CONFIG_XML = """<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <appSettings>
    <add key="runWebServerMenuItem" value="true" />
    <add key="listenerPort" value="8099" />
    <add key="startMinMenuItem" value="true" />
    <add key="minTrayMenuItem" value="true" />
  </appSettings>
</configuration>
"""

TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
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
      <Command>{exe}</Command>
      <WorkingDirectory>{dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def sensor_web_alive(url=None, timeout=2):
    """传感器 Web 服务是否在响应"""
    import urllib.request
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url or LHM_URL, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def is_admin():
    if not IS_WIN:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _copytree(src, dst):
    import shutil
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if os.path.isdir(s):
            _copytree(s, d)
        else:
            try:
                shutil.copy2(s, d)
            except Exception:
                pass


def ensure_files(base_dir, bundle_dir):
    """确保 exe 同目录下有完整的传感器组件，返回 lhm 目录（失败返回 None）"""
    target = os.path.join(base_dir, "sensors", "lhm")
    exe = os.path.join(target, "LibreHardwareMonitor.exe")
    if os.path.exists(exe):
        return target

    # 1) exe 内置的一份
    src = os.path.join(bundle_dir, "sensors", "lhm")
    if not os.path.exists(os.path.join(src, "LibreHardwareMonitor.exe")):
        # 2) 分发包里的 lhm.zip
        zip_path = os.path.join(base_dir, "sensors", "lhm.zip")
        if os.path.exists(zip_path):
            try:
                import zipfile
                os.makedirs(target, exist_ok=True)
                with zipfile.ZipFile(zip_path) as z:
                    z.extractall(target)
            except Exception:
                pass
        src = target

    if not os.path.exists(os.path.join(src, "LibreHardwareMonitor.exe")):
        return None
    if os.path.abspath(src) != os.path.abspath(target):
        _copytree(src, target)
    if not os.path.exists(exe):
        return None
    return target


def write_config(lhm_dir):
    path = os.path.join(lhm_dir, "LibreHardwareMonitor.config")
    if os.path.exists(path):
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(path)
            root = tree.getroot()
            app = root.find("appSettings")
            if app is not None:
                want = {"runWebServerMenuItem": "true", "listenerPort": str(LHM_PORT),
                        "startMinMenuItem": "true", "minTrayMenuItem": "true"}
                for k, v in want.items():
                    node = None
                    for e in app.findall("add"):
                        if e.get("key") == k:
                            node = e
                            break
                    if node is None:
                        node = ET.SubElement(app, "add")
                        node.set("key", k)
                    node.set("value", v)
                tree.write(path, encoding="utf-8", xml_declaration=True)
                return True
        except Exception:
            pass
    try:
        with io.open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write(CONFIG_XML)
        return True
    except Exception:
        return False


def ensure_task(lhm_dir):
    """注册（或更新）计划任务，返回是否成功"""
    exe = os.path.join(lhm_dir, "LibreHardwareMonitor.exe")
    xml = TASK_XML.replace("{exe}", exe).replace("{dir}", lhm_dir)
    tmp = os.path.join(os.environ.get("TEMP", lhm_dir), "pcm_temp_task.xml")
    try:
        with io.open(tmp, "w", encoding="utf-16", newline="\r\n") as f:
            f.write(xml)
        r = subprocess.run(["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/XML", tmp],
                           capture_output=True, text=True, creationflags=NO_WINDOW)
        ok = r.returncode == 0
        if not ok:      # 退化的注册方式（不含工作目录，至少能用）
            r2 = subprocess.run(["schtasks", "/Create", "/F", "/TN", TASK_NAME,
                                 "/RL", "HIGHEST", "/SC", "ONLOGON", "/TR", exe],
                                capture_output=True, text=True, creationflags=NO_WINDOW)
            ok = r2.returncode == 0
        return ok
    except Exception:
        return False
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


def kill_lhm():
    try:
        subprocess.run(["taskkill", "/F", "/IM", "LibreHardwareMonitor.exe"],
                       capture_output=True, creationflags=NO_WINDOW, timeout=8)
    except Exception:
        pass
    time.sleep(0.6)


def run_task():
    try:
        r = subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME],
                           capture_output=True, creationflags=NO_WINDOW, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def enable(base_dir, bundle_dir, say=print, wait=30):
    """执行启用流程。返回 (是否成功, 说明)"""
    say("")
    say("  启用 CPU 精密温度传感器")
    say("  " + "-" * 52)

    if sensor_web_alive():
        say("")
        say("  传感器已在运行，无需重复启用。")
        say("  手机 / 平板上已经在显示真实 CPU 温度。")
        return True, "already"

    lhm_dir = ensure_files(base_dir, bundle_dir)
    if not lhm_dir:
        say("")
        say("  [失败] 未找到传感器组件 sensors\\lhm")
        say("         请使用完整的分发包（PC-Monitor.zip），")
        say("         或把 sensors 目录放到 PCMonitor.exe 旁边。")
        return False, "missing"

    say("  [1/3] 写入配置（本地 Web 服务，端口 %d）..." % LHM_PORT)
    write_config(lhm_dir)
    say("        已完成")

    say("  [2/3] 注册开机自启并启动传感器 ...")
    task_ok = ensure_task(lhm_dir)
    kill_lhm()
    if task_ok:
        run_task()
        say("        计划任务 %s 已就绪（登录时自动运行）" % TASK_NAME)
    else:
        say("        计划任务不可用，改为直接启动（下次开机需手动启用）")
        try:
            subprocess.Popen([os.path.join(lhm_dir, "LibreHardwareMonitor.exe")],
                             cwd=lhm_dir, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except Exception:
            pass

    say("  [3/3] 验证（首次要安装驱动，最多等 %d 秒）..." % wait)
    ok = False
    for i in range(wait):
        time.sleep(1)
        if sensor_web_alive(timeout=1):
            ok = True
            break
        if i == 9:
            say("        仍在等待 Web 服务响应 ...")

    say("")
    if ok:
        say("  成功：CPU 精密温度已启用！")
        say("  手机 / 平板上的监控页面会在几秒内自动显示真实 CPU 温度。")
        return True, "ok"

    say("  传感器进程已启动，但 Web 服务暂时没有响应。")
    say("  处理办法：")
    say("    · 看右下角托盘有没有 LibreHardwareMonitor 图标，")
    say("      有的话右键 → Options → 勾选 Run Web Server")
    say("    · 或用任务管理器结束 LibreHardwareMonitor.exe 后重试")
    return False, "timeout"
