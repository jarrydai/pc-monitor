# -*- coding: utf-8 -*-
"""
组装可分发目录：dist/PC-Monitor/
  PCMonitor.exe        单文件服务端（内置网页资源 + 温度传感器组件）
  index.html           外部网页（优先使用，可直接修改）
  static/              PWA 图标、manifest、APK
  sensors/lhm/         温度传感器组件（exe 内也内置了一份，这里是双保险）
  启用CPU温度.bat       备用启用方式（一般用托盘菜单或 --enable-temp）
  放行防火墙.bat
  使用说明.txt

用法：python build_exe.py           只打包 exe
      python build_exe.py --dist    打包并组装分发包 + zip
"""
import io
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DIST_ROOT = os.path.join(HERE, "dist")
DIST = os.path.join(DIST_ROOT, "PC-Monitor")
EXE = os.path.join(DIST_ROOT, "PCMonitor.exe")
STAGING = os.path.join(HERE, "build_staging")

README = """电脑资源监控 · 使用说明
================================================

一、这是什么
  在电脑上运行一个小服务，手机 / 平板连同一个 WiFi 用浏览器打开，
  就能实时查看这台电脑的 CPU / GPU（支持 NVIDIA / AMD / Intel）/ 内存 /
  显存 / 温度 / 磁盘 / 网络 / 进程。
  同时会在右下角托盘常驻一个图标：鼠标悬停即可看到当前 CPU / GPU / 内存。

二、怎么启动
  1. 双击 PCMonitor.exe（不会弹任何窗口，直接静默运行在右下角托盘）
  2. 首次运行 Windows 会弹「是否允许访问网络」——必须勾选「专用网络」并点允许
     （不点允许，手机就连不上）
  3. 首次运行还会自动弹一次管理员授权，用来启用 CPU 温度传感器 —— 点【是】即可，
     之后就不用再管了（已注册成登录自启的计划任务）
  4. 想知道地址：右键托盘图标 →「复制地址」，或左键点图标直接打开页面
  5. 退出程序：右键托盘图标 →「退出（停止监控）」

  可选参数（在命令行里用）：
     PCMonitor.exe -p 8080                换端口
     PCMonitor.exe --open                 启动后自动打开浏览器页面
     PCMonitor.exe --console              保留黑窗口看日志（排错用）
     PCMonitor.exe --enable-temp          手动启用 CPU 温度（弹一次 UAC）
     PCMonitor.exe --no-auto-temp         本次启动不自动启用温度
     PCMonitor.exe --install-startup      设置开机自动启动（托盘后台运行）
     PCMonitor.exe --remove-startup       取消开机自动启动

三、右下角托盘图标
  · 鼠标悬停在图标上：显示当前 CPU / GPU / 内存占用
  · 左键单击或双击：打开监控页面
  · 右键菜单：
      - 打开监控页面 / 复制访问地址
      - 开机自动启动（打勾 = 已开启）
      - 启用 CPU 温度传感器
      - 打开程序目录
      - 退出（停止监控）

  新装的图标默认收在托盘的「^」（显示隐藏的图标）里，
  想让它常驻：把图标拖到任务栏即可。

四、CPU 温度（自动启用）
  Windows 不提供用户态可读的 CPU 核心温度，需要额外的传感器组件
  （已内置在 exe 里，不需要额外下载）。
  · 程序启动时会自动帮你启用：第一次弹一次 UAC，点【是】即可
  · 启用成功后注册为登录自启的计划任务，以后开机自动运行、不再弹窗
  · 若当时点了「否」，12 小时内不会再自动弹；需要时右键托盘 →
    「启用 CPU 温度传感器」手动开一次
  · 页面 CPU 温度显示「—」就表示还没启用，程序不会显示假数据

五、手机端 App（可选）
  浏览器打开页面后，菜单里可以「添加到桌面」，用起来和 App 一样。
  也可安装 static\\pc-monitor.apk（安卓，长按页面可改电脑地址）。

六、常见问题
  · 双击后好像没反应
      正常：程序静默运行在托盘里，没有窗口。看右下角托盘「^」里的监控图标。
  · 手机打不开页面
      - 确认手机和电脑连的是同一个 WiFi（不能一个连 5G 一个连光猫）
      - 双击「放行防火墙.bat」，或手动允许 PCMonitor.exe 访问专用网络
      - 托盘右键 →「复制地址」，挑和手机同网段的那个
  · GPU 显示「未检测到」
      - 一般是核显 / 未装驱动的独显，确认显卡驱动已装好
  · 端口被占用
      说明已经启动过一个了（看托盘有没有图标），先退出那个再启动
  · 想看运行日志
      程序目录下的 pcmonitor.log

七、文件说明
  PCMonitor.exe      主程序（网页资源和温度传感器组件都已内置）
  index.html         网页界面，改这里可以调颜色、布局，改完手机刷新即生效
  static\\            图标、APK 等静态文件
  sensors\\           温度传感器组件（exe 内已有，这个目录是备用）
  启用CPU温度.bat     备用的人工启用方式（一般不需要）
  pcmonitor.log      运行日志（自动生成）
"""


def make_lhm_staging():
    """准备打进 exe 的传感器组件（剔除 .pdb 调试文件和 .xml 文档，减小体积）"""
    src = os.path.join(HERE, "sensors", "lhm")
    if not os.path.isdir(src):
        return None
    dst = os.path.join(STAGING, "sensors", "lhm")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    os.makedirs(dst)
    total = [0]

    def _copy(s, d):
        for name in os.listdir(s):
            if name.lower().endswith((".pdb", ".xml")):
                continue
            sp, dp = os.path.join(s, name), os.path.join(d, name)
            if os.path.isdir(sp):
                os.makedirs(dp, exist_ok=True)
                _copy(sp, dp)
            else:
                shutil.copy2(sp, dp)
                total[0] += os.path.getsize(sp)

    _copy(src, dst)
    print("      传感器组件: %.1f MB" % (total[0] / 1048576))
    return dst


def build_exe():
    print("[1/3] 打包 exe ...")
    lhm = make_lhm_staging()
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile",
        "--noconsole",                       # 双击不弹黑窗口，静默在托盘运行
        "--name", "PCMonitor",
        "--add-data", os.path.join(HERE, "index.html") + ";.",
        "--add-data", os.path.join(HERE, "static") + ";static",
        "--icon", os.path.join(HERE, "static", "icon.ico"),
        "--distpath", DIST_ROOT,
        "--workpath", os.path.join(HERE, "build_tmp"),
        "--exclude-module", "tkinter",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
    ]
    if lhm:
        cmd += ["--add-data", lhm + ";sensors/lhm"]
    cmd.append(os.path.join(HERE, "server.py"))
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0 or not os.path.exists(EXE):
        raise SystemExit("打包失败")
    print("      %s  (%.1f MB)" % (EXE, os.path.getsize(EXE) / 1048576))


def make_dist():
    print("[2/3] 组装分发包 ...")
    if os.path.isdir(DIST):
        shutil.rmtree(DIST)
    os.makedirs(DIST)

    shutil.copy2(EXE, os.path.join(DIST, "PCMonitor.exe"))
    shutil.copy2(os.path.join(HERE, "index.html"), os.path.join(DIST, "index.html"))
    shutil.copytree(os.path.join(HERE, "static"), os.path.join(DIST, "static"))

    # 温度传感器（双保险：exe 内置了一份，外部这个目录是备用）
    src_lhm = os.path.join(HERE, "sensors", "lhm")
    if os.path.isdir(src_lhm):
        shutil.copytree(src_lhm, os.path.join(DIST, "sensors", "lhm"))
        shutil.copy2(os.path.join(HERE, "sensors", "_elevate_temp.ps1"),
                     os.path.join(DIST, "sensors", "_elevate_temp.ps1"))
    shutil.copy2(os.path.join(HERE, "启用CPU温度.bat"), os.path.join(DIST, "启用CPU温度.bat"))

    # 防火墙放行脚本（GBK+CRLF）
    fw = (
        "@echo off\r\n"
        "rem 允许 PCMonitor 通过 Windows 防火墙（需要管理员权限）\r\n"
        "chcp 936 >nul\r\n"
        "setlocal\r\n"
        "cd /d \"%~dp0\"\r\n"
        "\r\n"
        "net session >nul 2>&1\r\n"
        "if %errorlevel% neq 0 (\r\n"
        "  echo 正在请求管理员权限 ...\r\n"
        "  powershell -NoProfile -Command \"Start-Process -FilePath '%~f0' -Verb RunAs\"\r\n"
        "  exit /b\r\n"
        ")\r\n"
        "\r\n"
        "netsh advfirewall firewall delete rule name=\"PCMonitor\" >nul 2>&1\r\n"
        "netsh advfirewall firewall add rule name=\"PCMonitor\" dir=in action=allow program=\"%~dp0PCMonitor.exe\" profile=private,domain >nul\r\n"
        "if errorlevel 1 (\r\n"
        "  echo 放行失败，请手动在防火墙里允许 PCMonitor.exe。\r\n"
        ") else (\r\n"
        "  echo 已放行 PCMonitor.exe，手机现在应该能连上了。\r\n"
        ")\r\n"
        "echo.\r\n"
        "pause\r\n"
    )
    with io.open(os.path.join(DIST, "放行防火墙.bat"), "wb") as f:
        f.write(fw.encode("gbk"))

    with io.open(os.path.join(DIST, "使用说明.txt"), "wb") as f:
        f.write(b"\xef\xbb\xbf" + README.replace("\n", "\r\n").encode("utf-8"))

    print("      %s" % DIST)


def make_zip():
    print("[3/3] 压缩 zip ...")
    zpath = os.path.join(DIST_ROOT, "PC-Monitor.zip")
    if os.path.exists(zpath):
        os.remove(zpath)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _dirs, files in os.walk(DIST):
            for fn in files:
                full = os.path.join(root, fn)
                arc = os.path.join("PC-Monitor", os.path.relpath(full, DIST))
                z.write(full, arc)
    print("      %s  (%.1f MB)" % (zpath, os.path.getsize(zpath) / 1048576))


if __name__ == "__main__":
    build_exe()
    if "--dist" in sys.argv:
        make_dist()
        make_zip()
