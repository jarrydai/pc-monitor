# PC Monitor · 用手机实时看电脑资源

[![release](https://img.shields.io/github/v/release/jarrydai/pc-monitor?label=release)](https://github.com/jarrydai/pc-monitor/releases/latest)
[![license](https://img.shields.io/github/license/jarrydai/pc-monitor)](LICENSE)

在 Windows 上跑一个小服务，手机 / 平板连同一个 WiFi 用浏览器打开，就能实时看到这台电脑的
CPU、GPU、内存、显存、温度、磁盘、网络和占用最高的进程。Windows 右下角还会常驻一个托盘图标，
鼠标悬停即可看占用，右键打开页面 / 启用温度传感器 / 退出。

配套还有一个自建的安卓 App（横屏全屏，连不上主机时显示时间、农历、星期和天气）。

```
┌──────────────────────────────────────────────┐
│  手机 / 平板浏览器  ── WiFi ──▶  PC 服务端    │
│  http://<电脑IP>:8000        1 秒刷新一次快照 │
└──────────────────────────────────────────────┘
```

## 下载安装

不想折腾源码的话，直接到 **[Releases](https://github.com/jarrydai/pc-monitor/releases/latest)** 下载现成的包：

| 文件 | 平台 | 大小 | 说明 |
| --- | --- | --- | --- |
| `PCMonitor-v1.0.1-windows.exe` | Windows 10/11 x64 | 15 MB | 单文件，双击即用，内置网页与温度传感器组件 |
| `PCMonitor-v1.0.1-windows-full.zip` | Windows 10/11 x64 | 22 MB | 完整分发包，解压后运行 `PCMonitor.exe` |
| `PCMonitor-v1.0.1-android.apk` | Android 7.0+ | 78 KB | 安卓 App，自签名，安装时允许「未知来源」 |

电脑端启动后是静默托盘运行，首次会弹一次 UAC 启用 CPU 温度传感器，点【是】即可。
手机连同一个 WiFi，打开 `http://<电脑IP>:8000`。

> 默认端口 8000 若被 Windows 的 `winnat` 随机保留（Hyper-V / WSL 常见），程序会自动改用
> 8001–8020 或 9001–9020，并在托盘气泡与 `pcmonitor.log` 里告知新地址，不会弹错误框。

## 特性

- **横屏一屏**：CPU / GPU / 内存 / 显存 / 磁盘 / 网络 + 环形占比，小屏也不用滚动
- **GPU 全厂商支持**：NVIDIA（nvidia-smi）→ LibreHardwareMonitor（含 AMD / Intel 温度）→ Windows PDH 计数器，三级自动回退
- **CPU 温度**：借 LibreHardwareMonitor 的 Web 服务读真实核心温度，注册计划任务静默拉起，只在首次弹一次 UAC
- **托盘常驻**：纯 ctypes 调 Win32，无第三方 GUI 库；悬停显示 CPU / GPU / 内存，右键菜单
- **单文件 exe**：PyInstaller 打包，双击静默运行（无黑框、不弹网页），可设开机自启
- **安卓 App**：不用 Gradle 的最小构建链（aapt2 → javac → d8 → zipalign → apksigner）

## 目录结构

```
pc-monitor/                 Windows 服务端 + 网页端
  server.py                 入口：HTTP 服务、1s 采样快照、托盘、开机自启、温度自启
  gpu.py                    GPU 采样：nvidia-smi → LibreHardwareMonitor → PDH
  temp_enable.py            CPU 温度：LHM Web 服务 + 计划任务 PCMonitorTempSensor
  tray.py                   托盘图标与右键菜单（纯 ctypes Win32）
  startup.py                开机自启（HKCU\...\Run）
  build_exe.py              PyInstaller 打包 + 分发包组装 + zip
  index.html                手机端仪表盘（深色、横屏一屏）
  static/                   图标、PWA manifest、打包好的 APK
  sensors/_elevate_temp.ps1 提权启用温度的辅助脚本
  启动监控.bat / 停止监控.bat / 启用CPU温度.bat

android-build/              安卓 App（不用 Gradle）
  app/                      源码：Java + 内嵌 HTML（assets/clock.html）
  build.py                  构建脚本：编译资源、清单、dex、签名
```

## 快速开始（源码运行）

```bash
cd pc-monitor
pip install psutil
python server.py
```

然后浏览器打开 `http://127.0.0.1:8000`。手机访问 `http://<电脑局域网IP>:8000`
（启动日志里会直接打印出来）。

首次运行 Windows 可能弹「是否允许访问网络」，勾选**专用网络**并允许，否则手机连不上。
也可以手动跑一次 `放行防火墙端口（可选）.bat`。

### 命令行参数

| 参数 | 说明 |
| --- | --- |
| `-p 8080` | 换端口（默认 8000） |
| `--open` | 启动后自动打开浏览器 |
| `--console` | 保留控制台窗口看日志（排错用） |
| `--tray` / `--no-tray` | 强制开启 / 关闭托盘图标 |
| `--enable-temp` | 手动启用 CPU 温度（会弹一次 UAC） |
| `--no-auto-temp` | 本次启动不自动启用温度 |
| `--install-startup` / `--remove-startup` | 设置 / 取消开机自启 |

## 打包成 exe

```bash
cd pc-monitor
pip install pyinstaller psutil
python build_exe.py --dist
```

产出：

- `dist/PCMonitor.exe` —— 单文件、GUI 子系统（双击不弹黑框），内置网页与温度传感器组件
- `dist/PC-Monitor/` —— 分发包：exe + index.html + static + sensors + 说明
- `dist/PC-Monitor.zip` —— 打包好的 zip，可直接拷到别的电脑

## HTTP 接口

只有一个接口，1 秒刷新一次：

```
GET /api/stats
```

```jsonc
{
  "ts": 1762000000.0,
  "host": "DESKTOP-XXXX",
  "uptime": 36000,
  "cpu":  { "percent": 12.4, "cores": [ ... ], "count": 16, "freq": 3800,
            "name": "...", "temp": 45.0, "temp_src": "lhm" },
  "gpu":  { ... },          // 主 GPU
  "gpus": [ ... ],          // 全部 GPU
  "mem":  { "total": 34359738368, "used": 12345678901, "percent": 36.0, ... },
  "disk": { "total": ..., "used": ..., "percent": ..., "read_bps": ..., "write_bps": ... },
  "net":  { "up_bps": 1200, "down_bps": 45000, "recv": ... },
  "battery": { ... },
  "procs": [ { "pid": 1234, "name": "chrome.exe", "cpu": 3.2, "mem": 4.1 } ]
}
```

## CPU 温度为「—」怎么办？

Windows 用户态读不到 CPU 核心温度，需要额外驱动。本项目的做法是：

1. 用 [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) 的 Web 服务（`http://127.0.0.1:8099/data.json`）读传感器；
2. LHM 的清单要求管理员权限，所以注册成一个**计划任务** `PCMonitorTempSensor`（`/RL HIGHEST`，登录自启），由服务端静默 `/Run` 拉起，只在第一次需要过 UAC。

仓库不包含 LHM 的二进制（体积大、有独立许可）。从源码运行时，把 LHM 解压到
`pc-monitor/sensors/lhm/`（目录里要有 `LibreHardwareMonitor.exe`）即可；
打包好的 exe 已内置这一份，用户无需处理。

## 安卓 App 构建

`android-build/` 里带的是最小的自建构建链，不走 Gradle。需要自备 JDK 17 与 Android SDK：

- `android-build/jdk/jdk-17.x`（`JAVA_HOME`）
- `android-build/sdk/build-tools/33.0.2`、`android-build/sdk/platforms/android-33/android.jar`

```bash
cd android-build
python build.py          # 产出 out/pc-monitor.apk，并复制到 pc-monitor/static/
```

App 打开即强制横屏全屏、隐藏状态栏；连不上主机时自动切到时钟页（时间、农历、星期、天气），
页面内长按可以改电脑地址。

## 已知限制

- **仅 Windows**：GPU / 温度 / 托盘 / 计划任务全部依赖 Windows API
- 局域网内**没有鉴权**，默认监听 `0.0.0.0:8000`，请不要直接暴露到公网
- 端口固定 8000（可用 `-p` 或环境变量 `PCM_PORT` 改）

## 许可

[MIT License](LICENSE) © 2026 jarrydai

可以自由使用、修改、分发，包括商用，保留版权声明即可。
